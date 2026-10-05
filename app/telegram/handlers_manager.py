"""Private chat handlers: /start registration, CEO assistant, manager DM."""

from __future__ import annotations

import asyncio
import logging
from difflib import SequenceMatcher

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import employee_store
from app.config import settings
from app.constants import DEPARTMENT_NAMES
from app.db import repo
from app.db.models import Manager
from app.db.session import SessionLocal
from app.services.assistant import ask_with_files
from app.rbac import Role, ROLE_NAMES
from app.services.crypto import decrypt, encrypt
from app.services.llm import analyze_message
from app.services.odoo import OdooClient
from app.telegram import delivery, replies
from app.telegram.text_extract import get_message_text

_PLAIN_MESSAGE = (F.text & ~F.text.startswith("/")) | F.voice

_NOT_YOUR_BUTTON = "⚠️ Bu tugma siz uchun emas."

log = logging.getLogger(__name__)

router = Router()

# user_id -> [{"id": ..., "name": ...}] (pending multiple choice)
_pending_choices: dict[int, list[dict]] = {}
# user_id -> True (waiting for name input)
_waiting_name: set[int] = set()
# user_id -> {"full_name": ..., "odoo_user": {"id": ..., "name": ...}}
_pending_confirm: dict[int, dict] = {}

_odoo = OdooClient()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _is_ceo(user_id: int, username: str | None = None) -> bool:
    # Bir nechta CEO (CEO_TELEGRAM_USERNAME vergul bilan) — raqamli ID'ni botning
    # proaktiv xabarlari (masalan P1-6 eslatmasi) uchun ceo_state eslab qoladi.
    from app import ceo_state
    return ceo_state.is_ceo(user_id, username)


def _find_department(name_query: str) -> str | None:
    query = name_query.strip().casefold()
    for name in DEPARTMENT_NAMES:
        if name.casefold() == query:
            return name
    return None


_odoo_clients: dict[tuple[int, str, str], OdooClient] = {}


def _manager_odoo(manager: Manager) -> OdooClient:
    key = (manager.id, manager.odoo_login, manager.odoo_api_key)
    client = _odoo_clients.get(key)
    if client is None:
        client = OdooClient(login=manager.odoo_login, api_key=decrypt(manager.odoo_api_key))
        _odoo_clients[key] = client
    return client


async def _resolve_manager(message: Message) -> Manager | None:
    async with SessionLocal() as session:
        manager = await repo.get_manager_by_telegram_id(session, message.from_user.id)
        if manager is not None:
            return manager
        if message.from_user.username:
            return await repo.activate_manager_by_username(
                session, message.from_user.username, message.from_user.id
            )
    return None


async def _notify_general(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=None, text=text)
    except Exception:
        pass


async def _answer_with_format_options(message: Message, bot: Bot, question: str,
                                      result, role: Role) -> None:
    """Assistant natijasini yuborish — ma'lumotli savollarga Excel/HTML/Matn tugmalari bilan."""
    markup = delivery.offer_formats(
        f"dm-{message.from_user.id}", question, result,
        chat_id=message.from_user.id, thread_id=None,
        user_key=message.from_user.id, role=role,
    )
    await delivery.send_result(bot, message.from_user.id, None, result, reply_markup=markup)


async def _ask_assistant(message: Message, text: str, role: Role):
    """Yordamchidan javob oladi. "So'rovingiz tayyorlanmoqda" xabari faqat model
    haqiqatan Odoo'ga murojaat qilganda chiqadi — oddiy suhbatda yuborilmaydi."""
    async def _notify_working() -> None:
        await message.answer(replies.assistant_thinking_text())

    return await ask_with_files(message.from_user.id, text, role=role, on_tool_start=_notify_working)


# ─── /start ─────────────────────────────────────────────────────────────────

@router.message(Command("start"), F.chat.type == "private")
async def on_start(message: Message) -> None:
    user_id = message.from_user.id
    existing = await employee_store.get_employee_by_telegram_id(user_id)
    if existing:
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"❌ Bog'lanishni bekor qilish ({existing.odoo_user_name or existing.full_name})",
                callback_data=f"unlink:{user_id}"
            )]
        ])
        await message.answer(
            f"👋 Assalomu alaykum, {existing.full_name}!\n"
            f"🔗 Odoo: {existing.odoo_user_name or 'Noma\'lum'}",
            reply_markup=buttons,
        )
        return
    _waiting_name.add(user_id)
    await message.answer(
        "👋 Assalomu alaykum! Iltimos, o'zingizning to'liq F.I.SH. ni kiriting."
    )


