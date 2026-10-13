"""
Guruh xabarini tahlil qilish quvuri (2026-10-12): xabardagi media tayyorlanadi va xabar
turiga qarab kerakli modelga beriladi. `app/bot/handlers/group_monitor.py` shundan foydalanadi.

  Matn            -> asosiy model (`GROUP_AI_PROVIDER` / `GROUP_AI_MODEL`, odatda lokal Gemma)
  Rasm            -> o'sha model, rasmning o'zi bilan birga (`GROUP_IMAGE_ANALYSIS_ENABLED`)
  Ovozli xabar,
  audio           -> nutq matnga o'giriladi (`app/stt.py`: `GROUP_STT_PROVIDER` -- lokal yoki
                     OpenAI), matni `GROUP_MEDIA_AI_PROVIDER` modeli bilan tasniflanadi
                     (bo'sh bo'lsa asosiy model)
  Video,
  dumaloq video   -> ovozi matnga o'giriladi + `GROUP_VIDEO_FRAMES` ta kadr rasm sifatida
                     qo'shiladi, tasnif -- ovozli xabardagi kabi

Tasniflashning o'zi (prompt, sxema, ishonch chegarasi, kalit-so'zlarga qaytish) --
`app/group_ai.py`. Bu yerdagi hech bir funksiya xato tashlamaydi: media yuklanmasa yoki
o'qilmasa xabar shunchaki matni bo'yicha tahlil qilinadi.
"""

import asyncio
import io
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import Message

from app import group_ai, stt
from app.config import settings
from app.group_ai import MessageAnalysis

logger = logging.getLogger("aiogram_bot")

# Bot API orqali bot 20 MB dan katta faylni yuklab ololmaydi
_BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024
_IMAGE_DOCUMENT_LIMIT = 10 * 1024 * 1024
_SPEECH_TYPES = ("voice", "audio", "video", "video_note")

# (xabardagi maydon = media turi, logdagi nomi, fayl kengaytmasi noma'lum bo'lsa)
_SPEECH_MEDIA = (
    ("voice", "ovozli xabar", ".ogg"),
    ("video_note", "video xabar", ".mp4"),
    ("video", "video", ".mp4"),
    ("audio", "audio", ".mp3"),
)
_MIME_SUFFIXES = {
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/ogg": ".ogg", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "audio/flac": ".flac", "video/mp4": ".mp4", "video/quicktime": ".mov",
    "video/webm": ".webm",
}


@dataclass(frozen=True)
class MediaContent:
    """Xabardan tahlil uchun olingan narsa."""

    media_type: str = "text"  # text | photo | voice | audio | video | video_note
    transcript: str | None = None  # nutq matni
    images: tuple[bytes, ...] = ()  # JPEG: surat yoki video kadrlari

    @property
    def is_speech(self) -> bool:
        return self.media_type in _SPEECH_TYPES


def get_speech_media(message: Message):
    """Xabardagi nutq bo'lishi mumkin bo'lgan fayl: (media turi, nomi, fayl, kengaytma) yoki None."""
    for media_type, label, default_suffix in _SPEECH_MEDIA:
        media = getattr(message, media_type, None)
        if media is not None:
            suffix = Path(getattr(media, "file_name", None) or "").suffix.lower()
            suffix = suffix or _MIME_SUFFIXES.get(getattr(media, "mime_type", None) or "", default_suffix)
            return media_type, label, media, suffix
    return None


def get_media_type(message: Message) -> str:
    found = get_speech_media(message)
    if found:
        return found[0]
    return "photo" if _get_image(message) is not None else "text"


def _get_image(message: Message):
    """Xabardagi surat: mos o'lchamdagi `PhotoSize` yoki rasm fayli (hujjat qilib yuborilgan)."""
    sizes = getattr(message, "photo", None)
    if sizes:
        # Kerakli aniqlikka yetadigan eng kichik nusxa; hammasi kichik bo'lsa -- eng kattasi
        enough = [size for size in sizes if max(size.width, size.height) >= settings.GROUP_IMAGE_MAX_SIDE]
        return enough[0] if enough else sizes[-1]
    document = getattr(message, "document", None)
    if document is not None and (getattr(document, "mime_type", None) or "").startswith("image/"):
        if (getattr(document, "file_size", None) or 0) <= _IMAGE_DOCUMENT_LIMIT:
            return document
    return None


