"""
Boshqa hech qanday handlerga mos kelmagan erkin xabarlar uchun umumiy handler.
`handle_unhandled_message` StateFilter(None) bilan ISTALGAN xabarni ushlaydi -- shu
sabab bu router har doim ENG OXIRIDA ro'yxatdan o'tkazilishi kerak (aks holda boshqa
handlerlarni "yeb qo'yishi" mumkin).
"""

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from app.bot.keyboards import get_menu_reply_keyboard
from app.database import async_session_maker
from app.i18n import t
from app.utils import get_user_language

router = Router(name="fallback")


@router.message(StateFilter(None))
async def handle_unhandled_message(message: Message):
    """
    Foydalanuvchi buyurtma jarayonidan tashqarida botga erkin xabar yozganda, uni
    hech qaerga uzatmasdan, menyudan kerakli bo'limni tanlashni so'raydi.

    Faqat shaxsiy (private) chatlardan keladigan xabarlar uchun ishlaydi -- bot a'zo
    bo'lgan guruhlardagi (masalan, call-center guruhi) xabarlarga umuman javob bermaydi.
    """
    if message.chat.type != "private":
        return

    async with async_session_maker() as session:
        lang = await get_user_language(session, message.chat.id)

    await message.answer(t("menu_title", lang), reply_markup=get_menu_reply_keyboard(lang))
