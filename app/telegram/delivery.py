"""Report/format yetkazib berish: Excel/HTML tugmalari va fayllarni yuborish.

Assistant javob bergan har bir ma'lumotli so'rovdan so'ng chaqiruvchi
`remember()` bilan kontekstni saqlaydi va `format_keyboard()` orqali 2 ta
tugma (Excel / HTML) qo'shadi. Tugma bosilganda callback so'rovni shu
formatda qayta ishlaydi.

Natijada: barcha turdagi fayllar (Excel, HTML, PDF) haqiqiy hujjat sifatida
Telegram orqali yuboriladi — server manzili (PUBLIC_BASE_URL, odatda lokal
tarmoq IP'si) qanchalik tashqaridan erishib bo'lmasligidan qat'iy nazar,
foydalanuvchi faylni istalgan joydan (ofis tarmog'idan tashqarida ham) ochadi.
"""

from __future__ import annotations

import logging
import os
import re

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.rbac import Role
from app.services.assistant import AssistantResult, ask_with_files

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


def format_keyboard(scope: str) -> InlineKeyboardMarkup:
    """Excel / HTML tanlash tugmalari."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Excel fayl", callback_data=f"fmt:excel:{scope}"),
            InlineKeyboardButton(text="📈 HTML hisobot", callback_data=f"fmt:html:{scope}"),
        ]
    ])


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
                     files: list, text: str = "") -> None:
    """Yaratilgan fayllarni Telegram hujjati sifatida yuboradi (xlsx/pdf/html — barchasi).

    HTML hisobotlar ham hujjat sifatida yuboriladi (link emas) — ular to'liq mustaqil
    fayllar (CSS/ma'lumot ichida, tashqi server so'rovi yo'q), shuning uchun
    PUBLIC_BASE_URL server manzili (odatda LOKAL tarmoq IP'si) qanchalik erishib
    bo'lmasligidan qat'iy nazar, foydalanuvchi faylni yuklab olib istalgan joydan
    brauzerda ochishi mumkin — link ATAYLAB ko'rsatilmaydi (baribir ishlamaydi).
    """
    if not files:
        return
    for f in files:
        try:
            display = _clean_telegram_filename(f.filename)
            # LINK/CAPTION KERAK EMAS — fayl nomining o'zi Telegram'da ko'rinadi,
            # qo'shimcha "📎 ... fayl — ..." xabari aynan shu nomni takrorlab, ortiqcha bo'lardi.
            await bot.send_document(
                chat_id=chat_id,
                message_thread_id=thread_id,
                document=FSInputFile(f.path, filename=display),
            )
        except Exception as e:
            log.warning("send_files document %s failed: %s", f.filename, e)


def _strip_html(text: str) -> str:
    """HTML taglarni olib tashlab, oddiy matn qaytaradi (fallback uchun)."""
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


async def send_assistant_text(bot: Bot, chat_id: int, thread_id: int | None,
                              text: str, reply_markup=None) -> None:
    """Assistant matnini yuborish. Model hisobotlarda <b>/<a href> kabi HTML taglarni
    ishlatishi mumkin — shuning uchun avval parse_mode=HTML bilan yuboriladi, xato
    bo'lsa oddiy matn (tagsiz) sifatida qayta yuboriladi."""
    if not text:
        return
    if len(text) > 4000:
        text = text[:3997] + "..."
    try:
        await bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id,
            text=text, reply_markup=reply_markup, parse_mode="HTML",
        )
        return
    except Exception:
        pass
    try:
        await bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id,
            text=_strip_html(text), reply_markup=reply_markup,
        )
    except Exception as e:
        log.warning("send_assistant_text failed: %s", e)


async def send_result(bot: Bot, chat_id: int, thread_id: int | None,
                      result: AssistantResult) -> None:
    """Assistant natijasini yuborish: matn + fayllar (barchasi hujjat sifatida)."""
    text = result.text
    # Fayl sifatida yuboriladigan hisobotlarda (xlsx/pdf/html/csv) matndagi havola kerak emas
    for f in result.files:
        if f.url and f.url in text:
            text = text.replace(f.url, "")
    if text != result.text:
        text = re.sub(r"(Hisobot tayyorlandi:?\s*|Hisobot tayyor:?\s*)+$", "", text).strip(" \n\u2060")
    await send_assistant_text(bot, chat_id, thread_id, text)
    await send_files(bot, chat_id, thread_id, result.files, text=text)


async def _safe_edit_markup(message: Message | None, markup=None) -> None:
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data.startswith("fmt:"))
async def on_format_choice(callback: CallbackQuery, bot: Bot) -> None:
    """Excel yoki HTML tugmasi bosilganda — so'rovni shu formatda qayta bajarish."""
    await callback.answer()
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        return
    _, fmt, scope = parts

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

    if fmt == "excel":
        instruction = (
            "\n\n📊 Iltimos, yuqoridagi savol bo'yicha ma'lumotni EXCEL (.xlsx) fayl "
            "ko'rinishida tayyorlang — 'export_excel' toolini chaqiring (model, domain, "
            "fields parametrlarini so'rovga mos bering). Javobingiz qisqa bo'lsin va fayl "
            "havolasini ko'rsating."
        )
        # Model shu safar HAQIQATDA export_excel'ni chaqirishga MAJBUR qilinadi —
        # aks holda ba'zan faqat matn bilan javob berib, fayl umuman yaratilmasdi.
        force_tools = ["export_excel"]
        forced_choice = {"type": "tool", "name": "export_excel"}
    else:
        instruction = (
            "\n\n📈 Iltimos, yuqoridagi savol bo'yicha ma'lumotni HTML hisobot "
            "(jadval + grafiklar) ko'rinishida tayyorlang — 'generate_html_report' yoki "
            "tegishli 'generate_*_report' toolini chaqiring. Javobingiz qisqa bo'lsin va "
            "fayl havolasini ko'rsating."
        )
        # Aynan qaysi generate_*_report savolga mos kelishini model o'zi tanlasin —
        # lekin ULARDAN BIRINI albatta chaqirishga majbur (tool_choice=any + tor tools ro'yxati)
        force_tools = [
            "generate_html_report", "generate_company_report",
            "generate_sales_report", "generate_invoice_report", "generate_task_report",
        ]
        forced_choice = {"type": "any"}

    try:
        await bot.send_chat_action(chat_id=chat_id, action="typing", message_thread_id=thread_id)
        working = await bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id,
            text="⏳ Tayyorlanmoqda... biroz kuting",
        )
    except Exception as e:
        log.warning("fmt callback prep failed: %s", e)
        working = None

    try:
        # Ko'rsatma savolga qo'shilib emas, system prompt'ga tizim ko'rsatmasi
        # sifatida uzatiladi (foydalanuvchi matni <user_input> ichida qoladi).
        result = await ask_with_files(
            user_key, question,
            role=role, group_chat_id=group_chat_id,
            instruction=instruction,
            force_tools=force_tools, tool_choice=forced_choice,
        )
    except Exception as e:
        log.exception("fmt regeneration failed")
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                text=f"⚠️ Xatolik yuz berdi: {str(e)[:200]}",
            )
        except Exception:
            pass
        return

    # "Tayyorlanmoqda..." xabarini o'chirib, natijani yuboramiz
    if working is not None:
        try:
            await working.delete()
        except Exception:
            pass

    await send_result(bot, chat_id, thread_id, result)

    if not result.files:
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
