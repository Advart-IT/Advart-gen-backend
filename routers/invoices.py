"""
Invoices. The invoice itself is the studio's (brand_invoices / brand_invoice_lines, built in task management).
The portal shows a brand every live invoice the studio has made for it, as soon as it is made (the studio treats an
invoice as issued when it creates it; deleting it there hides it here), and records due dates and payment in
portal_invoices.

Paying: the customer pays outside the portal (bank transfer, UPI) and sends a screenshot of the payment with a note
("Pay now"). The invoice then shows "Payment sent" until an admin checks the money arrived, in the studio (task
management's Brand Submissions -> Invoices, Design/brand_invoices.py), and approves it; a screenshot alone never marks
an invoice paid. A rejected screenshot puts the invoice back to unpaid with the admin's reason, shown here.

The Razorpay Checkout routes further down are the earlier online-payment path, kept but no longer used by the
page: they stay off without Razorpay keys, and even with keys an invoice becomes Paid only after Razorpay's
signature has been checked and the payment read back from Razorpay with the right amount.
"""
import hashlib
import hmac
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config
import invoice_math
import invoice_pdf
import security
import storage
from database import get_db
from models import BrandInvoice, BrandInvoiceLine, PortalInvoice
from routers.uploads import take_uploads
from security import Caller, allowed

logger = logging.getLogger(__name__)
router = APIRouter()
INVOICES = allowed("invoices")  # an approved brand, and Invoices access there (admins, or granted in the Dot App)

RAZORPAY_API = "https://api.razorpay.com/v1"
METHOD_NAMES = {"upi": "UPI", "card": "Card", "netbanking": "Net banking", "wallet": "Wallet", "emi": "EMI"}


def _visible(db: Session, caller: Caller):
    """Every invoice query starts here: this brand's live invoices, each with its portal row (None until the portal
    has recorded something about it: a due date, a payment order, a payment)."""
    return (
        db.query(BrandInvoice, PortalInvoice)
        .outerjoin(PortalInvoice, PortalInvoice.invoice_id == BrandInvoice.id)
        .filter(BrandInvoice.brand_name == caller.brand_key, BrandInvoice.is_deleted.is_(False))
    )


def _own_invoice(db: Session, caller: Caller, invoice_id: int):
    row = _visible(db, caller).filter(BrandInvoice.id == invoice_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="That invoice no longer exists.")
    return row


def is_paid(portal: Optional[PortalInvoice]) -> bool:
    return bool(portal and portal.paid_at)


def _due_date(invoice: BrandInvoice, portal: Optional[PortalInvoice]) -> date:
    return (portal and portal.due_date) or invoice.invoice_date + timedelta(days=config.INVOICE_DUE_DAYS)


def _status(invoice: BrandInvoice, portal: Optional[PortalInvoice]) -> str:
    if is_paid(portal):
        return "paid"
    if portal and portal.proof_submitted_at:
        return "submitted"  # the customer says it's paid; staff haven't confirmed the money yet
    return "overdue" if _due_date(invoice, portal) < date.today() else "unpaid"  # overdue is derived, never stored


def is_due(invoice: BrandInvoice, portal: Optional[PortalInvoice]) -> bool:
    """Still waiting on the customer (the sidebar's "due" count): not paid, and no payment sent in yet."""
    return _status(invoice, portal) in ("unpaid", "overdue")


def _lines(db: Session, invoice_id: int) -> list:
    return db.query(BrandInvoiceLine).filter(BrandInvoiceLine.invoice_id == invoice_id).order_by(BrandInvoiceLine.id).all()


def _summary(invoice: BrandInvoice, portal: Optional[PortalInvoice], computed: dict) -> dict:
    return {
        "id": invoice.id, "number": invoice.invoice_number,
        "date": invoice.invoice_date.isoformat(), "due_date": _due_date(invoice, portal).isoformat(),
        "status": _status(invoice, portal),
        "item_count": computed["item_count"], "image_count": computed["image_count"], "total": computed["total"],
        "paid_at": portal.paid_at.isoformat() + "Z" if is_paid(portal) else None,
        "payment_method": portal.payment_method if portal else None,
    }