@router.message(Command("ism"), F.chat.type == "private")
async def on_change_name(message: Message) -> None:
    user_id = message.from_user.id
    existing = await employee_store.get_employee_by_telegram_id(user_id)
    if existing:
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"❌ Bog'lanishni bekor qilish",
                callback_data=f"unlink:{user_id}"
            )]
        ])
        await message.answer(
            f"🔌 Siz allaqachon bog'langansiz: <b>{existing.odoo_user_name or existing.full_name}</b>",
            reply_markup=buttons, parse_mode="HTML",
        )
        return
    _waiting_name.add(user_id)
    await message.answer("📝 To'liq F.I.SH. ni kiriting:")


@router.message(Command("unlink"), F.chat.type == "private")
async def on_unlink_cmd(message: Message) -> None:
    user_id = message.from_user.id
    employee = await employee_store.get_employee_by_telegram_id(user_id)
    if not employee:
        await message.answer("⚠️ Siz hali ro'yxatdan o'tmagansiz.")
        return
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha, bekor qil", callback_data=f"unlink:{user_id}"),
            InlineKeyboardButton(text="❌ Yo'q", callback_data="cancel"),
        ]
    ])
    await message.answer(
        f"⚠️ <b>{employee.odoo_user_name or employee.full_name}</b> bilan bog'ishni bekor qilmoqchimisiz?",
        reply_markup=buttons, parse_mode="HTML",
    )


# ─── Callback: unlink ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("unlink:"))
async def on_unlink(callback: CallbackQuery) -> None:
    _, user_id_str = callback.data.split(":")
    target_id = int(user_id_str)

    # Callback'ga faqat BIR MARTA javob berish mumkin — ogohlantirish avval tekshiriladi
    # (ilgari avval bo'sh javob berilib, ogohlantirish foydalanuvchiga ko'rinmay qolardi)
    if callback.from_user.id != target_id:
        await callback.answer(_NOT_YOUR_BUTTON, show_alert=True)
        return
    await callback.answer()

    employee = await employee_store.unlink_employee(target_id)
    if employee:
        name = employee.odoo_user_name or employee.full_name
        try:
            await callback.message.edit_text(
                f"✅ <b>{name}</b> bilan bog'ish bekor qilindi.",
                reply_markup=None, parse_mode="HTML",
            )
        except Exception:
            pass
        await callback.message.answer(
            f"✅ Bog'ish bekor qilindi: {name}\n\n"
            f"Qaytadan ulanish uchun /start bosing."
        )
        await _notify_general(callback.bot, f"🔴 Hodim bog'ishi bekor qilindi: {name}")
    else:
        try:
            await callback.message.edit_text("⚠️ Siz hali ro'yxatdan o'tmagansiz.", reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data == "cancel")
