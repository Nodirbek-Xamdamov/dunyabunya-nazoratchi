"""All SQL of the dashboard (read-only, parameterised, PostgreSQL). Functions take a ``db.Session`` and
plain values; times come in and go out as stored-format UTC strings (``Period.start_db``), so the pages work
unchanged. Only the W0 views of the ``api`` schema are read (SCHEMA.md §10). Definitions: see core.metrics.

How the prototype's numbers map onto the Genesis views (MAPPING.md):
* an "analysed item" is now an **episode** (``v_analysis_feed``), not a single message; it is counted on the
  day it started. The prototype's 8 sections are ``entities.topic``; unknown topics count as "Boshqa";
* complaint / positive / neutral = sentiment negative / positive / other;
* in the group feed and the message lists the episode's analysis is shown on its first message;
* requests = ``v_response_times`` (D-19); a late answer of a missed request is ``late_answer_at``;
* customer messages = ``v_messages_public.sender_role = 'client'``; staff = ``'employee'``;
* filtered / still pending episodes come from the per-day ``v_daily_source_counts`` (whole local days).
Numbers without a view (stored daily_stats rows, report send times, failed classifications) are returned
empty; the pages then show "—" / "no data".
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from core import metrics as m_
from core.db import Session
from core.text import like_pattern

_ESC = "ESCAPE '\\'"
TZ = "Asia/Tashkent"
MESSAGE_KEY = 10_000_000_000  # dashboard message id = source_id * MESSAGE_KEY + Telegram message id
SPEECH = list(m_.SPEECH_MEDIA)


def _ts(expr: str) -> str:
    """timestamptz -> stored-format UTC text (the session runs with TimeZone=UTC)."""
    return f"to_char({expr}, 'YYYY-MM-DD HH24:MI:SS.US')"


def _day(expr: str) -> str:
    return f"to_char({expr}, 'YYYY-MM-DD')"


def _window(column: str) -> str:
    return f"{column} >= %s::timestamptz AND {column} < %s::timestamptz"


def _days(column: str) -> str:
    """Whole local days touched by [start, end)."""
    return (f"{column} >= (%s::timestamptz AT TIME ZONE '{TZ}')::date AND "
            f"{column} <= ((%s::timestamptz - interval '1 microsecond') AT TIME ZONE '{TZ}')::date")


def _chat(column: str, chat_id) -> tuple[str, list]:
    return (f" AND {column} = %s", [chat_id]) if chat_id is not None else ("", [])


def _feed() -> tuple[str, list]:
    """Analysed episodes with the prototype's category and sentiment names."""
    sql = f"""
        SELECT f.episode_id, f.source_id, f.source_title AS title, f.started_at, f.local_day, f.relevance,
               (CASE WHEN f.relevance = 'yes' THEN
                    (CASE WHEN f.entities->>'topic' = ANY(%s) THEN f.entities->>'topic' ELSE %s END) END) AS category,
               (CASE f.sentiment WHEN 'negative' THEN %s WHEN 'positive' THEN %s ELSE %s END) AS sentiment,
               f.summary, f.needs_reply, f.confidence, f.ai_label, f.model_id, f.severity
        FROM api.v_analysis_feed f"""
    return sql, [list(m_.category_names()), m_.OTHER_CATEGORY, m_.COMPLAINT, m_.POSITIVE, m_.NEUTRAL]


_FIRST = """
        SELECT DISTINCT ON (episode_id) episode_id, source_id, tg_message_id
        FROM api.v_episode_messages ORDER BY episode_id, sent_at, tg_message_id"""


def has_table(s: Session, name: str) -> bool:
    """Kept for the pages: media of the feed always come from v_messages_public."""
    return True


# --------------------------------------------------------------------------- totals and series


