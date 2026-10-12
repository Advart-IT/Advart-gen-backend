"""Sign up (and the sign-up form's draft, kept as a lead), verify email, sign in and out, forgotten password, Google
sign-in, "who am I", and switching brand."""
import logging
import secrets
from datetime import timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import config
import mailer
import security
from database import get_db
from models import (
    BRAND_PENDING, ROLE_ADMIN, BrandInvoice, BrandItemType, PortalBrand, PortalBrandMember, PortalEmailCode,
    PortalSignupLead, PortalUser,
)
from security import Caller, get_caller

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")

WRONG_LOGIN = "Email or password is incorrect."
NO_BRAND = "Your account doesn't have access to any brand right now. Contact Advart Gen."


def me_payload(db: Session, user: PortalUser, brand: PortalBrand, member: PortalBrandMember) -> dict:
    return {
        "user": {"id": user.id, "name": user.name, "email": user.email, "phone": user.phone or ""},
        "brand": {
            "company": brand.company_name, "gstin": brand.gstin or "", "address": brand.address or "",
            "city": brand.city or "", "state": brand.state or "", "pin": brand.pin or "",
            # "pending" until Advart Gen approves a brand that signed up by itself; "rejected" with review_note
            "status": brand.status, "review_note": brand.review_note or "",
        },
        # The brand being worked in, and every brand this login can switch to (one, for most customers).
        "brand_id": brand.id,
        "brands": [{"id": b.id, "company": b.company_name, "status": b.status} for b in security.brands_of(db, user)],
        # What this login may do in this brand: see security.access_of.
        "role": member.role,
        "access": security.access_of(brand, member),
        "features": {
            "payments": bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET),
        },
    }


def _landing(db: Session, user: PortalUser):
    """The brand a sign-in lands on (and the access there), or 403 if the login can't use any brand."""
    landing = security.start_brand(db, user)
    if not landing:
        raise HTTPException(status_code=403, detail=NO_BRAND)
    return landing


def brand_key_for(company: str) -> str:
    """The studio's brand key for a company name: lower case, single spaces (how brand_name is stored there)."""
    return " ".join(company.lower().split())


def _find_user(db: Session, email: str):
    return db.query(PortalUser).filter(func.lower(PortalUser.email) == email.strip().lower()).first()


def _issue(db: Session, user: PortalUser, purpose: str, secret: str, minutes: int) -> None:
    """Stores a new code/token for the user and retires any earlier one of the same kind."""
    db.query(PortalEmailCode).filter(
        PortalEmailCode.user_id == user.id, PortalEmailCode.purpose == purpose, PortalEmailCode.used_at.is_(None),
    ).update({"used_at": security.now()})
    db.add(PortalEmailCode(
        user_id=user.id, purpose=purpose, code_hash=security.hash_secret(secret),
        expires_at=security.now() + timedelta(minutes=minutes),
    ))
    db.commit()


def _send_verification(db: Session, user: PortalUser) -> None:
    code = security.new_code()
    _issue(db, user, "verify", code, config.VERIFY_CODE_MINUTES)
    try:
        mailer.send_verification_code(user.email, user.name, code)
    except Exception as e:
        logger.error(f"[auth] verification email to user {user.id} failed: {e}")
        raise HTTPException(status_code=503, detail="We couldn't send the verification email. Please try again shortly.")


# ---- Sign up ---------------------------------------------------------------------------------------------------

class SignupDraft(BaseModel):
    token: Optional[str] = None  # from this browser's earlier draft, to keep updating the same lead
    company: str = ""
    contact_name: str = ""
    phone: str = ""
    email: str = ""


@router.post("/signup-draft")
def save_signup_draft(payload: SignupDraft, db: Session = Depends(get_db)):
    """The sign-up form, saved as it is typed (before Create account): someone who stops halfway is still a lead
    the Dot App shows. Only what was typed is kept; the browser gets a token to update its own lead and no other."""
    fields = {
        "company": payload.company.strip()[:255], "contact_name": payload.contact_name.strip()[:255],
        "phone": payload.phone.strip()[:64], "email": payload.email.strip().lower()[:255],
    }
    if not any(fields.values()):
        return {"token": payload.token}
    lead = db.query(PortalSignupLead).filter(
        PortalSignupLead.token_hash == security.hash_secret(payload.token)).first() if payload.token else None
    token = payload.token if lead else secrets.token_urlsafe(24)
    if not lead:
        lead = PortalSignupLead(token_hash=security.hash_secret(token))
        db.add(lead)
    if lead.user_id is None:  # once the account exists, the lead is history
        for name, value in fields.items():
            setattr(lead, name, value or None)
    db.commit()
    return {"token": token}


