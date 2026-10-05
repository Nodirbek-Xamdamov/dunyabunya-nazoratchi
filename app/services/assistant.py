"""CEO assistant — role-based tool routing with RBAC."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic

from app.config import settings
from app.db import repo
from app.db.session import SessionLocal
from app.rbac import Role, ROLE_TOOLS, ROLE_PROMPTS, ROLE_NAMES, ROLE_ALLOWED_MODELS
from app.services.odoo import OdooClient, OdooError, connection_hint
from app.services.odoo_tools import ALL_TOOLS, TOOL_SCHEMAS, pop_text_export
from app.prompt_guard import SECURITY_FRAGMENT, wrap_user_input

log = logging.getLogger(__name__)

_extra_headers = {}
if settings.ANTHROPIC_WORKSPACE_ID:
    _extra_headers["anthropic-workspace-id"] = settings.ANTHROPIC_WORKSPACE_ID

_client = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    default_headers=_extra_headers or None,
)
_odoo = OdooClient()

REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"


@dataclass
class FileOut:
    """Telegram'ga hujjat sifatida yuboriladigan generatsiya qilingan fayl."""
    kind: str       # xlsx / html / pdf ...
    filename: str
    path: str       # diskdagi to'liq manzil
    url: str        # ommaviy havola


@dataclass
class AssistantResult:
    """Assistant javobi: matn + (agar yaratilgan bo'lsa) fayllar."""
    text: str
    files: list[FileOut] = field(default_factory=list)
    file_error: str | None = None  # fayl so'ralib topilmagan bo'lsa — sababi (log/diagnostika uchun)
    # export_text tayyorlagan to'liq ro'yxatlar (tayyor Telegram-HTML) — alohida xabar bo'lib yuboriladi
    texts: list[str] = field(default_factory=list)
    # Odoo bilan aloqa yo'qligi sababli so'rov bajarilmagan bo'lsa — xato turi ("dns", "timeout"...)
    odoo_error: str | None = None


# Fayl/hisobot yaratuvchi tool'lar — shu nomlardan biri xato yoki "topilmadi" turidagi
# matn qaytarsa va oxir-oqibat hech qanday fayl yaratilmasa, sababini foydalanuvchiga
# ko'rsatish uchun eslab qolinadi (ilgari "Fayl yaratishning iloji bo'lmadi" degan umumiy
# xabar sababini yashirib yuborardi).
FILE_TOOLS = {
    "export_excel", "export_word", "export_pdf", "export_csv",
    "generate_html_report",
    "generate_company_report", "generate_sales_report",
    "generate_invoice_report", "generate_task_report",
}


def _collect_report_files(raw: str, found: dict[str, FileOut]) -> None:
    """Tool natijasidagi /reports/ havolalarini diskdagi fayllarga bog'laydi."""
    if not raw:
        return
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    for m in re.finditer(re.escape(base) + r"/reports/([A-Za-z0-9_.\-]+)", raw):
        fname = m.group(1)
        if fname in found:
            continue
        path = REPORTS_DIR / fname
        if not path.exists():
            continue
        kind = path.suffix.lstrip(".").lower() or "file"
        found[fname] = FileOut(kind=kind, filename=fname, url=m.group(0), path=str(path))

# ─── Base tools (all roles) ────────────────────────────────────────────────