async def on_cancel(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.edit_text("❌ Bekor qilindi.", reply_markup=None)
    except Exception:
        pass


# ─── Callback: ha/yo'q tasdiqlash ─────────────────────────────────────────

@router.callback_query(F.data.startswith("confirm:"))
async def on_confirm(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    # confirm:yes:user_id  or  confirm:no:user_id
    action = parts[1]
    user_id = int(parts[2])

    if callback.from_user.id != user_id:
        await callback.answer(_NOT_YOUR_BUTTON, show_alert=True)
        return
    await callback.answer()

    info = _pending_confirm.get(user_id)
    if not info:
        try:
            await callback.message.edit_text("⏰ Muddati o'tgan.", reply_markup=None)
        except Exception:
            pass
        return

    del _pending_confirm[user_id]
    chosen = info["odoo_user"]
    input_name = info["full_name"]

    if action == "yes":
        # res.users orqali topilgan bo'lsa uning ID'si odoo_user_id sifatida
        # saqlanadi (vazifa biriktirish/DM shu maydonga tayanadi) — hr.employee
        # zaxira qidiruvi orqali topilgan bo'lsa (login hisobi yo'q), odoo_user_id
        # ATAYLAB None qoldiriladi: hr.employee ID'si res.users ID namespace'iga
        # mos kelmaydi, uni shu maydonga yozish kelajakda vazifa DM'ini BUTUNLAY
        # BOSHQA (tasodifan xuddi shu ID'li) xodimga yuborib qo'yishi mumkin edi.
        is_res_users = chosen.get("source") == "res.users"
        odoo_user_id = chosen["id"] if is_res_users else None

        # Tekshiramiz: allaqachon ulanganmi?
        existing = await employee_store.get_employee_by_telegram_id(user_id)
        if existing and existing.odoo_user_id and odoo_user_id and existing.odoo_user_id != odoo_user_id:
            await callback.message.answer(
                f"⚠️ Siz allaqachon <b>{existing.odoo_user_name}</b> bilan bog'langansiz.\n"
                f"Bekor qilish uchun /unlink bosing.",
                parse_mode="HTML",
            )
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        await employee_store.upsert_employee(
            telegram_user_id=user_id,
            telegram_username=callback.from_user.username,
            full_name=input_name,
            odoo_user_id=odoo_user_id,
            odoo_user_name=chosen["name"],
        )
        log.info("employee registered: telegram_user_id=%s name=%r odoo_user_id=%s (source=%s)",
                  user_id, input_name, odoo_user_id, chosen.get("source"))

        try:
            await callback.message.edit_text(
                f"✅ Tasdiqlandi: <b>{chosen['name']}</b>",
                reply_markup=None, parse_mode="HTML",
            )
        except Exception:
            pass

        note = "" if is_res_users else _NO_LOGIN_NOTE
        await callback.message.answer(
            f"✅ Ro'yxatdan o'tdingiz!\n"
            f"👤 {input_name}\n"
            f"🔗 Odoo: {chosen['name']}{note}"
        )
        await _notify_general(
            bot,
            f"🆕 Hodim ro'yxatdan o'tdi: {input_name} → Odoo: {chosen['name']}"
        )
    else:
        try:
            await callback.message.edit_text(
                "🔄 Qaytadan to'liq F.I.SH. ni kiriting:", reply_markup=None
            )
        except Exception:
            pass
        _waiting_name.add(user_id)


# ─── Callback: hodim tanlash (bir nechta match bo'lsa) ─────────────────────

@router.callback_query(F.data.startswith("emp:"))
async def on_emp_pick(callback: CallbackQuery, bot: Bot) -> None:
    _, requester_id_str, chosen_id_str = callback.data.split(":")
    requester_id = int(requester_id_str)
    chosen_id = int(chosen_id_str)

    if callback.from_user.id != requester_id:
        await callback.answer(_NOT_YOUR_BUTTON, show_alert=True)
        return
    await callback.answer()

    pending = _pending_choices.get(requester_id, [])
    chosen_info = next((c for c in pending if c["id"] == chosen_id), None)
    if not chosen_info:
        try:
            await callback.message.edit_text("⏰ Muddati o'tgan. /start bosing.", reply_markup=None)
        except Exception:
            pass
        return

    del _pending_choices[requester_id]

    # Tasdiqlash uchun ha/yo'q beramiz
    _pending_confirm[requester_id] = {
        "full_name": chosen_info["name"],
        "odoo_user": chosen_info,
    }
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha", callback_data=f"confirm:yes:{requester_id}"),
            InlineKeyboardButton(text="❌ Yo'q", callback_data=f"confirm:no:{requester_id}"),
        ]
    ])
    note = "" if chosen_info.get("source") == "res.users" else _NO_LOGIN_NOTE
    try:
        await callback.message.edit_text(
            f"Siz <b>{chosen_info['name']}</b> ni tanladingiz.\n\n"
            f"Tasdiqlaysizmi?{note}",
            reply_markup=buttons, parse_mode="HTML",
        )
    except Exception:
        pass


# ─── Ism kutilayotgan bo'lsa (helper — asosiy DM handler ichidan chaqiriladi) ──

_NO_LOGIN_NOTE = (
    "\n\nℹ️ Diqqat: Odoo tizimida shaxsiy login hisobingiz topilmadi. Shuning uchun "
    "hozircha vazifa to'g'ridan-to'g'ri sizga biriktirilganda shaxsiy xabar kelmasligi "
    "mumkin — bu bot xatosi emas, Odoo login hisobi yaratilishi kerak (CEO/HR bilan bog'laning)."
)