@router.get("/invoices")
def list_invoices(caller: Caller = Depends(INVOICES), db: Session = Depends(get_db)):
    rows = _visible(db, caller).order_by(BrandInvoice.invoice_date.desc(), BrandInvoice.id.desc()).all()
    return [_summary(invoice, portal, invoice_math.compute(invoice, _lines(db, invoice.id))) for invoice, portal in rows]


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, caller: Caller = Depends(INVOICES), db: Session = Depends(get_db)):
    invoice, portal = _own_invoice(db, caller, invoice_id)
    computed = invoice_math.compute(invoice, _lines(db, invoice.id))
    brand = caller.brand
    company_address = ", ".join(part for part in (brand.address, brand.city, " ".join(p for p in (brand.state, brand.pin) if p)) if part)
    out = _summary(invoice, portal, computed)
    out.update(computed)
    out["seller"] = {
        "name": invoice.seller_name or "", "address": invoice.seller_address or "", "gstin": invoice.seller_gstin or "",
    }
    # The invoice's own snapshot of who it bills, falling back to the brand's Company details where it has none.
    out["bill_to"] = {
        "name": invoice.bill_to_name or brand.company_name,
        "address": invoice.bill_to_address or company_address,
        "gstin": invoice.bill_to_gstin or brand.gstin or "",
    }
    out["notes"] = invoice.notes or ""
    out["payment_proof"] = {
        "submitted_at": portal.proof_submitted_at.isoformat() + "Z", "note": portal.proof_note or "",
        "url": storage.url(portal.proof_path),
    } if portal and portal.proof_submitted_at else None
    # The studio turned down the last screenshot: why, so the customer can check and send it again.
    out["payment_rejected_note"] = (
        portal.proof_rejected_note if portal and not portal.paid_at and not portal.proof_submitted_at else None
    ) or ""
    return out


@router.get("/invoices/{invoice_id}/pdf")
def download_pdf(invoice_id: int, caller: Caller = Depends(INVOICES), db: Session = Depends(get_db)):
    invoice = get_invoice(invoice_id, caller, db)
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", invoice["number"]).strip("-") or "invoice"  # INV/26-27/0005 -> INV-26-27-0005
    return Response(
        content=invoice_pdf.render(invoice), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


# ---- Paying: a screenshot of the payment and a note --------------------------------------------------------------

MAX_PROOF_NOTE = 1000


class PaymentProof(BaseModel):
    upload_id: int  # the screenshot, uploaded first through /uploads like every other image
    note: str = ""


@router.post("/invoices/{invoice_id}/payment-proof")
def submit_payment_proof(invoice_id: int, payload: PaymentProof, caller: Caller = Depends(INVOICES),
                         db: Session = Depends(get_db)):
    invoice, portal = _own_invoice(db, caller, invoice_id)
    if is_paid(portal):
        raise HTTPException(status_code=409, detail="This invoice is already paid.")
    note = payload.note.strip()
    if len(note) > MAX_PROOF_NOTE:
        raise HTTPException(status_code=400, detail=f"Keep the note under {MAX_PROOF_NOTE} characters.")
    screenshot = take_uploads(db, caller, [payload.upload_id])[0]
    if not portal:
        portal = PortalInvoice(invoice_id=invoice.id)
        db.add(portal)
    # Sending again (a clearer screenshot, a second part-payment) replaces the earlier one.
    portal.proof_path, portal.proof_note, portal.proof_submitted_at = screenshot.storage_path, note or None, security.now()
    portal.proof_rejected_note = None
    db.commit()
    return get_invoice(invoice_id, caller, db)


# ---- Paying online with Razorpay (not used by the page any more; see the module docstring) ----------------------

def _payments_on() -> bool:
    return bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET)


def _paise(total: str) -> int:
    return int((Decimal(total) * 100).to_integral_value())