def prepare_image(data: bytes) -> bytes:
    """Rasmni model uchun tayyorlaydi: to'g'ri burilgan, `GROUP_IMAGE_MAX_SIDE`dan katta emas, JPEG."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((settings.GROUP_IMAGE_MAX_SIDE, settings.GROUP_IMAGE_MAX_SIDE))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def extract_frames(path: str, count: int) -> tuple[bytes, ...]:
    """Videodan bir tekis oraliqda `count` ta kadr oladi (JPEG). Tasvir yo'lagi bo'lmasa bo'sh."""
    import av

    frames: list[bytes] = []
    with av.open(path) as container:
        stream = next((stream for stream in container.streams if stream.type == "video"), None)
        if stream is None:
            return ()
        duration = container.duration or 0  # mikrosoniyalarda (av.time_base)
        for index in range(count):
            if duration:
                # Eng yaqin oldingi kalit-kadrga o'tadi -- butun videoni dekodlash shart emas
                container.seek(int(duration * (index + 0.5) / count))
            frame = next(container.decode(stream), None)
            if frame is None:
                break
            image = frame.to_image().convert("RGB")
            image.thumbnail((settings.GROUP_IMAGE_MAX_SIDE, settings.GROUP_IMAGE_MAX_SIDE))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
            frames.append(buffer.getvalue())
            if not duration:
                break  # davomiyligi noma'lum -- faqat birinchi kadr
    return tuple(frames)


async def _extract_speech(bot: Bot, message: Message, with_frames: bool) -> MediaContent:
    media_type, label, media, suffix = get_speech_media(message)
    want_frames = with_frames and media_type in ("video", "video_note") and settings.GROUP_VIDEO_FRAMES > 0
    if not settings.GROUP_STT_ENABLED and not want_frames:
        return MediaContent(media_type)

    size = getattr(media, "file_size", None) or 0
    duration = getattr(media, "duration", None) or 0
    if size > _BOT_API_DOWNLOAD_LIMIT or duration > settings.GROUP_STT_MAX_DURATION_SECONDS:
        logger.info(
            "%s tahlil qilinmadi: juda katta yoki uzun (%.1f MB, %s s, chat_id=%s)",
            label, size / 1024 / 1024, duration, message.chat.id,
        )
        return MediaContent(media_type)

    fd, path = tempfile.mkstemp(prefix="tg_media_", suffix=suffix)
    os.close(fd)
    transcript: str | None = None
    frames: tuple[bytes, ...] = ()
    try:
        await bot.download(media, destination=path)
        if settings.GROUP_STT_ENABLED:
            started = time.monotonic()
            transcript = await stt.transcribe_file(path)
            logger.info(
                "%s matnga o'girildi: %s s yozuv, %.1f s ichida, %s belgi",
                label, duration, time.monotonic() - started, len(transcript or ""),
            )
        if want_frames:
            try:
                frames = await asyncio.to_thread(extract_frames, path, settings.GROUP_VIDEO_FRAMES)
            except Exception:
                logger.exception("Videodan kadr olishda xatolik (chat_id=%s)", message.chat.id)
    except Exception:
        logger.exception("%s ni tahlilga tayyorlashda xatolik (chat_id=%s)", label, message.chat.id)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return MediaContent(media_type, transcript or None, frames)


async def _extract_image(bot: Bot, message: Message, image) -> MediaContent:
    try:
        buffer = io.BytesIO()
        await bot.download(image, destination=buffer)
        prepared = await asyncio.to_thread(prepare_image, buffer.getvalue())
        return MediaContent("photo", images=(prepared,))
    except Exception:
        logger.exception("Rasmni tahlilga tayyorlashda xatolik (chat_id=%s)", message.chat.id)
        return MediaContent("photo")


async def extract_media(bot: Bot, message: Message, *, with_images: bool = True) -> MediaContent:
    """Xabardagi mediani tahlilga tayyorlaydi: nutq -> matn, surat va video kadrlari -> JPEG.

    `with_images=False` -- rasm/kadrlar kerak emas (xodim xabari: undan faqat "N daqiqada
    javob beraman" va "tovar yo'q" qidiriladi, buning uchun matn yetarli)."""
    with_images = with_images and settings.GROUP_IMAGE_ANALYSIS_ENABLED
    if get_speech_media(message) is not None:
        return await _extract_speech(bot, message, with_images)
    image = _get_image(message)
    if image is None:
        return MediaContent()
    if not with_images:
        return MediaContent("photo")
    return await _extract_image(bot, message, image)


async def analyze(
    text: str,
    is_staff: bool,
    *,
    context: str = "",
    addressed_to_staff: bool = False,
    media: MediaContent = MediaContent(),
) -> MessageAnalysis:
    """Xabarni tasniflaydi. `text` -- yozilgan matn va (bo'lsa) nutq matni. Ovozli xabar, audio
    va video `GROUP_MEDIA_AI_PROVIDER` modeliga, qolgan hammasi asosiy modelga boradi."""
    provider = model = None
    if media.is_speech and settings.GROUP_MEDIA_AI_PROVIDER:
        provider, model = settings.GROUP_MEDIA_AI_PROVIDER, settings.GROUP_MEDIA_AI_MODEL
    return await group_ai.analyze_message(
        text,
        is_staff,
        context=context,
        addressed_to_staff=addressed_to_staff,
        images=media.images,
        provider=provider,
        model=model,
    )
