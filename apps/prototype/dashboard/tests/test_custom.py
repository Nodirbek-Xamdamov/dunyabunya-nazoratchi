"""Sections made in the chat (ui/custom.py): spec checks with useful errors, previews on the demo
database (without the customers' own texts), the static column lists matching what the data tools
really return, and the values the CEO sees."""

from __future__ import annotations

import pytest

from assistant import data_tools
from core import timeutil as tu
from core.settings import load_settings
from ui import custom


def _period(preset: str = "last30") -> tu.Period:
    settings = load_settings()
    return tu.resolve_period(preset, tu.now_local(settings.tz), settings.report_time)


def _spec(**fields) -> dict:
    return {"kind": "data", "title": "Test", "note": None, "source": "categories", "params": {}, "display": "table",
            **fields}


def test_specs_get_defaults_and_only_their_own_fields():
    spec = custom.data_spec(_spec(source="overview", display="kpi", x="nope", columns=["nope"]))
    assert spec == {"kind": "data", "title": "Test", "note": "", "source": "overview", "params": {}, "display": "kpi",
                    "metrics": list(custom.DEFAULT_METRICS["overview"])}
    table = custom.data_spec(_spec(params={"period": "last7", "group": None, "limit": None}))
    assert table["params"] == {"period": "last7"} and table["columns"] == list(custom.SOURCES["categories"].columns)
    chart = custom.data_spec(_spec(display="hbar", y=["complaints", "positive"]))
    assert chart["x"] == "category" and chart["y"] == ["complaints", "positive"]
    trend = custom.data_spec(_spec(source="daily_trend", display="line", y=["complaints"]))
    assert trend["x"] == "day"
    note = custom.note_spec({"title": "Eslatma", "text": "**Muhim**", "style": None})
    assert note == {"kind": "note", "title": "Eslatma", "text": "**Muhim**", "style": "plain"}


@pytest.mark.parametrize("fields, message", [
    ({"source": "sql"}, "source must be one of"),
    ({"display": "kpi", "source": "complaints"}, "display for complaints"),
    ({"columns": ["category", "nope"]}, "Unknown columns"),
    ({"display": "bar", "y": ["category"]}, "not number columns"),
    ({"display": "bar", "y": []}, "y must name"),
    ({"display": "pie", "y": ["share_of_company_related"]}, "pie chart"),
    ({"display": "pie", "y": ["complaints", "positive"]}, "pie chart"),
    ({"display": "kpi", "metrics": ["nope"]}, "Unknown metrics"),
    ({"display": "kpi", "source": "overview", "metrics": list(custom.SOURCES["overview"].columns)[:7]}, "At most 6"),
    ({"params": {"sql": "SELECT 1"}}, "takes only these params"),
    ({"source": "search_messages", "params": {"text": " "}}, "needs params.text"),
    ({"title": ""}, "title is required"),
    ({"title": "x" * 81}, "at most 80"),
])
def test_bad_specs_are_explained(fields, message):
    with pytest.raises(custom.ViewError, match=message):
        custom.data_spec(_spec(**fields))


def test_bad_notes_are_explained():
    with pytest.raises(custom.ViewError, match="style must be"):
        custom.note_spec({"title": "A", "text": "B", "style": "rainbow"})
    with pytest.raises(custom.ViewError, match="text is required"):
        custom.note_spec({"title": "A", "text": ""})


def test_static_columns_match_what_the_data_tools_return():
    settings = load_settings()
    period = _period()
    tools = data_tools.DataTools(settings, {"period": period})
    for name, source in custom.SOURCES.items():
        params = {"text": "a"} if name == "search_messages" else {}
        result = getattr(tools, name)(params)
        values = custom.metric_values(name, result)
        for metric in custom.metric_names(name):
            assert metric in values, (name, metric)
        rows = custom.rows_of(name, result)
        if source.rows is None:
            continue
        assert source.rows in result, name
        for row in rows:
            assert set(row) == set(source.columns), (name, set(row) ^ set(source.columns))
            for column, kind in source.columns.items():
                if kind in custom.NUMERIC and row[column] is not None and row[column] != "—":
                    assert custom.number(kind, row[column]) is not None, (name, column, row[column])


