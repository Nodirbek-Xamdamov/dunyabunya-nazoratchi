from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _ids(s: str) -> frozenset[int]:
    return frozenset(int(x) for x in re.findall(r"-?\d+", s or ""))


def _clock(s: str) -> time:
    hour, _, minute = s.strip().partition(":")
    return time(int(hour), int(minute or 0))


@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # empty = the bot works in every group it is added to
    allowed_chat_ids: frozenset = _ids(os.getenv("ALLOWED_CHAT_IDS", ""))
    # reminders go out on the due date at this local time
    remind_at: time = _clock(os.getenv("REMIND_AT") or "09:00")
    # a payment promised for the very day it was written is reminded of that evening instead
    evening_at: time = _clock(os.getenv("EVENING_AT") or "18:00")
    tz: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE") or "Asia/Tashkent")
    # document photos are read by OpenAI: each photo a date is written under is sent there
    openai_api_key: str = (os.getenv("OPENAI_API_KEY") or "").strip()
    openai_model: str = os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
    db_path: Path = ROOT / "data" / "hisoblovchi.db"


settings = Settings()
