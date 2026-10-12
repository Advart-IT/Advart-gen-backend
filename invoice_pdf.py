"""
An invoice as a real PDF file (the "Download PDF" button), built from exactly what routers/invoices.py's get_invoice
returns, so the file always matches the page. Laid out like the studio's own printed "Tax Invoice" (task management,
BrandInvoices.tsx's printInvoice): parties, one row per product, then the GST block and the total.

Noto Sans (fonts/, SIL Open Font License) because the PDF core fonts have no rupee sign.
"""
import os
from datetime import date, datetime
from decimal import Decimal

from fpdf import FPDF

_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
INK, MUTED, LINE, ACCENT, PAID = (17, 17, 20), (105, 105, 115), (222, 222, 228), (255, 220, 57), (22, 163, 74)
COLUMNS = (0.52, 0.12, 0.16, 0.20)  # description, images, rate, amount (fractions of the page width)


def money(value) -> str:
    """₹ with Indian digit grouping (₹1,23,456.50); paise only when there are any, as on the page."""
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    sign, amount = ("- " if amount < 0 else ""), abs(amount)
    whole, paise = int(amount), int((amount % 1) * 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = [head[max(0, end - 2):end] for end in range(len(head), 0, -2)]
        digits = ",".join(reversed(groups)) + "," + tail
    return f"{sign}₹{digits}" + (f".{paise:02d}" if paise else "")


def _day(iso: str) -> str:
    return (datetime.fromisoformat(iso.rstrip("Z")) if "T" in iso else date.fromisoformat(iso)).strftime("%d %b %Y")


def _pct(value: Decimal) -> str:
    return str(int(value)) if value == int(value) else f"{value:.1f}"


class _Invoice(FPDF):
    number = ""

    def footer(self):
        self.set_y(-12)
        self.set_font("Noto", size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"{self.number}  ·  Page {self.page_no()} of {{nb}}", align="C")


def render(invoice: dict) -> bytes:
    pdf = _Invoice(format="A4", unit="mm")
    pdf.number = invoice["number"]
    pdf.add_font("Noto", "", os.path.join(_FONTS, "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", os.path.join(_FONTS, "NotoSans-Bold.ttf"))
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()
    width = pdf.epw
    left = pdf.l_margin

    def text(size, bold=False, colour=INK):
        pdf.set_font("Noto", "B" if bold else "", size)
        pdf.set_text_color(*colour)

    def label(caption, x, y, w):
        pdf.set_xy(x, y)
        text(7.5, colour=MUTED)
        pdf.cell(w, 4, caption.upper(), align="L")

    # ---- Title and dates -------------------------------------------------------------------------------------
    pdf.set_fill_color(*ACCENT)
    pdf.rect(left, 11, 16, 1.4, style="F")
    pdf.set_xy(left, 15)
    text(20, bold=True)
    pdf.cell(width * 0.55, 10, "Tax Invoice")
    meta = [("Invoice no.", invoice["number"]), ("Date", _day(invoice["date"]))]
    if invoice["status"] == "paid":
        meta.append(("Paid", _day(invoice["paid_at"]) + (f" · {invoice['payment_method']}" if invoice.get("payment_method") else "")))
    else:
        meta.append(("Due", _day(invoice["due_date"])))
    y = 15
    for caption, value in meta:
        pdf.set_xy(left + width * 0.45, y)
        text(8.5, colour=MUTED)
        pdf.cell(width * 0.25, 5, caption, align="R")
        text(8.5, bold=True, colour=PAID if caption == "Paid" else INK)
        pdf.cell(width * 0.30, 5, value, align="R")
        y += 5.2

    # ---- From / Bill to --------------------------------------------------------------------------------------
    top = max(pdf.get_y(), y) + 9
    bottoms = []
    for index, (caption, party) in enumerate((("From", invoice["seller"]), ("Bill to", invoice["bill_to"]))):
        x = left + index * width / 2
        column = width / 2 - 6
        label(caption, x, top, column)
        pdf.set_xy(x, top + 5)
        text(10.5, bold=True)
        pdf.multi_cell(column, 5.2, party["name"] or "-", align="L")
        text(8.5, colour=MUTED)
        for line in (party["address"], f"GSTIN {party['gstin']}" if party["gstin"] else ""):
            if line:
                pdf.set_x(x)
                pdf.multi_cell(column, 4.4, line, align="L")
        bottoms.append(pdf.get_y())

    # ---- Lines ------------------------------------------------------------------------------------------------
    widths = [width * share for share in COLUMNS]

    def table_head():
        pdf.set_x(left)
        text(7.5, colour=MUTED)
        for caption, w, align in zip(("Description", "Images", "Rate", "Amount"), widths, "LRRR"):
            pdf.cell(w, 7, caption.upper(), align=align)
        pdf.ln(7)
        pdf.set_draw_color(*INK)
        pdf.set_line_width(0.35)
        pdf.line(left, pdf.get_y(), left + width, pdf.get_y())

    pdf.set_y(max(bottoms) + 9)
    table_head()
    pdf.set_line_width(0.2)
    for line in invoice["lines"]:
        text(9.5, bold=True)
        name_lines = pdf.multi_cell(widths[0] - 3, 5, line["description"], dry_run=True, output="LINES")
        height = 5 * len(name_lines) + (4.4 if line["detail"] else 0) + 5
        if pdf.will_page_break(height):
            pdf.add_page()
            table_head()
            pdf.set_line_width(0.2)
        row_top = pdf.get_y() + 2.5
        pdf.set_xy(left, row_top)
        text(9.5, bold=True)  # again: a page break above drew the column heads in their own font
        pdf.multi_cell(widths[0] - 3, 5, line["description"], align="L")
        if line["detail"]:
            text(8, colour=MUTED)
            pdf.set_x(left)
            pdf.multi_cell(widths[0] - 3, 4.4, line["detail"], align="L")
        x = left + widths[0]
        for value, w, bold, colour in (
            (str(line["images"]), widths[1], False, INK),
            (money(line["rate"]) if line["rate"] else "-", widths[2], False, MUTED),
            (money(line["amount"]), widths[3], True, INK),
        ):
            pdf.set_xy(x, row_top)
            text(9.5, bold=bold, colour=colour)
            pdf.cell(w, 5, value, align="R")
            x += w
        pdf.set_y(row_top + height - 2.5)
        pdf.set_draw_color(*LINE)
        pdf.line(left, pdf.get_y(), left + width, pdf.get_y())

    # ---- Totals -----------------------------------------------------------------------------------------------
    gst_pct = Decimal(invoice["gst_pct"])
    rows = [("Subtotal", money(invoice["subtotal"]))]
    if Decimal(invoice["bill_discount"]) > 0:
        rows.append(("Discount", "- " + money(invoice["bill_discount"])))
    rows.append(("Taxable value", money(invoice["taxable"])))
    if invoice["tax_mode"] == "igst":
        rows.append((f"IGST {_pct(gst_pct)}%", money(invoice["gst"])))
    else:
        half = money(Decimal(invoice["gst"]) / 2)
        rows += [(f"CGST {_pct(gst_pct / 2)}%", half), (f"SGST {_pct(gst_pct / 2)}%", half)]
    if Decimal(invoice["gst_waiver"]) > 0:
        rows.append(("GST waived", "- " + money(invoice["gst_waiver"])))
    block = width * 0.42
    x = left + width - block
    if pdf.will_page_break(6 * len(rows) + 16):
        pdf.add_page()
    pdf.ln(4)
    for caption, value in rows:
        pdf.set_x(x)
        text(9, colour=MUTED)
        pdf.cell(block * 0.55, 6, caption)
        text(9)
        pdf.cell(block * 0.45, 6, value, align="R")
        pdf.ln(6)
    pdf.set_draw_color(*INK)
    pdf.set_line_width(0.35)
    pdf.line(x, pdf.get_y() + 1, left + width, pdf.get_y() + 1)
    pdf.ln(3)
    pdf.set_x(x)
    text(12, bold=True)
    pdf.cell(block * 0.45, 8, "Total")
    pdf.cell(block * 0.55, 8, money(invoice["total"]), align="R")
    pdf.ln(10)

    # ---- Notes ------------------------------------------------------------------------------------------------
    if invoice.get("notes"):
        pdf.ln(4)
        label("Notes", left, pdf.get_y(), width)
        pdf.set_xy(left, pdf.get_y() + 5)
        text(8.5, colour=MUTED)
        pdf.multi_cell(width, 4.6, invoice["notes"], align="L")

    return bytes(pdf.output())
