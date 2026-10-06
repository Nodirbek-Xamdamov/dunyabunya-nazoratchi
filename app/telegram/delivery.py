"""Report/format yetkazib berish: Excel/HTML/Matn tugmalari va fayllarni yuborish.

Assistant javob bergan har bir ma'lumotli so'rovdan so'ng chaqiruvchi
`offer_formats()` orqali kontekstni saqlaydi va tugmalarni oladi: ro'yxat/jadval
so'rovida 3 ta (Excel / HTML / Matn), fayl allaqachon yuborilgan bo'lsa — shu
ma'lumotni chatning o'zida matn qilib olish uchun "Matn" tugmasi. Tugma
bosilganda callback so'rovni shu formatda qayta ishlaydi.

Natijada: barcha turdagi fayllar (Excel, HTML, PDF) haqiqiy hujjat sifatida
Telegram orqali yuboriladi — server manzili (PUBLIC_BASE_URL, odatda lokal
tarmoq IP'si) qanchalik tashqaridan erishib bo'lmasligidan qat'iy nazar,
foydalanuvchi faylni istalgan joydan (ofis tarmog'idan tashqarida ham) ochadi.
"""

from __future__ import annotations

import logging
import os
import re
import uuid

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import ceo_state, metrics
from app.rbac import Role
from app.services.assistant import FILE_TOOLS, AssistantResult, ask_with_files
from app.services.errors import friendly_error
from app.telegram.tg_format import TG_LIMIT, split_message, to_telegram_html

log = logging.getLogger(__name__)

router = Router()

# scope -> {"chat_id", "thread_id", "question", "user_key", "role", "group_chat_id"}
_entries: dict[str, dict] = {}

# Faqat JADVAL/RO'YXAT ko'rinishidagi ma'lumot so'ralganda Excel/HTML tugmalari chiqadi.
# Statistika/hisobot so'rovlari esa avtomatik (qora fonda) HTML report qilib beriladi —
# bular uchun tugma so'ralmaydi.
_TABULAR_KEYWORDS = (
    "ro'yxat", "royxat", "royhat", "ro'yhat", "ruyxat", "list", "jadval",
    "jadvali", "table", "jadvalini", "jadval ko'rinishida",
    # Model format so'ragan javoblar ham tugma ko'rsatishi uchun:
    "qaysi formatda", "excel yoki html", "formatda tayyorlab",
    "ko'rinishida tayyorlab", "qaysi ko'rinishda",
)

_STATS_KEYWORDS = (
    "statistika", "statistik", "statistics", "hisobot", "report", "dashboard",
    "grafik", "grafigi", "chart", "diagramma", "analitika", "analytics",
    "tahlil", "holati", "holat", "oqim", "cashflow", "foyda", "daromad",
    "xarajat", "tushum", "kirim", "chiqim", "kpi", "ko'rsatkich", "reyting",
    "dinamika", "taqqoslash", "o'sish", "kamayish",
)


def wants_format_buttons(question: str) -> bool:
    """Faqat jadval/ro'yxat ko'rinishidagi ma'lumot so'ralganda True — Excel/HTML tugmalari
    ko'rsatiladi. Statistika/analitika/hisobot so'rovlari avtomatik HTML report qilinadi,
    shuning uchun ularda tugma ko'rsatilmaydi."""
    if not question:
        return False
    q = question.lower()
    if any(kw in q for kw in _STATS_KEYWORDS):
        return False
    return any(kw in q for kw in _TABULAR_KEYWORDS)


_ENTRIES_MAX = 500  # xotirada cheksiz o'sib ketmasligi uchun — eskisi bosilmasa ham chegara


def remember(scope: str, *, chat_id: int, thread_id: int | None,
             question: str, user_key: int, role: Role = Role.CEO,
             group_chat_id: int | None = None) -> None:
    """So'nggi savol kontekstini saqlash (tugma bosilganda qayta ishlatish uchun)."""
    if len(_entries) >= _ENTRIES_MAX and scope not in _entries:
        oldest = next(iter(_entries), None)
        if oldest is not None:
            _entries.pop(oldest, None)
    _entries[scope] = {
        "chat_id": chat_id,
        "thread_id": thread_id,
        "question": question,
        "user_key": user_key,
        "role": role,
        "group_chat_id": group_chat_id,
    }


