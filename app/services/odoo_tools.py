"""Odoo tools for RBAC roles: Moliya, Ombor, Sotuv, CEO analytics."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.config import settings
from app.services.odoo import OdooClient

_odoo = OdooClient()
REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"


def _unguessable_suffix() -> str:
    """8 hex-belgili tasodifiy token — hisobot fayl nomlariga qo'shiladi (P0-2:
    Odoo record ID'sini to'g'ridan-to'g'ri fayl nomiga yozish uni taxmin qilib
    topib bo'ladigan qiladi, masalan invoice_123.pdf, invoice_124.pdf, ...)."""
    return uuid.uuid4().hex[:8]


# ─── MOLIYA TOOLS ──────────────────────────────────────────────────────────

async def search_kassa_chiqim(domain: list | None = None, limit: int = 50) -> list[dict]:
    """Kassa chiqimlarini qidirish (account.move, out_invoice)."""
    domain = domain or [["move_type", "=", "out_invoice"], ["state", "=", "posted"]]
    return await _odoo.execute_kw(
        "account.move", "search_read",
        [domain, ["id", "name", "invoice_date", "amount_total", "partner_id", "state"]],
        {"limit": limit, "order": "invoice_date desc"},
    )


async def create_kassa_chiqim(partner_id: int, amount: float, description: str = "") -> int:
    """Kassa chiqimi uchun qoralama (draft) yaratish."""
    vals = {
        "move_type": "out_invoice",
        "partner_id": partner_id,
        "invoice_line_ids": [(0, 0, {
            "name": description or "Kassa chiqimi",
            "quantity": 1,
            "price_unit": amount,
        })],
    }
    return await _odoo.execute_kw("account.move", "create", [vals])


async def search_payments(domain: list | None = None, limit: int = 50) -> list[dict]:
    """To'lovlarni qidirish (account.payment)."""
    domain = domain or [["state", "=", "posted"]]
    return await _odoo.execute_kw(
        "account.payment", "search_read",
        [domain, ["id", "name", "date", "amount", "partner_id", "payment_type", "state"]],
        {"limit": limit, "order": "date desc"},
    )


async def create_payment_draft(partner_id: int, amount: float, payment_type: str = "outbound") -> int:
    """To'lov qoralamasi yaratish."""
    vals = {
        "partner_id": partner_id,
        "amount": amount,
        "payment_type": payment_type,
    }
    return await _odoo.execute_kw("account.payment", "create", [vals])


async def create_currency_exchange(from_currency_id: int, to_currency_id: int,
                                    amount: float, rate: float) -> int:
    """Valyuta ayirboshlash amaliyotini kiritish."""
    vals = {
        "currency_from_id": from_currency_id,
        "currency_to_id": to_currency_id,
        "amount": amount,
        "rate": rate,
    }
    return await _odoo.execute_kw("res.currency.rate", "create", [vals])


async def get_invoice_pdf(invoice_id: int) -> str | None:
    """Schyot-faktura PDF hujjatini (reportlab bilan) generatsiya qilib, faylni
    REPORTS_DIR'ga saqlaydi va havolasini qaytaradi."""
    try:
        invoices = await _odoo.execute_kw(
            "account.move", "search_read",
            [[["id", "=", invoice_id]]],
            {"fields": ["name", "invoice_date", "partner_id", "payment_state",
                        "amount_total", "amount_tax", "amount_residual"], "limit": 1},
        )
        if not invoices:
            return None
        lines = await _odoo.execute_kw(
            "account.move.line", "search_read",
            [[["move_id", "=", invoice_id], ["display_type", "=", False]]],
            {"fields": ["name", "quantity", "price_unit", "price_subtotal"]},
        )
        import asyncio
        import shutil
        from app.services.pdf_documents import render_invoice_pdf
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = await asyncio.to_thread(render_invoice_pdf, {"invoice": invoices[0], "lines": lines})
        filename = f"invoice_{invoice_id}_{_unguessable_suffix()}.pdf"
        out_path = REPORTS_DIR / filename
        shutil.move(tmp_path, str(out_path))
        return f"{settings.PUBLIC_BASE_URL}/reports/{filename}"
    except Exception:
        return None


async def get_payment_receipt_pdf(payment_id: int) -> str | None:
    """To'lov kvitansiyasi PDF hujjatini generatsiya qilib, havolasini qaytaradi."""
    try:
        payments = await _odoo.execute_kw(
            "account.payment", "search_read",
            [[["id", "=", payment_id]]],
            {"fields": ["name", "date", "partner_id", "payment_type", "amount", "state"], "limit": 1},
        )
        if not payments:
            return None
        import asyncio
        import shutil
        from app.services.pdf_documents import render_payment_receipt_pdf
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = await asyncio.to_thread(render_payment_receipt_pdf, {"payment": payments[0]})
        filename = f"payment_{payment_id}_{_unguessable_suffix()}.pdf"
        out_path = REPORTS_DIR / filename
        shutil.move(tmp_path, str(out_path))
        return f"{settings.PUBLIC_BASE_URL}/reports/{filename}"
    except Exception:
        return None


# ─── OMBOR TOOLS ───────────────────────────────────────────────────────────

async def find_product(query: str, limit: int = 20) -> list[dict]:
    """Mahsulotlarni qidirish."""
    return await _odoo.execute_kw(
        "product.product", "search_read",
        [[["name", "ilike", query], ["type", "=", "product"]],
         ["id", "name", "list_price", "qty_available", "categ_id"]],
        {"limit": limit},
    )


async def check_stock_quant(product_id: int) -> dict | None:
    """Mahsulotning ombordagi qoldig'ini tekshirish."""
    result = await _odoo.execute_kw(
        "stock.quant", "search_read",
        [[["product_id", "=", product_id]],
         ["id", "product_id", "location_id", "quantity", "reserved_quantity"]],
        {"limit": 10},
    )
    return result[0] if result else None


async def check_low_stock(threshold: float = 10.0) -> list[dict]:
    """Kam qoldiqdagi mahsulotlarni aniqlash."""
    all_products = await _odoo.execute_kw(
        "product.product", "search_read",
        [[["type", "=", "product"]],
         ["id", "name", "qty_available", "list_price"]],
        {"limit": 500},
    )
    return [p for p in all_products if (p.get("qty_available") or 0) <= threshold]


async def create_internal_transfer(product_id: int, quantity: float,
                                    from_location_id: int, to_location_id: int) -> int:
    """Ichki ko'chirish (transfer) hujjati yaratish."""
    vals = {
        "location_id": from_location_id,
        "location_dest_id": to_location_id,
        "move_ids": [(0, 0, {
            "product_id": product_id,
            "product_uom_qty": quantity,
            "name": f"Transfer #{product_id}",
        })],
    }
    return await _odoo.execute_kw("stock.picking", "create", [vals])


async def get_delivery_slip_pdf(picking_id: int) -> str | None:
    """Nakladnoy/transfer akti PDF hujjatini generatsiya qilib, havolasini qaytaradi."""
    try:
        pickings = await _odoo.execute_kw(
            "stock.picking", "search_read",
            [[["id", "=", picking_id]]],
            {"fields": ["name", "location_id", "location_dest_id", "scheduled_date", "note"], "limit": 1},
        )
        if not pickings:
            return None
        raw_moves = await _odoo.execute_kw(
            "stock.move", "search_read",
            [[["picking_id", "=", picking_id]]],
            # Bu Odoo versiyasida "quantity_done" mavjud emas — "quantity" ishlatiladi
            {"fields": ["product_id", "product_uom_qty", "quantity"]},
        )
        moves = [{**m, "quantity_done": m.get("quantity", 0)} for m in raw_moves]
        import asyncio
        import shutil
        from app.services.pdf_documents import render_delivery_slip_pdf
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = await asyncio.to_thread(render_delivery_slip_pdf, {"picking": pickings[0], "moves": moves})
        filename = f"delivery_{picking_id}_{_unguessable_suffix()}.pdf"
        out_path = REPORTS_DIR / filename
        shutil.move(tmp_path, str(out_path))
        return f"{settings.PUBLIC_BASE_URL}/reports/{filename}"
    except Exception:
        return None


# ─── SOTUV TOOLS ───────────────────────────────────────────────────────────

async def find_partner(query: str, limit: int = 20) -> list[dict]:
    """Mijozlarni qidirish."""
    return await _odoo.execute_kw(
        "res.partner", "search_read",
        [[["name", "ilike", query]],
         ["id", "name", "email", "phone", "category_id"]],
        {"limit": limit},
    )


async def search_crm_leads(domain: list | None = None, limit: int = 50) -> list[dict]:
    """CRM lidlarini qidirish."""
    domain = domain or []
    return await _odoo.execute_kw(
        "crm.lead", "search_read",
        [domain, ["id", "name", "partner_name", "stage_id", "create_date", "user_id"]],
        {"limit": limit, "order": "create_date desc"},
    )


async def create_crm_lead(name: str, partner_name: str = "", planned_revenue: float = 0.0) -> int:
    """Yangi CRM lead yaratish."""
    vals = {"name": name}
    if partner_name:
        vals["partner_name"] = partner_name
    if planned_revenue:
        vals["planned_revenue"] = planned_revenue
    return await _odoo.execute_kw("crm.lead", "create", [vals])


