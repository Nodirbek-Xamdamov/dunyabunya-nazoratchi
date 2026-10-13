"""Masks secrets in any text that may reach logs or the database (error messages, tracebacks)."""

import re
from collections.abc import Iterable

MASK = "***"
_MIN_SECRET_LEN = 6

_secrets: set[str] = set()

# Shapes of secrets that must never leak even if they were not registered explicitly.
_TELEGRAM_TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")
_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


def register_secrets(values: Iterable[str | None]) -> None:
    """Remember exact secret values; they are masked wherever they appear."""
    for value in values:
        if value and len(value) >= _MIN_SECRET_LEN:
            _secrets.add(value)


def clear_secrets() -> None:
    _secrets.clear()


def redact(text: str) -> str:
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, MASK)
    text = _TELEGRAM_TOKEN.sub(MASK, text)
    text = _OPENAI_KEY.sub(MASK, text)
    text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
    return text
