"""Telegram handlers — group task routing, employee assignment, status updates."""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import employee_store, metrics
from app.config import settings
from app.db import repo
from app.db.session import SessionLocal
from app.db.repo import save_group_message
from app.services.assistant import ask_with_files
from app.services.audio_service import synthesize_speech
from app.services.errors import friendly_error
from app.services.llm import AnalyzeResult, analyze_message
from app.services.odoo import OdooClient
from app.telegram import attachments, delivery, replies
from app.telegram.text_extract import get_message_text

_GENERAL_CHAT_KEY = settings.TELEGRAM_GROUP_ID

log = logging.getLogger(__name__)

router = Router()
odoo = OdooClient()

_STATS_KEYWORDS = (
    "statistika", "hisobot", "taqsimot",
    "sotuv statistika", "oylik", "haftalik",
    "moliyaviy", "kirim", "chiqim", "balans",
    "vazifalar soni", "ochiq vazifa",
)

# Task selection state: CEO xabarining message_id'si -> {"result": AnalyzeResult, "topic": Topic,
# "users": [...], "reply_thread_id": int}. Kalit — XABAR (topik emas): ilgari topik bo'yicha
# saqlanardi va bitta topikdagi ikkinchi vazifa birinchisining tanlovini bosib ketardi —
# birinchi xabar ostidagi "Ha, topshirish" bosilsa IKKINCHI vazifa birinchi xodimga yozilardi.
_pending_task_selections: dict[int, dict] = {}
_PENDING_SELECTIONS_MAX = 200  # javobsiz qolgan tanlovlar xotirada to'planib qolmasligi uchun


def _remember_selection(key: int, selection: dict) -> None:
    while len(_pending_task_selections) >= _PENDING_SELECTIONS_MAX:
        _pending_task_selections.pop(next(iter(_pending_task_selections)))
    _pending_task_selections[key] = selection


