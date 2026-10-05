"""Odoo tools for RBAC roles: Moliya, Ombor, Sotuv, CEO analytics."""

from __future__ import annotations

import asyncio
import html as _html
import re
import shutil
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import report_html as rh
from app.services.odoo import OdooClient, OdooError

_odoo = OdooClient()
REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"

_TZ = ZoneInfo("Asia/Tashkent")
# Hisobot/eksportlarda maydon nomlari, tanlov (selection) qiymatlari o'zbekcha chiqishi va
# sanalar Toshkent vaqti bo'yicha guruhlanishi uchun. O'rnatilmagan tilni Odoo e'tiborsiz qoldiradi.
_UZ_CTX = {"lang": "uz_UZ", "tz": "Asia/Tashkent"}
_UZ_MONTHS = ("Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
              "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr")

# Odoo 18: account.payment holati 'draft/in_process/paid/canceled/rejected' ('posted' yo'q —
# u bilan filtrlangan har qanday so'rov bo'sh qaytardi). 'posted' eski versiyalar uchun qoldirilgan.
_PAYMENT_DONE_STATES = ["in_process", "paid", "posted"]
_CURRENCY_NAMES = {"UZS": "so'm"}


def _unguessable_suffix() -> str:
    """8 hex-belgili tasodifiy token — hisobot fayl nomlariga qo'shiladi (P0-2:
    Odoo record ID'sini to'g'ridan-to'g'ri fayl nomiga yozish uni taxmin qilib
    topib bo'ladigan qiladi, masalan invoice_123.pdf, invoice_124.pdf, ...)."""
    return uuid.uuid4().hex[:8]


