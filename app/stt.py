"""
Nutqni matnga o'girish (2026-10-10) -- ovozli xabar, audio, video va dumaloq video uchun.

DIQQAT (2026-10-13): monitoring bu moduldan FAQAT `transcribe_openai`ni ishlatadi -- guruhdagi
ovozli xabarlar OpenAI transkripsiya API'si orqali matnga aylantiriladi (foydalanuvchi qarori;
`app/monitor_classifier.py`, natija `messages.transcript`da). Pastdagi lokal model
(`transcribe_file`, `transcribe_sync`) hozir hech qayerdan chaqirilmaydi, qaytish uchun qoldirilgan.

Lokal yo'l: hammasi shu kompyuterning videokartasida ishlaydi (faster-whisper / CTranslate2),
audio hech qayerga yuborilmaydi. Ikki model:

  - O'zbekcha (asosiy) -- `islomov/rubaistt_v2_medium` (Whisper Medium, o'zbek tiliga
    moslashtirilgan). Aynan shu model call-center STT loyihasida boshqa modellar bilan
    solishtirilib tanlangan; uning CTranslate2 nusxasi o'sha loyiha tomonidan
    `~/.cache/faster-whisper-ct2/` ga yozib qo'yilgan va standart holatda o'sha ishlatiladi
    (`GROUP_STT_MODEL`).
  - Ruscha -- `GROUP_STT_RU_MODEL` (ko'p tilli Whisper large-v3-turbo). O'zbekcha model ruscha
    nutqni lotin harflarida "transliteratsiya" qilib yozadi ("gde on bil"), shu sabab avval
    til aniqlanadi (o'zbekcha modelning o'zi bilan, ~0,1 s): nutq ishonchli ravishda ruscha
    bo'lsa -- ruscha model ishlaydi, qolgan hamma holatda (o'zbekcha, aralash, noaniq) --
    o'zbekcha model.

Videoning faqat OVOZI matnga o'giriladi -- tasvirning o'zi (kadrlar) tahlil qilinmaydi.

Modellar birinchi kerak bo'lganda yuklanadi (har biri ~1,5 GB videokarta xotirasi) va
shundan keyin xotirada qoladi. Yuklab bo'lmasa (kutubxona yoki model fayli yo'q) ovozli
xabarlar shunchaki matnsiz qoladi -- botning qolgan ishi to'xtamaydi.

`GROUP_STT_PROVIDER=openai` (2026-10-12) -- yuqoridagi lokal modellar o'rniga OpenAI
transkripsiya API'si (`OPENAI_STT_MODEL`, standart `whisper-1`). Bu holda audio OpenAI'ga
yuboriladi. So'rov xato bersa yoki kalit bo'lmasa -- shu xabar lokal model orqali o'giriladi.
"""

import asyncio
import importlib.util
import io
import logging
import os
import sys
import threading
import wave
from pathlib import Path

import openai
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "faster-whisper-ct2" / "islomov--rubaistt_v2_medium"
SAMPLE_RATE = 16000
# Nutq shu ehtimoldan yuqori ishonch bilan ruscha deb topilsagina ruscha modelga beriladi.
# O'lchov (2026-10-10): haqiqiy o'zbekcha qo'ng'iroqlarda "ru" ehtimoli 0,05 dan oshmadi, tabiiy
# ruscha nutqda 0,59-0,99. Xato narxi teng emas: o'zbekcha nutq ruscha modelga tushsa matn
# yaroqsiz chiqadi, ruscha nutq o'zbekcha modelda qolsa lotincha transliteratsiya bo'ladi
# (tasniflovchi uni baribir tushunadi) -- shu sabab chegara ehtiyotkor tanlangan.
RUSSIAN_MIN_PROBABILITY = 0.5

# Whisper'ga soha lug'ati: notanish qurilish atamalarini ("gipsokarton", "rotband") to'g'ri
# eshitish ehtimolini oshiradi. Ro'yxat call-center STT loyihasida sinalgan; sohaga aloqasiz
# so'z qo'shilmasin -- Whisper ularni aytilmagan joyda ham "eshitib" yuboradi.
INITIAL_PROMPT = (
    "Assalomu alaykum, dunyabunya, buyurtma, yetkazib berish, ombor, tovar, hisob-kitob, "
    "sement, g'isht, armatura, bo'yoq, plitka, shifer, gips, beton, qum, shag'al, profil, "
    "gipsokarton, penopleks, rotband, alebastr, shpatlevka, penoblok, kley."
)

