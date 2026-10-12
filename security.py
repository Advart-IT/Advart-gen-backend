"""Passwords, the login cookie, the "who is calling" dependency every protected route uses, and what they may do."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import bcrypt
import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

import config
from database import get_db
from models import BRAND_ACTIVE, ROLE_ADMIN, PortalBrand, PortalBrandMember, PortalUser


def now() -> datetime:
    """Naive UTC, the form every DateTime column in this database is stored in."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---- Passwords -------------------------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("ascii")


def check_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("ascii"))
    except ValueError:
        return False


# ---- One-time codes and reset tokens (only the hash is stored) ---------------------------------------------------

def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def same_secret(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(value), expected_hash)


# ---- Brands a login may use, and what it may do there --------------------------------------------------------

def memberships_of(db: Session, user: PortalUser) -> List[Tuple[PortalBrand, PortalBrandMember]]:
    """Every brand this login can use with its access there: the home brand first, then the rest by name."""
    rows = (
        db.query(PortalBrand, PortalBrandMember)
        .join(PortalBrandMember, PortalBrandMember.brand_id == PortalBrand.id)
        .filter(PortalBrandMember.user_id == user.id)
        .all()
    )
    return sorted(rows, key=lambda row: (row[0].id != user.brand_id, row[0].company_name.lower()))


def brands_of(db: Session, user: PortalUser) -> List[PortalBrand]:
    return [brand for brand, _member in memberships_of(db, user)]


def _membership(db: Session, user: PortalUser, brand_id: int) -> Optional[PortalBrandMember]:
    return db.query(PortalBrandMember).filter(PortalBrandMember.user_id == user.id, PortalBrandMember.brand_id == brand_id).first()


def access_of(brand: PortalBrand, member: PortalBrandMember) -> dict:
    """What this login may do in this brand. Nothing but its own account until the brand is approved."""
    active = brand.status == BRAND_ACTIVE
    admin = member.role == ROLE_ADMIN
    return {
        "products": active and (admin or bool(member.can_products)),  # products and the moodboard
        "invoices": active and (admin or bool(member.can_invoices)),
        "team": active and admin,      # see and invite teammates
        "company": admin,              # edit the company (billing) details
    }


# ---- Login cookie ----------------------------------------------------------------------------------------------

def set_session(response: Response, user: PortalUser, remember: bool, brand_id: Optional[int] = None) -> None:
    """Signs the user in to brand_id (their home brand when not given). Switching brand issues a new cookie."""
    lifetime = timedelta(days=config.REMEMBER_DAYS) if remember else timedelta(hours=config.SESSION_HOURS)
    token = jwt.encode(
        {"sub": str(user.id), "brand": brand_id or user.brand_id, "rem": remember,
         "exp": datetime.now(timezone.utc) + lifetime},
        config.JWT_SECRET, algorithm="HS256",
    )
    response.set_cookie(
        config.COOKIE_NAME, token, httponly=True, secure=config.COOKIE_SECURE, samesite="lax", path="/",
        # "Keep me signed in" off = a browser-session cookie (gone when the browser closes).
        max_age=int(lifetime.total_seconds()) if remember else None,
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(config.COOKIE_NAME, path="/")


class Caller:
    """The signed-in user, the brand they are working in, and their access there. Every query in a protected route
    is scoped to `brand`."""

    def __init__(self, user: PortalUser, brand: PortalBrand, member: PortalBrandMember, remember: bool = True):
        self.user = user
        self.brand = brand
        self.member = member
        self.remember = remember

    @property
    def brand_key(self) -> str:
        return self.brand.brand_key

    @property
    def access(self) -> dict:
        return access_of(self.brand, self.member)


def start_brand(db: Session, user: PortalUser) -> Optional[Tuple[PortalBrand, PortalBrandMember]]:
    """Where a sign-in lands: the home brand, or (if the login has since been taken off it) its first other brand."""
    rows = memberships_of(db, user)
    return rows[0] if rows else None


def _caller_from_cookie(token: Optional[str], db: Session) -> Optional[Caller]:
    if not token:
        return None
    try:
        claims = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        user_id = int(claims["sub"])
        wanted = int(claims.get("brand") or 0)
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
    user = db.query(PortalUser).filter(PortalUser.id == user_id).first()
    if not user or not user.is_active or not user.email_verified:
        return None
    member = _membership(db, user, wanted) if wanted else None
    if member:
        brand = db.query(PortalBrand).filter(PortalBrand.id == member.brand_id).first()
    else:
        # A brand the login has since been taken off falls back to where a sign-in lands; none left = signed out.
        landing = start_brand(db, user)
        if not landing:
            return None
        brand, member = landing
    return Caller(user, brand, member, bool(claims.get("rem", True))) if brand else None


def get_caller(
    session: Optional[str] = Cookie(None, alias=config.COOKIE_NAME),
    showing_brand: Optional[int] = Header(None, alias="X-Brand-Id"),
    db: Session = Depends(get_db),
) -> Caller:
    caller = _caller_from_cookie(session, db)
    if not caller:
        raise HTTPException(status_code=401, detail="Please sign in.")
    # The page sends the brand it is showing. Another tab may have switched brand since (the cookie is shared):
    # refuse rather than, say, file a new product under a brand the person isn't looking at.
    if showing_brand is not None and showing_brand != caller.brand.id:
        raise HTTPException(status_code=409, detail={
            "code": "brand_changed", "message": f"You switched to {caller.brand.company_name} in another tab. Reloading…",
        })
    return caller


AREA_NAMES = {"products": "Products", "invoices": "Invoices", "team": "Team"}


def allowed(*areas: str):
    """A route dependency: the caller, if this brand is approved and they may use at least one of these areas
    (see access_of). Otherwise 403, with a code the page can show a proper message for."""
    def check(caller: Caller = Depends(get_caller)) -> Caller:
        if caller.brand.status != BRAND_ACTIVE:
            raise HTTPException(status_code=403, detail={
                "code": "brand_not_active", "message": "Your account is waiting for approval from Advart Gen.",
            })
        if not any(caller.access[area] for area in areas):
            raise HTTPException(status_code=403, detail={
                "code": "no_access",
                "message": f"You don't have access to {' or '.join(AREA_NAMES[a] for a in areas)} for {caller.brand.company_name}.",
            })
        return caller
    return check
