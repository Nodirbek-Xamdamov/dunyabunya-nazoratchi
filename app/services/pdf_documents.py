"""
PDF document generation service using ReportLab.
Generates 4 types of PDF documents with dark blue header styling.
"""
from __future__ import annotations

import html
import os
import re
import tempfile
from datetime import datetime, timedelta
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

# ── Dark blue theme ──
HEADER_BG = colors.HexColor("#1e3a5f")
HEADER_FG = colors.white
ROW_ALT = colors.HexColor("#f0f4f8")
BORDER_COLOR = colors.HexColor("#cbd5e1")
ACCENT = colors.HexColor("#2563eb")


def _register_fonts() -> tuple[str, str]:
    """Unicode shriftni ro'yxatga oladi va (oddiy, qalin) nomlarini qaytaradi.

    ReportLab'ning standart Helvetica shriftida kirill harflari va o'zbekcha ʻ/‘ belgilari
    yo'q — mijoz/mahsulot nomi kirillda bo'lsa PDF'da qora to'rtburchaklar chiqardi."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        (os.path.join(windir, "Fonts", "arial.ttf"), os.path.join(windir, "Fonts", "arialbd.ttf")),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ]
    for regular, bold in candidates:
        if not (os.path.exists(regular) and os.path.exists(bold)):
            continue
        try:
            pdfmetrics.registerFont(TTFont("DBSans", regular))
            pdfmetrics.registerFont(TTFont("DBSans-Bold", bold))
            pdfmetrics.registerFontFamily("DBSans", normal="DBSans", bold="DBSans-Bold",
                                          italic="DBSans", boldItalic="DBSans-Bold")
            return "DBSans", "DBSans-Bold"
        except Exception:  # noqa: BLE001 — buzuq shrift fayli: keyingisini sinaymiz
            continue
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _register_fonts()

_CELL_STYLE = ParagraphStyle("Cell", fontName=FONT, fontSize=9, leading=11)

_INVOICE_PAYMENT_STATES = {
    "not_paid": "To'lanmagan", "partial": "Qisman to'langan", "paid": "To'langan",
    "in_payment": "To'lov jarayonida", "reversed": "Qaytarilgan",
}
_PAYMENT_STATES = {
    "draft": "Qoralama", "in_process": "Jarayonda", "paid": "To'langan", "posted": "O'tkazilgan",
    "canceled": "Bekor qilingan", "cancel": "Bekor qilingan", "rejected": "Rad etilgan",
}


def _safe(text: Any) -> str:
    """Escape HTML/text for ReportLab Paragraph."""
    return html.escape(str(text), quote=False) if text else ""


def _cell(text: Any) -> Paragraph:
    """Jadval katagi uchun matn — uzun nom katak ichida qatorga bo'linadi (ustundan chiqib ketmaydi)."""
    return Paragraph(_safe(text), _CELL_STYLE)


def _m2o(value: Any, default: str = "N/A") -> str:
    return value[1] if isinstance(value, (list, tuple)) and len(value) > 1 and value[1] else default