def _text_button(scope: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="📝 Matn (shu chatda)", callback_data=f"fmt:text:{scope}")


def format_keyboard(scope: str) -> InlineKeyboardMarkup:
    """Excel / HTML / Matn tanlash tugmalari."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Excel fayl", callback_data=f"fmt:excel:{scope}"),
            InlineKeyboardButton(text="📈 HTML hisobot", callback_data=f"fmt:html:{scope}"),
        ],
        [_text_button(scope)],
    ])


def text_keyboard(scope: str) -> InlineKeyboardMarkup:
    """Fayl yuborilgandan keyin — shu ma'lumotni chatda matn qilib olish tugmasi."""
    return InlineKeyboardMarkup(inline_keyboard=[[_text_button(scope)]])


def offer_formats(scope: str, question: str, result: AssistantResult, *,
                  chat_id: int, thread_id: int | None, user_key: int,
                  role: Role = Role.CEO,
                  group_chat_id: int | None = None) -> InlineKeyboardMarkup | None:
    """Javob ostiga qo'yiladigan format tugmalari (kerak bo'lmasa None) — tugma
    bosilganda qayta ishlatish uchun savol kontekstini ham eslab qoladi.

    - Odoo bilan aloqa yo'q bo'lsa — tugma yo'q (qaysi format tanlanmasin, natija bir xil xato);
    - ro'yxat/jadval so'rovi — Excel / HTML / Matn;
    - boshqa so'rov, lekin fayl yuborilgan (masalan avtomatik HTML hisobot) — faqat "Matn".

    Har bir javob O'Z kaliti bilan eslab qolinadi (`scope` + tasodifiy qo'shimcha). Ilgari
    kalit faqat chat edi: yangi savol eskisini bosib ketar va OLDINGI javob ostidagi
    "Excel" tugmasi bosilganda KEYINGI savolning ma'lumoti kelardi."""
    if result.odoo_error:
        return None
    # Savoldagi "ro'yxat/jadval" so'zi faqat javob uchun ma'lumot o'qilgan bo'lsa hisobga olinadi —
    # "xodimlarga xat yoz, ro'yxat bilan" kabi matn ishiga eksport tugmalari qo'yilmaydi
    wants_table = (wants_format_buttons(question) and getattr(result, "used_data", True)) \
        or wants_format_buttons(result.text)
    if not wants_table and not result.files:
        return None
    key = f"{scope}-{uuid.uuid4().hex[:8]}"
    remember(key, chat_id=chat_id, thread_id=thread_id, question=question,
             user_key=user_key, role=role, group_chat_id=group_chat_id)
    return format_keyboard(key) if wants_table else text_keyboard(key)


def task_keyboard(result: AssistantResult) -> InlineKeyboardMarkup | None:
    """Yordamchi shu javobda vazifa yaratgan bo'lsa — ostiga "Odoo'da ochish / O'chirish" tugmalari
    (guruh mavzusida yaratilgan vazifadagi bilan bir xil: xato yozilgan vazifa bir bosishda o'chiriladi)."""
    if not result.task_ids:
        return None
    from app.telegram import replies
    return replies.created_task_keyboard(result.task_ids[-1])


def _clean_stem(filename: str) -> str:
    """Fayl nomidan uuid/hex qismini olib tashlab, o'qiladigan sarlavha qaytaradi."""
    stem, _ext = os.path.splitext(filename)
    parts = [p for p in stem.split("_") if p]
    # Oxirgi uuid/hex qismini olib tashlash (masalan de14d7cc, 8-12 belgili hex)
    while parts and len(parts[-1]) >= 6 and re.fullmatch(r"[0-9a-fA-F]+", parts[-1]):
        parts.pop()
    if not parts:
        parts = [stem]
    return " ".join(p.capitalize() for p in parts).strip()[:80]


def _clean_telegram_filename(filename: str) -> str:
    """Telegram'da ko'rinadigan fayl nomini tozalaydi: uuid/hex qismi olib tashlanadi."""
    stem, ext = os.path.splitext(filename)
    name = _clean_stem(filename)
    if not name:
        name = stem
    return (name + ext) if ext else name


