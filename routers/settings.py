"""Settings: the signed-in person's own details, their company's billing details, and their password."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import security
from database import get_db
from models import PortalUser
from routers.auth import me_payload
from security import Caller, get_caller

router = APIRouter(prefix="/settings")


class AccountUpdate(BaseModel):
    name: str
    phone: str
    email: str


@router.patch("/account")
def update_account(payload: AccountUpdate, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    name, phone, email = payload.name.strip(), payload.phone.strip(), payload.email.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Enter your contact name.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="That email doesn't look right.")
    if email != caller.user.email.lower():
        taken = db.query(PortalUser.id).filter(func.lower(PortalUser.email) == email, PortalUser.id != caller.user.id).first()
        if taken:
            raise HTTPException(status_code=409, detail="Another account already uses that email.")
    caller.user.name, caller.user.phone, caller.user.email = name, phone, email
    db.commit()
    return me_payload(db, caller.user, caller.brand, caller.member)


class CompanyUpdate(BaseModel):
    company: str
    gstin: str
    pin: str = ""
    address: str = ""
    city: str = ""
    state: str = ""


@router.patch("/company")
def update_company(payload: CompanyUpdate, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    if not caller.access["company"]:
        raise HTTPException(status_code=403, detail="Only your brand's admin can change the company details.")
    company = payload.company.strip()
    gstin = "".join(payload.gstin.split()).upper()
    if not company:
        raise HTTPException(status_code=400, detail="Enter your brand or company name.")
    if len(gstin) != 15:
        raise HTTPException(status_code=400, detail="GSTIN must be 15 characters, e.g. 29AAGFK7392P1ZQ.")
    brand = caller.brand
    # Only the displayed name changes. brand_key (the link to this brand's products and invoices) never does.
    brand.company_name, brand.gstin = company, gstin
    brand.pin, brand.address = payload.pin.strip(), payload.address.strip()
    brand.city, brand.state = payload.city.strip(), payload.state.strip()
    db.commit()
    return me_payload(db, caller.user, brand, caller.member)


class PasswordUpdate(BaseModel):
    current: str
    new: str


@router.post("/password")
def change_password(payload: PasswordUpdate, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    if not payload.current:
        raise HTTPException(status_code=400, detail="Enter your current password.")
    if len(payload.new) < 8:
        raise HTTPException(status_code=400, detail="New password needs at least 8 characters.")
    if not security.check_password(payload.current, caller.user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    caller.user.password_hash = security.hash_password(payload.new)
    db.commit()
    return {"ok": True}
