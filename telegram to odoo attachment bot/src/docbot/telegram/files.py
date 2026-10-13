"""Downloading an uploaded file from Telegram (the ``FileSource`` of the file store)."""

from pathlib import Path

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError

from docbot.errors import PermanentError, TransientError


class TelegramFiles:
    def __init__(self, bot: Bot, *, timeout: int = 120):
        self._bot = bot
        self._timeout = timeout

    async def download(self, file_id: str, destination: Path) -> None:
        try:
            await self._bot.download(file_id, destination=destination, timeout=self._timeout)
        except TelegramRetryAfter as exc:
            raise TransientError("Telegram: juda ko'p so'rov", retry_after=exc.retry_after) from None
        except (TelegramNetworkError, TelegramServerError, aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise TransientError(f"Telegram'dan yuklab bo'lmadi: {type(exc).__name__}: {exc}") from None
        except TelegramBadRequest as exc:
            raise PermanentError(f"Telegram faylni bermadi: {exc.message}") from None