async def send_files(bot: Bot, chat_id: int, thread_id: int | None,
                     files: list, text: str = "", reply_markup=None) -> None:
    """Yaratilgan fayllarni Telegram hujjati sifatida yuboradi (xlsx/pdf/html — barchasi).

    HTML hisobotlar ham hujjat sifatida yuboriladi (link emas) — ular to'liq mustaqil
    fayllar (CSS/ma'lumot ichida, tashqi server so'rovi yo'q), shuning uchun
    PUBLIC_BASE_URL server manzili (odatda LOKAL tarmoq IP'si) qanchalik erishib
    bo'lmasligidan qat'iy nazar, foydalanuvchi faylni yuklab olib istalgan joydan
    brauzerda ochishi mumkin — link ATAYLAB ko'rsatilmaydi (baribir ishlamaydi).
    `reply_markup` berilsa — oxirgi hujjat ostiga qo'yiladi.
    """
    if not files:
        return
    for idx, f in enumerate(files):
        try:
            display = _clean_telegram_filename(f.filename)
            # LINK/CAPTION KERAK EMAS — fayl nomining o'zi Telegram'da ko'rinadi,
            # qo'shimcha "📎 ... fayl — ..." xabari aynan shu nomni takrorlab, ortiqcha bo'lardi.
            await bot.send_document(
                chat_id=chat_id,
                message_thread_id=thread_id,
                document=FSInputFile(f.path, filename=display),
                reply_markup=reply_markup if idx == len(files) - 1 else None,
            )
            metrics.inc("reports")
        except Exception as e:
            log.warning("send_files document %s failed: %s", f.filename, e)


def _strip_html(text: str) -> str:
    """HTML taglarni olib tashlab, oddiy matn qaytaradi (fallback uchun)."""
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


async def send_assistant_text(bot: Bot, chat_id: int, thread_id: int | None,
                              text: str, reply_markup=None) -> None:
    """Assistant matnini yuborish. Model Markdown yoki <b>/<a href> kabi HTML yozishi
    mumkin — avval Telegram HTML'ga o'giriladi (tg_format), parse_mode=HTML bilan
    yuboriladi, xato bo'lsa oddiy matn (tagsiz) sifatida qayta yuboriladi. Uzun javob
    kesilmaydi — bir nechta xabarga bo'linadi (tugmalar oxirgisiga qo'shiladi)."""
    if not text:
        return
    try:
        formatted = to_telegram_html(text)
    except Exception:
        log.exception("to_telegram_html failed — xom matn yuboriladi")
        formatted = text
    await _send_html_parts(bot, chat_id, thread_id, formatted, reply_markup)


async def _send_html_parts(bot: Bot, chat_id: int, thread_id: int | None,
                           formatted: str, reply_markup=None) -> None:
    """Tayyor Telegram-HTML matnni (kerak bo'lsa bir nechta xabarga bo'lib) yuboradi."""
    parts = split_message(formatted)
    for idx, part in enumerate(parts):
        markup = reply_markup if idx == len(parts) - 1 else None
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                text=part, reply_markup=markup, parse_mode="HTML",
            )
            continue
        except Exception as e:
            log.warning("send_assistant_text HTML rad etildi (%s) — oddiy matn yuboriladi", e)
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                text=_strip_html(part)[:TG_LIMIT], reply_markup=markup,
            )
        except Exception as e:
            log.warning("send_assistant_text failed: %s", e)