# "uz" / "ru" -> yuklangan model. Qurilma (cuda/cpu) hamma model uchun bitta.
_models: dict = {}
_failed: set[str] = set()
_device: str | None = None
_dlls_registered = False
# Videokarta bitta: yuklash ham, har bir transkripsiya ham navbat bilan bajariladi
_lock = threading.Lock()


def _register_cuda_dlls() -> None:
    """Windows: CTranslate2 videokartada ishlashi uchun cuBLAS va cuDNN DLL'larini topishi kerak,
    ular esa odatda PATH'da bo'lmaydi. Ikki joydan qidiramiz: `GROUP_STT_CUDA_DLL_DIR` (loyiha
    ichidagi `cuda_runtime` papkasi) va pip orqali o'rnatilgan nvidia-*-cu12 paketlari."""
    global _dlls_registered
    if _dlls_registered or sys.platform != "win32":
        return
    _dlls_registered = True
    dll_dirs = []
    if settings.GROUP_STT_CUDA_DLL_DIR:
        dll_dirs.append(Path(settings.GROUP_STT_CUDA_DLL_DIR).resolve())
    spec = importlib.util.find_spec("nvidia")
    if spec is not None and spec.submodule_search_locations:
        for root in spec.submodule_search_locations:
            dll_dirs.extend(Path(root).glob("*/bin"))
    for dll_dir in dll_dirs:
        if dll_dir.is_dir():
            os.add_dll_directory(str(dll_dir))
            os.environ["PATH"] = f"{dll_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _model_name(language: str) -> str:
    if language == "ru":
        return settings.GROUP_STT_RU_MODEL
    return settings.GROUP_STT_MODEL or str(DEFAULT_MODEL_DIR)


def _load(language: str, device: str):
    from faster_whisper import WhisperModel

    name = _model_name(language)
    compute_type = "float16" if device == "cuda" else "int8"
    logger.info("Nutq modeli yuklanmoqda (%s): %s (%s, %s)", language, name, device, compute_type)
    return WhisperModel(name, device=device, compute_type=compute_type)


def _get_model(language: str):
    """Modelni (kerak bo'lsa yuklab) qaytaradi; yuklab bo'lmasa None."""
    global _device
    if language in _models:
        return _models[language]
    if language in _failed:
        return None
    try:
        _register_cuda_dlls()
        if _device is None:
            _device = "cpu" if settings.GROUP_STT_DEVICE == "cpu" else "cuda"
        try:
            _models[language] = _load(language, _device)
        except Exception:
            if _device != "cuda" or settings.GROUP_STT_DEVICE != "auto":
                raise
            logger.warning("Nutq modelini videokartada yuklab bo'lmadi -- protsessorda ishlaydi", exc_info=True)
            _switch_to_cpu()
            _models[language] = _load(language, _device)
    except Exception:
        _failed.add(language)
        logger.exception("Nutq modelini (%s) yuklab bo'lmadi", language)
        return None
    return _models[language]


def _switch_to_cpu() -> None:
    global _device
    _device = "cpu"
    _models.clear()


def _is_russian(model, audio) -> bool:
    if not settings.GROUP_STT_RU_MODEL:
        return False
    language, probability, _all = model.detect_language(audio)
    return language == "ru" and probability >= RUSSIAN_MIN_PROBABILITY


def _run(path: str) -> str | None:
    from faster_whisper.audio import decode_audio

    main_language = settings.GROUP_STT_LANGUAGE
    model = _get_model(main_language)
    if model is None:
        return None
    # PyAV istalgan konteynerdan (ogg/opus, mp3, m4a, mp4 video) audio yo'lagini o'qiydi
    audio = decode_audio(path, sampling_rate=SAMPLE_RATE)
    if audio.size < SAMPLE_RATE // 2:
        return ""

    language, prompt = main_language, INITIAL_PROMPT
    if _is_russian(model, audio):
        russian_model = _get_model("ru")
        if russian_model is not None:
            model, language, prompt = russian_model, "ru", None

    segments, _info = model.transcribe(
        audio,
        language=language,
        task="transcribe",
        beam_size=5,
        # Xabarlar bir-biridan mustaqil; oldingi bo'lak xatosi keyingisiga o'tmasin
        condition_on_previous_text=False,
        # Jim joylar va shovqinni kesib tashlaydi -- Whisper jimlikda "gallyutsinatsiya" qiladi
        vad_filter=True,
        initial_prompt=prompt,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def transcribe_sync(path: str) -> str | None:
    """Audio/video fayldagi nutqni matnga o'giradi. Nutq bo'lmasa bo'sh satr, model
    ishlamasa None qaytaradi. Bloklovchi -- `transcribe_file` orqali chaqiring."""
    with _lock:
        try:
            return _run(path)
        except Exception:
            # cuDNN/cuBLAS topilmasa xato model yuklashda emas, birinchi transkripsiyada chiqadi
            if _device != "cuda" or settings.GROUP_STT_DEVICE != "auto":
                logger.exception("Nutqni matnga o'girishda xatolik")
                return None
            logger.warning("Videokartada transkripsiya ishlamadi -- protsessorga o'tilmoqda", exc_info=True)
        try:
            _switch_to_cpu()
            return _run(path)
        except Exception:
            logger.exception("Nutqni matnga o'girishda xatolik")
            return None


# ---------------------------------------------------------------------------
# OpenAI transkripsiya API'si
# ---------------------------------------------------------------------------

# API qabul qiladigan fayl turlari (kengaytma bo'yicha tekshiradi)
_OPENAI_AUDIO_SUFFIXES = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}
_OPENAI_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Videoning tasviri transkripsiyaga kerak emas: shundan kattasi faqat ovoz yo'lagiga aylantirib yuboriladi
_SEND_AS_IS_MAX_BYTES = 2 * 1024 * 1024

