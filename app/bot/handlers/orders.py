"""Buyurtmani tasdiqlash, rad etish va o'zgartirish so'rash oqimi."""

import asyncio
import json
import logging
from pathlib import Path
import uuid

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.handlers.menu import is_reserved_navigation_text
from app.bot.states import OrderFeedbackState
from app.database import async_session_maker
from app.i18n import t
from app.models import ConfirmationStatus, Order
from app.utils import cleanup_pdf_if_no_longer_needed, get_user_language

logger = logging.getLogger("aiogram_bot")

FEEDBACK_DIR = Path("data/feedback_files")
FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

# Reject/changes-request feedback_text orqali Odoo'ga (chat-ids/group-orders) o'z matnini
# yuboradi -- lekin tasdiqlashda foydalanuvchi hech narsa yozmaydi, shu sabab Odoo tomonida
# "tasdiqlandi" signali ko'rinishi uchun shu tayyor matn qo'yiladi (Odoo devs so'roviga ko'ra).
CONFIRMED_NOTICE_TEXT = "Buyurtma tasdiqlandi."

router = Router(name="orders")


def _is_unauthorized_group_action(order: Order, actor_user_id: int) -> bool:
    """
    Guruhga yuborilgan buyurtmalarda (order.group_id bor) faqat shu buyurtma uchun
    mas'ul shaxs (order.chat_id) tugmalarni bosa oladi -- guruhning boshqa a'zolari
    bossa rad etiladi. Individual (DM) buyurtmalarga taalluqli emas.
    """
    return order.group_id is not None and order.chat_id is not None and actor_user_id != order.chat_id


async def _start_group_aware_feedback(
    callback: CallbackQuery,
    state: FSMContext,
    order: Order,
    target_state,
    order_id: int,
    lang: str,
    prompt_text: str,
) -> tuple[FSMContext, bool]:
    """
    Guruh buyurtmasi (order.group_id bor) uchun rad etish sababi/o'zgartirish matni
    GURUHDA emas, tugmani bosgan shaxsning shaxsiy (DM) chatida so'raladi -- guruh
    faqat mijozga ko'rinadigan joy, ichki sabab-tafsilot boshqa guruh a'zolariga ochiq
    qolmasligi kerak (2026-08-06, mijoz aniq so'ragan: "guruhda qanday qilishni o'zing
    ideasini qil"). FSM holati bosgan shaxsning SHAXSIY chat kaliti bilan qo'lda
    yasab saqlanadi -- aiogram'ning standart kaliti callback kelgan (guruh) chatga
    bog'langan bo'lardi, shu sabab qo'lda `StorageKey` orqali DM javobi to'g'ri
    ushlanishi ta'minlanadi. Guruh a'zosi botni /start bosgani (group_memberships
    faqat shunday odamlarni saqlaydi) uchun DM odatda yuboriladi -- bloklab qo'ygan
    bo'lsa (yagona kutilayotgan muvaffaqiyatsizlik sababi) xavfsizlik uchun guruhning
    o'ziga qaytariladi (eski xatti-harakat). `group_chat_id`/`group_message_id`/
    `group_caption` FSM data'ga saqlanadi -- javob kelganda (`_update_group_caption_
    after_feedback`) guruhdagi asl PDF xabarining sarlavhasini yakuniy holat bilan
    yangilash uchun kerak. Qaytaradi: (ishlatilgan FSMContext, DM'ga o'tkazildimi)."""
    if order.group_id is not None:
        try:
            await callback.bot.send_message(chat_id=callback.from_user.id, text=prompt_text, parse_mode="HTML")
        except Exception:
            logger.warning(
                "Guruh a'zosiga DM yuborib bo'lmadi (chat_id=%s), guruhning o'ziga qaytarildi",
                callback.from_user.id,
            )
        else:
            private_key = StorageKey(
                bot_id=callback.bot.id, chat_id=callback.from_user.id, user_id=callback.from_user.id
            )
            private_state = FSMContext(storage=state.storage, key=private_key)
            await private_state.set_state(target_state)
            await private_state.update_data(
                order_id=order_id,
                lang=lang,
                group_chat_id=callback.message.chat.id,
                group_message_id=callback.message.message_id,
                group_caption=callback.message.caption,
            )
            return private_state, True

    await state.set_state(target_state)
    await state.update_data(order_id=order_id, lang=lang)
    await callback.message.answer(prompt_text, parse_mode="HTML")
    return state, False


