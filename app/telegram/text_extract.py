from __future__ import annotations

import io

from aiogram import Bot
from aiogram.types import Message

from app.services.audio_service import transcribe_audio_bytes


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
        await bot.download(message.voice, destination=buf)
        return await transcribe_audio_bytes(buf.getvalue(), mime_type="audio/ogg")
    return None