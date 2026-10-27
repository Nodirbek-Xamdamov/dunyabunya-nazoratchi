"""All SQL of the dashboard (read-only, parameterised). Functions take a ``db.Session`` and plain
values; times are stored-format UTC strings (``Period.start_db``). Definitions: see core.metrics."""

from __future__ import annotations

from datetime import date

import pandas as pd

from core import metrics as m_
from core.db import Session
from core.text import like_pattern

_ESC = "ESCAPE '\\'"
# a check's deadline is at most a day after its start: lets the deadline index narrow the scan
_CHECK_WINDOW = ("k.deadline >= ? AND k.deadline < datetime(?, '+1 day') "
                 "AND k.started_at >= ? AND k.started_at < ?")


def _check_window(start: str, end: str) -> tuple[str, list]:
    return _CHECK_WINDOW, [start, end, start, end]


def _cat_case(alias: str = "c") -> tuple[str, list]:
    """Category as the report counts it: unknown/empty -> "Boshqa"; NULL when not company related."""
    names = list(m_.category_names())
    marks = ",".join("?" * len(names))
    sql = (f"(CASE WHEN {alias}.is_company_related = 1 THEN "
           f"(CASE WHEN {alias}.category IN ({marks}) THEN {alias}.category ELSE ? END) END)")
    return sql, [*names, m_.OTHER_CATEGORY]


