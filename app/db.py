"""SQLite storage: payment reminders (one row per photo and due date), the private chats that pressed /start,
and which of those chats was given which reminder."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    thread_id INTEGER,                       -- forum topic, NULL in ordinary groups
    photo_message_id INTEGER NOT NULL,
    due_date TEXT NOT NULL,                  -- YYYY-MM-DD
    customer TEXT,
    order_number TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,                -- when the present payment day was written, local time
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | sent (given to at least one chat) | cancelled
    tag TEXT,                                -- the name from the reply: who talked to the customer
    file_id TEXT                             -- the photo in Telegram: sent along when it could not be read
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (status, due_date);
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER PRIMARY KEY,             -- private chat that pressed /start: reminders are sent here
    name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    reminder_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,                -- the /start chat that was given the reminder
    sent_at TEXT NOT NULL,                   -- local time, YYYY-MM-DDTHH:MM:SS
    PRIMARY KEY (reminder_id, chat_id)
);
CREATE INDEX IF NOT EXISTS deliveries_chat ON deliveries (chat_id, sent_at);
"""


class Reminders:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        # a database made before a column existed gets it here
        have = {row["name"] for row in self._db.execute("PRAGMA table_info(reminders)")}
        with self._db:
            for column in ("tag", "file_id"):
                if column not in have:
                    self._db.execute(f"ALTER TABLE reminders ADD COLUMN {column} TEXT")

    @staticmethod
    def _stamp(now: datetime | None) -> str:
        return (now or datetime.now()).replace(tzinfo=None).isoformat(timespec="seconds")

    def add(self, *, chat_id: int, thread_id: int | None, photo_message_id: int, due_date: date,
            customer: str | None, order_number: str | None, created_by: str | None, tag: str | None = None,
            file_id: str | None = None, now: datetime | None = None) -> int:
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO reminders (chat_id, thread_id, photo_message_id, due_date, customer, order_number,"
                " created_by, created_at, tag, file_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, thread_id, photo_message_id, due_date.isoformat(), customer, order_number, created_by,
                 self._stamp(now), tag, file_id),
            )
        return cursor.lastrowid

    def pending_for_photo(self, chat_id: int, photo_message_id: int) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM reminders WHERE chat_id = ? AND photo_message_id = ? AND status = 'pending'",
            (chat_id, photo_message_id),
        ).fetchone()

    def sent_for_photo(self, chat_id: int, photo_message_id: int, due_date: date) -> sqlite3.Row | None:
        """The reminder of this photo for this day that has already gone out, if there is one."""
        return self._db.execute(
            "SELECT * FROM reminders WHERE chat_id = ? AND photo_message_id = ? AND due_date = ? AND status = 'sent'",
            (chat_id, photo_message_id, due_date.isoformat()),
        ).fetchone()

    def pending(self, chat_id: int | None = None) -> list[sqlite3.Row]:
        """Pending reminders of one group; of every group when no chat is given."""
        return self._db.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND (? IS NULL OR chat_id = ?) ORDER BY due_date, id",
            (chat_id, chat_id),
        ).fetchall()

    def due(self, today: date) -> list[sqlite3.Row]:
        """Reminders whose day has come but which nobody has been given yet."""
        return self._db.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_date <= ? ORDER BY due_date, id",
            (today.isoformat(),),
        ).fetchall()

    def between(self, start: date, end: date) -> list[sqlite3.Row]:
        """Everyone who is to pay from `start` to `end`, both days included, whether reminded already or not."""
        return self._db.execute(
            "SELECT * FROM reminders WHERE status != 'cancelled' AND due_date BETWEEN ? AND ? ORDER BY due_date, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    def reschedule(self, reminder_id: int, due_date: date, tag: str | None = None, now: datetime | None = None) -> None:
        """Move the payment day, written at `now`; a reply without a name keeps the name written before."""
        with self._db:
            self._db.execute(
                "UPDATE reminders SET due_date = ?, tag = COALESCE(?, tag), created_at = ? WHERE id = ?",
                (due_date.isoformat(), tag, self._stamp(now), reminder_id),
            )

    def set_status(self, reminder_id: int, status: str) -> None:
        with self._db:
            self._db.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))

    def subscribe(self, chat_id: int, name: str | None) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO subscribers (chat_id, name, created_at) VALUES (?, ?, ?)",
                (chat_id, name, datetime.now().isoformat(timespec="seconds")),
            )

    def unsubscribe(self, chat_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))

    def subscribers(self) -> list[int]:
        rows = self._db.execute("SELECT chat_id FROM subscribers ORDER BY created_at, chat_id").fetchall()
        return [row["chat_id"] for row in rows]

    def undelivered(self, chat_id: int, today: date) -> list[sqlite3.Row]:
        """What this chat is still owed: today's reminders, and older ones that go out only today.

        An older one that the others got on an earlier day is not repeated to a chat that pressed /start later.
        """
        return self._db.execute(
            "SELECT * FROM reminders r WHERE"
            " NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.reminder_id = r.id AND d.chat_id = :chat)"
            " AND ((r.status = 'pending' AND r.due_date <= :today)"
            "  OR (r.status = 'sent' AND (r.due_date = :today OR EXISTS ("
            "   SELECT 1 FROM deliveries d WHERE d.reminder_id = r.id AND substr(d.sent_at, 1, 10) = :today))))"
            " ORDER BY r.due_date, r.id",
            {"chat": chat_id, "today": today.isoformat()},
        ).fetchall()

    def deliver(self, reminder_ids: list[int], chat_id: int, when: datetime) -> None:
        """Record that the chat was given these reminders."""
        sent_at = when.replace(tzinfo=None).isoformat(timespec="seconds")
        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO deliveries (reminder_id, chat_id, sent_at) VALUES (?, ?, ?)",
                [(reminder_id, chat_id, sent_at) for reminder_id in reminder_ids],
            )
            self._db.executemany(
                "UPDATE reminders SET status = 'sent' WHERE id = ? AND status = 'pending'",
                [(reminder_id,) for reminder_id in reminder_ids],
            )

    def delivered_on(self, chat_id: int, day: date) -> list[sqlite3.Row]:
        """What the chat was given on that day, in the order it was given: this is the numbering of its day's list."""
        return self._db.execute(
            "SELECT r.* FROM deliveries d JOIN reminders r ON r.id = d.reminder_id"
            " WHERE d.chat_id = ? AND substr(d.sent_at, 1, 10) = ? ORDER BY d.sent_at, r.due_date, r.id",
            (chat_id, day.isoformat()),
        ).fetchall()