async def find_stale_leads(days: int = 30) -> list[dict]:
    """Harakatsiz (eski) CRM lidlarni aniqlash."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    leads = await _odoo.execute_kw(
        "crm.lead", "search_read",
        [[["create_date", "<", cutoff], ["stage_id.is_won", "=", False]],
         ["id", "name", "partner_name", "create_date", "stage_id", "user_id"]],
        {"limit": 50, "order": "create_date asc"},
    )
    return leads


async def create_sale_order_draft(partner_id: int, order_lines: list[dict]) -> int:
    """Sotuv buyurtmasi qoralamasi yaratish.
    order_lines: [{"product_id": int, "price_unit": float, "product_uom_qty": float}]
    """
    line_vals = []
    for line in order_lines:
        line_vals.append((0, 0, {
            "product_id": line["product_id"],
            "price_unit": line.get("price_unit", 0.0),
            "product_uom_qty": line.get("product_uom_qty", 1.0),
        }))
    vals = {
        "partner_id": partner_id,
        "order_line": line_vals,
    }
    return await _odoo.execute_kw("sale.order", "create", [vals])


async def check_partner_debt(partner_id: int) -> dict:
    """Mijozning qarzdorligini tekshirish."""
    invoices = await _odoo.execute_kw(
        "account.move", "search_read",
        [[["partner_id", "=", partner_id], ["move_type", "=", "out_invoice"],
          ["state", "=", "posted"], ["amount_residual", ">", 0]],
         ["id", "name", "invoice_date", "amount_total", "amount_residual"]],
        {"limit": 50, "order": "invoice_date desc"},
    )
    total_debt = sum(inv.get("amount_residual", 0) for inv in invoices)
    return {
        "partner_id": partner_id,
        "total_debt": total_debt,
        "unpaid_invoices": len(invoices),
        "invoices": invoices,
    }


async def get_sale_order_pdf(order_id: int) -> str | None:
    """Sotuv buyurtmasi PDF hujjatini generatsiya qilib, havolasini qaytaradi."""
    try:
        orders = await _odoo.execute_kw(
            "sale.order", "search_read",
            [[["id", "=", order_id]]],
            {"fields": ["name", "date_order", "partner_id", "user_id", "amount_total", "note"], "limit": 1},
        )
        if not orders:
            return None
        lines = await _odoo.execute_kw(
            "sale.order.line", "search_read",
            [[["order_id", "=", order_id], ["display_type", "=", False]]],
            {"fields": ["product_id", "product_uom_qty", "price_unit", "price_subtotal"]},
        )
        import asyncio
        import shutil
        from app.services.pdf_documents import render_sale_order_pdf
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = await asyncio.to_thread(render_sale_order_pdf, {"order": orders[0], "lines": lines})
        filename = f"sale_order_{order_id}_{_unguessable_suffix()}.pdf"
        out_path = REPORTS_DIR / filename
        shutil.move(tmp_path, str(out_path))
        return f"{settings.PUBLIC_BASE_URL}/reports/{filename}"
    except Exception:
        return None


# ─── CEO ANALYTICS TOOLS ───────────────────────────────────────────────────


def _clean_cell_value(val):
    """Odoo qiymatlarini o'qilishi oson formatga o'tkazish.
    (id, name) m2o/m2m tuple'larini nomga aylantiradi."""
    if isinstance(val, (list, tuple)) and len(val) == 2 and isinstance(val[0], (int, float)):
        # many2one: [id, name]
        return val[1] if val[1] else str(val[0])
    if isinstance(val, (list, tuple)):
        # many2many: [[id, name], ...]
        cleaned = [_clean_cell_value(v) for v in val]
        return ", ".join(str(v) for v in cleaned)
    return val


async def _search_read_clean(model: str, domain: list | None = None,
                             fields: list | None = None, limit: int = 1000) -> list[dict]:
    """Odoo'dan yozuvlarni o'qib, nomlarini ochib beradi."""
    flds = fields or []
    if not flds:
        # Model maydonlarini avtomatik aniqlash
        meta = await _odoo.execute_kw(
            model, "fields_get", [], {"attributes": ["string", "type"]}
        )
        skip = {"one2many", "many2many", "binary", "text"}
        flds = [f for f, m in meta.items() if m.get("type") not in skip][:25]
    rows = await _odoo.execute_kw(
        model, "search_read", [domain or [], flds], {"limit": limit}
    )
    return [{k: _clean_cell_value(v) for k, v in r.items()} for r in rows]


async def export_excel(model: str = "", domain: list | None = None,
                       fields: list | None = None, title: str = "Hisobot",
                       limit: int = 10000) -> str:
    """Odoo ma'lumotlarini haqiqiy Excel (.xlsx) faylga eksport qiladi.

    Foydalanuvchi excel/xlsx fayl so'raganda shu toolni chaqiring.
    model: qaysi Odoo model (masalan hr.employee, sale.order)
    domain/fields: qidiruv shartlari — avvalgi tool natijasiga mos bo'lsin.
    Fayl diskka yoziladi va havola qaytariladi (bot faylni ham yuboradi).
    """
    import re as _re
    import uuid
    from datetime import datetime

    if not model:
        return "Excel uchun model ko'rsatilmadi. Masalan: model='hr.employee'"

    rows = await _search_read_clean(model, domain, fields, limit=limit)
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception as e:
        return f"Excel kutubxonasi yo'q: {e}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Ustunlarni tartiblash: birinchi qator kalitlari + keyin qo'shimchalari
    cols: list[str] = []
    for row in rows:
        for k in row.keys():
            if k not in cols:
                cols.append(k)
    if not cols:
        return "Eksport uchun ustunlar topilmadi."

    wb = Workbook()
    ws = wb.active
    ws.title = (title or "Hisobot")[:31]

    # Sarlavha
    header_fill = PatternFill("solid", fgColor="1A1A2E")
    header_font = Font(bold=True, color="FFFFFF")
    for ci, col in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=ci, value=str(col))
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    # Ma'lumotlar — 0 qiymatlar bo'sh (blank) qoldiriladi
    for ri, row in enumerate(rows, start=2):
        for ci, col in enumerate(cols, start=1):
            val = row.get(col)
            if isinstance(val, (int, float)) and val == 0:
                val = ""
            ws.cell(row=ri, column=ci, value=val)

    # Ustun kengliklari
    for ci, col in enumerate(cols, start=1):
        letter = get_column_letter(ci)
        max_len = max([len(str(col))] + [len(str(row.get(col, ""))) for row in rows[:200]])
        ws.column_dimensions[letter].width = min(max_len + 2, 45)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    slug = _re.sub(r"[^a-z0-9\u0400-\u04ff]+", "_", str(title).lower().replace("'", "")).strip("_") or "export"
    slug = _re.sub(r"_+", "_", slug)
    filename = f"{slug[:40]}_{uuid.uuid4().hex[:8]}.xlsx"
    out_path = REPORTS_DIR / filename
    wb.save(out_path)

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/reports/{filename}"


async def generate_html_report(model: str = "", domain: list | None = None,
                               fields: list | None = None,
                               title: str = "Hisobot") -> str:
    """Har qanday Odoo model ma'lumotlaridan HTML hisobot (jadval + grafik) yaratadi.

    Foydalanuvchi HTML/grafik/jadval ko'rinishida so'raganda ishlatiladi.
    model: masalan hr.employee, sale.order, crm.lead
    """
    from app.services.claude_report import generate_report_link

    if not model:
        return "HTML hisobot uchun model ko'rsatilmadi. Masalan: model='hr.employee'"

    rows = await _search_read_clean(model, domain, fields, limit=1000)
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    # id ustunini olib tashlash (keraksiz)
    for row in rows:
        row.pop("id", None)

    link = generate_report_link(
        title=title,
        description=f"{model} — {len(rows)} ta yozuv",
        rows=rows,
    )
    return link or "Hisobot yaratilmadi."


async def aggregate_data(model: str, group_by: str, fields: list[str],
                         domain: list | None = None) -> list[dict]:
    """Ma'lumotlarni guruhlab hisoblash."""
    domain = domain or []
    return await _odoo.execute_kw(
        model, "read_group",
        [domain, fields, [group_by]],
    )


async def growth_dynamics(model: str, date_field: str, value_field: str,
                           domain: list | None = None) -> list[dict]:
    """O'sish dinamikasini hisoblash."""
    domain = domain or []
    return await _odoo.execute_kw(
        model, "read_group",
        [domain, [value_field], [f"{date_field}:month"]],
    )


async def search_employees_readonly(query: str = "") -> list[dict]:
    """Xodimlarni o'qish rejimida qidirish."""
    domain = [["active", "=", True]]
    if query:
        domain.append(["name", "ilike", query])
    return await _odoo.execute_kw(
        "hr.employee", "search_read",
        [domain, ["id", "name", "job_id", "department_id", "work_email"]],
        {"limit": 50},
    )


# ─── FORMAT EXPORTS (Word/PDF/CSV) ──────────────────────────────────────────

def _export_slug(title: str) -> str:
    import re as _re
    slug = _re.sub(r"[^a-z0-9\u0400-\u04ff]+", "_", str(title).lower().replace("'", "")).strip("_") or "export"
    return _re.sub(r"_+", "_", slug)[:40]


async def _export_rows(model: str, domain: list | None = None,
                       fields: list | None = None, limit: int = 10000):
    """Eksport uchun tozalangan qatorlar + ustunlar ro'yxati."""
    rows = await _search_read_clean(model, domain, fields, limit=limit)
    if not rows:
        return [], []
    cols: list[str] = []
    for row in rows:
        for k in row.keys():
            if k not in cols:
                cols.append(k)
    return rows, cols


def _display_value(val) -> str:
    """Katakka yoziladigan qiymat — 0 bo'lsa bo'sh qoldiriladi."""
    if isinstance(val, (int, float)) and val == 0:
        return ""
    if isinstance(val, bool):
        return "Ha" if val else "Yo'q"
    return val


