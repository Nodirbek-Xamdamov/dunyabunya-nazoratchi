"""The dashboard's own small read-write database (dashboard/data/state.db, STATE_DB_PATH).

* ``layout_versions`` -- the layout overlay (ui.layout) that the AI assistant and the chat buttons
  change. Every change is a new row, so nothing is ever lost: undo writes the earlier state as a new
  row. One chat message (or one button press) is one change group = one undo step.
* ``chat_messages`` -- the conversation with the assistant (it survives reloads and restarts).
* ``agent_prompts`` -- the system prompts edited on the «AI agentlar» page: every save is a new row,
  the newest row of an agent is its prompt, an empty text means the default.
* ``custom_agents`` -- agents created on that page; ``agent_models`` -- the model chosen for a
  built-in agent; ``connections`` -- Odoo and Telegram bot connections of the agents (the key or
  token is DPAPI-encrypted, core.secrets); ``bot_offsets`` / ``bot_messages`` -- the agents'
  Telegram bots (agent_bots.py): the next update to read and the conversations.

This file has nothing to do with the bot's monitoring database, which stays read-only (core.db).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable

from core.settings import Settings

_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS layout_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    group_id TEXT NOT NULL,
    source TEXT NOT NULL,
    summary TEXT NOT NULL,
    reverts TEXT NOT NULL DEFAULT '',
    layout_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    agents TEXT NOT NULL DEFAULT '',
    changes_json TEXT NOT NULL DEFAULT '[]',
    request_ids TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    files TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS agent_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_agent_prompts_agent ON agent_prompts (agent, id);
CREATE TABLE IF NOT EXISTS custom_agents (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    routing TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    parent TEXT NOT NULL,
    model TEXT NOT NULL,
    tool_groups TEXT NOT NULL DEFAULT '[]',
    odoo_connection TEXT,
    telegram_connection TEXT,
    generated_prompt TEXT NOT NULL DEFAULT '',
    missing TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS agent_models (
    agent TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    secret BLOB,
    status TEXT NOT NULL DEFAULT 'new',
    status_text TEXT NOT NULL DEFAULT '',
    checked_at TEXT
);
CREATE TABLE IF NOT EXISTS bot_offsets (
    connection TEXT PRIMARY KEY,
    next_update INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    connection TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bot_messages_chat ON bot_messages (connection, chat_id, id);
"""
# columns added after the first release: (table, column, definition)
_MIGRATIONS = (("chat_messages", "files", "TEXT NOT NULL DEFAULT '[]'"),)
_MIGRATED: set[str] = set()
UNDO = "undo"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _dump(layout: dict) -> str:
    return json.dumps(layout, ensure_ascii=False, sort_keys=True)


def _same(first: dict, second: dict) -> bool:
    """Equal layouts; empty parts ({} pages, [] lists) count as not there."""
    def canonical(layout: dict) -> str:
        return _dump({key: value for key, value in layout.items() if value})

    return canonical(first) == canonical(second)