def has_table(s: Session, name: str) -> bool:
    """Tables added by newer bot versions (media_files) may be missing in older or demo databases."""
    return bool(s.scalar(f"has_table.{name}", "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                         [name], default=0))


def _customer_where(start: str, end: str, chat_id: int | None = None, alias: str = "m") -> tuple[str, list]:
    sql = f"{alias}.sent_at_utc >= ? AND {alias}.sent_at_utc < ? AND {alias}.is_staff = 0"
    params: list = [start, end]
    if chat_id is not None:
        sql += f" AND {alias}.chat_id = ?"
        params.append(chat_id)
    return sql, params


# --------------------------------------------------------------------------- totals and series


def totals(s: Session, start: str, end: str, chat_id: int | None = None) -> dict:
    where, params = _customer_where(start, end, chat_id)
    row = s.one("totals.messages", f"""
        SELECT COUNT(*),
               COALESCE(SUM(c.is_company_related = 1), 0),
               COALESCE(SUM(c.is_company_related = 1 AND c.sentiment = ?), 0),
               COALESCE(SUM(c.is_company_related = 1 AND c.sentiment = ?), 0),
               COALESCE(SUM(c.is_company_related = 0), 0),
               COALESCE(SUM(m.status = 'pending'), 0),
               COALESCE(SUM(m.status = 'failed'), 0)
        FROM messages m LEFT JOIN classifications c ON c.message_ref = m.id
        WHERE {where}""", [m_.COMPLAINT, m_.POSITIVE, *params])
    window, wparams = _check_window(start, end)
    chat_sql = " AND k.chat_id = ?" if chat_id is not None else ""
    checks = s.one("totals.checks", f"""
        SELECT COALESCE(SUM(k.status = 'answered'), 0), COALESCE(SUM(k.status = 'missed'), 0),
               COALESCE(SUM(k.status = 'pending'), 0)
        FROM manager_checks k WHERE {window}{chat_sql}""", [*wparams, *([chat_id] if chat_id is not None else [])])
    keys = ("customer", "related", "complaints", "positive", "filtered", "pending", "failed")
    result = dict(zip(keys, row or (0,) * len(keys)))
    result.update(dict(zip(("answered", "missed", "waiting"), checks or (0, 0, 0))))
    return result


def daily(s: Session, start: str, end: str, offset: str, chat_id: int | None = None) -> pd.DataFrame:
    """Per local day: customer messages, related, complaints, positive, filtered, answered, missed."""
    where, params = _customer_where(start, end, chat_id)
    msgs = s.df("daily.messages", f"""
        SELECT date(m.sent_at_utc, ?) AS day, COUNT(*) AS customer,
               COALESCE(SUM(c.is_company_related = 1), 0) AS related,
               COALESCE(SUM(c.is_company_related = 1 AND c.sentiment = ?), 0) AS complaints,
               COALESCE(SUM(c.is_company_related = 1 AND c.sentiment = ?), 0) AS positive,
               COALESCE(SUM(c.is_company_related = 0), 0) AS filtered
        FROM messages m LEFT JOIN classifications c ON c.message_ref = m.id
        WHERE {where} GROUP BY day""", [offset, m_.COMPLAINT, m_.POSITIVE, *params])
    window, wparams = _check_window(start, end)
    chat_sql = " AND k.chat_id = ?" if chat_id is not None else ""
    checks = s.df("daily.checks", f"""
        SELECT date(k.started_at, ?) AS day, COALESCE(SUM(k.status = 'answered'), 0) AS answered,
               COALESCE(SUM(k.status = 'missed'), 0) AS missed
        FROM manager_checks k WHERE {window}{chat_sql} GROUP BY day""",
                  [offset, *wparams, *([chat_id] if chat_id is not None else [])])
    frame = msgs.merge(checks, on="day", how="outer")
    for column in ("customer", "related", "complaints", "positive", "filtered", "answered", "missed"):
        if column not in frame:
            frame[column] = 0
        frame[column] = frame[column].fillna(0).astype(int)
    frame["neutral"] = (frame["related"] - frame["complaints"] - frame["positive"]).clip(lower=0)
    return frame.sort_values("day").reset_index(drop=True)


def by_category(s: Session, start: str, end: str, chat_id: int | None = None) -> pd.DataFrame:
    cat_sql, cat_params = _cat_case()
    where, params = _customer_where(start, end, chat_id)
    frame = s.df("by_category", f"""
        SELECT {cat_sql} AS category, COUNT(*) AS related,
               COALESCE(SUM(c.sentiment = ?), 0) AS complaints, COALESCE(SUM(c.sentiment = ?), 0) AS positive
        FROM messages m JOIN classifications c ON c.message_ref = m.id
        WHERE {where} AND c.is_company_related = 1 GROUP BY 1""", [*cat_params, m_.COMPLAINT, m_.POSITIVE, *params])
    base = pd.DataFrame({"category": list(m_.category_names())})
    frame = base.merge(frame, on="category", how="left").fillna(0)
    for column in ("related", "complaints", "positive"):
        frame[column] = frame[column].astype(int)
    frame["neutral"] = (frame["related"] - frame["complaints"] - frame["positive"]).clip(lower=0)
    return frame


def category_daily(s: Session, start: str, end: str, offset: str) -> pd.DataFrame:
    cat_sql, cat_params = _cat_case()
    where, params = _customer_where(start, end)
    return s.df("category_daily", f"""
        SELECT date(m.sent_at_utc, ?) AS day, {cat_sql} AS category, COUNT(*) AS related,
               COALESCE(SUM(c.sentiment = ?), 0) AS complaints, COALESCE(SUM(c.sentiment = ?), 0) AS positive
        FROM messages m JOIN classifications c ON c.message_ref = m.id
        WHERE {where} AND c.is_company_related = 1 GROUP BY day, category""",
                [offset, *cat_params, m_.COMPLAINT, m_.POSITIVE, *params])


def hour_weekday(s: Session, start: str, end: str, offset: str, chat_id: int | None = None) -> pd.DataFrame:
    """Company-related customer messages by local weekday (0 = Monday) and hour."""
    where, params = _customer_where(start, end, chat_id)
    frame = s.df("hour_weekday", f"""
        SELECT CAST(strftime('%w', m.sent_at_utc, ?) AS INTEGER) AS wd,
               CAST(strftime('%H', m.sent_at_utc, ?) AS INTEGER) AS hour, COUNT(*) AS n
        FROM messages m JOIN classifications c ON c.message_ref = m.id
        WHERE {where} AND c.is_company_related = 1 GROUP BY wd, hour""", [offset, offset, *params])
    if not frame.empty:
        frame["wd"] = (frame["wd"] + 6) % 7
    return frame


def group_category_complaints(s: Session, start: str, end: str, top: int = 15) -> pd.DataFrame:
    where, params = _customer_where(start, end)
    cat_sql, cat_params = _cat_case()
    return s.df("group_category_complaints", f"""
        WITH top_groups AS (
            SELECT m.chat_id, COUNT(*) AS n FROM messages m JOIN classifications c ON c.message_ref = m.id
            WHERE {where} AND c.is_company_related = 1 AND c.sentiment = ?
            GROUP BY m.chat_id ORDER BY n DESC LIMIT ?)
        SELECT m.chat_id, COALESCE(g.title, CAST(m.chat_id AS TEXT)) AS title, {cat_sql} AS category,
               COUNT(*) AS complaints
        FROM messages m JOIN classifications c ON c.message_ref = m.id
        JOIN top_groups t ON t.chat_id = m.chat_id LEFT JOIN groups g ON g.chat_id = m.chat_id
        WHERE {where} AND c.is_company_related = 1 AND c.sentiment = ?
        GROUP BY m.chat_id, category""",
                [*params, m_.COMPLAINT, top, *cat_params, *params, m_.COMPLAINT])


# --------------------------------------------------------------------------- groups


def groups_overview(s: Session, start: str, end: str, prev_start: str, prev_end: str) -> pd.DataFrame:
    """One row per known group with period numbers and previous-period complaints."""
    window, wparams = _check_window(start, end)
    frame = s.df("groups_overview", f"""
        SELECT g.chat_id, COALESCE(g.title, CAST(g.chat_id AS TEXT)) AS title, g.is_present,
               g.manager_username, g.admin_user_ids,
               COALESCE(a.customer, 0) AS customer, COALESCE(a.related, 0) AS related,
               COALESCE(a.complaints, 0) AS complaints, COALESCE(a.positive, 0) AS positive,
               COALESCE(a.filtered, 0) AS filtered,
               COALESCE(k.answered, 0) AS answered, COALESCE(k.missed, 0) AS missed,
               COALESCE(k.waiting, 0) AS waiting,
               COALESCE(p.complaints, 0) AS prev_complaints, COALESCE(p.related, 0) AS prev_related,
               (SELECT MAX(x.sent_at_utc) FROM messages x WHERE x.chat_id = g.chat_id) AS last_activity
        FROM groups g
        LEFT JOIN (SELECT m.chat_id, COUNT(*) AS customer, SUM(c.is_company_related = 1) AS related,
                          SUM(c.is_company_related = 1 AND c.sentiment = ?) AS complaints,
                          SUM(c.is_company_related = 1 AND c.sentiment = ?) AS positive,
                          SUM(c.is_company_related = 0) AS filtered
                   FROM messages m LEFT JOIN classifications c ON c.message_ref = m.id
                   WHERE m.sent_at_utc >= ? AND m.sent_at_utc < ? AND m.is_staff = 0
                   GROUP BY m.chat_id) a ON a.chat_id = g.chat_id
        LEFT JOIN (SELECT k.chat_id, SUM(k.status = 'answered') AS answered, SUM(k.status = 'missed') AS missed,
                          SUM(k.status = 'pending') AS waiting
                   FROM manager_checks k WHERE {window} GROUP BY k.chat_id) k ON k.chat_id = g.chat_id
        LEFT JOIN (SELECT m.chat_id, SUM(c.is_company_related = 1) AS related,
                          SUM(c.is_company_related = 1 AND c.sentiment = ?) AS complaints
                   FROM messages m LEFT JOIN classifications c ON c.message_ref = m.id
                   WHERE m.sent_at_utc >= ? AND m.sent_at_utc < ? AND m.is_staff = 0
                   GROUP BY m.chat_id) p ON p.chat_id = g.chat_id""",
                 [m_.COMPLAINT, m_.POSITIVE, start, end, *wparams, m_.COMPLAINT, prev_start, prev_end])
    frame["is_present"] = frame["is_present"].fillna(1).astype(bool)
    frame["has_manager"] = frame["manager_username"].notna()
    frame["admins"] = frame["admin_user_ids"].fillna("").map(lambda v: len([p for p in str(v).split(",") if p.strip()]))
    return frame


def group_sparks(s: Session, start: str, end: str, offset: str) -> pd.DataFrame:
    where, params = _customer_where(start, end)
    return s.df("group_sparks", f"""
        SELECT m.chat_id, date(m.sent_at_utc, ?) AS day, COUNT(*) AS n
        FROM messages m JOIN classifications c ON c.message_ref = m.id
        WHERE {where} AND c.is_company_related = 1 GROUP BY m.chat_id, day""", [offset, *params])


def group_info(s: Session, chat_id: int) -> dict | None:
    row = s.one("group_info", """
        SELECT g.chat_id, COALESCE(g.title, CAST(g.chat_id AS TEXT)), g.is_present, g.admin_user_ids,
               g.manager_username, g.admins_checked_at,
               (SELECT MIN(sent_at_utc) FROM messages WHERE chat_id = g.chat_id),
               (SELECT MAX(sent_at_utc) FROM messages WHERE chat_id = g.chat_id)
        FROM groups g WHERE g.chat_id = ?""", [chat_id])
    if row is None:
        return None
    keys = ("chat_id", "title", "is_present", "admin_user_ids", "manager_username", "admins_checked_at",
            "first_at", "last_at")
    info = dict(zip(keys, row))
    admin_ids = [int(part) for part in str(info["admin_user_ids"] or "").split(",") if part.strip().lstrip("-").isdigit()]
    info["admin_ids"] = admin_ids
    names: list[str] = []
    if admin_ids:
        marks = ",".join("?" * len(admin_ids))
        _cols, data, _trunc = s.rows("group_admin_names", f"""
            SELECT sender_id, sender_name, sender_username FROM messages
            WHERE id IN (SELECT MAX(id) FROM messages WHERE chat_id = ? AND sender_id IN ({marks}) GROUP BY sender_id)""",
                                    [chat_id, *admin_ids])
        names = [name or (f"@{username}" if username else str(sender)) for sender, name, username in data]
    info["admin_names"] = names
    return info


def group_feed(s: Session, chat_id: int, start: str, end: str, limit: int, *, search: str | None = None,
               sentiment: str | None = None, category: str | None = None, customers_only: bool = False) -> pd.DataFrame:
    cat_sql, cat_params = _cat_case()
    media = has_table(s, "media_files")  # pictures/GIFs the bot saved (only from company-related talk)
    sql = f"""
        SELECT m.id, m.message_id, m.sent_at_utc, m.sender_id, m.sender_name, m.sender_username, m.is_staff,
               m.text, m.media_type, m.transcript, m.reply_to_message_id, m.status,
               c.is_company_related, {cat_sql} AS category, c.sentiment, c.summary, c.needs_manager,
               rp.sender_name AS reply_name, substr(COALESCE(rp.text, rp.transcript, ''), 1, 90) AS reply_text,
               {"mf.status AS media_status, mf.path AS media_path" if media else "NULL AS media_status, NULL AS media_path"}
        FROM messages m LEFT JOIN classifications c ON c.message_ref = m.id
        LEFT JOIN messages rp ON rp.chat_id = m.chat_id AND rp.message_id = m.reply_to_message_id
        {"LEFT JOIN media_files mf ON mf.message_ref = m.id" if media else ""}
        WHERE m.chat_id = ? AND m.sent_at_utc >= ? AND m.sent_at_utc < ?"""
    params: list = [*cat_params, chat_id, start, end]
    if search:
        pattern = like_pattern(search)
        sql += f" AND (m.text LIKE ? {_ESC} OR m.transcript LIKE ? {_ESC} OR c.summary LIKE ? {_ESC})"
        params += [pattern, pattern, pattern]
    if sentiment:
        sql += " AND c.is_company_related = 1 AND c.sentiment = ?"
        params.append(sentiment)
    if category:
        sql += f" AND {cat_sql} = ?"
        params += [*cat_params, category]
    if customers_only:
        sql += " AND m.is_staff = 0"
    sql += " ORDER BY m.sent_at_utc DESC, m.message_id DESC"
    frame = s.df("group_feed", sql, params, limit=limit)
    truncated = frame.attrs.get("truncated", False)
    frame = frame.iloc[::-1].reset_index(drop=True)
    frame.attrs["truncated"] = truncated
    return frame


def group_customers(s: Session, chat_id: int, start: str, end: str) -> pd.DataFrame:
    """Customers who wrote to the company (company-related messages only)."""
    where, params = _customer_where(start, end, chat_id)
    return s.df("group_customers", f"""
        SELECT m.sender_id, MAX(m.sender_name) AS name, MAX(m.sender_username) AS username,
               COUNT(*) AS related, COALESCE(SUM(c.sentiment = ?), 0) AS complaints, MAX(m.sent_at_utc) AS last_at
        FROM messages m JOIN classifications c ON c.message_ref = m.id
        WHERE {where} AND c.is_company_related = 1
        GROUP BY m.sender_id ORDER BY related DESC LIMIT 200""", [m_.COMPLAINT, *params])


# --------------------------------------------------------------------------- requests and answers


def checks(s: Session, start: str, end: str, chat_id: int | None = None) -> pd.DataFrame:
    """Requests (manager_checks) with who answered and when. For missed requests the first later
    answer is found with the bot's "someone answered" rule; for answered ones the answering message
    is the chat message sent exactly at answered_at."""
    window, wparams = _check_window(start, end)
    chat_sql = " AND k.chat_id = ?" if chat_id is not None else ""
    frame = s.df("checks", f"""
        WITH base AS (
            SELECT k.id, k.chat_id, k.status, k.started_at, k.answered_at, k.deadline, k.manager_username,
                   a.sender_id AS asker_id, a.sender_name AS asker_name, a.sender_username AS asker_username,
                   COALESCE(ac.summary, substr(COALESCE(a.text, a.transcript, ''), 1, 300)) AS request_text,
                   CASE WHEN k.status = 'missed' THEN (
                       SELECT m2.id FROM messages m2
                       LEFT JOIN classifications c2 ON c2.message_ref = m2.id
                       LEFT JOIN messages r ON r.chat_id = m2.chat_id AND r.message_id = m2.reply_to_message_id
                       WHERE m2.chat_id = k.chat_id AND m2.message_id > k.first_message_id
                         AND m2.sender_id IS NOT NULL AND m2.sender_id IS NOT a.sender_id
                         AND (m2.is_staff = 1 OR r.sender_id = a.sender_id
                              OR (c2.model != 'rule' AND c2.needs_manager = 0))
                       ORDER BY m2.message_id LIMIT 1) END AS later_id,
                   CASE WHEN k.status = 'answered' THEN (
                       SELECT r2.id FROM messages r2
                       WHERE r2.chat_id = k.chat_id AND r2.sent_at_utc = k.answered_at
                         AND r2.message_id > k.first_message_id
                       ORDER BY r2.message_id LIMIT 1) END AS responder_msg
            FROM manager_checks k JOIN messages a ON a.id = k.message_ref
            LEFT JOIN classifications ac ON ac.message_ref = a.id
            WHERE {window}{chat_sql})
        SELECT b.*, COALESCE(g.title, CAST(b.chat_id AS TEXT)) AS title,
               lm.sent_at_utc AS later_at, lm.sender_id AS later_sender, lm.sender_name AS later_name,
               lm.sender_username AS later_username, lm.is_staff AS later_is_staff,
               rm.sender_id AS resp_sender, rm.sender_name AS resp_name, rm.sender_username AS resp_username,
               rm.is_staff AS resp_is_staff
        FROM base b LEFT JOIN messages lm ON lm.id = b.later_id LEFT JOIN messages rm ON rm.id = b.responder_msg
        LEFT JOIN groups g ON g.chat_id = b.chat_id""",
                 [*wparams, *([chat_id] if chat_id is not None else [])])
    for column in ("started_at", "answered_at", "later_at", "deadline"):
        frame[column + "_ts"] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame["response_s"] = (frame["answered_at_ts"] - frame["started_at_ts"]).dt.total_seconds()
    frame.loc[frame["status"] != m_.CHECK_ANSWERED, "response_s"] = None
    frame["later_s"] = (frame["later_at_ts"] - frame["started_at_ts"]).dt.total_seconds()
    frame.loc[frame["status"] != m_.CHECK_MISSED, "later_s"] = None
    return frame


def waiting_now(s: Session) -> pd.DataFrame:
    return s.df("waiting_now", """
        SELECT k.id, k.chat_id, COALESCE(g.title, CAST(k.chat_id AS TEXT)) AS title, k.started_at, k.deadline,
               a.sender_name, a.sender_username,
               COALESCE(ac.summary, substr(COALESCE(a.text, a.transcript, ''), 1, 200)) AS request_text
        FROM manager_checks k JOIN messages a ON a.id = k.message_ref
        LEFT JOIN classifications ac ON ac.message_ref = a.id LEFT JOIN groups g ON g.chat_id = k.chat_id
        WHERE k.status = 'pending' ORDER BY k.deadline LIMIT 50""")


def staff_activity(s: Session, start: str, end: str) -> pd.DataFrame:
    return s.df("staff_activity", """
        SELECT m.sender_id, MAX(m.sender_name) AS name, MAX(m.sender_username) AS username,
               COUNT(*) AS messages, COUNT(DISTINCT m.chat_id) AS groups
        FROM messages m WHERE m.sent_at_utc >= ? AND m.sent_at_utc < ? AND m.is_staff = 1
        GROUP BY m.sender_id""", [start, end])


# --------------------------------------------------------------------------- messages


def latest_complaints(s: Session, start: str, end: str, limit: int = 10) -> pd.DataFrame:
    cat_sql, cat_params = _cat_case()
    where, params = _customer_where(start, end)
    return s.df("latest_complaints", f"""
        SELECT m.id, m.chat_id, COALESCE(g.title, CAST(m.chat_id AS TEXT)) AS title, m.sent_at_utc,
               {cat_sql} AS category, c.summary, m.text, m.transcript, m.media_type, m.sender_name, m.sender_username
        FROM messages m JOIN classifications c ON c.message_ref = m.id LEFT JOIN groups g ON g.chat_id = m.chat_id
        WHERE {where} AND c.is_company_related = 1 AND c.sentiment = ?
        ORDER BY m.sent_at_utc DESC LIMIT ?""", [*cat_params, *params, m_.COMPLAINT, limit])


def category_top_groups(s: Session, start: str, end: str, category: str, limit: int = 10) -> pd.DataFrame:
    cat_sql, cat_params = _cat_case()
    where, params = _customer_where(start, end)
    return s.df("category_top_groups", f"""
        SELECT m.chat_id, COALESCE(g.title, CAST(m.chat_id AS TEXT)) AS title, COUNT(*) AS related,
               COALESCE(SUM(c.sentiment = ?), 0) AS complaints
        FROM messages m JOIN classifications c ON c.message_ref = m.id LEFT JOIN groups g ON g.chat_id = m.chat_id
        WHERE {where} AND {cat_sql} = ? GROUP BY m.chat_id ORDER BY related DESC LIMIT ?""",
                [m_.COMPLAINT, *params, *cat_params, category, limit])


def category_top_customers(s: Session, start: str, end: str, category: str, limit: int = 10) -> pd.DataFrame:
    cat_sql, cat_params = _cat_case()
    where, params = _customer_where(start, end)
    return s.df("category_top_customers", f"""
        SELECT m.sender_id, MAX(m.sender_name) AS name, MAX(m.sender_username) AS username,
               COUNT(DISTINCT m.chat_id) AS groups, COUNT(*) AS related, COALESCE(SUM(c.sentiment = ?), 0) AS complaints
        FROM messages m JOIN classifications c ON c.message_ref = m.id
        WHERE {where} AND {cat_sql} = ? GROUP BY m.sender_id ORDER BY related DESC LIMIT ?""",
                [m_.COMPLAINT, *params, *cat_params, category, limit])


def category_summaries(s: Session, start: str, end: str, category: str, limit: int = 5000) -> list[str]:
    cat_sql, cat_params = _cat_case()
    where, params = _customer_where(start, end)
    _cols, data, _trunc = s.rows("category_summaries", f"""
        SELECT c.summary FROM messages m JOIN classifications c ON c.message_ref = m.id
        WHERE {where} AND {cat_sql} = ? AND c.summary IS NOT NULL""", [*params, *cat_params, category], limit=limit)
    return [row[0] for row in data]


def messages_list(s: Session, start: str, end: str, limit: int, *, text: str | None = None,
                  chat_ids: tuple[int, ...] = (), category: str | None = None, sentiment: str | None = None,
                  author: str | None = None, needs_answer: bool = False, include_staff: bool = False) -> pd.DataFrame:
    """Search / category message list (newest first, at most ``limit`` rows)."""
    cat_sql, cat_params = _cat_case()
    sql = f"""
        SELECT m.id, m.chat_id, COALESCE(g.title, CAST(m.chat_id AS TEXT)) AS title, m.sent_at_utc,
               m.sender_name, m.sender_username, m.is_staff, m.media_type, m.text, m.transcript,
               {cat_sql} AS category, c.sentiment, c.summary, c.needs_manager, c.is_company_related
        FROM messages m LEFT JOIN classifications c ON c.message_ref = m.id LEFT JOIN groups g ON g.chat_id = m.chat_id
        WHERE m.sent_at_utc >= ? AND m.sent_at_utc < ?"""
    params: list = [*cat_params, start, end]
    if not include_staff:
        sql += " AND m.is_staff = 0"
    if text:
        pattern = like_pattern(text)
        sql += f" AND (m.text LIKE ? {_ESC} OR m.transcript LIKE ? {_ESC} OR c.summary LIKE ? {_ESC})"
        params += [pattern, pattern, pattern]
    if chat_ids:
        sql += f" AND m.chat_id IN ({','.join('?' * len(chat_ids))})"
        params += list(chat_ids)
    if category:
        sql += f" AND {cat_sql} = ?"
        params += [*cat_params, category]
    if sentiment:
        sql += " AND c.is_company_related = 1 AND c.sentiment = ?"
        params.append(sentiment)
    if author:
        pattern = like_pattern(author)
        sql += f" AND (m.sender_name LIKE ? {_ESC} OR m.sender_username LIKE ? {_ESC})"
        params += [pattern, pattern]
    if needs_answer:
        sql += " AND c.needs_manager = 1"
    sql += " ORDER BY m.sent_at_utc DESC"
    return s.df("messages_list", sql, params, limit=limit)


def group_titles(s: Session) -> pd.DataFrame:
    return s.df("group_titles", "SELECT chat_id, COALESCE(title, CAST(chat_id AS TEXT)) AS title FROM groups ORDER BY title")


# --------------------------------------------------------------------------- stored report data


def stored_summaries(s: Session, first: date, last: date, category: str | None = None) -> pd.DataFrame:
    sql = "SELECT date, category, summary_text, report_sent_at FROM daily_summaries WHERE date >= ? AND date <= ?"
    params: list = [first.isoformat(), last.isoformat()]
    if category:
        sql += " AND category = ?"
        params.append(category)
    return s.df("stored_summaries", sql + " ORDER BY date DESC", params, limit=400)


def report_dates(s: Session) -> list[str]:
    _cols, data, _trunc = s.rows("report_dates", """
        SELECT date FROM daily_stats UNION SELECT date FROM daily_summaries ORDER BY date DESC""", limit=400)
    return [row[0] for row in data]


def daily_stats_for(s: Session, day: str) -> pd.DataFrame:
    return s.df("daily_stats_for", """
        SELECT category, total, complaints, positive, neutral, share FROM daily_stats WHERE date = ? ORDER BY id""", [day])


def daily_stats_range(s: Session, first: str, last: str) -> pd.DataFrame:
    return s.df("daily_stats_range", """
        SELECT date, category, total, complaints, positive, neutral FROM daily_stats
        WHERE date >= ? AND date <= ? AND category IN (?, ?, ?)""",
                [first, last, m_.STAT_TOTAL, m_.STAT_FILTERED, m_.STAT_MISSED])


# --------------------------------------------------------------------------- system / data quality


def freshness(s: Session) -> dict:
    return {
        "first_message": s.scalar("fresh.first", "SELECT MIN(sent_at_utc) FROM messages"),
        "last_message": s.scalar("fresh.message", "SELECT MAX(sent_at_utc) FROM messages"),
        "last_customer": s.scalar("fresh.customer", "SELECT MAX(sent_at_utc) FROM messages WHERE is_staff = 0"),
        "last_classified": s.scalar("fresh.classified", "SELECT classified_at FROM classifications ORDER BY id DESC LIMIT 1"),
        "last_report": s.scalar("fresh.report", "SELECT MAX(report_sent_at) FROM daily_summaries"),
        "pending": s.scalar("fresh.pending", "SELECT COUNT(*) FROM messages WHERE status = 'pending'", default=0),
        "oldest_pending": s.scalar("fresh.oldest_pending", "SELECT MIN(sent_at_utc) FROM messages WHERE status = 'pending'"),
    }


def quality(s: Session, start: str, end: str) -> dict:
    where, params = _customer_where(start, end)
    marks = ",".join("?" * len(m_.SPEECH_MEDIA))
    row = s.one("quality", f"""
        SELECT COUNT(*), COALESCE(SUM(m.status = 'failed'), 0), COALESCE(SUM(m.status = 'pending'), 0),
               COALESCE(SUM(c.model = 'rule'), 0), COALESCE(SUM(c.id IS NOT NULL AND c.model != 'rule'), 0),
               COALESCE(SUM(m.media_type IN ({marks}) AND m.transcript = ''), 0),
               COALESCE(SUM(m.media_type IN ({marks})), 0)
        FROM messages m LEFT JOIN classifications c ON c.message_ref = m.id WHERE {where}""",
                 [*m_.SPEECH_MEDIA, *m_.SPEECH_MEDIA, *params])
    keys = ("customer", "failed", "pending", "rule", "model", "voice_empty", "speech")
    return dict(zip(keys, row or (0,) * len(keys)))


def groups_health(s: Session) -> pd.DataFrame:
    return s.df("groups_health", """
        SELECT g.chat_id, COALESCE(g.title, CAST(g.chat_id AS TEXT)) AS title, g.is_present, g.manager_username,
               (SELECT MAX(sent_at_utc) FROM messages WHERE chat_id = g.chat_id) AS last_at
        FROM groups g""")
