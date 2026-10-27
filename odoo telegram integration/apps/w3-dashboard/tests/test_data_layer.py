"""W3-02: the read-only data layer on genesis_test -- writes are refused, only api views are read,
lists are paged, and the numbers match the W0 fixtures (SCHEMA.md §8a)."""

from __future__ import annotations

import re
from datetime import datetime, time

import psycopg
import pytest

from core import db, queries
from core import metrics as m_
from core import timeutil as tu
from core.settings import DASHBOARD_DIR, load_settings

ALL_TIME = ("2000-01-01 00:00:00.000000", "2100-01-01 00:00:00.000000")


# --------------------------------------------------------------------------- read-only (negative tests)


def test_session_is_read_only_and_sees_only_api():
    settings = load_settings()
    assert settings.db.database == "genesis_test" and settings.db.role == "w3_dashboard"
    conn = db.open_connection(settings)
    try:
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert conn.execute("SHOW search_path").fetchone()[0] == "api"
        assert conn.execute("SELECT current_user").fetchone()[0] == "w3_dashboard"
    finally:
        conn.close()


_VIEW_REFUSED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.ObjectNotInPrerequisiteState)


@pytest.mark.parametrize("sql, refused", [
    # views: refused while the statement is planned (no write right / not updatable)
    ("INSERT INTO api.v_sources (kind, title) VALUES ('supergroup', 'x')", _VIEW_REFUSED),
    ("UPDATE api.v_messages_public SET text = 'x'", _VIEW_REFUSED),
    # tables: the read-only session refuses before rights are even checked -- also the ones the
    # w3_dashboard role may write (SCHEMA.md §3: audit_log, review_queue answers, report_deliveries)
    ("INSERT INTO core.audit_log (actor, action) VALUES ('test', 'write')", psycopg.errors.ReadOnlySqlTransaction),
    ("UPDATE core.review_queue SET human_decision = 'x'", psycopg.errors.ReadOnlySqlTransaction),
    ("INSERT INTO core.report_deliveries DEFAULT VALUES", psycopg.errors.ReadOnlySqlTransaction),
    ("DELETE FROM core.messages", psycopg.errors.ReadOnlySqlTransaction),
])
def test_database_refuses_writes(sql, refused):
    """Bypasses the Session guard and sends the statement straight to the server."""
    conn = db.open_connection(load_settings())
    try:
        with pytest.raises(refused):
            conn.execute(sql)
    finally:
        conn.close()


def test_role_has_no_write_rights_on_the_views():
    """Second line of defence: even without the read-only session the role cannot write to api."""
    with db.session(load_settings()) as session:
        _cols, rows, _trunc = session.rows("views", "SELECT table_name FROM information_schema.views "
                                                    "WHERE table_schema = 'api' ORDER BY 1")
        views = [row[0] for row in rows]
        assert len(views) >= 19  # W0 adds views with new migrations (V008: v_categories, v_staff_day, ...)
        for view in views:
            for right in ("INSERT", "UPDATE", "DELETE"):
                assert session.scalar("priv", "SELECT has_table_privilege(current_user, %s, %s)",
                                      [f"api.{view}", right]) is False, (view, right)


@pytest.mark.parametrize("sql", [
    "INSERT INTO core.audit_log (actor, action) VALUES ('test', 'write')",
    "update core.review_queue set human_decision = 'x'",
    "  DELETE FROM core.messages",
    "CREATE TABLE api.x (id int)",
])
def test_session_refuses_anything_but_select(sql):
    with db.session(load_settings()) as session:
        with pytest.raises(db.WriteRefused):
            session.rows("write", sql)


def test_core_tables_are_not_readable():
    with db.session(load_settings()) as session:
        with pytest.raises(db.DataError):
            session.rows("core", "SELECT * FROM core.messages", limit=1)
        assert session.scalar("ok", "SELECT COUNT(*) FROM api.v_messages_public") > 0


def test_nothing_changed_after_write_attempts():
    with db.session(load_settings()) as session:
        before = session.scalar("n", "SELECT COUNT(*) FROM api.v_sources")
        with pytest.raises(db.WriteRefused):
            session.rows("write", "INSERT INTO api.v_sources (source_id) VALUES (999)")
        assert session.scalar("n", "SELECT COUNT(*) FROM api.v_sources") == before == 3