_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=120.0, max_retries=1)
    return _openai_client


def extract_wav(path: str) -> bytes:
    """Istalgan audio/video fayldan ovoz yo'lagini 16 kHz mono WAV qilib qaytaradi (PyAV)."""
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    buffer = io.BytesIO()
    with av.open(path) as container, wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if stream is None:  # ovozsiz video
            return b""
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                wav.writeframes(resampled.to_ndarray().tobytes())
        for resampled in resampler.resample(None):  # resampler ichida qolgan oxirgi bo'lak
            wav.writeframes(resampled.to_ndarray().tobytes())
    return buffer.getvalue()


def _upload_name(path: str) -> str:
    """Telegram ovozli xabari `.oga` (ogg/opus) bo'lib keladi -- API uni faqat `.ogg` nomi bilan oladi."""
    suffix = Path(path).suffix.lower()
    return "audio" + {".oga": ".ogg", ".opus": ".ogg"}.get(suffix, suffix)


async def _request_openai_transcription(name: str, data: bytes) -> str:
    kwargs = {"language": settings.OPENAI_STT_LANGUAGE} if settings.OPENAI_STT_LANGUAGE else {}
    result = await _get_openai_client().audio.transcriptions.create(
        model=settings.OPENAI_STT_MODEL, file=(name, data), prompt=INITIAL_PROMPT, **kwargs
    )
    return (result.text or "").strip()


async def transcribe_openai(path: str) -> str | None:
    """OpenAI orqali matnga o'giradi. Nutq bo'lmasa bo'sh satr, so'rov bajarilmasa None."""
    name = _upload_name(path)
    size = os.path.getsize(path)
    try:
        if Path(name).suffix in _OPENAI_AUDIO_SUFFIXES and size <= _SEND_AS_IS_MAX_BYTES:
            try:
                with open(path, "rb") as source:
                    return await _request_openai_transcription(name, source.read())
            except openai.BadRequestError:
                # Kengaytmasi to'g'ri, lekin ichidagi format API'ga yoqmadi -- WAV qilib qayta yuboramiz
                logger.info("OpenAI faylni asl holida qabul qilmadi, WAV qilib yuborilmoqda")
        data = await asyncio.to_thread(extract_wav, path)
        if len(data) > _OPENAI_MAX_UPLOAD_BYTES:
            logger.info("Yozuv OpenAI uchun juda uzun (%.1f MB)", len(data) / 1024 / 1024)
            return None
        if len(data) < SAMPLE_RATE:  # yarim soniyadan qisqa
            return ""
        return await _request_openai_transcription("audio.wav", data)
    except openai.OpenAIError as exc:
        logger.warning("OpenAI transkripsiyasi bajarilmadi: %s", exc)
    except Exception:
        logger.exception("Faylni OpenAI transkripsiyasiga tayyorlashda xatolik")
    return None


async def transcribe_file(path: str) -> str | None:
    """Audio/video fayldagi nutqni matnga o'giradi (`GROUP_STT_PROVIDER`). Nutq bo'lmasa bo'sh
    satr, hech bir yo'l ishlamasa None."""
    if settings.GROUP_STT_PROVIDER == "openai" and settings.OPENAI_API_KEY:
        transcript = await transcribe_openai(path)
        if transcript is not None:
            return transcript
        logger.warning("OpenAI transkripsiyasi ishlamadi -- lokal modelga o'tilmoqda")
    return await asyncio.to_thread(transcribe_sync, path)