class SignupRequest(BaseModel):
    company: str
    contact_name: str
    phone: str
    email: str
    password: str
    lead_token: Optional[str] = None  # the form's draft, now finished


@router.post("/signup")
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    company, name, phone = payload.company.strip(), payload.contact_name.strip(), payload.phone.strip()
    email = payload.email.strip().lower()
    if not company or not name or not phone or not email:
        raise HTTPException(status_code=400, detail="Please fill in every field.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="That email doesn't look right.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password needs at least 8 characters.")

    user = _find_user(db, email)
    if user and user.email_verified:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Sign in instead.")
    if user:
        # Signed up before but never entered the code: let them start again rather than leaving them stuck.
        user.name, user.phone, user.password_hash = name, phone, security.hash_password(payload.password)
        db.commit()
    else:
        key = brand_key_for(company)
        # A new sign-up may never attach itself to a brand that already exists, in the portal OR in the studio:
        # that brand's products and invoices would become visible to whoever typed its name. Staff add people to
        # an existing brand with scripts/create_user.py.
        taken = (
            db.query(PortalBrand.id).filter(PortalBrand.brand_key == key).first()
            or db.query(BrandItemType.id).filter(func.lower(BrandItemType.brand_name) == key).first()
            or db.query(BrandInvoice.id).filter(func.lower(BrandInvoice.brand_name) == key).first()
        )
        if taken:
            raise HTTPException(
                status_code=409,
                detail="This brand already has a workspace on Advart Gen. Ask your Advart Gen contact to add you to it.",
            )
        # Waits for Advart Gen to approve it in the Dot App; whoever signs it up is its admin, with every access.
        brand = PortalBrand(brand_key=key, company_name=company, status=BRAND_PENDING)
        db.add(brand)
        db.flush()
        user = PortalUser(
            brand_id=brand.id, name=name, email=email, phone=phone,
            password_hash=security.hash_password(payload.password), email_verified=False,
        )
        db.add(user)
        db.flush()
        db.add(PortalBrandMember(user_id=user.id, brand_id=brand.id, role=ROLE_ADMIN, can_products=True, can_invoices=True))
        db.commit()
        db.refresh(user)
    if payload.lead_token:
        lead = db.query(PortalSignupLead).filter(PortalSignupLead.token_hash == security.hash_secret(payload.lead_token)).first()
        if lead and lead.user_id is None:
            lead.user_id = user.id
            lead.company, lead.contact_name, lead.phone, lead.email = company, name, phone, email
            db.commit()
    _send_verification(db, user)
    return {"email": user.email}


class VerifyRequest(BaseModel):
    email: str
    code: str


@router.post("/verify-email")
def verify_email(payload: VerifyRequest, response: Response, db: Session = Depends(get_db)):
    user = _find_user(db, payload.email)
    code = (
        db.query(PortalEmailCode)
        .filter(PortalEmailCode.user_id == user.id, PortalEmailCode.purpose == "verify", PortalEmailCode.used_at.is_(None))
        .order_by(PortalEmailCode.id.desc()).first()
    ) if user else None
    if not code:
        raise HTTPException(status_code=400, detail="That code isn't right. Request a new one.")
    if code.expires_at < security.now():
        raise HTTPException(status_code=400, detail="That code has expired. Request a new one.")
    if code.attempts >= config.VERIFY_CODE_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many wrong attempts. Request a new code.")
    typed = payload.code.strip()
    any_code_ok = config.DEV_ACCEPT_ANY_CODE and len(typed) == 6 and typed.isdigit()  # development only: see config.py
    if not any_code_ok and not security.same_secret(typed, code.code_hash):
        code.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="That code isn't right.")
    code.used_at = security.now()
    user.email_verified = True
    user.last_login_at = security.now()
    db.commit()
    brand, member = _landing(db, user)
    security.set_session(response, user, remember=False, brand_id=brand.id)
    return me_payload(db, user, brand, member)


class EmailRequest(BaseModel):
    email: str


@router.post("/resend-code")
def resend_code(payload: EmailRequest, db: Session = Depends(get_db)):
    user = _find_user(db, payload.email)
    if user and not user.email_verified:
        _send_verification(db, user)
    return {"ok": True}  # the same answer whether or not the email has an account


