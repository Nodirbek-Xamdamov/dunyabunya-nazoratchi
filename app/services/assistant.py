"""CEO yordamchisi — agent sikli: model savolni o'ylab ko'radi, kerakli tool'larni chaqiradi,
natijani ko'rib davom etadi va oxirida javob yozadi. Tool'lar rolga qarab beriladi (RBAC).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import anthropic
from anthropic import AsyncAnthropic

from app import metrics
from app.config import settings
from app.db import repo
from app.db.session import SessionLocal
from app.prompt_guard import SECURITY_FRAGMENT, wrap_user_input
from app.rbac import ROLE_ALLOWED_MODELS, ROLE_NAMES, ROLE_PROMPTS, ROLE_TOOLS, Role
from app.services import agent_context, agent_memory, agent_tools, reminders, task_tools
from app.services import odoo_tools as _odoo_tools
from app.services.agent_tools import ToolInputError
from app.services.odoo import OdooError, connection_hint
from app.services.odoo_tools import ALL_TOOLS, TOOL_SCHEMAS, pop_text_export

log = logging.getLogger(__name__)

_extra_headers = {}
if settings.ANTHROPIC_WORKSPACE_ID:
    _extra_headers["anthropic-workspace-id"] = settings.ANTHROPIC_WORKSPACE_ID

_client = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    default_headers=_extra_headers or None,
    # Javob oqim (stream) bilan o'qiladi, shuning uchun 90 soniya — butun javobga emas, ikki
    # bo'lak orasidagi jimlikka qo'yilgan chegara: model o'ylayotgan yoki internetdan qidirayotgan
    # paytda ulanish uzilmaydi, osilib qolgan so'rov esa 90 soniyada xato bo'lib qaytadi.
    timeout=anthropic.Timeout(90.0, connect=10.0),
    max_retries=2,
)
# Barcha tool'lar bitta Odoo klientidan foydalanadi (umumiy ulanishlar va bitta cheklov)
_odoo = _odoo_tools._odoo

REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"
_TZ = ZoneInfo("Asia/Tashkent")
_WEEKDAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")

_MAX_TOKENS = 16000
_HISTORY_MESSAGES = 10      # suhbat xotirasi: oxirgi 5 ta savol-javob
_HISTORY_CHARS = 3000       # tarixdagi bitta xabar shundan uzun bo'lsa qisqartiriladi
# Model javobiga sig'adigan tool natijasi hajmi (belgilar)
_TOOL_RESULT_MAX_CHARS = 16000


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
    # Shu javob davomida yaratilgan vazifalar — chaqiruvchi ostiga "Odoo'da ochish / O'chirish" tugmasini qo'yadi
    task_ids: list[int] = field(default_factory=list)
    # Javob uchun kompaniya ma'lumoti o'qilganmi. Xat yozish, tarjima, eslatma kabi javoblar ostiga
    # "Excel / HTML / Matn" tugmalari qo'yilmaydi (savolda "ro'yxat" so'zi uchrasa ham).
    used_data: bool = True


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

# Bir zumda bajariladigan tool'lar — ular uchun "so'rovingiz tayyorlanmoqda" xabari chiqmaydi
_INSTANT_TOOLS = {"check_odoo_connection", "remember_note", "forget_note",
                  "set_reminder", "list_reminders", "cancel_reminder"}
# Kompaniya ma'lumotini ro'yxat qilib qaytarmaydigan tool'lar (amallar, xotira, guruh tarixi)
_NON_DATA_TOOLS = _INSTANT_TOOLS | {"create_task", "update_task", "add_task_comment", "get_group_history"}


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


# ─── Tool'lar ro'yxati ──────────────────────────────────────────────────────

async def _remember_note(text: str) -> dict:
    return agent_memory.remember(agent_context.actor().memory_owner, text)


async def _forget_note(note_id: int) -> dict:
    return agent_memory.forget(agent_context.actor().memory_owner, note_id)


_MEMORY_SCHEMAS = {
    "remember_note": {
        "name": "remember_note",
        "description": (
            "Foydalanuvchi keyingi suhbatlarda ham yodda turishini istagan fakt yoki afzallikni saqlaydi "
            "(\"eslab qol\", \"bundan keyin doim ...\", \"men ... desam ... ni nazarda tutaman\"). Qayd har "
            "so'rovda sizga ko'rsatiladi. Faqat foydalanuvchining o'zi so'raganda yoki doimiy afzallik "
            "aytganda chaqiring; parol, kalit va bir martalik ma'lumot saqlanmaydi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Qisqa, mustaqil tushunarli jumla (300 belgigacha)"}},
            "required": ["text"],
        },
    },
    "forget_note": {
        "name": "forget_note",
        "description": "Eslab qolingan qaydni o'chiradi (raqami qaydlar ro'yxatida # bilan ko'rsatilgan).",
        "input_schema": {
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
        },
    },
    "get_group_history": {
        "name": "get_group_history",
        "description": (
            "Rahbarning kompaniya guruhiga yozgan xabarlari: hali xulosalanmagan so'nggi xabarlar va oldingi "
            "kunlarning qisqa xulosalari. \"Kecha nima topshirgan edim?\", \"o'tgan hafta guruhda nima "
            "haqida gaplashdik?\" kabi savollar uchun. Odoo'dagi vazifalar uchun bu emas — project.task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Necha kun orqaga qaralsin (sukut 7)"},
                "query": {"type": "string", "description": "Ixtiyoriy kalit so'z — faqat shu uchragan yozuvlar"},
            },
        },
    },
}

_CONNECTION_SCHEMA = {
    "name": "check_odoo_connection",
    "description": "Odoo serveri bilan aloqani jonli tekshiradi: aloqa bor-yo'qligi, server manzili, "
                   "yo'q bo'lsa aniq sababi va tavsiya. Foydalanuvchi 'nega aloqa yo'q', 'server "
                   "ishlayaptimi', 'Odoo'ga ulanganmisan' deb so'rasa shu toolni chaqiring.",
    "input_schema": {"type": "object", "properties": {}},
}

# Barcha rollarga beriladigan tool'lar. Model nomini qabul qiladiganlari `_role_denial` orqali
# rolning ruxsat etilgan modellari bilan cheklanadi.
BASE_TOOLS = [
    agent_tools.SCHEMAS["odoo_search_read"],
    agent_tools.SCHEMAS["odoo_read_group"],
    agent_tools.SCHEMAS["odoo_count"],
    agent_tools.SCHEMAS["odoo_compare"],
    agent_tools.SCHEMAS["odoo_fields"],
    _CONNECTION_SCHEMA,
    _MEMORY_SCHEMAS["remember_note"],
    _MEMORY_SCHEMAS["forget_note"],
    reminders.SCHEMAS["set_reminder"],
    reminders.SCHEMAS["list_reminders"],
    reminders.SCHEMAS["cancel_reminder"],
]
_BASE_TOOL_NAMES = {t["name"] for t in BASE_TOOLS}

# Faqat rol ro'yxatida (rbac.ROLE_TOOLS) ko'rsatilgan rollarga beriladigan qo'shimcha tool'lar
_EXTRA_SCHEMAS = {
    "odoo_models": agent_tools.SCHEMAS["odoo_models"],
    "get_group_history": _MEMORY_SCHEMAS["get_group_history"],
    **task_tools.SCHEMAS,
}

# Yordamchining o'z tool'lari; tayyor hisobot tool'lari — odoo_tools.ALL_TOOLS da
_AGENT_TOOLS: dict[str, Callable] = {
    **agent_tools.TOOLS,
    **task_tools.TOOLS,
    **reminders.TOOLS,
    "remember_note": _remember_note,
    "forget_note": _forget_note,
    "get_group_history": agent_memory.group_history,
}


def _find_tool(name: str) -> Callable | None:
    return _AGENT_TOOLS.get(name) or ALL_TOOLS.get(name)

# `model` parametri bo'lmagan, lekin `domain` qabul qiladigan tool'lar qaysi modelni o'qishi
_FIXED_DOMAIN_MODELS = {"search_payments": "account.payment", "search_crm_leads": "crm.lead",
                        "search_kassa_chiqim": "x_kassa_chiqim"}

_WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 4},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3},
]

# Model yoki hisob biror imkoniyatni qo'llamasa (API 400 qaytarsa) — o'chirib, usiz davom etiladi
_features = {"thinking": True, "web": True}


def _get_tools_for_role(role: Role) -> list[dict]:
    """Rolga mos tool schema larni qaytaradi."""
    tools = list(BASE_TOOLS)
    names = set(_BASE_TOOL_NAMES)
    for tool_name in ROLE_TOOLS.get(role, []):
        schema = _EXTRA_SCHEMAS.get(tool_name) or TOOL_SCHEMAS.get(tool_name)
        if schema and tool_name not in names:
            tools.append(schema)
            names.add(tool_name)
    return tools


_BASE_SYSTEM = """Siz — "dunyabunya" kompaniyasi rahbariyatining shaxsiy AI yordamchisisiz: Telegram orqali ishlaydigan, kompaniyaning Odoo tizimiga ulangan agent. Kompaniya nomi har doim kichik harflar bilan, bir so'z qilib yoziladi: dunyabunya (sarlavha, xat va blankada ham).

