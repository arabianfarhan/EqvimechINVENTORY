"""Purchase Requisition sheet as a PDF (A4, black & white).

Built with fpdf2's core Helvetica font, which only supports Latin-1, so a few
symbols are swapped for plain-text equivalents (₹ -> Rs., → -> ->, etc.).
"""
from fpdf import FPDF
from fpdf.fonts import FontFace

_REPLACE = {
    "₹": "Rs.", "→": "->", "←": "<-", "—": "-", "–": "-", "…": "...", "·": "-", "•": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"', "✓": "OK", "⚠": "!", "×": "x",
}


def _t(value):
    text = "" if value is None else str(value)
    for a, b in _REPLACE.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


class _Sheet(FPDF):
    def __init__(self, pr_no, printed_at):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.pr_no = pr_no
        self.printed_at = printed_at
        self.set_margins(12, 12, 12)
        self.set_auto_page_break(True, margin=14)

    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", "", 7.5)
        self.cell(0, 4, _t(f"{self.pr_no}  -  printed {self.printed_at}  -  page {self.page_no()}/{{nb}}"),
                  align="R")


def build_pr_pdf(pr, lines, actions, printed_at, status_label, company_line):
    """
    pr        : PR header dict
    lines     : list of dicts with part_id, part_name, description, qty_requested, unit,
                stock_now, qty_received, rate
    actions   : list of (when, action, by, remarks) tuples, already shortened
    """
    pdf = _Sheet(pr["pr_no"], printed_at)
    pdf.alias_nb_pages()
    pdf.add_page()
    line_h = 5.2

    # ── title ──
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 7, "EQVIMECH PVT. LTD. - PURCHASE REQUISITION", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.cell(0, 5, _t(f"{company_line}  -  Printed {printed_at}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ── header block ──
    label = FontFace(emphasis="BOLD")
    pdf.set_font("Helvetica", "", 9)
    with pdf.table(col_widths=(28, 65, 28, 65), line_height=line_h, first_row_as_headings=False,
                   borders_layout="ALL", padding=1.2) as t:
        for l1, v1, l2, v2 in (
            ("PR No.", pr["pr_no"], "Status", status_label),
            ("Requested by", pr.get("requester"), "Raised on", pr.get("_raised")),
            ("Required by", pr.get("_required"), "Priority", pr.get("priority")),
            ("Vendor", pr.get("vendor_name") or "-", "PO / Ref", pr.get("po_ref") or "-"),
            ("Ordered on", pr.get("_ordered") or "-", "Expected", pr.get("_expected") or "-"),
        ):
            r = t.row()
            r.cell(_t(l1), style=label); r.cell(_t(v1)); r.cell(_t(l2), style=label); r.cell(_t(v2))
        r = t.row()
        r.cell("Reason", style=label)
        r.cell(_t(pr.get("reason") or "-"), colspan=3)
    pdf.ln(4)

    # ── items ──
    has_rate = any(l.get("rate") is not None for l in lines)
    heads = ["#", "Item code", "Name", "Description", "Order qty", "Unit", "Stock now", "Recd", "Pending"]
    widths = [7, 32, 34, 43, 14, 11, 14, 11, 17]
    if has_rate:
        heads.append("Rate")
        widths = [7, 28, 30, 36, 13, 10, 13, 10, 17, 22]
    heading = FontFace(emphasis="BOLD", fill_color=(225, 225, 225))
    pdf.set_font("Helvetica", "", 8.5)
    num_cols = {4, 6, 7, 8, 9}
    with pdf.table(col_widths=widths, line_height=4.6, headings_style=heading, padding=1.1,
                   text_align=tuple("RIGHT" if i in num_cols else "LEFT" for i in range(len(heads)))) as t:
        h = t.row()
        for x in heads:
            h.cell(x)
        for i, l in enumerate(lines, start=1):
            r = t.row()
            r.cell(str(i))
            r.cell(_t(l.get("part_id")))
            r.cell(_t(l.get("part_name")))
            r.cell(_t(l.get("description") or ""))
            r.cell(str(l.get("qty_requested")), style=FontFace(emphasis="BOLD"))
            r.cell(_t(l.get("unit") or "Nos"))
            r.cell("" if l.get("stock_now") is None else str(int(l["stock_now"])))
            r.cell(str(l.get("qty_received") or 0))
            r.cell(str(int(l.get("qty_requested") or 0) - int(l.get("qty_received") or 0)))
            if has_rate:
                r.cell("" if l.get("rate") is None else f"{float(l['rate']):,.2f}")
    pdf.ln(4)

    # ── action history ──
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 6, "Action history", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    with pdf.table(col_widths=(32, 24, 34, 96), line_height=4.6, headings_style=heading, padding=1.1) as t:
        h = t.row()
        for x in ("Date & time", "Action", "By", "Remarks"):
            h.cell(x)
        for when, action, by, remarks in actions:
            r = t.row()
            r.cell(_t(when)); r.cell(_t(action)); r.cell(_t(by)); r.cell(_t(remarks))

    # ── signatures ──
    if pdf.get_y() > pdf.h - 40:
        pdf.add_page()
    pdf.ln(16)
    y = pdf.get_y()
    col = (pdf.w - pdf.l_margin - pdf.r_margin - 16) / 3
    pdf.set_font("Helvetica", "", 8.5)
    for i, text in enumerate(("Requested by", "Store / Purchase", "Received by")):
        x = pdf.l_margin + i * (col + 8)
        pdf.line(x, y, x + col, y)
        pdf.set_xy(x, y + 1)
        pdf.cell(col, 5, text, align="C")
    return bytes(pdf.output())
