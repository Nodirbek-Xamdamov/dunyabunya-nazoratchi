"""
Telegram xom ma'lumotlari arxivi (2026-10-26) -- `RAW_TELEGRAM_DIR` (hozir `E:\\Telegram raw data`).

Kuzatiladigan guruhga kelgan HAR BIR xabar va tahrir (xizmat xabarlari, stikerlar, botlar ham)
Telegram'dan qanday kelgan bo'lsa shunday, guruh va kun bo'yicha faylga qo'shib yoziladi:

    <RAW_TELEGRAM_DIR>\\2026\\10-Oktabr\\Oqtosh zakaz dunyabunya\\2026-10-26.jsonl
    <RAW_TELEGRAM_DIR>\\2026\\10-Oktabr\\Oqtosh zakaz dunyabunya\\2026-10-26 fayllar\\10-15-03_512.jpg

`.jsonl` faylning har bir qatori bitta yozuv (JSON):
  "message" -- yangi xabar, "edit" -- tahrir (asl xabar kunining fayliga tushadi),
  "file"    -- xabardagi fayl (rasm, ovoz, video, hujjat, stiker) yuklab olindi yoki olinmadi.
Asosiy maydonlar (vaqt, yuboruvchi, matn, fayl) yuqorida, Telegram'ning to'liq xabari "telegram" ichida.
Kun = xabar yuborilgan kun, `TIMEZONE` bo'yicha. Bu yerda hech narsa tozalanmaydi va tahlil
qilinmaydi -- arxiv faqat to'ldiriladi, hech qachon o'chirilmaydi.

Guruh papkasining nomi guruh birinchi ko'rilgandagi nomidan olinadi va `guruhlar.json`da chat_id
bilan bog'lab qo'yiladi: guruh nomi o'zgarsa ham ma'lumotlari o'sha papkada davom etadi.

Fayllar xabar yozilishini kutmasdan alohida fon vazifada yuklanadi (`media_loop`); bot qayta ishga
tushsa oxirgi `RAW_RESCAN_DAYS` kunning yuklanmay qolgan fayllari qayta navbatga qo'yiladi.
Telegram Bot API 20 MB dan katta faylni bermaydi -- bunday fayl "too_big" deb yoziladi.
"""

import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.config import settings
from app.monitor_db import local_tz

logger = logging.getLogger(__name__)

MONTHS = ("Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
          "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr")
INDEX_NAME = "guruhlar.json"
EVENT_MESSAGE, EVENT_EDIT, EVENT_FILE = "message", "edit", "file"
FILE_SAVED, FILE_TOO_BIG, FILE_FAILED = "saved", "too_big", "failed"

# Fayli bor xabar turlari, tekshirish tartibida (GIF "document" bo'lib ham keladi)
_FILE_TYPES = ("animation", "voice", "video_note", "video", "audio", "sticker", "document")
_DEFAULT_SUFFIX = {"photo": ".jpg", "voice": ".ogg", "video_note": ".mp4", "video": ".mp4", "animation": ".mp4"}
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DOWNLOAD_TIMEOUT = 120
_DOWNLOAD_ATTEMPTS = 3
_RETRY_SECONDS = 60

_lock = threading.Lock()
_index: dict[str, dict] | None = None
_queue: asyncio.Queue | None = None
_known: set[str] = set()  # shu ishga tushishda navbatga qo'yilgan fayllar (file_unique_id)


@dataclass(frozen=True)
class FileJob:
    """Yuklab olinadigan fayl: qaysi kun faylida turibdi va qanday nom bilan saqlanadi."""

    day_file: Path
    message_id: int
    file_id: str
    file_unique_id: str | None  # monitor.db'dan ko'chirilgan eski yozuvlarda yo'q
    name: str  # "10-15-03_512" yoki "10-15-03_512_hisob.pdf" (kengaytmasi bo'lsa)
    media_type: str

    @property
    def key(self) -> str:
        return self.file_unique_id or self.file_id