def _report_url(filename: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/reports/{filename}"


# ─── Umumiy yordamchilar ───────────────────────────────────────────────────

def _today() -> date:
    return datetime.now(_TZ).date()


def _m2o_name(value, default: str = "Noma'lum") -> str:
    """many2one qiymatidan ([id, nom]) nomni oladi."""
    if isinstance(value, (list, tuple)) and len(value) > 1 and value[1]:
        return str(value[1])
    return default


def _currency_label(value) -> str:
    name = _m2o_name(value, "")
    return _CURRENCY_NAMES.get(name, name)


def _group_count(row: dict, groupby: str) -> int:
    """read_group guruhidagi yozuvlar soni.

    Odoo bu sonni so'ralgan "id:count" kalitida EMAS, `<maydon>_count` (yoki lazy=False
    bo'lsa `__count`) kalitida qaytaradi. Ilgari kod `row.get("id:count")` deb o'qigani
    uchun hisobotlarda "Buyurtmalar: 0", "CRM lidlar: 0" chiqardi."""
    field = groupby.split(":")[0]
    for key in ("__count", f"{field}_count", "id"):
        value = row.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _month_key(row: dict, groupby: str) -> tuple[int, int] | None:
    """"date_order:month" guruhining (yil, oy) kaliti — `__range`dan olinadi."""
    start = ((row.get("__range") or {}).get(groupby) or {}).get("from")
    if not start:
        return None
    try:
        # Guruh Toshkent vaqti bo'yicha, chegara esa UTC'da kelishi mumkin ("...-28 19:00:00")
        moment = datetime.fromisoformat(str(start).replace(" ", "T")) + timedelta(hours=12)
    except ValueError:
        return None
    return moment.year, moment.month


def _month_label(row: dict, groupby: str) -> str:
    key = _month_key(row, groupby)
    if key is None:
        return str(row.get(groupby) or "Sanasi yo'q")
    return f"{_UZ_MONTHS[key[1] - 1]} {key[0]}"


def _dt_bounds(field: str, date_from: str = "", date_to: str = "") -> list:
    """Sana-VAQT (datetime) maydoni uchun oraliq — Toshkent kuni bo'yicha, UTC'ga o'girilgan.

    Ilgari `date_order <= '2026-10-05'` yozilardi: bu 5-oktabr 00:00 (UTC) gacha degani,
    ya'ni oxirgi kunning deyarli barcha buyurtmalari hisobotdan tushib qolardi."""
    bounds = []
    midnight = datetime.min.time()
    if date_from:
        start = datetime.combine(date.fromisoformat(date_from[:10]), midnight, _TZ)
        bounds.append([field, ">=", start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")])
    if date_to:
        end = datetime.combine(date.fromisoformat(date_to[:10]) + timedelta(days=1), midnight, _TZ)
        bounds.append([field, "<", end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")])
    return bounds


def _date_bounds(field: str, date_from: str = "", date_to: str = "") -> list:
    """Faqat sana (date) maydoni uchun oraliq (ikkala chegara ham kiradi)."""
    bounds = []
    if date_from:
        bounds.append([field, ">=", date.fromisoformat(date_from[:10]).isoformat()])
    if date_to:
        bounds.append([field, "<=", date.fromisoformat(date_to[:10]).isoformat()])
    return bounds


def _period_label(date_from: str, date_to: str) -> str:
    return f"{date_from or 'Boshidan'} — {date_to or 'Hozirgacha'}"


async def _rg(model: str, domain: list, fields: list, groupby, **kwargs) -> list[dict]:
    """read_group (o'zbekcha kontekst bilan; bir nechta guruh bo'lsa lazy=False)."""
    groupby = [groupby] if isinstance(groupby, str) else list(groupby)
    kw = {"context": _UZ_CTX, **kwargs}
    if len(groupby) > 1:
        kw.setdefault("lazy", False)
    return await _odoo.execute_kw(model, "read_group", [domain, fields, groupby], kw)


def _clean_groups(rows: list[dict], groupby: str) -> list[dict]:
    """read_group natijasini model o'qishi oson ko'rinishga keltiradi: guruh nomi, soni va
    jamlangan maydonlar (`__domain`, `__range` kabi texnik kalitlarsiz)."""
    out = []
    for r in rows:
        value = r.get(groupby)
        item: dict = {}
        if groupby.endswith(":month"):
            item["davr"] = _month_label(r, groupby)
        elif isinstance(value, (list, tuple)):
            item["id"], item["nomi"] = value[0], _m2o_name(value)
        else:
            item["nomi"] = "Ko'rsatilmagan" if value in (False, None) else value
        item["soni"] = _group_count(r, groupby)
        for key, val in r.items():
            if key.startswith("__") or key in (groupby, "id") or key.endswith("_count"):
                continue
            item[key] = val
        out.append(item)
    return out


# ── Maydonlar sxemasi (nomi, turi, tanlov qiymatlari) ──

_FIELDS_META_TTL = 600
_fields_meta_cache: dict[str, tuple[float, dict]] = {}

_TECHNICAL_FIELD_RE = re.compile(
    r"^(message_|activity_|website_|rating_|access_|__)|^(create_uid|write_uid|write_date|"
    r"has_message|my_activity_date_deadline|display_name)$"
)


async def _fields_meta(model: str) -> dict:
    """Model maydonlari sxemasi — 10 daqiqa keshlanadi (har eksportda qayta so'ralmaydi)."""
    now = time.monotonic()
    cached = _fields_meta_cache.get(model)
    if cached and now - cached[0] < _FIELDS_META_TTL:
        return cached[1]
    try:
        meta = await _odoo.execute_kw(
            model, "fields_get", [],
            {"attributes": ["string", "type", "selection", "relation"], "context": _UZ_CTX},
        )
    except OdooError as e:
        if e.kind:  # Odoo'ga yetib bo'lmadi — chaqiruvchi aniq sababni ko'rsatadi
            raise
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    if meta:
        _fields_meta_cache[model] = (now, meta)
    return meta


def _field_label(field: str, meta: dict) -> str:
    return str((meta.get(field) or {}).get("string") or field)


async def _selection_labels(model: str, field: str) -> dict:
    """Tanlov (selection) maydonining qiymat -> ko'rinadigan nom lug'ati."""
    selection = ((await _fields_meta(model)).get(field) or {}).get("selection") or []
    return {key: label for key, label in selection}


def _local_datetime(value: str) -> str:
    """Odoo datetime'ni (UTC) Toshkent vaqtiga o'girib, daqiqagacha yozadi."""
    try:
        moment = datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        return value
    return moment.astimezone(_TZ).strftime("%Y-%m-%d %H:%M")


def _strip_tags(value: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", value, flags=re.IGNORECASE)
    return _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _clean_cell_value(val):
    """Odoo qiymatlarini o'qilishi oson formatga o'tkazish.
    (id, name) m2o/m2m tuple'larini nomga aylantiradi."""
    if isinstance(val, (list, tuple)) and len(val) == 2 and isinstance(val[0], (int, float)) \
            and not isinstance(val[1], (int, float)):
        # many2one: [id, name]
        return val[1] if val[1] else str(val[0])
    if isinstance(val, (list, tuple)):
        # many2many: [[id, name], ...]
        cleaned = [_clean_cell_value(v) for v in val]
        return ", ".join(str(v) for v in cleaned)
    return val


def _display_cell(val, fmeta: dict, names: dict | None = None):
    """Bitta katak qiymatini ko'rsatishga tayyorlaydi (maydon turiga qarab).

    Odoo bo'sh maydon uchun ham False qaytaradi — u "False"/0 bo'lib emas, bo'sh katak
    bo'lib chiqishi kerak; faqat haqiqiy mantiqiy maydon "Ha"/"Yo'q" bo'ladi."""
    ftype = fmeta.get("type")
    if ftype == "boolean":
        return "Ha" if val else "Yo'q"
    if val is None or val is False:
        return ""
    if ftype == "selection":
        return dict(fmeta.get("selection") or []).get(val, val)
    if ftype == "datetime" and isinstance(val, str):
        return _local_datetime(val)
    if ftype == "html" and isinstance(val, str):
        return _strip_tags(val)
    if ftype in ("many2many", "one2many") and isinstance(val, list):
        if names is not None:
            return ", ".join(str(names.get(i, i)) for i in val)
        return f"{len(val)} ta" if val else ""
    return _clean_cell_value(val)


async def _resolve_x2many_names(raw: list[dict], fields: list[str], meta: dict) -> dict[str, dict]:
    """Ko'p-qiymatli maydonlardagi (masalan vazifaning user_ids) ID'larni nomlarga o'giradi —
    aks holda ro'yxatda mas'ullar o'rnida "87, 112" kabi raqamlar chiqardi."""
    resolved: dict[str, dict] = {}
    for field in fields:
        fmeta = meta.get(field) or {}
        relation = fmeta.get("relation")
        if fmeta.get("type") not in ("many2many", "one2many") or not relation:
            continue
        ids = sorted({i for r in raw for i in (r.get(field) or []) if isinstance(i, int)})
        if not ids or len(ids) > 3000:
            continue
        try:
            records = await _odoo.execute_kw(relation, "read", [ids, ["display_name"]], {"context": _UZ_CTX})
        except OdooError as e:
            if e.kind:
                raise
            continue  # bu modelni o'qishga ruxsat yo'q — ID'lar soni ko'rsatiladi
        resolved[field] = {r["id"]: r.get("display_name") or str(r["id"]) for r in records}
    return resolved


_READ_PAGE_SIZE = 2000


async def _read_rows(model: str, domain: list | None = None, fields: list | None = None,
                     limit: int = 1000, *, keep_id: bool = False) -> tuple[list[dict], list[tuple[str, str]], dict]:
    """Yozuvlarni o'qib, ko'rsatishga tayyor holga keltiradi.

    Qaytadi: (qatorlar {maydon: qiymat}, ustunlar [(maydon, sarlavha)], maydonlar sxemasi).
    Sarlavhalar Odoo'dagi maydon nomi ("Bo'lim", "Ish telefoni"), texnik nom (department_id) emas.
    Texnik "id" ustuni faqat `keep_id` bilan va aniq so'ralganda qoladi."""
    meta = await _fields_meta(model)
    flds = [f for f in (fields or []) if f != "id" or keep_id]
    if not flds:
        skip = {"one2many", "many2many", "binary", "text", "html", "json", "properties"}
        flds = [f for f, m in meta.items()
                if m.get("type") not in skip and f != "id" and not _TECHNICAL_FIELD_RE.search(f)][:25]
        flds = flds or ["display_name"]

    limit = max(1, int(limit or 1000))
    raw: list[dict] = []
    while len(raw) < limit:
        kwargs = {"limit": min(_READ_PAGE_SIZE, limit - len(raw)), "context": _UZ_CTX}
        if raw or limit > _READ_PAGE_SIZE:
            # Bir nechta sahifa — tartib barqaror bo'lishi shart (aks holda qatorlar takrorlanadi)
            kwargs.update(offset=len(raw), order="id")
        page = await _odoo.execute_kw(model, "search_read", [domain or [], flds], kwargs)
        raw.extend(page)
        if len(page) < kwargs["limit"]:
            break

    names = await _resolve_x2many_names(raw, flds, meta)
    rows = [{f: _display_cell(r.get(f), meta.get(f) or {}, names.get(f)) for f in flds} for r in raw]
    return rows, [(f, _field_label(f, meta)) for f in flds], meta


def _numeric_fields(columns: list[tuple[str, str]], meta: dict) -> list[str]:
    """Jamlash ma'noga ega son ustunlar (summa, miqdor) — tartib raqami/rang kabi emas."""
    skip = {"sequence", "color", "priority"}
    return [f for f, _ in columns
            if (meta.get(f) or {}).get("type") in ("float", "monetary", "integer") and f not in skip]


async def _search_read_clean(model: str, domain: list | None = None,
                             fields: list | None = None, limit: int = 1000) -> list[dict]:
    """Odoo'dan yozuvlarni o'qib, nomlarini ochib beradi (kalitlar — maydon nomlari)."""
    rows, _columns, _meta = await _read_rows(model, domain, fields, limit)
    return rows


async def _count(model: str, domain: list | None = None) -> int:
    return await _odoo.execute_kw(model, "search_count", [domain or []])


_storable_domain_cache: list | None = None


async def _storable_domain() -> list:
    """Omborda hisobga olinadigan mahsulotlar filtri. Odoo 18'da bu `is_storable=True`
    (`type='product'` qiymati endi yo'q — u bilan qidiruv har doim 0 ta natija berardi)."""
    global _storable_domain_cache
    if _storable_domain_cache is None:
        meta = await _fields_meta("product.product")
        if not meta:
            return [["type", "in", ["product", "consu"]]]
        _storable_domain_cache = ([["is_storable", "=", True]] if "is_storable" in meta
                                  else [["type", "=", "product"]])
    return _storable_domain_cache


async def _save_pdf(render, data: dict, filename: str) -> str:
    """PDF'ni (reportlab — bloklovchi) alohida oqimda yasab, hisobotlar papkasiga ko'chiradi."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = await asyncio.to_thread(render, data)
    shutil.move(tmp_path, str(REPORTS_DIR / filename))
    return _report_url(filename)


# ─── MOLIYA TOOLS ──────────────────────────────────────────────────────────

# "Kassa chiqim" — shu kompaniya Odoo'sidagi alohida (Studio) hujjat: pul bir hisobdan
# ("Chiqim") ikkinchisiga ("Kirim") o'tkaziladi. Ilgari bu tool mijozga yozilgan
# hisob-fakturalarni (account.move, out_invoice) "kassa chiqimi" deb qaytarardi.
_KASSA_MODEL = "x_kassa_chiqim"
_KASSA_STATUS_FIELD = "x_studio_selection_field_8t_1je44ddti"
_KASSA_STATUS_KEYS = {"new": "status1", "done": "status2", "cancel": "status3"}
_KASSA_STATUS_LABELS = {"status1": "Yangi", "status2": "Bajarilgan", "status3": "Bekor qilingan"}


async def search_kassa_chiqim(domain: list | None = None, limit: int = 50, status: str = "",
                              date_from: str = "", date_to: str = "") -> dict:
    """Kassa chiqimlari (Odoo'dagi "Kassa chiqim" hujjatlari): ro'yxat + valyuta bo'yicha jami."""
    domain = list(domain or [])
    if status:
        domain.append([_KASSA_STATUS_FIELD, "=", _KASSA_STATUS_KEYS.get(status, status)])
    domain += _dt_bounds("create_date", date_from, date_to)
    limit = max(1, min(int(limit or 50), 200))

    rows, total, sums = await asyncio.gather(
        _odoo.execute_kw(
            _KASSA_MODEL, "search_read",
            [domain, ["x_name", "x_studio_amount", "x_studio_currency_id", "x_studio_chiqim",
                      "x_studio_kirim", _KASSA_STATUS_FIELD, "x_studio_note_selection",
                      "create_date", "create_uid"]],
            {"limit": limit, "order": "id desc"},
        ),
        _count(_KASSA_MODEL, domain),
        _rg(_KASSA_MODEL, domain, ["x_studio_amount"], ["x_studio_currency_id"]),
    )
    return {
        "jami_soni": total,
        "korsatilgan": len(rows),
        "jami_summa": [
            {"valyuta": _m2o_name(s.get("x_studio_currency_id"), "ko'rsatilmagan"),
             "summa": s.get("x_studio_amount") or 0, "soni": _group_count(s, "x_studio_currency_id")}
            for s in sums
        ],
        "yozuvlar": [
            {"id": r["id"],
             "sana": _local_datetime(r["create_date"]) if r.get("create_date") else "",
             "summa": r.get("x_studio_amount") or 0,
             "valyuta": _m2o_name(r.get("x_studio_currency_id"), ""),
             "chiqim_hisobi": _m2o_name(r.get("x_studio_chiqim"), ""),
             "kirim_hisobi": _m2o_name(r.get("x_studio_kirim"), ""),
             "holat": _KASSA_STATUS_LABELS.get(r.get(_KASSA_STATUS_FIELD), r.get(_KASSA_STATUS_FIELD) or ""),
             "turi": r.get("x_studio_note_selection") or "",
             "izoh": r.get("x_name") or "",
             "kiritgan": _m2o_name(r.get("create_uid"), "")}
            for r in rows
        ],
    }


async def create_kassa_chiqim(partner_id: int = 0, amount: float = 0, description: str = "") -> dict:
    """Bot orqali kassa chiqimi yaratilmaydi.

    Ilgari bu tool "kassa chiqimi" o'rniga mijozga HISOB-FAKTURA qoralamasini
    (account.move, out_invoice) yaratardi — buxgalteriyada mutlaqo boshqa hujjat."""
    return {"error": "Kassa chiqimini bot orqali yaratib bo'lmaydi: Odoo'dagi \"Kassa chiqim\" hujjati "
                     "chiqim va kirim hisoblarini talab qiladi va tasdiqlanganda buxgalteriya "
                     "o'tkazmasini yaratadi. Uni Odoo'ning o'zida kiriting. Mavjud chiqimlarni "
                     "ko'rish uchun search_kassa_chiqim ishlatiladi."}


async def search_payments(domain: list | None = None, limit: int = 50) -> list[dict]:
    """To'lovlarni qidirish (account.payment) — sukut bo'yicha o'tkazilgan to'lovlar."""
    domain = domain or [["state", "in", _PAYMENT_DONE_STATES]]
    return await _odoo.execute_kw(
        "account.payment", "search_read",
        [domain, ["id", "name", "date", "amount", "currency_id", "partner_id", "payment_type", "state"]],
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


async def create_currency_exchange(from_currency_id: int = 0, to_currency_id: int = 0,
                                    amount: float = 0, rate: float = 0) -> dict:
    """Bot orqali valyuta ayirboshlash kiritilmaydi.

    Ilgari bu tool valyuta KURSLARI jadvaliga (res.currency.rate) mavjud bo'lmagan
    maydonlar bilan yozishga urinardi va har doim Odoo xatosi bilan tugardi."""
    return {"error": "Valyuta ayirboshlashni bot orqali kiritib bo'lmaydi: Odoo'dagi \"Currency Exchange\" "
                     "hujjati mijoz, kurs va buxgalteriya hisoblarini talab qiladi. Uni Odoo'ning "
                     "o'zida kiriting. Mavjud ayirboshlashlarni ko'rish uchun odoo_search_read "
                     "(model: account.currency.exchange) ishlatiladi."}


async def get_invoice_pdf(invoice_id: int) -> str | None:
    """Schyot-faktura PDF hujjatini (reportlab bilan) generatsiya qilib, faylni
    REPORTS_DIR'ga saqlaydi va havolasini qaytaradi."""
    invoices = await _odoo.execute_kw(
        "account.move", "search_read",
        [[["id", "=", invoice_id]]],
        {"fields": ["name", "invoice_date", "partner_id", "payment_state", "currency_id",
                    "amount_total", "amount_tax", "amount_residual"], "limit": 1},
    )
    if not invoices:
        return None
    lines = await _odoo.execute_kw(
        "account.move.line", "search_read",
        # Odoo 16+: mahsulot qatorlari display_type='product' (ilgari False edi — o'sha
        # filtr bilan faktura PDF'i qatorlarsiz chiqardi)
        [[["move_id", "=", invoice_id], ["display_type", "in", ["product", False]]]],
        {"fields": ["name", "quantity", "price_unit", "price_subtotal"]},
    )
    from app.services.pdf_documents import render_invoice_pdf
    return await _save_pdf(
        render_invoice_pdf,
        {"invoice": invoices[0], "lines": lines, "currency": _currency_label(invoices[0].get("currency_id"))},
        f"invoice_{invoice_id}_{_unguessable_suffix()}.pdf",
    )


async def get_payment_receipt_pdf(payment_id: int) -> str | None:
    """To'lov kvitansiyasi PDF hujjatini generatsiya qilib, havolasini qaytaradi."""
    payments = await _odoo.execute_kw(
        "account.payment", "search_read",
        [[["id", "=", payment_id]]],
        {"fields": ["name", "date", "partner_id", "payment_type", "amount", "currency_id", "state"], "limit": 1},
    )
    if not payments:
        return None
    from app.services.pdf_documents import render_payment_receipt_pdf
    return await _save_pdf(
        render_payment_receipt_pdf,
        {"payment": payments[0], "currency": _currency_label(payments[0].get("currency_id"))},
        f"payment_{payment_id}_{_unguessable_suffix()}.pdf",
    )


# ─── OMBOR TOOLS ───────────────────────────────────────────────────────────

async def find_product(query: str, limit: int = 20) -> list[dict]:
    """Mahsulotlarni nomi, artikuli yoki shtrix-kodi bo'yicha qidirish."""
    return await _odoo.execute_kw(
        "product.product", "search_read",
        [["|", "|", ["name", "ilike", query], ["default_code", "ilike", query], ["barcode", "ilike", query]],
         ["id", "name", "default_code", "list_price", "qty_available", "uom_id", "categ_id"]],
        {"limit": limit},
    )


async def check_stock_quant(product_id: int) -> dict:
    """Mahsulotning ombordagi qoldig'i: jami va har bir ombor joyi bo'yicha.

    Faqat ichki (internal) joylar hisoblanadi — ilgari birinchi uchragan bitta yozuv
    qaytarilardi (u mijoz/ta'minotchi "joyi"dagi manfiy qoldiq bo'lishi ham mumkin edi)."""
    quants = await _odoo.execute_kw(
        "stock.quant", "search_read",
        [[["product_id", "=", product_id], ["location_id.usage", "=", "internal"]],
         ["product_id", "location_id", "quantity", "reserved_quantity"]],
        {"limit": 500},
    )
    total = sum(q.get("quantity") or 0 for q in quants)
    reserved = sum(q.get("reserved_quantity") or 0 for q in quants)
    return {
        "product_id": product_id,
        "mahsulot": _m2o_name(quants[0].get("product_id"), "") if quants else "",
        "jami_qoldiq": total,
        "band_qilingan": reserved,
        "mavjud": total - reserved,
        "joylar": [
            {"joy": _m2o_name(q.get("location_id"), ""), "qoldiq": q.get("quantity") or 0,
             "band_qilingan": q.get("reserved_quantity") or 0}
            for q in quants if q.get("quantity")
        ],
    }


async def check_low_stock(threshold: float = 10.0, include_zero: bool = False, limit: int = 100) -> dict:
    """Kam qoldiqdagi mahsulotlar (0 < qoldiq <= chegara); tugaganlari alohida sanaladi."""
    storable = await _storable_domain()
    low = storable + [["qty_available", "<=", threshold]]
    if not include_zero:
        low = low + [["qty_available", ">", 0]]
    limit = max(1, min(int(limit or 100), 300))
    rows, total, out_of_stock = await asyncio.gather(
        _odoo.execute_kw(
            "product.product", "search_read",
            [low, ["id", "name", "default_code", "qty_available", "uom_id", "categ_id"]],
            {"limit": limit},
        ),
        _count("product.product", low),
        _count("product.product", storable + [["qty_available", "<=", 0]]),
    )
    return {
        "chegara": threshold,
        "kam_qolgan_soni": total,
        "tugagan_soni": out_of_stock,
        "korsatilgan": len(rows),
        "mahsulotlar": sorted(rows, key=lambda p: p.get("qty_available") or 0),
    }


async def create_internal_transfer(product_id: int, quantity: float,
                                    from_location_id: int, to_location_id: int) -> int:
    """Ichki ko'chirish (transfer) hujjati qoralamasini yaratish."""
    # Operatsiya turi (picking_type_id) Odoo'da majburiy — ichki ko'chirish turi topiladi,
    # imkon bo'lsa aynan shu manba joyiga biriktirilgani
    types = await _odoo.execute_kw(
        "stock.picking.type", "search_read",
        [[["code", "=", "internal"]], ["id", "default_location_src_id"]],
        {"limit": 50},
    )
    if not types:
        raise OdooError("Odoo'da ichki ko'chirish (internal) operatsiya turi topilmadi")
    picking_type = next(
        (t for t in types if (t.get("default_location_src_id") or [None])[0] == from_location_id),
        types[0],
    )
    vals = {
        "picking_type_id": picking_type["id"],
        "location_id": from_location_id,
        "location_dest_id": to_location_id,
        "move_ids": [(0, 0, {
            "product_id": product_id,
            "product_uom_qty": quantity,
            "name": f"Transfer #{product_id}",
            "location_id": from_location_id,
            "location_dest_id": to_location_id,
        })],
    }
    return await _odoo.execute_kw("stock.picking", "create", [vals])


async def get_delivery_slip_pdf(picking_id: int) -> str | None:
    """Nakladnoy/transfer akti PDF hujjatini generatsiya qilib, havolasini qaytaradi."""
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
    from app.services.pdf_documents import render_delivery_slip_pdf
    return await _save_pdf(
        render_delivery_slip_pdf, {"picking": pickings[0], "moves": moves},
        f"delivery_{picking_id}_{_unguessable_suffix()}.pdf",
    )


# ─── SOTUV TOOLS ───────────────────────────────────────────────────────────

async def find_partner(query: str, limit: int = 20) -> list[dict]:
    """Mijozlarni nomi yoki telefoni bo'yicha qidirish."""
    return await _odoo.execute_kw(
        "res.partner", "search_read",
        [["|", ["name", "ilike", query], ["phone", "ilike", query]],
         ["id", "name", "email", "phone", "category_id"]],
        {"limit": limit},
    )


async def search_crm_leads(domain: list | None = None, limit: int = 50) -> list[dict]:
    """CRM lidlarini qidirish."""
    domain = domain or []
    return await _odoo.execute_kw(
        "crm.lead", "search_read",
        [domain, ["id", "name", "partner_name", "stage_id", "expected_revenue", "create_date", "user_id"]],
        {"limit": limit, "order": "create_date desc"},
    )


async def create_crm_lead(name: str, partner_name: str = "", planned_revenue: float = 0.0) -> int:
    """Yangi CRM lead yaratish."""
    vals = {"name": name}
    if partner_name:
        vals["partner_name"] = partner_name
    if planned_revenue:
        # Maydon nomi Odoo'da expected_revenue (planned_revenue eski versiyalarda edi)
        vals["expected_revenue"] = planned_revenue
    return await _odoo.execute_kw("crm.lead", "create", [vals])


async def find_stale_leads(days: int = 30) -> list[dict]:
    """Harakatsiz (eski) CRM lidlarni aniqlash."""
    cutoff = (_today() - timedelta(days=days)).isoformat()
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
        vals = {
            "product_id": line["product_id"],
            "product_uom_qty": line.get("product_uom_qty", 1.0),
        }
        # Narx berilmagan bo'lsa Odoo narxlar ro'yxatidan o'zi qo'yadi (0 yozib yuborilmaydi)
        if line.get("price_unit") is not None:
            vals["price_unit"] = line["price_unit"]
        line_vals.append((0, 0, vals))
    vals = {
        "partner_id": partner_id,
        "order_line": line_vals,
    }
    return await _odoo.execute_kw("sale.order", "create", [vals])


async def check_partner_debt(partner_id: int) -> dict:
    """Mijozning qarzdorligini tekshirish (jami — barcha to'lanmagan fakturalar bo'yicha)."""
    domain = [["partner_id", "=", partner_id], ["move_type", "=", "out_invoice"],
              ["state", "=", "posted"], ["amount_residual", ">", 0]]
    invoices, totals = await asyncio.gather(
        _odoo.execute_kw(
            "account.move", "search_read",
            [domain, ["id", "name", "invoice_date", "invoice_date_due", "amount_total",
                      "amount_residual", "currency_id"]],
            {"limit": 50, "order": "invoice_date desc"},
        ),
        _rg("account.move", domain, ["amount_residual"], ["currency_id"]),
    )
    debts = [
        {"valyuta": _m2o_name(t.get("currency_id"), ""), "qarz": t.get("amount_residual") or 0,
         "fakturalar_soni": _group_count(t, "currency_id")}
        for t in totals
    ]
    return {
        "partner_id": partner_id,
        "total_debt": sum(d["qarz"] for d in debts) if len(debts) <= 1 else None,
        "qarz_valyuta_boyicha": debts,
        "unpaid_invoices": sum(d["fakturalar_soni"] for d in debts),
        "invoices": invoices,
    }


async def get_sale_order_pdf(order_id: int) -> str | None:
    """Sotuv buyurtmasi PDF hujjatini generatsiya qilib, havolasini qaytaradi."""
    orders = await _odoo.execute_kw(
        "sale.order", "search_read",
        [[["id", "=", order_id]]],
        {"fields": ["name", "date_order", "partner_id", "user_id", "amount_total", "currency_id", "note"],
         "limit": 1},
    )
    if not orders:
        return None
    lines = await _odoo.execute_kw(
        "sale.order.line", "search_read",
        [[["order_id", "=", order_id], ["display_type", "=", False]]],
        {"fields": ["product_id", "product_uom_qty", "price_unit", "price_subtotal"]},
    )
    from app.services.pdf_documents import render_sale_order_pdf
    return await _save_pdf(
        render_sale_order_pdf,
        {"order": orders[0], "lines": lines, "currency": _currency_label(orders[0].get("currency_id"))},
        f"sale_order_{order_id}_{_unguessable_suffix()}.pdf",
    )


# ─── CEO ANALYTICS TOOLS ───────────────────────────────────────────────────

EXPORT_MAX_ROWS = 50000  # bitta faylga sig'adigan eng ko'p yozuv


def _export_slug(title: str) -> str:
    return rh.slug(title)


async def _export_rows(model: str, domain: list | None = None, fields: list | None = None,
                       limit: int = 10000) -> tuple[list[dict], list[tuple[str, str]], int, dict[str, str]]:
    """Eksport uchun tayyor qatorlar, ustunlar [(maydon, sarlavha)], bazadagi JAMI soni va
    maydon turlari {maydon: "monetary" | "date" | ...} (fayl formatlash uchun)."""
    limit = max(1, min(int(limit or 10000), EXPORT_MAX_ROWS))
    (rows, columns, meta), total = await asyncio.gather(
        _read_rows(model, domain, fields, limit, keep_id=True),
        _count(model, domain),
    )
    types = {field: (meta.get(field) or {}).get("type") or "" for field, _label in columns}
    return rows, columns, max(total, len(rows)), types


def _export_result(filename: str, shown: int, total: int) -> dict | str:
    """Fayl havolasi; yozuvlar sig'may qolgan bo'lsa — model buni foydalanuvchiga aytishi uchun
    aniq sonlar bilan (ilgari 10 000 dan ortig'i hech qanday ogohlantirishsiz tushib qolardi)."""
    url = _report_url(filename)
    if total <= shown:
        return url
    return {
        "url": url, "rows_in_file": shown, "rows_total": total,
        "note": f"Faylga jami {total:,} ta yozuvdan dastlabki {shown:,} tasi kirdi — buni "
                f"foydalanuvchiga ayting; to'liq ma'lumot uchun filtrni (davr, bo'lim) toraytirish kerak.",
    }


def _display_value(val) -> str:
    """Katakka yoziladigan qiymat — 0 bo'lsa bo'sh qoldiriladi."""
    if isinstance(val, bool):
        return "Ha" if val else ""
    if isinstance(val, (int, float)) and val == 0:
        return ""
    return val


# ── Excel ko'rinishi ──
_XLSX_HEADER_FILL = "184F95"   # sarlavha qatori (to'q ko'k; oq matn bilan kontrasti 8:1)
_XLSX_ACCENT = "2A78D6"
_XLSX_STRIPE = "F3F7FD"        # juft qatorlar (juda och ko'k)
_XLSX_TOTAL_FILL = "E3EDFA"
_XLSX_MUTED = "6B6A66"
_XLSX_HEADER_ROW = 4           # 1-qator: nom, 2-qator: izoh, 3-qator: bo'sh
_XLSX_FULL_STYLE_MAX_ROWS = 5000  # bundan katta faylda har katakni bezash juda sekin
_XLSX_MAX_WIDTH = 50


_XLSX_DATE = "DD.MM.YYYY"
_XLSX_DATETIME = "DD.MM.YYYY HH:MM"


def _xlsx_column_format(field: str, ftype: str, values: list) -> str | None:
    """Ustunning raqam formati — butun ustun uchun bitta (bir ustunda "1 000" va "1 000,50"
    aralash chiqmasligi uchun): sana, sana-vaqt, butun son yoki ikki kasr xonali summa."""
    if ftype == "date":
        return _XLSX_DATE
    if ftype == "datetime":
        return _XLSX_DATETIME
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return None
    if field == "id":
        return "0"
    if ftype == "integer" or all(float(v).is_integer() for v in numbers):
        return "#,##0"
    return "#,##0.00"


def _xlsx_value(value, number_format: str | None):
    """Katak qiymati: sana — haqiqiy Excel sanasi (saralash va filtr to'g'ri ishlaydi),
    0 va bo'sh qiymat — bo'sh katak."""
    if value is None or value == "" or value is False:
        return None
    if value is True:
        return "Ha"
    if isinstance(value, (int, float)):
        return value or None  # 0 qiymatlar bo'sh qoldiriladi
    if isinstance(value, str) and number_format == _XLSX_DATE:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return value
    if isinstance(value, str) and number_format == _XLSX_DATETIME:
        try:
            return datetime.strptime(value[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return value
    return value


def _xlsx_text_width(value, number_format) -> int:
    """Katak matni taxminan necha belgi joy egallashi (ustun kengligi uchun)."""
    if value is None:
        return 0
    if isinstance(value, datetime):
        return 16
    if isinstance(value, date):
        return 10
    if isinstance(value, (int, float)):
        return len(f"{value:,.2f}" if number_format == "#,##0.00" else f"{value:,.0f}")
    return max((len(line) for line in str(value).split("\n")), default=0)


def _write_xlsx(path: Path, title: str, rows: list[dict], columns: list[tuple[str, str]],
                types: dict[str, str] | None = None, total: int | None = None) -> None:
    """Excel fayl: sarlavha bloki, rangli ustun nomlari, navbatlashgan qatorlar, sonlar va
    sanalar o'z formatida, summalar bo'yicha "Jami" qatori, filtr, qotirilgan sarlavha va
    bosmaga tayyor sahifa sozlamalari."""
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.properties import PageSetupProperties

    types = types or {}
    count = len(columns)
    last_letter = get_column_letter(count)
    first_row = _XLSX_HEADER_ROW + 1
    last_row = _XLSX_HEADER_ROW + len(rows)
    polish = len(rows) <= _XLSX_FULL_STYLE_MAX_ROWS

    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\[\]:*?/\\]", " ", title or "Hisobot")[:31] or "Hisobot"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = _XLSX_ACCENT
    wb.properties.title = title or "Hisobot"
    wb.properties.creator = "dunyabunya agent"

    # 1) Sarlavha bloki
    ws.cell(row=1, column=1, value=title or "Hisobot").font = Font(size=15, bold=True, color="0B0B0B")
    ws.row_dimensions[1].height = 26
    shown = len(rows)
    info = f"{shown:,} ta yozuv".replace(",", " ")
    if total and total > shown:
        info = f"jami {total:,} ta yozuvdan dastlabki {shown:,} tasi".replace(",", " ")
    info += f"  ·  {datetime.now(_TZ).strftime('%d.%m.%Y %H:%M')}  ·  dunyabunya"
    ws.cell(row=2, column=1, value=info).font = Font(size=9, color=_XLSX_MUTED)

    # 2) Ustun nomlari (son va sana ustunlari — qiymatlari kabi o'ngga tekislanadi)
    formats = {
        ci: _xlsx_column_format(field, types.get(field) or "", [row.get(field) for row in rows])
        for ci, (field, _label) in enumerate(columns, start=1)
    }
    header_font = Font(bold=True, color="FFFFFF", size=10.5)
    header_fill = PatternFill("solid", fgColor=_XLSX_HEADER_FILL)
    header_border = Border(bottom=Side(style="medium", color=_XLSX_ACCENT))
    for ci, (_field, label) in enumerate(columns, start=1):
        cell = ws.cell(row=_XLSX_HEADER_ROW, column=ci, value=str(label))
        cell.font = header_font
        cell.fill = header_fill
        cell.border = header_border
        cell.alignment = Alignment(horizontal="right" if formats[ci] else "left", vertical="center",
                                   wrap_text=True, indent=1)
    ws.row_dimensions[_XLSX_HEADER_ROW].height = 30

    # 3) Ma'lumotlar
    widths = [len(str(label)) + 4 for _field, label in columns]  # +4: filtr tugmasi uchun joy
    sums: dict[int, float] = {}
    measures = {ci for ci, (field, _label) in enumerate(columns, start=1)
                if types.get(field) in ("monetary", "float")}
    top = Alignment(vertical="top")
    wrap = Alignment(vertical="top", wrap_text=True)
    for ri, row in enumerate(rows, start=first_row):
        for ci, (field, _label) in enumerate(columns, start=1):
            number_format = formats[ci]
            value = _xlsx_value(row.get(field), number_format)
            if value is None:
                continue
            cell = ws.cell(row=ri, column=ci, value=value)
            if number_format and not isinstance(value, str):
                cell.number_format = number_format
            if ci in measures and isinstance(value, (int, float)):
                sums[ci] = sums.get(ci, 0) + value
            if polish or ri - first_row < 300:
                width = _xlsx_text_width(value, number_format)
                if width > widths[ci - 1]:
                    widths[ci - 1] = width
                if polish and isinstance(value, str):
                    cell.alignment = wrap if (width > _XLSX_MAX_WIDTH or "\n" in value) else top
                elif polish:
                    cell.alignment = top

    # 4) Jami qatori (faqat summa/miqdor ustunlari bo'lsa)
    if sums and len(rows) > 1:
        total_row = last_row + 1
        total_font = Font(bold=True)
        total_fill = PatternFill("solid", fgColor=_XLSX_TOTAL_FILL)
        total_border = Border(top=Side(style="thin", color=_XLSX_ACCENT))
        label_col = next((ci for ci in range(1, count + 1) if ci not in sums), None)
        for ci in range(1, count + 1):
            cell = ws.cell(row=total_row, column=ci)
            cell.font, cell.fill, cell.border = total_font, total_fill, total_border
            if ci in sums:
                cell.value = sums[ci]
                cell.number_format = formats[ci] or "#,##0.00"
                widths[ci - 1] = max(widths[ci - 1], _xlsx_text_width(sums[ci], cell.number_format) + 1)
            elif ci == label_col:
                # Faylga yozuvlarning bir qismi sig'gan bo'lsa — yig'indi ham shu qismga tegishli
                cell.value = "Jami (fayldagi qatorlar)" if total and total > shown else "Jami"
        ws.row_dimensions[total_row].height = 20

    # 5) Navbatlashgan qatorlar — bitta shartli format (har katakni bo'yamasdan, katta faylda ham tez)
    if rows:
        ws.conditional_formatting.add(
            f"A{first_row}:{last_letter}{last_row}",
            FormulaRule(formula=["MOD(ROW(),2)=0"], stopIfTrue=False,
                        fill=PatternFill(start_color=_XLSX_STRIPE, end_color=_XLSX_STRIPE, fill_type="solid")),
        )

    for ci, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(ci)].width = max(8, min(width + 2, _XLSX_MAX_WIDTH))

    # 6) Ko'rish va bosma sozlamalari
    ws.freeze_panes = ws.cell(row=first_row, column=2 if count >= 5 else 1)
    ws.auto_filter.ref = f"A{_XLSX_HEADER_ROW}:{last_letter}{max(last_row, _XLSX_HEADER_ROW)}"
    ws.print_title_rows = f"{_XLSX_HEADER_ROW}:{_XLSX_HEADER_ROW}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.oddFooter.center.text = "&P / &N"
    ws.oddFooter.right.text = "dunyabunya"
    wb.save(path)


async def export_excel(model: str = "", domain: list | None = None,
                       fields: list | None = None, title: str = "Hisobot",
                       limit: int = 10000) -> dict | str:
    """Odoo ma'lumotlarini haqiqiy Excel (.xlsx) faylga eksport qiladi.

    Foydalanuvchi excel/xlsx fayl so'raganda shu toolni chaqiring.
    model: qaysi Odoo model (masalan hr.employee, sale.order)
    domain/fields: qidiruv shartlari — avvalgi tool natijasiga mos bo'lsin.
    Fayl diskka yoziladi va havola qaytariladi (bot faylni ham yuboradi).
    """
    if not model:
        return "Excel uchun model ko'rsatilmadi. Masalan: model='hr.employee'"

    rows, columns, total, types = await _export_rows(model, domain, fields, limit)
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."
    try:
        import openpyxl  # noqa: F401
    except Exception as e:
        return f"Excel kutubxonasi yo'q: {e}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_export_slug(title)}_{uuid.uuid4().hex[:8]}.xlsx"
    # Katta faylni yozish soniyalab davom etadi — boshqa foydalanuvchilar kutib qolmasligi uchun alohida oqimda
    await asyncio.to_thread(_write_xlsx, REPORTS_DIR / filename, title, rows, columns, types, total)
    return _export_result(filename, len(rows), total)


_HTML_REPORT_MAX_ROWS = 1000


async def generate_html_report(model: str = "", domain: list | None = None,
                               fields: list | None = None,
                               title: str = "Hisobot") -> dict | str:
    """Har qanday Odoo model ma'lumotlaridan HTML hisobot (jadval + grafik) yaratadi.

    Foydalanuvchi HTML/grafik/jadval ko'rinishida so'raganda ishlatiladi.
    model: masalan hr.employee, sale.order, crm.lead
    """
    from app.services.claude_report import generate_report_link

    if not model:
        return "HTML hisobot uchun model ko'rsatilmadi. Masalan: model='hr.employee'"

    (rows, columns, meta), total = await asyncio.gather(
        _read_rows(model, domain, fields, _HTML_REPORT_MAX_ROWS),
        _count(model, domain),
    )
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    total = max(total, len(rows))
    numeric = _numeric_fields(columns, meta)
    totals = None
    description = f"{total:,} ta yozuv"
    if total > len(rows):
        description = f"jami {total:,} ta yozuvdan dastlabki {len(rows):,} tasi (to'liq ro'yxat uchun Excel)"
        if numeric:
            # Jadvalga sig'magan qatorlar ham hisobga kirishi uchun yig'indini Odoo'ning o'zi hisoblaydi
            try:
                grouped = await _odoo.execute_kw(model, "read_group", [domain or [], numeric, []])
                totals = grouped[0] if grouped else None
            except OdooError as e:
                if e.kind:
                    raise
                # Bazada saqlanmaydigan maydonni Odoo jamlay olmaydi — yig'indi kartochkasisiz davom etamiz
    link = generate_report_link(
        title=title, description=description.replace(",", " "), rows=rows,
        columns=columns, numeric=numeric, totals=totals, total_rows=total,
    )
    if link and total > len(rows):
        return {"url": link, "rows_in_file": len(rows), "rows_total": total,
                "note": f"Hisobotga jami {total:,} ta yozuvdan dastlabki {len(rows):,} tasi sig'di — buni "
                        f"foydalanuvchiga ayting (to'liq ro'yxat uchun Excel)."}
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
    """O'sish dinamikasini hisoblash (oylar kesimida)."""
    groupby = f"{date_field}:month"
    rows = await _rg(model, domain or [], [value_field], groupby)
    return _clean_groups(rows, groupby)


async def search_employees_readonly(query: str = "") -> list[dict]:
    """Xodimlarni o'qish rejimida qidirish."""
    domain = [["active", "=", True]]
    if query:
        domain.append(["name", "ilike", query])
    return await _odoo.execute_kw(
        "hr.employee", "search_read",
        [domain, ["id", "name", "job_id", "department_id", "work_email", "work_phone", "mobile_phone"]],
        {"limit": 1000},
    )


# ─── FORMAT EXPORTS (Word/PDF/CSV) ──────────────────────────────────────────

def _write_docx(path: Path, title: str, subtitle: str, rows: list[dict],
                columns: list[tuple[str, str]]) -> None:
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = docx.Document()
    doc.add_heading(title or "Hisobot", level=1)
    doc.add_paragraph().add_run(subtitle).italic = True

    tbl = doc.add_table(rows=1, cols=len(columns))
    tbl.style = "Light Grid Accent 1"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, (_field, label) in zip(tbl.rows[0].cells, columns):
        cell.text = str(label)
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True
    for row in rows:
        cells = tbl.add_row().cells
        for cell, (field, _label) in zip(cells, columns):
            val = _display_value(row.get(field))
            cell.text = "" if val is None else str(val)
    doc.save(path)


async def export_word(model: str = "", domain: list | None = None,
                      fields: list | None = None, title: str = "Hisobot",
                      limit: int = 10000) -> dict | str:
    """Odoo ma'lumotlaridan Word (.docx) hujjat yaratadi.
    Foydalanuvchi 'word', 'docx', 'hujjat qilib ber' so'rasa ishlatiladi.
    model: qaysi Odoo model (masalan hr.employee, sale.order)
    """
    if not model:
        return "Word uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    # Word jadvali minglab qatorda juda sekin va og'ir — 2000 qator bilan cheklanadi
    rows, columns, total, _types = await _export_rows(model, domain, fields, min(int(limit or 2000), 2000))
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."
    try:
        import docx  # noqa: F401
    except Exception as e:
        return f"Word kutubxonasi yo'q: {e}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_export_slug(title)}_{uuid.uuid4().hex[:8]}.docx"
    subtitle = f"{len(rows)} ta yozuv • {datetime.now(_TZ).strftime('%Y-%m-%d %H:%M')}"
    await asyncio.to_thread(_write_docx, REPORTS_DIR / filename, title, subtitle, rows, columns)
    return _export_result(filename, len(rows), total)


def _write_pdf(path: Path, title: str, subtitle: str, rows: list[dict],
               columns: list[tuple[str, str]]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from app.services.pdf_documents import FONT, FONT_BOLD

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleX", parent=styles["Title"], fontName=FONT_BOLD, fontSize=16)
    sub_style = ParagraphStyle("SubX", parent=styles["Normal"], fontName=FONT)
    head_style = ParagraphStyle("HeadX", parent=styles["BodyText"], fontName=FONT_BOLD, fontSize=8.5,
                                leading=10, textColor=colors.white)
    cell_style = ParagraphStyle("CellX", parent=styles["BodyText"], fontName=FONT, fontSize=7.5, leading=9)

    def para(value, style):
        # Paragraph matnni XML sifatida o'qiydi — "&", "<" ekranlanmasa PDF yaratilmay qoladi
        text = "" if value is None else str(value)
        return Paragraph(_html.escape(text, quote=False), style)

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4),
                            leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=title or "Hisobot")
    data = [[para(label, head_style) for _field, label in columns]]
    for row in rows:
        data.append([para(_display_value(row.get(field)), cell_style) for field, _label in columns])

    tbl = Table(data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A1A2E")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f8")]),
    ]))
    doc.build([para(title or "Hisobot", title_style), Spacer(1, 4 * mm),
               para(subtitle, sub_style), Spacer(1, 4 * mm), tbl])


async def export_pdf(model: str = "", domain: list | None = None,
                     fields: list | None = None, title: str = "Hisobot",
                     limit: int = 10000) -> dict | str:
    """Odoo ma'lumotlaridan PDF hisobot yaratadi.
    Foydalanuvchi 'pdf', 'pdf qilib ber' so'rasa ishlatiladi.
    """
    if not model:
        return "PDF uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    # PDF jadvali sahifalab teriladi — 5000 qatordan ortig'i daqiqalab ketadi
    rows, columns, total, _types = await _export_rows(model, domain, fields, min(int(limit or 5000), 5000))
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."
    try:
        import reportlab  # noqa: F401
    except Exception as e:
        return f"PDF kutubxonasi yo'q: {e}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_export_slug(title)}_{uuid.uuid4().hex[:8]}.pdf"
    await asyncio.to_thread(_write_pdf, REPORTS_DIR / filename, title, f"{len(rows)} ta yozuv", rows, columns)
    return _export_result(filename, len(rows), total)


def _write_csv(path: Path, rows: list[dict], columns: list[tuple[str, str]]) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([label for _field, label in columns])
        for row in rows:
            writer.writerow([_display_value(row.get(field)) for field, _label in columns])


async def export_csv(model: str = "", domain: list | None = None,
                     fields: list | None = None, title: str = "Hisobot",
                     limit: int = 10000) -> dict | str:
    """Odoo ma'lumotlaridan CSV fayl yaratadi (Excel ocha oladi)."""
    if not model:
        return "CSV uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    rows, columns, total, _types = await _export_rows(model, domain, fields, limit)
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_export_slug(title)}_{uuid.uuid4().hex[:8]}.csv"
    await asyncio.to_thread(_write_csv, REPORTS_DIR / filename, rows, columns)
    return _export_result(filename, len(rows), total)


# ─── MATN (TELEGRAM CHATIGA) EKSPORT ────────────────────────────────────────

TEXT_EXPORT_MAX_ROWS = 300  # bundan ko'pi chatda o'qib bo'lmaydi — Excel tavsiya qilinadi

# token -> tayyor Telegram-HTML matn. export_text shu yerga yozadi, assistant.ask_with_files
# olib ketadi: ro'yxatni model qayta yozmaydi (qisqartirmaydi, xato qilmaydi) — bot
# o'zi to'liq yuboradi.
_text_exports: dict[str, str] = {}
_TEXT_EXPORTS_MAX = 50  # olib ketilmagan (masalan javob yo'lda uzilgan) matnlar to'planib qolmasligi uchun


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
    if not model:
        return "Matn uchun model ko'rsatilmadi. Masalan: model='hr.employee'"
    limit = max(1, min(int(limit or TEXT_EXPORT_MAX_ROWS), TEXT_EXPORT_MAX_ROWS))
    flds = [f for f in (fields or []) if f != "id"] or ["display_name"]
    if group_by and group_by not in flds:
        flds.append(group_by)

    (rows, columns, _meta), total = await asyncio.gather(
        _read_rows(model, domain, flds, limit),
        _count(model, domain),
    )
    if not rows:
        return "Bu so'rov bo'yicha ma'lumot topilmadi."
    labels = dict(columns)

    main, *rest = [f for f in flds if f != group_by] or flds

    def _line(n: int, row: dict) -> str:
        head = _html.escape(_text_value(row.get(main)) or "—", quote=False)
        details = [
            f"{_html.escape(str(labels[f]), quote=False)}: {_html.escape(v, quote=False)}"
            for f in rest if (v := _text_value(row.get(f)))
        ]
        return f"{n}. <b>{head}</b>" + (" — " + "; ".join(details) if details else "")

    shown = len(rows)
    total = max(total, shown)
    safe_title = _html.escape(str(title or "Ro'yxat"), quote=False)
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
            group_title = _html.escape(name or "Ko'rsatilmagan", quote=False)
            out.append(f"<b>{group_title}</b> ({len(members)} ta)")
            out.extend(_line(n, row) for n, row in enumerate(members, start=1))
            out.append("")
    else:
        out.extend(_line(n, row) for n, row in enumerate(rows, start=1))

    while len(_text_exports) >= _TEXT_EXPORTS_MAX:
        _text_exports.pop(next(iter(_text_exports)))
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

_UNPAID_INVOICE_DOMAIN = [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
                          ["amount_residual", ">", 0]]


async def get_top_debtors(limit: int = 10) -> list[dict]:
    """Eng katta qarzdor MIJOZLAR — har bir mijozning barcha to'lanmagan fakturalari jami.

    Ilgari eng katta alohida FAKTURALAR qaytarilardi: 179 ta mayda qarzi bor mijoz
    ro'yxatga kirmay, bitta katta fakturali mijoz birinchi bo'lib chiqardi."""
    rows = await _rg(
        "account.move", _UNPAID_INVOICE_DOMAIN, ["amount_residual"], ["partner_id", "currency_id"],
        limit=max(1, min(int(limit or 10), 100)), orderby="amount_residual desc",
    )
    return [
        {"mijoz": _m2o_name(r.get("partner_id")),
         "partner_id": (r.get("partner_id") or [None])[0],
         "qarz": r.get("amount_residual") or 0,
         "valyuta": _m2o_name(r.get("currency_id"), ""),
         "tolanmagan_fakturalar": _group_count(r, "partner_id")}
        for r in rows
    ]


async def get_overdue_invoices(days: int = 0) -> dict:
    """To'lov muddati o'tgan to'lanmagan fakturalar (`days` — kamida necha kun kechikkan)."""
    cutoff = (_today() - timedelta(days=max(0, int(days or 0)))).isoformat()
    domain = _UNPAID_INVOICE_DOMAIN + [["invoice_date_due", "<", cutoff]]
    invoices, totals = await asyncio.gather(
        _odoo.execute_kw(
            "account.move", "search_read",
            [domain, ["id", "name", "partner_id", "invoice_date", "invoice_date_due",
                      "amount_total", "amount_residual", "currency_id"]],
            {"limit": 100, "order": "invoice_date_due asc"},
        ),
        _rg("account.move", domain, ["amount_residual"], ["currency_id"]),
    )
    return {
        "jami": [
            {"valyuta": _m2o_name(t.get("currency_id"), ""), "qarz": t.get("amount_residual") or 0,
             "fakturalar_soni": _group_count(t, "currency_id")}
            for t in totals
        ],
        "korsatilgan": len(invoices),
        "fakturalar": invoices,
    }


async def get_cashflow_summary(date_from: str = "", date_to: str = "") -> list[dict]:
    """Pul oqimi — oylar va valyutalar bo'yicha kirim, chiqim va farqi (account.payment)."""
    domain = [["state", "in", _PAYMENT_DONE_STATES]] + _date_bounds("date", date_from, date_to)
    groups = await _rg("account.payment", domain, ["amount"], ["date:month", "payment_type", "currency_id"])
    months: dict[tuple, dict] = {}
    for g in groups:
        key = (_month_key(g, "date:month") or (0, 0), _m2o_name(g.get("currency_id"), ""))
        item = months.setdefault(key, {"davr": _month_label(g, "date:month"), "valyuta": key[1],
                                        "kirim": 0.0, "chiqim": 0.0})
        side = "kirim" if g.get("payment_type") == "inbound" else "chiqim"
        item[side] += g.get("amount") or 0
    return [{**item, "farq": item["kirim"] - item["chiqim"]} for _key, item in sorted(months.items())]


async def get_sales_by_seller(date_from: str = "", date_to: str = "", limit: int = 10) -> list[dict]:
    """Sotuvchilar bo'yicha sotuv reytingi (summa kamayish tartibida)."""
    domain = [["state", "in", ["sale", "done"]]] + _dt_bounds("date_order", date_from, date_to)
    rows = await _rg("sale.order", domain, ["amount_total"], "user_id",
                     limit=limit, orderby="amount_total desc")
    return _clean_groups(rows, "user_id")


async def get_employees_by_department() -> list[dict]:
    """Xodimlar soni bo'limlar bo'yicha (hr.employee)."""
    rows = await _rg("hr.employee", [["active", "=", True]], ["id:count"], "department_id")
    return sorted(_clean_groups(rows, "department_id"), key=lambda g: -g["soni"])


async def get_upcoming_deadlines(days: int = 7) -> list[dict]:
    """Yaqin muddatdagi (N kun ichida) ochiq vazifalar."""
    today = _today()
    return await _odoo.execute_kw(
        "project.task", "search_read",
        [[["state", "not in", ["1_done", "1_canceled"]],
          ["date_deadline", ">=", today.isoformat()],
          ["date_deadline", "<=", (today + timedelta(days=days)).isoformat() + " 23:59:59"]],
         ["id", "name", "project_id", "user_ids", "date_deadline", "state", "priority"]],
        {"limit": 100, "order": "date_deadline asc"},
    )


async def get_crm_funnel() -> list[dict]:
    """CRM funnel — lidlar bosqichlar bo'yicha, kutilayotgan daromad bilan."""
    rows = await _rg("crm.lead", [], ["expected_revenue"], "stage_id")
    return _clean_groups(rows, "stage_id")


async def get_profit_summary(date_from: str = "", date_to: str = "") -> dict:
    """Fakturalar va to'lovlar xulosasi (valyuta bo'yicha): yozilgan fakturalar, qolgan qarz,
    mijozlardan tushgan va chiqib ketgan to'lovlar.

    Ilgari birinchi 500 ta faktura va 1000 ta to'lov qo'lda qo'shilardi — bu bazada (75 000
    faktura) natija bir necha foizni tashkil qilardi; to'lovlar esa 'posted' holati bo'yicha
    qidirilgani uchun 0 chiqardi. Endi jamlashni Odoo'ning o'zi bajaradi."""
    inv_domain = ([["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"]]
                  + _date_bounds("invoice_date", date_from, date_to))
    pay_domain = [["state", "in", _PAYMENT_DONE_STATES]] + _date_bounds("date", date_from, date_to)
    invoices, payments = await asyncio.gather(
        _rg("account.move", inv_domain, ["amount_total", "amount_residual"], ["currency_id", "move_type"]),
        _rg("account.payment", pay_domain, ["amount"], ["currency_id", "payment_type"]),
    )
    by_currency: dict[str, dict] = {}

    def bucket(row: dict) -> dict:
        return by_currency.setdefault(_m2o_name(row.get("currency_id"), "ko'rsatilmagan"), {
            "jami_faktura": 0.0, "qaytarilgan": 0.0, "qolgan_qarz": 0.0, "fakturalar_soni": 0,
            "mijozlardan_tushgan": 0.0, "chiqim_tolovlar": 0.0,
        })

    for row in invoices:
        b = bucket(row)
        if row.get("move_type") == "out_refund":
            b["qaytarilgan"] += row.get("amount_total") or 0
        else:
            b["jami_faktura"] += row.get("amount_total") or 0
            b["qolgan_qarz"] += row.get("amount_residual") or 0
            b["fakturalar_soni"] += _group_count(row, "currency_id")
    for row in payments:
        side = "mijozlardan_tushgan" if row.get("payment_type") == "inbound" else "chiqim_tolovlar"
        bucket(row)[side] += row.get("amount") or 0

    return {
        "davr": _period_label(date_from, date_to),
        "valyutalar": [{"valyuta": cur, **{k: round(v, 2) for k, v in vals.items()}}
                       for cur, vals in by_currency.items()],
        "izoh": "Bu sof foyda EMAS (tannarx va xarajatlar hisobga olinmagan) — faqat fakturalar "
                "va to'lovlar yig'indisi. Turli valyutalardagi summalar qo'shilmaydi.",
    }


# ─── STATISTICS TOOLS ───────────────────────────────────────────────────────

_SALE_DONE = [["state", "in", ["sale", "done"]]]


async def get_sales_stats(date_from: str = "", date_to: str = "", group_by: str = "date_order:month") -> list[dict]:
    """Sotuv statistikasi — sale.order bo'yicha guruhlab hisoblash."""
    domain = _SALE_DONE + _dt_bounds("date_order", date_from, date_to)
    rows = await _rg("sale.order", domain, ["amount_total", "amount_tax"], group_by)
    return _clean_groups(rows, group_by)


async def get_sales_by_partner(date_from: str = "", date_to: str = "", limit: int = 20) -> list[dict]:
    """Sotuv statistikasi — eng ko'p xarid qilgan mijozlar (summa kamayish tartibida)."""
    domain = _SALE_DONE + _dt_bounds("date_order", date_from, date_to)
    rows = await _rg("sale.order", domain, ["amount_total"], "partner_id",
                     limit=limit, orderby="amount_total desc")
    return _clean_groups(rows, "partner_id")


def _sale_line_domain(date_from: str, date_to: str) -> list:
    """Faqat TASDIQLANGAN buyurtmalarning mahsulot qatorlari, buyurtma sanasi bo'yicha
    (ilgari qoralama/bekor qilingan takliflar ham qo'shilib, summa oshib ketardi)."""
    return (_SALE_DONE + [["product_id", "!=", False], ["display_type", "=", False]]
            + _dt_bounds("order_id.date_order", date_from, date_to))


async def get_sales_by_product(date_from: str = "", date_to: str = "", limit: int = 20) -> list[dict]:
    """Sotuv statistikasi — eng ko'p sotilgan mahsulotlar (summa kamayish tartibida)."""
    rows = await _rg("sale.order.line", _sale_line_domain(date_from, date_to),
                     ["price_subtotal", "product_uom_qty"], "product_id",
                     limit=limit, orderby="price_subtotal desc")
    return _clean_groups(rows, "product_id")


async def get_invoice_stats(date_from: str = "", date_to: str = "") -> list[dict]:
    """Schyot-faktura statistikasi (mijozlarga yozilgan, oylar kesimida)."""
    domain = ([["move_type", "=", "out_invoice"], ["state", "=", "posted"]]
              + _date_bounds("invoice_date", date_from, date_to))
    rows = await _rg("account.move", domain, ["amount_total", "amount_residual"], "invoice_date:month")
    return _clean_groups(rows, "invoice_date:month")


async def get_crm_stats() -> list[dict]:
    """CRM lidlar statistikasi."""
    return _clean_groups(await _rg("crm.lead", [], ["id:count"], "stage_id"), "stage_id")


async def _task_state_groups() -> list[dict]:
    """Vazifalar holatlar bo'yicha: [{"holat": kod, "nomi": o'zbekcha nom, "soni": n}]."""
    rows, labels = await asyncio.gather(
        _rg("project.task", [], ["id:count"], "state"),
        _selection_labels("project.task", "state"),
    )
    return [
        {"holat": r.get("state") or "", "nomi": labels.get(r.get("state"), r.get("state") or "Ko'rsatilmagan"),
         "soni": _group_count(r, "state")}
        for r in rows
    ]


async def get_task_stats() -> list[dict]:
    """Vazifalar statistikasi."""
    return await _task_state_groups()


_UZ_MONTHS_SHORT = ("Yan", "Fev", "Mar", "Apr", "May", "Iyn", "Iyl", "Avg", "Sen", "Okt", "Noy", "Dek")


def _report_file(title: str) -> str:
    """Hisobot fayli nomi: o'qiladigan nom + taxmin qilib bo'lmaydigan qo'shimcha
    (Telegram'da "Kompaniya Statistikasi.html" bo'lib ko'rinadi)."""
    return f"{_export_slug(title)}_{uuid.uuid4().hex[:12]}.html"


async def _currency_info(model: str, domain: list) -> tuple[str, str]:
    """(valyuta nomi, ogohlantirish). Hujjatlar bitta valyutada bo'lsa — uning nomi;
    aralash bo'lsa — nom bo'sh va ogohlantirishda valyutalar bo'yicha taqsimot."""
    groups = await _rg(model, domain, ["amount_total"], "currency_id")
    named = [(_m2o_name(g.get("currency_id"), ""), g.get("amount_total") or 0)
             for g in groups if g.get("currency_id")]
    if len(named) == 1:
        return named[0][0], ""
    if len(named) > 1:
        parts = ", ".join(f"{name}: {rh.fmt_num(amount)}" for name, amount in named)
        return "", f"Diqqat: hujjatlar turli valyutalarda ({parts}) — jami summalar ularning oddiy yig'indisi."
    return "", ""


def _subtitle(date_from: str, date_to: str, currency: str) -> str:
    period = _period_label(date_from, date_to)
    return f"{period} · Summalar valyutasi: {currency}" if currency else period


def _month_axis(keys: list) -> tuple[list[str], list[str], list[str]]:
    """Oylar o'qi uchun yozuvlar: qisqa nom ("Mar"), ostidagi yil (birinchi ustunda va har
    yanvarda) va to'liq nom ("Mart 2026")."""
    short, years, full = [], [], []
    for i, key in enumerate(keys):
        if not key or key == (0, 0):
            short.append("—")
            years.append("")
            full.append("Sanasi yo'q")
            continue
        year, month = key
        short.append(_UZ_MONTHS_SHORT[month - 1])
        years.append(str(year) if i == 0 or month == 1 else "")
        full.append(f"{_UZ_MONTHS[month - 1]} {year}")
    return short, years, full


def _is_partial_month(key, date_to: str) -> bool:
    """Grafikdagi oxirgi oy hali tugamaganmi? Shunday bo'lsa uning ustuni past chiqadi —
    bu sotuv tushib ketgani emas, shuning uchun alohida (ochroq) ko'rsatiladi."""
    if not key or key == (0, 0):
        return False
    end = min(date.fromisoformat(date_to[:10]), _today()) if date_to else _today()
    return (end.year, end.month) == tuple(key) and (end + timedelta(days=1)).month == end.month


async def generate_sales_report(date_from: str = "", date_to: str = "") -> str:
    """Sotuv hisoboti — boyitilgan HTML report: grafik + jadvallar + tahlil."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    domain = _SALE_DONE + _dt_bounds("date_order", date_from, date_to)

    # Mustaqil statistikalarni PARALLEL o'qiymiz. "Top" ro'yxatlar summa bo'yicha
    # saralanadi (ilgari tartibsiz birinchi 15 ta guruh olinardi — ular eng yiriklari emas edi).
    monthly, by_partner, by_product, by_seller, (currency, currency_note) = await asyncio.gather(
        _rg("sale.order", domain, ["amount_total", "amount_tax"], "date_order:month"),
        _rg("sale.order", domain, ["amount_total"], "partner_id", limit=15, orderby="amount_total desc"),
        _rg("sale.order.line", _sale_line_domain(date_from, date_to),
            ["price_subtotal", "product_uom_qty"], "product_id", limit=15, orderby="price_subtotal desc"),
        _rg("sale.order", domain, ["amount_total"], "user_id", limit=10, orderby="amount_total desc"),
        _currency_info("sale.order", domain),
    )

    total_orders = sum(_group_count(m, "date_order:month") for m in monthly)
    total_amount = sum(m.get("amount_total") or 0 for m in monthly)
    total_tax = sum(m.get("amount_tax") or 0 for m in monthly)

    keys = [_month_key(m, "date_order:month") for m in monthly]
    short, years, full = _month_axis(keys)
    amounts = [round(m.get("amount_total") or 0) for m in monthly]

    monthly_blocks = []
    if len(monthly) >= 2:  # bitta oy uchun grafik chizilmaydi — uni kartochkadagi son aytadi
        monthly_blocks.append(rh.columns(
            short[-12:], [("Sotuv", amounts[-12:])], titles=full[-12:], sublabels=years[-12:],
            partial_last=_is_partial_month(keys[-1], date_to),
        ))
    monthly_blocks.append(rh.table(
        ["Oy", "Buyurtmalar", "Sotuv summasi"],
        [[label, _group_count(m, "date_order:month"), amount] for label, m, amount in zip(full, monthly, amounts)],
    ))

    body = "\n".join([
        rh.card("Oylik sotuv dinamikasi", *monthly_blocks),
        rh.grid(
            rh.card("Eng yirik mijozlar", rh.table(
                ["Mijoz", "Buyurtmalar", "Summa"],
                [[_m2o_name(p.get("partner_id")), _group_count(p, "partner_id"),
                  round(p.get("amount_total") or 0)] for p in by_partner],
                numbered=True, bar_col=2), sub="sotuv summasi bo'yicha"),
            rh.card("Eng ko'p sotilgan mahsulotlar", rh.table(
                ["Mahsulot", "Miqdori", "Summa"],
                [[_m2o_name(p.get("product_id")), round(p.get("product_uom_qty") or 0),
                  round(p.get("price_subtotal") or 0)] for p in by_product],
                numbered=True, bar_col=2), sub="sotuv summasi bo'yicha"),
        ),
        rh.card("Sotuvchilar reytingi", rh.table(
            ["Sotuvchi", "Buyurtmalar", "Summa"],
            [[_m2o_name(s.get("user_id"), "Biriktirilmagan"), _group_count(s, "user_id"),
              round(s.get("amount_total") or 0)] for s in by_seller],
            numbered=True, bar_col=2)),
        rh.note(currency_note),
    ])

    title = "Sotuv hisoboti"
    page = rh.page(
        title=title, subtitle=_subtitle(date_from, date_to, currency),
        tiles_html=rh.tiles(
            rh.tile("Jami sotuv", rh.fmt_num(total_amount), currency, hero=True),
            rh.tile("Buyurtmalar", rh.fmt_int(total_orders), "tasdiqlangan"),
            rh.tile("O'rtacha buyurtma", rh.fmt_num(total_amount / total_orders if total_orders else 0), currency),
            rh.tile("Soliq", rh.fmt_num(total_tax), currency) if total_tax else "",
        ),
        body=body,
    )
    filename = _report_file(title)
    (REPORTS_DIR / filename).write_text(page, encoding="utf-8")
    return _report_url(filename)


async def generate_invoice_report(date_from: str = "", date_to: str = "") -> str:
    """Schyot-faktura hisoboti — HTML report."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    domain = ([["move_type", "=", "out_invoice"], ["state", "=", "posted"]]
              + _date_bounds("invoice_date", date_from, date_to))
    stats, (currency, currency_note) = await asyncio.gather(
        _rg("account.move", domain, ["amount_total", "amount_residual"], "invoice_date:month"),
        _currency_info("account.move", domain),
    )
    if not stats:
        return "Bu davr uchun faktura ma'lumotlari topilmadi."

    keys = [_month_key(s, "invoice_date:month") for s in stats]
    short, years, full = _month_axis(keys)
    counts = [_group_count(s, "invoice_date:month") for s in stats]
    totals = [round(s.get("amount_total") or 0) for s in stats]
    debts = [round(s.get("amount_residual") or 0) for s in stats]
    paid = [t - d for t, d in zip(totals, debts)]
    total, debt = sum(totals), sum(debts)

    charts = [rh.card("To'lov holati", rh.meter(total - debt, total, "To'langan", "Qarz"),
                      sub="yozilgan fakturalardan")]
    if len(stats) >= 2:
        charts.append(rh.card("Oylar bo'yicha", rh.columns(
            short[-12:], [("To'langan", paid[-12:]), ("Qarz", debts[-12:])],
            titles=full[-12:], sublabels=years[-12:])))
    body = "\n".join([
        rh.grid(*charts),
        rh.card("Oylar kesimida", rh.table(
            ["Oy", "Fakturalar soni", "Jami summa", "To'langan", "Qarz"],
            [list(row) for row in zip(full, counts, totals, paid, debts)])),
        rh.note(currency_note),
    ])

    title = "Faktura hisoboti"
    page = rh.page(
        title=title, subtitle=_subtitle(date_from, date_to, currency),
        tiles_html=rh.tiles(
            rh.tile("Yozilgan fakturalar", rh.fmt_num(total), currency, hero=True),
            rh.tile("Fakturalar soni", rh.fmt_int(sum(counts)), "mijozlarga yozilgan"),
            rh.tile("To'langan", rh.fmt_num(total - debt), rh.fmt_pct(total - debt, total)),
            rh.tile("Qarz", rh.fmt_num(debt), rh.fmt_pct(debt, total)),
        ),
        body=body,
    )
    filename = _report_file(title)
    (REPORTS_DIR / filename).write_text(page, encoding="utf-8")
    return _report_url(filename)


