from __future__ import annotations

import io
import logging

from aiogram import Bot
from aiogram.types import Message

from app.services.audio_service import transcribe_audio_bytes

log = logging.getLogger(__name__)


async def get_message_text(message: Message, bot: Bot) -> str | None:
    """Xabardan matnni oladi: oddiy matn/caption bo'lsa to'g'ridan-to'g'ri, ovozli xabar
    bo'lsa Telegram'dan yuklab olib to'liq in-memory (BytesIO) transkript qiladi.

    Telegram ovoz xabarlari ogg/opus formatda — Whisper buni to'g'ridan-to'g'ri qabul
    qiladi, konvertatsiya yoki vaqtinchalik fayl (disk I/O) kerak emas."""
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.voice:
        buf = io.BytesIO()
        try:
            await bot.download(message.voice, destination=buf)
        except Exception:
            # Yuklab bo'lmadi (Telegram/tarmoq) — bo'sh matn qaytadi va chaqiruvchi "ovozli
            # xabarni tushunolmadim" deb javob beradi; ilgari xato handler'ni jimgina to'xtatardi
            log.warning("ovozli xabarni Telegram'dan yuklab bo'lmadi", exc_info=True)
            return ""
        return await transcribe_audio_bytes(buf.getvalue(), mime_type="audio/ogg")
    return None