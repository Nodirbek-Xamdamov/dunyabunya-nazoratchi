"""
Odoo'dan kelgan (o'zbek tilidagi) dinamik matnni foydalanuvchi tanlagan tilga tarjima
qilish. deep-translator kutubxonasi (Google Translate ochiq web endpointi) ishlatiladi --
API kalit talab qilmaydi.
"""

import asyncio
import logging

from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

_SUPPORTED = {"uz", "ru"}


def _translate_sync(text: str, target_lang: str) -> str:
    return GoogleTranslator(source="auto", target=target_lang).translate(text)


async def translate_text(text: str, target_lang: str | None) -> str:
    """
    `text`ni `target_lang`ga tarjima qiladi (Odoo matni asli o'zbek tilida deb qabul
    qilinadi, shu sabab target_lang == "uz" bo'lsa tarjima qilinmaydi). deep-translator
    tarmoqqa bloklovchi (sync) so'rov yuboradi, shu sabab asyncio.to_thread orqali
    ishga tushiriladi -- event loop bloklanmasligi uchun. Tarjima xizmati ishlamay
    qolsa, xatoni yutib, asl matnni qaytaradi (foydalanuvchi xabarsiz qolmasligi uchun).
    """
    if not text or not target_lang or target_lang not in _SUPPORTED or target_lang == "uz":
        return text

    try:
        return await asyncio.to_thread(_translate_sync, text, target_lang)
    except Exception:
        logger.exception("Matnni '%s' tiliga tarjima qilishda xatolik, asl matn qaytarildi", target_lang)
        return text
