"""/start komandasi, til tanlash va tasdiqlanmagan buyurtmalarni yuborish."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy import select

from app.bot.keyboards import get_menu_reply_keyboard
from app.bot.pdf import send_order_pdf_to_user
from app.config import settings
from app.database import async_session_maker
from app.i18n import CHOOSE_LANGUAGE_TEXT, LANGUAGES, get_language_keyboard, t
from app.models import ConfirmationStatus, Order
from app.utils import normalize_username, upsert_telegram_user

logger = logging.getLogger("aiogram_bot")

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """/start bosilganda avval tilni tanlash so'raladi.

    Agar foydalanuvchi biror FSM oqimi o'rtasida (masalan "Buyurtma berish" -- lokatsiya
    so'ralayotgan bosqichda) /start bossa: avvalgi holat tozalanadi va o'sha oqimga
    tegishli ReplyKeyboardMarkup (masalan "joylashuv yuborish" tugmalari) pastda osilib
    qolmasligi uchun aniq olib tashlanadi -- CHOOSE_LANGUAGE_TEXT xabari INLINE klaviatura
    bilan yuborilgani uchun (pastdagi Reply klaviaturani avtomatik almashtira olmaydi)
    shu tozalash alohida qadam sifatida bajariladi (2026-08-02 bug fix)."""
    if not message.from_user or not message.from_user.username:
        await message.answer(t("username_required", "uz"))
        return

    normalized = normalize_username(message.from_user.username)

    if normalized == normalize_username(settings.SUPPORT_USERNAME):
        # Support akkaunt botni sinash uchun /start bossa ham hech narsa yubormaymiz --
        # bu chat faqat mijoz xabarlari haqidagi bildirishnomalar uchun toza qolishi kerak.
        return

    if await state.get_state() is not None:
        await state.clear()
        await message.answer(t("flow_reset_on_start", "uz"), reply_markup=ReplyKeyboardRemove())

    await message.answer(CHOOSE_LANGUAGE_TEXT, reply_markup=get_language_keyboard())


@router.callback_query(F.data.startswith("lang_"))
async def process_language_selection(callback: CallbackQuery, bot: Bot):
    """Til tanlangandan so'ng: tilni saqlaydi, tasdiqlanmagan PDF larni shu tilda yuboradi va menyuni ko'rsatadi."""
    lang = callback.data.split("_", 1)[1]
    if lang not in LANGUAGES:
        lang = "uz"

    user = callback.from_user
    if not user or not user.username:
        await callback.answer()
        return

    normalized = normalize_username(user.username)
    chat_id = callback.message.chat.id

    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning("Til tanlash tugmalarini o'chirishda xatolik: %s", exc)

    await bot.send_message(
        chat_id, t("welcome", lang, name=user.first_name), reply_markup=get_menu_reply_keyboard(lang)
    )

    async with async_session_maker() as session:
        # Guruh a'zoligini keyinchalik aniqlash uchun foydalanuvchini alohida saqlaymiz
        await upsert_telegram_user(session, chat_id=chat_id, username=user.username, language=lang)
        await session.commit()

        # Faqat ushbu username'ga tegishli va hali tasdiqlanmagan/rad etilmagan buyurtmalarni olamiz
        result = await session.execute(
            select(Order).where(
                Order.normalized_username == normalized,
                Order.confirmation_status == ConfirmationStatus.PENDING.value
            )
        )
        orders = result.scalars().all()

        sent_count = 0
        for order in orders:
            order.chat_id = chat_id

            # notified holatidan qat'i nazar, chat_id biriktirib PDF yuboramiz
            sent = await send_order_pdf_to_user(bot, chat_id, order, lang)
            if sent:
                order.notified = True
                sent_count += 1

        await session.commit()

        if sent_count > 0:
            logger.info("Foydalanuvchi %s uchun %s ta PDF yuborildi (til=%s).", normalized, sent_count, lang)
        else:
            logger.info("Foydalanuvchi %s uchun bazada kutilayotgan buyurtmalar topilmadi.", normalized)
            await bot.send_message(chat_id, t("no_pending_orders", lang))