async def export_word(model: str = "", domain: list | None = None,
                      fields: list | None = None, title: str = "Hisobot",
                      limit: int = 10000) -> str:
    """Odoo ma'lumotlaridan Word (.docx) hujjat yaratadi.
    Foydalanuvchi 'word', 'docx', 'hujjat qilib ber' so'rasa ishlatiladi.
    model: qaysi Odoo model (masalan hr.employee, sale.order)
    """
    import uuid
    from datetime import datetime

    if not model:
        return "Word uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    rows, cols = await _export_rows(model, domain, fields, limit=limit)
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    try:
        import docx
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.shared import Pt
    except Exception as e:
        return f"Word kutubxonasi yo'q: {e}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_heading(title or "Hisobot", level=1)
    p = doc.add_paragraph()
    p.add_run(f"{model} — {len(rows)} ta yozuv • {datetime.now().strftime('%Y-%m-%d %H:%M')}").italic = True

    table = doc.add_table(rows=1, cols=len(cols))
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0].cells
    for i, col in enumerate(cols):
        hdr[i].text = str(col)
        for para in hdr[i].paragraphs:
            for run in para.runs:
                run.bold = True

    for row in rows:
        cells = table.add_row().cells
        for ci, col in enumerate(cols):
            val = _display_value(row.get(col))
            cells[ci].text = str(val) if val is not None else ""

    filename = f"{_export_slug(title)}_{uuid.uuid4().hex[:8]}.docx"
    out_path = REPORTS_DIR / filename
    doc.save(out_path)
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/reports/{filename}"


async def export_pdf(model: str = "", domain: list | None = None,
                     fields: list | None = None, title: str = "Hisobot",
                     limit: int = 10000) -> str:
    """Odoo ma'lumotlaridan PDF hisobot yaratadi.
    Foydalanuvchi 'pdf', 'pdf qilib ber' so'rasa ishlatiladi.
    """
    import uuid

    if not model:
        return "PDF uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    rows, cols = await _export_rows(model, domain, fields, limit=limit)
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
    except Exception as e:
        return f"PDF kutubxonasi yo'q: {e}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_export_slug(title)}_{uuid.uuid4().hex[:8]}.pdf"
    out_path = REPORTS_DIR / filename

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleX", parent=styles["Title"], fontSize=16)
    head_style = ParagraphStyle("HeadX", parent=styles["Heading3"], fontSize=9, textColor=colors.white)
    cell_style = ParagraphStyle("CellX", parent=styles["BodyText"], fontSize=7.5, leading=9)

    doc = SimpleDocTemplate(str(out_path), pagesize=landscape(A4),
                            leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=title or "Hisobot")

    story = [Paragraph(str(title or "Hisobot"), title_style),
             Spacer(1, 4 * mm),
             Paragraph(f"{model} — {len(rows)} ta yozuv", styles["Normal"])]

    data = [[Paragraph(f"<b>{col}</b>", head_style) for col in cols]]
    for row in rows:
        data.append([Paragraph(str(_display_value(row.get(col)) or ""), cell_style) for col in cols])

    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A1A2E")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f8")]),
    ]))
    story.append(Spacer(1, 4 * mm))
    story.append(t)
    doc.build(story)

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/reports/{filename}"


async def export_csv(model: str = "", domain: list | None = None,
                     fields: list | None = None, title: str = "Hisobot",
                     limit: int = 10000) -> str:
    """Odoo ma'lumotlaridan CSV fayl yaratadi (Excel ocha oladi)."""
    import csv
    import uuid

    if not model:
        return "CSV uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    rows, cols = await _export_rows(model, domain, fields, limit=limit)
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_export_slug(title)}_{uuid.uuid4().hex[:8]}.csv"
    out_path = REPORTS_DIR / filename

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for row in rows:
            writer.writerow([str(_display_value(row.get(c)) or "") for c in cols])

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/reports/{filename}"


# ─── MATN (TELEGRAM CHATIGA) EKSPORT ────────────────────────────────────────

TEXT_EXPORT_MAX_ROWS = 300  # bundan ko'pi chatda o'qib bo'lmaydi — Excel tavsiya qilinadi

# token -> tayyor Telegram-HTML matn. export_text shu yerga yozadi, assistant.ask_with_files
# olib ketadi: ro'yxatni model qayta yozmaydi (qisqartirmaydi, xato qilmaydi) — bot
# o'zi to'liq yuboradi.
_text_exports: dict[str, str] = {}


def pop_text_export(token: str) -> str | None:
    return _text_exports.pop(token, None)


def _text_value(val) -> str:
    """Matn eksportidagi qiymat: bo'sh/False — "", son — minglik ajratgich bilan."""
    if val is None or val is False or val == "":
        return ""
    if val is True:
        return "Ha"
    if isinstance(val, float):
        return f"{val:,.2f}".rstrip("0").rstrip(".")
    if isinstance(val, int):
        return f"{val:,}"
    return str(val).strip()


async def export_text(model: str = "", domain: list | None = None,
                      fields: list | None = None, title: str = "Ro'yxat",
                      group_by: str = "", limit: int = TEXT_EXPORT_MAX_ROWS) -> dict | str:
    """Odoo yozuvlari ro'yxatini FAYLSIZ — Telegram chatining o'ziga matn ko'rinishida
    tayyorlaydi (foydalanuvchi "matn", "shu yerga yoz" desa). Birinchi field — asosiy
    (qalin) ustun, qolganlari "Nomi: qiymat" ko'rinishida; group_by berilsa yozuvlar
    shu field bo'yicha guruhlab chiqariladi."""
    import asyncio
    import html

    if not model:
        return "Matn uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    limit = max(1, min(int(limit or TEXT_EXPORT_MAX_ROWS), TEXT_EXPORT_MAX_ROWS))
    flds = [f for f in (fields or []) if f != "id"] or ["display_name"]
    if group_by and group_by not in flds:
        flds.append(group_by)

    rows, total = await asyncio.gather(
        _search_read_clean(model, domain, flds, limit=limit),
        _odoo.execute_kw(model, "search_count", [domain or []]),
    )
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    try:
        meta = await _odoo.execute_kw(model, "fields_get", [flds], {"attributes": ["string"]})
    except Exception:
        meta = {}  # nomlarsiz ham chiqaveradi — texnik field nomi ishlatiladi
    labels = {f: (meta.get(f) or {}).get("string") or f for f in flds}

    main, *rest = [f for f in flds if f != group_by] or flds

    def _line(n: int, row: dict) -> str:
        head = html.escape(_text_value(row.get(main)) or "—", quote=False)
        details = [
            f"{html.escape(str(labels[f]), quote=False)}: {html.escape(v, quote=False)}"
            for f in rest if (v := _text_value(row.get(f)))
        ]
        return f"{n}. <b>{head}</b>" + (" — " + "; ".join(details) if details else "")

    shown = len(rows)
    safe_title = html.escape(str(title or "Ro'yxat"), quote=False)
    header = f"<b>{safe_title}</b> — {shown:,} ta yozuv"
    if total > shown:
        header += (f"\nJami {total:,} ta yozuvdan dastlabki {shown:,} tasi ko'rsatildi — "
                   f"to'liq ro'yxat uchun Excel faylni tanlang.")
    out = [header, ""]
    if group_by:
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(_text_value(row.get(group_by)), []).append(row)
        # Guruhsiz ("") yozuvlar oxirida
        for name in sorted(groups, key=lambda g: (g == "", g.casefold())):
            members = groups[name]
            group_title = html.escape(name or "Ko'rsatilmagan", quote=False)
            out.append(f"<b>{group_title}</b> ({len(members)} ta)")
            out.extend(_line(n, row) for n, row in enumerate(members, start=1))
            out.append("")
    else:
        out.extend(_line(n, row) for n, row in enumerate(rows, start=1))

    token = uuid.uuid4().hex[:12]
    _text_exports[token] = "\n".join(out).strip()
    return {
        "text_export": token,
        "rows_shown": shown,
        "rows_total": total,
        "note": "To'liq ro'yxat foydalanuvchiga alohida xabar bilan yuboriladi. Javobingizda "
                "ro'yxatni QAYTA YOZMANG — faqat bir jumla yozing (masalan: jami nechta yozuv).",
    }


# ─── CEO EXTRA ANALYTICS ───────────────────────────────────────────────────

async def get_top_debtors(limit: int = 10) -> list[dict]:
    """Eng katta qarzdor mijozlar (to'lanmagan fakturalar bo'yicha)."""
    return await _odoo.execute_kw(
        "account.move", "search_read",
        [[["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"],
          ["amount_residual", ">", 0]],
         ["id", "name", "partner_id", "invoice_date", "invoice_date_due", "amount_total", "amount_residual"]],
        {"limit": limit, "order": "amount_residual desc"},
    )


async def get_overdue_invoices(days: int = 30) -> list[dict]:
    """Muddati o'tgan to'lanmagan fakturalar."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return await _odoo.execute_kw(
        "account.move", "search_read",
        [[["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"],
          ["amount_residual", ">", 0], ["invoice_date_due", "<", cutoff]],
         ["id", "name", "partner_id", "invoice_date", "invoice_date_due", "amount_total", "amount_residual"]],
        {"limit": 100, "order": "invoice_date_due asc"},
    )


async def get_cashflow_summary(date_from: str = "", date_to: str = "") -> list[dict]:
    """Kassa/pul oqimi xulosasi — oylar bo'yicha kirim va chiqim (account.payment)."""
    domain = [["state", "=", "posted"]]
    if date_from:
        domain.append(["date", ">=", date_from])
    if date_to:
        domain.append(["date", "<=", date_to])
    return await _odoo.execute_kw(
        "account.payment", "read_group",
        [domain, ["amount", "payment_type"], ["date:month"]],
    )


async def get_sales_by_seller(date_from: str = "", date_to: str = "", limit: int = 10) -> list[dict]:
    """Sotuvchilar bo'yicha sotuv reytingi (sale.order user_id)."""
    domain = [["state", "in", ["sale", "done"]]]
    if date_from:
        domain.append(["date_order", ">=", date_from])
    if date_to:
        domain.append(["date_order", "<=", date_to])
    return await _odoo.execute_kw(
        "sale.order", "read_group",
        [domain, ["amount_total", "id:count"], ["user_id"]],
        {"limit": limit},
    )


async def get_employees_by_department() -> list[dict]:
    """Xodimlar soni bo'limlar bo'yicha (hr.employee)."""
    return await _odoo.execute_kw(
        "hr.employee", "read_group",
        [[["active", "=", True], ["department_id", "!=", False]], ["id:count"], ["department_id"]],
    )