def totals(s: Session, start: str, end: str, chat_id: int | None = None) -> dict:
    feed, fparams = _feed()
    chat_f, chat_fp = _chat("f.source_id", chat_id)
    row = s.one("totals.feed", f"""
        WITH f AS ({feed})
        SELECT COALESCE(SUM((f.relevance = 'yes')::int), 0),
               COALESCE(SUM((f.relevance = 'yes' AND f.sentiment = %s)::int), 0),
               COALESCE(SUM((f.relevance = 'yes' AND f.sentiment = %s)::int), 0)
        FROM f WHERE {_window('f.started_at')}{chat_f}""",
                [*fparams, m_.COMPLAINT, m_.POSITIVE, start, end, *chat_fp]) or (0, 0, 0)
    chat_m, chat_mp = _chat("m.source_id", chat_id)
    customer = s.scalar("totals.customer", f"""
        SELECT COUNT(*) FROM api.v_messages_public m
        WHERE {_window('m.sent_at')} AND m.sender_role = 'client'{chat_m}""", [start, end, *chat_mp], default=0)
    chat_d, chat_dp = _chat("d.source_id", chat_id)
    days = s.one("totals.days", f"""
        SELECT COALESCE(SUM(d.filtered_episodes), 0), COALESCE(SUM(d.pending_episodes), 0)
        FROM api.v_daily_source_counts d WHERE {_days('d.local_day')}{chat_d}""", [start, end, *chat_dp]) or (0, 0)
    chat_k, chat_kp = _chat("k.source_id", chat_id)
    checks_row = s.one("totals.checks", f"""
        SELECT COALESCE(SUM((k.status = 'answered')::int), 0), COALESCE(SUM((k.status = 'missed')::int), 0),
               COALESCE(SUM((k.status = 'pending')::int), 0)
        FROM api.v_response_times k WHERE {_window('k.requested_at')}{chat_k}""", [start, end, *chat_kp]) or (0, 0, 0)
    result = {"customer": int(customer), "related": int(row[0]), "complaints": int(row[1]), "positive": int(row[2]),
              "filtered": int(days[0]), "pending": int(days[1]), "failed": 0}
    result.update(dict(zip(("answered", "missed", "waiting"), (int(v) for v in checks_row))))
    return result


def daily(s: Session, start: str, end: str, offset: str = "", chat_id: int | None = None) -> pd.DataFrame:
    """Per local day: customer messages, related, complaints, positive, filtered, answered, missed."""
    feed, fparams = _feed()
    chat_f, chat_fp = _chat("f.source_id", chat_id)
    eps = s.df("daily.feed", f"""
        WITH f AS ({feed})
        SELECT {_day('f.local_day')} AS day,
               COALESCE(SUM((f.relevance = 'yes')::int), 0) AS related,
               COALESCE(SUM((f.relevance = 'yes' AND f.sentiment = %s)::int), 0) AS complaints,
               COALESCE(SUM((f.relevance = 'yes' AND f.sentiment = %s)::int), 0) AS positive
        FROM f WHERE {_window('f.started_at')}{chat_f} GROUP BY 1""",
               [*fparams, m_.COMPLAINT, m_.POSITIVE, start, end, *chat_fp])
    chat_m, chat_mp = _chat("m.source_id", chat_id)
    msgs = s.df("daily.messages", f"""
        SELECT {_day('m.local_day')} AS day, COUNT(*) AS customer FROM api.v_messages_public m
        WHERE {_window('m.sent_at')} AND m.sender_role = 'client'{chat_m} GROUP BY 1""", [start, end, *chat_mp])
    chat_d, chat_dp = _chat("d.source_id", chat_id)
    filtered = s.df("daily.filtered", f"""
        SELECT {_day('d.local_day')} AS day, SUM(d.filtered_episodes) AS filtered FROM api.v_daily_source_counts d
        WHERE {_days('d.local_day')}{chat_d} GROUP BY 1""", [start, end, *chat_dp])
    chat_k, chat_kp = _chat("k.source_id", chat_id)
    checks_frame = s.df("daily.checks", f"""
        SELECT {_day('k.local_day')} AS day, SUM((k.status = 'answered')::int) AS answered,
               SUM((k.status = 'missed')::int) AS missed
        FROM api.v_response_times k WHERE {_window('k.requested_at')}{chat_k} GROUP BY 1""", [start, end, *chat_kp])
    frame = pd.DataFrame({"day": pd.Series(dtype="object")})
    for part in (msgs, eps, filtered, checks_frame):
        frame = frame.merge(part, on="day", how="outer")
    for column in ("customer", "related", "complaints", "positive", "filtered", "answered", "missed"):
        if column not in frame:
            frame[column] = 0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype(int)
    frame["neutral"] = (frame["related"] - frame["complaints"] - frame["positive"]).clip(lower=0)
    return frame.sort_values("day").reset_index(drop=True)


def by_category(s: Session, start: str, end: str, chat_id: int | None = None) -> pd.DataFrame:
    feed, fparams = _feed()
    chat_f, chat_fp = _chat("f.source_id", chat_id)
    frame = s.df("by_category", f"""
        WITH f AS ({feed})
        SELECT f.category, COUNT(*) AS related, SUM((f.sentiment = %s)::int) AS complaints,
               SUM((f.sentiment = %s)::int) AS positive
        FROM f WHERE {_window('f.started_at')} AND f.relevance = 'yes'{chat_f} GROUP BY 1""",
                 [*fparams, m_.COMPLAINT, m_.POSITIVE, start, end, *chat_fp])
    base = pd.DataFrame({"category": list(m_.category_names())})
    frame = base.merge(frame, on="category", how="left").fillna(0)
    for column in ("related", "complaints", "positive"):
        frame[column] = frame[column].astype(int)
    frame["neutral"] = (frame["related"] - frame["complaints"] - frame["positive"]).clip(lower=0)
    return frame