def _log_bg_exc(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.warning("bg task failed: %s", task.exception())


def _is_stats(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _STATS_KEYWORDS)


# ─── Employee name resolution ──────────────────────────────────────────────

# Simple in-memory cache for employee lookups (TTL: 5 min)
_emp_cache: dict[str, tuple[list, str | None, str | None]] = {}
_emp_cache_ts: dict[str, float] = {}
_CACHE_TTL = 300

async def _resolve_assignee(name: str | None) -> tuple[list[int], str | None, str | None]:
    """Ism bo'yicha mas'ulni aniqlaydi: (user_ids, unresolved_name, no_login_name).

    - user_ids: res.users orqali aniq (yagona/yetakchi) nomzod topilsa — Odoo'da
      RASMAN biriktiriladi (project.task.user_ids).
    - unresolved_name: hech qanday moslik (res.users HAM, hr.employee HAM) topilmasa.
    - no_login_name: hr.employee'da aniq nomzod topildi-yu, Odoo LOGIN hisobi
      yo'qligi sababli RASMAN biriktirib bo'lmadi — chaqiruvchi "topilmadi" emas,
      aniq sababi bilan xabar berishi uchun (jonli Odoo'da tasdiqlangan: 180 ta
      faol xodimdan 134 tasida res.users hisobi yo'q).
    """
    import time
    if not name:
        return [], None, None
    # Check cache (faqat res.users orqali TASDIQLANGAN natija keshlanadi — asl
    # xatti-harakat: topilmagan/login-yo'q holatlar har safar qayta tekshiriladi)
    now = time.time()
    cache_key = name.lower().strip()
    if cache_key in _emp_cache:
        if now - _emp_cache_ts.get(cache_key, 0) < _CACHE_TTL:
            return _emp_cache[cache_key]
    employee = await employee_store.get_employee_by_name(name)
    if employee and employee.odoo_user_id:
        result = ([employee.odoo_user_id], None, None)
        _emp_cache[cache_key] = result
        _emp_cache_ts[cache_key] = now
        return result
    users = await odoo.find_users_by_name(name)
    # Aniq yetakchi bo'lsa (score farqi katta) — yagona nomzod deb qabul qilamiz
    if len(users) == 1 or (len(users) > 1 and users[0].get("score", 0) - users[1].get("score", 0) >= 10):
        result = ([users[0]["id"]], None, None)
        _emp_cache[cache_key] = result
        _emp_cache_ts[cache_key] = now
        return result
    # res.users'da aniq topilmadi — hr.employee (login hisobi yo'q xodimlar) bo'yicha ham tekshiramiz
    employees_odoo = await odoo.find_employees_by_name(name)
    if len(employees_odoo) == 1 or (len(employees_odoo) > 1 and
                                     employees_odoo[0].get("score", 0) - employees_odoo[1].get("score", 0) >= 10):
        return [], None, employees_odoo[0]["name"]
    return [], name, None


async def _find_odoo_users(name: str) -> list[dict]:
    """Ism bo'yicha Odoo foydalanuvchilarini topish — avval local DB, keyin Odoo
    res.users (login hisobi bor — vazifaga rasman biriktirish mumkin), TOPILMASA
    hr.employee (butun xodimlar ro'yxati — login hisobi yo'q bo'lsa ham).

    Jonli Odoo'da tasdiqlangan: 180 ta faol xodimdan 134 tasida Odoo LOGIN
    hisobi YO'Q. Faqat res.users bo'yicha qidirilganda ular ISMI QANCHALIK TO'G'RI
    aytilishidan qat'iy nazar HECH QACHON topilmas va "ismli xodim topilmadi" deb
    (aslida MAVJUD bo'lgan xodim uchun) noto'g'ri xabar berilar edi.

    Har bir natija `source` bilan belgilanadi ("res.users"/"hr.employee") —
    chaqiruvchi (on_task_assign/_write_task) shunga qarab vazifani Odoo'da
    RASMAN biriktirish (user_ids) mumkinmi yo'qmi hal qiladi (Odoo'ning o'zi
    project.task.user_ids maydoniga faqat res.users yozuvlarini qabul qiladi —
    hr.employee ID'sini shu maydonga yozib bo'lmaydi).

    Natija har doim unikal qilinadi: bir xil xodim (odoo_user_id yoki to'liq ism
    bo'yicha) takroran tugma bo'lib chiqmaydi.
    """
    if not name:
        return []
    # 1. Local DB'dan BARCHA mos hodimlarni topish (faqat res.users bilan bog'langanlar —
    #    odoo_user_id shu yerda ATAYLAB faqat login hisobi borlar uchun to'ldiriladi)
    employees = await employee_store.get_employees_by_name(name)
    local_results = []
    seen: set = set()
    for emp in employees:
        if not emp.odoo_user_id:
            continue
        # Bir xil odoo_user_id'ga ega bir nechta Employee yozuvi bo'lishi mumkin — bittasini olamiz
        if emp.odoo_user_id in seen:
            continue
        seen.add(emp.odoo_user_id)
        local_results.append({"id": emp.odoo_user_id, "name": emp.odoo_user_name or emp.full_name,
                              "score": 100.0, "source": "res.users"})
    if local_results:
        return local_results
    # 2. Odoo'dan to'g'ridan-to'g'ri fuzzy qidirish (score >= 75, ball bo'yicha saralangan)
    users = await odoo.find_users_by_name(name)
    if users:
        return [{**u, "source": "res.users"} for u in users]
    # 3. res.users'da yo'q — hr.employee (login hisobi yo'q xodimlar) bo'yicha ham qidiramiz
    employees_odoo = await odoo.find_employees_by_name(name)
    return [{**e, "source": "hr.employee"} for e in employees_odoo]


async def _attachments_for(message: Message, bot: Bot) -> list[dict] | None:
    """Guruhdagi savolga ilova qilingan rasm/hujjat — yordamchi ularni ham ko'radi (izohi bor
    xabarlar uchun; izohsiz rasm guruhda savol hisoblanmaydi)."""
    if not attachments.has_attachment(message):
        return None
    blocks, problems = await attachments.extract([message], bot)
    if problems:
        try:
            await bot.send_message(chat_id=message.chat.id, message_thread_id=message.message_thread_id,
                                   text="⚠️ " + "\n".join(problems))
        except Exception:
            pass
    return blocks or None


async def _notify_general(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=None, text=text)
    except Exception:
        pass


async def _send_task_to_employee(bot: Bot, *, task_id: int, title: str, description: str,
                                  topic_name: str, telegram_user_id: int | None) -> bool:
    """Agar hodim Telegram'da ulangan bo'lsa, unga vazifani OVOZLI xabar (TTS)
    + qisqacha matnli summary sifatida yuboradi. Ovoz generatsiyasi muvaffaqiyatsiz
    bo'lsa, faqat matnli xabarga qaytadi (funksionallik hech qachon butunlay
    to'xtamasligi uchun). Muvaffaqiyatli yuborilsa True, aks holda (ulanmagan
    yoki yuborishda xato) False qaytaradi — chaqiruvchi shu asosda guruhga aniq
    sabab bilan xabar berishi mumkin."""
    if not telegram_user_id:
        return False

    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"task_done:{task_id}"),
            InlineKeyboardButton(text="❌ Bekor qilindi", callback_data=f"task_cancel:{task_id}"),
        ]
    ])

    desc_short = description[:300] + "..." if len(description) > 300 else description
    # Xabar HTML rejimida ketadi — vazifa matnidagi "<", "&" ekranlanmasa Telegram uni rad etadi
    # va xodimga xabar umuman bormaydi ("botni bloklagan bo'lishi mumkin" degan noto'g'ri sabab bilan)
    summary = (f"📋 <b>Yangi vazifa</b>\n\n📌 {html.escape(title)}\n"
               f"📂 Bo'lim: {html.escape(topic_name)}\n")
    if desc_short:
        summary += f"\n📝 {html.escape(desc_short)}\n"

    speech_text = f"Yangi vazifa. {title}. Bo'lim: {topic_name}."
    if desc_short:
        speech_text += f" Tavsif: {desc_short}"

    try:
        audio_bytes = await synthesize_speech(speech_text)
    except Exception as e:  # noqa: BLE001
        log.warning("TTS generatsiyasida kutilmagan xato: %s", e)
        audio_bytes = b""

    try:
        if audio_bytes:
            await bot.send_voice(
                chat_id=telegram_user_id,
                voice=BufferedInputFile(audio_bytes, filename="vazifa.ogg"),
                caption=summary, parse_mode="HTML", reply_markup=buttons,
            )
        else:
            await bot.send_message(chat_id=telegram_user_id, text=summary, reply_markup=buttons, parse_mode="HTML")
        log.info("Vazifa xabari yuborildi tg_id=%d task #%d (ovozli=%s)", telegram_user_id, task_id, bool(audio_bytes))
        return True
    except Exception as e:
        log.warning("Failed to send task notification to tg_id=%d: %s", telegram_user_id, e)
        return False


