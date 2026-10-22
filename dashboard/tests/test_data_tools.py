"""Data agent tools on the small demo database: every tool answers, numbers match the dashboard's
queries, inputs are validated, outputs are bounded."""

from __future__ import annotations

import pytest

from assistant import data_tools
from assistant.llm import ToolError
from core import db, queries
from core import timeutil as tu
from core.settings import load_settings

NO_PERIOD = {"period": "selected", "date_from": None, "date_to": None}


@pytest.fixture
def tools() -> data_tools.DataTools:
    settings = load_settings()
    now = tu.now_local(settings.tz)
    return data_tools.DataTools(settings, {"period": tu.resolve_period("last30", now, settings.report_time), "now": now})


def test_overview_matches_queries(tools):
    result = tools.overview({**NO_PERIOD, "group": None})
    period = tools.period(NO_PERIOD)
    with db.session(tools.settings) as session:
        totals = queries.totals(session, period.start_db, period.end_db)
    assert result["complaints"] == totals["complaints"] and result["company_related_messages"] == totals["related"]
    # messages not about the company are not part of any statistic (2026-10-22)
    assert not {"customer_messages", "unrelated_filtered_messages"} & set(result)
    assert result["requests"]["missed"] == totals["missed"]
    assert result["period"] == tu.fmt_period(period)


def test_group_scope_and_resolution(tools):
    title = tools.titles()[0][1]
    assert tools.overview({**NO_PERIOD, "group": title})["group"] == title
    with pytest.raises(ToolError, match="No group matches"):
        tools.group("bunday-guruh-yoq-123")
    assert tools.find_group({"name": ""})["total_matches"] == len(tools.titles())


def test_periods(tools):
    with pytest.raises(ToolError, match="custom needs"):
        tools.period({"period": "custom", "date_from": None, "date_to": None})
    with pytest.raises(ToolError, match="Unknown period"):
        tools.period({"period": "decade"})
    custom = tools.period({"period": "custom", "date_from": "2026-10-01", "date_to": "2026-10-03"})
    assert custom.days == 3
    with pytest.raises(ToolError, match="At most"):
        tools.daily_trend({"period": "custom", "date_from": "2026-01-01", "date_to": "2026-10-01", "group": None})


def test_rankings_and_lists_are_bounded(tools):
    ranking = tools.groups_ranking({**NO_PERIOD, "sort_by": "complaints", "limit": 5})
    complaints = [row["complaints"] for row in ranking["rows"]]
    assert len(complaints) <= 5 and complaints == sorted(complaints, reverse=True)
    assert tools.groups_ranking({**NO_PERIOD, "sort_by": "risk", "limit": 999})["rows"].__len__() <= data_tools.MAX_ROWS
    with pytest.raises(ToolError):
        tools.groups_ranking({**NO_PERIOD, "sort_by": "alphabet", "limit": None})
    complaints = tools.complaints({**NO_PERIOD, "group": None, "category": None, "limit": 4})
    assert len(complaints["newest_first"]) <= 4
    assert all(len(row["text"]) <= data_tools.SNIPPET for row in complaints["newest_first"])


def test_every_tool_answers(tools):
    assert len(tools.categories({**NO_PERIOD, "group": None})["rows"]) == 8
    missed = tools.missed_requests({**NO_PERIOD, "group": None, "limit": None})
    assert missed["missed_total"] == missed["never_answered"] + missed["answered_later"]
    assert "waiting_total" in tools.waiting_now({})
    assert "newest_first" in tools.search_messages({**NO_PERIOD, "text": "armatura", "group": None, "category": None,
                                                    "sentiment": None, "limit": None})
    assert "rows" in tools.staff({**NO_PERIOD, "limit": None})
    assert tools.daily_trend({**NO_PERIOD, "group": None})["days"]
    assert "summaries" in tools.report_summaries({"date_from": None, "date_to": None, "category": None})
    with pytest.raises(ToolError, match="Unknown category"):
        tools.complaints({**NO_PERIOD, "group": None, "category": "Ob-havo", "limit": None})


def _strict(schema: dict) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for value in schema["properties"].values():
            _strict(value)
    for option in schema.get("anyOf", []):
        _strict(option)
    if schema.get("type") == "array":
        _strict(schema["items"])


def test_tool_schemas_are_strict(tools):
    built = data_tools.build(tools.settings, tools.context)
    assert len({tool.name for tool in built}) == len(built) == 11
    for tool in built:
        _strict(tool.schema)