async def send_result(bot: Bot, chat_id: int, thread_id: int | None,
                      result: AssistantResult, reply_markup=None) -> None:
    """Assistant natijasini yuborish: matn, so'ng matn-ro'yxatlar (export_text), so'ng
    fayllar (barchasi hujjat sifatida). `reply_markup` (format tugmalari) eng oxirgi
    yuborilgan xabar ostiga qo'yiladi."""
    text = result.text
    # Fayl sifatida yuboriladigan hisobotlarda (xlsx/pdf/html/csv) matndagi havola kerak emas
    for f in result.files:
        if f.url and f.url in text:
            text = text.replace(f.url, "")
    if text != result.text:
        # Havola olib tashlangach "Hisobot tayyorlandi:" yarim jumla bo'lib qoladi \u2014 matn
        # o'rtasida bo'lsa nuqta bilan yakunlanadi, javob faqat shundan iborat bo'lsa
        # umuman yuborilmaydi (fayl nomining o'zi yetarli)
        text = re.sub(r"(Hisobot tayyor(?:landi)?)\s*:[ \t]*(?=\n|$)", r"\1.", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip(" \n\u2060")
        if re.fullmatch(r"(?:Hisobot tayyor(?:landi)?\.?\s*)+", text):
            text = ""

    markup_on_files = reply_markup if result.files else None
    markup_on_texts = reply_markup if result.texts and not result.files else None
    markup_on_answer = reply_markup if not result.files and not result.texts else None

    await send_assistant_text(bot, chat_id, thread_id, text, reply_markup=markup_on_answer)
    # export_text ro'yxatlari tayyor HTML — Markdown o'girishidan o'tkazilmaydi
    # (mahsulot nomlaridagi "*", "_" kabi belgilar buzilmasligi uchun)
    for idx, block in enumerate(result.texts):
        last = idx == len(result.texts) - 1
        await _send_html_parts(bot, chat_id, thread_id, block, markup_on_texts if last else None)
    await send_files(bot, chat_id, thread_id, result.files, text=text, reply_markup=markup_on_files)


async def _safe_edit_markup(message: Message | None, markup=None) -> None:
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=markup)
    except Exception:
        pass


def format_request(fmt: str) -> dict:
    """Format tugmasi ("excel" / "html" / "text") bosilganda yordamchiga beriladigan qo'shimcha
    parametrlar: tizim ko'rsatmasi va qaysi tool'lar majburlanishi yoki olib tashlanishi."""
    if fmt == "text":
        return {
            "instruction": (
                "\n\n📝 Yuqoridagi savol bo'yicha ma'lumotni FAYLSIZ — shu chatning o'zida MATN "
                "ko'rinishida bering. Yozuvlar ro'yxati/jadvali bo'lsa 'export_text' toolini "
                "chaqiring (model, domain, fields; guruhlash kerak bo'lsa group_by) — to'liq "
                "ro'yxatni bot o'zi chatga yuboradi, siz uni qayta yozmang. Statistika yoki "
                "hisoblangan ko'rsatkichlar bo'lsa — raqamlarni tegishli tool'lar orqali olib, "
                "barcha ko'rsatkichlarni qisqartirmasdan matn bilan yozing. Format so'ramang, "
                "fayl yoki havola bermang."
            ),
            # Ro'yxatmi yoki hisoblangan ko'rsatkichmi — model o'zi hal qiladi (majburlanmaydi),
            # lekin fayl yaratuvchi tool'lar unga umuman berilmaydi.
            "force_tools": None, "tool_choice": None, "exclude_tools": FILE_TOOLS,
        }
    if fmt == "excel":
        return {
            "instruction": (
                "\n\n📊 Iltimos, yuqoridagi savol bo'yicha ma'lumotni EXCEL (.xlsx) fayl "
                "ko'rinishida tayyorlang — 'export_excel' toolini chaqiring (model, domain, "
                "fields parametrlarini so'rovga mos bering; savolda guruhlash so'ralgan bo'lsa "
                "group_by). Javobingiz qisqa bo'lsin va fayl havolasini ko'rsating."
            ),
            # Model shu safar HAQIQATDA export_excel'ni chaqirishga MAJBUR qilinadi —
            # aks holda ba'zan faqat matn bilan javob berib, fayl umuman yaratilmasdi.
            "force_tools": ["export_excel"], "tool_choice": {"type": "tool", "name": "export_excel"},
            "exclude_tools": None,
        }
    return {
        "instruction": (
            "\n\n📈 Iltimos, yuqoridagi savol bo'yicha ma'lumotni HTML hisobot "
            "(jadval + grafiklar) ko'rinishida tayyorlang — 'generate_html_report' (savolda "
            "guruhlash so'ralgan bo'lsa group_by bilan) yoki tegishli 'generate_*_report' "
            "toolini chaqiring. Javobingiz qisqa bo'lsin va fayl havolasini ko'rsating."
        ),
        # Aynan qaysi generate_*_report savolga mos kelishini model o'zi tanlasin —
        # lekin ULARDAN BIRINI albatta chaqirishga majbur (tool_choice=any + tor tools ro'yxati)
        "force_tools": ["generate_html_report", "generate_company_report",
                        "generate_sales_report", "generate_invoice_report", "generate_task_report"],
        "tool_choice": {"type": "any"}, "exclude_tools": None,
    }