def _load(text: str | None) -> dict:
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def new_group(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@contextmanager
def connect(settings: Settings):
    """One short connection; a process-wide lock keeps writers in order (each use is milliseconds)."""
    path = settings.state_db_path
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            if str(path) not in _MIGRATED:
                for table, column, definition in _MIGRATIONS:
                    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                    if column not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                _MIGRATED.add(str(path))
            yield conn
        finally:
            conn.close()


@contextmanager
def _transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    if conn.in_transaction:
        conn.execute("COMMIT")


def _latest(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT layout_json FROM layout_versions ORDER BY id DESC LIMIT 1").fetchone()
    return _load(row[0]) if row else {}


def _insert(conn: sqlite3.Connection, layout: dict, group_id: str, source: str, summary: str, reverts: str = "") -> None:
    conn.execute("INSERT INTO layout_versions (created_at, group_id, source, summary, reverts, layout_json) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (_now(), group_id, source, summary, reverts, _dump(layout)))


# --------------------------------------------------------------------------- layout


def current_layout(settings: Settings) -> dict:
    with connect(settings) as conn:
        return _latest(conn)


def update_layout(settings: Settings, change: Callable[[dict], tuple[dict, str]], *, group_id: str,
                  source: str) -> str | None:
    """Applies ``change(current) -> (new_layout, summary)`` inside one write transaction and stores a
    new version when the layout really changed. Returns the summary, or None when nothing changed.
    Exceptions from ``change`` (e.g. ui.layout.LayoutError) leave the database untouched."""
    with connect(settings) as conn, _transaction(conn):
        current = _latest(conn)
        new, summary = change(current)
        if _same(new, current):
            return None
        _insert(conn, new, group_id, source, summary)
        return summary


def _undo_candidates(rows: list[tuple]) -> tuple[list[str], dict[str, dict]]:
    """Change groups that can still be undone, newest first (undo groups and undone groups skipped)."""
    undone = set()
    for _vid, _gid, source, _summary, reverts in rows:
        if source == UNDO and reverts:
            undone.update(reverts.split(","))
    order: list[str] = []
    groups: dict[str, dict] = {}
    for vid, gid, source, summary, _reverts in rows:
        if source == UNDO or gid in undone:
            continue
        if gid not in groups:
            groups[gid] = {"first": vid, "summaries": []}
            order.append(gid)
        groups[gid]["summaries"].append(summary)
    return list(reversed(order)), groups


def _version_rows(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute("SELECT id, group_id, source, summary, reverts FROM layout_versions ORDER BY id").fetchall()


def can_undo(settings: Settings) -> bool:
    with connect(settings) as conn:
        order, _groups = _undo_candidates(_version_rows(conn))
    return bool(order)


def undo(settings: Settings, *, group_id: str, steps: int = 1) -> str | None:
    """Reverts the last ``steps`` change groups that are not undone yet. Returns what was reverted
    (the summaries of those changes) or None when there is nothing to undo."""
    with connect(settings) as conn, _transaction(conn):
        order, groups = _undo_candidates(_version_rows(conn))
        picked = order[:max(1, steps)]
        if not picked:
            return None
        first_id = min(groups[gid]["first"] for gid in picked)
        row = conn.execute("SELECT layout_json FROM layout_versions WHERE id < ? ORDER BY id DESC LIMIT 1",
                           (first_id,)).fetchone()
        target = _load(row[0]) if row else {}
        summary = "; ".join(text for gid in picked for text in groups[gid]["summaries"])
        _insert(conn, target, group_id, UNDO, summary, ",".join(picked))
        return summary


def restore(settings: Settings, version_id: int, *, group_id: str, summary: str) -> bool:
    """Makes an earlier version the current layout again (as a new version, so it can be undone)."""
    with connect(settings) as conn, _transaction(conn):
        row = conn.execute("SELECT layout_json FROM layout_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            return False
        if _same(_load(row[0]), _latest(conn)):
            return False
        _insert(conn, _load(row[0]), group_id, "restore", summary)
        return True


def history(settings: Settings, limit: int = 20) -> list[dict]:
    """Latest layout versions, newest first."""
    with connect(settings) as conn:
        rows = conn.execute("SELECT id, created_at, group_id, source, summary FROM layout_versions "
                            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(("id", "created_at", "group_id", "source", "summary"), row)) for row in rows]


# --------------------------------------------------------------------------- chat


def chat_add(settings: Settings, role: str, text: str, *, agents: str = "", changes: list[str] | None = None,
             request_ids: list[str] | None = None, input_tokens: int = 0, output_tokens: int = 0,
             files: list[str] | None = None) -> int:
    """``files``: names of pictures stored by core.images."""
    with connect(settings) as conn:
        cursor = conn.execute(
            "INSERT INTO chat_messages (created_at, role, text, agents, changes_json, request_ids, input_tokens, "
            "output_tokens, files) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), role, text, agents, json.dumps(changes or [], ensure_ascii=False), ",".join(request_ids or []),
             int(input_tokens), int(output_tokens), json.dumps(files or [])))
        return int(cursor.lastrowid)


def chat_recent(settings: Settings, limit: int) -> list[dict]:
    """The last ``limit`` messages, oldest first."""
    if limit <= 0:
        return []
    with connect(settings) as conn:
        rows = conn.execute("SELECT id, created_at, role, text, agents, changes_json, files FROM chat_messages "
                            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    messages = []
    for row in reversed(rows):
        item = dict(zip(("id", "created_at", "role", "text", "agents", "changes_json", "files"), row))
        for source, target in (("changes_json", "changes"), ("files", "files")):
            try:
                value = json.loads(item.pop(source) or "[]")
            except ValueError:
                value = []
            item[target] = [str(entry) for entry in value] if isinstance(value, list) else []
        messages.append(item)
    return messages


def chat_count_since(settings: Settings, since_utc: datetime, role: str = "user") -> int:
    with connect(settings) as conn:
        row = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE role = ? AND created_at >= ?",
                           (role, since_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))).fetchone()
    return int(row[0]) if row else 0


def local_time(value: str, tz) -> datetime | None:
    """Stored UTC text -> aware local time."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(tz)
    except (TypeError, ValueError):
        return None


def local_midnight_utc(tz) -> datetime:
    now = datetime.now(tz)
    return (now - timedelta(hours=now.hour, minutes=now.minute, seconds=now.second,
                            microseconds=now.microsecond)).astimezone(timezone.utc)


# --------------------------------------------------------------------------- agent system prompts

_PROMPT_COLUMNS = ("id", "created_at", "action", "text")


def agent_prompt(settings: Settings, agent: str) -> dict | None:
    """The newest saved prompt row of an agent (``text`` empty = the default), or None when it was
    never changed."""
    with connect(settings) as conn:
        row = conn.execute("SELECT id, created_at, action, text FROM agent_prompts WHERE agent = ? "
                           "ORDER BY id DESC LIMIT 1", (agent,)).fetchone()
    return dict(zip(_PROMPT_COLUMNS, row)) if row else None


def set_agent_prompt(settings: Settings, agent: str, text: str, action: str) -> int:
    """Stores a new prompt version (``text`` empty = back to the default). ``action``: save | reset |
    restore."""
    with connect(settings) as conn:
        cursor = conn.execute("INSERT INTO agent_prompts (created_at, agent, action, text) VALUES (?, ?, ?, ?)",
                              (_now(), agent, action, text))
        return int(cursor.lastrowid)


def agent_prompt_history(settings: Settings, agent: str, limit: int = 20) -> list[dict]:
    """Saved prompt versions of an agent, newest first."""
    with connect(settings) as conn:
        rows = conn.execute("SELECT id, created_at, action, text FROM agent_prompts WHERE agent = ? "
                            "ORDER BY id DESC LIMIT ?", (agent, limit)).fetchall()
    return [dict(zip(_PROMPT_COLUMNS, row)) for row in rows]


def agent_prompt_version(settings: Settings, agent: str, version_id: int) -> dict | None:
    with connect(settings) as conn:
        row = conn.execute("SELECT id, created_at, action, text FROM agent_prompts WHERE agent = ? AND id = ?",
                           (agent, version_id)).fetchone()
    return dict(zip(_PROMPT_COLUMNS, row)) if row else None


# --------------------------------------------------------------------------- agents made on the page

_AGENT_COLUMNS = ("id", "created_at", "updated_at", "name", "summary", "routing", "task", "parent", "model",
                  "tool_groups", "odoo_connection", "telegram_connection", "generated_prompt", "missing")
_JSON_LISTS = ("tool_groups", "missing")


def _agent_row(row) -> dict:
    item = dict(zip(_AGENT_COLUMNS, row))
    for key in _JSON_LISTS:
        try:
            value = json.loads(item[key] or "[]")
        except ValueError:
            value = []
        item[key] = value if isinstance(value, list) else []
    return item


def custom_agents(settings: Settings) -> list[dict]:
    """Agents created on the «AI agentlar» page, oldest first."""
    with connect(settings) as conn:
        rows = conn.execute(f"SELECT {', '.join(_AGENT_COLUMNS)} FROM custom_agents ORDER BY created_at, id").fetchall()
    return [_agent_row(row) for row in rows]


def custom_agent(settings: Settings, agent_id: str) -> dict | None:
    with connect(settings) as conn:
        row = conn.execute(f"SELECT {', '.join(_AGENT_COLUMNS)} FROM custom_agents WHERE id = ?",
                           (agent_id,)).fetchone()
    return _agent_row(row) if row else None


def save_custom_agent(settings: Settings, agent: dict) -> None:
    """Inserts or updates one agent (``agent`` holds every column except the timestamps)."""
    values = {key: agent.get(key) for key in _AGENT_COLUMNS if key not in ("created_at", "updated_at")}
    for key in _JSON_LISTS:
        values[key] = json.dumps(values.get(key) or [], ensure_ascii=False)
    for key in ("summary", "routing", "task", "generated_prompt"):
        values[key] = values.get(key) or ""
    columns = list(values)
    now = _now()
    with connect(settings) as conn:
        conn.execute(
            f"INSERT INTO custom_agents (created_at, updated_at, {', '.join(columns)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET "
            "updated_at = excluded.updated_at, "
            + ", ".join(f"{column} = excluded.{column}" for column in columns if column != "id"),
            (now, now, *values.values()))


def delete_custom_agent(settings: Settings, agent_id: str) -> list[str]:
    """Deletes an agent. Its sub-agents move one level up (to its parent), its Telegram bot connection
    is deleted, and so is its Odoo connection when no other agent uses it. Returns the moved ids."""
    with connect(settings) as conn, _transaction(conn):
        row = conn.execute("SELECT parent, odoo_connection, telegram_connection FROM custom_agents WHERE id = ?",
                           (agent_id,)).fetchone()
        if row is None:
            return []
        parent, odoo, telegram = row
        moved = [item[0] for item in conn.execute("SELECT id FROM custom_agents WHERE parent = ?", (agent_id,))]
        conn.execute("UPDATE custom_agents SET parent = ?, updated_at = ? WHERE parent = ?", (parent, _now(), agent_id))
        conn.execute("DELETE FROM custom_agents WHERE id = ?", (agent_id,))
        if telegram:
            conn.execute("DELETE FROM connections WHERE id = ?", (telegram,))
            conn.execute("DELETE FROM bot_offsets WHERE connection = ?", (telegram,))
        if odoo and not conn.execute("SELECT 1 FROM custom_agents WHERE odoo_connection = ? LIMIT 1",
                                     (odoo,)).fetchone():
            conn.execute("DELETE FROM connections WHERE id = ?", (odoo,))
        return moved


def agent_model(settings: Settings, agent: str) -> str | None:
    """The model chosen on the page for a built-in agent (None = the default from the settings)."""
    with connect(settings) as conn:
        row = conn.execute("SELECT model FROM agent_models WHERE agent = ?", (agent,)).fetchone()
    return row[0] if row else None


def set_agent_model(settings: Settings, agent: str, model: str | None) -> None:
    with connect(settings) as conn:
        if model:
            conn.execute("INSERT INTO agent_models (agent, model, updated_at) VALUES (?, ?, ?) ON CONFLICT(agent) "
                         "DO UPDATE SET model = excluded.model, updated_at = excluded.updated_at",
                         (agent, model, _now()))
        else:
            conn.execute("DELETE FROM agent_models WHERE agent = ?", (agent,))


# --------------------------------------------------------------------------- connections (Odoo, Telegram bots)

_CONNECTION_COLUMNS = ("id", "created_at", "updated_at", "kind", "name", "config", "secret", "status",
                       "status_text", "checked_at")


def _connection_row(row) -> dict:
    item = dict(zip(_CONNECTION_COLUMNS, row))
    try:
        config = json.loads(item["config"] or "{}")
    except ValueError:
        config = {}
    item["config"] = config if isinstance(config, dict) else {}
    return item


def connection(settings: Settings, connection_id: str | None) -> dict | None:
    if not connection_id:
        return None
    with connect(settings) as conn:
        row = conn.execute(f"SELECT {', '.join(_CONNECTION_COLUMNS)} FROM connections WHERE id = ?",
                           (connection_id,)).fetchone()
    return _connection_row(row) if row else None


def connections(settings: Settings, kind: str) -> list[dict]:
    with connect(settings) as conn:
        rows = conn.execute(f"SELECT {', '.join(_CONNECTION_COLUMNS)} FROM connections WHERE kind = ? "
                            "ORDER BY created_at, id", (kind,)).fetchall()
    return [_connection_row(row) for row in rows]


def save_connection(settings: Settings, connection_id: str, kind: str, name: str, config: dict,
                    secret: bytes | None, status: str = "ok", status_text: str = "") -> None:
    """Inserts or updates a connection; ``secret`` None keeps the stored one."""
    now = _now()
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO connections (id, created_at, updated_at, kind, name, config, secret, status, status_text, "
            "checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "updated_at = excluded.updated_at, name = excluded.name, config = excluded.config, "
            "secret = COALESCE(excluded.secret, connections.secret), status = excluded.status, "
            "status_text = excluded.status_text, checked_at = excluded.checked_at",
            (connection_id, now, now, kind, name, json.dumps(config, ensure_ascii=False), secret, status,
             status_text, now))


def set_connection_status(settings: Settings, connection_id: str, status: str, status_text: str = "") -> None:
    with connect(settings) as conn:
        conn.execute("UPDATE connections SET status = ?, status_text = ?, checked_at = ? WHERE id = ?",
                     (status, status_text, _now(), connection_id))


def delete_connection(settings: Settings, connection_id: str) -> None:
    with connect(settings) as conn, _transaction(conn):
        conn.execute("DELETE FROM connections WHERE id = ?", (connection_id,))
        conn.execute("DELETE FROM bot_offsets WHERE connection = ?", (connection_id,))
        conn.execute("UPDATE custom_agents SET odoo_connection = NULL WHERE odoo_connection = ?", (connection_id,))
        conn.execute("UPDATE custom_agents SET telegram_connection = NULL WHERE telegram_connection = ?",
                     (connection_id,))


# --------------------------------------------------------------------------- the agents' Telegram bots


def bot_offset(settings: Settings, connection_id: str) -> int:
    with connect(settings) as conn:
        row = conn.execute("SELECT next_update FROM bot_offsets WHERE connection = ?", (connection_id,)).fetchone()
    return int(row[0]) if row else 0


def set_bot_offset(settings: Settings, connection_id: str, next_update: int) -> None:
    with connect(settings) as conn:
        conn.execute("INSERT INTO bot_offsets (connection, next_update) VALUES (?, ?) ON CONFLICT(connection) "
                     "DO UPDATE SET next_update = excluded.next_update", (connection_id, int(next_update)))


def add_bot_message(settings: Settings, connection_id: str, chat_id: int, role: str, text: str) -> None:
    with connect(settings) as conn:
        conn.execute("INSERT INTO bot_messages (created_at, connection, chat_id, role, text) VALUES (?, ?, ?, ?, ?)",
                     (_now(), connection_id, int(chat_id), role, text))


def bot_history(settings: Settings, connection_id: str, chat_id: int, limit: int) -> list[dict]:
    """The last ``limit`` messages of one chat with an agent's bot, oldest first."""
    if limit <= 0:
        return []
    with connect(settings) as conn:
        rows = conn.execute("SELECT role, text FROM bot_messages WHERE connection = ? AND chat_id = ? "
                            "ORDER BY id DESC LIMIT ?", (connection_id, int(chat_id), limit)).fetchall()
    return [{"role": role, "text": text} for role, text in reversed(rows)]


def clear_bot_history(settings: Settings, connection_id: str, chat_id: int) -> None:
    with connect(settings) as conn:
        conn.execute("DELETE FROM bot_messages WHERE connection = ? AND chat_id = ?", (connection_id, int(chat_id)))


def bot_questions_since(settings: Settings, connection_id: str, since_utc: datetime) -> int:
    with connect(settings) as conn:
        row = conn.execute("SELECT COUNT(*) FROM bot_messages WHERE connection = ? AND role = 'user' "
                           "AND created_at >= ?",
                           (connection_id, since_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))).fetchone()
    return int(row[0]) if row else 0
