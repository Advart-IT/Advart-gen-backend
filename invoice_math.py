"""
What an invoice comes to, for the customer.

The amounts are NOT stored: the studio keeps each line's production cost in USD and works the bill out from it
(task management, Design/brand_invoices.py's _compute). This file repeats that calculation exactly, so the
portal shows the same total the studio's own invoice shows:

    cost_inr   = cost_usd x usd_to_inr_rate
    price      = cost_inr / (1 - profit_margin_pct / 100)
    line net   = price - line discount (a percent of the price, or a flat amount)
    subtotal   = sum(line net)
    taxable    = subtotal - bill discount (a percent of the subtotal, or a flat amount)
    gst        = taxable x gst_pct / 100, waived entirely when waive_gst is on
    total      = taxable + gst - gst waiver

Cost, margin and profit are the studio's own figures and never leave this file: `compute` returns only what is
printed on the invoice. If the studio's formula changes, change it here too.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import List

_PAISA = Decimal("0.01")


def _r(value) -> Decimal:
    return Decimal(value).quantize(_PAISA, rounding=ROUND_HALF_UP)


def _discount(base: Decimal, kind: str, value) -> Decimal:
    value = Decimal(value or 0)
    if kind == "percent":
        amount = base * min(max(value, Decimal("0")), Decimal("100")) / Decimal("100")
    elif kind == "amount":
        amount = min(max(value, Decimal("0")), base)
    else:
        amount = Decimal("0")
    return _r(amount)


def _state_code(gstin) -> str:
    """The first two characters of a GSTIN are the state code."""
    gstin = (gstin or "").strip()
    return gstin[:2] if len(gstin) >= 2 and gstin[:2].isdigit() else ""


def compute(invoice, lines: List) -> dict:
    rate = Decimal(invoice.usd_to_inr_rate)
    lines_out = []
    subtotal = Decimal("0")
    images = 0
    for line in lines:
        cost_inr = _r(Decimal(line.cost_usd) * rate)
        price = _r(cost_inr / (Decimal("1") - Decimal(invoice.profit_margin_pct) / Decimal("100")))
        net = price - _discount(price, line.discount_type, line.discount_value)
        subtotal += net
        count = int(line.images_finalised or 0)
        images += count
        lines_out.append({
            "description": line.item_name,
            "detail": line.item_type_name or "",
            "images": count,
            "rate": str(_r(net / count)) if count else None,
            "amount": str(net),
        })
    bill_discount = _discount(subtotal, invoice.bill_discount_type, invoice.bill_discount_value)
    taxable = subtotal - bill_discount
    gst = _r(taxable * Decimal(invoice.gst_pct) / Decimal("100"))
    gst_waiver = gst if invoice.waive_gst else Decimal("0")
    seller_state, buyer_state = _state_code(invoice.seller_gstin), _state_code(invoice.bill_to_gstin)
    return {
        "lines": lines_out,
        "item_count": len(lines_out),
        "image_count": images,
        "subtotal": str(subtotal),
        "bill_discount": str(bill_discount),
        "taxable": str(taxable),
        "gst_pct": str(Decimal(invoice.gst_pct).normalize()),
        "gst": str(gst),
        "gst_waiver": str(gst_waiver),
        # Seller and buyer in different states = one IGST line; otherwise the GST is split CGST + SGST.
        "tax_mode": "igst" if seller_state and buyer_state and seller_state != buyer_state else "cgst_sgst",
        "total": str(taxable + gst - gst_waiver),
    }