# ---- Sign in and out -------------------------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str
    remember: bool = True


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = _find_user(db, payload.email)
    if not user or not security.check_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail=WRONG_LOGIN)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been switched off. Contact Advart Gen.")
    if not user.email_verified:
        _send_verification(db, user)
        raise HTTPException(status_code=403, detail={
            "code": "email_not_verified", "email": user.email,
            "message": "Verify your email to continue. We've sent you a new code.",
        })
    brand, member = _landing(db, user)
    user.last_login_at = security.now()
    db.commit()
    security.set_session(response, user, payload.remember, brand_id=brand.id)
    return me_payload(db, user, brand, member)


@router.post("/logout")
def logout(response: Response):
    security.clear_session(response)
    return {"ok": True}


@router.get("/me")
def me(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return me_payload(db, caller.user, caller.brand, caller.member)


class SwitchBrandRequest(BaseModel):
    brand_id: int


@router.post("/switch-brand")
def switch_brand(payload: SwitchBrandRequest, response: Response, caller: Caller = Depends(get_caller),
                 db: Session = Depends(get_db)):
    row = next(((b, m) for b, m in security.memberships_of(db, caller.user) if b.id == payload.brand_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="You don't have access to that brand.")
    brand, member = row
    security.set_session(response, caller.user, caller.remember, brand.id)
    return me_payload(db, caller.user, brand, member)


@router.get("/config")
def public_config():
    """What the sign-in page needs before anyone is signed in. A Google client id is not a secret."""
    return {"google_client_id": config.GOOGLE_CLIENT_ID}


# ---- Forgotten password ----------------------------------------------------------------------------------------

@router.post("/forgot-password")
def forgot_password(payload: EmailRequest, db: Session = Depends(get_db)):
    user = _find_user(db, payload.email)
    if user and user.is_active:
        token = security.new_token()
        _issue(db, user, "reset", token, config.RESET_LINK_MINUTES)
        try:
            mailer.send_reset_link(user.email, user.name, token)
        except Exception as e:
            logger.error(f"[auth] reset email to user {user.id} failed: {e}")
    return {"ok": True}  # the same answer whether or not the email has an account


class ResetRequest(BaseModel):
    token: str
    password: str


@router.post("/reset-password")
def reset_password(payload: ResetRequest, db: Session = Depends(get_db)):
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password needs at least 8 characters.")
    code = db.query(PortalEmailCode).filter(
        PortalEmailCode.purpose == "reset", PortalEmailCode.code_hash == security.hash_secret(payload.token.strip()),
        PortalEmailCode.used_at.is_(None),
    ).first()
    if not code or code.expires_at < security.now():
        raise HTTPException(status_code=400, detail="This reset link has expired or was already used. Request a new one.")
    user = db.query(PortalUser).filter(PortalUser.id == code.user_id).first()
    user.password_hash = security.hash_password(payload.password)
    user.email_verified = True  # the link arrived in their inbox, which is what verifying an email proves
    code.used_at = security.now()
    db.commit()
    return {"ok": True}


# ---- Google ----------------------------------------------------------------------------------------------------

class GoogleRequest(BaseModel):
    access_token: str


@router.post("/google")
def google_login(payload: GoogleRequest, response: Response, db: Session = Depends(get_db)):
    """Signs in an EXISTING account whose email matches the Google account. The token is checked with Google and
    must have been issued to this app's own client id (a token issued to some other site is refused)."""
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google sign-in isn't set up yet. Sign in with your email.")
    try:
        info = requests.get(
            "https://oauth2.googleapis.com/tokeninfo", params={"access_token": payload.access_token}, timeout=10,
        )
        data = info.json() if info.status_code == 200 else {}
    except Exception as e:
        logger.warning(f"[auth] Google token check failed: {e}")
        data = {}
    if data.get("aud") != config.GOOGLE_CLIENT_ID or str(data.get("email_verified")).lower() != "true" or not data.get("email"):
        raise HTTPException(status_code=401, detail="Google sign-in failed. Please try again.")
    user = _find_user(db, data["email"])
    if not user:
        raise HTTPException(status_code=404, detail="No Advart Gen account uses this Google email. Create an account first.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been switched off. Contact Advart Gen.")
    brand, member = _landing(db, user)
    user.email_verified = True
    user.last_login_at = security.now()
    db.commit()
    security.set_session(response, user, remember=True, brand_id=brand.id)
    return me_payload(db, user, brand, member)