async def _notify_assignee_or_warn(bot: Bot, *, task_id: int, title: str, description: str,
                                    topic_name: str, odoo_user_id: int | None,
                                    assignee_name: str | None, display_name: str,
                                    chat_id: int, thread_id: int | None,
                                    no_login: bool = False) -> None:
    """Vazifa Odoo'da yaratilgach — mas'ul xodimga ovozli xabar+summary yuboradi
    (agar u botda ro'yxatdan o'tgan bo'lsa), aks holda GURUHDA aniq sababini
    bildiradi. Odoo task yaratish (yozish) allaqachon muvaffaqiyatli bo'lgan,
    shuning uchun bu yerdagi har qanday muammo faqat bildirishnoma bosqichiga
    tegishli — task o'zi yo'qolmaydi.

    Xodim aniqlanishi IKKI bosqichda: avval Odoo bog'lanishi (odoo_user_id)
    orqali, topilmasa — CEO aytgan ism bo'yicha to'g'ridan-to'g'ri Telegram
    ro'yxatidan (employee_store) qidiriladi. Bu ikkinchi bosqich Odoo login
    hisobi yo'q (yoki Odoo'da umuman topilmagan) xodimlarga ham — ular botda
    /start bosgan va ismi to'g'ri aytilgan bo'lsa — xabar yetib borishini
    ta'minlaydi."""
    emp_record = None
    if odoo_user_id:
        emp_record = await employee_store.get_employee_by_odoo_user_id(odoo_user_id)
    if not emp_record and assignee_name:
        emp_record = await employee_store.find_active_employee_by_name(assignee_name)

    if not emp_record or not emp_record.telegram_user_id:
        log.info("_notify_assignee_or_warn: %s uchun botda ro'yxat topilmadi — xabar yuborilmadi", display_name)
        reason = (
            f"{display_name}da Odoo login hisobi yo'qligi sababli rasman biriktirilmadi va shaxsiy xabar yuborilmadi"
            if no_login else
            f"{display_name} botda ro'yxatdan o'tmaganligi sababli shaxsiy xabar yuborilmadi"
        )
        try:
            await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=f"ℹ️ Vazifa yaratildi, biroq {reason}.")
        except Exception:
            pass
        return

    sent = await _send_task_to_employee(
        bot, task_id=task_id, title=title, description=description,
        topic_name=topic_name, telegram_user_id=emp_record.telegram_user_id,
    )
    if not sent:
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                text=f"⚠️ Vazifa yaratildi, biroq {display_name}ga shaxsiy xabar yuborib bo'lmadi "
                     f"(botni bloklagan yoki chatni boshlamagan bo'lishi mumkin).",
            )
        except Exception:
            pass


async def notify_task_assignee(bot: Bot, *, task_id: int, title: str, description: str, topic_name: str,
                               odoo_user_id: int | None, assignee_name: str | None) -> str:
    """Yordamchi (create_task) yaratgan vazifa haqida mas'ul xodimga ovozli xabar + matn yuboradi.
    Natijani matn bilan qaytaradi — yordamchi uni rahbarga aytadi (guruhga alohida xabar yozilmaydi)."""
    emp = await employee_store.get_employee_by_odoo_user_id(odoo_user_id) if odoo_user_id else None
    if not emp and assignee_name:
        emp = await employee_store.find_active_employee_by_name(assignee_name)
    if not emp or not emp.telegram_user_id:
        return "yuborilmadi: xodim botda ro'yxatdan o'tmagan (/start bosmagan)"
    sent = await _send_task_to_employee(bot, task_id=task_id, title=title, description=description,
                                        topic_name=topic_name, telegram_user_id=emp.telegram_user_id)
    return "yuborildi" if sent else "yuborilmadi: xodim botni bloklagan yoki chatni boshlamagan"


async def _save_task_log(*, odoo_task_id: int, odoo_project_id: int, telegram_topic_id: int | None,
                         telegram_message_id: int | None, title: str,
                         created_by_telegram_user_id: int | None) -> None:
    """Yaratilgan vazifani task_log jurnaliga yozadi. Jurnal yozuvi muvaffaqiyatsiz bo'lsa ham
    (vazifa Odoo'da allaqachon yaratilgan) davom etiladi — ilgari bu yerdagi xato mas'ul
    xodimga bildirishnoma yuborilishini ham to'xtatib qo'yardi."""
    try:
        async with SessionLocal() as s:
            await repo.save_task_log(s, odoo_task_id=odoo_task_id, odoo_project_id=odoo_project_id,
                                     telegram_chat_id=settings.TELEGRAM_GROUP_ID,
                                     telegram_topic_id=telegram_topic_id,
                                     telegram_message_id=telegram_message_id,
                                     title=title or "Vazifa",
                                     created_by_telegram_user_id=created_by_telegram_user_id)
    except Exception:
        log.exception("task_log yozib bo'lmadi (odoo_task_id=%s)", odoo_task_id)


# ─── Asosiy guruh — topic xabarlari ───────────────────────────────────────

