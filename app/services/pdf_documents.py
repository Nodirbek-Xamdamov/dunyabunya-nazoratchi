"""
PDF document generation service using ReportLab.
Generates 4 types of PDF documents with dark blue header styling.
"""
from __future__ import annotations

import html
import os
import tempfile
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

# ── Dark blue theme ──
HEADER_BG = colors.HexColor("#1e3a5f")
HEADER_FG = colors.white
ROW_ALT = colors.HexColor("#f0f4f8")
BORDER_COLOR = colors.HexColor("#cbd5e1")
ACCENT = colors.HexColor("#2563eb")


def _safe(text: Any) -> str:
    """Escape HTML/text for ReportLab Paragraph."""
    return html.escape(str(text)) if text else ""


def _fmt_money(amount: Any, currency: str = "so'm") -> str:
    try:
        return f"{float(amount):,.2f} {currency}"
    except (ValueError, TypeError):
        return f"0.00 {currency}"


def _build_header(title: str, doc_info: dict) -> list:
    """Build styled header elements."""
    styles = getSampleStyleSheet()
    elements = []

    # Title
    title_style = ParagraphStyle(
        "DocTitle", parent=styles["Title"],
        fontSize=18, textColor=HEADER_FG, backColor=HEADER_BG,
        spaceAfter=6, alignment=1, borderPadding=(8, 8, 8, 8),
    )
    elements.append(Paragraph(_safe(title), title_style))
    elements.append(Spacer(1, 4 * mm))

    # Document info
    info_style = ParagraphStyle("Info", parent=styles["Normal"], fontSize=10, textColor=colors.HexColor("#475569"))
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
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        # Body
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
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


def render_sale_order_pdf(data: dict) -> str:
    """
    Render sale order PDF. Returns path to temp file.
    data: {"order": {...}, "lines": [...]}
    """
    order = data.get("order", {})
    lines = data.get("lines", [])

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=tempfile.gettempdir()) as f:
        filepath = f.name

    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    # Header
    info = {
        "Buyurtma raqami": order.get("name", "N/A"),
        "Sana": order.get("date_order", "N/A"),
        "Mijoz": order.get("partner_id", [0, "N/A"])[1] if isinstance(order.get("partner_id"), list) else "N/A",
        "Sotuv menejeri": order.get("user_id", [0, "N/A"])[1] if isinstance(order.get("user_id"), list) else "N/A",
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
            _safe(pname),
            str(line.get("product_uom_qty", 0)),
            _fmt_money(line.get("price_unit", 0)),
            _fmt_money(line.get("price_subtotal", 0)),
        ])

    # Total row
    rows.append(["", "", "", "JAMI:", _fmt_money(order.get("amount_total", 0))])

    page_width = A4[0] - 30 * mm
    table = _build_table(headers, rows, [15 * mm, 60 * mm, 25 * mm, 35 * mm, 40 * mm])
    elements.append(table)
    elements.append(Spacer(1, 8 * mm))

    # Note
    if order.get("note"):
        note_style = ParagraphStyle("Note", fontSize=9, textColor=colors.HexColor("#64748b"))
        elements.append(Paragraph(f"<b>Eslatma:</b> {_safe(order['note'])}", note_style))

    doc.build(elements)
    return filepath