def category_daily(s: Session, start: str, end: str, offset: str = "") -> pd.DataFrame:
    feed, fparams = _feed()
    frame = s.df("category_daily", f"""
        WITH f AS ({feed})
        SELECT {_day('f.local_day')} AS day, f.category, COUNT(*) AS related,
               SUM((f.sentiment = %s)::int) AS complaints, SUM((f.sentiment = %s)::int) AS positive
        FROM f WHERE {_window('f.started_at')} AND f.relevance = 'yes' GROUP BY 1, 2""",
                 [*fparams, m_.COMPLAINT, m_.POSITIVE, start, end])
    for column in ("related", "complaints", "positive"):
        frame[column] = pd.to_numeric(frame[column]).astype(int) if not frame.empty else frame[column]
    return frame


def hour_weekday(s: Session, start: str, end: str, offset: str = "", chat_id: int | None = None) -> pd.DataFrame:
    """Company-related episodes by local weekday (0 = Monday) and hour of their start."""
    feed, fparams = _feed()
    chat_f, chat_fp = _chat("f.source_id", chat_id)
    return s.df("hour_weekday", f"""
        WITH f AS ({feed})
        SELECT (EXTRACT(ISODOW FROM f.started_at AT TIME ZONE '{TZ}')::int - 1) AS wd,
               EXTRACT(HOUR FROM f.started_at AT TIME ZONE '{TZ}')::int AS hour, COUNT(*) AS n
        FROM f WHERE {_window('f.started_at')} AND f.relevance = 'yes'{chat_f} GROUP BY 1, 2""",
                [*fparams, start, end, *chat_fp])


def group_category_complaints(s: Session, start: str, end: str, top: int = 15) -> pd.DataFrame:
    feed, fparams = _feed()
    return s.df("group_category_complaints", f"""
        WITH f AS ({feed}),
             c AS (SELECT * FROM f WHERE {_window('f.started_at')} AND f.relevance = 'yes' AND f.sentiment = %s),
             top_groups AS (SELECT source_id FROM c GROUP BY source_id ORDER BY COUNT(*) DESC LIMIT %s)
        SELECT c.source_id AS chat_id, MAX(c.title) AS title, c.category, COUNT(*) AS complaints
        FROM c JOIN top_groups t ON t.source_id = c.source_id GROUP BY c.source_id, c.category""",
                [*fparams, start, end, m_.COMPLAINT, top])


# --------------------------------------------------------------------------- groups


def _managers_text(column: str = "s.managers") -> str:
    """'Aziz, @bek' from the jsonb list of managers; NULL when the group has none."""
    return (f"(SELECT NULLIF(string_agg(COALESCE('@' || (x->>'username'), x->>'name'), ', '), '') "
            f"FROM jsonb_array_elements(COALESCE({column}, '[]'::jsonb)) x)")


def _admin_ids(column: str = "s.admins") -> str:
    return (f"(SELECT COALESCE(string_agg(x->>'tg_user_id', ','), '') "
            f"FROM jsonb_array_elements(COALESCE({column}, '[]'::jsonb)) x)")