async def generate_task_report() -> str:
    """Vazifalar hisoboti — HTML report."""
    from app.services.claude_report import generate_report_link

    groups = await _task_state_groups()
    rows = [{"holat": g["nomi"], "soni": g["soni"]} for g in groups]
    if not rows:
        return "Vazifalar topilmadi."

    link = generate_report_link(
        title="Vazifalar hisoboti",
        description="Vazifalar holatlari bo'yicha",
        rows=rows, columns=[("holat", "Holat"), ("soni", "Vazifalar soni")], numeric=["soni"],
        count_rows=False,
    )
    return link or "Hisobot yaratilmadi."


async def generate_company_report(date_from: str = "", date_to: str = "") -> str:
    """Kompaniyaning to'liq statistikasi — barcha bo'limlar, oylar kesimida.

    Standart holatda JORIY YIL (1-yanvardan bugungacha) bo'yicha hisoblaydi — "shu yilgi
    to'liq hisobot" so'ralganda parametrsiz chaqiring. Boshqa davr kerak bo'lsa
    date_from/date_to (YYYY-MM-DD) bering; butun tarix uchun ikkalasiga ham "all" bering.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # "shu yilgi" — standart bo'yicha joriy yil boshidan bugungacha (butun tarix uchun
    # date_from="all" beriladi). Jamlashni Odoo'ning o'zi (read_group) bajaradi — xom
    # qatorlar tortib olinmaydi, shuning uchun bir yillik davr ham og'ir/sekin bo'lmaydi.
    if date_from == "" and date_to == "":
        date_from = _today().replace(month=1, day=1).isoformat()
        date_to = _today().isoformat()
    elif date_from == "all" or date_to == "all":
        date_from = date_to = ""

    sales_domain = _SALE_DONE + _dt_bounds("date_order", date_from, date_to)
    invoice_domain = ([["move_type", "=", "out_invoice"], ["state", "=", "posted"]]
                      + _date_bounds("invoice_date", date_from, date_to))

    sales, invoices, tasks, crm, emp_count, (currency, currency_note) = await asyncio.gather(
        _rg("sale.order", sales_domain, ["amount_total"], "date_order:month"),
        _rg("account.move", invoice_domain, ["amount_total", "amount_residual"], "invoice_date:month"),
        _task_state_groups(),
        _rg("crm.lead", [], ["id:count"], "stage_id"),
        _count("hr.employee", [["active", "=", True]]),
        _currency_info("sale.order", sales_domain),
    )

    total_sales = sum(s.get("amount_total") or 0 for s in sales)
    total_orders = sum(_group_count(s, "date_order:month") for s in sales)
    total_invoiced = sum(i.get("amount_total") or 0 for i in invoices)
    total_residual = sum(i.get("amount_residual") or 0 for i in invoices)
    total_crm = sum(_group_count(c, "stage_id") for c in crm)
    open_tasks = sum(t["soni"] for t in tasks if t["holat"] not in ("1_done", "1_canceled"))

    # Oylar kesimi: sotuv va fakturalar bitta jadvalda
    months: dict[tuple, dict] = {}
    for s in sales:
        key = _month_key(s, "date_order:month") or (0, 0)
        months.setdefault(key, {}).update(
            orders=_group_count(s, "date_order:month"), sales=round(s.get("amount_total") or 0))
    for i in invoices:
        key = _month_key(i, "invoice_date:month") or (0, 0)
        months.setdefault(key, {}).update(
            invoiced=round(i.get("amount_total") or 0), residual=round(i.get("amount_residual") or 0))
    keys = sorted(months)
    short, years, full = _month_axis(keys)
    ordered = [months[k] for k in keys]

    sales_keys = [k for k in keys if "sales" in months[k]]
    sales_card = ""
    if len(sales_keys) >= 2:  # bitta oy uchun grafik chizilmaydi — uni kartochkadagi son aytadi
        chart_keys = sales_keys[-12:]
        c_short, c_years, c_full = _month_axis(chart_keys)
        sales_card = rh.card("Oylik sotuv", rh.columns(
            c_short, [("Sotuv", [months[k]["sales"] for k in chart_keys])],
            titles=c_full, sublabels=c_years,
            partial_last=_is_partial_month(chart_keys[-1], date_to),
        ), sub="tasdiqlangan buyurtmalar summasi")

    body = "\n".join([
        sales_card,
        rh.grid(
            rh.card("Fakturalar to'lovi",
                    rh.meter(total_invoiced - total_residual, total_invoiced, "To'langan", "Qarz"),
                    sub="davr ichida yozilgan fakturalardan"),
            rh.card("Vazifalar holati", rh.hbars([t["nomi"] for t in tasks], [t["soni"] for t in tasks]),
                    sub="hozirgi holat"),
            rh.card("CRM bosqichlari",
                    rh.hbars([_m2o_name(c.get("stage_id"), "Bosqichsiz") for c in crm[:8]],
                             [_group_count(c, "stage_id") for c in crm[:8]]),
                    sub=f"hozirgi holat · jami {rh.fmt_int(total_crm)} ta lid"),
        ),
        rh.card("Oylar kesimida", rh.table(
            ["Oy", "Buyurtmalar", "Sotuv", "Fakturalar", "Qarz"],
            [[label, m.get("orders", 0), m.get("sales", 0), m.get("invoiced", 0), m.get("residual", 0)]
             for label, m in zip(full, ordered)])),
        rh.note(currency_note),
    ])

    title = "Kompaniya statistikasi"
    page = rh.page(
        title=title, subtitle=_subtitle(date_from, date_to, currency),
        tiles_html=rh.tiles(
            rh.tile("Jami sotuv", rh.fmt_num(total_sales),
                    f"{rh.fmt_int(total_orders)} ta buyurtma" + (f" · {currency}" if currency else ""), hero=True),
            rh.tile("Fakturalar", rh.fmt_num(total_invoiced), "mijozlarga yozilgan"),
            rh.tile("To'lanmagan qarz", rh.fmt_num(total_residual),
                    f"yozilgan fakturalarning {rh.fmt_pct(total_residual, total_invoiced)}" if total_invoiced else ""),
            rh.tile("Ochiq vazifalar", rh.fmt_int(open_tasks), f"jami {rh.fmt_int(sum(t['soni'] for t in tasks))} ta"),
            rh.tile("Xodimlar", rh.fmt_int(emp_count), "faol"),
        ),
        body=body,
    )
    filename = _report_file(title)
    (REPORTS_DIR / filename).write_text(page, encoding="utf-8")
    return _report_url(filename)


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
        "description": "Kassa chiqimlari (Odoo'dagi 'Kassa chiqim' hujjatlari): so'nggi yozuvlar ro'yxati, "
                       "jami soni va valyuta bo'yicha jami summasi. Har yozuvda summa, valyuta, chiqim/kirim "
                       "hisobi, holat, turi va kim kiritgani bor.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["new", "done", "cancel"],
                           "description": "Holat bo'yicha filtr (ixtiyoriy): new=yangi, done=bajarilgan, cancel=bekor qilingan"},
                "date_from": {"type": "string", "description": "Kiritilgan sana, boshlanishi (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "Kiritilgan sana, tugashi (YYYY-MM-DD)"},
                "limit": {"type": "integer", "default": 50, "description": "Ro'yxatdagi yozuvlar soni (ko'pi bilan 200)"},
            },
        },
    },
    "create_kassa_chiqim": {
        "name": "create_kassa_chiqim",
        "description": "Kassa chiqimi yaratish — bot orqali MUMKIN EMAS (tool faqat sababini qaytaradi, "
                       "uni foydalanuvchiga ayting).",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Summa"},
                "description": {"type": "string", "description": "Tavsif"},
            },
        },
    },
    "search_payments": {
        "name": "search_payments",
        "description": "To'lovlarni qidirish (account.payment). Domain berilmasa o'tkazilgan to'lovlar "
                       "(state: in_process, paid) qaytadi. Har to'lovda valyuta (currency_id) bor — "
                       "turli valyutadagi summalarni qo'shmang.",
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
        "description": "Valyuta ayirboshlash kiritish — bot orqali MUMKIN EMAS (tool faqat sababini "
                       "qaytaradi, uni foydalanuvchiga ayting).",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "rate": {"type": "number"},
            },
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
        "description": "Mahsulotlarni nomi, artikuli (default_code) yoki shtrix-kodi bo'yicha qidirish; "
                       "natijada joriy qoldiq (qty_available) ham bor",
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
        "description": "Mahsulotning ombordagi qoldig'i: jami, band qilingan va har bir ombor joyi bo'yicha",
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "integer"}},
            "required": ["product_id"],
        },
    },
    "check_low_stock": {
        "name": "check_low_stock",
        "description": "Kam qoldiqdagi mahsulotlar (0 < qoldiq <= chegara): jami soni, tugagan mahsulotlar "
                       "soni va dastlabki mahsulotlar ro'yxati. To'liq ro'yxat uchun export_excel ishlating.",
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "default": 10.0, "description": "Chegara miqdori"},
                "include_zero": {"type": "boolean", "default": False,
                                 "description": "true bo'lsa tugagan (qoldig'i 0) mahsulotlar ham ro'yxatga kiradi"},
                "limit": {"type": "integer", "default": 100},
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
                "planned_revenue": {"type": "number", "description": "Kutilayotgan daromad"},
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
        "description": "Eng katta qarzdor mijozlar: har bir mijozning barcha to'lanmagan fakturalari "
                       "bo'yicha JAMI qarzi (kamayish tartibida), valyutasi va fakturalar soni",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
    },
    "get_overdue_invoices": {
        "name": "get_overdue_invoices",
        "description": "To'lov muddati o'tgan to'lanmagan fakturalar: valyuta bo'yicha jami qarz, soni "
                       "va eng eski 100 tasi ro'yxati",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 0,
                                    "description": "Kamida necha kun kechikkanlari (0 = muddati o'tganlarning hammasi)"}},
        },
    },
    "get_cashflow_summary": {
        "name": "get_cashflow_summary",
        "description": "Pul oqimi xulosasi — oylar va valyutalar bo'yicha kirim, chiqim va farqi (to'lovlar asosida)",
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
        "description": "Fakturalar va to'lovlar xulosasi (valyuta bo'yicha): yozilgan fakturalar, qaytarilgan, "
                       "qolgan qarz, mijozlardan tushgan va chiqib ketgan to'lovlar. Bu sof foyda EMAS — "
                       "javobda 'foyda' deb atamang.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
        },
    },
}