def test_queries_read_only_api_views():
    source = (DASHBOARD_DIR / "core" / "queries.py").read_text(encoding="utf-8")
    named = set(re.findall(r"\bcore\.([a-z_]+)", source)) - {"metrics", "db", "text"}  # imports, docstring
    assert not named, f"queries.py names core objects: {named}"
    names = set(re.findall(r"\bapi\.(v_[a-z_]+)", source))
    assert names and all(name.startswith("v_") for name in names)


# --------------------------------------------------------------------------- paging


def test_message_list_is_paged():
    settings = load_settings()
    with db.session(settings) as session:
        everything = queries.messages_list(session, *ALL_TIME, 1000, include_staff=True)
        first = queries.messages_list(session, *ALL_TIME, 10, include_staff=True)
        second = queries.messages_list(session, *ALL_TIME, 10, include_staff=True, offset=10)
        last = queries.messages_list(session, *ALL_TIME, 10, include_staff=True, offset=len(everything) - 5)
    assert len(everything) == 91 and not everything.attrs["truncated"]
    assert len(first) == len(second) == 10 and first.attrs["truncated"] and second.attrs["truncated"]
    assert set(first["id"]).isdisjoint(second["id"])
    assert list(first["id"]) + list(second["id"]) == list(everything["id"][:20])
    assert len(last) == 5 and not last.attrs["truncated"]


def test_group_feed_is_paged():
    with db.session(load_settings()) as session:
        page1 = queries.group_feed(session, 1, *ALL_TIME, 5)
        page2 = queries.group_feed(session, 1, *ALL_TIME, 5, offset=5)
    assert len(page1) == len(page2) == 5 and page1.attrs["truncated"]
    assert page1["sent_at_utc"].min() >= page2["sent_at_utc"].max()  # newer page first, each page oldest-first


# --------------------------------------------------------------------------- numbers from the fixtures


def test_totals_match_the_fixtures():
    with db.session(load_settings()) as session:
        totals = queries.totals(session, *ALL_TIME)
        cats = queries.by_category(session, *ALL_TIME)
    assert (totals["answered"], totals["missed"], totals["waiting"]) == (12, 15, 1)
    assert totals["related"] == 36 and totals["complaints"] == 14 and totals["positive"] == 7
    assert totals["customer"] == 60 and totals["filtered"] == 2
    assert cats["related"].sum() == totals["related"] and cats["complaints"].sum() == totals["complaints"]
    assert list(cats["category"]) == list(m_.category_names())


def test_checks_and_late_answers():
    with db.session(load_settings()) as session:
        checks = queries.checks(session, *ALL_TIME)
    answered = checks[checks["status"] == m_.CHECK_ANSWERED]
    assert len(answered) == 12 and answered["resp_sender"].notna().all()
    late = checks[(checks["status"] == m_.CHECK_MISSED) & checks["later_s"].notna()]
    assert len(late) == 9 and (late["later_s"] > 0).all()
    assert checks["request_link"].str.startswith("https://t.me/").all()


def test_times_are_utc_text_shown_in_tashkent():
    """The clock is fixed: the end of the fixtures' last local day (the loader writes yesterday and today,
    up to the evening), so the result does not depend on the time the tests run."""
    settings = load_settings()
    with db.session(settings) as session:
        fresh = queries.freshness(session)
        stored = session.scalar("max", "SELECT MAX(sent_at) FROM api.v_messages_public")
        anchor = session.scalar("anchor", "SELECT MAX(local_day) FROM api.v_daily_source_counts")
    fixed_now = datetime.combine(anchor, time(23, 59, 59), tzinfo=settings.tz)
    shown = tu.from_db(fresh["last_message"], settings.tz)
    assert shown is not None and shown.utcoffset().total_seconds() == 5 * 3600
    assert shown == stored  # the same moment: UTC text in, Tashkent time out, no offset added twice
    assert shown.date() == anchor and shown <= fixed_now


def test_ai_results_carry_label_confidence_and_link():
    with db.session(load_settings()) as session:
        rows = queries.latest_complaints(session, *ALL_TIME, 50)
    assert len(rows) == 14
    assert (rows["ai_label"] == "AI taxmini").all() and rows["confidence"].between(0, 1).all()
    assert rows["tg_link"].str.startswith("https://t.me/").all()


def test_sources_without_a_view_stay_empty():
    with db.session(load_settings()) as session:
        assert queries.daily_stats_range(session, "2000-01-01", "2100-01-01").empty
        assert queries.freshness(session)["last_report"] is None
