"""
A brand's team, for the brand's admin (whoever signed it up, or anyone Advart Gen makes one): who has access, inviting
a teammate, and removing one. A teammate invited here gets Products (with the moodboard) only: Invoices for a member is
granted by Advart Gen in the Dot App (task management's Design/portal_admin.py), which also manages roles, other brands
and deactivating logins.
"""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import config
import mailer
import security
from database import get_db
from models import ROLE_ADMIN, ROLE_MEMBER, PortalBrandMember, PortalEmailCode, PortalUser
from security import Caller, allowed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/team")
TEAM = allowed("team")


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _team(db: Session, caller: Caller) -> list:
    rows = (
        db.query(PortalUser, PortalBrandMember)
        .join(PortalBrandMember, PortalBrandMember.user_id == PortalUser.id)
        .filter(PortalBrandMember.brand_id == caller.brand.id)
        .order_by(PortalBrandMember.role != ROLE_ADMIN, PortalUser.name)
        .all()
    )
    return [{
        "id": user.id, "name": user.name, "email": user.email, "role": member.role,
        "access": {"products": member.role == ROLE_ADMIN or bool(member.can_products),
                   "invoices": member.role == ROLE_ADMIN or bool(member.can_invoices)},
        # Invited but hasn't chosen a password / signed in yet.
        "invited": user.password_hash is None, "active": bool(user.is_active),
        "last_login_at": _iso(user.last_login_at), "you": user.id == caller.user.id,
    } for user, member in rows]


@router.get("")
def list_team(caller: Caller = Depends(TEAM), db: Session = Depends(get_db)):
    return _team(db, caller)


class Invite(BaseModel):
    name: str
    email: str


@router.post("/invite")
def invite(payload: Invite, caller: Caller = Depends(TEAM), db: Session = Depends(get_db)):
    name, email = payload.name.strip(), payload.email.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Enter their name.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="That email doesn't look right.")
    user = db.query(PortalUser).filter(func.lower(PortalUser.email) == email).first()
    if user and db.query(PortalBrandMember.id).filter(
            PortalBrandMember.user_id == user.id, PortalBrandMember.brand_id == caller.brand.id).first():
        raise HTTPException(status_code=409, detail=f"{email} is already on your team.")
    token = None
    if not user:
        # A new login with no password yet: the invitation's link lets them choose one (and proves the email).
        user = PortalUser(brand_id=caller.brand.id, name=name[:255], email=email, password_hash=None, email_verified=False)
        db.add(user)
        db.flush()
        token = security.new_token()
        db.add(PortalEmailCode(user_id=user.id, purpose="reset", code_hash=security.hash_secret(token),
                               expires_at=security.now() + timedelta(days=config.INVITE_LINK_DAYS)))
    db.add(PortalBrandMember(user_id=user.id, brand_id=caller.brand.id, role=ROLE_MEMBER, can_products=True,
                             can_invoices=False, invited_by=caller.user.id))
    db.commit()
    try:
        mailer.send_invite(email, user.name, caller.user.name, caller.brand.company_name, token)
    except Exception as e:
        logger.error(f"[team] invitation email to {email} failed: {e}")
        raise HTTPException(status_code=503, detail="They were added, but the invitation email couldn't be sent. "
                                                    "They can sign in with 'Forgot password?' to choose a password.")
    return _team(db, caller)


@router.delete("/{user_id}")
def remove(user_id: int, caller: Caller = Depends(TEAM), db: Session = Depends(get_db)):
    """Takes a teammate off this brand (their login, and any other brand they have, stays). Admins are removed by
    Advart Gen only, so a brand can never lose its last admin from here."""
    member = db.query(PortalBrandMember).filter(
        PortalBrandMember.user_id == user_id, PortalBrandMember.brand_id == caller.brand.id).first()
    if not member:
        raise HTTPException(status_code=404, detail="They're not on your team.")
    if member.role == ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="An admin can only be removed by Advart Gen.")
    db.delete(member)
    db.commit()
    return _team(db, caller)
