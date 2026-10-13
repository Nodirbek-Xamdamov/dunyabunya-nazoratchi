"""app.stt -- nutqni matnga o'girish yo'lini tanlash (`GROUP_STT_PROVIDER`): lokal model yoki
OpenAI transkripsiya API'si. Haqiqiy model ham, haqiqiy API ham chaqirilmaydi."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app import stt


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(stt.settings, "GROUP_STT_PROVIDER", "openai")
    monkeypatch.setattr(stt.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(stt.settings, "OPENAI_STT_MODEL", "whisper-1")
    monkeypatch.setattr(stt.settings, "OPENAI_STT_LANGUAGE", "")


def _fake_openai(monkeypatch, *results):
    """`audio.transcriptions.create` navbat bilan `results`ni qaytaradi (xato bo'lsa -- tashlaydi)."""
    create = AsyncMock(side_effect=[
        result if isinstance(result, Exception) else SimpleNamespace(text=result) for result in results
    ])
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    monkeypatch.setattr(stt, "_get_openai_client", lambda: client)
    return create


def _file(tmp_path, name, size=1000):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return str(path)


def _bad_request():
    request = httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
    return openai.BadRequestError("Invalid file format", response=httpx.Response(400, request=request), body=None)


async def test_telegram_voice_is_uploaded_as_is_under_ogg_name(monkeypatch, tmp_path):
    create = _fake_openai(monkeypatch, "  narxi qancha  ")
    extract = AsyncMock()
    monkeypatch.setattr(stt, "extract_wav", extract)

    assert await stt.transcribe_file(_file(tmp_path, "tg_media_1.oga")) == "narxi qancha"

    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "whisper-1"
    assert kwargs["file"] == ("audio.ogg", b"x" * 1000)
    assert "sement" in kwargs["prompt"]  # soha lug'ati
    assert "language" not in kwargs  # til aniqlash modelga qoldirilgan
    extract.assert_not_called()


async def test_language_is_sent_only_when_configured(monkeypatch, tmp_path):
    create = _fake_openai(monkeypatch, "matn")
    monkeypatch.setattr(stt.settings, "OPENAI_STT_LANGUAGE", "uz")
    monkeypatch.setattr(stt.settings, "OPENAI_STT_MODEL", "gpt-4o-transcribe")
    await stt.transcribe_file(_file(tmp_path, "a.mp3"))
    assert create.await_args.kwargs["language"] == "uz" and create.await_args.kwargs["model"] == "gpt-4o-transcribe"


async def test_big_video_is_reduced_to_its_audio_track(monkeypatch, tmp_path):
    create = _fake_openai(monkeypatch, "mana qarang")
    monkeypatch.setattr(stt, "extract_wav", lambda path: b"W" * 40_000)

    assert await stt.transcribe_file(_file(tmp_path, "clip.mp4", size=3 * 1024 * 1024)) == "mana qarang"
    assert create.await_args.kwargs["file"] == ("audio.wav", b"W" * 40_000)


async def test_unsupported_container_is_converted_before_upload(monkeypatch, tmp_path):
    create = _fake_openai(monkeypatch, "matn")
    monkeypatch.setattr(stt, "extract_wav", lambda path: b"W" * 40_000)
    await stt.transcribe_file(_file(tmp_path, "clip.mov"))
    assert create.await_args.kwargs["file"][0] == "audio.wav"


async def test_file_rejected_as_is_is_retried_as_wav(monkeypatch, tmp_path):
    create = _fake_openai(monkeypatch, _bad_request(), "ikkinchi urinish")
    monkeypatch.setattr(stt, "extract_wav", lambda path: b"W" * 40_000)

    assert await stt.transcribe_file(_file(tmp_path, "voice.ogg")) == "ikkinchi urinish"
    assert [call.kwargs["file"][0] for call in create.await_args_list] == ["audio.ogg", "audio.wav"]


async def test_too_short_or_silent_track_gives_empty_text_without_a_request(monkeypatch, tmp_path):
    create = _fake_openai(monkeypatch)
    monkeypatch.setattr(stt, "extract_wav", lambda path: b"")
    assert await stt.transcribe_file(_file(tmp_path, "silent.mov")) == ""
    create.assert_not_called()


async def test_openai_failure_falls_back_to_local_model(monkeypatch, tmp_path):
    request = httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
    _fake_openai(monkeypatch, openai.APIConnectionError(request=request))
    local = lambda path: "lokal model matni"  # noqa: E731
    monkeypatch.setattr(stt, "transcribe_sync", local)

    assert await stt.transcribe_file(_file(tmp_path, "voice.ogg")) == "lokal model matni"


@pytest.mark.parametrize("provider, key", [("local", "sk-test"), ("openai", None)])
async def test_local_model_is_used_when_openai_is_not_selected_or_has_no_key(monkeypatch, tmp_path, provider, key):
    create = _fake_openai(monkeypatch, "openai matni")
    monkeypatch.setattr(stt.settings, "GROUP_STT_PROVIDER", provider)
    monkeypatch.setattr(stt.settings, "OPENAI_API_KEY", key)
    monkeypatch.setattr(stt, "transcribe_sync", lambda path: "lokal model matni")

    assert await stt.transcribe_file(_file(tmp_path, "voice.ogg")) == "lokal model matni"
    create.assert_not_called()