@router.message(F.chat.id == settings.TELEGRAM_GROUP_ID, F.message_thread_id.is_not(None))
async def on_topic_message(message: Message, bot: Bot) -> None:
    log.info("on_topic_message: chat=%s thread=%s from=%s text=%s", message.chat.id, message.message_thread_id, message.from_user.id if message.from_user else '?', (message.text or '')[:60])
    # 2026-09-28 (CEO buyrug'i): asosiy guruhda bot FAQAT CEO'larga (.env'dagi
    # CEO_TELEGRAM_USERNAME ro'yxati) javob beradi — bo'lim topiklarida ham.
    # Boshqa a'zo xabari STT/LLM'ga yuborilmay jimgina tashlab yuboriladi.
    if not _is_ceo_message(message):
        return
    text = await get_message_text(message, bot)
    if not text:
        if message.voice:
            log.info("on_topic: ovoz transkripti bo'sh — xabardor qilamiz")
            try:
                await bot.send_message(
                    chat_id=settings.TELEGRAM_GROUP_ID,
                    message_thread_id=message.message_thread_id,
                    text="⚠️ Ovozli xabaringizni tushunolmadim. Qaytadan yozib ko'ring yoki matn sifatida yuboring.",
                )
            except Exception:
                pass
        return
    async with SessionLocal() as session:
        topic = await repo.get_topic_by_telegram_id(session, message.message_thread_id)
    if not topic:
        if await _is_for_another_user(text, bot):
            return
        log.info("on_topic: no topic for thread_id=%s, falling back to assistant", message.message_thread_id)
        # Ro'yxatdan o'tmagan mavzu — faqat AI javob (Odoo'ga vazifa YOZILMAYDI)
        await _assistant_reply(bot, text, thread_id=message.message_thread_id, user=message.from_user,
                               attachments=await _attachments_for(message, bot))
        return

    # Guruh xabarini saqlash (1 kunlik kontekst)
    user_name = message.from_user.full_name if message.from_user else "Noma'lum"

    async def _save_context():
        try:
            async with SessionLocal() as s:
                await save_group_message(s, chat_id=message.chat.id, topic_id=message.message_thread_id,
                                         user_id=message.from_user.id if message.from_user else 0,
                                         user_name=user_name, text=text[:1000], msg_type="voice" if message.voice else "text")
        except Exception:
            pass

    log.info("on_topic: chat=%s thread=%s text=%s", message.chat.id, message.message_thread_id, text[:80])
    await bot.send_chat_action(chat_id=settings.TELEGRAM_GROUP_ID, action="typing", message_thread_id=message.message_thread_id)
    # Yagona OpenAI tahlil: task/so'rov/statistika ajratiladi. (Ilgari triage +
    # analyze IKKI alohida LLM chaqiruvi ketardi — endi bittasi yetarli, tezroq.)
    # Kontekst saqlash (DB yozuvi) tahlil natijasiga bog'liq EMAS — oldin
    # kutib turmasdan PARALLEL bajariladi.
    _, result = await asyncio.gather(_save_context(), analyze_message(text))
    log.info("on_topic: is_task=%s scores=%s", result.is_task, result.department_scores)
    if result.analysis_failed:
        # AI xizmatlari javob bermadi — xabar vazifami yoki savolmi, noma'lum. Ilgari bunday
        # holatda HAR QANDAY xabar (salom ham) Odoo'ga vazifa bo'lib yozilib ketardi.
        await bot.send_message(
            chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=message.message_thread_id,
            text="⚠️ Xabarni tahlil qilib bo'lmadi — AI xizmati hozir javob bermayapti. "
                 "Odoo'ga hech narsa yozilmadi. Birozdan keyin xabarni qayta yuboring.",
        )
        return
    if not result.is_task:
        # Vazifa emas va boshqa odamga qaratilgan ("@kimdir ...") — bot aralashmaydi.
        # (Vazifa bo'lsa "@kimdir shuni qilsin" ham odatdagidek Odoo'ga yoziladi.)
        if await _is_for_another_user(text, bot):
            return
        await _assistant_reply(bot, text, thread_id=message.message_thread_id, user=message.from_user,
                               attachments=await _attachments_for(message, bot))
        return

    # Agar ism aytilgan bo'lsa — hodim tanlash tugmalarini ko'rsatamiz
    if result.assignee_name:
        try:
            users = await _find_odoo_users(result.assignee_name)
        except Exception as e:
            # Ilgari bu yerda xato butun handler'ni jim-jimgina to'xtatib qo'yardi —
            # vazifa yozilmasdi-yu, foydalanuvchi hech qanday xabar ko'rmasdi.
            log.exception("on_topic_message: xodim qidirishda xatolik")
            await bot.send_message(
                chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=message.message_thread_id,
                text=f"⚠️ Odoo bilan bog'lanishda xatolik yuz berdi: {friendly_error(e)}\nBiroz keyin qayta urinib ko'ring.",
            )
            return

        # Aniq yetakchi bo'lsa (score farqi katta) — yagona nomzod rejimiga o'tamiz
        if len(users) > 1 and users[0].get("score", 0) - users[1].get("score", 0) >= 10:
            users = users[:1]

        if not users:
            # Ism topilmadi — oddiy yozamiz
            await _write_task(bot, result, topic.odoo_project_id, topic.name,
                              reply_thread_id=message.message_thread_id,
                              telegram_topic_id=message.message_thread_id,
                              telegram_message_id=message.message_id,
                              created_by_telegram_user_id=message.from_user.id if message.from_user else None)
            return

        # Tanlov shu XABARga bog'lanadi — tugmalar ham shu kalit bilan
        selection_key = message.message_id
        users = users[:8]  # tugmalar soni; tanlov faqat shu ko'rsatilganlar orasidan
        _remember_selection(selection_key, {
            "result": result,
            "topic": topic,
            "users": users,
            "reply_thread_id": message.message_thread_id,
            "telegram_topic_id": message.message_thread_id,
            "telegram_message_id": message.message_id,
            "created_by_telegram_user_id": message.from_user.id if message.from_user else None,
        })
        # Vazifa matni va ism — Odoo/LLM'dan kelgan erkin matn: HTML uchun ekranlanadi
        # ("<", "&" bo'lsa Telegram xabarni rad etib, tugmalar umuman chiqmay qolardi)
        task_title = html.escape(result.title or result.description[:80])

        if len(users) == 1:
            # 1 ta topildi — tasdiqlash so'raymiz
            user = users[0]
            buttons = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Ha, topshirish", callback_data=f"task_assign:{selection_key}:{user['id']}"),
                    InlineKeyboardButton(text="❌ Yo'q", callback_data=f"task_skip:{selection_key}"),
                ]
            ])
            no_login_hint = "\n⚠️ Diqqat: bu xodimda Odoo login hisobi yo'q — rasman biriktirib bo'lmaydi, shaxsiy xabar ham ketmaydi." \
                if user.get("source") == "hr.employee" else ""
            await bot.send_message(
                chat_id=settings.TELEGRAM_GROUP_ID,
                message_thread_id=message.message_thread_id,
                text=(
                    f"📋 <b>Vazifa:</b> {task_title}\n\n"
                    f"👤 <b>Bajaruvchi:</b> {html.escape(str(user['name']))}\n"
                    f"{no_login_hint}\n"
                    f"Topshirasizmi?"
                ),
                reply_markup=buttons, parse_mode="HTML",
            )
            return

        # Bir nechta topildi — tanlash kerak
        buttons = [
            [InlineKeyboardButton(text=str(u["name"])[:60], callback_data=f"task_assign:{selection_key}:{u['id']}")]
            for u in users
        ]
        buttons.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"task_skip:{selection_key}")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await bot.send_message(
            chat_id=settings.TELEGRAM_GROUP_ID,
            message_thread_id=message.message_thread_id,
            text=(
                f"📋 <b>Vazifa:</b> {task_title}\n\n"
                f"👤 \"{html.escape(result.assignee_name)}\" bo'yicha {len(users)} ta hodim topildi.\n"
                f"Qaysi biriga topshirasiz?"
            ),
            reply_markup=keyboard, parse_mode="HTML",
        )
        return

    # Ism aytilmagan — avtomatik yozamiz
    await _write_task(bot, result, topic.odoo_project_id, topic.name,
                      reply_thread_id=message.message_thread_id,
                      telegram_topic_id=message.message_thread_id,
                      telegram_message_id=message.message_id,
                      created_by_telegram_user_id=message.from_user.id if message.from_user else None)