def render_invoice_pdf(data: dict) -> str:
    """Render invoice (schyot-faktura) PDF."""
    invoice = data.get("invoice", {})
    lines = data.get("lines", [])

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=tempfile.gettempdir()) as f:
        filepath = f.name

    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    info = {
        "Faktura raqami": invoice.get("name", "N/A"),
        "Sana": invoice.get("invoice_date", "N/A"),
        "Mijoz": invoice.get("partner_id", [0, "N/A"])[1] if isinstance(invoice.get("partner_id"), list) else "N/A",
        "Holat": invoice.get("payment_state", "N/A"),
    }
    elements.extend(_build_header("SCHYOT-FAKTURA", info))

    headers = ["#", "Tavsif", "Soni", "Narx", "Jami"]
    rows = []
    for i, line in enumerate(lines, 1):
        rows.append([
            str(i),
            _safe(line.get("name", "")),
            str(line.get("quantity", 0)),
            _fmt_money(line.get("price_unit", 0)),
            _fmt_money(line.get("price_subtotal", 0)),
        ])

    rows.append(["", "", "", "JAMI:", _fmt_money(invoice.get("amount_total", 0))])
    rows.append(["", "", "", "SOLIQ:", _fmt_money(invoice.get("amount_tax", 0))])
    rows.append(["", "", "", "TO'LOV:", _fmt_money(invoice.get("amount_residual", 0))])

    table = _build_table(headers, rows, [15 * mm, 55 * mm, 25 * mm, 35 * mm, 40 * mm])
    elements.append(table)

    doc.build(elements)
    return filepath


def render_payment_receipt_pdf(data: dict) -> str:
    """Render payment receipt PDF."""
    payment = data.get("payment", {})

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=tempfile.gettempdir()) as f:
        filepath = f.name

    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    ptype = "KIRIM" if payment.get("payment_type") == "inbound" else "CHIQIM"
    info = {
        "To'lov raqami": payment.get("name", "N/A"),
        "Sana": payment.get("date", "N/A"),
        "Mijoz": payment.get("partner_id", [0, "N/A"])[1] if isinstance(payment.get("partner_id"), list) else "N/A",
        "Turi": ptype,
        "Summa": _fmt_money(payment.get("amount", 0)),
    }
    elements.extend(_build_header("TO'LOV KVITANSIYASI", info))

    # Simple summary table
    headers = ["Ko'rsatkich", "Qiymat"]
    rows = [
        ["To'lov raqami", _safe(payment.get("name", ""))],
        ["Sana", _safe(payment.get("date", ""))],
        ["Mijoz", _safe(payment.get("partner_id", ["", ""])[1] if isinstance(payment.get("partner_id"), list) else "")],
        ["Summa", _fmt_money(payment.get("amount", 0))],
        ["Holat", _safe(payment.get("state", ""))],
    ]
    table = _build_table(headers, rows, [50 * mm, 100 * mm])
    elements.append(table)

    doc.build(elements)
    return filepath


def render_delivery_slip_pdf(data: dict) -> str:
    """Render delivery slip (nakladnoy) PDF."""
    picking = data.get("picking", {})
    moves = data.get("moves", [])

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=tempfile.gettempdir()) as f:
        filepath = f.name

    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    elements = []

    info = {
        "Xujjat raqami": picking.get("name", "N/A"),
        "Manba": picking.get("location_id", [0, "N/A"])[1] if isinstance(picking.get("location_id"), list) else "N/A",
        "Manzil": picking.get("location_dest_id", [0, "N/A"])[1] if isinstance(picking.get("location_dest_id"), list) else "N/A",
        "Rejalashtirilgan sana": picking.get("scheduled_date", "N/A"),
    }
    elements.extend(_build_header("TASHISH XUJJATI (NAKLADNOY)", info))

    headers = ["#", "Mahsulot", "Buyurtma", "Yetkazilgan"]
    rows = []
    for i, move in enumerate(moves, 1):
        product = move.get("product_id", [0, "N/A"])
        pname = product[1] if isinstance(product, (list, tuple)) else str(product)
        rows.append([
            str(i),
            _safe(pname),
            str(move.get("product_uom_qty", 0)),
            str(move.get("quantity_done", 0)),
        ])

    table = _build_table(headers, rows, [15 * mm, 60 * mm, 35 * mm, 35 * mm])
    elements.append(table)

    if picking.get("note"):
        note_style = ParagraphStyle("Note", fontSize=9, textColor=colors.HexColor("#64748b"))
        elements.append(Spacer(1, 6 * mm))
        elements.append(Paragraph(f"<b>Eslatma:</b> {_safe(picking['note'])}", note_style))

    doc.build(elements)
    return filepath
