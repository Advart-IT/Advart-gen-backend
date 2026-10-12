"""
Shows an invoice's payment screenshot, sets its due date, or records a payment made outside the portal (bank
transfer, UPI, cheque, ...). Customers see every live studio invoice as soon as the studio makes it; to hide one,
delete it in the studio.

    python scripts/update_invoice.py INV/26-27/0004                     # status, and the customer's screenshot + note
    python scripts/update_invoice.py INV/26-27/0004 --due 2026-10-31
    python scripts/update_invoice.py INV/26-27/0004 --paid "Bank transfer" --reference UTR123
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import security
from database import SessionLocal
from models import BrandInvoice, PortalInvoice

parser = argparse.ArgumentParser()
parser.add_argument("number", help="the invoice number as the studio shows it")
parser.add_argument("--due", help="due date, YYYY-MM-DD")
parser.add_argument("--paid", metavar="METHOD", help='record a payment made outside the portal, e.g. "Bank transfer"')
parser.add_argument("--reference", default=None, help="payment reference for --paid")
args = parser.parse_args()

db = SessionLocal()
invoice = db.query(BrandInvoice).filter(BrandInvoice.invoice_number == args.number, BrandInvoice.is_deleted.is_(False)).first()
if not invoice:
    sys.exit(f"No live invoice numbered {args.number}.")
portal = db.query(PortalInvoice).filter(PortalInvoice.invoice_id == invoice.id).first()

if not args.due and not args.paid:
    import storage

    print(f"{args.number} ({invoice.brand_name}), dated {invoice.invoice_date}")
    print(f"  due:  {(portal and portal.due_date) or 'the default'}")
    print(f"  paid: {portal.paid_at} by {portal.payment_method}" if portal and portal.paid_at else "  paid: not yet")
    if portal and portal.proof_submitted_at:
        print(f"  payment screenshot sent {portal.proof_submitted_at} UTC: {storage.url(portal.proof_path)}")
        print(f"  their note: {portal.proof_note or '(none)'}")
    else:
        print("  payment screenshot: none sent")
    sys.exit(0)

if not portal:
    portal = PortalInvoice(invoice_id=invoice.id)
    db.add(portal)

if args.due:
    portal.due_date = date.fromisoformat(args.due)
if args.paid:
    portal.paid_at = portal.paid_at or security.now()
    portal.payment_method, portal.payment_reference = args.paid, args.reference
print(f"{args.number} ({invoice.brand_name})"
      + (f": due {portal.due_date}" if portal.due_date else "")
      + (f", recorded as paid by {portal.payment_method}" if portal.paid_at else "") + ".")
db.commit()
db.close()
