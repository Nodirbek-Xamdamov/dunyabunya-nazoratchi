"""Which chats the bot serves: ALLOWED_CHAT_IDS from .env plus groups registered at run time (data/chats.json).

A group gets registered when an admin (ADMIN_IDS) adds the bot to it, or when an admin presses "allow"
after someone else did. Telegram changes a group's id when it becomes a supergroup (this can happen
when the bot is made admin); the registration follows the new id.

Only messages written after a group was connected are processed (the user's rule: "do not touch old
messages"). Telegram never hands a bot the history anyway; ``is_new`` also guards against anything
posted before the connection that still reaches the bot (e.g. queued while the group was not allowed yet).

Groups can be paused (the user's rollout: test in a private chat first, then "tayyor"). A paused group is
not touched at all: its messages are not taken and the bot writes nothing there. ``go_live`` resumes every
group from that moment on: only messages written after it count. The file is re-read when it changes, so
``docbot groups`` (CLI) and the admin's /pauza, /tayyor act on the running bot at once.
"""

import json
import logging
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from docbot.clock import utcnow

log = logging.getLogger(__name__)


class ChatRegistry:
    def __init__(self, path: Path, static: Iterable[int]):
        self._path = path
        self._static = set(static)
        self._mtime: float | None = None
        self._chats: dict[int, dict[str, Any]] = self._load()

    def _load(self) -> dict[int, dict[str, Any]]:
        try:
            self._mtime = self._path.stat().st_mtime
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._mtime = None
            return {}
        except (OSError, ValueError):
            log.exception("%s could not be read; only ALLOWED_CHAT_IDS are served", self._path)
            return {}
        return {int(key): value for key, value in raw.items()}

    def _fresh(self) -> None:
        """Pick up changes made by another process (docbot groups ...)."""
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self._chats = self._load()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._path.with_name(self._path.name + ".tmp")
        partial.write_text(json.dumps({str(k): v for k, v in self._chats.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(partial, self._path)
        self._mtime = self._path.stat().st_mtime

    def allowed(self, chat_id: int) -> bool:
        self._fresh()
        return chat_id in self._static or chat_id in self._chats

    def paused(self, chat_id: int) -> bool:
        self._fresh()
        return bool((self._chats.get(chat_id) or {}).get("paused"))

    def live(self, chat_id: int) -> bool:
        """Served and not paused: its new messages are taken and the bot may write there."""
        return self.allowed(chat_id) and not self.paused(chat_id)

    def groups(self) -> dict[int, dict[str, Any]]:
        self._fresh()
        return {chat_id: dict(info) for chat_id, info in self._chats.items()}

    def pause_all(self) -> list[str]:
        self._fresh()
        for info in self._chats.values():
            info["paused"] = True
        self._save()
        log.info("all groups paused")
        return [str(info.get("title") or chat_id) for chat_id, info in self._chats.items()]

    def go_live(self) -> list[str]:
        """Every group is served again, counting only messages written from now on."""
        self._fresh()
        now = utcnow().isoformat()
        for info in self._chats.values():
            info["paused"] = False
            info["live_since"] = now
        self._save()
        log.info("all groups live since %s", now)
        return [str(info.get("title") or chat_id) for chat_id, info in self._chats.items()]

    def connected_at(self, chat_id: int) -> datetime | None:
        """Since when the group's messages count: its last "go live", else its connection (None: ALLOWED_CHAT_IDS)."""
        self._fresh()
        info = self._chats.get(chat_id) or {}
        stamp = info.get("live_since") or info.get("added_at")
        try:
            return datetime.fromisoformat(stamp) if stamp else None
        except ValueError:
            return None

    def is_new(self, chat_id: int, sent_at: datetime) -> bool:
        """The message was written after the group was connected (always true for chats without a date)."""
        since = self.connected_at(chat_id)
        return since is None or sent_at >= since

    def add(self, chat_id: int, *, title: str | None, added_by: int | None) -> bool:
        """Register a chat. False if it was already served."""
        if self.allowed(chat_id):
            return False
        self._chats[chat_id] = {"title": title, "added_by": added_by, "added_at": utcnow().isoformat()}
        self._save()
        log.info("chat %s (%s) registered by %s", chat_id, title, added_by)
        return True

    def remove(self, chat_id: int) -> bool:
        if self._chats.pop(chat_id, None) is None:
            return False
        self._save()
        log.info("chat %s unregistered", chat_id)
        return True

    def migrate(self, old_id: int, new_id: int) -> None:
        """The group became a supergroup with a new id: keep serving it."""
        if not self.allowed(old_id) or self.allowed(new_id):
            return
        info = dict(self._chats.get(old_id) or {"title": None, "added_by": None})
        info["migrated_from"] = old_id
        self._chats[new_id] = info
        self._save()
        log.info("chat %s moved to %s", old_id, new_id)