async def _find_registration_candidates(name: str) -> tuple[list[dict], bool, bool]:
    """Ism bo'yicha ro'yxatdan o'tish nomzodlarini qidiradi: avval Odoo LOGIN
    hisobi (res.users) bo'yicha, TOPILMASA — butun hodimlar ro'yxati (hr.employee)
    bo'yicha ham (qat'iy chegara bilan), TOPILMASA — xuddi shu ro'yxat ustida
    PASTROQ chegara bilan "o'xshash nomlar" taklifi (masalan foydalanuvchi
    to'liq F.I.SH. yozganda, Odoo'da esa faqat ism-familya saqlangan bo'lsa,
    qat'iy moslik score'ni pasaytirib yuborishi mumkin — shunday holatda ham
    foydalanuvchi tugab qolmasligi uchun).

    Qaytadi: (nomzodlar, used_employee_fallback, is_loose_suggestion).
    `used_employee_fallback`/`is_loose_suggestion` — ikkalasi ham chaqiruvchiga
    tegishli ogohlantirish/matn ko'rsatish uchun.

    SABAB (qat'iy→hr.employee bosqichi): jonli Odoo'da tekshirilgan — 180 ta
    faol hodimdan atigi 54 tasi res.users (login) hisobiga ega. Faqat res.users
    bo'yicha qidirilsa, qolgan ~126 nafar xodim ISMI QANCHALIK ANIQ YOZILISHIDAN
    QAT'IY NAZAR hech qachon topilmas va /start orqali ro'yxatdan o'ta olmas
    edi ("ismlarni yaxshi ajratmayapti" shikoyatining asosiy sababi shu edi —
    muammo fuzzy-moslashtirish sifatida emas, qidiruv doirasi res.users bilan
    cheklanganida edi).
    """
    users = await _odoo.find_users_by_name(name)
    candidates = [{**u, "source": "res.users"} for u in users]
    if candidates:
        return candidates, False, False

    employees = await _odoo.find_employees_by_name(name)
    if employees:
        return [{**e, "source": "hr.employee"} for e in employees], True, False

    loose = await _odoo.find_employees_by_name_loose(name)
    return [{**e, "source": "hr.employee"} for e in loose], bool(loose), True


