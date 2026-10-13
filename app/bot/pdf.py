"""Buyurtma PDF faylini foydalanuvchiga (tanlagan tilida) yuborish."""

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.bot.keyboards import get_order_inline_keyboard
from app.i18n import t
from app.models import Order
from app.translate import translate_text
from app.utils import clean_text_for_odoo

logger = logging.getLogger("aiogram_bot")


async def send_order_pdf_to_user(bot: Bot, chat_id: int, order: Order, lang: str = "uz") -> bool:
    """Buyurtma PDF faylini foydalanuvchiga yuborish yordamchi funksiyasi."""
    try:
        formatted_msg = clean_text_for_odoo(order.custom_message)

        if formatted_msg:
            caption = await translate_text(formatted_msg, lang)
        else:
            doc_name = order.doc_id if order.doc_id else f"#{order.id}"
            caption = t("pdf_caption_fallback", lang, doc_name=doc_name)

        custom_filename = f"Tijorat taklifi - {order.doc_id if order.doc_id else order.id}.pdf"

        if order.pdf_path and Path(order.pdf_path).exists():
            file_bytes = Path(order.pdf_path).read_bytes()
            document = BufferedInputFile(file_bytes, filename=custom_filename)

            await bot.send_document(
                chat_id=chat_id,
                document=document,
                caption=caption,
                parse_mode="HTML",
                reply_markup=get_order_inline_keyboard(order.id, lang),
            )
            return True
        else:
            logger.warning("PDF fayl topilmadi: %s", order.pdf_path)
    except Exception as e:
        logger.exception("PDF yuborishda xatolik (order_id=%s): %s", order.id, e)
    return False