# ─── Callback: vazifani hodimga topshirish ─────────────────────────────────

@router.callback_query(F.data.startswith("task_assign:"))
async def on_task_assign(callback: CallbackQuery, bot: Bot) -> None:
    if not await _is_ceo_callback(callback):
        return
    await callback.answer()
    _, selection_key_str, user_id_str = callback.data.split(":")
    selection_key = int(selection_key_str)
    chosen_user_id = int(user_id_str)

    sel = _pending_task_selections.get(selection_key)
    # Tanlangan nomzod AYNAN shu vazifa uchun taklif qilinganlardan bo'lishi shart —
    # aks holda (eskirgan/begona tugma) vazifa noto'g'ri odamga yozilib ketishi mumkin
    chosen_candidate = next((u for u in (sel or {}).get("users", []) if u.get("id") == chosen_user_id), None)
    if not sel or chosen_candidate is None:
        try:
            await callback.message.edit_text("⏰ Muddati o'tgan — vazifani qaytadan yozing.", reply_markup=None)
        except Exception:
            pass
        return

    result: AnalyzeResult = sel["result"]
    topic = sel["topic"]
    telegram_topic_id = sel["telegram_topic_id"]
    telegram_message_id = sel["telegram_message_id"]
    created_by_telegram_user_id = sel.get("created_by_telegram_user_id")
    # Tugma ikki marta bosilsa ikkita vazifa yaratilmasligi uchun tanlov darhol olib tashlanadi
    del _pending_task_selections[selection_key]

    # Tanlangan nomzod res.users (login bor) yoki hr.employee (login yo'q) dan
    # kelganini _find_odoo_users bergan ro'yxatdan aniqlaymiz — bunga qarab Odoo'da
    # RASMAN biriktirish (user_ids) mumkinmi yo'qmi hal qilinadi (project.task.user_ids
    # faqat res.users yozuvlarini qabul qiladi, hr.employee ID'sini emas).
    is_res_users = chosen_candidate.get("source", "res.users") == "res.users"
    # Ism allaqachon _find_odoo_users natijasida bor — qo'shimcha "res.users read"
    # so'rovi (Odoo'ga yana bir round-trip) ORTIQCHA edi, faqat javobni sekinlashtirardi.
    user_name = chosen_candidate.get("name") or f"#{chosen_user_id}"
    task_title = result.title or result.description[:80] or "Vazifa"

    # Task yaratamiz Odoo'da
    try:
        task_id = await odoo.create_task(
            project_id=topic.odoo_project_id,
            name=task_title,
            description=result.description,
            user_ids=[chosen_user_id] if is_res_users else None,
            date_deadline=result.deadline,
        )
    except Exception as e:
        log.exception("on_task_assign: Odoo'da vazifa yaratishda xatolik")
        try:
            await callback.message.edit_text(
                f"⚠️ Vazifani Odoo'ga yozishda xatolik yuz berdi: {friendly_error(e)}",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    async def _log():
        await _save_task_log(odoo_task_id=task_id, odoo_project_id=topic.odoo_project_id,
                             telegram_topic_id=telegram_topic_id,
                             telegram_message_id=telegram_message_id, title=task_title,
                             created_by_telegram_user_id=created_by_telegram_user_id)

    metrics.inc("tasks_created")

    async def _edit():
        try:
            await callback.message.edit_text(
                f"✅ <b>Vazifa topshirildi!</b>\n\n"
                f"📌 {html.escape(task_title)}\n"
                f"👤 Bajaruvchi: {html.escape(str(user_name))}\n"
                f"🆔 #{task_id}",
                reply_markup=replies.created_task_keyboard(task_id), parse_mode="HTML",
            )
        except Exception:
            pass

    # DB yozuvi (Postgres) va Telegram xabarini yangilash bir-biriga bog'liq
    # EMAS — ketma-ket emas, PARALLEL bajariladi (javob tezroq keladi).
    await asyncio.gather(_log(), _edit())

    async def _notify_assignee():
        # `chosen_user_id` faqat res.users bo'lsa Odoo bog'lanishi sifatida beriladi;
        # aks holda (hr.employee, login yo'q) `_notify_assignee_or_warn` ismi bo'yicha
        # employee_store'dan to'g'ridan-to'g'ri qidiradi — Odoo login holatidan qat'iy
        # nazar, botda /start bosgan bo'lsa xabar (ovoz+summary) baribir yetib boradi.
        await _notify_assignee_or_warn(
            bot,
            task_id=task_id,
            title=task_title,
            description=result.description or "",
            topic_name=topic.name,
            odoo_user_id=chosen_user_id if is_res_users else None,
            assignee_name=result.assignee_name or user_name,
            display_name=user_name,
            chat_id=callback.message.chat.id,
            thread_id=callback.message.message_thread_id,
            no_login=not is_res_users,
        )

    # Xodimga bildirishnoma va General topikga xabar bir-biriga bog'liq EMAS —
    # PARALLEL yuboriladi.
    await asyncio.gather(
        _notify_assignee(),
        _notify_general(bot, f"📋 Vazifa → {user_name}\n📌 {task_title}"),
    )


@router.callback_query(F.data.startswith("task_skip:"))
async def on_task_skip(callback: CallbackQuery) -> None:
    if not await _is_ceo_callback(callback):
        return
    await callback.answer()
    _, selection_key_str = callback.data.split(":")
    _pending_task_selections.pop(int(selection_key_str), None)

    try:
        await callback.message.edit_text("❌ Bekor qilindi.", reply_markup=None)
    except Exception:
        pass


# ─── Callback: yaratilgan vazifani o'chirish (CEO, ikki bosqichli) ─────────
# Bot oddiy gapni vazifa deb tushunib Odoo'ga yozib qo'yishi mumkin — CEO uni shu
# xabarning o'zidan o'chira oladi (ilgari buning uchun shaxsiy chatda /otask <raqam>
# yozish kerak edi, raqam esa hech qayerda ko'rsatilmasdi).

async def _set_markup(callback: CallbackQuery, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data.startswith("task_del:"))
async def on_task_delete_ask(callback: CallbackQuery) -> None:
    if not await _is_ceo_callback(callback):
        return
    await callback.answer()
    task_id = int(callback.data.split(":")[1])
    await _set_markup(callback, replies.delete_confirm_keyboard(task_id))


@router.callback_query(F.data.startswith("task_delno:"))
async def on_task_delete_cancel(callback: CallbackQuery) -> None:
    if not await _is_ceo_callback(callback):
        return
    await callback.answer()
    task_id = int(callback.data.split(":")[1])
    await _set_markup(callback, replies.created_task_keyboard(task_id))


@router.callback_query(F.data.startswith("task_delok:"))
async def on_task_delete_confirm(callback: CallbackQuery, bot: Bot) -> None:
    if not await _is_ceo_callback(callback):
        return
    await callback.answer()
    task_id = int(callback.data.split(":")[1])
    try:
        await odoo.delete_task(task_id)
    except Exception as e:
        log.exception("task_delete failed %s", task_id)
        await _set_markup(callback, replies.created_task_keyboard(task_id))
        try:
            await bot.send_message(
                chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id,
                text=f"⚠️ #{task_id} vazifani o'chirib bo'lmadi: {friendly_error(e)}",
            )
        except Exception:
            pass
        return
    metrics.inc("tasks_deleted")
    await _mark_task_message(callback.message, "\n\n🗑 <b>Vazifa Odoo'dan o'chirildi</b>")


# ─── Callback: hodim "Bajarildi" / "Bekor qilindi" bosdi ──────────────────

async def _mark_task_message(message: Message, suffix_html: str) -> None:
    """Xodimga yuborilgan vazifa xabariga natijani qo'shib, tugmalarini olib tashlaydi.

    Vazifa odatda OVOZLI xabar (matni — caption) bo'lib boradi. Ilgari har doim
    `message.text + ...` qilinardi: ovozli xabarda `text` None bo'lgani uchun xato
    jimgina yutilib, xabar o'zgarmas, tugmalar qolaverar va xodim ularni qayta-qayta
    bosa olardi (har bosishda guruhga yana "bajarildi" xabari ketardi)."""
    is_caption = not getattr(message, "text", None) and bool(getattr(message, "caption", None))
    original = getattr(message, "html_text", None)
    if not isinstance(original, str) or not original:
        original = html.escape(getattr(message, "text", None) or getattr(message, "caption", None) or "")
    try:
        if is_caption:
            await message.edit_caption(caption=original + suffix_html, reply_markup=None, parse_mode="HTML")
        else:
            await message.edit_text(original + suffix_html, reply_markup=None, parse_mode="HTML")
        return
    except Exception as e:  # noqa: BLE001
        log.warning("vazifa xabarini yangilab bo'lmadi (%s) — faqat tugmalar olib tashlanadi", e)
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def _finish_task(callback: CallbackQuery, bot: Bot, *, done: bool) -> None:
    """"Bajarildi"/"Bekor qilindi": Odoo'da holatni yozadi, xodim xabarini yangilaydi va
    vazifa berilgan topikka xabar qiladi."""
    # Tugma bosilganiga darhol javob beriladi (aks holda Telegram'da "yuklanmoqda" aylanib turadi);
    # natija/xato alohida xabar bilan bildiriladi — callback'ga ikkinchi marta javob berib bo'lmaydi
    await callback.answer()
    task_id = int(callback.data.split(":")[1])
    action = "task_done" if done else "task_cancel"

    try:
        await odoo.update_task_stage(task_id, done=done)
    except Exception as e:
        log.exception("%s failed %s", action, task_id)
        try:
            await bot.send_message(
                chat_id=callback.from_user.id,
                text=f"⚠️ Vazifa holatini Odoo'ga yozib bo'lmadi: {friendly_error(e)}\n"
                     f"Birozdan keyin tugmani qayta bosing.",
            )
        except Exception:
            pass
        return

    suffix = "\n\n✅ <b>Bajarildi deb belgilandi</b>" if done else "\n\n❌ <b>Bekor qilindi deb belgilandi</b>"
    await _mark_task_message(callback.message, suffix)

    # Xabarnoma vazifa berilgan joyga boradi: guruh mavzusiga yoki — vazifa yordamchi orqali
    # shaxsiy chatdan berilgan bo'lsa — o'sha chatga (task_log jadvalidan aniqlanadi)
    async with SessionLocal() as session:
        log_entry = await repo.get_task_log_by_odoo_task_id(session, task_id)
    if log_entry is None:
        return
    from_group = (log_entry.telegram_chat_id or settings.TELEGRAM_GROUP_ID) == settings.TELEGRAM_GROUP_ID
    if from_group and not log_entry.telegram_topic_id:
        return  # guruhning umumiy mavzusiga "bajarildi" xabari yozilmaydi

    # Xodim ismi: ro'yxatdan o'tgan ism, topilmasa Telegram nomi
    emp = await employee_store.get_employee_by_telegram_id(callback.from_user.id)
    emp_name = ((emp.full_name if emp else None) or callback.from_user.full_name
                or callback.from_user.username or str(callback.from_user.id))
    outcome = "✅ {} — bajarildi ({})" if done else "❌ {} — bekor qilindi ({})"
    try:
        await bot.send_message(
            chat_id=log_entry.telegram_chat_id or settings.TELEGRAM_GROUP_ID,
            message_thread_id=log_entry.telegram_topic_id,
            text=outcome.format(log_entry.title or "Vazifa", emp_name),
        )
    except Exception as e:
        log.warning("%s: guruhga xabarnoma yuborilmadi: %s", action, e)


@router.callback_query(F.data.startswith("task_done:"))
async def on_task_done(callback: CallbackQuery, bot: Bot) -> None:
    await _finish_task(callback, bot, done=True)


@router.callback_query(F.data.startswith("task_cancel:"))
async def on_task_cancel(callback: CallbackQuery, bot: Bot) -> None:
    await _finish_task(callback, bot, done=False)


# ─── General topik ─────────────────────────────────────────────────────────

@router.message(F.chat.id == settings.TELEGRAM_GROUP_ID, F.message_thread_id.is_(None))
async def on_general_message(message: Message, bot: Bot) -> None:
    """General topik — faqat CEO bilan AI muloqot. Odoga vazifa YAZILMAYDI."""
    if not _is_ceo_message(message):
        return
    # "@kimdir ..." — CEO boshqa odamga yozyapti: bot javob bermaydi, lekin xabar
    # guruh konteksti sifatida pastda saqlanadi
    for_another_user = await _is_for_another_user(message.text or message.caption, bot)
    if not for_another_user:
        await bot.send_chat_action(chat_id=settings.TELEGRAM_GROUP_ID, action="typing", message_thread_id=message.message_thread_id)
    text = await get_message_text(message, bot)
    if not text:
        if message.voice:
            try:
                await bot.send_message(
                    chat_id=settings.TELEGRAM_GROUP_ID,
                    message_thread_id=message.message_thread_id,
                    text="⚠️ Ovozli xabaringizni tushunolmadim. Qaytadan yozib ko'ring yoki matn sifatida yuboring.",
                )
            except Exception:
                pass
        return
    # Guruh xabarini saqlash (1 kunlik kontekst)
    user_name = message.from_user.full_name if message.from_user else "Noma'lum"

    async def _save_context():
        try:
            async with SessionLocal() as s:
                await save_group_message(s, chat_id=message.chat.id, topic_id=None,
                                         user_id=message.from_user.id if message.from_user else 0,
                                         user_name=user_name, text=text[:1000], msg_type="voice" if message.voice else "text")
        except Exception:
            pass

    if for_another_user:
        await _save_context()
        return
    # Kontekst saqlash (DB) va assistant javobi bir-biriga bog'liq EMAS — PARALLEL.
    # General topikda faqat assistant javob beradi — task yaratilmaydi.
    await asyncio.gather(_save_context(),
                         _assistant_reply(bot, text, thread_id=None, user=message.from_user,
                                          attachments=await _attachments_for(message, bot)))


# ─── Boshqa guruhlar ──────────────────────────────────────────────────────

def _is_ceo_message(message: Message) -> bool:
    """Xabar CEO'danmi? (asosiy guruhning barcha topiklari va boshqa guruhlar uchun CEO filtri)"""
    user = message.from_user
    if user is None:
        return False
    # Bir nechta CEO (CEO_TELEGRAM_USERNAME vergul bilan). Username bo'yicha tanilsa,
    # raqamli ID proaktiv xabarlar (muddati o'tgan vazifa eslatmasi) uchun eslab qolinadi.
    from app import ceo_state
    return ceo_state.is_ceo(user.id, user.username)


async def _is_ceo_callback(callback: CallbackQuery) -> bool:
    """Guruhdagi "topshirish/bekor qilish" tugmalarini faqat CEO bosa oladi —
    aks holda istalgan a'zo CEO so'ragan vazifani Odoo'ga yozdirib yuborishi mumkin edi."""
    from app import ceo_state
    user = callback.from_user
    if user is not None and ceo_state.is_ceo(user.id, user.username):
        return True
    try:
        await callback.answer("Bu tugma faqat CEO uchun.", show_alert=True)
    except Exception:
        pass
    return False


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.chat.id != settings.TELEGRAM_GROUP_ID)
async def on_other_group(message: Message, bot: Bot) -> None:
    """Boshqa guruhdan Odoo'ga task YOZILMAYDI.

    Faqat CEO yozgan xabarlar ko'rib chiqiladi. Ovoz yoki matn 80%+ ishonch bilan
    task bo'lsa — Odoo'ga yozilmaydi, balki asosiy (general) guruhga note qilib
    tashlanadi: "📌 Manba: {Guruh} | {Kim}: {Matn}". CEO o'sha yerda ko'rib,
    kerak bo'lsa o'zi bo'limga yuboradi. 80% dan past ishonch — hech narsa."""
    if not _is_ceo_message(message):
        return

    text = await get_message_text(message, bot)
    if not text:
        return

    result = await analyze_message(text)
    if not result.is_task or result.analysis_failed:
        return

    # Task ekaniga ishonch: is_task_confidence + eng yuqori bo'lim bahosi
    best_dept = max(result.department_scores.values(), default=0.0)
    confidence = max(result.is_task_confidence, best_dept)
    if confidence < 0.80:
        log.info("Other group: task ekaniga ishonch yetarli emas (conf=%.2f) — note tashlanmaydi", confidence)
        return

    # Odoo'ga yozilmaydi — general guruhga note qilib tashlanadi
    sender = message.from_user.full_name if message.from_user else "Noma'lum"
    group_label = message.chat.title or f"chat {message.chat.id}"
    note = replies.general_reminder_text(group_label, sender, text)
    try:
        await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, text=note)
        log.info("Other group note -> general: chat=%s sender=%s conf=%.2f", group_label, sender, confidence)
    except Exception as e:
        log.warning("Other group note failed: %s", e)
        return

    # Manba guruhga tasdiqlash
    try:
        await message.reply("📌 Eslatma asosiy guruhga yuborildi (Odoo'ga yozilmadi).")
    except Exception:
        pass