async def _register_employee_by_name(message: Message, user_id: int) -> None:
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 100:
        await message.answer("⚠️ Iltimos, to'g'ri ism kiriting (2-100 ta belgi).")
        return

    _waiting_name.discard(user_id)

    try:
        candidates, used_fallback, is_loose = await _find_registration_candidates(name)
    except Exception as e:
        log.exception("_register_employee_by_name: Odoo qidiruvida xatolik")
        _waiting_name.add(user_id)  # qayta urinib ko'rish uchun holatni saqlaymiz
        await message.answer(f"⚠️ Odoo bilan bog'lanishda xatolik yuz berdi: {str(e)[:200]}\nQaytadan to'liq F.I.SH. ni kiriting.")
        return

    log.info("_register_employee_by_name: name=%r -> %d nomzod (fallback=%s, loose=%s)",
              name, len(candidates), used_fallback, is_loose)
    note = _NO_LOGIN_NOTE if used_fallback else ""

    # Hech narsa topilmasa (aniq HAM, o'xshash HAM) — ro'yxatdan o'tish mumkin emas
    if not candidates:
        await message.answer(
            f"⚠️ \"{name}\" bo'yicha Odoo'da hech qanday mos yoki o'xshash hodim topilmadi.\n"
            f"Faqat Odoo'dagi hodimlar ro'yxatdan o'ta oladi. F.I.SH.ni tekshirib, qayta yozing."
        )
        return

    # 1 ta topilgan — to'g'ridan-to'g'ri tasdiqlash so'raymiz
    if len(candidates) == 1:
        chosen = candidates[0]
        _pending_confirm[user_id] = {
            "full_name": name,
            "odoo_user": chosen,
        }
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ha", callback_data=f"confirm:yes:{user_id}"),
                InlineKeyboardButton(text="❌ Yo'q", callback_data=f"confirm:no:{user_id}"),
            ]
        ])
        header = "🔍 Aniq mos kelmadi, lekin bunga o'xshash topildi" if is_loose else "🔍 Odoo'da topildi"
        await message.answer(
            f"{header}:\n\n"
            f"<b>{chosen['name']}</b>\n\n"
            f"Bu sizmisiz? Tasdiqlaysizmi?{note}",
            reply_markup=buttons, parse_mode="HTML",
        )
        return

    # Bir nechta topilgan — buttonlar bilan tanlash
    _pending_choices[user_id] = candidates[:10]
    buttons = [
        [InlineKeyboardButton(text=u["name"], callback_data=f"emp:{user_id}:{u['id']}")]
        for u in candidates[:10]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    header = (
        f"⚠️ \"{name}\" bo'yicha aniq moslik topilmadi, lekin {len(candidates)} ta o'xshash hodim bor"
        if is_loose else
        f"⚠️ \"{name}\" bo'yicha {len(candidates)} ta hodim topildi"
    )
    await message.answer(
        f"{header}.\n"
        f"O'zingizni tanlang:{note}",
        reply_markup=keyboard,
    )


# ─── CEO admin buyruqlari ──────────────────────────────────────────────────

@router.message(F.chat.type == "private", Command("menejer_biriktir"))
async def on_link_manager(message: Message, command: CommandObject) -> None:
    if not _is_ceo(message.from_user.id, message.from_user.username):
        await message.answer(replies.not_ceo_text())
        return
    args = (command.args or "").split(maxsplit=4)
    if len(args) < 5:
        await message.answer(replies.link_manager_usage_text())
        return
    username, department_query, odoo_login, odoo_api_key, full_name = args
    department = _find_department(department_query)
    if department is None:
        await message.answer(replies.department_not_found_text(department_query, DEPARTMENT_NAMES))
        return

    async with SessionLocal() as session:
        topic = await repo.get_topic_by_name(session, department)
        if topic is None:
            await message.answer(replies.department_not_found_text(department_query, DEPARTMENT_NAMES))
            return
        await repo.upsert_manager(
            session,
            telegram_username=username,
            full_name=full_name,
            department=department,
            odoo_project_id=topic.odoo_project_id,
            odoo_login=odoo_login,
            odoo_api_key=encrypt(odoo_api_key),
        )
    await message.answer(replies.manager_linked_text(full_name, department))


@router.message(F.chat.type == "private", Command("menejer_ochir"))
async def on_unlink_manager(message: Message, command: CommandObject) -> None:
    if not _is_ceo(message.from_user.id, message.from_user.username):
        await message.answer(replies.not_ceo_text())
        return
    args = (command.args or "").split()
    if not args:
        await message.answer(replies.unlink_manager_usage_text())
        return
    async with SessionLocal() as session:
        removed = await repo.deactivate_manager(session, args[0])
    await message.answer(replies.manager_unlinked_text() if removed else replies.manager_not_found_text())


@router.message(F.chat.type == "private", Command("otask"))
async def on_delete_task(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if not args or not args[0].isdigit():
        await message.answer(replies.delete_task_usage_text())
        return
    task_id = int(args[0])

    # CEO — to'liq nazorat: menejerlarga xos bo'lim/egalik tekshiruvlarisiz
    # ISTALGAN vazifani o'chira oladi (o'z admin Odoo hisobi bilan). Bu, jumladan,
    # CEO'ning hali birorta ham menejer ro'yxatga olinmagan holatda ham /otask'ni
    # o'zi sinab ko'rishi uchun kerak.
    if _is_ceo(message.from_user.id, message.from_user.username):
        try:
            task = await _odoo.get_task(task_id)
        except Exception as e:
            log.exception("on_delete_task (CEO): get_task xatosi")
            await message.answer(f"⚠️ Odoo bilan bog'lanishda xatolik yuz berdi: {str(e)[:200]}")
            return
        if task is None:
            await message.answer(replies.task_not_found_text(task_id))
            return
        try:
            await _odoo.delete_task(task_id)
        except Exception as e:
            log.exception("on_delete_task (CEO): delete_task xatosi")
            await message.answer(f"⚠️ Vazifani o'chirishda xatolik yuz berdi: {str(e)[:200]}")
            return
        await message.answer(replies.task_deleted_text(task_id))
        return

    manager = await _resolve_manager(message)
    if manager is None:
        await message.answer(replies.not_a_manager_text())
        return
    odoo_client = _manager_odoo(manager)

    try:
        task = await odoo_client.get_task(task_id)
    except Exception as e:
        log.exception("on_delete_task: get_task xatosi")
        await message.answer(f"⚠️ Odoo bilan bog'lanishda xatolik yuz berdi: {str(e)[:200]}")
        return
    if task is None:
        await message.answer(replies.task_not_found_text(task_id))
        return
    task_project_id = task["project_id"][0] if task.get("project_id") else None
    if task_project_id != manager.odoo_project_id:
        await message.answer(replies.task_not_yours_text(task_id))
        return

    # Bo'lim mos bo'lishi yetarli emas — menejer FAQAT O'ZI yaratgan vazifani
    # o'chira oladi (bir bo'limda bir nechta menejer bo'lishi mumkin). Eski
    # (created_by_telegram_user_id ustuni qo'shilishidan oldingi) yozuvlarda bu
    # maydon NULL — egalik tasdiqlanmagani uchun ATAYLAB rad etiladi.
    async with SessionLocal() as session:
        log_entry = await repo.get_task_log_by_odoo_task_id(session, task_id)
    if log_entry is None or log_entry.created_by_telegram_user_id != message.from_user.id:
        await message.answer(replies.task_not_created_by_you_text(task_id))
        return

    try:
        await odoo_client.delete_task(task_id)
    except Exception as e:
        log.exception("on_delete_task: delete_task xatosi")
        await message.answer(f"⚠️ Vazifani o'chirishda xatolik yuz berdi: {str(e)[:200]}")
        return
    await message.answer(replies.task_deleted_text(task_id))


# ─── Shaxsiy chat: ism kiritish / CEO / Menejer ────────────────────────────
# DIQQAT: aiogram bir xil filtrga mos BIRINCHI handler'da to'xtaydi — shuning
# uchun bir xil filtrli bir nechta handler bo'lishi MUMKIN EMAS (keyingilari
# hech qachon ishga tushmaydi). Tartib bitta handler ichida: ism kiritish →
# CEO → Menejer.

@router.message(_PLAIN_MESSAGE, F.chat.type == "private")
async def on_private_dm_message(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id

    # 1) /start bosib, ismi kutilayotgan xodim ro'yxatdan o'tishi
    if user_id in _waiting_name:
        if not message.text:
            return
        await _register_employee_by_name(message, user_id)
        return

    # Kim yozayotgani ovozni matnga o'girishdan OLDIN aniqlanadi — begona odamning ovozli
    # xabarlari uchun pullik transkripsiya (OpenAI) chaqirilmaydi
    is_ceo = _is_ceo(user_id, message.from_user.username)
    manager = None if is_ceo else await _resolve_manager(message)
    if not is_ceo and manager is None:
        # Yordamchi faqat CEO va menejerlar uchun. Ro'yxatdan o'tmagan odamga nima qilish
        # kerakligi aytiladi (bot qayta ishga tushsa "ism kutilmoqda" holati unutiladi va
        # ismini yozgan odam javobsiz qolib ketardi).
        if await employee_store.get_employee_by_telegram_id(user_id) is None:
            await message.answer("Ro'yxatdan o'tish uchun /start buyrug'ini yuboring.")
        return

    text = await get_message_text(message, bot)
    if not text:
        if message.voice:
            try:
                await message.answer("⚠️ Ovozli xabaringizni tushunolmadim. Qaytadan yozib ko'ring yoki matn sifatida yuboring.")
            except Exception:
                pass
        return

    # 2) CEO — to'liq yordamchi (barcha bo'limlar)
    if is_ceo:
        try:
            result = await _ask_assistant(message, text, Role.CEO)
        except Exception as e:
            log.exception("on_private_dm_message (CEO): ask_with_files xatosi")
            await message.answer(f"⚠️ Javob tayyorlashda xatolik yuz berdi: {str(e)[:200]}")
            return
        await _answer_with_format_options(message, bot, text, result, role=Role.CEO)
        return

    # 3) Menejer — faqat o'z bo'limi (role-based assistant)

    role_map = {
        "Moliya": Role.MOLIYA,
        "Ombor": Role.OMBOR,
        "Sotuv": Role.SOTUV,
        "CRM": Role.SOTUV,
        "Marketing": Role.SOTUV,
        "HR": Role.HR,
        "Logistika": Role.OMBOR,
        "Taminot": Role.OMBOR,
    }
    # Tanib bo'lmagan bo'lim (amalda /menejer_biriktir DEPARTMENT_NAMES bo'yicha
    # oldindan tekshirgani uchun deyarli bo'lmaydi) — xavfsiz tomonga: to'liq
    # CEO huquqi EMAS, balki hech qanday tool/model ruxsati yo'q Role.NONE.
    if manager.department not in role_map:
        log.warning("on_private_dm_message: tanib bo'lmagan bo'lim %r — Role.NONE beriladi", manager.department)
    role = role_map.get(manager.department, Role.NONE)

    try:
        result = await _ask_assistant(message, text, role)
    except Exception as e:
        log.exception("on_private_dm_message (manager): ask_with_files xatosi")
        await message.answer(f"⚠️ Javob tayyorlashda xatolik yuz berdi: {str(e)[:200]}")
        return
    await _answer_with_format_options(message, bot, text, result, role=role)