def groups_overview(s: Session, start: str, end: str, prev_start: str, prev_end: str) -> pd.DataFrame:
    """One row per known group with period numbers and previous-period complaints."""
    feed, fparams = _feed()
    frame = s.df("groups_overview", f"""
        WITH f AS ({feed}),
             a AS (SELECT source_id, COUNT(*) FILTER (WHERE relevance = 'yes') AS related,
                          COUNT(*) FILTER (WHERE relevance = 'yes' AND sentiment = %s) AS complaints,
                          COUNT(*) FILTER (WHERE relevance = 'yes' AND sentiment = %s) AS positive
                   FROM f WHERE {_window('started_at')} GROUP BY source_id),
             p AS (SELECT source_id, COUNT(*) FILTER (WHERE relevance = 'yes') AS related,
                          COUNT(*) FILTER (WHERE relevance = 'yes' AND sentiment = %s) AS complaints
                   FROM f WHERE {_window('started_at')} GROUP BY source_id),
             m AS (SELECT source_id, COUNT(*) FILTER (WHERE sender_role = 'client'
                                                      AND {_window('sent_at')}) AS customer,
                          MAX(sent_at) AS last_at
                   FROM api.v_messages_public GROUP BY source_id),
             d AS (SELECT source_id, SUM(filtered_episodes) AS filtered FROM api.v_daily_source_counts
                   WHERE {_days('local_day')} GROUP BY source_id),
             k AS (SELECT source_id, SUM((status = 'answered')::int) AS answered,
                          SUM((status = 'missed')::int) AS missed, SUM((status = 'pending')::int) AS waiting
                   FROM api.v_response_times WHERE {_window('requested_at')} GROUP BY source_id)
        SELECT s.source_id AS chat_id, COALESCE(s.title, s.source_id::text) AS title, s.is_present,
               {_managers_text()} AS manager_username, {_admin_ids()} AS admin_user_ids,
               COALESCE(m.customer, 0) AS customer, COALESCE(a.related, 0) AS related,
               COALESCE(a.complaints, 0) AS complaints, COALESCE(a.positive, 0) AS positive,
               COALESCE(d.filtered, 0) AS filtered,
               COALESCE(k.answered, 0) AS answered, COALESCE(k.missed, 0) AS missed,
               COALESCE(k.waiting, 0) AS waiting,
               COALESCE(p.complaints, 0) AS prev_complaints, COALESCE(p.related, 0) AS prev_related,
               {_ts('m.last_at')} AS last_activity
        FROM api.v_sources s
        LEFT JOIN a ON a.source_id = s.source_id LEFT JOIN p ON p.source_id = s.source_id
        LEFT JOIN m ON m.source_id = s.source_id LEFT JOIN d ON d.source_id = s.source_id
        LEFT JOIN k ON k.source_id = s.source_id""",
                 [*fparams, m_.COMPLAINT, m_.POSITIVE, start, end, m_.COMPLAINT, prev_start, prev_end,
                  start, end, start, end, start, end])
    for column in ("customer", "related", "complaints", "positive", "filtered", "answered", "missed", "waiting",
                   "prev_complaints", "prev_related"):
        frame[column] = pd.to_numeric(frame[column]).fillna(0).astype(int)
    frame["is_present"] = frame["is_present"].fillna(True).astype(bool)
    frame["has_manager"] = frame["manager_username"].notna()
    frame["admins"] = frame["admin_user_ids"].fillna("").map(lambda v: len([p for p in str(v).split(",") if p.strip()]))
    return frame


def group_sparks(s: Session, start: str, end: str, offset: str = "") -> pd.DataFrame:
    feed, fparams = _feed()
    return s.df("group_sparks", f"""
        WITH f AS ({feed})
        SELECT f.source_id AS chat_id, {_day('f.local_day')} AS day, COUNT(*) AS n
        FROM f WHERE {_window('f.started_at')} AND f.relevance = 'yes' GROUP BY 1, 2""", [*fparams, start, end])


def group_info(s: Session, chat_id: int) -> dict | None:
    row = s.one("group_info", f"""
        SELECT s.source_id, COALESCE(s.title, s.source_id::text), s.is_present, {_admin_ids()},
               {_managers_text()}, {_ts('s.admins_checked_at')},
               (SELECT {_ts('MIN(sent_at)')} FROM api.v_messages_public WHERE source_id = s.source_id),
               (SELECT {_ts('MAX(sent_at)')} FROM api.v_messages_public WHERE source_id = s.source_id),
               (SELECT COALESCE(jsonb_agg(x->>'name'), '[]'::jsonb) FROM jsonb_array_elements(COALESCE(s.admins, '[]'::jsonb)) x),
               s.tg_chat_id, s.tg_username
        FROM api.v_sources s WHERE s.source_id = %s""", [chat_id])
    if row is None:
        return None
    keys = ("chat_id", "title", "is_present", "admin_user_ids", "manager_username", "admins_checked_at",
            "first_at", "last_at", "admin_names", "tg_chat_id", "tg_username")
    info = dict(zip(keys, row))
    info["admin_ids"] = [int(p) for p in str(info["admin_user_ids"] or "").split(",") if p.strip().lstrip("-").isdigit()]
    info["admin_names"] = [str(name) for name in (info["admin_names"] or []) if name]
    return info


def _message_select(feed_alias: str = "f") -> str:
    """Columns of one message row as the pages expect them (+ the link to the original message)."""
    a = feed_alias
    return f"""
        (m.source_id * {MESSAGE_KEY} + m.tg_message_id) AS id, m.source_id AS chat_id,
        COALESCE(m.source_title, m.source_id::text) AS title, m.tg_message_id AS message_id,
        {_ts('m.sent_at')} AS sent_at_utc, m.sender_tg_id AS sender_id, m.sender_name,
        NULL::text AS sender_username, (m.sender_role = 'employee') AS is_staff, m.text, m.kind AS media_type,
        m.transcript, m.reply_to_tg_message_id AS reply_to_message_id, NULL::text AS status,
        (CASE {a}.relevance WHEN 'yes' THEN 1 WHEN 'no' THEN 0 END) AS is_company_related, {a}.category,
        {a}.sentiment, {a}.summary, {a}.needs_reply AS needs_manager, {a}.confidence, {a}.ai_label,
        m.media_status, m.media_path, m.tg_link"""


