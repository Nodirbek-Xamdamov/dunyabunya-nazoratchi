"""
Rasm va GIF'larni web sahifaga o'tkazish (2026-10-22) -- xabar yig'ishdan ALOHIDA fon vazifa.

Guruhdagi rasm (photo) va GIF (animation) xabarining fayli FAQAT kompaniyaga oid suhbatda bo'lsa
Telegram'dan yuklab olinadi va `MEDIA_DIR`ga yoziladi; CEO dashboard'i uni guruhning suhbat
lentasida ko'rsatadi (dashboard `media_files` jadvalini faqat o'qiydi).

Suhbat -- shu guruhda rasmdan `MEDIA_CONTEXT_MINUTES` daqiqa oldin va keyin yozilgan xabarlar,
rasm javob bergan xabar va rasmga javob bergan xabarlar (rasmning o'z izohi ham). Ulardan
birortasini tasniflovchi kompaniyaga oid degan bo'lsa -- fayl saqlanadi, aks holda "skipped".
Qaror rasm yuborilgandan `MEDIA_CONTEXT_MINUTES` daqiqa o'tib, atrofidagi mijoz xabarlari
tasniflab bo'lingach qilinadi -- izohsiz rasmdan keyin yozilgan "shundan 20 ta kerak" ham hisobga kiradi.

Cheklovlar: bir kunda (xabar kuni, `TIMEZONE`) ko'pi bilan `MEDIA_DAILY_LIMIT` ta fayl ("limit"),
`MEDIA_MAX_FILE_MB`dan katta fayl saqlanmaydi ("too_big"; Telegram Bot API 20 MB dan kattasini
bermaydi ham). Har rasm bo'yicha qaror `media_files`da bitta qator -- bir marta qilinadi.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app import monitor_db
from app.config import settings
from app.monitor_db import MEDIA_FAILED, MEDIA_LIMIT, MEDIA_SAVED, MEDIA_SKIPPED, MEDIA_TOO_BIG, Message

logger = logging.getLogger(__name__)

_BATCH = 20
_LOOP_SECONDS = 60
# Tarmoq xatosi bilan yuklab bo'lmagan fayl shuncha aylanishdan keyin "failed" bo'ladi
_DOWNLOAD_ATTEMPTS = 3
# aiogram'ning standart 30 soniyasi bu serverda yetmay qoldi (2026-10-22: TimeoutError, chala fayl)
DOWNLOAD_TIMEOUT = 90
_DEFAULT_SUFFIX = {"photo": ".jpg", "animation": ".mp4"}

# xabarning bazadagi ID'si -> ketma-ket yuklab olish xatolari (bot qayta ishga tushsa nolga qaytadi)
_failures: dict[int, int] = {}


def relative_path(message: Message, suffix: str) -> str:
    """`MEDIA_DIR` ichidagi joy: oy papkasi va guruh_xabar nomi, masalan "2026-10/1004290911290_512.jpg"."""
    return f"{message.local_date:%Y-%m}/{abs(message.chat_id)}_{message.message_id}{suffix}"


async def _download(bot: Bot, message: Message) -> tuple[str, str | None, int | None] | None:
    """Faylni yuklab oladi: (status, yo'l, hajm). Tarmoq xatosida None -- keyingi aylanishda qayta."""
    max_bytes = settings.MEDIA_MAX_FILE_MB * 1024 * 1024
    partial: Path | None = None
    try:
        file = await bot.get_file(message.file_id)
        if file.file_size and file.file_size > max_bytes:
            return MEDIA_TOO_BIG, None, file.file_size
        suffix = Path(file.file_path or "").suffix.lower() or _DEFAULT_SUFFIX.get(message.media_type, "")
        relative = relative_path(message, suffix)
        target = Path(settings.MEDIA_DIR) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")  # dashboard chala yozilgan faylni ko'rmasin
        await bot.download_file(file.file_path, destination=partial, timeout=DOWNLOAD_TIMEOUT)
    except TelegramBadRequest as exc:
        # "file is too big", eskirgan file_id -- qayta urinishdan foyda yo'q
        logger.warning("Rasmni yuklab bo'lmadi (xabar=%s): %r", message.id, exc)
        return MEDIA_FAILED, None, None
    except Exception as exc:
        if partial is not None:
            partial.unlink(missing_ok=True)
        _failures[message.id] = _failures.get(message.id, 0) + 1
        if _failures[message.id] < _DOWNLOAD_ATTEMPTS:
            logger.warning("Rasmni yuklab olishda xato (xabar=%s), qayta uriniladi: %r", message.id, exc)
            return None
        logger.warning("Rasmni %s marta yuklab bo'lmadi (xabar=%s): %r", _DOWNLOAD_ATTEMPTS, message.id, exc)
        return MEDIA_FAILED, None, None
    _failures.pop(message.id, None)
    size = partial.stat().st_size
    if size > max_bytes:
        partial.unlink(missing_ok=True)
        return MEDIA_TOO_BIG, None, size
    partial.replace(target)
    return MEDIA_SAVED, relative, size


async def process_media(bot: Bot, now: datetime | None = None) -> int:
    """Bir aylanish: suhbati yig'ilib bo'lgan rasm/GIF'lar bo'yicha qaror qiladi. Nechta qaror
    yozilganini qaytaradi."""
    now = now or monitor_db.utc_now()
    minutes = settings.MEDIA_CONTEXT_MINUTES
    saved_today: dict[date, int] = {}
    decided = 0
    for message in await monitor_db.media_candidates(now - timedelta(minutes=minutes), _BATCH):
        related = await monitor_db.in_company_conversation(message, minutes)
        if related is None:
            continue  # atrofidagi mijoz xabarlari hali tasniflanmagan
        if not related:
            await monitor_db.save_media_decision(message, MEDIA_SKIPPED)
            decided += 1
            continue
        day = message.local_date
        if day not in saved_today:
            saved_today[day] = await monitor_db.media_saved_count(day)
        if saved_today[day] >= settings.MEDIA_DAILY_LIMIT:
            await monitor_db.save_media_decision(message, MEDIA_LIMIT)
            decided += 1
            continue
        result = await _download(bot, message)
        if result is None:
            continue
        status, path, size = result
        await monitor_db.save_media_decision(message, status, path, size)
        decided += 1
        if status == MEDIA_SAVED:
            saved_today[day] += 1
            logger.info("Rasm saqlandi: xabar=%s, %s (%s KB)", message.id, path, round(size / 1024))
    return decided


async def media_loop(bot: Bot) -> None:
    """Bot jarayonida BITTA nusxada ishlaydi (`app/bot/__init__.py`): har daqiqa yangi rasmlarni ko'radi."""
    while True:
        try:
            while await process_media(bot) >= _BATCH:  # navbat katta -- kutmasdan davom etadi
                pass
        except Exception:
            logger.exception("Rasm/GIF'larni saqlashda xatolik")
        await asyncio.sleep(_LOOP_SECONDS)
