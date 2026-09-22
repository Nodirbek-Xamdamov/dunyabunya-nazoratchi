"""CEO assistant — role-based tool routing with RBAC."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from anthropic import AsyncAnthropic

from app.config import settings
from app.db import repo
from app.db.session import SessionLocal
from app.rbac import Role, ROLE_TOOLS, ROLE_PROMPTS, ROLE_NAMES, ROLE_ALLOWED_MODELS
from app.services.odoo import OdooClient
from app.services.odoo_tools import ALL_TOOLS, TOOL_SCHEMAS
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


# Fayl/hisobot yaratuvchi tool'lar — shu nomlardan biri xato yoki "topilmadi" turidagi
# matn qaytarsa va oxir-oqibat hech qanday fayl yaratilmasa, sababini foydalanuvchiga
# ko'rsatish uchun eslab qolinadi (ilgari "Fayl yaratishning iloji bo'lmadi" degan umumiy
# xabar sababini yashirib yuborardi).
_FILE_TOOLS = {
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
- Kirish, xulosa, salomlashish yoki minnatdorchilik so'zlarini yozmang.
- Javoblar qisqa, aniq va rasmiy bo'lishi shart.

2. ANIQLIK VA GALUSTINATSIYA:
- Faqat va faqat tool orqali bazadan olingan aniq faktlarga tayaning.
- O'zingizdan biron bir raqam, ism yoki holat to'qib chiqarish qat'iyan taqiqlanadi.
- Agar so'ralgan ma'lumot bazada topilmasa, mavjud bo'lmasa yoki vazifani bajarish imkoni bo'lmasa — hech qanday bahona va tushuntirishlarsiz to'g'ridan-to'g'ri ayting: "Buni qila olmayman" yoki "Ma'lumot topilmadi". Sababini tushuntirib o'tirish taqiqlanadi.

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
  Jami {soni} ta ma'lumot topildi. Ushbu ma'lumotlarni qaysi formatda tayyorlab beray: Excel yoki HTML?
- Xodimlar/ishchilar ro'yxati so'ralsa: search_employees_readonly yoki odoo_search_read (model: hr.employee) dan foydalaning.

5. TANLOV BILDIRILGANDA:
- Foydalanuvchi "excel" desa -> generate_* orqali Excel faylni tayyorlab, havola yoki faylni taqdim eting.
- Foydalanuvchi "html" desa -> generate_* orqali HTML linkini bering.
- Javob faqat: "Hisobot tayyorlandi: {link}"

6. HISOBOT VIZUALIZATSIYASI (GRAFIK QOIDALARI — QAT'IY):
- Grafik (bar-chart/diagramma) FAQAT quyidagi shartlardan biri bajarilganda chiziladi:
  * Ko'rsatkich vaqt bo'yicha dinamikani bildirsa (oylik sotuv, haftalik hajm) va kamida 2-3 ta davr qiymati mavjud bo'lsa;
  * Yoki kamida 2 ta haqiqiy (nol bo'lmagan) toifa o'rtasida taqqoslash bo'lsa.
- Bo'sh, yagona qiymatli yoki barchasi 0 ga teng ko'rsatkichlar uchun grafik CHIZILMAYDI —
  o'rniga KPI metrika kartochkasi (katta son + ostida nom) yoki ixcham jadval ko'rsatiladi.
  Bo'sh o'qli (0, 0.2, 0.4) grafik gridlari hech qachon ko'rsatilmaydi.
- Moliyaviy katta sonlar ixcham yoziladi: 120 mlrd, 20 mlrd, 500 mln. To'liq uzun
  ko'rinish (masalan "120,000,000,000") yozilmaydi.

TEXNIK MA'LUMOT (TOOL CHAQIRISH UCHUN):
MAVJUD MODELLAR VA FIELDLAR:
- project.task: id, name, project_id, user_ids, stage_id, date_deadline, create_date, description, priority, state
- project.project: id, name, user_id, stage_id, date_start, date, state
- sale.order: id, name, partner_id, date_order, amount_total, state, user_id, order_line
- sale.order.line: id, order_id, product_id, name, price_unit, product_uom_qty, price_subtotal
- res.partner: id, name, email, phone, category_id
- res.users: id, name, login, email, groups_id
- account.move: id, name, invoice_date, amount_total, partner_id, move_type, state, amount_residual
- account.payment: id, name, date, amount, partner_id, payment_type, state
- product.product: id, name, list_price, qty_available, categ_id, type
- stock.quant: id, product_id, location_id, quantity, reserved_quantity
- stock.picking: id, name, location_id, location_dest_id, state
- crm.lead: id, name, partner_name, stage_id, create_date, user_id
- hr.employee: id, name, job_id, department_id, work_email

QOIDALAR:
1. Har doim avval tegishli toolni chaqir, ma'lumotni olgach javob yoz.
2. Tool natijasini qayta sanab o'tmang — qisqa xulosa bering.
3. Sizning rolingiz: {role_name}
{role_prompt}
"""