BASE_TOOLS = [
    {
        "name": "odoo_search_read",
        "description": "Odoo'dan yozuvlarni o'qish",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "domain": {"type": "array"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["model", "domain", "fields"],
        },
    },
    {
        "name": "odoo_read_group",
        "description": "Guruhlab hisoblash",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "domain": {"type": "array"},
                "group_by": {"type": "array", "items": {"type": "string"}},
                "fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["model", "domain", "group_by", "fields"],
        },
    },
    {
        "name": "odoo_count",
        "description": "Yozuvlar sonini qaytaradi",
        "input_schema": {
            "type": "object",
            "properties": {"model": {"type": "string"}, "domain": {"type": "array"}},
            "required": ["model", "domain"],
        },
    },
    {
        "name": "check_odoo_connection",
        "description": "Odoo serveri bilan aloqani jonli tekshiradi: aloqa bor-yo'qligi, server manzili, "
                       "yo'q bo'lsa aniq sababi va tavsiya. Foydalanuvchi 'nega aloqa yo'q', 'server "
                       "ishlayaptimi', 'Odoo'ga ulanganmisan' deb so'rasa shu toolni chaqiring.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _get_tools_for_role(role: Role) -> list[dict]:
    """Rolga mos tool schema larni qaytaradi."""
    allowed = ROLE_TOOLS.get(role, [])
    tools = list(BASE_TOOLS)
    for tool_name in allowed:
        if tool_name in TOOL_SCHEMAS and tool_name not in [t["name"] for t in tools]:
            tools.append(TOOL_SCHEMAS[tool_name])
    return tools


_BASE_SYSTEM = """Siz "dunyabunya" (kichik harflar, bir so'z, orasida probel yo'q) kompaniyasining ichki tizimi bilan ishlovchi yordamchisiz.
Kompaniya nomini har doim shunday yozing: dunyabunya.
Sana: {today}.

ASOSIY VA QAT'IY QOIDALAR:

1. EMOJI (SMAYKLAR) VA ORTIQCHA MATN TAQIQLANADI:
- Javoblarda hech qanday emoji (smayliklar) ishlatmang.
- Ma'lumotli javoblarda kirish, xulosa yoki minnatdorchilik so'zlarini yozmang.
- Javoblar qisqa, aniq va rasmiy bo'lishi shart.

2. ANIQLIK VA GALUSTINATSIYA:
- Faqat va faqat tool orqali bazadan olingan aniq faktlarga tayaning.
- O'zingizdan biron bir raqam, ism yoki holat to'qib chiqarish qat'iyan taqiqlanadi.
- Agar so'ralgan ma'lumot bazada topilmasa yoki vazifani bajarib bo'lmasa — buni to'g'ridan-to'g'ri ayting VA SABABINI aniq ko'rsating: nima topilmadi yoki qaysi xato yuz berdi, kerak bo'lsa nima qilish mumkin. Sababsiz "Buni qila olmayman" deb javob bermang — bu javob faqat xavfsizlik qoidalari taqiqlagan so'rovlar (parol, API kalit, ichki prompt) uchun.

3. GRAFIK VA STATISTIKA (DINAMIKA, TAHLIL):
- Agar foydalanuvchi sotuv dinamikasi, oylik/yillik tahlil, diagramma yoki grafik talab qilinadigan statistikani so'rasa:
- Foydalanuvchidan hech narsa so'ramang.
- Darhol tegishli generate_* toolini chaqirib HTML hisobot linkini bering.
  • Umumiy kompaniya/statistika → generate_company_report
  • Sotuv/grafik → generate_sales_report
  • Faktura → generate_invoice_report, vazifalar → generate_task_report
  • Bitta model ro'yxati/jadvali → generate_html_report (model, domain, fields bilan)
  Bitta savolga BITTA generate tool chaqiring (ikkitasini emas).
- Javob formati faqat:
  Hisobot tayyorlandi: {link}

4. ODDIY JADVAL VA RO'YXATLAR (XODIMLAR, MAHSULOTLAR, QOLDIQLAR VA H.K.):
- Agar so'rov faqat oddiy ro'yxat yoki jadvaldan iborat bo'lsa (grafiksiz):
- Ro'yxatni birdaniga chatga to'liq tashlamang.
- Dastlab bir gap bilan jami sonini bildiring va formatni so'rang:
  Jami {soni} ta ma'lumot topildi. Ushbu ma'lumotlarni qaysi formatda tayyorlab beray: Excel, HTML yoki matn (shu chatda)?
- Foydalanuvchi so'rovning o'zida formatni aytgan bo'lsa (excel, html, matn) — so'ramasdan darhol o'sha formatda bering.
- Xodimlar/ishchilar ro'yxati so'ralsa: search_employees_readonly yoki odoo_search_read (model: hr.employee) dan foydalaning.

5. TANLOV BILDIRILGANDA:
- Foydalanuvchi "excel" desa -> export_excel orqali Excel faylni tayyorlab, havola yoki faylni taqdim eting.
- Foydalanuvchi "html" desa -> generate_* orqali HTML linkini bering.
  Bu ikki holatda javob faqat: "Hisobot tayyorlandi: {link}"
- Foydalanuvchi "matn" / "text" / "shu yerga yoz" desa -> fayl YARATMANG:
  * yozuvlar ro'yxati bo'lsa — export_text toolini chaqiring (to'liq ro'yxatni bot o'zi chatga yuboradi; siz ro'yxatni qayta yozmang, faqat bir jumla yozing);
  * hisoblangan ko'rsatkichlar/statistika bo'lsa — raqamlarni tool orqali olib, barchasini to'liq matn bilan yozing.

6. HISOBOT VIZUALIZATSIYASI (GRAFIK QOIDALARI — QAT'IY):
- Grafik (bar-chart/diagramma) FAQAT quyidagi shartlardan biri bajarilganda chiziladi:
  * Ko'rsatkich vaqt bo'yicha dinamikani bildirsa (oylik sotuv, haftalik hajm) va kamida 2-3 ta davr qiymati mavjud bo'lsa;
  * Yoki kamida 2 ta haqiqiy (nol bo'lmagan) toifa o'rtasida taqqoslash bo'lsa.
- Bo'sh, yagona qiymatli yoki barchasi 0 ga teng ko'rsatkichlar uchun grafik CHIZILMAYDI —
  o'rniga KPI metrika kartochkasi (katta son + ostida nom) yoki ixcham jadval ko'rsatiladi.
  Bo'sh o'qli (0, 0.2, 0.4) grafik gridlari hech qachon ko'rsatilmaydi.
- Moliyaviy katta sonlar ixcham yoziladi: 120 mlrd, 20 mlrd, 500 mln. To'liq uzun
  ko'rinish (masalan "120,000,000,000") yozilmaydi.

7. ODDIY SUHBAT (MA'LUMOT SO'RALMAGAN XABARLAR):
- Salomlashish, minnatdorchilik, "ishlayapsanmi?", "nima qila olasan?" kabi oddiy xabarlarga tool CHAQIRMASDAN bir-ikki jumla bilan xushmuomala javob bering (salomga — salom).
- Bunday xabarlarga "Buni qila olmayman" yoki "Ma'lumot topilmadi" deb javob bermang.
- Xabar mazmuni tushunarsiz bo'lsa — nimani nazarda tutganini qisqa so'rang.

8. ODOO SERVERI BILAN ALOQA:
- Tool natijasida "odoo_connection" xatosi kelsa — Odoo bilan aloqa yo'qligini, tool qaytargan sababni ("error") va tavsiyani ("tavsiya") TO'LIQ, o'zgartirmasdan yozing. Sababni "timeout" yoki "texnik nosozlik" deb umumlashtirmang.
- Foydalanuvchi "nega aloqa yo'q?", "server ishlayaptimi?", "Odoo'ga ulanganmisan?" kabi savol bersa — check_odoo_connection toolini chaqiring va natijadagi barcha ma'lumotni (holat, server manzili, sabab, tavsiya) to'liq yozing. Bu ma'lumot maxfiy EMAS.
- Parol va API kalit hech qachon ko'rsatilmaydi.

TEXNIK MA'LUMOT (TOOL CHAQIRISH UCHUN):
MAVJUD MODELLAR VA FIELDLAR:
- project.task: id, name, project_id, user_ids, stage_id, date_deadline, create_date, description, priority, state
- project.project: id, name, user_id, stage_id, date_start, date, state
- sale.order: id, name, partner_id, date_order, amount_total, state, user_id, order_line
- sale.order.line: id, order_id, product_id, name, price_unit, product_uom_qty, price_subtotal
- res.partner: id, name, email, phone, category_id
- res.users: id, name, login, email, groups_id
- account.move: id, name, invoice_date, amount_total, partner_id, move_type, state, amount_residual
- account.payment: id, name, date, amount, currency_id, partner_id, payment_type, state
- product.product: id, name, default_code, list_price, qty_available, categ_id, type, is_storable
- stock.quant: id, product_id, location_id, quantity, reserved_quantity
- stock.picking: id, name, location_id, location_dest_id, state
- crm.lead: id, name, partner_name, stage_id, expected_revenue, create_date, user_id
- hr.employee: id, name, job_id, department_id, work_email, work_phone, mobile_phone
- account.currency.exchange (valyuta ayirboshlash): id, date, partner_id, payment_type ('receive' — kirim, 'send' — chiqim), to_currency_id, amount, manual_rate, system_rate, state ('draft', 'posted')
- Kassa chiqimlari alohida hujjat — ular uchun search_kassa_chiqim toolidan foydalaning (account.move'dan qidirmang).

HOLAT (state) QIYMATLARI — domain'da FAQAT shulardan foydalan, boshqa field o'ylab topma:
- project.task.state: '01_in_progress', '02_changes_requested', '03_approved',
  '04_waiting_normal', '1_done' (bajarilgan), '1_canceled' (bekor qilingan).
  OCHIQ vazifa = [["state","not in",["1_done","1_canceled"]]]. `is_closed`, `stage_id.is_closed`,
  `stage_id.fold` kabi fieldlar YO'Q — ishlatma.
- sale.order.state: 'draft' (taklif), 'sent', 'sale' (tasdiqlangan), 'cancel'.
- account.move.state: 'draft', 'posted', 'cancel'; move_type: 'out_invoice' (mijozga hisob),
  'in_invoice' (ta'minotchi hisobi), 'out_refund', 'in_refund', 'entry'.
- account.payment.payment_type: 'inbound' (kirim), 'outbound' (chiqim).
- account.payment.state: 'draft', 'in_process' (jarayonda), 'paid' (to'langan), 'canceled',
  'rejected'. 'posted' qiymati YO'Q — o'tkazilgan to'lovlar = [["state","in",["in_process","paid"]]].
- product.product.type: 'consu' (tovar), 'service' (xizmat), 'combo'. 'product' qiymati YO'Q —
  omborda hisobga olinadigan tovar = [["is_storable","=",true]].
- stock.picking.state: 'draft', 'waiting', 'confirmed', 'assigned', 'done', 'cancel'.
Tool xato qaytarsa (masalan "Invalid field") — xato natijani javob sifatida ishlatma,
domain'ni tuzatib qayta chaqir.

VALYUTA VA SANA:
- Hujjatlar so'm (UZS) va dollar (USD) da yuritiladi. Summani har doim valyutasi bilan yozing;
  turli valyutadagi summalarni bir-biriga QO'SHMANG — currency_id bo'yicha alohida ko'rsating.
- account.move: amount_total/amount_residual — hujjat valyutasida; amount_total_signed va
  amount_residual_signed — kompaniya valyutasida (USD). Bularni aralashtirmang.
- Sana-vaqt maydonlari (date_order, create_date) bazada UTC'da; Toshkent vaqti = UTC+5.

QOIDALAR:
1. Ma'lumot so'ralganda har doim avval tegishli toolni chaqir, ma'lumotni olgach javob yoz.
2. Tool natijasini qayta sanab o'tmang — qisqa xulosa bering.
3. Tool natijasida "ogohlantirish" bo'lsa (natija qisqartirilgan yoki limitga yetgan) — ko'rsatilgan
   qatorlar sonini JAMI son deb aytmang; jami sonni odoo_count, yig'indini odoo_read_group bilan oling.
4. Sizning rolingiz: {role_name}
{role_prompt}
"""


# Barcha rollarga beriladigan asosiy tool'lar (ROLE_TOOLS ro'yxatidan qat'iy nazar)
_BASE_TOOL_NAMES = {t["name"] for t in BASE_TOOLS}

# Model javobiga sig'adigan tool natijasi hajmi (belgilar)
_TOOL_RESULT_MAX_CHARS = 8000
_SEARCH_READ_MAX_LIMIT = 500


def _role_denial(name: str, args: dict, role: Role) -> str | None:
    """Tool chaqiruvi rolga ruxsat etilmagan bo'lsa — rad etish sababi, aks holda None.

    Ikki tekshiruv (ikkalasi ham texnik nazorat, promptdagi ko'rsatma emas):
    - tool rolning ro'yxatida bo'lishi shart (model ro'yxatda yo'q tool nomini "o'ylab
      topsa" ham bajarilmaydi);
    - `model` parametrini erkin qabul qiladigan HAR QANDAY tool (odoo_search_read,
      export_excel, generate_html_report...) uchun model rolga ruxsat etilgan bo'lishi
      shart. Ilgari faqat 4 ta tool tekshirilardi — HR menejeri
      export_excel(model="account.move") orqali Moliya ma'lumotini olib chiqa olardi."""
    if name not in _BASE_TOOL_NAMES and name not in ROLE_TOOLS.get(role, []):
        log.warning("tool %s: role=%s uchun ruxsat etilmagan — rad etildi", name, role.value)
        return f"'{name}' amali sizning rolingizga ruxsat etilmagan."
    allowed = ROLE_ALLOWED_MODELS.get(role, set())
    if allowed is not None and "model" in args:
        model = args.get("model") or ""
        if model not in allowed:
            log.warning("tool %s: role=%s model=%r ruxsat etilmagan — rad etildi", name, role.value, model)
            return f"Sizning rolingiz '{model}' modeliga kira olmaydi."
    return None


def _serialize_tool_result(result) -> str:
    """Tool natijasini model uchun JSON qiladi; sig'masa — BUTUN qatorlar bilan qisqartiradi.

    Ilgari matn 8000-belgida shunchaki kesilardi: model yarmi yo'q ro'yxatni ko'rib, bundan
    bexabar holda sanab/jamlab noto'g'ri javob berishi mumkin edi. Endi nechta qator
    ko'rsatilgani va qolganini qanday olish aniq aytiladi."""
    raw = json.dumps(result, ensure_ascii=False, default=str)
    if len(raw) <= _TOOL_RESULT_MAX_CHARS:
        return raw
    if isinstance(result, list):
        kept, size = [], 0
        for row in result:
            piece = json.dumps(row, ensure_ascii=False, default=str)
            if size + len(piece) + 2 > _TOOL_RESULT_MAX_CHARS - 600:
                break
            kept.append(row)
            size += len(piece) + 2
        return json.dumps({
            "qatorlar": kept,
            "korsatilgan": len(kept),
            "jami_qaytgan": len(result),
            "ogohlantirish": (
                f"Natija uzun bo'lgani uchun {len(result)} ta qatordan faqat dastlabki {len(kept)} tasi "
                "ko'rsatildi. Ko'rinmagan qatorlar haqida taxmin qilmang: sanash uchun odoo_count, "
                "jamlash uchun odoo_read_group, to'liq ro'yxat uchun export_excel yoki export_text ishlating."
            ),
        }, ensure_ascii=False, default=str)
    return raw[:_TOOL_RESULT_MAX_CHARS] + "\n... [natija juda uzun — shu yerda kesildi, qolgani ko'rinmaydi]"


def _odoo_connection_failure(raw: str) -> dict | None:
    """_exec_tool natijasi Odoo bilan ALOQA xatosi bo'lsa — o'sha xato dict'i, aks holda None."""
    if '"odoo_connection"' not in raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("odoo_connection") else None


def _odoo_unreachable_text(failure: dict) -> str:
    """Odoo bilan aloqa yo'qligi haqida foydalanuvchiga to'liq xabar: sabab, server, tavsiya."""
    header = ("Odoo serveri javob beryapti, lekin tizimga kirib bo'lmadi — so'rov bajarilmadi."
              if failure.get("odoo_connection") == "auth" else
              "Odoo serveri bilan aloqa yo'q — so'rov bajarilmadi.")
    return (
        f"⚠️ {header}\n\n"
        f"Sabab: {failure.get('error')}.\n"
        f"Server: {_odoo.host} (baza: {_odoo.db})\n"
        f"Nima qilish kerak: {failure.get('tavsiya')}"
    )


def _collect_text_exports(raw: str, found: list[str]) -> None:
    """export_text natijasidagi tayyor matnni (token orqali) olib, yuborish ro'yxatiga qo'shadi."""
    if '"text_export"' not in raw:
        return
    try:
        parsed = json.loads(raw)
    except Exception:
        return
    if isinstance(parsed, dict) and parsed.get("text_export"):
        text = pop_text_export(str(parsed["text_export"]))
        if text:
            found.append(text)


async def _exec_tool(name: str, args: dict, role: Role) -> str:
    """Tool ni bajarish — asosiy yoki rolga xos.

    Bajarishdan oldin `_role_denial` rol ruxsatini tekshiradi: role prompt boshqa
    bo'lim ma'lumotini "taqiqlaydi", lekin bu LLM uchun ko'rsatma, texnik cheklov emas."""
    try:
        denial = _role_denial(name, args, role)
        if denial:
            return json.dumps({"error": denial}, ensure_ascii=False)
        if name == "odoo_search_read":
            limit = max(1, min(int(args.get("limit") or 50), _SEARCH_READ_MAX_LIMIT))
            r = await _odoo.execute_kw(
                args["model"], "search_read",
                [args["domain"]],
                {"fields": args["fields"], "limit": limit},
            )
            if isinstance(r, list) and len(r) >= limit:
                # Limitga yetgan natija — JAMI son emas: model buni bilmasa "50 ta xodim bor" deb yuboradi
                r = {"qatorlar": r, "korsatilgan": len(r),
                     "ogohlantirish": f"Limit ({limit}) to'ldi — bazada bundan ko'p yozuv bo'lishi mumkin. "
                                      "Jami sonni odoo_count bilan aniqlang."}
        elif name == "odoo_read_group":
            group_by = args["group_by"]
            r = await _odoo.execute_kw(
                args["model"], "read_group",
                [args["domain"], args["fields"], group_by],
                # Bir nechta guruh so'ralsa lazy=False shart — aks holda Odoo faqat birinchisi bo'yicha guruhlaydi
                {"lazy": False} if isinstance(group_by, list) and len(group_by) > 1 else {},
            )
        elif name == "odoo_count":
            r = await _odoo.execute_kw(args["model"], "search_count", [args["domain"]])
        elif name == "check_odoo_connection":
            r = await _odoo.diagnose()
        elif name in ALL_TOOLS:
            func = ALL_TOOLS[name]
            r = await func(**args)
        else:
            r = "Noma'lum tool"

        log.info("tool %s ok: %s", name, str(r)[:100])
        return _serialize_tool_result(r)
    except OdooError as e:
        if not e.kind:
            # Odoo so'rovni qabul qilib rad etgan (noto'g'ri field/domain...) — model tuzatib qayta chaqiradi
            log.exception("tool %s error", name)
            return json.dumps({"error": str(e)[:300]})
        # Odoo'ga yetib bo'lmadi/kirib bo'lmadi — aniq sabab va tavsiya bilan qaytariladi
        log.warning("tool %s: Odoo bilan aloqa yo'q (%s) — %s", name, e.kind, e)
        return json.dumps({"error": str(e)[:400], "odoo_connection": e.kind,
                           "tavsiya": connection_hint(e.kind)}, ensure_ascii=False)
    except Exception as e:
        log.exception("tool %s error", name)
        return json.dumps({"error": str(e)[:300]})


async def ask_with_files(telegram_user_id: int, question: str, role: Role = Role.CEO,
                         group_chat_id: int | None = None,
                         instruction: str = "",
                         force_tools: list[str] | None = None,
                         tool_choice: dict | None = None,
                         exclude_tools: set[str] | None = None,
                         on_tool_start: Callable[[], Awaitable[None]] | None = None) -> AssistantResult:
    """Savolga javob berish — rolga mos tool'lar bilan.

    Matn javob bilan birga, agar tool'lar fayl (xlsx/html) yaratgan bo'lsa,
    ularning ro'yxatini ham qaytaradi — chaqiruvchi Telegram hujjat sifatida yuboradi.
    Foydalanuvchi matni <user_input> belgilari ichida yuboriladi (prompt-injection
    himoyasi); `instruction` esa tizim tomonidan qo'shilgan ko'rsatma sifatida
    system prompt'ga qo'shiladi.

    `force_tools`/`tool_choice`: berilsa, model shu tool'lardan birini albatta
    chaqirishga MAJBUR qilinadi (Anthropic `tool_choice`) — foydalanuvchi Excel/HTML
    tugmasini bosgach qayta so'ralganda ishlatiladi, aks holda model tool chaqirmasdan
    faqat matn bilan javob berib, fayl umuman yaratilmasligi mumkin edi.

    `exclude_tools`: shu tool'lar modelga umuman berilmaydi ("Matn" tugmasi — fayl
    yaratuvchi tool'larsiz).

    `on_tool_start`: model BIRINCHI marta tool so'raganda (ya'ni haqiqatan Odoo'ga
    murojaat boshlanganda) bir marta chaqiriladi — chaqiruvchi "so'rovingiz
    tayyorlanmoqda" xabarini shu yerda yuboradi. Oddiy suhbatda (model tool'siz
    darhol javob bersa) umuman chaqirilmaydi.
    """
    found_files: dict[str, FileOut] = {}
    found_texts: list[str] = []
    odoo_failure: dict | None = None
    tool_started = False
    async with SessionLocal() as session:
        history = await repo.get_recent_conversation(session, telegram_user_id)

    messages = [{"role": m.role, "content": m.content} for m in history[-6:] if m.content]
    # Anthropic API suhbat "user" xabaridan boshlanishini talab qiladi — tarix kesimi
    # "assistant"dan boshlanib qolsa (juftlik buzilgan bo'lsa) butun so'rov 400 bilan yiqilardi
    while messages and messages[0]["role"] != "user":
        messages.pop(0)

    if group_chat_id:
        try:
            async with SessionLocal() as session:
                group_msgs = await repo.get_group_messages_last_24h(session, group_chat_id, limit=30)
            if group_msgs:
                context_lines = [f"- [{m.from_user_name}] {m.text[:200]}" for m in group_msgs if m.text]
                context = "\n".join(context_lines[-20:])
                # Guruh konteksti ham chegaralangan blokda — faqat ma'lumot sifatida
                messages.insert(0, {"role": "user", "content":
                                    "<group_context>\n[Guruh konteksti — oxirgi 24 soat]:\n"
                                    + context + "\n</group_context>"})
        except Exception:
            pass

    # Foydalanuvchi matni aniq chegaralar ichida — undagi buyruqlar tizim ko'rsatmasi emas
    messages.append({"role": "user", "content": wrap_user_input(question)})

    role_prompt = ROLE_PROMPTS.get(role, "")
    role_name = ROLE_NAMES.get(role, "Noma'lum")

    system = _BASE_SYSTEM.replace("{today}", date.today().isoformat())
    system = system.replace("{role_name}", role_name)
    system = system.replace("{role_prompt}", role_prompt)
    # Xavfsizlik/fakt bloki eng yuqori ustuvorlikda — prompt boshida turadi
    system = SECURITY_FRAGMENT + "\n\n" + system
    if instruction:
        # Tizim qo'shgan ko'rsatma (masalan format tanlovi) — user_input ichida EMAS
        system += "\n\nQO'SHIMCHA TIZIM KO'RSATMACHI (bajarilishi shart):\n" + instruction

    tools = _get_tools_for_role(role)
    if exclude_tools:
        tools = [t for t in tools if t["name"] not in exclude_tools]
    if force_tools:
        narrowed = [t for t in tools if t["name"] in force_tools]
        # Agar rol shu tool'larning birortasiga ruxsatli bo'lmasa (bo'sh qolsa) —
        # to'liq ro'yxatga qaytamiz, aks holda Anthropic bo'sh tools=[] ni rad etadi
        tools = narrowed or tools
    answer = ""
    file_tool_results: list[tuple[str, str]] = []  # (tool_name, raw_result) — xato diagnostikasi uchun

    for i in range(8):
        create_kwargs = dict(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages,
        )
        # tool_choice faqat BIRINCHI iteratsiyada majburlanadi — shundan keyin ham
        # majburlansa, Claude tool natijasini ko'rib matn bilan yakunlay olmay,
        # tool'ni cheksiz qayta chaqirishga majbur bo'lib qolardi
        if tool_choice and i == 0:
            create_kwargs["tool_choice"] = tool_choice
        resp = await _client.messages.create(**create_kwargs)
        log.info("iter %d: stop=%s blocks=%s role=%s", i, resp.stop_reason,
                 [b.type for b in resp.content], role.value)

        text_parts = [b.text for b in resp.content if b.type == "text"]
        if text_parts:
            answer = "".join(text_parts)

        # Claude bir nechta tool so'rasa ularni PARALLEL bajarish — javob tezlashadi
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        # tool so'ralmagan har qanday to'xtash (end_turn, max_tokens, refusal...) — yakun.
        # Aks holda bo'sh tool_result ro'yxati bilan user xabari yuborilib, API 400 qaytarardi.
        if resp.stop_reason != "tool_use" or not tool_uses:
            if resp.stop_reason == "max_tokens":
                log.warning("ask_with_files: javob max_tokens chegarasida kesildi")
            break

        log.info("iter %d: tools=%s", i, [b.name for b in tool_uses])
        # Aloqa tekshiruvi — bir soniyalik diagnostika, ma'lumot so'rovi emas ("ishlayapsanmi?")
        if (on_tool_start is not None and not tool_started
                and any(b.name != "check_odoo_connection" for b in tool_uses)):
            tool_started = True
            try:
                await on_tool_start()
            except Exception:
                log.warning("ask_with_files: on_tool_start xatosi", exc_info=True)

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        executed = await asyncio.gather(
            *[_exec_tool(b.name, b.input, role) for b in tool_uses]
        )
        for b, r in zip(tool_uses, executed):
            _collect_report_files(r, found_files)
            _collect_text_exports(r, found_texts)
            if b.name in FILE_TOOLS:
                file_tool_results.append((b.name, r))
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": r})

        # Barcha so'rovlar Odoo bilan aloqa yo'qligi sababli yiqilgan bo'lsa — modelni
        # qayta chaqirish foydasiz (u sababni "timeout" deb qisqartirar yoki "Buni qila
        # olmayman" deb yuborardi): aniq sabab foydalanuvchiga to'g'ridan-to'g'ri beriladi.
        failures = [f for f in map(_odoo_connection_failure, executed) if f]
        if failures and len(failures) == len(executed):
            odoo_failure = failures[0]
            answer = _odoo_unreachable_text(odoo_failure)
            break
        messages.append({"role": "user", "content": results})

    if not answer:
        answer = "Kechirasiz, ma'lumot topilmadi."

    # Model matnida ham fayl havolasi bo'lishi mumkin — tekshirib olamiz
    _collect_report_files(answer, found_files)

    # Fayl kutilgan-u topilmagan bo'lsa — sababini oxirgi fayl-tool natijasidan chiqarib olamiz
    # (masalan "Excel kutubxonasi yo'q: ...", "Bu so'rov bo'yicha ma'lumot topilmadi.",
    # yoki _exec_tool tomonidan qaytarilgan {"error": "..."})
    file_error: str | None = None
    if not found_files and file_tool_results:
        last_name, last_raw = file_tool_results[-1]
        try:
            parsed = json.loads(last_raw)
        except Exception:
            parsed = last_raw
        if isinstance(parsed, dict) and "error" in parsed:
            file_error = f"{last_name}: {str(parsed['error'])[:300]}"
        elif isinstance(parsed, str) and not parsed.startswith("http"):
            file_error = f"{last_name}: {parsed[:300]}"
        if file_error:
            log.warning("ask_with_files: fayl kutilgan-u yaratilmadi — %s", file_error)

    async with SessionLocal() as session:
        await repo.add_conversation_message(session, telegram_user_id, "user", question)
        await repo.add_conversation_message(session, telegram_user_id, "assistant", answer)
    return AssistantResult(
        text=answer, files=list(found_files.values()), file_error=file_error, texts=found_texts,
        odoo_error=odoo_failure["odoo_connection"] if odoo_failure else None,
    )


async def ask(telegram_user_id: int, question: str, role: Role = Role.CEO,
              group_chat_id: int | None = None) -> str:
    """Faqat matn qaytaruvchi soddalashtirilgan API (eskilar uchun mos)."""
    result = await ask_with_files(telegram_user_id, question, role=role, group_chat_id=group_chat_id)
    return result.text