# ─── Yordamchi funksiyalar ────────────────────────────────────────────────

_LEADING_MENTION_RE = re.compile(r"^\s*@([A-Za-z0-9_]{4,32})\b")


async def _is_for_another_user(text: str | None, bot: Bot) -> bool:
    """Xabar "@kimdir ..." deb boshlansa va u bot emas bo'lsa — CEO botga emas, o'sha
    odamga yozyapti (ilgari bot bunday xabarlarga ham "Buni qila olmayman" deb javob berardi)."""
    m = _LEADING_MENTION_RE.match(text or "")
    if not m:
        return False
    try:
        bot_username = ((await bot.me()).username or "").casefold()
    except Exception:
        bot_username = ""
    return m.group(1).casefold() != bot_username


async def _assistant_reply(bot: Bot, text: str, thread_id: int | None = None, *,
                           user=None, attachments: list[dict] | None = None) -> None:
    try:
        # Javob tayyorlanguncha "yozmoqda..." indikatori turadi — alohida kutish xabari yuborilmaydi
        async with delivery.typing(bot, settings.TELEGRAM_GROUP_ID, thread_id):
            result = await ask_with_files(
                _GENERAL_CHAT_KEY, text, group_chat_id=_GENERAL_CHAT_KEY,
                user_name=(user.full_name if user else ""), actor_id=(user.id if user else None),
                chat_id=settings.TELEGRAM_GROUP_ID, thread_id=thread_id, attachments=attachments,
            )
    except Exception as e:
        # Ilgari bu yerda xato bo'lsa foydalanuvchi hech qanday javob olmasdan
        # abadiy kutib qolardi.
        log.exception("_assistant_reply: ask_with_files xatosi")
        await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=thread_id,
                               text=f"⚠️ Javob tayyorlashda xatolik yuz berdi: {friendly_error(e)}")
        return
    scope = f"grp-{thread_id if thread_id is not None else 'main'}"
    markup = delivery.offer_formats(
        scope, text, result, chat_id=settings.TELEGRAM_GROUP_ID, thread_id=thread_id,
        user_key=_GENERAL_CHAT_KEY, group_chat_id=_GENERAL_CHAT_KEY,
    )
    await delivery.send_result(bot, settings.TELEGRAM_GROUP_ID, thread_id, result,
                               reply_markup=markup or delivery.task_keyboard(result))