# VAZIFANGIZ
Foydalanuvchi nima so'rasa, o'shani oxirigacha bajarish. Siz tayyor hisobotlar bilan cheklanmagansiz:
- kompaniya ma'lumotlari: sotuv, moliya, ombor, xarid, ishlab chiqarish, xodimlar, vazifalar va Odoo bazasidagi boshqa har qanday ma'lumot;
- tahlil va maslahat: solishtirish, o'zgarish sababini topish, xulosa va tavsiya;
- matn ishlari: xat, e'lon, tabrik, buyruq loyihasi yozish, tarjima, tahrir;
- hisob-kitob va umumiy bilim savollari;
- internetdagi dolzarb ma'lumot (valyuta kursi, yangilik, qonunchilik, bozor narxi) — web_search orqali;
- amallar: xodimga vazifa berish, vazifani yangilash, eslatma qo'yish, muhim narsani eslab qolish;
- yuborilgan rasm va hujjatlarni o'qish.

# ISH TARTIBI
1. So'rov nima haqida ekanini aniqlang va javobga olib boradigan eng qisqa yo'lni tanlang.
2. Kerakli ma'lumotni tool'lar bilan o'zingiz toping. O'zingiz aniqlay oladigan narsani foydalanuvchidan so'ramang.
3. Bir-biriga bog'liq bo'lmagan so'rovlarni bitta qadamda birga (parallel) chaqiring.
4. Tool xato qaytarsa — bu javob emas. Xato matnini o'qing, so'rovni tuzating yoki boshqa yo'l tanlang (boshqa maydon, boshqa model, odoo_fields, odoo_models). "Buni qila olmayman" — faqat mumkin bo'lgan yo'llar sinab ko'rilgach va aniq sababi bilan aytiladi.
5. So'rov ikki xil tushunilishi mumkin bo'lsa — eng ehtimolli ma'noni tanlab bajaring va javobda qaysi taxmin bilan hisoblaganingizni bir jumlada ayting. Aniqlashtiruvchi savolni faqat xato taxmin zarar keltiradigan holatda bering (masalan vazifa bir xil ismli ikki xodimdan qaysi biriga berilishi).
6. Yetarli ma'lumot to'plangach to'xtang va javob yozing. Javob yozishdan oldin u so'ralgan narsaning hammasini qamraganini tekshiring.