def group_feed(s: Session, chat_id: int, start: str, end: str, limit: int, *, search: str | None = None,
               sentiment: str | None = None, category: str | None = None, customers_only: bool = False,
               offset: int = 0) -> pd.DataFrame:
    feed, fparams = _feed()
    sql = f"""
        WITH f AS ({feed}), fm AS ({_FIRST})
        SELECT {_message_select()},
               rp.sender_name AS reply_name, substr(COALESCE(rp.text, rp.transcript, ''), 1, 90) AS reply_text
        FROM api.v_messages_public m
        LEFT JOIN fm ON fm.source_id = m.source_id AND fm.tg_message_id = m.tg_message_id
        LEFT JOIN f ON f.episode_id = fm.episode_id
        LEFT JOIN api.v_messages_public rp ON rp.source_id = m.source_id AND rp.tg_message_id = m.reply_to_tg_message_id
        WHERE m.source_id = %s AND {_window('m.sent_at')}"""
    params: list = [*fparams, chat_id, start, end]
    if search:
        pattern = like_pattern(search)
        sql += f" AND (m.text ILIKE %s {_ESC} OR m.transcript ILIKE %s {_ESC} OR f.summary ILIKE %s {_ESC})"
        params += [pattern, pattern, pattern]
    if sentiment:
        sql += " AND f.relevance = 'yes' AND f.sentiment = %s"
        params.append(sentiment)
    if category:
        sql += " AND f.category = %s"
        params.append(category)
    if customers_only:
        sql += " AND m.sender_role = 'client'"
    sql += " ORDER BY m.sent_at DESC, m.tg_message_id DESC"
    frame = s.df("group_feed", sql, params, limit=limit, offset=offset)
    truncated = frame.attrs.get("truncated", False)
    frame = frame.iloc[::-1].reset_index(drop=True)
    frame.attrs["truncated"] = truncated
    frame.attrs["offset"] = offset
    return frame


def _participants(where: str) -> str:
    feed, _ = _feed()
    return f"""
        WITH f AS ({feed})
        SELECT p.sender_tg_id AS sender_id, MAX(p.name) AS name, NULL::text AS username,
               COUNT(DISTINCT f.source_id) AS groups, COUNT(DISTINCT f.episode_id) AS related,
               COUNT(DISTINCT f.episode_id) FILTER (WHERE f.sentiment = %s) AS complaints,
               {_ts('MAX(p.last_at)')} AS last_at
        FROM f JOIN api.v_episode_participants p ON p.episode_id = f.episode_id AND p.role = 'client'
        WHERE {_window('f.started_at')} AND f.relevance = 'yes' {where}
        GROUP BY p.sender_tg_id ORDER BY related DESC LIMIT %s"""


def group_customers(s: Session, chat_id: int, start: str, end: str) -> pd.DataFrame:
    """Customers who wrote to the company (company-related episodes only)."""
    _sql, fparams = _feed()
    return s.df("group_customers", _participants("AND f.source_id = %s"),
                [*fparams, m_.COMPLAINT, start, end, chat_id, 200])


# --------------------------------------------------------------------------- requests and answers


