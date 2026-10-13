"""Regression tests for audio STT/TTS: faqat OpenAI (Gemini butunlay olib tashlandi).
STT: OPENAI_WHISPER_MODEL asosiy, xato/bo'sh bo'lsa "whisper-1" bilan qayta sinaladi.

Barcha testlar mok'larga asoslangan — tarmoqqa (OpenAI API'ga) chiqilmaydi.
API shartnomasi: transcribe_audio* -> str ('' = muvaffaqiyatsiz/bo'sh).
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

import app.services.audio_service as audio_service


# ── Fake OpenAI client (Whisper) ──────────────────────────────────────────────

class _FakeTranscriptionsAPI:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.result


class _FakeSpeechResponse:
    def __init__(self, data: bytes):
        self._data = data

    async def aread(self) -> bytes:
        return self._data


class _FakeSpeechAPI:
    def __init__(self, result: bytes | None = None, exc=None):
        self.result = result
        self.exc = exc
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.exc:
            raise self.exc
        return _FakeSpeechResponse(self.result or b"")


class _FakeAudioAPI:
    def __init__(self, result=None, exc=None, speech_result: bytes | None = None, speech_exc=None):
        self.transcriptions = _FakeTranscriptionsAPI(result=result, exc=exc)
        self.speech = _FakeSpeechAPI(result=speech_result, exc=speech_exc)


class _FakeClient:
    def __init__(self, result=None, exc=None, speech_result: bytes | None = None, speech_exc=None):
        self.audio = _FakeAudioAPI(result=result, exc=exc, speech_result=speech_result, speech_exc=speech_exc)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Har testda haqiqiy API kalitlari ishlatilmasligi uchun sozlamalarni tozalaymiz."""
    monkeypatch.setattr(audio_service.settings, "OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(audio_service.settings, "OPENAI_WHISPER_MODEL", "gpt-4o-transcribe")
    yield


# ── _transcribe_whisper ──────────────────────────────────────────────────────

async def test_whisper_success_returns_text(monkeypatch):
    fake = _FakeClient(result="Salom, bugun ombor qoldiqlarini tekshiramiz")
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)

    text = await audio_service._transcribe_whisper(b"fake-audio-bytes", "audio/ogg", "gpt-4o-transcribe")

    assert text == "Salom, bugun ombor qoldiqlarini tekshiramiz"
    # To'g'ri API parametrlari: model, in-memory file (BytesIO), response_format, temperature
    kw = fake.audio.transcriptions.last_kwargs
    assert kw["model"] == "gpt-4o-transcribe"
    assert kw["response_format"] == "text"
    assert kw["temperature"] == 0
    fname, fobj, ctype = kw["file"]
    assert fname == "voice.ogg"
    assert ctype == "audio/ogg"
    assert isinstance(fobj, io.BytesIO)
    assert fobj.getvalue() == b"fake-audio-bytes"


async def test_whisper_success_with_json_object_result(monkeypatch):
    """SDK ba'zi hollarda .text atributli obyekt qaytarishi mumkin."""
    class _Resp:
        text = "Matn obyekt sifatida"

    fake = _FakeClient(result=_Resp())
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    assert await audio_service._transcribe_whisper(b"x", "audio/mpeg", "gpt-4o-transcribe") == "Matn obyekt sifatida"


async def test_whisper_empty_result_returns_empty(monkeypatch):
    fake = _FakeClient(result="")
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    assert await audio_service._transcribe_whisper(b"x", "audio/ogg", "gpt-4o-transcribe") == ""


async def test_whisper_api_error_returns_empty(monkeypatch):
    fake = _FakeClient(exc=RuntimeError("401 invalid api key"))
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    assert await audio_service._transcribe_whisper(b"x", "audio/ogg", "gpt-4o-transcribe") == ""


async def test_whisper_language_passed_through(monkeypatch):
    fake = _FakeClient(result="Ruscha matn")
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    await audio_service._transcribe_whisper(b"x", "audio/ogg", "gpt-4o-transcribe", language="uz")
    assert fake.audio.transcriptions.last_kwargs.get("language") == "uz"


# ── transcribe_audio_bytes (orchestration: asosiy model -> "whisper-1" fallback) ──

async def test_primary_model_success_skips_fallback_model(monkeypatch):
    calls = []

    async def _fake_whisper(b, m, model, language=None):
        calls.append(model)
        return "Asosiy model natijasi"

    monkeypatch.setattr(audio_service, "_transcribe_whisper", _fake_whisper)

    text = await audio_service.transcribe_audio_bytes(b"x", "audio/ogg")
    assert text == "Asosiy model natijasi"
    assert calls == ["gpt-4o-transcribe"]  # fallback modeli chaqirilmagan


async def test_primary_empty_falls_back_to_whisper1(monkeypatch):
    calls = []

    async def _fake_whisper(b, m, model, language=None):
        calls.append(model)
        return "" if model == "gpt-4o-transcribe" else "Fallback natijasi"

    monkeypatch.setattr(audio_service, "_transcribe_whisper", _fake_whisper)

    assert await audio_service.transcribe_audio_bytes(b"x") == "Fallback natijasi"
    assert calls == ["gpt-4o-transcribe", "whisper-1"]


async def test_primary_error_falls_back_to_whisper1(monkeypatch):
    calls = []

    async def _fake_whisper(b, m, model, language=None):
        calls.append(model)
        if model == "gpt-4o-transcribe":
            raise RuntimeError("network down")
        return "Fallback natijasi"

    monkeypatch.setattr(audio_service, "_transcribe_whisper", _fake_whisper)
    assert await audio_service.transcribe_audio_bytes(b"x") == "Fallback natijasi"
    assert calls == ["gpt-4o-transcribe", "whisper-1"]