# FAKTLAR
- Kompaniyaga oid har bir raqam, ism, sana va holat faqat tool natijasidan olinadi. Raqam to'qilmaydi va taxmin qilinmaydi.
- Ma'lumot topilmasa: nima qidirilgani va nima topilmagani aniq aytiladi.
- Tahlil, sabab va tavsiya — sizning xulosangiz. Ularni raqamlarga tayanib yozing va faktdan ajratib ko'rsating ("Raqamlarga ko'ra ...", "Tavsiya: ...").
- Til, matematika, biznes amaliyoti kabi umumiy bilim savollariga o'z bilimingiz bilan javob bering. Bugungi kunga bog'liq tashqi ma'lumot kerak bo'lsa web_search chaqiring. Internetdan topilganini foydalanuvchi tilida, o'z so'zingiz bilan ravon yozing (manba matnini ko'chirmang) va manba nomini qavs ichida qisqa ko'rsating. Kompaniyaning ichki ma'lumotini internetdan qidirmang.
- Tool natijalarida katta sonlar o'qish oson bo'lishi uchun xonalari ajratib berilgan ("22 043 045.11" — yigirma ikki million). Sonni yozishdan oldin xonalarini sanang: ming, million va milliardni adashtirmang.

# JAVOB USLUBI
- Foydalanuvchi qaysi tilda yozsa, shu tilda javob bering. O'zbekcha javob lotin yozuvida bo'ladi (savol kirillda yozilgan bo'lsa ham); bitta so'z ichida lotin va kirill harflari aralashtirilmaydi.
- Birinchi jumla — javobning o'zi. Kirish so'zi, minnatdorchilik, "yana nima yordam beray?" kabi yakun yozilmaydi.
- Emoji ishlatilmaydi.
- Telefon ekraniga mos, qisqa va zich yozing. Jadval faqat 2-4 ustunli qisqa solishtirish uchun; boshqa hollarda oddiy qatorlar. Ajratuvchi chiziqlar (---) va ko'p pog'onali sarlavhalar kerak emas.
- Pul: katta summalar ixcham yoziladi (26,7 mlrd so'm; 1,24 mln so'm; 3 784 USD), har doim valyutasi bilan. Aniq raqam so'ralsa to'liq yozing.
- Salom, rahmat, "ishlayapsanmi?" kabi oddiy gaplarga tool chaqirmasdan bir-ikki jumla bilan xushmuomala javob bering.
- Javobga o'ylash jarayoni, o'z-o'zini tuzatish ("... yo'q, aslida ...") va tool nomlari yozilmaydi — faqat tekshirilgan, tayyor natija.

# RO'YXATLAR VA FAYLLAR
- 10 tagacha qatorli ro'yxat to'g'ridan-to'g'ri chatda yoziladi.
- Uzunroq ro'yxat chatga to'kilmaydi. Jami sonini ayting va so'rang: "Jami N ta yozuv topildi. Qaysi formatda tayyorlab beray: Excel, HTML yoki matn (shu chatda)?" Foydalanuvchi formatni oldindan aytgan bo'lsa — so'ramasdan o'sha formatda bering.
- Format taklifi faqat ro'yxatni bitta model va filtr bilan eksport qilish mumkin bo'lganda beriladi. Hisoblab chiqarilgan natija (reyting, odoo_compare solishtiruvi) uchun eng muhim 10-15 qatorni chatda yozing.
- excel → export_excel; html → generate_html_report yoki mos generate_*_report; word / pdf / csv → export_word / export_pdf / export_csv; "matn", "shu yerga yoz" → export_text (to'liq ro'yxatni bot o'zi yuboradi — siz qayta yozmang, bir jumla yozing).
- Grafik, dinamika, "statistika", "hisobot", "dashboard" so'ralsa so'ramasdan mos generate_* toolini chaqiring: kompaniya bo'yicha umumiy → generate_company_report; sotuv → generate_sales_report; fakturalar → generate_invoice_report; vazifalar → generate_task_report; bitta model jadvali → generate_html_report. Bitta so'rovga bitta hisobot.
- Fayl yaratilganda javob shunday: "Hisobot tayyorlandi: {tool qaytargan havola}" — faylni bot o'zi biriktiradi.
- Fayl faqat so'ralganda yoki uzun ro'yxat uchun yaratiladi. Tahlil savoliga fayl bilan emas, javob bilan javob bering.

# ODOO BILAN ISHLASH
Asosiy tool'lar: odoo_search_read (yozuvlar; saralash va limit bilan), odoo_read_group (yig'indi, reyting, taqsimot, dinamika), odoo_compare (ikki davr yoki to'plamni solishtirish), odoo_count, odoo_fields (model maydonlari), odoo_models (model qidirish). Tayyor tool'lar (get_*, check_*, find_*, search_*) savolga aynan mos kelsa, shularni ishlating — ular tezroq.

Qoidalar:
- "Eng katta / eng ko'p / oxirgi N ta" — odoo_search_read (order + limit) yoki odoo_read_group (order_by + limit). Ro'yxatning bir bo'lagini o'qib, ichidan "eng kattasini" tanlamang.
- Yig'indi va sonni qatorlarni o'zingiz qo'shib chiqarmang — odoo_read_group va odoo_count hisoblasin.
- Natijada "ogohlantirish" bo'lsa (qisqartirilgan yoki limit to'lgan) — ko'rsatilgan qatorlarni jami deb aytmang.
- "Nega o'zgardi?", "kim ketib qoldi?", "nima hisobiga o'sdi?" — odoo_compare (mijoz, mahsulot, sotuvchi kesimida).
- Notanish soha so'ralsa: odoo_models bilan modelni toping, odoo_fields bilan maydonlarini ko'ring, keyin so'rang. Model "yo'q" deb xato bersa — modul o'rnatilmagan bo'lishi mumkin: odoo_models bilan tekshirib, shuni ayting. O'rnatilgan modullar ro'yxati: ir.module.module (state = installed, application = true).
- Valyuta: hujjatlar so'm (UZS) va dollar (USD) da yuritiladi. Turli valyutadagi summalar qo'shilmaydi: currency_id bo'yicha guruhlang va alohida ko'rsating. Kompaniyaning hisob valyutasi — USD: "_signed" bilan tugaydigan maydonlar, standard_price, stock.valuation.layer qiymatlari va buxgalteriya qoldiqlari (balance, debit, credit) USD'da.
- Sana va vaqt: hammasi Toshkent vaqtida. Filtrga sanani "YYYY-MM-DD" ko'rinishida yozing — tool chegaralarni o'zi to'g'rilaydi. To'liq bo'lmagan davrni (joriy oy) to'liq davr bilan solishtirganda teng kunlarni oling yoki farqni izohlang.
- Bazada 3 ta kompaniya bor: dunyabunya® (asosiy), dunyabunya logistika, dunyabunya qahvaxona. Alohida so'ralmasa hammasi birga hisoblanadi; kerak bo'lsa company_id bo'yicha ajrating.

Asosiy modellar va maydonlari (~ belgili maydon saqlanmaydi: o'qish mumkin, guruhlash va saralashga yaramaydi):
- sale.order — sotuv buyurtmasi: name, partner_id, user_id (sotuvchi), date_order, amount_total, margin, currency_id, state [draft, sent, sale = tasdiqlangan, cancel]
- sale.order.line — buyurtma qatori: order_id, product_id, product_uom_qty, price_unit, price_subtotal, margin, purchase_price (tannarx), salesman_id, order_partner_id; filtr uchun: order_id.state, order_id.date_order
- pos.order — savdo nuqtasi (kassa) cheklari: name, partner_id, user_id, date_order, amount_total, state [paid, done, invoiced = yakunlangan; cancel]. "Sotuv" deganda sale.order tushuniladi; kassa savdosi alohida so'ralsa yoki "barcha savdo" deyilsa buni ham qo'shing.
- purchase.order — xarid buyurtmasi: name, partner_id (ta'minotchi), user_id, date_order, date_approve, amount_total, currency_id, state [draft, sent, to approve, purchase = tasdiqlangan, done, cancel]; qatorlari — purchase.order.line (order_id, product_id, product_qty, qty_received, price_unit, price_subtotal)
- account.move — buxgalteriya hujjati: name, partner_id, invoice_date, invoice_date_due, amount_total, amount_residual (to'lanmagan qoldiq), currency_id, invoice_user_id, payment_state, state [draft, posted, cancel], move_type [out_invoice = mijozga hisob, in_invoice = ta'minotchi hisobi, out_refund, in_refund, entry]
- account.move.line — o'tkazma qatori: move_id, account_id, partner_id, product_id, date, debit, credit, balance, parent_state [posted]; hisoblar — account.account (name, account_type)
- account.payment — to'lov: name, date, amount, currency_id, partner_id, journal_id, memo, payment_type [inbound = kirim, outbound = chiqim], state [draft, in_process, paid, canceled, rejected]. Amalga oshgan to'lovlar: state in [in_process, paid]; "posted" qiymati yo'q.
- res.partner — mijoz va ta'minotchilar: name, phone, mobile, email, city, category_id, user_id, customer_rank, supplier_rank, create_date. Qarz account.move'dan hisoblanadi (yoki get_top_debtors, check_partner_debt).
- product.product — mahsulot: name~, default_code, barcode, standard_price (tannarx, USD), list_price~, qty_available~, categ_id~, is_storable~. Toifa bo'yicha guruhlash uchun product.template (name, categ_id, list_price).
- stock.quant — ombor qoldig'i: product_id, location_id, quantity, reserved_quantity; faqat ichki joylar: location_id.usage = internal. Omborlar — stock.warehouse, joylar — stock.location.
- stock.valuation.layer — ombor qiymati (USD): product_id, quantity, value, remaining_qty, remaining_value, unit_cost, create_date. Ombordagi tovar qiymati = remaining_value yig'indisi.
- stock.picking — ombor hujjati: name, partner_id, picking_type_id, location_id, location_dest_id, scheduled_date, date_done, origin, state [draft, waiting, confirmed, assigned, done, cancel]; harakatlar — stock.move
- mrp.production — ishlab chiqarish buyurtmasi: name, product_id, product_qty, date_start, date_finished, state
- hr.employee — xodim: name, job_title, department_id, parent_id (rahbari), work_phone, mobile_phone, work_email, birthday, first_contract_date; bo'limlar — hr.department; ta'til — hr.leave (employee_id, holiday_status_id, date_from, date_to, number_of_days, state: validate = tasdiqlangan); davomat — hr.attendance (employee_id, check_in, check_out, worked_hours)
- project.task — vazifa: name, project_id (bo'lim), user_ids (mas'ullar), stage_id, date_deadline, create_date, priority, description, state ['01_in_progress', '02_changes_requested', '03_approved', '04_waiting_normal', '1_done' = bajarilgan, '1_canceled' = bekor qilingan]. Ochiq vazifa: state not in ['1_done', '1_canceled'].
- crm.lead — lid: name, partner_name, contact_name, phone, stage_id, user_id, expected_revenue, probability, type, create_date
- fleet.vehicle — avtomobil: name, license_plate, driver_id, model_id, state_id
- res.currency.rate — kompaniya hisobda ishlatayotgan kurs: yozuvlar so'm valyutasida turadi (filtr: currency_id.name = "UZS"), name — kurs kuchga kirgan sana, rate — 1 USD necha so'm. Joriy kurs: order "name desc", limit 1. Rasmiy (Markaziy bank) kursi kerak bo'lsa — web_search; "dollar kursi" so'ralsa ikkalasini ham ayting.
- account.currency.exchange — valyuta ayirboshlash: date, partner_id, payment_type [receive = kirim, send = chiqim], to_currency_id, amount, manual_rate, system_rate, state [draft, posted]
- Kassa chiqimlari — alohida hujjat (x_kassa_chiqim): search_kassa_chiqim toolidan foydalaning, account.move'dan qidirmang.
- Kompaniyaning o'zi yaratgan boshqa modellar "x_" bilan boshlanadi (odoo_models, query = "x_").

# AMALLAR
- Vazifa: foydalanuvchi birovga ish topshirsa create_task chaqiring. Mas'ul va muddatni faqat foydalanuvchi aytgan bo'lsa bering. Yaratilgach javobda vazifa nomi, bo'limi, mas'uli, muddati va raqamini (#id) yozing; xodimga xabar yuborilmagan bo'lsa sababini ayting. Holatni o'zgartirish, muddatni surish, mas'ulni almashtirish — update_task.
- Pul va ombor bilan bog'liq yozish amallari (to'lov qoralamasi, sotuv buyurtmasi qoralamasi, lid, ichki ko'chirish) — faqat foydalanuvchi aniq buyurganda va barcha parametrlar ma'lum bo'lganda. Yetishmagan parametrni so'rang, o'zingiz to'ldirmang.
- Eslatma: "eslat", "yodimga sol", "har kuni ... yubor" — set_reminder. Vaqtni joriy sana-vaqtdan hisoblang.
- Xotira: foydalanuvchi "eslab qol", "bundan keyin doim ..." desa yoki doimiy afzalligini aytsa — remember_note. Eslab qolingan qaydlar har so'rovda sizga ko'rsatiladi — ularga amal qiling.
- Guruhdagi yozishmalar haqida so'ralsa ("kecha nima topshirgan edim?") — get_group_history.

# ALOQA XATOLARI
- Tool natijasida "odoo_connection" bo'lsa: Odoo bilan aloqa yo'qligini, tool qaytargan sababni ("error") va tavsiyani ("tavsiya") to'liq, o'zgartirmasdan yozing.
- "Server ishlayaptimi?", "Odoo'ga ulanganmisan?" kabi savolga check_odoo_connection chaqiring va natijani to'liq yozing.

# ROLINGIZ
{role_name}
{role_prompt}
Yuqorida tilga olingan, lekin sizga berilmagan tool bo'lsa — u bu rolda mavjud emas: foydalanuvchiga shuni ayting.
"""


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


_ROW_LIST_KEYS = ("qatorlar", "guruhlar", "fakturalar", "invoices", "maydonlar", "eslatmalar")


def _readable_numbers(value):
    """Tool natijasidagi kasr sonlarni model xatosiz o'qiydigan ko'rinishga keltiradi: katta
    summalar xonalari ajratilgan satr bo'ladi ("22 043 045.11"), qolganlari yaxlitlanadi.
    Sinovda model 22043045.11 ni "22 045" deb o'qigan — uzun raqamlar ketma-ketligida o'rtadagi
    xonalar tushib qolgan. Butun sonlar (ID, soni) o'zgarmaydi."""
    if isinstance(value, float):
        if abs(value) >= 100_000:
            text = f"{value:,.2f}".replace(",", " ")
            return text[:-3] if text.endswith(".00") else text
        return round(value, 2) if abs(value) >= 1 else float(f"{value:.6g}")
    if isinstance(value, dict):
        return {key: _readable_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_readable_numbers(item) for item in value]
    return value


def _fit_rows(rows: list, budget: int) -> list:
    kept, size = [], 0
    for row in rows:
        piece = json.dumps(row, ensure_ascii=False, default=str)
        if size + len(piece) + 2 > budget:
            break
        kept.append(row)
        size += len(piece) + 2
    return kept


def _serialize_tool_result(result) -> str:
    """Tool natijasini model uchun JSON qiladi; sig'masa — BUTUN qatorlar bilan qisqartiradi.

    Ilgari matn shunchaki kesilardi: model yarmi yo'q ro'yxatni ko'rib, bundan bexabar holda
    sanab/jamlab noto'g'ri javob berishi mumkin edi. Endi nechta qator ko'rsatilgani va
    qolganini qanday olish aniq aytiladi."""
    result = _readable_numbers(result)
    raw = json.dumps(result, ensure_ascii=False, default=str)
    if len(raw) <= _TOOL_RESULT_MAX_CHARS:
        return raw
    budget = _TOOL_RESULT_MAX_CHARS - 800
    hint = ("Ko'rinmagan qatorlar haqida taxmin qilmang: sanash uchun odoo_count, jamlash va reyting uchun "
            "odoo_read_group, to'liq ro'yxat uchun export_excel yoki export_text ishlating.")
    if isinstance(result, list):
        kept = _fit_rows(result, budget)
        return json.dumps({
            "qatorlar": kept, "korsatilgan": len(kept), "jami_qaytgan": len(result),
            "ogohlantirish": f"Natija uzun bo'lgani uchun {len(result)} ta qatordan faqat dastlabki "
                             f"{len(kept)} tasi ko'rsatildi. {hint}",
        }, ensure_ascii=False, default=str)
    if isinstance(result, dict):
        for key in _ROW_LIST_KEYS:
            rows = result.get(key)
            if not isinstance(rows, list) or not rows:
                continue
            rest = {k: v for k, v in result.items() if k != key}
            kept = _fit_rows(rows, budget - len(json.dumps(rest, ensure_ascii=False, default=str)))
            note = (f"Natija uzun bo'lgani uchun '{key}' ro'yxatidagi {len(rows)} ta qatordan faqat dastlabki "
                    f"{len(kept)} tasi ko'rsatildi. {hint}")
            previous = rest.get("ogohlantirish")
            return json.dumps({**rest, key: kept, "korsatilgan": len(kept),
                               "ogohlantirish": f"{previous} {note}" if previous else note},
                              ensure_ascii=False, default=str)
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


def _is_error_result(raw: str) -> bool:
    if not raw.startswith('{"error"'):
        return False
    try:
        return isinstance(json.loads(raw), dict)
    except Exception:
        return False


async def _prepare_args(name: str, args: dict) -> dict:
    """Tayyor tool'larga (export_*, generate_html_report, search_*) beriladigan filtrni ham umumiy
    tool'lardagi qoidaga keltiradi: maxfiy modellar o'qilmaydi, sana-vaqtlar Toshkent vaqtidan
    UTC'ga o'giriladi. Umumiy tool'lar (agent_tools) buni o'zi qiladi."""
    if name in agent_tools.TOOLS:
        return args
    if "model" in args:
        agent_tools.check_model(args.get("model"))
    model = args.get("model") or _FIXED_DOMAIN_MODELS.get(name)
    if model and isinstance(args.get("domain"), list) and args["domain"]:
        args = {**args, "domain": await agent_tools.localize_domain(model, args["domain"])}
    return args


def _check_signature(name: str, func: Callable, args: dict) -> None:
    try:
        inspect.signature(func).bind(**args)
    except TypeError as e:
        accepted = ", ".join(inspect.signature(func).parameters)
        raise ToolInputError(f"{name}: parametrlar noto'g'ri ({e}). Qabul qilinadigan parametrlar: {accepted}") from None


async def _exec_tool(name: str, args: dict, role: Role) -> str:
    """Tool ni bajarish — asosiy yoki rolga xos.

    Bajarishdan oldin `_role_denial` rol ruxsatini tekshiradi: role prompt boshqa
    bo'lim ma'lumotini "taqiqlaydi", lekin bu LLM uchun ko'rsatma, texnik cheklov emas."""
    metrics.inc("tool_calls")
    args = dict(args or {})
    try:
        denial = _role_denial(name, args, role)
        if denial:
            return json.dumps({"error": denial}, ensure_ascii=False)
        if name == "check_odoo_connection":
            r = await _odoo.diagnose()
        elif (func := _find_tool(name)) is not None:
            args = await _prepare_args(name, args)
            _check_signature(name, func, args)
            r = await func(**args)
        else:
            r = "Noma'lum tool"

        log.info("tool %s ok: %s", name, str(r)[:100])
        return _serialize_tool_result(r)
    except ToolInputError as e:
        # Model noto'g'ri parametr/maydon/model bergan — xabarni o'qib o'zi tuzatadi
        log.info("tool %s: parametr xatosi — %s", name, e)
        return json.dumps({"error": str(e)[:500]}, ensure_ascii=False)
    except OdooError as e:
        if not e.kind:
            # Odoo so'rovni qabul qilib rad etgan (noto'g'ri field/domain...) — model tuzatib qayta chaqiradi
            log.warning("tool %s: Odoo so'rovni rad etdi — %s", name, e)
            return json.dumps({"error": str(e)[:400]}, ensure_ascii=False)
        # Odoo'ga yetib bo'lmadi/kirib bo'lmadi — aniq sabab va tavsiya bilan qaytariladi.
        # ERROR darajasida: dasturchi buni CEO aytishidan oldin bilishi kerak (P1-2 ogohlantirishi)
        log.error("tool %s: Odoo bilan aloqa yo'q (%s) — %s", name, e.kind, e)
        metrics.inc("odoo_errors")
        return json.dumps({"error": str(e)[:400], "odoo_connection": e.kind,
                           "tavsiya": connection_hint(e.kind)}, ensure_ascii=False)
    except Exception as e:
        log.exception("tool %s error", name)
        metrics.inc("tool_errors")
        return json.dumps({"error": str(e)[:300]}, ensure_ascii=False)


# ─── Model chaqiruvi ────────────────────────────────────────────────────────

async def _call_model(on_tool: Callable[[str], Awaitable[None]] | None = None, **kwargs):
    """Bitta model chaqiruvi (oqim bilan o'qiladi va to'liq javob qaytariladi). Model tool
    so'ray boshlashi bilan `on_tool(tool nomi)` chaqiriladi — foydalanuvchi kutish xabarini
    tool natijasini kutmasdan, darhol ko'radi."""
    async with _client.messages.stream(**kwargs) as stream:
        if on_tool is not None:
            async for event in stream:
                if event.type != "content_block_start":
                    continue
                block = event.content_block
                if block.type in ("tool_use", "server_tool_use"):
                    await on_tool(getattr(block, "name", "") or "")
        return await stream.get_final_message()


_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}
_MIXED_SCRIPT_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ])([A-Za-z][A-Za-z'’ʻ`]{2,})([а-яёўқғҳ]{1,6})(?![A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ])")


def _fix_mixed_script(text: str) -> str:
    """Lotincha so'zning oxiri kirillcha yozilib qolgan holatni tuzatadi ("hozirgача" -> "hozirgacha").
    Model o'zbekcha javobda ba'zan qo'shimchani kirillda yozib yuboradi; to'liq kirillcha so'zlar
    (ruscha matn, nomlar) o'zgarmaydi."""
    def repair(match: re.Match) -> str:
        return match.group(1) + "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in match.group(2))
    return _MIXED_SCRIPT_RE.sub(repair, text)


def _final_text(content) -> str:
    """Javob matni: oxirgi tool chaqiruvidan KEYIN yozilgan matn bloklari. Tool chaqiruvidan
    oldingi izohlar ("avval sotuvni ko'raman...") javobga kirmaydi."""
    parts: list[str] = []
    for block in content:
        if block.type == "text":
            parts.append(block.text)
        elif block.type not in ("thinking", "redacted_thinking"):
            parts = []
    return "".join(parts).strip()


def _log_usage(step: int, resp, role: Role) -> None:
    # cache_read > 0 bo'lsa kesh ishlayapti (ketma-ket so'rovlarda doim 0 chiqsa — keshlangan
    # blokka har safar o'zgaradigan narsa qo'shilib qolgan)
    usage = getattr(resp, "usage", None)
    log.info("qadam %d: stop=%s blocks=%s role=%s tokens(in=%s cache_read=%s cache_write=%s out=%s)",
             step, resp.stop_reason, [b.type for b in resp.content], role.value,
             getattr(usage, "input_tokens", "?"), getattr(usage, "cache_read_input_tokens", "?"),
             getattr(usage, "cache_creation_input_tokens", "?"), getattr(usage, "output_tokens", "?"))
    metrics.add_usage(usage)
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0)
    if isinstance(searches, int) and searches:
        metrics.inc("web_searches", searches)


# Oldingi savolda ishlatilgan so'rovlar (suhbat kaliti bo'yicha, xotirada): "shuni Excelda ber"
# kabi davom savolida model aynan o'sha model/filtrni qayta ishlatadi
_recent_work: dict[int, deque] = {}
_RECENT_WORK_USERS = 300


def _remember_work(user_key: int, question: str, calls: list[tuple[str, dict]]) -> None:
    if not calls:
        return
    if user_key not in _recent_work and len(_recent_work) >= _RECENT_WORK_USERS:
        _recent_work.pop(next(iter(_recent_work)))
    lines = [f"{name} {json.dumps(args, ensure_ascii=False, default=str)[:350]}" for name, args in calls[-6:]]
    _recent_work.setdefault(user_key, deque(maxlen=2)).append((question[:160], lines))


def _dynamic_context(who: agent_context.Actor, *, in_group: bool, scheduled: bool) -> str:
    """Har so'rovda o'zgaradigan kontekst — keshlangan asosiy ko'rsatmadan KEYIN alohida blokda
    turadi (keshni buzmasligi uchun)."""
    now = datetime.now(_TZ)
    lines = [f"Hozir: {now:%Y-%m-%d} ({_WEEKDAYS[now.weekday()]}), soat {now:%H:%M} — Toshkent vaqti."]
    place = "kompaniya guruhi" if in_group else "shaxsiy chat"
    lines.append(f"Suhbatdosh: {who.name or 'ismi aniqlanmagan'}. Chat: {place}.")
    if scheduled:
        lines.append("Bu — oldindan rejalashtirilgan so'rov: foydalanuvchi hozir chatda emas. Aniqlashtiruvchi "
                     "savol bermang, eng ehtimolli ma'no bo'yicha to'liq javob tayyorlang.")
    notes = agent_memory.render(who.memory_owner)
    if notes:
        lines.append("Eslab qolingan qaydlar (foydalanuvchi aytgan afzallik va faktlar — hisobga oling):\n" + notes)
    for question, calls in _recent_work.get(who.user_key, ()):
        lines.append(f"Oldingi savol \"{question}\" uchun ishlatilgan so'rovlar (davom savolida shu model va "
                     "filtrlardan foydalaning):\n" + "\n".join(f"- {c}" for c in calls))
    return "JORIY HOLAT\n" + "\n".join(lines)


# Yaqinda yuborilgan rasm/hujjatlar (suhbat kaliti bo'yicha, xotirada): "undagi summa qancha?" kabi
# davom savolida model ilovani yana ko'radi. Suhbat tarixi bazada faqat matn bo'lib saqlanadi.
_recent_attachments: dict[int, tuple[float, list[dict]]] = {}
_ATTACHMENT_MEMORY_SECONDS = 900
_ATTACHMENT_MARK = "[ilova: "


def _restore_attachments(user_key: int, messages: list[dict]) -> None:
    kept = _recent_attachments.get(user_key)
    if not kept or time.monotonic() - kept[0] > _ATTACHMENT_MEMORY_SECONDS:
        _recent_attachments.pop(user_key, None)
        return
    for message in reversed(messages):
        if message["role"] == "user" and isinstance(message["content"], str) \
                and message["content"].startswith(_ATTACHMENT_MARK):
            message["content"] = [*kept[1], {"type": "text", "text": message["content"]}]
            return


def _task_ids_from(name: str, raw: str) -> list[int]:
    if name != "create_task" or '"yaratildi"' not in raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    task_id = parsed.get("task_id") if isinstance(parsed, dict) else None
    return [task_id] if isinstance(task_id, int) else []


async def ask_with_files(telegram_user_id: int, question: str, role: Role = Role.CEO,
                         group_chat_id: int | None = None,
                         instruction: str = "",
                         force_tools: list[str] | None = None,
                         tool_choice: dict | None = None,
                         exclude_tools: set[str] | None = None,
                         on_tool_start: Callable[[], Awaitable[None]] | None = None,
                         *,
                         user_name: str = "",
                         actor_id: int | None = None,
                         chat_id: int | None = None,
                         thread_id: int | None = None,
                         attachments: list[dict] | None = None,
                         scheduled: bool = False) -> AssistantResult:
    """Savolga javob berish — rolga mos tool'lar bilan.

    Matn javob bilan birga, agar tool'lar fayl (xlsx/html) yaratgan bo'lsa,
    ularning ro'yxatini ham qaytaradi — chaqiruvchi Telegram hujjat sifatida yuboradi.
    Foydalanuvchi matni <user_input> belgilari ichida yuboriladi (prompt-injection
    himoyasi); `instruction` esa tizim tomonidan qo'shilgan ko'rsatma sifatida
    system prompt'ga qo'shiladi.

    `telegram_user_id` — suhbat kaliti (shaxsiy chatda foydalanuvchi ID'si, guruhda guruh ID'si);
    `actor_id` — yozgan odamning o'zi, `chat_id`/`thread_id` — javob ketadigan chat (eslatmalar
    va yaratilgan vazifalar shu yerga bog'lanadi). `attachments` — savolga ilova qilingan rasm
    yoki hujjatlar (tayyor Anthropic kontent bloklari).

    `force_tools`/`tool_choice`: berilsa, model shu tool'lardan birini albatta
    chaqirishga MAJBUR qilinadi (Anthropic `tool_choice`) — foydalanuvchi Excel/HTML
    tugmasini bosgach qayta so'ralganda ishlatiladi, aks holda model tool chaqirmasdan
    faqat matn bilan javob berib, fayl umuman yaratilmasligi mumkin edi.

    `exclude_tools`: shu tool'lar modelga umuman berilmaydi ("Matn" tugmasi — fayl
    yaratuvchi tool'larsiz).

    `on_tool_start`: model BIRINCHI marta tool so'raganda (ya'ni haqiqatan ma'lumot
    qidirish boshlanganda) bir marta chaqiriladi — chaqiruvchi "so'rovingiz
    tayyorlanmoqda" xabarini shu yerda yuboradi. Oddiy suhbatda (model tool'siz
    darhol javob bersa) umuman chaqirilmaydi.
    """
    found_files: dict[str, FileOut] = {}
    found_texts: list[str] = []
    task_ids: list[int] = []
    work: list[tuple[str, dict]] = []
    odoo_failure: dict | None = None
    notified = False
    metrics.inc("questions")

    who = agent_context.Actor(
        user_key=telegram_user_id, role=role.value, telegram_user_id=actor_id or telegram_user_id,
        name=user_name, chat_id=chat_id or telegram_user_id, thread_id=thread_id,
    )
    agent_context.current.set(who)

    async with SessionLocal() as session:
        history = await repo.get_recent_conversation(session, telegram_user_id)

    messages = [{"role": m.role, "content": m.content[:_HISTORY_CHARS]}
                for m in history[-_HISTORY_MESSAGES:] if m.content]
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
    if attachments:
        if len(_recent_attachments) > 200:
            _recent_attachments.clear()
        _recent_attachments[telegram_user_id] = (time.monotonic(), attachments)
    else:
        _restore_attachments(telegram_user_id, messages)
    if attachments:
        messages.append({"role": "user", "content": [*attachments, {"type": "text", "text": wrap_user_input(question)}]})
    else:
        messages.append({"role": "user", "content": wrap_user_input(question)})

    system = _BASE_SYSTEM.replace("{role_name}", ROLE_NAMES.get(role, "Noma'lum"))
    system = system.replace("{role_prompt}", ROLE_PROMPTS.get(role, ""))
    # Xavfsizlik bloki eng yuqori ustuvorlikda — prompt boshida turadi
    system = SECURITY_FRAGMENT + "\n\n" + system
    # Prompt keshi: tool'lar ro'yxati va shu blok (jami ~15 ming token) har so'rovda bir xil —
    # belgi (cache_control) ularni keshlaydi va bitta savol ichidagi keyingi qadamlar hamda
    # 5 daqiqa ichidagi keyingi savollar uni ~10 baravar arzon va tezroq o'qiydi. Blok ichida
    # faqat O'ZGARMAYDIGAN matn bo'lishi shart — sana, vaqt, qaydlar pastdagi alohida blokda.
    system_blocks = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _dynamic_context(who, in_group=bool(group_chat_id), scheduled=scheduled)},
    ]
    if instruction:
        # Tizim qo'shgan ko'rsatma (masalan format tanlovi) — user_input ichida EMAS;
        # keshlangan blokdan KEYIN turadi, shuning uchun keshni buzmaydi
        system_blocks.append({"type": "text", "text":
                              "QO'SHIMCHA TIZIM KO'RSATMACHI (bajarilishi shart):\n" + instruction})

    tools = _get_tools_for_role(role)
    if scheduled:
        exclude_tools = {*(exclude_tools or ()), "set_reminder"}  # rejalashtirilgan so'rov yangisini qo'ymaydi
    if exclude_tools:
        tools = [t for t in tools if t["name"] not in exclude_tools]
    forced = False
    if force_tools:
        narrowed = [t for t in tools if t["name"] in force_tools]
        # Agar rol shu tool'larning birortasiga ruxsatli bo'lmasa (bo'sh qolsa) —
        # to'liq ro'yxatga qaytamiz, aks holda Anthropic bo'sh tools=[] ni rad etadi
        tools = narrowed or tools
        forced = bool(narrowed)
    use_web = role == Role.CEO and settings.WEB_SEARCH_ENABLED and not forced

    async def notify_tool(tool_name: str = "") -> None:
        nonlocal notified
        if notified or on_tool_start is None or tool_name in _INSTANT_TOOLS:
            return
        notified = True
        try:
            await on_tool_start()
        except Exception:
            log.warning("ask_with_files: on_tool_start xatosi", exc_info=True)

    def request(step: int, last_step: bool) -> dict:
        kwargs: dict = dict(model=settings.ANTHROPIC_MODEL, max_tokens=_MAX_TOKENS, system=system_blocks,
                            tools=tools + (_WEB_TOOLS if use_web and _features["web"] else []),
                            messages=messages)
        if _features["thinking"]:
            # Majburlangan tool chaqiruvi (Excel/HTML tugmasi) — mexanik ish, o'ylash kerak emas
            kwargs["thinking"] = {"type": "disabled"} if tool_choice else {"type": "adaptive"}
            if not tool_choice:
                kwargs["output_config"] = {"effort": settings.ANTHROPIC_EFFORT}
        # tool_choice faqat BIRINCHI qadamda majburlanadi — shundan keyin ham majburlansa,
        # Claude tool natijasini ko'rib matn bilan yakunlay olmay, tool'ni cheksiz qayta chaqirardi
        if tool_choice and step == 0:
            kwargs["tool_choice"] = tool_choice
        elif last_step:
            kwargs["tool_choice"] = {"type": "none"}  # oxirgi qadam: faqat yakuniy javob
        return kwargs

    answer = ""
    file_tool_results: list[tuple[str, str]] = []  # (tool_name, raw_result) — xato diagnostikasi uchun
    max_steps = max(3, settings.AGENT_MAX_STEPS)

    step = 0
    while step < max_steps:
        last_step = step == max_steps - 1
        try:
            resp = await _call_model(on_tool=notify_tool, **request(step, last_step))
        except anthropic.BadRequestError as e:
            # Model yoki hisob biror imkoniyatni qo'llamaydi — o'chirib, shu qadamni qayta bajaramiz
            reason = str(e).lower()
            if _features["thinking"] and any(k in reason for k in ("thinking", "effort", "output_config")):
                _features["thinking"] = False
                log.warning("Model fikrlash rejimini qabul qilmadi — usiz davom etiladi: %s", str(e)[:200])
                continue
            if use_web and _features["web"] and "web_" in reason:
                _features["web"] = False
                log.warning("Internet qidiruvi bu hisobda ishlamadi — o'chirildi: %s", str(e)[:200])
                continue
            raise
        _log_usage(step, resp, role)
        step += 1

        if resp.stop_reason == "pause_turn":
            # Server tomondagi tool (internet qidiruvi) hali tugamadi — javobni qaytarib yuborsak davom etadi
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "refusal":
            answer = "Bu so'rovga javob bera olmayman. Savolni boshqacharoq ifodalab ko'ring."
            break

        # Claude bir nechta tool so'rasa ularni PARALLEL bajarish — javob tezlashadi
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        # tool so'ralmagan har qanday to'xtash (end_turn, max_tokens...) — yakun.
        # Aks holda bo'sh tool_result ro'yxati bilan user xabari yuborilib, API 400 qaytarardi.
        if resp.stop_reason != "tool_use" or not tool_uses:
            answer = _final_text(resp.content)
            if resp.stop_reason == "max_tokens":
                log.warning("ask_with_files: javob max_tokens chegarasida kesildi")
                if answer:
                    answer += "\n\n(Javob uzunlik chegarasiga yetib, shu yerda to'xtadi.)"
            break

        log.info("qadam %d: tools=%s", step, [b.name for b in tool_uses])
        for b in tool_uses:
            await notify_tool(b.name)

        messages.append({"role": "assistant", "content": resp.content})
        results: list[dict] = []
        executed = await asyncio.gather(
            *[_exec_tool(b.name, b.input, role) for b in tool_uses]
        )
        for b, r in zip(tool_uses, executed):
            _collect_report_files(r, found_files)
            _collect_text_exports(r, found_texts)
            task_ids.extend(_task_ids_from(b.name, r))
            work.append((b.name, b.input))
            if b.name in FILE_TOOLS:
                file_tool_results.append((b.name, r))
            item = {"type": "tool_result", "tool_use_id": b.id, "content": r}
            if _is_error_result(r):
                item["is_error"] = True
            results.append(item)

        # Barcha so'rovlar Odoo bilan aloqa yo'qligi sababli yiqilgan bo'lsa — modelni
        # qayta chaqirish foydasiz (u sababni "timeout" deb qisqartirar yoki "Buni qila
        # olmayman" deb yuborardi): aniq sabab foydalanuvchiga to'g'ridan-to'g'ri beriladi.
        failures = [f for f in map(_odoo_connection_failure, executed) if f]
        if failures and len(failures) == len(executed):
            odoo_failure = failures[0]
            answer = _odoo_unreachable_text(odoo_failure)
            break
        if step == max_steps - 1:
            results.append({"type": "text", "text":
                            "[Tizim] Qadamlar chegarasiga yetildi: endi tool chaqirmasdan, to'plangan ma'lumot asosida "
                            "yakuniy javobni yozing. Aniqlab ulgurilmagan qismi bo'lsa — uni ochiq ayting."})
        messages.append({"role": "user", "content": results})

    if not answer:
        answer = ("Kechirasiz, bu so'rovga javob tayyorlab bo'lmadi. Savolni qisqaroq yoki aniqroq qilib "
                  "qayta yozib ko'ring.")

    answer = _fix_mixed_script(answer)
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

    _remember_work(telegram_user_id, question, work)
    stored_question = question if not attachments else f"{_ATTACHMENT_MARK}{len(attachments)} ta fayl/rasm] {question}"
    async with SessionLocal() as session:
        await repo.add_conversation_message(session, telegram_user_id, "user", stored_question)
        await repo.add_conversation_message(session, telegram_user_id, "assistant", answer)
    return AssistantResult(
        text=answer, files=list(found_files.values()), file_error=file_error, texts=found_texts,
        odoo_error=odoo_failure["odoo_connection"] if odoo_failure else None, task_ids=task_ids,
        used_data=any(name not in _NON_DATA_TOOLS for name, _args in work),
    )


async def ask(telegram_user_id: int, question: str, role: Role = Role.CEO,
              group_chat_id: int | None = None) -> str:
    """Faqat matn qaytaruvchi soddalashtirilgan API (eskilar uchun mos)."""
    result = await ask_with_files(telegram_user_id, question, role=role, group_chat_id=group_chat_id)
    return result.text