def enabled() -> bool:
    return bool(settings.RAW_TELEGRAM_DIR.strip())


def root() -> Path:
    return Path(settings.RAW_TELEGRAM_DIR.strip())


def month_folder(day: date) -> str:
    """"10-Oktabr": raqam tartib uchun, nomi o'qish uchun."""
    return f"{day:%m}-{MONTHS[day.month - 1]}"


def safe_name(text: str | None, limit: int = 80) -> str:
    """Windows papka/fayl nomiga yaroqli qiladi: taqiqlangan belgilar olib tashlanadi, oxiridagi
    nuqta va bo'shliq kesiladi."""
    cleaned = " ".join(_BAD_NAME_CHARS.sub(" ", text or "").split())
    return cleaned[:limit].rstrip(" .")


def to_local(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(local_tz())


# ---------------------------------------------------------------------------
# Guruh papkalari (guruhlar.json)
# ---------------------------------------------------------------------------


def _load_index() -> dict[str, dict]:
    global _index
    if _index is None:
        path = root() / INDEX_NAME
        _index = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _index


def _save_index() -> None:
    path = root() / INDEX_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(_index, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def group_folder(chat_id: int, title: str | None, first_day: date) -> str:
    """Guruhning doimiy papka nomi. Birinchi marta ko'rilganda guruh nomidan yasaladi (boshqa
    guruhda shu nom band bo'lsa oxiriga chat_id qo'shiladi); keyin nomi o'zgarsa ham o'zgarmaydi."""
    index = _load_index()
    key = str(chat_id)
    entry = index.get(key)
    if entry is None:
        folder = safe_name(title) or str(abs(chat_id))
        if any(item["folder"].lower() == folder.lower() for item in index.values()):
            folder = f"{folder} ({abs(chat_id)})"
        index[key] = {"folder": folder, "title": title, "first_day": first_day.isoformat()}
        _save_index()
    elif title and entry.get("title") != title:
        entry["title"] = title  # papka o'sha-o'sha, faqat hozirgi nomi yangilanadi
        _save_index()
    return index[key]["folder"]


def day_file(chat_id: int, title: str | None, day: date) -> Path:
    folder = group_folder(chat_id, title, day)
    return root() / f"{day:%Y}" / month_folder(day) / folder / f"{day.isoformat()}.jsonl"


def files_dir(day_path: Path) -> Path:
    """Kun faylining yonidagi fayllar papkasi: "2026-10-26 fayllar"."""
    return day_path.with_name(f"{day_path.stem} fayllar")


def append(day_path: Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _lock:
        day_path.parent.mkdir(parents=True, exist_ok=True)
        with day_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


# ---------------------------------------------------------------------------
# Xabar yozuvi
# ---------------------------------------------------------------------------


def file_of(message: Message) -> tuple[str, object] | None:
    """Xabardagi fayl: (turi, Telegram obyekti). Rasmda eng katta o'lchami olinadi."""
    for media_type in _FILE_TYPES:
        media = getattr(message, media_type, None)
        if media is not None:
            return media_type, media
    if message.photo:
        return "photo", message.photo[-1]
    return None


def _sender(message: Message) -> tuple[int | None, str | None, str | None, bool]:
    user = message.from_user
    chat = message.sender_chat
    if chat is not None:  # anonim admin yoki kanal nomidan yozilgan
        return chat.id, chat.username, message.author_signature or chat.title, False
    if user is None:
        return None, None, None, False
    return user.id, user.username, user.full_name, user.is_bot


def _telegram_json(message: Message) -> dict:
    try:
        return message.model_dump(mode="json", exclude_none=True, by_alias=True)
    except Exception as exc:  # Telegram'ning yangi maydoni va h.k. -- yozuv baribir qolsin
        logger.warning("Xabarni JSON'ga aylantirib bo'lmadi (xabar=%s): %r", message.message_id, exc)
        return {"error": repr(exc)}


def message_record(message: Message, event: str) -> dict:
    sender_id, username, name, is_bot = _sender(message)
    replied = message.reply_to_message
    content_type = getattr(message.content_type, "value", message.content_type)
    record = {
        "event": event,
        "saved_at": to_local(datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "chat_id": message.chat.id,
        "chat_title": message.chat.title,
        "message_id": message.message_id,
        "date": to_local(message.date).isoformat(timespec="seconds"),
        "edit_date": None,
        "sender_id": sender_id,
        "sender_name": name,
        "sender_username": username,
        "sender_is_bot": is_bot,
        "type": content_type,
        "text": message.text or message.caption,
        "reply_to": replied.message_id if replied is not None else None,
        "thread_id": message.message_thread_id,
    }
    edited = message.edit_date
    if isinstance(edited, (int, float)):  # aiogram uni Unix soniyalarida beradi
        edited = datetime.fromtimestamp(edited, tz=timezone.utc)
    if edited is not None:
        record["edit_date"] = to_local(edited).isoformat(timespec="seconds")
    found = file_of(message)
    if found is not None:
        media_type, media = found
        record.update(
            file_type=media_type,
            file_id=media.file_id,
            file_unique_id=media.file_unique_id,
            file_name=getattr(media, "file_name", None),
            file_size=getattr(media, "file_size", None),
            mime_type=getattr(media, "mime_type", None),
            duration=getattr(media, "duration", None),
        )
    record["telegram"] = _telegram_json(message)
    return record


def file_job(day_path: Path, record: dict) -> FileJob | None:
    """Yozuvdagi fayl uchun yuklab olish vazifasi (fayl bo'lmasa None)."""
    if not record.get("file_id"):
        return None
    sent = datetime.fromisoformat(record["date"])
    name = f"{sent:%H-%M-%S}_{record['message_id']}"
    original = safe_name(record.get("file_name"), limit=60)
    if original:
        name = f"{name}_{original}"
    return FileJob(day_path, record["message_id"], record["file_id"], record.get("file_unique_id"), name,
                   record.get("file_type") or "")


def record(message: Message, *, edited: bool = False) -> Path:
    """Xabar (yoki tahrir) yozuvini kun fayliga qo'shadi; fayli bo'lsa yuklab olish navbatiga
    qo'yadi. Tahrirda `message.date` asl yuborilgan vaqt -- yozuv o'sha kunning fayliga tushadi."""
    data = message_record(message, EVENT_EDIT if edited else EVENT_MESSAGE)
    day = to_local(message.date).date()
    path = day_file(message.chat.id, message.chat.title, day)
    append(path, data)
    job = file_job(path, data)
    if job is not None and settings.RAW_TELEGRAM_FILES:
        _enqueue(job)
    return path


def _enqueue(job: FileJob) -> None:
    """Navbatga qo'yadi. Bir fayl bir marta: izohi tahrirlangan rasm qayta yuklanmaydi."""
    if _queue is None or job.key in _known:
        return
    _known.add(job.key)
    _queue.put_nowait((job, 0))


# ---------------------------------------------------------------------------
# Fayllarni yuklab olish
# ---------------------------------------------------------------------------


def _suffix(file_path: str | None, job: FileJob) -> str:
    if Path(job.name).suffix:  # asl nomida kengaytma bor (hujjat, audio)
        return ""
    suffix = Path(file_path or "").suffix.lower()
    if suffix == ".oga":
        suffix = ".ogg"  # Telegram ovozi; Windows .ogg ni taniydi
    return suffix or _DEFAULT_SUFFIX.get(job.media_type, "")


async def download(bot: Bot, job: FileJob) -> dict | None:
    """Faylni yuklab oladi va natijani ("file" yozuvi) qaytaradi. Tarmoq xatosida None."""
    partial: Path | None = None
    try:
        info = await bot.get_file(job.file_id)
        target = files_dir(job.day_file) / f"{job.name}{_suffix(info.file_path, job)}"
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        await bot.download_file(info.file_path, destination=partial, timeout=_DOWNLOAD_TIMEOUT)
    except TelegramBadRequest as exc:  # "file is too big", eskirgan file_id -- qayta urinish foydasiz
        status = FILE_TOO_BIG if "too big" in str(exc).lower() else FILE_FAILED
        return {"status": status, "path": None, "size": None, "error": str(exc)}
    except Exception as exc:
        if partial is not None:
            partial.unlink(missing_ok=True)
        logger.warning("Faylni yuklab olishda xato (xabar=%s): %r", job.message_id, exc)
        return None
    partial.replace(target)
    relative = target.relative_to(job.day_file.parent).as_posix()
    return {"status": FILE_SAVED, "path": relative, "size": target.stat().st_size, "error": None}


def file_record(job: FileJob, result: dict) -> dict:
    return {
        "event": EVENT_FILE,
        "saved_at": to_local(datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "message_id": job.message_id,
        "file_unique_id": job.file_unique_id,
        "file_id": job.file_id,
        **result,
    }


async def handle(bot: Bot, job: FileJob, attempt: int) -> bool:
    """Bitta faylni yuklab, natijasini kun fayliga yozadi. Qayta urinish kerak bo'lsa False."""
    result = await download(bot, job)
    if result is None:
        if attempt + 1 < _DOWNLOAD_ATTEMPTS:
            return False
        result = {"status": FILE_FAILED, "path": None, "size": None, "error": "tarmoq xatosi"}
    append(job.day_file, file_record(job, result))
    if result["status"] != FILE_SAVED:
        logger.warning("Fayl saqlanmadi (xabar=%s): %s %s", job.message_id, result["status"], result["error"])
    return True


def unfinished_jobs(day_path: Path) -> list[FileJob]:
    """Kun faylidagi hali "file" yozuvi yo'q fayllar (bot o'chib qolganda navbatda qolganlar)."""
    jobs: dict[str, FileJob] = {}
    done: set[str] = set()
    with day_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue  # chala yozilgan oxirgi qator
            if item.get("event") == EVENT_FILE:
                done.add(item.get("file_unique_id") or item.get("file_id"))
            else:
                job = file_job(day_path, item)
                if job is not None:
                    jobs.setdefault(job.key, job)
    return [job for key, job in jobs.items() if key not in done]


def recent_day_files(days: int, today: date | None = None) -> list[Path]:
    today = today or datetime.now(local_tz()).date()
    found: list[Path] = []
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        month = root() / f"{day:%Y}" / month_folder(day)
        if month.is_dir():
            found.extend(sorted(month.glob(f"*/{day.isoformat()}.jsonl")))
    return found


async def media_loop(bot: Bot) -> None:
    """Bot jarayonida BITTA nusxada ishlaydi (`app/bot/__init__.py`): navbatdagi fayllarni birma-bir yuklaydi."""
    global _queue
    _queue = asyncio.Queue()
    try:
        for path in await asyncio.to_thread(recent_day_files, settings.RAW_RESCAN_DAYS):
            for job in await asyncio.to_thread(unfinished_jobs, path):
                _enqueue(job)
        if _queue.qsize():
            logger.info("Xom arxiv: %s ta yuklanmay qolgan fayl navbatga qo'yildi", _queue.qsize())
    except Exception:
        logger.exception("Xom arxivdagi yuklanmay qolgan fayllarni qidirishda xatolik")
    while True:
        job, attempt = await _queue.get()
        try:
            if not await handle(bot, job, attempt):
                asyncio.get_running_loop().call_later(_RETRY_SECONDS, _queue.put_nowait, (job, attempt + 1))
        except Exception:
            logger.exception("Xom arxiv faylini saqlashda xatolik (xabar=%s)", job.message_id)