def checks(s: Session, start: str, end: str, chat_id: int | None = None) -> pd.DataFrame:
    """Requests (v_response_times) with who answered and when. A missed request answered later has
    ``late_answer_at`` and the late responder; an answered one has ``answered_at`` and its responder."""
    feed, fparams = _feed()
    chat_k, chat_kp = _chat("k.source_id", chat_id)
    frame = s.df("checks", f"""
        WITH f AS ({feed})
        SELECT k.response_pair_id AS id, k.source_id AS chat_id, k.status, {_ts('k.requested_at')} AS started_at,
               {_ts('k.answered_at')} AS answered_at, {_ts('k.deadline_at')} AS deadline,
               {_managers_text()} AS manager_username,
               k.requester_tg_id AS asker_id, a.sender_name AS asker_name, NULL::text AS asker_username,
               COALESCE(f.summary, substr(COALESCE(a.text, a.transcript, ''), 1, 300)) AS request_text,
               COALESCE(k.source_title, k.source_id::text) AS title,
               (CASE WHEN k.status = 'missed' THEN {_ts('k.late_answer_at')} END) AS later_at,
               (CASE WHEN k.status = 'missed' AND k.late_answer_at IS NOT NULL THEN k.responder_tg_id END) AS later_sender,
               (CASE WHEN k.status = 'missed' AND k.late_answer_at IS NOT NULL THEN k.responder_name END) AS later_name,
               NULL::text AS later_username,
               (CASE WHEN k.status = 'missed' AND k.late_answer_at IS NOT NULL
                     THEN (k.responder_role = 'employee')::int END) AS later_is_staff,
               (CASE WHEN k.status = 'answered' THEN k.responder_tg_id END) AS resp_sender,
               (CASE WHEN k.status = 'answered' THEN k.responder_name END) AS resp_name,
               NULL::text AS resp_username,
               (CASE WHEN k.status = 'answered' THEN (k.responder_role = 'employee')::int END) AS resp_is_staff,
               k.request_link, k.answer_link
        FROM api.v_response_times k
        LEFT JOIN api.v_sources s ON s.source_id = k.source_id
        LEFT JOIN api.v_messages_public a ON a.source_id = k.source_id AND a.tg_message_id = k.request_tg_message_id
        LEFT JOIN f ON f.episode_id = k.episode_id
        WHERE {_window('k.requested_at')}{chat_k}""", [*fparams, start, end, *chat_kp])
    for column in ("started_at", "answered_at", "later_at", "deadline"):
        frame[column + "_ts"] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame["response_s"] = (frame["answered_at_ts"] - frame["started_at_ts"]).dt.total_seconds()
    frame.loc[frame["status"] != m_.CHECK_ANSWERED, "response_s"] = None
    frame["later_s"] = (frame["later_at_ts"] - frame["started_at_ts"]).dt.total_seconds()
    frame.loc[frame["status"] != m_.CHECK_MISSED, "later_s"] = None
    return frame


def waiting_now(s: Session) -> pd.DataFrame:
    feed, fparams = _feed()
    return s.df("waiting_now", f"""
        WITH f AS ({feed})
        SELECT k.response_pair_id AS id, k.source_id AS chat_id, COALESCE(k.source_title, k.source_id::text) AS title,
               {_ts('k.requested_at')} AS started_at, {_ts('k.deadline_at')} AS deadline,
               a.sender_name, NULL::text AS sender_username,
               COALESCE(f.summary, substr(COALESCE(a.text, a.transcript, ''), 1, 200)) AS request_text
        FROM api.v_response_times k
        LEFT JOIN api.v_messages_public a ON a.source_id = k.source_id AND a.tg_message_id = k.request_tg_message_id
        LEFT JOIN f ON f.episode_id = k.episode_id
        WHERE k.status = 'pending' ORDER BY k.deadline_at LIMIT 50""", fparams)


def staff_activity(s: Session, start: str, end: str) -> pd.DataFrame:
    return s.df("staff_activity", f"""
        SELECT m.sender_tg_id AS sender_id, MAX(m.sender_name) AS name, NULL::text AS username,
               COUNT(*) AS messages, COUNT(DISTINCT m.source_id) AS groups
        FROM api.v_messages_public m WHERE {_window('m.sent_at')} AND m.sender_role = 'employee'
        GROUP BY m.sender_tg_id""", [start, end])


# --------------------------------------------------------------------------- messages


def latest_complaints(s: Session, start: str, end: str, limit: int = 10) -> pd.DataFrame:
    feed, fparams = _feed()
    return s.df("latest_complaints", f"""
        WITH f AS ({feed}), fm AS ({_FIRST})
        SELECT {_message_select()}
        FROM f JOIN fm ON fm.episode_id = f.episode_id
        JOIN api.v_messages_public m ON m.source_id = fm.source_id AND m.tg_message_id = fm.tg_message_id
        WHERE {_window('f.started_at')} AND f.relevance = 'yes' AND f.sentiment = %s
        ORDER BY f.started_at DESC LIMIT %s""", [*fparams, start, end, m_.COMPLAINT, limit])


def category_top_groups(s: Session, start: str, end: str, category: str, limit: int = 10) -> pd.DataFrame:
    feed, fparams = _feed()
    return s.df("category_top_groups", f"""
        WITH f AS ({feed})
        SELECT f.source_id AS chat_id, MAX(f.title) AS title, COUNT(*) AS related,
               SUM((f.sentiment = %s)::int) AS complaints
        FROM f WHERE {_window('f.started_at')} AND f.relevance = 'yes' AND f.category = %s
        GROUP BY f.source_id ORDER BY related DESC LIMIT %s""", [*fparams, m_.COMPLAINT, start, end, category, limit])