async def _update_group_caption_after_feedback(bot: Bot, data: dict, suffix: str) -> None:
    """Guruh buyurtmasi bo'yicha sabab/o'zgartirish DM orqali qabul qilingach (yoki
    vaqt tugab avto-rad etilgach), guruhdagi asl PDF xabarining sarlavhasini yakuniy
    holat bilan yangilaydi -- aks holda guruh a'zolari uchun tugmalari olib
    tashlangan, lekin natijasi ko'rinmaydigan "osilib qolgan" xabar qolar edi.
    Individual (DM) buyurtmalarda `data`da bu maydonlar yo'q -- shunchaki jim
    qaytadi (no-op)."""
    group_chat_id = data.get("group_chat_id")
    group_message_id = data.get("group_message_id")
    if not group_chat_id or not group_message_id:
        return
    try:
        await bot.edit_message_caption(
            chat_id=group_chat_id,
            message_id=group_message_id,
            caption=(data.get("group_caption") or "") + suffix,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Guruh xabari sarlavhasini yangilashda xatolik: %s", exc)


@router.callback_query(F.data.startswith("confirm_"))
async def process_confirm(callback: CallbackQuery, state: FSMContext):
    """Tasdiqlash tugmasi bosilganda."""
    try:
        order_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Invalid ID!", show_alert=True)
        return

    async with async_session_maker() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        lang = await get_user_language(session, (order.chat_id if order else None) or callback.message.chat.id)

        if not order:
            await callback.answer(t("order_not_found", lang), show_alert=True)
            return

        if _is_unauthorized_group_action(order, callback.from_user.id):
            await callback.answer(t("not_authorized_group_action", lang), show_alert=True)
            return

        order.confirmation_status = ConfirmationStatus.CONFIRMED.value
        order.feedback_text = CONFIRMED_NOTICE_TEXT
        order.synced = False
        await session.commit()
        await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

        try:
            await callback.message.edit_caption(
                caption=(callback.message.caption or "") + t("confirm_caption_suffix", lang),
                parse_mode="HTML"
            )
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception as e:
            logger.warning("Caption yangilashda xatolik: %s", e)

        await callback.answer(t("confirm_alert", lang), show_alert=True)


@router.callback_query(F.data.startswith("reject_"))
async def process_reject(callback: CallbackQuery, state: FSMContext):
    """Rad etish tugmasi bosilganda sababini so'rash."""
    try:
        parts = callback.data.split("_")
        order_id = int(parts[2]) if len(parts) > 2 else int(parts[1])
    except (IndexError, ValueError):
        await callback.answer("Invalid ID!", show_alert=True)
        return

    async with async_session_maker() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        lang = await get_user_language(session, (order.chat_id if order else None) or callback.message.chat.id)

    if not order:
        await callback.answer(t("order_not_found", lang), show_alert=True)
        return

    if _is_unauthorized_group_action(order, callback.from_user.id):
        await callback.answer(t("not_authorized_group_action", lang), show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning("Tugmalarni o'chirishda xatolik: %s", exc)

    reason_state, used_dm = await _start_group_aware_feedback(
        callback, state, order, OrderFeedbackState.waiting_for_reject_reason, order_id, lang, t("reject_prompt", lang)
    )

    if used_dm:
        await callback.answer(t("group_action_check_dm", lang), show_alert=True)
    else:
        await callback.answer()

    await asyncio.sleep(300)

    current_state = await reason_state.get_state()
    if current_state == OrderFeedbackState.waiting_for_reject_reason.state:
        data = await reason_state.get_data()
        if data.get("order_id") == order_id:
            async with async_session_maker() as session:
                res = await session.execute(select(Order).where(Order.id == order_id))
                ord_obj = res.scalar_one_or_none()
                if ord_obj and ord_obj.confirmation_status == ConfirmationStatus.PENDING.value:
                    ord_obj.confirmation_status = ConfirmationStatus.REJECTED.value
                    ord_obj.feedback_text = None
                    ord_obj.synced = False
                    await session.commit()
                    await cleanup_pdf_if_no_longer_needed(session, ord_obj.pdf_path)
                    timeout_chat_id = callback.from_user.id if used_dm else callback.message.chat.id
                    await callback.bot.send_message(chat_id=timeout_chat_id, text=t("reject_timeout", lang))
                    await _update_group_caption_after_feedback(callback.bot, data, t("reject_caption_suffix", lang))
            await reason_state.clear()


@router.message(OrderFeedbackState.waiting_for_reject_reason)
async def handle_reject_reason(message: Message, state: FSMContext, bot: Bot):
    """Rad etish sababini qabul qilish."""
    if is_reserved_navigation_text(message.text):
        # Foydalanuvchi sabab yozish o'rniga menyu tugmasini (yoki /start, /menu) bosdi --
        # bu matnni SABAB sifatida SAQLAMAYMIZ. Holatni tozalab, xabarni SkipHandler
        # orqali navbatdagi routerga (menu.py) o'tkazamiz -- u menyuni ko'rsatadi.
        # Buyurtma PENDING holatida qoladi, mijoz istasa "Rad etish"ni qayta bosadi.
        await state.clear()
        raise SkipHandler()

    data = await state.get_data()
    order_id = data.get("order_id")
    lang = data.get("lang", "uz")

    if not order_id:
        await state.clear()
        return

    reason_text = message.text or message.caption or ""

    async with async_session_maker() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()

        if order:
            order.confirmation_status = ConfirmationStatus.REJECTED.value
            order.feedback_text = reason_text if reason_text else None
            order.synced = False
            await session.commit()
            await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

    await message.answer(t("reject_accepted", lang))
    await _update_group_caption_after_feedback(bot, data, t("reject_caption_suffix", lang))
    await state.clear()


@router.callback_query(F.data.startswith("request_changes_"))
async def process_request_changes(callback: CallbackQuery, state: FSMContext):
    """O'zgartirish kiritish tugmasi bosilganda."""
    parts = callback.data.split("_")
    order_id = int(parts[2]) if len(parts) > 2 else int(parts[1])

    async with async_session_maker() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        lang = await get_user_language(session, (order.chat_id if order else None) or callback.message.chat.id)

    if not order:
        await callback.answer(t("order_not_found", lang), show_alert=True)
        return

    if _is_unauthorized_group_action(order, callback.from_user.id):
        await callback.answer(t("not_authorized_group_action", lang), show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning("Tugmalarni o'chirishda xatolik: %s", exc)

    feedback_state, used_dm = await _start_group_aware_feedback(
        callback, state, order, OrderFeedbackState.waiting_for_feedback, order_id, lang, t("changes_prompt", lang)
    )

    if used_dm:
        await callback.answer(t("group_action_check_dm", lang), show_alert=True)
    else:
        await callback.answer()

    await asyncio.sleep(300)

    current_state = await feedback_state.get_state()
    if current_state == OrderFeedbackState.waiting_for_feedback.state:
        data = await feedback_state.get_data()
        if data.get("order_id") == order_id:
            await feedback_state.clear()


@router.message(OrderFeedbackState.waiting_for_feedback)
async def handle_feedback_input(message: Message, state: FSMContext, bot: Bot):
    """Foydalanuvchining feedback xabarini qabul qiladi."""
    if is_reserved_navigation_text(message.text):
        # Xuddi rad etish sababi bilan bir xil himoya -- menyu tugmasi/`/start`/`/menu`
        # o'zgartirish matni sifatida saqlanmaydi, holat tozalanib menyuga o'tkaziladi.
        await state.clear()
        raise SkipHandler()

    data = await state.get_data()
    order_id = data.get("order_id")
    lang = data.get("lang", "uz")

    if not order_id:
        await message.answer(t("generic_error", lang))
        await state.clear()
        return

    feedback_text = message.text or message.caption or ""
    saved_files = []

    if message.photo:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_ext = Path(file_info.file_path).suffix or ".jpg"
        file_path = FEEDBACK_DIR / f"feedback_{order_id}_{uuid.uuid4().hex[:8]}{file_ext}"
        await bot.download_file(file_info.file_path, destination=file_path)
        saved_files.append(str(file_path))

    elif message.voice or message.audio:
        audio_obj = message.voice or message.audio
        file_info = await bot.get_file(audio_obj.file_id)
        file_ext = Path(file_info.file_path).suffix or ".ogg"
        file_path = FEEDBACK_DIR / f"feedback_{order_id}_{uuid.uuid4().hex[:8]}{file_ext}"
        await bot.download_file(file_info.file_path, destination=file_path)
        saved_files.append(str(file_path))

    elif message.document:
        file_info = await bot.get_file(message.document.file_id)
        file_ext = Path(message.document.file_name).suffix if message.document.file_name else ".file"
        file_path = FEEDBACK_DIR / f"feedback_{order_id}_{uuid.uuid4().hex[:8]}{file_ext}"
        await bot.download_file(file_info.file_path, destination=file_path)
        saved_files.append(str(file_path))

    async with async_session_maker() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()

        if order:
            order.confirmation_status = ConfirmationStatus.CHANGES_REQUESTED.value
            order.synced = False
            if feedback_text:
                order.feedback_text = feedback_text

            if saved_files:
                existing_files = []
                if order.feedback_files:
                    try:
                        existing_files = json.loads(order.feedback_files)
                    except Exception:
                        existing_files = []
                existing_files.extend(saved_files)
                order.feedback_files = json.dumps(existing_files, ensure_ascii=False)

            await session.commit()
            await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

    await message.answer(t("changes_accepted", lang))
    await _update_group_caption_after_feedback(bot, data, t("changes_caption_suffix", lang))
    await state.clear()