_BASE_MODEL_TOOLS = {"odoo_search_read", "odoo_read_group", "odoo_count"}


async def _exec_tool(name: str, args: dict, role: Role) -> str:
    """Tool ni bajarish — asosiy yoki rolga xos.

    odoo_search_read/odoo_read_group/odoo_count BASE_TOOLS sifatida BARCHA
    rollarga beriladi va `model` parametrini erkin qabul qiladi — shuning
    uchun bu yerda ROLE_ALLOWED_MODELS bo'yicha qayta tekshirilmasa, masalan
    Sotuv menejeri "model: account.move" deb so'rab Moliya ma'lumotlarini
    o'qib olishi mumkin bo'lardi (role prompt buni "taqiqlaydi", lekin bu
    LLM uchun ko'rsatma, texnik cheklov emas)."""
    try:
        if name in _BASE_MODEL_TOOLS:
            allowed = ROLE_ALLOWED_MODELS.get(role)
            model = args.get("model", "")
            if allowed is not None and model not in allowed:
                log.warning("tool %s: role=%s model=%r ruxsat etilmagan — rad etildi", name, role.value, model)
                return json.dumps({"error": f"Sizning rolingiz '{model}' modeliga kira olmaydi."})
        if name == "odoo_search_read":
            r = await _odoo.execute_kw(
                args["model"], "search_read",
                [args["domain"]],
                {"fields": args["fields"], "limit": args.get("limit", 50)},
            )
        elif name == "odoo_read_group":
            r = await _odoo.execute_kw(
                args["model"], "read_group",
                [args["domain"], args["fields"], args["group_by"]],
            )
        elif name == "odoo_count":
            r = await _odoo.execute_kw(args["model"], "search_count", [args["domain"]])
        elif name in ALL_TOOLS:
            func = ALL_TOOLS[name]
            r = await func(**args)
        else:
            r = "Noma'lum tool"

        log.info("tool %s ok: %s", name, str(r)[:100])
        return json.dumps(r, ensure_ascii=False, default=str)[:8000]
    except Exception as e:
        log.exception("tool %s error", name)
        return json.dumps({"error": str(e)[:300]})


async def ask_with_files(telegram_user_id: int, question: str, role: Role = Role.CEO,
                         group_chat_id: int | None = None,
                         instruction: str = "",
                         force_tools: list[str] | None = None,
                         tool_choice: dict | None = None) -> AssistantResult:
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
    """
    found_files: dict[str, FileOut] = {}
    async with SessionLocal() as session:
        history = await repo.get_recent_conversation(session, telegram_user_id)

    messages = [{"role": m.role, "content": m.content} for m in history[-6:]]

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
    if force_tools:
        narrowed = [t for t in tools if t["name"] in force_tools]
        # Agar rol shu tool'larning birortasiga ruxsatli bo'lmasa (bo'sh qolsa) —
        # to'liq ro'yxatga qaytamiz, aks holda Anthropic bo'sh tools=[] ni rad etadi
        tools = narrowed or tools
    answer = ""
    file_tool_results: list[tuple[str, str]] = []  # (tool_name, raw_result) — xato diagnostikasi uchun

    for i in range(5):
        create_kwargs = dict(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=800,
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

        if resp.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": resp.content})
        # Claude bir nechta tool so'rasa ularni PARALLEL bajarish — javob tezlashadi
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        results = []
        if tool_uses:
            executed = await asyncio.gather(
                *[_exec_tool(b.name, b.input, role) for b in tool_uses]
            )
            for b, r in zip(tool_uses, executed):
                _collect_report_files(r, found_files)
                if b.name in _FILE_TOOLS:
                    file_tool_results.append((b.name, r))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": r})
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
    return AssistantResult(text=answer, files=list(found_files.values()), file_error=file_error)


async def ask(telegram_user_id: int, question: str, role: Role = Role.CEO,
              group_chat_id: int | None = None) -> str:
    """Faqat matn qaytaruvchi soddalashtirilgan API (eskilar uchun mos)."""
    result = await ask_with_files(telegram_user_id, question, role=role, group_chat_id=group_chat_id)
    return result.text