@router.post("/invoices/{invoice_id}/pay/order")
def create_payment_order(invoice_id: int, caller: Caller = Depends(INVOICES), db: Session = Depends(get_db)):
    invoice, portal = _own_invoice(db, caller, invoice_id)
    if is_paid(portal):
        raise HTTPException(status_code=409, detail="This invoice is already paid.")
    if not _payments_on():
        raise HTTPException(status_code=503, detail="Online payment isn't set up yet. Please contact Advart Gen to pay this invoice.")
    amount = _paise(invoice_math.compute(invoice, _lines(db, invoice.id))["total"])
    if amount <= 0:
        raise HTTPException(status_code=400, detail="This invoice has nothing to pay.")
    try:
        response = requests.post(
            f"{RAZORPAY_API}/orders", auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET), timeout=20,
            json={"amount": amount, "currency": "INR", "receipt": invoice.invoice_number[:40],
                  "notes": {"invoice_id": str(invoice.id), "brand": caller.brand_key}},
        )
        response.raise_for_status()
        order = response.json()
    except Exception as e:
        logger.error(f"[invoices] could not create a Razorpay order for invoice {invoice.id}: {e}")
        raise HTTPException(status_code=502, detail="We couldn't reach the payment service. Please try again.")
    if not portal:
        portal = PortalInvoice(invoice_id=invoice.id)
        db.add(portal)
    portal.gateway_order_id = order["id"]
    db.commit()
    return {
        "key_id": config.RAZORPAY_KEY_ID, "order_id": order["id"], "amount": amount, "currency": "INR",
        "invoice_number": invoice.invoice_number,
        "prefill": {"name": caller.user.name, "email": caller.user.email, "contact": caller.user.phone or ""},
    }


class PaymentConfirmation(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def signature_ok(order_id: str, payment_id: str, signature: str, secret: Optional[str] = None) -> bool:
    expected = hmac.new(
        (secret or config.RAZORPAY_KEY_SECRET).encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/invoices/{invoice_id}/pay/verify")
def confirm_payment(invoice_id: int, payload: PaymentConfirmation, caller: Caller = Depends(INVOICES),
                    db: Session = Depends(get_db)):
    invoice, portal = _own_invoice(db, caller, invoice_id)
    if is_paid(portal):
        return get_invoice(invoice_id, caller, db)
    if not _payments_on():
        raise HTTPException(status_code=503, detail="Online payment isn't set up yet.")
    # The order must be the one this app created for THIS invoice, and the signature must be Razorpay's.
    if not portal or payload.razorpay_order_id != portal.gateway_order_id or not signature_ok(
            payload.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature):
        raise HTTPException(status_code=400, detail="We couldn't confirm this payment. If money was taken, contact Advart Gen.")
    expected = _paise(invoice_math.compute(invoice, _lines(db, invoice.id))["total"])
    try:
        response = requests.get(
            f"{RAZORPAY_API}/payments/{payload.razorpay_payment_id}",
            auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET), timeout=20,
        )
        response.raise_for_status()
        payment = response.json()
    except Exception as e:
        logger.error(f"[invoices] could not read Razorpay payment {payload.razorpay_payment_id}: {e}")
        raise HTTPException(status_code=502, detail="We couldn't confirm this payment yet. Please refresh in a minute.")
    if (payment.get("order_id") != portal.gateway_order_id or payment.get("status") not in ("captured", "authorized")
            or int(payment.get("amount") or 0) != expected):
        logger.error(f"[invoices] Razorpay payment {payload.razorpay_payment_id} does not match invoice {invoice.id}: {payment}")
        raise HTTPException(status_code=400, detail="We couldn't confirm this payment. If money was taken, contact Advart Gen.")
    portal.paid_at = security.now()
    portal.payment_method = METHOD_NAMES.get(payment.get("method"), (payment.get("method") or "Online").title())
    portal.payment_reference = payload.razorpay_payment_id
    db.commit()
    return get_invoice(invoice_id, caller, db)