async def test_both_models_fail_returns_empty(monkeypatch):
    async def _fake_whisper(b, m, model, language=None):
        return ""

    monkeypatch.setattr(audio_service, "_transcribe_whisper", _fake_whisper)
    assert await audio_service.transcribe_audio_bytes(b"x") == ""


async def test_same_primary_and_fallback_model_tried_only_once(monkeypatch):
    """OPENAI_WHISPER_MODEL allaqachon 'whisper-1' bo'lsa, ikki marta bir xil
    modelni chaqirmasligi kerak (dict.fromkeys dedup)."""
    monkeypatch.setattr(audio_service.settings, "OPENAI_WHISPER_MODEL", "whisper-1")
    calls = []

    async def _fake_whisper(b, m, model, language=None):
        calls.append(model)
        return ""

    monkeypatch.setattr(audio_service, "_transcribe_whisper", _fake_whisper)
    assert await audio_service.transcribe_audio_bytes(b"x") == ""
    assert calls == ["whisper-1"]


async def test_no_openai_key_returns_empty_without_calling_whisper(monkeypatch):
    monkeypatch.setattr(audio_service.settings, "OPENAI_API_KEY", "")
    whisper_called = {"v": False}

    async def _fake_whisper(b, m, model, language=None):
        whisper_called["v"] = True
        return "hech qachon"

    monkeypatch.setattr(audio_service, "_transcribe_whisper", _fake_whisper)
    assert await audio_service.transcribe_audio_bytes(b"x") == ""
    assert whisper_called["v"] is False


# ── synthesize_speech (TTS: matn -> OGG/Opus) ────────────────────────────────

async def test_synthesize_speech_returns_ogg_bytes(monkeypatch):
    fake = _FakeClient(speech_result=b"fake-ogg-opus-bytes")
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    audio = await audio_service.synthesize_speech("Yangi vazifa: hisobot tayyorlang.")
    assert audio == b"fake-ogg-opus-bytes"
    assert fake.audio.speech.last_kwargs["response_format"] == "opus"
    assert fake.audio.speech.last_kwargs["model"] == audio_service.settings.OPENAI_TTS_MODEL
    assert fake.audio.speech.last_kwargs["voice"] == audio_service.settings.OPENAI_TTS_VOICE


async def test_synthesize_speech_empty_text_returns_empty_no_call(monkeypatch):
    fake = _FakeClient(speech_result=b"should-not-be-used")
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    assert await audio_service.synthesize_speech("") == b""
    assert fake.audio.speech.last_kwargs is None


async def test_synthesize_speech_no_openai_key_returns_empty(monkeypatch):
    monkeypatch.setattr(audio_service.settings, "OPENAI_API_KEY", "")
    fake = _FakeClient(speech_result=b"should-not-be-used")
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    assert await audio_service.synthesize_speech("Salom") == b""


async def test_synthesize_speech_api_error_returns_empty_not_raises(monkeypatch):
    fake = _FakeClient(speech_exc=RuntimeError("OpenAI TTS xatosi"))
    monkeypatch.setattr(audio_service, "_get_openai_client", lambda: fake)
    assert await audio_service.synthesize_speech("Salom") == b""


# ── get_message_text (downstream zanjir) ─────────────────────────────────────

async def test_get_message_text_voice_passes_bytes_in_memory(monkeypatch):
    """Ovozli xabar: bytes to'g'ridan-to'g'ri uzatiladi — vaqtinchalik fayl yozilmaydi."""
    from app.telegram import text_extract

    class _FakeVoice:
        pass

    class _FakeMessage:
        text = None
        caption = None
        voice = _FakeVoice()

    class _FakeBot:
        async def download(self, voice, destination):
            destination.write(b"real-ogg-bytes")

    captured = {}

    async def _fake_transcribe(audio_bytes, mime_type="audio/ogg", language=None):
        captured["bytes"] = audio_bytes
        captured["mime"] = mime_type
        return "Nodirga ombor qoldiqlarini tekshirishni topshir"

    monkeypatch.setattr(text_extract, "transcribe_audio_bytes", _fake_transcribe)

    result = await text_extract.get_message_text(_FakeMessage(), _FakeBot())
    assert result == "Nodirga ombor qoldiqlarini tekshirishni topshir"
    assert captured["bytes"] == b"real-ogg-bytes"
    assert captured["mime"] == "audio/ogg"


async def test_get_message_text_plain_text_not_transcribed(monkeypatch):
    from app.telegram import text_extract

    class _FakeMessage:
        text = "Sotuv statistikasi qanday?"
        caption = None
        voice = None

    called = {"v": False}

    async def _fake_transcribe(audio_bytes, mime_type="audio/ogg", language=None):
        called["v"] = True
        return ""

    monkeypatch.setattr(text_extract, "transcribe_audio_bytes", _fake_transcribe)
    assert await text_extract.get_message_text(_FakeMessage(), None) == "Sotuv statistikasi qanday?"
    assert called["v"] is False


def test_tempfile_no_longer_used_in_voice_path():
    """text_extract endi tempfile/disk I/O ishlatmaydi (faqat BytesIO)."""
    from app.telegram import text_extract

    src = Path(text_extract.__file__).read_text(encoding="utf-8")
    assert "tempfile" not in src
    assert "NamedTemporaryFile" not in src
    assert "transcribe_audio_bytes" in src