async def _write_task(bot: Bot, result: AnalyzeResult, odoo_project_id: int,
                      department_label: str, reply_thread_id: int,
                      telegram_topic_id: int | None = None, telegram_message_id: int | None = None,
                      created_by_telegram_user_id: int | None = None) -> None:
    log.info("_write_task: project=%d dept=%s title=%s", odoo_project_id, department_label, result.title)
    try:
        user_ids, unresolved_name, no_login_name = await _resolve_assignee(result.assignee_name)
    except Exception as e:
        log.exception("_write_task: xodim aniqlashda xatolik")
        await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=reply_thread_id,
                               text=f"⚠️ Odoo bilan bog'lanishda xatolik yuz berdi: {friendly_error(e)}")
        return
    log.info("_write_task: user_ids=%s unresolved=%s", user_ids, unresolved_name)
    task_title = result.title or result.description[:80] or "Vazifa"
    try:
        task_id = await odoo.create_task(
            project_id=odoo_project_id,
            name=task_title,
            description=result.description, user_ids=user_ids, date_deadline=result.deadline,
        )
    except Exception as e:
        # Ilgari bu yerda try/except umuman yo'q edi — Odoo xato bersa (masalan
        # timeout), vazifa yozilmasdi-yu, guruhda hech qanday xabar chiqmasdi.
        log.exception("_write_task: Odoo'da vazifa yaratishda xatolik")
        await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=reply_thread_id,
                               text=f"⚠️ Vazifani Odoo'ga yozishda xatolik yuz berdi: {friendly_error(e)}")
        return

    async def _log():
        await _save_task_log(odoo_task_id=task_id, odoo_project_id=odoo_project_id,
                             telegram_topic_id=telegram_topic_id,
                             telegram_message_id=telegram_message_id, title=task_title,
                             created_by_telegram_user_id=created_by_telegram_user_id)

    metrics.inc("tasks_created")
    assignee = result.assignee_name if user_ids else None
    text = replies.success_reply_text(task_title, department_label, assignee, result.deadline, task_id)
    await asyncio.gather(_log(), bot.send_message(
        chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=reply_thread_id, text=text,
        reply_markup=replies.created_task_keyboard(task_id)))
    if user_ids:
        # Bu yo'l odatda tasdiqlash tugmasi (on_task_assign) orqali o'tadi, lekin
        # _resolve_assignee shu yerda ham to'g'ridan-to'g'ri bitta aniq nomzod topib
        # qolishi mumkin — shunday holatda ham xabar (ovoz+summary yoki aniq sabab
        # bilan bildirishnoma) yuborilishi kerak, aks holda "Odoo'ga yozildi" deyilib
        # xodim bexabar qoladi.
        await _notify_assignee_or_warn(
            bot,
            task_id=task_id,
            title=task_title,
            description=result.description or "",
            topic_name=department_label,
            odoo_user_id=user_ids[0],
            assignee_name=result.assignee_name,
            display_name=result.assignee_name or f"#{user_ids[0]}",
            chat_id=settings.TELEGRAM_GROUP_ID,
            thread_id=reply_thread_id,
        )
    elif no_login_name:
        # Xodim TOPILDI (hr.employee'da), lekin Odoo login hisobi yo'qligi sababli
        # RASMAN biriktirib bo'lmadi. Baribir botda /start bosgan bo'lishi mumkin —
        # shunday holatda ham ovozli xabar+summary yuboriladi, aks holda aniq sababi bildiriladi.
        await _notify_assignee_or_warn(
            bot,
            task_id=task_id,
            title=task_title,
            description=result.description or "",
            topic_name=department_label,
            odoo_user_id=None,
            assignee_name=no_login_name,
            display_name=no_login_name,
            chat_id=settings.TELEGRAM_GROUP_ID,
            thread_id=reply_thread_id,
            no_login=True,
        )
    elif unresolved_name:
        # Odoo'da (na res.users, na hr.employee) hech topilmadi — baribir shu ism
        # bilan botda /start bosgan bo'lishi mumkin (masalan yangi xodim, Odoo'ga
        # hali kiritilmagan). Topilsa ovozli xabar+summary yuboriladi.
        emp = await employee_store.find_active_employee_by_name(unresolved_name)
        if emp and emp.telegram_user_id:
            await _send_task_to_employee(
                bot,
                task_id=task_id,
                title=task_title,
                description=result.description or "",
                topic_name=department_label,
                telegram_user_id=emp.telegram_user_id,
            )
        else:
            await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=reply_thread_id,
                                   text=replies.assignee_not_found_text(unresolved_name))