def category_top_customers(s: Session, start: str, end: str, category: str, limit: int = 10) -> pd.DataFrame:
    _sql, fparams = _feed()
    return s.df("category_top_customers", _participants("AND f.category = %s"),
                [*fparams, m_.COMPLAINT, start, end, category, limit])


def category_summaries(s: Session, start: str, end: str, category: str, limit: int = 5000) -> list[str]:
    feed, fparams = _feed()
    _cols, data, _trunc = s.rows("category_summaries", f"""
        WITH f AS ({feed})
        SELECT f.summary FROM f
        WHERE {_window('f.started_at')} AND f.relevance = 'yes' AND f.category = %s AND f.summary IS NOT NULL""",
                                 [*fparams, start, end, category], limit=limit)
    return [row[0] for row in data]


def messages_list(s: Session, start: str, end: str, limit: int, *, text: str | None = None,
                  chat_ids: tuple[int, ...] = (), category: str | None = None, sentiment: str | None = None,
                  author: str | None = None, needs_answer: bool = False, include_staff: bool = False,
                  offset: int = 0) -> pd.DataFrame:
    """Search / category message list (newest first, one page of at most ``limit`` rows from ``offset``)."""
    feed, fparams = _feed()
    sql = f"""
        WITH f AS ({feed}), fm AS ({_FIRST})
        SELECT {_message_select()}
        FROM api.v_messages_public m
        LEFT JOIN fm ON fm.source_id = m.source_id AND fm.tg_message_id = m.tg_message_id
        LEFT JOIN f ON f.episode_id = fm.episode_id
        WHERE {_window('m.sent_at')}"""
    params: list = [*fparams, start, end]
    if not include_staff:
        sql += " AND m.sender_role <> 'employee'"
    if text:
        pattern = like_pattern(text)
        sql += f" AND (m.text ILIKE %s {_ESC} OR m.transcript ILIKE %s {_ESC} OR f.summary ILIKE %s {_ESC})"
        params += [pattern, pattern, pattern]
    if chat_ids:
        sql += " AND m.source_id = ANY(%s)"
        params.append(list(chat_ids))
    if category:
        sql += " AND f.category = %s"
        params.append(category)
    if sentiment:
        sql += " AND f.relevance = 'yes' AND f.sentiment = %s"
        params.append(sentiment)
    if author:
        sql += f" AND m.sender_name ILIKE %s {_ESC}"
        params.append(like_pattern(author))
    if needs_answer:
        sql += " AND f.needs_reply"
    sql += " ORDER BY m.sent_at DESC, m.tg_message_id DESC"
    return s.df("messages_list", sql, params, limit=limit, offset=offset)


def group_titles(s: Session) -> pd.DataFrame:
    return s.df("group_titles", """
        SELECT source_id AS chat_id, COALESCE(title, source_id::text) AS title FROM api.v_sources ORDER BY title""")


# --------------------------------------------------------------------------- stored report data


def stored_summaries(s: Session, first: date, last: date, category: str | None = None) -> pd.DataFrame:
    sql = f"""
        SELECT {_day('local_day')} AS date,
               (CASE scope WHEN 'all' THEN %s ELSE scope_key END) AS category, summary AS summary_text,
               NULL::text AS report_sent_at, ai_label
        FROM api.v_daily_summary WHERE local_day >= %s AND local_day <= %s"""
    params: list = [m_.SUMMARY_OVERALL, first, last]
    if category == m_.SUMMARY_OVERALL:
        sql += " AND scope = 'all'"
    elif category:
        sql += " AND scope <> 'all' AND scope_key = %s"
        params.append(category)
    return s.df("stored_summaries", sql + " ORDER BY local_day DESC", params, limit=400)


def report_dates(s: Session) -> list[str]:
    _cols, data, _trunc = s.rows("report_dates", f"""
        SELECT {_day('d')} FROM (SELECT local_day AS d FROM api.v_daily_category_counts
                                 UNION SELECT local_day FROM api.v_daily_summary) x ORDER BY 1 DESC""", limit=400)
    return [row[0] for row in data]


def daily_stats_for(s: Session, day: str) -> pd.DataFrame:
    """The day's numbers per section (computed from the views; the stored daily_stats rows have no view)."""
    feed, fparams = _feed()
    frame = s.df("daily_stats_for", f"""
        WITH f AS ({feed})
        SELECT f.category, COUNT(*) AS total, SUM((f.sentiment = %s)::int) AS complaints,
               SUM((f.sentiment = %s)::int) AS positive
        FROM f WHERE f.local_day = %s::date AND f.relevance = 'yes' GROUP BY 1 ORDER BY total DESC""",
                 [*fparams, m_.COMPLAINT, m_.POSITIVE, day])
    if frame.empty:
        return pd.DataFrame(columns=["category", "total", "complaints", "positive", "neutral", "share"])
    for column in ("total", "complaints", "positive"):
        frame[column] = pd.to_numeric(frame[column]).astype(int)
    frame["neutral"] = (frame["total"] - frame["complaints"] - frame["positive"]).clip(lower=0)
    frame["share"] = (frame["total"] * 100 / frame["total"].sum()).round().astype(int)
    return frame


