"""Prints the SQLite query plan of the main dashboard queries on the demo database (diagnostics only;
nothing is created or changed). Run: .venv\\Scripts\\python.exe tests\\explain_queries.py"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASHBOARD_DIR))
os.environ.setdefault("DASHBOARD_DEMO_MODE", "1")
os.environ.setdefault("DASHBOARD_DEMO_DB_PATH", str(DASHBOARD_DIR / "demo" / "demo_monitor.db"))

from core import db, queries  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.settings import load_settings  # noqa: E402


def main() -> None:
    settings = load_settings()
    conn = db.open_connection(settings)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    session = db.Session(conn, 30, settings.result_limit)
    now = datetime.now(timezone.utc).astimezone(settings.tz)
    period = tu.resolve_period("last30", now, settings.report_time)
    previous = period.previous(now)
    s, e, off = period.start_db, period.end_db, period.offset_modifier()
    calls = {
        "totals": lambda: queries.totals(session, s, e),
        "daily": lambda: queries.daily(session, s, e, off),
        "by_category": lambda: queries.by_category(session, s, e),
        "groups_overview": lambda: queries.groups_overview(session, s, e, previous.start_db, previous.end_db),
        "checks": lambda: queries.checks(session, s, e),
        "group_feed": lambda: queries.group_feed(session, -1003000000103, s, e, 300, search="sement"),
        "messages_list": lambda: queries.messages_list(session, s, e, 1000, text="armatura"),
        "category_daily": lambda: queries.category_daily(session, s, e, off),
        "freshness": lambda: queries.freshness(session),
    }
    for name, call in calls.items():
        statements.clear()
        call()
        for sql in list(statements):
            if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
                continue
            plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]
            scans = [step for step in plan if step.startswith("SCAN") and "USING" not in step]
            print(f"{name:16} {'OK ' if not scans else 'SCAN'} {' | '.join(plan)[:400]}")
    conn.close()


if __name__ == "__main__":
    main()
