"""
Audio transcription/synthesis service — faqat OpenAI (Gemini butunlay olib tashlandi).
STT primary: OPENAI_WHISPER_MODEL (in-memory BytesIO — no disk I/O, no format conversion).
STT fallback: "whisper-1" (boshqa OpenAI modeli — birinchisi xato/bo'sh qaytarsa sinaladi).
TTS: OpenAI (matnni Telegram ovozli xabar sifatida yuborish uchun OGG/Opus qaytaradi).
"""
from __future__ import annotations

import asyncio
import io
import logging

from app.config import settings

log = logging.getLogger(__name__)

_WHISPER_TIMEOUT_SECONDS = 30
_WHISPER_FALLBACK_MODEL = "whisper-1"

_openai_client = None


def _get_openai_client():
    """Lazy singleton OpenAI async client (testlarda monkeypatch qilish oson)."""
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI

        _openai_client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=_WHISPER_TIMEOUT_SECONDS,
            max_retries=0,  # retry'lar bizning orchestration darajasida boshqariladi
        )
    return _openai_client


async def _transcribe_whisper(audio_bytes: bytes, mime_type: str, model: str,
                              language: str | None = None) -> str:
    """OpenAI Whisper orqali transkripsiya — to'liq in-memory (BytesIO), disk I/O yo'q.

    Telegram ovoz xabarlari ogg/opus — Whisper buni to'g'ridan-to'g'ri qabul qiladi,
    ffmpeg konvertatsiyasi shart emas. Muvaffaqiyatsizlik/bo'sh natijada '' qaytaradi
    (chaqiruvchi boshqa modelga fallback qiladi).
    """
    client = _get_openai_client()
    file_obj = io.BytesIO(audio_bytes)
    # Whisper kutgan (filename, fileobj, content_type) formati — serialize shart emas
    ext = "ogg" if "ogg" in mime_type else mime_type.split("/")[-1]
    kwargs: dict = {
        "model": model,
        "file": (f"voice.{ext}", file_obj, mime_type),
        "response_format": "text",  # to'g'ridan-to'g'ri matn — json parse qilinmaydi
        "temperature": 0,           # deterministik transkripsiya
    }
    if language:
        kwargs["language"] = language

    try:
        result = await asyncio.wait_for(
            client.audio.transcriptions.create(**kwargs),
            timeout=_WHISPER_TIMEOUT_SECONDS,
        )
        if isinstance(result, str):
            return result.strip()
        return str(getattr(result, "text", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        # Har qanday xato (kvota, tarmoq, timeout, bo'sh javob) — '' qaytaramiz,
        # orchestration qatlami boshqa modelga o'tadi
        log.warning("OpenAI Whisper (%s) transkript xatosi: %s", model, str(e)[:160])
        return ""


async def transcribe_audio_bytes(audio_bytes: bytes, mime_type: str = "audio/ogg",
                                 language: str | None = None) -> str:
    """Ovozli xabarni matnga o'girish — API shartnomasi: '' (bo'sh) = muvaffaqiyatsiz.

    Faqat OpenAI Whisper: avval OPENAI_WHISPER_MODEL, u xato/bo'sh qaytarsa
    boshqa OpenAI modeli ("whisper-1") bilan qayta sinaladi."""
    if not settings.OPENAI_API_KEY:
        log.error("Audio transcription failed: OPENAI_API_KEY sozlanmagan")
        return ""

    for model in dict.fromkeys([settings.OPENAI_WHISPER_MODEL, _WHISPER_FALLBACK_MODEL]):
        try:
            text = await _transcribe_whisper(audio_bytes, mime_type, model, language=language)
        except Exception as e:  # noqa: BLE001
            log.warning("OpenAI Whisper (%s) kutilmagan xato: %s", model, str(e)[:120])
            text = ""
        if text:
            log.info("Audio transcribed via OpenAI Whisper (%s): %d chars", model, len(text))
            return text
        log.warning("OpenAI Whisper (%s) natija bermadi", model)

    log.error("Audio transcription failed: barcha OpenAI Whisper modellari ishlamadi yoki bo'sh qaytardi")
    return ""


_TTS_TIMEOUT_SECONDS = 30
_TTS_MAX_CHARS = 1000  # OpenAI TTS limiti 4096, lekin vazifa xabarlari uchun shuncha yetarli


async def synthesize_speech(text: str) -> bytes:
    """Matnni OGG/Opus ovozli xabarga aylantiradi (Telegram send_voice bilan
    to'g'ridan-to'g'ri mos format). OPENAI_API_KEY sozlanmagan yoki xato yuz
    bersa bo'sh bytes ('') qaytaradi — chaqiruvchi matnli xabarga qaytishi kerak."""
    text = (text or "").strip()
    if not text or not settings.OPENAI_API_KEY:
        return b""
    client = _get_openai_client()
    try:
        response = await asyncio.wait_for(
            client.audio.speech.create(
                model=settings.OPENAI_TTS_MODEL,
                voice=settings.OPENAI_TTS_VOICE,
                input=text[:_TTS_MAX_CHARS],
                response_format="opus",
            ),
            timeout=_TTS_TIMEOUT_SECONDS,
        )
        return await response.aread()
    except Exception as e:  # noqa: BLE001
        log.warning("OpenAI TTS xatosi: %s", str(e)[:160])
        return b""