def _local_date(value: Any) -> str:
    """Odoo sana-vaqtini (UTC) Toshkent vaqtiga o'giradi; oddiy sana o'zgarishsiz qoladi."""
    text = str(value) if value else ""
    if len(text) < 16:
        return text or "N/A"
    try:
        return (datetime.fromisoformat(text.replace(" ", "T")) + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _fmt_money(amount: Any, currency: str = "so'm") -> str:
    try:
        return f"{float(amount):,.2f} {currency}"
    except (ValueError, TypeError):
        return f"0.00 {currency}"


def _fmt_qty(value: Any) -> str:
    try:
        number = float(value or 0)
    except (ValueError, TypeError):
        return str(value)
    return f"{int(number):,}" if number == int(number) else f"{number:,.2f}"


def _build_header(title: str, doc_info: dict) -> list:
    """Build styled header elements."""
    styles = getSampleStyleSheet()
    elements = []

    # Title
    title_style = ParagraphStyle(
        "DocTitle", parent=styles["Title"], fontName=FONT_BOLD,
        fontSize=18, textColor=HEADER_FG, backColor=HEADER_BG,
        spaceAfter=6, alignment=1, borderPadding=(8, 8, 8, 8),
    )
    elements.append(Paragraph(_safe(title), title_style))
    elements.append(Spacer(1, 4 * mm))

    # Document info
    info_style = ParagraphStyle("Info", parent=styles["Normal"], fontName=FONT, fontSize=10,
                                textColor=colors.HexColor("#475569"))
    for key, val in doc_info.items():
        elements.append(Paragraph(f"<b>{_safe(key)}:</b> {_safe(val)}", info_style))

    elements.append(Spacer(1, 4 * mm))
    elements.append(HRFlowable(width="100%", thickness=1, color=BORDER_COLOR))
    elements.append(Spacer(1, 4 * mm))

    return elements


def _build_table(headers: list[str], rows: list[list], col_widths: list[float] | None = None) -> Table:
    """Build a styled table."""
    data = [headers] + rows
    table = Table(data, colWidths=col_widths, repeatRows=1)

    style = TableStyle([
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), HEADER_FG),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        # Body
        ("FONTNAME", (0, 1), (-1, -1), FONT),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        # Grid
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, ACCENT),
    ])

    # Alternating row colors
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.add("BACKGROUND", (0, i), (-1, i), ROW_ALT)

    table.setStyle(style)
    return table


def _new_pdf_path() -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=tempfile.gettempdir()) as f:
        return f.name


def _plain(text: Any) -> str:
    """Odoo eslatmalari HTML ko'rinishida saqlanadi (<p>...</p>) — teglarsiz matnga o'giriladi."""
    return html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def _note(text: Any) -> Paragraph:
    note_style = ParagraphStyle("Note", fontName=FONT, fontSize=9, textColor=colors.HexColor("#64748b"))
    return Paragraph(f"<b>Eslatma:</b> {_safe(_plain(text))}", note_style)


def render_sale_order_pdf(data: dict) -> str:
    """
    Render sale order PDF. Returns path to temp file.
    data: {"order": {...}, "lines": [...], "currency": "so'm"}
    """
    order = data.get("order", {})
    lines = data.get("lines", [])
    currency = data.get("currency") or "so'm"

    filepath = _new_pdf_path()
    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    # Header
    info = {
        "Buyurtma raqami": order.get("name", "N/A"),
        "Sana": _local_date(order.get("date_order")),
        "Mijoz": _m2o(order.get("partner_id")),
        "Sotuv menejeri": _m2o(order.get("user_id")),
    }
    elements.extend(_build_header("SOTUV BUYURTMASI", info))

    # Lines table
    headers = ["#", "Mahsulot", "Soni", "Narx", "Jami"]
    rows = []
    for i, line in enumerate(lines, 1):
        product = line.get("product_id", [0, "N/A"])
        pname = product[1] if isinstance(product, (list, tuple)) else str(product)
        rows.append([
            str(i),
            _cell(pname),
            _fmt_qty(line.get("product_uom_qty", 0)),
            _fmt_money(line.get("price_unit", 0), currency),
            _fmt_money(line.get("price_subtotal", 0), currency),
        ])

    # Total row
    rows.append(["", "", "", "JAMI:", _fmt_money(order.get("amount_total", 0), currency)])

    table = _build_table(headers, rows, [12 * mm, 68 * mm, 22 * mm, 38 * mm, 42 * mm])
    elements.append(table)
    elements.append(Spacer(1, 8 * mm))

    # Note
    if _plain(order.get("note")):
        elements.append(_note(order["note"]))

    doc.build(elements)
    return filepath