async def get_upcoming_deadlines(days: int = 7) -> list[dict]:
    """Yaqin muddatdagi (N kun ichida) ochiq vazifalar."""
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return await _odoo.execute_kw(
        "project.task", "search_read",
        [[["state", "not in", ["done", "cancelled"]], ["date_deadline", ">=", today],
          ["date_deadline", "<=", end]],
         ["id", "name", "project_id", "user_ids", "date_deadline", "state", "priority"]],
        {"limit": 100, "order": "date_deadline asc"},
    )


async def get_crm_funnel() -> list[dict]:
    """CRM funnel — lidlar bosqichlar bo'yicha (faqat mavjud maydonlar)."""
    return await _odoo.execute_kw(
        "crm.lead", "read_group",
        [[], ["id:count"], ["stage_id"]],
    )


async def get_profit_summary(date_from: str = "", date_to: str = "") -> dict:
    """Foyda xulosasi: sotuv, to'langan, qarz va to'lovlar balansi."""
    # To'lanmagan qarz (customer invoices)
    domain = [["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"]]
    if date_from:
        domain.append(["invoice_date", ">=", date_from])
    if date_to:
        domain.append(["invoice_date", "<=", date_to])
    invoices = await _odoo.execute_kw(
        "account.move", "search_read",
        [domain, ["amount_total", "amount_residual"]],
        {"limit": 500},
    )
    total_invoiced = sum(i.get("amount_total", 0) or 0 for i in invoices)
    total_residual = sum(i.get("amount_residual", 0) or 0 for i in invoices)

    # To'lovlar (income payments)
    pdomain = [["state", "=", "posted"], ["payment_type", "=", "inbound"]]
    if date_from:
        pdomain.append(["date", ">=", date_from])
    if date_to:
        pdomain.append(["date", "<=", date_to])
    payments = await _odoo.execute_kw(
        "account.payment", "search_read",
        [pdomain, ["amount"]],
        {"limit": 1000},
    )
    total_paid = sum(p.get("amount", 0) or 0 for p in payments)

    return {
        "jami_faktura": round(total_invoiced, 2),
        "to_langan": round(total_paid, 2),
        "qolgan_qarz": round(total_residual, 2),
        "fakturalar_soni": len(invoices),
    }


# ─── STATISTICS TOOLS ───────────────────────────────────────────────────────

async def get_sales_stats(date_from: str = "", date_to: str = "", group_by: str = "date_order:month") -> list[dict]:
    """Sotuv statistikasi — sale.order bo'yicha guruhlab hisoblash."""
    domain = [["state", "in", ["sale", "done"]]]
    if date_from:
        domain.append(["date_order", ">=", date_from])
    if date_to:
        domain.append(["date_order", "<=", date_to])
    return await _odoo.execute_kw(
        "sale.order", "read_group",
        [domain, ["amount_total", "amount_tax"], [group_by]],
    )

async def get_sales_by_partner(date_from: str = "", date_to: str = "", limit: int = 20) -> list[dict]:
    """Sotuv statistikasi — mijozlar bo'yicha."""
    domain = [["state", "in", ["sale", "done"]]]
    if date_from:
        domain.append(["date_order", ">=", date_from])
    if date_to:
        domain.append(["date_order", "<=", date_to])
    return await _odoo.execute_kw(
        "sale.order", "read_group",
        [domain, ["amount_total"], ["partner_id"]],
        {"limit": limit},
    )

async def get_sales_by_product(date_from: str = "", date_to: str = "", limit: int = 20) -> list[dict]:
    """Sotuv statistikasi — mahsulotlar bo'yicha."""
    domain = []
    if date_from:
        domain.append(["create_date", ">=", date_from])
    if date_to:
        domain.append(["create_date", "<=", date_to])
    return await _odoo.execute_kw(
        "sale.order.line", "read_group",
        [domain, ["price_subtotal", "product_uom_qty"], ["product_id"]],
        {"limit": limit},
    )

async def get_invoice_stats(date_from: str = "", date_to: str = "") -> list[dict]:
    """Schyot-faktura statistikasi."""
    domain = [["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"]]
    if date_from:
        domain.append(["invoice_date", ">=", date_from])
    if date_to:
        domain.append(["invoice_date", "<=", date_to])
    return await _odoo.execute_kw(
        "account.move", "read_group",
        [domain, ["amount_total", "amount_residual"], ["invoice_date:month"]],
    )

async def get_crm_stats() -> list[dict]:
    """CRM lidlar statistikasi."""
    return await _odoo.execute_kw(
        "crm.lead", "read_group",
        [[], ["id:count"], ["stage_id"]],
    )

async def get_task_stats() -> list[dict]:
    """Vazifalar statistikasi."""
    return await _odoo.execute_kw(
        "project.task", "read_group",
        [[], ["id:count"], ["state"]],
    )

async def generate_sales_report(date_from: str = "", date_to: str = "") -> str:
    """Sotuv hisoboti — boyitilgan HTML report: grafik + jadvallar + tahlil."""
    from app.services.claude_report import generate_report_link
    import asyncio
    import uuid, os
    from datetime import datetime

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Oylik sotuv
    domain = [["state", "in", ["sale", "done"]]]
    if date_from:
        domain.append(["date_order", ">=", date_from])
    if date_to:
        domain.append(["date_order", "<=", date_to])
    
    # 2. Mijozlar bo'yicha (top 15)
    line_domain = []
    if date_from:
        line_domain.append(["create_date", ">=", date_from])
    if date_to:
        line_domain.append(["create_date", "<=", date_to])

    # Mustaqil statistikalarni PARALLEL o'qiymiz
    monthly, by_partner, by_product, by_seller = await asyncio.gather(
        _odoo.execute_kw(
            "sale.order", "read_group",
            [domain, ["amount_total", "amount_tax"], ["date_order:month"]],
            {},
        ),
        _odoo.execute_kw(
            "sale.order", "read_group",
            [domain, ["amount_total", "id:count"], ["partner_id"]],
            {"limit": 15},
        ),
        _odoo.execute_kw(
            "sale.order.line", "read_group",
            [line_domain, ["price_subtotal", "product_uom_qty"], ["product_id"]],
            {"limit": 15},
        ),
        _odoo.execute_kw(
            "sale.order", "read_group",
            [domain, ["amount_total", "id:count"], ["user_id"]],
            {"limit": 10},
        ),
    )
    
    # 5. Umumiy statistika
    total_orders = sum(m.get("date_order_count", 0) for m in monthly)
    total_amount = sum(m.get("amount_total", 0) for m in monthly)
    total_tax = sum(m.get("amount_tax", 0) for m in monthly)
    
    # HTML report yaratish
    filename = f"sales_{uuid.uuid4().hex[:12]}.html"
    
    # Oylik chart data
    month_labels = [str(m.get("date_order:month", "")) for m in monthly]
    month_values = [round(m.get("amount_total", 0), 0) for m in monthly]
    month_orders = [m.get("date_order_count", 0) for m in monthly]
    
    # Mijozlar chart data
    partner_labels = []
    partner_values = []
    for p in by_partner[:10]:
        name = str(p.get("partner_id", ["Noma'lum"])[1])[:25] if isinstance(p.get("partner_id"), list) else "Noma'lum"
        partner_labels.append(name)
        partner_values.append(round(p.get("amount_total", 0), 0))
    
    # Mahsulotlar chart data
    product_labels = []
    product_values = []
    for pr in by_product[:10]:
        name = str(pr.get("product_id", ["Noma'lum"])[1])[:25] if isinstance(pr.get("product_id"), list) else "Noma'lum"
        product_labels.append(name)
        product_values.append(round(pr.get("price_subtotal", 0), 0))
    
    # Sotuvchilar chart data
    seller_labels = []
    seller_values = []
    for s in by_seller[:8]:
        name = str(s.get("user_id", ["Noma'lum"])[1])[:25] if isinstance(s.get("user_id"), list) else "Noma'lum"
        seller_labels.append(name)
        seller_values.append(round(s.get("amount_total", 0), 0))
    
    def fmt(n):
        if n >= 1_000_000_000:
            return f"{n/1_000_000_000:.1f} mlrd"
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f} mln"
        if n >= 1_000:
            return f"{n/1_000:.1f} ming"
        return str(int(n))

    # Grafik chizish qoidasi: bo'sh, yagona yoki 0 qiymatli ma'lumotga grafik chizilmaydi
    def chart_ok(values):
        return sum(1 for v in values if v) >= 2

    def kpi_chips(labels, values):
        chips = ""
        for lb, vl in zip(labels, values):
            if vl:
                chips += (f'<div class="kpi-chip"><div class="kpi-value">{fmt(vl)}</div>'
                          f'<div class="kpi-label">{lb}</div></div>')
        return f'<div class="kpi-row">{chips}</div>' if chips else ""
    
    def bars_html(labels, values, color="#4361ee"):
        if not values:
            return ""
        mx = max(values) or 1
        items = ""
        for lb, vl in zip(labels, values):
            pct = (vl / mx) * 100
            items += f'<div class="bar-row"><div class="bar-label">{lb}</div><div class="bar-container"><div class="bar" style="width:{pct:.0f}%;background:{color}"></div></div><div class="bar-value">{fmt(vl)}</div></div>'
        return items
    
    def table_html(headers, rows_data):
        ths = "".join(f"<th>{h}</th>" for h in headers)
        trs = ""
        for row in rows_data:
            cells = ""
            for i, cell in enumerate(row):
                cls = ' class="number"' if isinstance(cell, (int, float)) else ""
                if isinstance(cell, (int, float)) and cell == 0:
                    # 0 qiymatlar bo'sh qoldiriladi
                    val = ""
                else:
                    val = fmt(cell) if isinstance(cell, (int, float)) else str(cell)
                cells += f"<td{cls}>{val}</td>"
            trs += f"<tr>{cells}</tr>"
        return f'<table><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>'
    
    # Jadvallar
    monthly_table = table_html(
        ["Oy", "Buyurtmalar", "Sotuv summasi"],
        [[str(m.get("date_order:month", "")), m.get("date_order_count", 0), round(m.get("amount_total", 0), 0)] for m in monthly]
    )
    
    partner_table = table_html(
        ["Mijoz", "Buyurtmalar", "Summa"],
        [[str(p.get("partner_id", [""])[1])[:40] if isinstance(p.get("partner_id"), list) else "", p.get("id:count", 0), round(p.get("amount_total", 0), 0)] for p in by_partner[:15]]
    )
    
    product_table = table_html(
        ["Mahsulot", "Miqdori", "Summa"],
        [[str(pr.get("product_id", [""])[1])[:40] if isinstance(pr.get("product_id"), list) else "", round(pr.get("product_uom_qty", 0), 0), round(pr.get("price_subtotal", 0), 0)] for pr in by_product[:15]]
    )
    
    seller_table = table_html(
        ["Sotuvchi", "Buyurtmalar", "Summa"],
        [[str(s.get("user_id", [""])[1])[:30] if isinstance(s.get("user_id"), list) else "", s.get("id:count", 0), round(s.get("amount_total", 0), 0)] for s in by_seller[:10]]
    )
    
    # Grafik shartlari: kamida 2 ta nol bo'lmagan qiymat bo'lsa chart, aks holda KPI kartochkalari
    c1_ok = chart_ok(month_values)
    c2_ok = chart_ok(partner_values)
    c3_ok = chart_ok(product_values)
    c4_ok = chart_ok(seller_values)

    c1_visual = '<div class="chart-wrap"><canvas id="c1"></canvas></div>' if c1_ok else kpi_chips(month_labels, month_values)
    c2_visual = '<div class="chart-wrap"><canvas id="c2"></canvas></div>' if c2_ok else kpi_chips(partner_labels, partner_values)
    c3_visual = '<div class="chart-wrap"><canvas id="c3"></canvas></div>' if c3_ok else kpi_chips(product_labels, product_values)
    c4_visual = '<div class="chart-wrap"><canvas id="c4"></canvas></div>' if c4_ok else kpi_chips(seller_labels, seller_values)

    charts_js = []
    if c1_ok:
        charts_js.append(
            f"new Chart(C('c1'),{{type:'bar',data:{{labels:{month_labels},datasets:[{{label:'Sotuv',data:{month_values},backgroundColor:'#6366f1',borderRadius:6}},{{label:'Buyurtmalar',data:{month_orders},backgroundColor:'#8b5cf6',borderRadius:6}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'top',labels:{{color:'#cbd5e1'}}}}}},scales:{{y:{{beginAtZero:true,grid:{{color:'rgba(148,163,184,0.08)'}},ticks:{{callback:v=>v>=1e9?(v/1e9).toFixed(0)+' mlrd':v>=1e6?(v/1e6).toFixed(0)+' mln':v}}}}}}}}}});"
        )
    if c2_ok:
        charts_js.append(
            f"new Chart(C('c2'),{{type:'bar',data:{{labels:{partner_labels},datasets:[{{label:'Summa',data:{partner_values},backgroundColor:'#f472b6',borderRadius:6}}]}},options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{beginAtZero:true,grid:{{color:'rgba(148,163,184,0.08)'}},ticks:{{callback:v=>v>=1e9?(v/1e9).toFixed(0)+' mlrd':v>=1e6?(v/1e6).toFixed(0)+' mln':v}}}}}}}}}});"
        )
    if c3_ok:
        charts_js.append(
            f"new Chart(C('c3'),{{type:'bar',data:{{labels:{product_labels},datasets:[{{label:'Summa',data:{product_values},backgroundColor:'#22d3ee',borderRadius:6}}]}},options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{beginAtZero:true,grid:{{color:'rgba(148,163,184,0.08)'}},ticks:{{callback:v=>v>=1e9?(v/1e9).toFixed(0)+' mlrd':v>=1e6?(v/1e6).toFixed(0)+' mln':v}}}}}}}}}});"
        )
    if c4_ok:
        charts_js.append(
            f"new Chart(C('c4'),{{type:'doughnut',data:{{labels:{seller_labels},datasets:[{{data:{seller_values},backgroundColor:['#6366f1','#a855f7','#ec4899','#f472b6','#22d3ee','#fbbf24','#f43f5e','#34d399']}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'right',labels:{{color:'#cbd5e1'}}}}}}}}}});"
        )
    charts_js = "\n".join(charts_js)

    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sotuv hisoboti</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#0f172a 100%); color:#e2e8f0; padding:20px; min-height:100vh; }}