def daily_stats_range(s: Session, first: str, last: str) -> pd.DataFrame:
    """Stored report rows (the daily_stats table) have no api view: nothing to compare yet."""
    return pd.DataFrame(columns=["date", "category", "total", "complaints", "positive", "neutral"])


# --------------------------------------------------------------------------- system / data quality


def freshness(s: Session) -> dict:
    row = s.one("fresh.health", f"""
        SELECT {_ts('last_message_at')}, {_ts('last_analysis_at')}, queued_jobs + running_jobs, db_bytes
        FROM api.v_system_health""") or (None, None, 0, None)
    first = s.scalar("fresh.first", f"SELECT {_ts('MIN(sent_at)')} FROM api.v_messages_public")
    customer = s.scalar("fresh.customer", f"SELECT {_ts('MAX(sent_at)')} FROM api.v_messages_public "
                                          "WHERE sender_role = 'client'")
    oldest = s.scalar("fresh.oldest_pending", f"SELECT {_ts('MIN(oldest_queued_at)')} FROM api.v_pipeline_status "
                                              "WHERE status = 'queued'")
    return {"first_message": first, "last_message": row[0], "last_customer": customer, "last_classified": row[1],
            "last_report": None, "pending": int(row[2] or 0), "oldest_pending": oldest,
            "db_bytes": None if row[3] is None else int(row[3])}


def quality(s: Session, start: str, end: str) -> dict:
    row = s.one("quality.messages", f"""
        SELECT COUNT(*), COALESCE(SUM((m.kind = ANY(%s) AND COALESCE(m.transcript, '') = '')::int), 0),
               COALESCE(SUM((m.kind = ANY(%s))::int), 0)
        FROM api.v_messages_public m WHERE {_window('m.sent_at')} AND m.sender_role = 'client'""",
                 [SPEECH, SPEECH, start, end]) or (0, 0, 0)
    models = s.one("quality.models", f"""
        SELECT COALESCE(SUM((f.model_id = %s)::int), 0), COALESCE(SUM((f.model_id <> %s)::int), 0)
        FROM api.v_analysis_feed f WHERE {_window('f.started_at')}""", [m_.RULE_MODEL, m_.RULE_MODEL, start, end]) or (0, 0)
    pending = s.scalar("quality.pending", f"""
        SELECT COALESCE(SUM(pending_episodes), 0) FROM api.v_daily_source_counts WHERE {_days('local_day')}""",
                       [start, end], default=0)
    return {"customer": int(row[0]), "failed": 0, "pending": int(pending), "rule": int(models[0]),
            "model": int(models[1]), "voice_empty": int(row[1]), "speech": int(row[2])}


def groups_health(s: Session) -> pd.DataFrame:
    return s.df("groups_health", f"""
        SELECT s.source_id AS chat_id, COALESCE(s.title, s.source_id::text) AS title, s.is_present,
               {_managers_text()} AS manager_username,
               (SELECT {_ts('MAX(sent_at)')} FROM api.v_messages_public WHERE source_id = s.source_id) AS last_at
        FROM api.v_sources s""")


# --------------------------------------------------------------------------- camera (W3-04)


def staff_days(s: Session, first: str, last: str) -> pd.DataFrame:
    """One row per local day x permanent staff member x camera (api.v_staff_day, D-31/D-32), only the
    latest analysis run of each. Staff are shown by number only ("Hodim N"); the view carries no names.
    ``exits`` and ``frames`` stay JSON lists (times as ISO text); the camera module arranges them."""
    return s.df("staff_days", f"""
        SELECT DISTINCT ON (local_day, staff_no, camera_id)
               {_day('local_day')} AS day, staff_no, camera_id, camera_name,
               {_ts('first_in_at')} AS first_in_at, {_ts('last_out_at')} AS last_out_at,
               office_minutes::float8 AS office_minutes, room_minutes::float8 AS room_minutes,
               away_minutes::float8 AS away_minutes, exit_count, exits, first_in_frame, last_out_frame, frames,
               session_count, uncertain, uncertainty_note, ai_label, analysis_run
        FROM api.v_staff_day
        WHERE local_day >= %s::date AND local_day <= %s::date
        ORDER BY local_day, staff_no, camera_id, computed_at DESC""", [first, last])
