"""app.ai_processor -- xabardagi mediani tahlilga tayyorlash (rasm, video kadrlari, nutq) va
xabar turiga qarab kerakli modelni tanlash."""

import io
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app import ai_processor, group_ai, stt
from app.ai_processor import MediaContent, analyze, extract_frames, extract_media, get_speech_media, prepare_image
from app.group_ai import MessageAnalysis


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    settings = ai_processor.settings
    monkeypatch.setattr(settings, "GROUP_IMAGE_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(settings, "GROUP_IMAGE_MAX_SIDE", 256)
    monkeypatch.setattr(settings, "GROUP_VIDEO_FRAMES", 3)
    monkeypatch.setattr(settings, "GROUP_STT_ENABLED", True)
    monkeypatch.setattr(settings, "GROUP_STT_MAX_DURATION_SECONDS", 600)
    monkeypatch.setattr(settings, "GROUP_MEDIA_AI_PROVIDER", "")


def _png(width, height) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), (200, 30, 30, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_size(data: bytes) -> tuple[str, tuple[int, int]]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return image.format, image.size


def _write_video(path, *, seconds=2, with_audio=True) -> None:
    """PyAV bilan kichik sinov videosi: har soniyada rangi o'zgaradigan kadrlar (+ ovoz yo'lagi)."""
    import av

    with av.open(str(path), "w") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 640, 480, "yuv420p"
        audio = container.add_stream("aac", rate=16000) if with_audio else None
        for index in range(seconds * 10):
            pixels = np.full((480, 640, 3), 40 + 100 * (index // 10), dtype=np.uint8)
            for packet in video.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        if audio is not None:
            samples = (np.sin(np.arange(16000 * seconds) * 0.05) * 8000).astype(np.int16).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = 16000
            for packet in audio.encode(frame):
                container.mux(packet)
            for packet in audio.encode():
                container.mux(packet)


def _bot_that_downloads(data: bytes | None = None, source_path=None):
    """`bot.download` -- faylga yoki BytesIO'ga berilgan ma'lumotni yozadi."""
    bot = MagicMock()

    async def download(media, destination):
        payload = data if data is not None else source_path.read_bytes()
        if isinstance(destination, (str, bytes)):
            with open(destination, "wb") as target:
                target.write(payload)
        else:
            destination.write(payload)

    bot.download = AsyncMock(side_effect=download)
    return bot


def _message(**media):
    fields = dict(voice=None, video_note=None, video=None, audio=None, photo=None, document=None)
    return SimpleNamespace(chat=SimpleNamespace(id=-1), **(fields | media))


def test_prepare_image_downsizes_and_converts_to_jpeg():
    image_format, size = _image_size(prepare_image(_png(1200, 600)))
    assert image_format == "JPEG" and size == (256, 128)
    # Kichik rasm kattalashtirilmaydi
    assert _image_size(prepare_image(_png(100, 50)))[1] == (100, 50)


def test_extract_frames_takes_evenly_spaced_frames_and_handles_missing_video(tmp_path):
    video = tmp_path / "clip.mp4"
    _write_video(video, seconds=3, with_audio=False)

    frames = extract_frames(str(video), 3)
    assert len(frames) == 3
    assert all(_image_size(frame) == ("JPEG", (256, 192)) for frame in frames)
    # Kadrlar videoning turli joyidan olingan (sinov videosida har soniyaning rangi boshqa)
    assert len(set(frames)) == 3

    sound_only = tmp_path / "sound.wav"
    with wave.open(str(sound_only), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    assert extract_frames(str(sound_only), 3) == ()


def test_extract_wav_returns_16k_mono_audio_track(tmp_path):
    video = tmp_path / "clip.mp4"
    _write_video(video, seconds=2)

    with wave.open(io.BytesIO(stt.extract_wav(str(video)))) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert 1.8 < wav.getnframes() / 16000 < 2.3

    silent = tmp_path / "silent.mp4"
    _write_video(silent, seconds=1, with_audio=False)
    assert stt.extract_wav(str(silent)) == b""


@pytest.mark.parametrize(
    "media, expected",
    [
        (dict(voice=SimpleNamespace()), ("voice", ".ogg")),
        (dict(video_note=SimpleNamespace()), ("video_note", ".mp4")),
        (dict(video=SimpleNamespace(file_name="IMG_1.MOV", mime_type="video/quicktime")), ("video", ".mov")),
        (dict(video=SimpleNamespace(file_name=None, mime_type="video/quicktime")), ("video", ".mov")),
        (dict(audio=SimpleNamespace(file_name=None, mime_type="audio/mp4")), ("audio", ".m4a")),
        (dict(audio=SimpleNamespace(file_name=None, mime_type=None)), ("audio", ".mp3")),
    ],
)
def test_speech_media_type_and_file_suffix(media, expected):
    media_type, _label, _media, suffix = get_speech_media(_message(**media))
    assert (media_type, suffix) == expected


async def test_plain_text_message_has_no_media():
    bot = MagicMock()
    assert await extract_media(bot, _message()) == MediaContent()
    assert get_speech_media(_message()) is None


async def test_photo_is_downloaded_and_prepared():
    sizes = [SimpleNamespace(file_id="s", width=90, height=60), SimpleNamespace(file_id="m", width=320, height=213),
             SimpleNamespace(file_id="l", width=1280, height=853)]
    bot = _bot_that_downloads(_png(320, 213))

    media = await extract_media(bot, _message(photo=sizes))

    assert bot.download.await_args.args[0].file_id == "m"  # 256 pikselga yetadigan eng kichik nusxa
    assert media.media_type == "photo" and not media.is_speech
    assert _image_size(media.images[0]) == ("JPEG", (256, 170))


async def test_image_sent_as_file_is_analyzed_and_other_documents_are_not():
    bot = _bot_that_downloads(_png(300, 300))
    picture = SimpleNamespace(mime_type="image/png", file_size=500_000)
    assert (await extract_media(bot, _message(document=picture))).images

    for document in (SimpleNamespace(mime_type="application/pdf", file_size=1000),
                     SimpleNamespace(mime_type="image/png", file_size=50 * 1024 * 1024)):
        bot.download.reset_mock()
        assert await extract_media(bot, _message(document=document)) == MediaContent()
        bot.download.assert_not_called()


async def test_unreadable_image_does_not_break_processing():
    bot = _bot_that_downloads(b"bu rasm emas")
    assert await extract_media(bot, _message(photo=[SimpleNamespace(file_id="x", width=800, height=600)])) == MediaContent("photo")


async def test_video_gives_transcript_and_frames(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    _write_video(video, seconds=2)
    transcribe = AsyncMock(return_value="mana qarang yarmi singan")
    monkeypatch.setattr(stt, "transcribe_file", transcribe)
    bot = _bot_that_downloads(source_path=video)

    media = await extract_media(bot, _message(video=SimpleNamespace(file_size=video.stat().st_size, duration=2)))

    assert media.media_type == "video" and media.is_speech
    assert media.transcript == "mana qarang yarmi singan"
    assert len(media.images) == 3
    downloaded_to = transcribe.await_args.args[0]
    assert downloaded_to.endswith(".mp4")
    assert not ai_processor.os.path.exists(downloaded_to)  # vaqtinchalik fayl o'chirilgan


async def test_voice_has_no_frames_and_staff_video_skips_them(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    _write_video(video, seconds=1)
    monkeypatch.setattr(stt, "transcribe_file", AsyncMock(return_value="10 minutda javob beraman"))
    bot = _bot_that_downloads(source_path=video)
    clip = SimpleNamespace(file_size=1000, duration=1)

    assert (await extract_media(bot, _message(voice=clip))).images == ()
    staff_video = await extract_media(bot, _message(video=clip), with_images=False)
    assert staff_video.images == () and staff_video.transcript == "10 minutda javob beraman"


async def test_video_frames_work_without_speech_to_text(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    _write_video(video, seconds=1, with_audio=False)
    monkeypatch.setattr(ai_processor.settings, "GROUP_STT_ENABLED", False)
    transcribe = AsyncMock()
    monkeypatch.setattr(stt, "transcribe_file", transcribe)
    bot = _bot_that_downloads(source_path=video)

    media = await extract_media(bot, _message(video=SimpleNamespace(file_size=1000, duration=1)))
    assert media.transcript is None and len(media.images) >= 1
    transcribe.assert_not_called()


async def test_speech_goes_to_media_model_and_everything_else_to_main_model(monkeypatch):
    classify = AsyncMock(return_value=MessageAnalysis())
    monkeypatch.setattr(group_ai, "analyze_message", classify)
    monkeypatch.setattr(ai_processor.settings, "GROUP_MEDIA_AI_PROVIDER", "openai")
    monkeypatch.setattr(ai_processor.settings, "GROUP_MEDIA_AI_MODEL", "gpt-4o-mini")

    await analyze("matn", False, context="ctx", addressed_to_staff=True)
    await analyze("matn", False, media=MediaContent("photo", images=(b"jpeg",)))
    for media_type in ("voice", "audio", "video", "video_note"):
        await analyze("nutq matni", False, media=MediaContent(media_type, "nutq matni", (b"kadr",)))

    text_call, photo_call, *speech_calls = classify.await_args_list
    assert text_call.args == ("matn", False)
    assert text_call.kwargs == dict(context="ctx", addressed_to_staff=True, images=(), provider=None, model=None)
    assert photo_call.kwargs["images"] == (b"jpeg",) and photo_call.kwargs["provider"] is None
    for call in speech_calls:
        assert (call.kwargs["provider"], call.kwargs["model"]) == ("openai", "gpt-4o-mini")
        assert call.kwargs["images"] == (b"kadr",)

    # Alohida model sozlanmagan -- ovozli xabar ham asosiy modelga boradi
    monkeypatch.setattr(ai_processor.settings, "GROUP_MEDIA_AI_PROVIDER", "")
    await analyze("nutq matni", False, media=MediaContent("voice", "nutq matni"))
    assert classify.await_args.kwargs["provider"] is None