def test_preview_runs_the_source_without_customer_texts():
    settings = load_settings()
    info = custom.preview("complaints", {"period": "last30", "limit": 5}, settings, _period("last7"))
    assert info["rows_now"] <= 5 and "time" in info["columns"] and info["displays"] == ["table", "list"]
    for row in info["first_rows"]:
        assert not set(row) & {"customer", "summary", "text"}
    overview = custom.preview("overview", {}, settings, _period("last7"))
    assert set(overview["metrics"]) == set(custom.SOURCES["overview"].columns) and "columns" not in overview
    assert overview["period"] == tu.fmt_period(_period("last7"))  # "selected" is the sidebar period
    with pytest.raises(data_tools.ToolError, match="No group matches"):
        custom.preview("categories", {"group": "hech-qanday-guruh-yoq"}, settings, _period())
    spec, data = custom.check(_spec(display="pie", y=["complaints"]), settings, _period())
    assert spec["x"] == "category" and data["rows_now"] == 8


def test_values_the_ceo_sees():
    assert custom.number("pct", "12,5%") == 12.5 and custom.number("pct", "—") is None
    assert custom.number("min", "1 soat 5 daq") == 65 and custom.number("min", "30 soniya") == 0.5
    assert custom.number("min", "2 kun 3 soat") == 2 * 1440 + 180 and custom.number("int", True) is None
    assert custom._text("deadline", "text", "4 min left") == "4 daq qoldi"
    assert "muddat" in custom._text("deadline", "text", "deadline passed (the bot closes it within a minute)")
    assert custom._text("answered_later_by", "text", "never answered") == "javob berilmagan"
    assert custom._text("name", "text", data_tools.OTHERS).startswith("Boshqa")
    assert custom._text("risk_level", "text", "red") == "Qizil"
    assert custom._text("bot_in_group", "bool", False) == "yo'q"
    assert custom._text("risk_reasons", "list", ["a", "b"]) == "a; b"
    assert custom._text("complaints", "int", 12345) == "12 345"
    assert custom.label("requests.missed") == "Javobsiz so'rovlar" and custom.label("sum.complaints") == "Shikoyatlar (jami)"
    assert custom.label("nope_field") == "nope field"
    for name, source in custom.SOURCES.items():
        for column in [*source.columns, *custom.metric_names(name)]:
            assert custom.label(column) != column.replace("_", " "), (name, column)


def test_charts_are_built_for_every_display():
    rows = [{"category": "Narx", "complaints": 3, "positive": 1, "share_of_company_related": "25%"},
            {"category": "Yetkazish", "complaints": 5, "positive": 0, "share_of_company_related": "75%"}]
    for display in ("bar", "stacked_bar", "hbar", "line", "area"):
        figure = custom.figure({"source": "categories", "display": display, "x": "category",
                                "y": ["complaints", "share_of_company_related"]}, rows)
        assert len(figure.data) == 2
    pie = custom.figure({"source": "categories", "display": "pie", "x": "category", "y": ["complaints"]},
                        [*rows, {"category": "Bo'sh", "complaints": 0}])
    assert list(pie.data[0].values) == [3, 5] and list(pie.data[0].labels) == ["Narx", "Yetkazish"]
    days = custom.figure({"source": "daily_trend", "display": "line", "x": "day", "y": ["complaints"]},
                         [{"day": "2026-10-16", "complaints": 2}, {"day": "2026-10-17", "complaints": 4}])
    assert list(days.data[0].x) == ["16-okt", "17-okt"]
    text = custom.sources_text()
    assert all(f"- {name}:" in text for name in custom.SOURCES) and "sum.complaints" in text
