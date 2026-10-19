"""Read-only smoke test on the real monitoring database (skipped when it is not there).
It only opens the file read-only, runs count queries and renders the overview page."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

from core import db, queries
from core import timeutil as tu
from core.settings import load_settings
from tests.conftest import DASHBOARD_DIR, use_database
from tests.test_views import PAGE_SCRIPT


@pytest.fixture
def real_db(demo_db):
    use_database(False)
    settings = load_settings()
    if not settings.db_path.is_file():
        pytest.skip("real monitoring database not found")
    yield settings
    use_database(True, demo_db)


def test_real_database_counts(real_db):
    now = datetime.now(timezone.utc).astimezone(real_db.tz)
    period = tu.resolve_period("last30", now, real_db.report_time)
    with db.session(real_db) as session:
        assert session.scalar("count", "SELECT COUNT(*) FROM messages") >= 0
        totals = queries.totals(session, period.start_db, period.end_db)
        assert totals["customer"] >= totals["related"] >= totals["complaints"]
        assert isinstance(queries.freshness(session), dict)


@pytest.mark.parametrize("name", ["overview", "groups", "system"])
def test_real_pages_open(real_db, name):
    app = AppTest.from_string(PAGE_SCRIPT.format(root=str(DASHBOARD_DIR), name=name), default_timeout=120)
    app.query_params["p"] = "last30"
    app.run()
    assert not app.exception, [item.value for item in app.exception]
