"""A minimal Telegram Bot API client (standard library only) for the agents' own bots.

The token is part of every request URL, so error messages built here never contain the URL. A bot
token must belong to a NEW bot made for the agent: a token that is already used by another program
(a webhook is set, or another program polls it -- Telegram answers 409) is refused, because two
programs reading the same bot steal each other's messages."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

API_BASE = os.environ.get("DASHBOARD_TELEGRAM_API_BASE", "https://api.telegram.org")  # tests point it elsewhere
TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
MAX_TEXT = 4000  # Telegram allows 4096 characters per message


class TelegramError(Exception):
    """``code``: Telegram's error code (401 wrong token, 409 used elsewhere ...), 0 = network."""

    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code


@dataclass(frozen=True)
class BotInfo:
    id: int
    username: str
    name: str


def call(token: str, method: str, params: dict | None = None, timeout: float = 30.0) -> dict | list | bool:
    request = urllib.request.Request(f"{API_BASE}/bot{token}/{method}",
                                     data=json.dumps(params or {}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # Telegram answers errors with a JSON body too
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            raise TelegramError(exc.code, f"Telegram xatosi {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TelegramError(0, f"Telegram'ga ulanib bo'lmadi ({type(exc).__name__})") from None
    except ValueError:
        raise TelegramError(0, "Telegram tushunarsiz javob qaytardi") from None
    if not body.get("ok"):
        raise TelegramError(int(body.get("error_code") or 0), str(body.get("description") or "Telegram xatosi"))
    return body.get("result")


def check_token(token: str, protected_usernames: tuple[str, ...] = ()) -> BotInfo:
    """Checks that the token works and is free for an agent. Raises TelegramError with a text for the
    page."""
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise TelegramError(400, "Token shakli noto'g'ri (masalan 123456789:AAE...). Uni @BotFather beradi.")
    me = call(token, "getMe", timeout=15)
    username = str(me.get("username") or "")
    if username.lower() in {name.lower().lstrip("@") for name in protected_usernames}:
        raise TelegramError(409, f"@{username} -- ishlab turgan bot. Agent uchun @BotFather'da yangi bot oching.")
    hook = call(token, "getWebhookInfo", timeout=15)
    if hook.get("url"):
        raise TelegramError(409, f"@{username} boshqa dasturda ishlatilmoqda (webhook o'rnatilgan). "
                                 "Agent uchun yangi bot oching.")
    return BotInfo(int(me["id"]), username, str(me.get("first_name") or username))


def split_text(text: str, size: int = MAX_TEXT) -> list[str]:
    """Long answers in several messages, cut at a line break when possible."""
    text = (text or "").strip() or "…"
    parts = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


def parse_allowed(text: str) -> tuple[list[int], list[str]]:
    """'123456789, @ali, vali' -> ([123456789], ['ali', 'vali'])."""
    ids, names = [], []
    for item in re.split(r"[\s,;]+", text or ""):
        item = item.strip().lstrip("@")
        if not item:
            continue
        if item.lstrip("-").isdigit():
            ids.append(int(item))
        elif re.match(r"^[A-Za-z][A-Za-z0-9_]{3,31}$", item):
            names.append(item.lower())
    return sorted(set(ids)), sorted(set(names))