.container {{ max-width:1000px; margin:0 auto; }}
h1 {{ font-size:1.5em; margin-bottom:4px; font-weight:800; color:#fff; }}
.sub {{ color:#94a3b8; font-size:.9em; margin-bottom:20px; }}
.cards {{ display:flex; gap:14px; margin-bottom:20px; flex-wrap:wrap; }}
.card {{ background:rgba(30,41,59,.75); border:1px solid rgba(148,163,184,.12); border-radius:16px; padding:18px 20px; flex:1; min-width:150px; box-shadow:0 8px 24px rgba(0,0,0,.25); }}
.card .lbl {{ font-size:.8em; color:#94a3b8; margin-bottom:2px; }}
.card .val {{ font-size:1.7em; font-weight:700; color:#c7d2fe; }}
.section {{ background:rgba(30,41,59,.75); border:1px solid rgba(148,163,184,.12); border-radius:16px; padding:20px; margin-bottom:16px; box-shadow:0 8px 24px rgba(0,0,0,.25); }}
.section h2 {{ font-size:1.1em; margin-bottom:14px; color:#f1f5f9; }}
.chart-wrap {{ height:280px; position:relative; margin-bottom:16px; }}
table {{ width:100%; border-collapse:collapse; font-size:.88em; border-radius:10px; overflow:hidden; background:rgba(30,41,59,.45); border:1px solid rgba(148,163,184,.12); }}
th {{ background:rgba(99,102,241,.2); color:#e0e7ff; padding:10px 12px; text-align:left; border-bottom:1px solid rgba(148,163,184,.15); }}
td {{ padding:8px 12px; border-bottom:1px solid rgba(148,163,184,.1); color:#e2e8f0; }}
tr:hover td {{ background:rgba(99,102,241,.08); }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.kpi-row {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:8px; }}
.kpi-chip {{ background:rgba(99,102,241,.12); border:1px solid rgba(148,163,184,.15); border-radius:12px; padding:12px 16px; min-width:120px; text-align:center; }}
.kpi-chip .kpi-value {{ font-size:1.4em; font-weight:700; color:#c7d2fe; }}
.kpi-chip .kpi-label {{ font-size:.78em; color:#94a3b8; margin-top:2px; }}
.footer {{ text-align:center; color:#64748b; font-size:.78em; margin-top:20px; }}
</style>
</head>
<body>
<div class="container">
<h1>Sotuv hisoboti</h1>
<p class="sub">{date_from or 'Boshidan'} — {date_to or 'Hozirgacha'} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="cards">
  <div class="card"><div class="lbl">Jami buyurtmalar</div><div class="val">{total_orders:,}</div></div>
  <div class="card"><div class="lbl">Jami sotuv</div><div class="val">{fmt(total_amount)}</div></div>
  <div class="card"><div class="lbl">Soliq</div><div class="val">{fmt(total_tax)}</div></div>
  <div class="card"><div class="lbl">Oylar</div><div class="val">{len(monthly)}</div></div>
</div>

<div class="section">
  <h2>Oylik sotuv dinamikasi</h2>
  {c1_visual}
  {monthly_table}
</div>

<div class="section">
  <h2>Top mijozlar</h2>
  {c2_visual}
  {partner_table}
</div>

<div class="section">
  <h2>Top mahsulotlar</h2>
  {c3_visual}
  {product_table}
</div>

<div class="section">
  <h2>Sotuvchilar reytingi</h2>
  {c4_visual}
  {seller_table}
</div>

<p class="footer">dunyabunya agent — {datetime.now().strftime('%Y-%m-%d')}</p>
</div>

<script>
Chart.defaults.color = '#cbd5e1';
Chart.defaults.borderColor = 'rgba(148,163,184,0.08)';
const C=s=>document.getElementById(s).getContext('2d');
{charts_js}
</script>
</body>
</html>"""
    
    out_path = REPORTS_DIR / filename
    out_path.write_text(html, encoding="utf-8")
    
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/reports/{filename}"


async def generate_invoice_report(date_from: str = "", date_to: str = "") -> str:
    """Schyot-faktura hisoboti — HTML report."""
    from app.services.claude_report import generate_report_link
    
    domain = [["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"]]
    if date_from:
        domain.append(["invoice_date", ">=", date_from])
    if date_to:
        domain.append(["invoice_date", "<=", date_to])
    
    stats = await _odoo.execute_kw(
        "account.move", "read_group",
        [domain, ["amount_total", "amount_residual"], ["invoice_date:month"]],
    )
    
    rows = []
    for s in stats:
        month = str(s.get("invoice_date:month", "Noma'lum"))
        total = round(s.get("amount_total", 0), 0)
        residual = round(s.get("amount_residual", 0), 0)
        paid = total - residual
        rows.append({
            "Oy": month,
            "Jami summa": total,
            "To'langan": paid,
            "Qarz": residual,
        })
    
    if not rows:
        return "Bu davr uchun faktura ma'lumotlari topilmadi."
    
    link = generate_report_link(
        title=f"Faktura hisoboti ({date_from or 'Boshidan'} — {date_to or 'Hozirgacha'})",
        description="Schyot-fakturalar, to'lovlar va qarzdorlik",
        rows=rows,
    )
    return link or "Hisobot yaratilmadi."


async def generate_task_report() -> str:
    """Vazifalar hisoboti — HTML report."""
    from app.services.claude_report import generate_report_link
    
    stats = await _odoo.execute_kw(
        "project.task", "read_group",
        [[], ["id:count"], ["state"]],
    )
    
    rows = []
    for s in stats:
        state = str(s.get("state", "Noma'lum"))
        count = s.get("id:count", 0)
        rows.append({
            "Holat": state,
            "Soni": count,
        })
    
    if not rows:
        return "Vazifalar topilmadi."
    
    link = generate_report_link(
        title="Vazifalar hisoboti",
        description="Vazifalar holatlari bo'yicha",
        rows=rows,
    )
    return link or "Hisobot yaratilmadi."


async def generate_company_report(date_from: str = "", date_to: str = "") -> str:
    """Kompaniyaning to'liq statistikasi — barcha bo'limlar.

    Standart holatda JORIY YIL (1-yanvardan bugungacha) bo'yicha hisoblaydi — "shu yilgi
    to'liq hisobot" so'ralganda parametrsiz chaqiring. Boshqa davr kerak bo'lsa
    date_from/date_to (YYYY-MM-DD) bering; butun tarix uchun ikkalasiga ham "all" bering.
    """
    import asyncio
    import uuid
    from datetime import date, datetime

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # "shu yilgi" — standart bo'yicha joriy yil boshidan bugungacha (butun tarix uchun
    # date_from="all" beriladi). Bitta yillik oraliqqa cheklash Odoo'dan bir yo'la
    # o'nlab yillik xom qatorlarni tortib olishning oldini oladi — read_group baribir
    # server tomonida agregat qiladi, lekin domain qanchalik tor bo'lsa shunchalik tez.
    if date_from == "" and date_to == "":
        date_from = date.today().replace(month=1, day=1).isoformat()
        date_to = date.today().isoformat()
    elif date_from == "all" or date_to == "all":
        date_from = date_to = ""
    period_label = f"{date_from or 'Boshidan'} — {date_to or 'Hozirgacha'}"

    def fmt(n):
        n = float(n or 0)
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f} mlrd"
        if n >= 1_000_000: return f"{n/1_000_000:.1f} mln"
        if n >= 1_000: return f"{n/1_000:.1f} ming"
        return str(int(n))

    # Grafik chizish qoidasi: bo'sh, yagona yoki 0 qiymatli ma'lumotga grafik chizilmaydi
    def chart_ok(values):
        return sum(1 for v in values if v) >= 2

    def kpi_chips(labels, values):
        chips = ""
        for lb, vl in zip(labels, values):
            if vl:
                chips += (f'<div class="kpi-chip"><div class="kpi-value">{fmt(vl)}</div>'
                          f'<div class="kpi-label">{lb}</div></div>')
        return f'<div class="kpi-row">{chips}</div>' if chips else ""
    
    sales_domain = [["state", "in", ["sale", "done"]]]
    invoice_domain = [["move_type", "in", ["out_invoice"]], ["state", "=", "posted"]]
    if date_from:
        sales_domain.append(["date_order", ">=", date_from])
        invoice_domain.append(["invoice_date", ">=", date_from])
    if date_to:
        sales_domain.append(["date_order", "<=", date_to])
        invoice_domain.append(["invoice_date", "<=", date_to])

    # Barcha bo'lim statistikalarini PARALLEL o'qiymiz (mustaqil so'rovlar) — xom
    # qatorlar emas, faqat Odoo'ning o'zida oldindan hisoblangan (read_group) jamlangan
    # sonlar so'raladi, shuning uchun bir yillik davr ham og'ir/sekin bo'lmaydi.
    sales, invoices, tasks, crm, products, product_count_result, emp_count = await asyncio.gather(
        _odoo.execute_kw(
            "sale.order", "read_group",
            [sales_domain, ["amount_total", "id:count"], ["date_order:month"]],
        ),
        _odoo.execute_kw("account.move", "read_group",
            [invoice_domain, ["amount_total", "amount_residual"], ["invoice_date:month"]]),
        _odoo.execute_kw("project.task", "read_group", [[], ["id:count"], ["state"]]),
        _odoo.execute_kw("crm.lead", "read_group", [[], ["id:count"], ["stage_id"]]),
        _odoo.execute_kw("product.product", "search_read",
            [[["type", "=", "product"]], ["id", "name", "qty_available", "list_price"]],
            {"limit": 1}),
        _odoo.execute_kw("product.product", "search_count", [[["type", "=", "product"]]]),
        _odoo.execute_kw("hr.employee", "search_count", [[["active", "=", True]]]),
    )

    total_sales = sum(s.get("amount_total", 0) for s in sales)
    total_orders = sum(s.get("id:count", 0) for s in sales)
    total_invoiced = sum(i.get("amount_total", 0) for i in invoices)
    total_residual = sum(i.get("amount_residual", 0) for i in invoices)
    total_crm = sum(c.get("id:count", 0) for c in crm)
    
    # Oylik sotuv data
    sales_labels = [str(s.get("date_order:month", "")) for s in sales[-6:]]
    sales_values = [round(s.get("amount_total", 0), 0) for s in sales[-6:]]
    
    # Vazifalar data
    task_labels = [str(t.get("state", "")) for t in tasks]
    task_values = [t.get("id:count", 0) for t in tasks]
    
    # CRM data
    crm_labels = [str(c.get("stage_id", [""])[1])[:20] if isinstance(c.get("stage_id"), list) else str(c.get("stage_id", "")) for c in crm[:8]]
    crm_values = [c.get("id:count", 0) for c in crm[:8]]
    
    # Grafik shartlari: kamida 2 ta nol bo'lmagan qiymat bo'lsa chart, aks holda KPI kartochkalari
    c1_ok = chart_ok(sales_values)
    c2_ok = chart_ok(task_values)
    c3_ok = chart_ok(crm_values)
    c4_ok = chart_ok([total_invoiced, total_residual])

    c1_visual = '<div class="chart-wrap"><canvas id="c1"></canvas></div>' if c1_ok else kpi_chips(sales_labels, sales_values)
    c2_visual = '<div class="chart-wrap"><canvas id="c2"></canvas></div>' if c2_ok else kpi_chips(task_labels, task_values)
    c3_visual = '<div class="chart-wrap"><canvas id="c3"></canvas></div>' if c3_ok else kpi_chips(crm_labels, crm_values)
    c4_visual = '<div class="chart-wrap"><canvas id="c4"></canvas></div>' if c4_ok else kpi_chips(['Jami', 'Qarz'], [total_invoiced, total_residual])

    charts_js = []
    if c1_ok:
        charts_js.append(
            f"new Chart(C('c1'),{{type:'bar',data:{{labels:{sales_labels},datasets:[{{label:'Sotuv',data:{sales_values},backgroundColor:'#6366f1',borderRadius:6}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true,grid:{{color:'rgba(148,163,184,0.08)'}},ticks:{{callback:v=>v>=1e9?(v/1e9).toFixed(0)+' mlrd':v>=1e6?(v/1e6).toFixed(0)+' mln':v}}}}}}}}}});"
        )
    if c2_ok:
        charts_js.append(
            f"new Chart(C('c2'),{{type:'doughnut',data:{{labels:{task_labels},datasets:[{{data:{task_values},backgroundColor:['#34d399','#fbbf24','#f43f5e','#22d3ee']}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'right',labels:{{color:'#cbd5e1'}}}}}}}}}});"
        )
    if c3_ok:
        charts_js.append(
            f"new Chart(C('c3'),{{type:'bar',data:{{labels:{crm_labels},datasets:[{{label:'Soni',data:{crm_values},backgroundColor:'#a855f7',borderRadius:6}}]}},options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{beginAtZero:true,grid:{{color:'rgba(148,163,184,0.08)'}},ticks:{{callback:v=>v>=1e9?(v/1e9).toFixed(0)+' mlrd':v>=1e6?(v/1e6).toFixed(0)+' mln':v}}}}}}}}}});"
        )
    if c4_ok:
        charts_js.append(
            f"new Chart(C('c4'),{{type:'bar',data:{{labels:['Jami','Qarz'],datasets:[{{data:[{round(total_invoiced, 0)},{round(total_residual, 0)}],backgroundColor:['#34d399','#f43f5e'],borderRadius:6}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true,grid:{{color:'rgba(148,163,184,0.08)'}},ticks:{{callback:v=>v>=1e9?(v/1e9).toFixed(0)+' mlrd':v>=1e6?(v/1e6).toFixed(0)+' mln':v}}}}}}}}}});"
        )
    charts_js = "\n".join(charts_js)

    filename = f"company_{uuid.uuid4().hex[:12]}.html"
    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kompaniya statistikasi</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#0f172a 100%); color:#e2e8f0; padding:20px; min-height:100vh; }}
.container {{ max-width:1000px; margin:0 auto; }}
h1 {{ font-size:1.5em; margin-bottom:4px; font-weight:800; color:#fff; }}
.sub {{ color:#94a3b8; font-size:.9em; margin-bottom:20px; }}
.cards {{ display:flex; gap:14px; margin-bottom:20px; flex-wrap:wrap; }}
.card {{ background:rgba(30,41,59,.75); border:1px solid rgba(148,163,184,.12); border-radius:16px; padding:18px 20px; flex:1; min-width:140px; box-shadow:0 8px 24px rgba(0,0,0,.25); text-align:center; }}
.card .emoji {{ font-size:1.8em; margin-bottom:4px; }}
.card .lbl {{ font-size:.8em; color:#94a3b8; margin-bottom:2px; }}
.card .val {{ font-size:1.5em; font-weight:700; color:#c7d2fe; }}
.section {{ background:rgba(30,41,59,.75); border:1px solid rgba(148,163,184,.12); border-radius:16px; padding:20px; margin-bottom:16px; box-shadow:0 8px 24px rgba(0,0,0,.25); }}
.section h2 {{ font-size:1.1em; margin-bottom:14px; color:#f1f5f9; }}
.row {{ display:flex; gap:16px; flex-wrap:wrap; }}
.row > div {{ flex:1; min-width:300px; }}
.chart-wrap {{ height:250px; position:relative; }}
table {{ width:100%; border-collapse:collapse; font-size:.85em; margin-top:12px; border-radius:10px; overflow:hidden; background:rgba(30,41,59,.45); border:1px solid rgba(148,163,184,.12); }}
th {{ background:rgba(99,102,241,.2); color:#e0e7ff; padding:8px 10px; text-align:left; border-bottom:1px solid rgba(148,163,184,.15); }}
td {{ padding:7px 10px; border-bottom:1px solid rgba(148,163,184,.1); color:#e2e8f0; }}
.kpi-row {{ display:flex; gap:10px; flex-wrap:wrap; }}
.kpi-chip {{ background:rgba(99,102,241,.12); border:1px solid rgba(148,163,184,.15); border-radius:12px; padding:12px 16px; min-width:110px; text-align:center; }}
.kpi-chip .kpi-value {{ font-size:1.4em; font-weight:700; color:#c7d2fe; }}
.kpi-chip .kpi-label {{ font-size:.78em; color:#94a3b8; margin-top:2px; }}
.footer {{ text-align:center; color:#64748b; font-size:.78em; margin-top:20px; }}
</style>
</head>
<body>
<div class="container">
<h1>Kompaniya to'liq statistikasi</h1>
<p class="sub">{period_label} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="cards">
  <div class="card"><div class="emoji">💰</div><div class="lbl">Jami sotuv</div><div class="val">{fmt(total_sales)}</div></div>
  <div class="card"><div class="emoji">📦</div><div class="lbl">Buyurtmalar</div><div class="val">{total_orders:,}</div></div>
  <div class="card"><div class="emoji">🧾</div><div class="lbl">Fakturalar</div><div class="val">{fmt(total_invoiced)}</div></div>
  <div class="card"><div class="emoji">📋</div><div class="lbl">Qarz</div><div class="val">{fmt(total_residual)}</div></div>
  <div class="card"><div class="emoji">🎯</div><div class="lbl">CRM lidlar</div><div class="val">{total_crm:,}</div></div>
  <div class="card"><div class="emoji">👥</div><div class="lbl">Xodimlar</div><div class="val">{emp_count:,}</div></div>
</div>

<div class="row">
  <div class="section">
    <h2>Oylik sotuv</h2>
    {c1_visual}
  </div>
  <div class="section">
    <h2>Vazifalar holati</h2>
    {c2_visual}
  </div>
</div>

<div class="row">
  <div class="section">
    <h2>CRM bosqichlari</h2>
    {c3_visual}
  </div>
  <div class="section">
    <h2>Fakturalar</h2>
    {c4_visual}
  </div>
</div>

<p class="footer">dunyabunya agent — {datetime.now().strftime('%Y-%m-%d')}</p>
</div>
<script>
Chart.defaults.color = '#cbd5e1';
Chart.defaults.borderColor = 'rgba(148,163,184,0.08)';
const C=s=>document.getElementById(s).getContext('2d');
{charts_js}
</script>
</body></html>"""
    
    out_path = REPORTS_DIR / filename
    out_path.write_text(html, encoding="utf-8")
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/reports/{filename}"


STATS_TOOLS = {
    "generate_company_report": generate_company_report,
    "generate_sales_report": generate_sales_report,
    "generate_invoice_report": generate_invoice_report,
    "generate_task_report": generate_task_report,

    "get_sales_stats": get_sales_stats,
    "get_sales_by_partner": get_sales_by_partner,
    "get_sales_by_product": get_sales_by_product,
    "get_invoice_stats": get_invoice_stats,
    "get_crm_stats": get_crm_stats,
    "get_task_stats": get_task_stats,
}

STATS_TOOL_SCHEMAS = {
    "generate_company_report": {
        "name": "generate_company_report",
        "description": "Kompaniyaning to'liq statistikasi — barcha bo'limlar, vazifalar, CRM, fakturalar. "
                        "Parametrsiz chaqirilsa JORIY YIL (1-yanvardan bugungacha) bo'yicha hisoblaydi — "
                        "'shu yilgi to'liq hisobot' uchun shunchaki bo'sh chaqiring. Butun tarix uchun "
                        "date_from='all' bering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD, yoki 'all' (butun tarix). Bo'sh = joriy yil boshi"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD. Bo'sh = bugun"},
            },
        },
    },
    "generate_sales_report": {
        "name": "generate_sales_report",
        "description": "Sotuv hisoboti — grafik va jadvallar bilan HTML report yaratadi. Faqat havolani qaytaradi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Boshlanish sanasi (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "Tugash sanasi (YYYY-MM-DD)"},
            },
        },
    },
    "generate_invoice_report": {
        "name": "generate_invoice_report",
        "description": "Faktura hisoboti — to'lovlar va qarzdorlik bilan HTML report",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
        },
    },
    "generate_task_report": {
        "name": "generate_task_report",
        "description": "Vazifalar hisoboti — holatlar bo'yicha HTML report",
        "input_schema": {"type": "object", "properties": {}},
    },

    "get_sales_stats": {
        "name": "get_sales_stats",
        "description": "Sotuv statistikasi — umumiy sotuv, oylik/haftalik guruhlash",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Boshlanish sanasi (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "Tugash sanasi (YYYY-MM-DD)"},
                "group_by": {"type": "string", "default": "date_order:month", "description": "Guruhlash: date_order:month, date_order:week, partner_id"},
            },
        },
    },
    "get_sales_by_partner": {
        "name": "get_sales_by_partner",
        "description": "Sotuv statistikasi — mijozlar bo'yicha top-",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    "get_sales_by_product": {
        "name": "get_sales_by_product",
        "description": "Sotuv statistikasi — mahsulotlar bo'yicha",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    "get_invoice_stats": {
        "name": "get_invoice_stats",
        "description": "Schyot-faktura statistikasi — oylik tushum va qarz",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
        },
    },
    "get_crm_stats": {
        "name": "get_crm_stats",
        "description": "CRM lidlar statistikasi — bosqichlar bo'yicha",
        "input_schema": {"type": "object", "properties": {}},
    },
    "get_task_stats": {
        "name": "get_task_stats",
        "description": "Vazifalar statistikasi — holatlar bo'yicha",
        "input_schema": {"type": "object", "properties": {}},
    },
}

# ─── Tool registry ─────────────────────────────────────────────────────────

ALL_TOOLS: dict[str, callable] = {
    # Moliya
    "search_kassa_chiqim": search_kassa_chiqim,
    "create_kassa_chiqim": create_kassa_chiqim,
    "search_payments": search_payments,
    "create_payment_draft": create_payment_draft,
    "create_currency_exchange": create_currency_exchange,
    "get_invoice_pdf": get_invoice_pdf,
    "get_payment_receipt_pdf": get_payment_receipt_pdf,
    # Ombor
    "find_product": find_product,
    "check_stock_quant": check_stock_quant,
    "check_low_stock": check_low_stock,
    "create_internal_transfer": create_internal_transfer,
    "get_delivery_slip_pdf": get_delivery_slip_pdf,
    # Sotuv
    "find_partner": find_partner,
    "search_crm_leads": search_crm_leads,
    "create_crm_lead": create_crm_lead,
    "find_stale_leads": find_stale_leads,
    "create_sale_order_draft": create_sale_order_draft,
    "check_partner_debt": check_partner_debt,
    "get_sale_order_pdf": get_sale_order_pdf,
    # Stats
    **STATS_TOOLS,
    # Format exports
    "export_word": export_word,
    "export_pdf": export_pdf,
    "export_csv": export_csv,
    "export_text": export_text,
    # CEO
    "export_excel": export_excel,
    "generate_html_report": generate_html_report,
    "aggregate_data": aggregate_data,
    "growth_dynamics": growth_dynamics,
    "search_employees_readonly": search_employees_readonly,
    # CEO extra analytics
    "get_top_debtors": get_top_debtors,
    "get_overdue_invoices": get_overdue_invoices,
    "get_cashflow_summary": get_cashflow_summary,
    "get_sales_by_seller": get_sales_by_seller,
    "get_employees_by_department": get_employees_by_department,
    "get_upcoming_deadlines": get_upcoming_deadlines,
    "get_crm_funnel": get_crm_funnel,
    "get_profit_summary": get_profit_summary,
}


# ─── Tool schemas for Anthropic ────────────────────────────────────────────

TOOL_SCHEMAS: dict[str, dict] = {
    "search_kassa_chiqim": {
        "name": "search_kassa_chiqim",
        "description": "Kassa chiqimlarini qidirish (account.move, out_invoice)",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "array", "description": "Odoo domain (ixtiyoriy)"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    "create_kassa_chiqim": {
        "name": "create_kassa_chiqim",
        "description": "Kassa chiqimi uchun qoralama yaratish",
        "input_schema": {
            "type": "object",
            "properties": {
                "partner_id": {"type": "integer", "description": "Partner ID"},
                "amount": {"type": "number", "description": "Summa"},
                "description": {"type": "string", "description": "Tavsif"},
            },
            "required": ["partner_id", "amount"],
        },
    },
    "search_payments": {
        "name": "search_payments",
        "description": "To'lovlarni qidirish (account.payment)",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "array", "description": "Odoo domain (ixtiyoriy)"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    "create_payment_draft": {
        "name": "create_payment_draft",
        "description": "To'lov qoralamasi yaratish",
        "input_schema": {
            "type": "object",
            "properties": {
                "partner_id": {"type": "integer"},
                "amount": {"type": "number"},
                "payment_type": {"type": "string", "enum": ["inbound", "outbound"], "default": "outbound"},
            },
            "required": ["partner_id", "amount"],
        },
    },
    "create_currency_exchange": {
        "name": "create_currency_exchange",
        "description": "Valyuta ayirboshlash amaliyotini kiritish",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_currency_id": {"type": "integer"},
                "to_currency_id": {"type": "integer"},
                "amount": {"type": "number"},
                "rate": {"type": "number"},
            },
            "required": ["from_currency_id", "to_currency_id", "amount", "rate"],
        },
    },
    "get_invoice_pdf": {
        "name": "get_invoice_pdf",
        "description": "Schyot-faktura PDF ini olish",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "integer"}},
            "required": ["invoice_id"],
        },
    },
    "get_payment_receipt_pdf": {
        "name": "get_payment_receipt_pdf",
        "description": "To'lov kvitansiyasi PDF ini olish",
        "input_schema": {
            "type": "object",
            "properties": {"payment_id": {"type": "integer"}},
            "required": ["payment_id"],
        },
    },
    "find_product": {
        "name": "find_product",
        "description": "Mahsulotlarni qidirish",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Qidiruv so'zi"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    "check_stock_quant": {
        "name": "check_stock_quant",
        "description": "Mahsulotning ombordagi qoldig'ini tekshirish",
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "integer"}},
            "required": ["product_id"],
        },
    },
    "check_low_stock": {
        "name": "check_low_stock",
        "description": "Kam qoldiqdagi mahsulotlarni aniqlash",
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "default": 10.0, "description": "Chegara miqdori"},
            },
        },
    },
    "create_internal_transfer": {
        "name": "create_internal_transfer",
        "description": "Ichki ko'chirish (transfer) hujjati yaratish",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "quantity": {"type": "number"},
                "from_location_id": {"type": "integer"},
                "to_location_id": {"type": "integer"},
            },
            "required": ["product_id", "quantity", "from_location_id", "to_location_id"],
        },
    },
    "get_delivery_slip_pdf": {
        "name": "get_delivery_slip_pdf",
        "description": "Nakladnoy/transfer akti PDF ini olish",
        "input_schema": {
            "type": "object",
            "properties": {"picking_id": {"type": "integer"}},
            "required": ["picking_id"],
        },
    },
    "find_partner": {
        "name": "find_partner",
        "description": "Mijozlarni qidirish",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    "search_crm_leads": {
        "name": "search_crm_leads",
        "description": "CRM lidlarini qidirish",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "array"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    "create_crm_lead": {
        "name": "create_crm_lead",
        "description": "Yangi CRM lead yaratish",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "partner_name": {"type": "string"},
                "planned_revenue": {"type": "number"},
            },
            "required": ["name"],
        },
    },
    "find_stale_leads": {
        "name": "find_stale_leads",
        "description": "Harakatsiz (eski) CRM lidlarni aniqlash",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30, "description": "Kunlar soni"},
            },
        },
    },
    "create_sale_order_draft": {
        "name": "create_sale_order_draft",
        "description": "Sotuv buyurtmasi qoralamasi yaratish",
        "input_schema": {
            "type": "object",
            "properties": {
                "partner_id": {"type": "integer"},
                "order_lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "integer"},
                            "price_unit": {"type": "number"},
                            "product_uom_qty": {"type": "number"},
                        },
                    },
                },
            },
            "required": ["partner_id", "order_lines"],
        },
    },
    "check_partner_debt": {
        "name": "check_partner_debt",
        "description": "Mijozning qarzdorligini tekshirish",
        "input_schema": {
            "type": "object",
            "properties": {"partner_id": {"type": "integer"}},
            "required": ["partner_id"],
        },
    },
    "get_sale_order_pdf": {
        "name": "get_sale_order_pdf",
        "description": "Sotuv buyurtmasi PDF ini olish",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}},
            "required": ["order_id"],
        },
    },
    **STATS_TOOL_SCHEMAS,
    "export_excel": {
        "name": "export_excel",
        "description": "Odoo ma'lumotlarini haqiqiy Excel (.xlsx) faylga eksport qiladi. Foydalanuvchi 'excel', 'xlsx', 'fayl qilib ber', 'eksport' so'rasa shu toolni ishlating. Kerakli model (masalan hr.employee), domain va fields ni avvalgi qidiruvga mos qilib bering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model, masalan hr.employee, sale.order, account.move"},
                "domain": {"type": "array", "description": "Odoo domain filtrlari (ixtiyoriy)"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Eksport qilinadigan maydonlar, masalan [\"name\", \"job_id\", \"department_id\", \"work_email\"]"},
                "title": {"type": "string", "default": "Hisobot", "description": "Fayl va varaq nomi"},
                "limit": {"type": "integer", "default": 10000},
            },
        },
    },
    "generate_html_report": {
        "name": "generate_html_report",
        "description": "Odoo model ma'lumotlaridan chiroyli HTML hisobot (jadval + grafiklar) yaratadi va fayl havolasini qaytaradi. Foydalanuvchi 'html', 'grafik', 'jadval', 'dashboard' ko'rinishida so'raganda shu toolni ishlating.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model, masalan hr.employee, sale.order"},
                "domain": {"type": "array", "description": "Odoo domain filtrlari (ixtiyoriy)"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Hisobotga kiritiladigan maydonlar"},
                "title": {"type": "string", "default": "Hisobot", "description": "Hisobot sarlavhasi"},
            },
        },
    },
    "aggregate_data": {
        "name": "aggregate_data",
        "description": "Ma'lumotlarni guruhlab hisoblash",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "group_by": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "domain": {"type": "array"},
            },
            "required": ["model", "group_by", "fields"],
        },
    },
    "growth_dynamics": {
        "name": "growth_dynamics",
        "description": "O'sish dinamikasini hisoblash (oylik)",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "date_field": {"type": "string"},
                "value_field": {"type": "string"},
                "domain": {"type": "array"},
            },
            "required": ["model", "date_field", "value_field"],
        },
    },
    "search_employees_readonly": {
        "name": "search_employees_readonly",
        "description": "Xodimlarni o'qish rejimida qidirish",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "default": ""}},
        },
    },
    "export_word": {
        "name": "export_word",
        "description": "Odoo model ma'lumotlaridan Word (.docx) hujjat yaratadi. Foydalanuvchi 'word', 'docx', 'hujjat qilib ber' so'rasa ishlatiladi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model (masalan hr.employee, sale.order)"},
                "domain": {"type": "array"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string", "default": "Hisobot"},
                "limit": {"type": "integer", "default": 10000},
            },
            "required": ["model"],
        },
    },
    "export_pdf": {
        "name": "export_pdf",
        "description": "Odoo model ma'lumotlaridan PDF hisobot yaratadi. Foydalanuvchi 'pdf', 'pdf qilib ber' so'rasa ishlatiladi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "domain": {"type": "array"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string", "default": "Hisobot"},
                "limit": {"type": "integer", "default": 10000},
            },
            "required": ["model"],
        },
    },
    "export_csv": {
        "name": "export_csv",
        "description": "Odoo model ma'lumotlaridan CSV fayl yaratadi. Foydalanuvchi 'csv' so'rasa ishlatiladi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "domain": {"type": "array"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string", "default": "Hisobot"},
                "limit": {"type": "integer", "default": 10000},
            },
            "required": ["model"],
        },
    },
    "export_text": {
        "name": "export_text",
        "description": "Odoo yozuvlari ro'yxatini FAYLSIZ, shu Telegram chatining o'ziga MATN ko'rinishida chiqaradi. Foydalanuvchi 'matn', 'text', 'matn ko'rinishida', 'shu yerga yoz', 'chatga tashla' desa shu toolni ishlating. To'liq ro'yxatni bot o'zi yuboradi — javobingizda ro'yxatni qayta yozmang. 300 tadan ko'p yozuv bo'lsa dastlabki 300 tasi chiqadi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Odoo model, masalan hr.employee, sale.order"},
                "domain": {"type": "array", "description": "Odoo domain filtrlari (ixtiyoriy)"},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Ko'rsatiladigan maydonlar; BIRINCHISI asosiy ustun (masalan [\"name\", \"job_id\", \"work_phone\"]). 2-5 ta maydon yetarli."},
                "title": {"type": "string", "default": "Ro'yxat", "description": "Ro'yxat sarlavhasi"},
                "group_by": {"type": "string", "description": "Shu maydon bo'yicha guruhlab chiqarish (ixtiyoriy), masalan department_id"},
                "limit": {"type": "integer", "default": 300},
            },
            "required": ["model", "fields"],
        },
    },
    "get_top_debtors": {
        "name": "get_top_debtors",
        "description": "Eng katta qarzdor mijozlar ro'yxati (to'lanmagan fakturalar bo'yicha)",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
    },
    "get_overdue_invoices": {
        "name": "get_overdue_invoices",
        "description": "Muddati o'tgan to'lanmagan fakturalar",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 30, "description": "Nech kundan beri muddati o'tgan"}},
        },
    },
    "get_cashflow_summary": {
        "name": "get_cashflow_summary",
        "description": "Kassa/pul oqimi xulosasi — oylar bo'yicha kirim va chiqim",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
            },
        },
    },
    "get_sales_by_seller": {
        "name": "get_sales_by_seller",
        "description": "Sotuvchilar bo'yicha sotuv reytingi",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    "get_employees_by_department": {
        "name": "get_employees_by_department",
        "description": "Xodimlar soni bo'limlar bo'yicha",
        "input_schema": {"type": "object", "properties": {}},
    },
    "get_upcoming_deadlines": {
        "name": "get_upcoming_deadlines",
        "description": "Yaqin muddatdagi (N kun ichida) ochiq vazifalar",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 7}},
        },
    },
    "get_crm_funnel": {
        "name": "get_crm_funnel",
        "description": "CRM funnel — lidlar bosqichlar bo'yicha kutilayotgan daromad bilan",
        "input_schema": {"type": "object", "properties": {}},
    },
    "get_profit_summary": {
        "name": "get_profit_summary",
        "description": "Foyda xulosasi: jami faktura, to'langan summa, qolgan qarz",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
        },
    },
}