@router.callback_query(F.data.startswith("fmt:"))
async def on_format_choice(callback: CallbackQuery, bot: Bot) -> None:
    """Excel, HTML yoki Matn tugmasi bosilganda — so'rovni shu formatda qayta bajarish."""
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    _, fmt, scope = parts

    # Guruhda bot faqat CEO'ga xizmat qiladi — tugmani boshqa a'zo bossa hisobot
    # (CEO huquqi bilan) qayta yaratilib, guruhga yuborilmasligi kerak
    pending = _entries.get(scope)
    if pending is not None and pending["chat_id"] < 0:
        user = getattr(callback, "from_user", None)
        if user is None or not ceo_state.is_ceo(user.id, user.username):
            await callback.answer("Bu tugma faqat CEO uchun.", show_alert=True)
            return
    await callback.answer()

    entry = _entries.pop(scope, None)
    # Tugmalarni darhol o'chiramiz (ikki marta bosilishining oldini olish)
    await _safe_edit_markup(callback.message)

    if entry is None:
        try:
            await callback.message.answer("⏰ Muddati o'tgan — savolni qaytadan yozing.")
        except Exception:
            pass
        return

    chat_id = entry["chat_id"]
    thread_id = entry["thread_id"]
    question = entry["question"]
    role = entry["role"]
    user_key = entry["user_key"]
    group_chat_id = entry["group_chat_id"]

    request = format_request(fmt)

    # Fayl tool'i majburlangani uchun odatda 1-2 soniyada tayyor bo'ladi — alohida
    # "Tayyorlanmoqda..." xabari foydalanuvchi buni ko'rguncha allaqachon eskirib, keyin
    # o'chirilib ketadigan ortiqcha xabar bo'lardi; "typing" indikatori yetarli.
    try:
        await bot.send_chat_action(chat_id=chat_id, action="typing", message_thread_id=thread_id)
    except Exception as e:
        log.warning("fmt callback prep failed: %s", e)

    try:
        # Ko'rsatma savolga qo'shilib emas, system prompt'ga tizim ko'rsatmasi
        # sifatida uzatiladi (foydalanuvchi matni <user_input> ichida qoladi).
        result = await ask_with_files(
            user_key, question,
            role=role, group_chat_id=group_chat_id,
            **request,
            actor_id=getattr(getattr(callback, "from_user", None), "id", None),
            user_name=getattr(getattr(callback, "from_user", None), "full_name", "") or "",
            chat_id=chat_id, thread_id=thread_id,
        )
    except Exception as e:
        log.exception("fmt regeneration failed")
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                text=f"⚠️ Xatolik yuz berdi: {friendly_error(e)}",
            )
        except Exception:
            pass
        return

    # Fayl yuborilgach — shu ma'lumotni chatda matn qilib olish taklifi ham beriladi
    follow_up = None
    if fmt != "text" and result.files:
        remember(scope, chat_id=chat_id, thread_id=thread_id, question=question,
                 user_key=user_key, role=role, group_chat_id=group_chat_id)
        follow_up = text_keyboard(scope)
    await send_result(bot, chat_id, thread_id, result, reply_markup=follow_up)

    # Matn tanlanganda fayl kutilmaydi; Odoo bilan aloqa yo'q bo'lsa sababi javobning o'zida
    if fmt != "text" and not result.files and not result.odoo_error:
        # Sababini log qilamiz (avval bu holat sukut saqlab, aniqlash imkonsiz edi)
        log.warning(
            "fmt callback: fayl yaratilmadi — fmt=%s scope=%s reason=%r answer=%r",
            fmt, scope, result.file_error, (result.text or "")[:200],
        )
        note = "ℹ️ Fayl yaratishning iloji bo'lmadi — yuqoridagi javobdan foydalaning."
        if result.file_error:
            note += f"\nSabab: {result.file_error}"
        try:
            await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=note)
        except Exception:
            pass