def render_invoice_pdf(data: dict) -> str:
    """Render invoice (schyot-faktura) PDF."""
    invoice = data.get("invoice", {})
    lines = data.get("lines", [])
    currency = data.get("currency") or "so'm"

    filepath = _new_pdf_path()
    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    state = invoice.get("payment_state")
    info = {
        "Faktura raqami": invoice.get("name", "N/A"),
        "Sana": invoice.get("invoice_date") or "N/A",
        "Mijoz": _m2o(invoice.get("partner_id")),
        "Holat": _INVOICE_PAYMENT_STATES.get(state, state or "N/A"),
    }
    elements.extend(_build_header("SCHYOT-FAKTURA", info))

    headers = ["#", "Tavsif", "Soni", "Narx", "Jami"]
    rows = []
    for i, line in enumerate(lines, 1):
        rows.append([
            str(i),
            _cell(line.get("name", "")),
            _fmt_qty(line.get("quantity", 0)),
            _fmt_money(line.get("price_unit", 0), currency),
            _fmt_money(line.get("price_subtotal", 0), currency),
        ])

    rows.append(["", "", "", "JAMI:", _fmt_money(invoice.get("amount_total", 0), currency)])
    rows.append(["", "", "", "SHUNDAN SOLIQ:", _fmt_money(invoice.get("amount_tax", 0), currency)])
    rows.append(["", "", "", "TO'LANMAGAN:", _fmt_money(invoice.get("amount_residual", 0), currency)])

    table = _build_table(headers, rows, [12 * mm, 68 * mm, 22 * mm, 38 * mm, 42 * mm])
    elements.append(table)

    doc.build(elements)
    return filepath


def render_payment_receipt_pdf(data: dict) -> str:
    """Render payment receipt PDF."""
    payment = data.get("payment", {})
    currency = data.get("currency") or "so'm"

    filepath = _new_pdf_path()
    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    ptype = "KIRIM" if payment.get("payment_type") == "inbound" else "CHIQIM"
    amount = _fmt_money(payment.get("amount", 0), currency)
    info = {
        "To'lov raqami": payment.get("name", "N/A"),
        "Sana": payment.get("date") or "N/A",
        "Mijoz": _m2o(payment.get("partner_id")),
        "Turi": ptype,
        "Summa": amount,
    }
    elements.extend(_build_header("TO'LOV KVITANSIYASI", info))

    # Simple summary table
    state = payment.get("state")
    headers = ["Ko'rsatkich", "Qiymat"]
    rows = [
        ["To'lov raqami", _cell(payment.get("name", ""))],
        ["Sana", _cell(payment.get("date", ""))],
        ["Mijoz", _cell(_m2o(payment.get("partner_id"), ""))],
        ["Summa", amount],
        ["Holat", _cell(_PAYMENT_STATES.get(state, state or ""))],
    ]
    table = _build_table(headers, rows, [50 * mm, 100 * mm])
    elements.append(table)

    doc.build(elements)
    return filepath


def render_delivery_slip_pdf(data: dict) -> str:
    """Render delivery slip (nakladnoy) PDF."""
    picking = data.get("picking", {})
    moves = data.get("moves", [])

    filepath = _new_pdf_path()
    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    info = {
        "Xujjat raqami": picking.get("name", "N/A"),
        "Manba": _m2o(picking.get("location_id")),
        "Manzil": _m2o(picking.get("location_dest_id")),
        "Rejalashtirilgan sana": _local_date(picking.get("scheduled_date")),
    }
    elements.extend(_build_header("TASHISH XUJJATI (NAKLADNOY)", info))

    headers = ["#", "Mahsulot", "Buyurtma", "Yetkazilgan"]
    rows = []
    for i, move in enumerate(moves, 1):
        product = move.get("product_id", [0, "N/A"])
        pname = product[1] if isinstance(product, (list, tuple)) else str(product)
        rows.append([
            str(i),
            _cell(pname),
            _fmt_qty(move.get("product_uom_qty", 0)),
            _fmt_qty(move.get("quantity_done", 0)),
        ])

    table = _build_table(headers, rows, [12 * mm, 98 * mm, 35 * mm, 35 * mm])
    elements.append(table)

    if _plain(picking.get("note")):
        elements.append(Spacer(1, 6 * mm))
        elements.append(_note(picking["note"]))

    doc.build(elements)
    return filepath
