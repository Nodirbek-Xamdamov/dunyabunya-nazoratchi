"""Which chats W1 may read: core.sources rows with enabled = true. Unknown chats are noted (metadata only,
enabled = false) so a person can approve them; their messages are never read or stored."""
from __future__ import annotations

import psycopg


class Allowlist:
    def __init__(self, conn: psycopg.Connection, reader: str = "w1_bot"):
        self.conn = conn
        self.reader = reader
        self.ids: set[int] = set()
        self._noted: set[int] = set()
        self.reload()

    def reload(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT tg_chat_id FROM core.sources WHERE enabled")
            self.ids = {r[0] for r in cur.fetchall()}

    def allowed(self, chat_id: int | None) -> bool:
        return chat_id is not None and chat_id in self.ids

    def note_unknown(self, chat_id: int, title: str | None, kind: str) -> bool:
        """Record an unknown chat once (enabled = false). Returns True if a new row was written."""
        if chat_id in self._noted:
            return False
        self._noted.add(chat_id)
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO core.sources (kind, tg_chat_id, title, enabled, reader, is_present)
                           VALUES (%s, %s, %s, false, %s, true) ON CONFLICT (tg_chat_id) DO NOTHING""",
                        (kind, chat_id, title or f"chat {chat_id}", self.reader))
            return cur.rowcount == 1


def chat_kind(chat) -> str:
    """Telethon entity -> sources.kind."""
    if getattr(chat, "megagroup", False):
        return "supergroup"
    if getattr(chat, "broadcast", False):
        return "channel"
    if chat.__class__.__name__ == "User":
        return "dm"
    return "group"
