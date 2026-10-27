"""Sections the CEO adds in the chat (user's decision, 2026-10-17).

A *data view* shows the result of one of the dashboard's own read-only data tools as KPI cards, a
table, a list or a chart. The data tools (assistant/data_tools.py) are the vetted queries the Q&A
agent uses: the AI only picks the source, its parameters and the display -- it never writes SQL. A
*note* is a short Markdown text. Both are kept as a small spec in the layout overlay::

    {"kind": "data", "title": "Shikoyatlar toifalar bo'yicha", "note": "", "source": "categories",
     "params": {"period": "selected"}, "display": "pie", "x": "category", "y": ["complaints"]}
    {"kind": "note", "title": "Eslatma", "text": "**Markdown** matn", "style": "info"}

``data_spec`` / ``note_spec`` check a spec (their errors are read by the AI agent), ``preview`` runs a
source once so the agent can see what it returns, and ``render`` draws a stored spec
(ui.layout.custom_block). Period "selected" follows the sidebar.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import timeutil as tu
from core.settings import Settings, load_settings
from core.text import esc
from ui import charts
from ui import components as c
from ui import theme
from ui.i18n import UZ, t

log = logging.getLogger("dashboard.custom")
OTHERS = "other participants (not staff)"


class ToolError(Exception):
    """A data section that cannot be computed. In W3 the AI data tools stay in the prototype (D-16), so
    every data section ends here and is shown as unavailable; note sections still work."""
_SETTINGS = load_settings()

KINDS = ("data", "note")
DISPLAYS = ("kpi", "table", "list", "bar", "stacked_bar", "hbar", "line", "area", "pie")
CHARTS = ("bar", "stacked_bar", "hbar", "line", "area", "pie")
NOTE_STYLES = ("plain", "info", "success", "warning", "error")
PARAMS = ("period", "date_from", "date_to", "group", "category", "sort_by", "limit", "text", "sentiment")
NUMERIC = ("int", "num", "pct", "min")  # column kinds a chart can draw
MAX_TITLE = 80
MAX_CAPTION = 300
MAX_NOTE = 4000
MAX_SERIES = 4
MAX_METRICS = 6
CHART_HEIGHT = 330
_PERIOD = ("period", "date_from", "date_to")
_ROWS = ("table", "list", *CHARTS)
_PRIVATE = ("customer", "author", "request", "summary", "text")  # not shown to the agent in previews
_DURATION = re.compile(r"(\d+)\s*(kun|soat|daq|soniya)")
_UNIT_MINUTES = {"kun": 1440, "soat": 60, "daq": 1, "soniya": 1 / 60}
_MINUTES_LEFT = re.compile(r"^(\d+) min left$")


class ViewError(ValueError):
    """A section that cannot be built; the message (read by the AI agent) says why and what is valid."""


@dataclass(frozen=True)
class Source:
    """One data tool as a section source. ``columns``: column -> kind (text; int = a count; num = a
    number that is not summed; pct = "12%"; min = a duration such as "3 soat 5 daq"; list; bool; day).
    ``totals``: top-level numbers of the result usable as KPI cards; ``sums``: the rows are complete,
    so column totals are KPI cards too; ``long``: long texts (the body of a list card)."""

    rows: str | None  # key of the row list in the tool result (None: one set of numbers)
    params: tuple[str, ...]
    displays: tuple[str, ...]
    columns: dict
    totals: tuple[str, ...] = ()
    sums: bool = False
    long: tuple[str, ...] = ()


SOURCES: dict[str, Source] = {
    "overview": Source(None, (*_PERIOD, "group"), ("kpi", "table"), {
        "company_related_messages": "int", "not_yet_classified": "int", "complaints": "int", "complaint_rate": "pct", "positive_messages": "int",
        "neutral_messages": "int", "requests.total": "int", "requests.answered_in_time": "int",
        "requests.missed": "int", "requests.missed_but_answered_later": "int", "requests.never_answered": "int",
        "requests.still_waiting": "int", "answered_in_time_share": "pct", "median_answer_time": "min",
        "p90_answer_time": "min", "previous_period.complaints": "int", "previous_period.missed": "int",
        "previous_period.company_related_messages": "int", "previous_period.answered_in_time_share": "pct"}),
    "groups_ranking": Source("rows", (*_PERIOD, "sort_by", "limit"), ("kpi", *_ROWS), {
        "group": "text", "risk_score": "num", "risk_level": "text", "risk_reasons": "list", "complaints": "int",
        "complaint_rate": "pct", "missed_requests": "int", "answered_in_time_share": "pct",
        "median_answer_time": "min", "company_related_messages": "int",
        "manager": "text", "bot_in_group": "bool"}, totals=("groups_total", "groups_with_company_messages")),
    "categories": Source("rows", (*_PERIOD, "group"), ("kpi", *_ROWS), {
        "category": "text", "responsible_department": "text", "company_related_messages": "int",
        "complaints": "int", "positive": "int", "share_of_company_related": "pct"},
        totals=("company_related_total",), sums=True),
    "missed_requests": Source("rows", (*_PERIOD, "group", "limit"), ("kpi", "table", "list"), {
        "time": "text", "group": "text", "customer": "text", "request": "text", "answered_later_by": "text",
        "minutes_until_later_answer": "num"}, totals=("missed_total", "never_answered", "answered_later"),
        long=("request",)),
    "waiting_now": Source("rows", (), ("kpi", "table", "list"), {
        "group": "text", "customer": "text", "since": "text", "request": "text", "deadline": "text"},
        totals=("waiting_total",), long=("request",)),
    "complaints": Source("newest_first", (*_PERIOD, "group", "category", "limit"), ("table", "list"), {
        "time": "text", "group": "text", "category": "text", "customer": "text", "summary": "text",
        "text": "text"}, long=("summary", "text")),
    "search_messages": Source("newest_first", ("text", *_PERIOD, "group", "category", "sentiment", "limit"),
                              ("table", "list"), {
        "time": "text", "group": "text", "author": "text", "category": "text", "sentiment": "text",
        "summary": "text", "text": "text"}, long=("summary", "text")),
    "staff": Source("rows", (*_PERIOD, "limit"), ("kpi", *_ROWS), {
        "name": "text", "messages_written": "int", "groups": "num", "answered_in_time": "int",
        "answered_late": "int", "median_answer_time": "min"}, totals=("active_staff", "answered_requests")),
    "daily_trend": Source("days", (*_PERIOD, "group"), ("kpi", "table", "bar", "stacked_bar", "line", "area"), {
        "day": "day", "company_related": "int", "complaints": "int",
        "positive": "int", "answered_in_time": "int", "missed": "int"}, sums=True),
    "report_summaries": Source("summaries", ("date_from", "date_to", "category"), ("list", "table"), {
        "report_window": "text", "category": "text", "summary": "text"}, long=("summary",)),
}

DEFAULT_METRICS = {"overview": ("complaints", "requests.missed", "answered_in_time_share",
                                "company_related_messages")}
# KPI card -> the overview value with its change against the previous period; growth of these is bad
DELTAS = {"complaints": "change_vs_previous.complaints", "requests.missed": "change_vs_previous.missed",
          "answered_in_time_share": "change_vs_previous.answered_in_time_share"}
INVERSE = ("complaints", "requests.missed", "missed_total", "never_answered", "waiting_total", "sum.complaints",
           "sum.missed")


# --------------------------------------------------------------------------- names and values


def label(name: str) -> str:
    """Uzbek name of a column or KPI value."""
    if name.startswith("sum."):
        return t("cv.sum", name=label(name[4:]))
    return UZ.get(f"cv.f.{name}", name.replace("_", " "))


def _axis_name(name: str, kind: str) -> str:
    unit = {"pct": " (%)", "min": f" ({t('cv.unit.min')})"}.get(kind, "")
    return label(name) + unit


def metric_names(name: str) -> list[str]:
    source = SOURCES[name]
    if source.rows is None:
        return list(source.columns)
    sums = [f"sum.{column}" for column, kind in source.columns.items() if kind == "int"] if source.sums else []
    return [*source.totals, *sums]


def number(kind: str, value) -> float | None:
    """A chart value: counts as they are, "12,5%" -> 12.5, "1 soat 5 daq" -> 65 (minutes)."""
    if value is None or isinstance(value, bool):
        return None
    if kind in ("int", "num"):
        return float(value) if isinstance(value, (int, float)) else None
    if kind == "pct" and isinstance(value, str) and value.endswith("%"):
        try:
            return float(value[:-1].replace(",", ".").replace(" ", ""))
        except ValueError:
            return None
    if kind == "min" and isinstance(value, str):
        parts = _DURATION.findall(value)
        return round(sum(int(amount) * _UNIT_MINUTES[unit] for amount, unit in parts), 1) if parts else None
    return None


def _cell(column: str, kind: str, value) -> str | int | float | None:
    """A value as the CEO sees it (the data tools write a few words in English for the AI)."""
    if value is None:
        return None
    if kind == "bool":
        return t("cv.v.yes") if value else t("cv.v.no")
    if kind == "list":
        return "; ".join(str(item) for item in value) if isinstance(value, list) else str(value)
    if kind == "day":
        return tu.fmt_day(datetime.fromisoformat(str(value)[:10]).date())
    if column == "deadline" and isinstance(value, str):
        found = _MINUTES_LEFT.match(value)
        return t("ov.waiting.left", minutes=found.group(1)) if found else t("ov.waiting.overdue")
    if column == "answered_later_by" and value == "never answered":
        return t("cv.v.never")
    if column == "name" and value == OTHERS:
        return t("cv.v.others")
    if column == "manager" and value == "none":
        return "—"
    if column == "category" and value == "overall":
        return t("cv.v.overall")
    if column == "risk_level" and value in ("red", "yellow", "green"):
        return t(f"gr.risk.{value}")
    return value


def _text(column: str, kind: str, value) -> str:
    shown = _cell(column, kind, value)
    if shown is None or shown == "":
        return "—"
    if isinstance(shown, bool):
        return t("cv.v.yes") if shown else t("cv.v.no")
    if isinstance(shown, int):
        return f"{shown:,}".replace(",", " ")
    if isinstance(shown, float):
        return f"{shown:,.1f}".replace(",", " ").replace(".", ",")
    return str(shown)


def palette() -> list[str]:
    """Chart colors of the sections built in the chat."""
    return list(theme.CHART_COLORS)


# --------------------------------------------------------------------------- specs


def _field(value, name: str, limit: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ViewError(f"{name} is required.")
    if len(text) > limit:
        raise ViewError(f"{name} can be at most {limit} characters.")
    return text


def note_spec(raw: dict) -> dict:
    style = raw.get("style") or "plain"
    if style not in NOTE_STYLES:
        raise ViewError(f"style must be one of: {', '.join(NOTE_STYLES)}.")
    return {"kind": "note", "title": _field(raw.get("title"), "title", MAX_TITLE),
            "text": _field(raw.get("text"), "text", MAX_NOTE), "style": style}


def _unique(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in (values or []) if value))


def params_of(name: str, raw) -> dict:
    source = SOURCES[name]
    params = {key: value for key, value in (raw or {}).items() if value not in (None, "", [])}
    if "period" not in source.params and params.get("period") == "selected":  # that is what they use anyway
        params.pop("period")
    unknown = [key for key in params if key not in source.params]
    if unknown:
        raise ViewError(f"{name} takes only these params: {', '.join(source.params) or 'none'} "
                        f"(not {', '.join(unknown)}).")
    if name == "search_messages" and not str(params.get("text") or "").strip():
        raise ViewError("search_messages needs params.text (the word or phrase to look for).")
    return params


def data_spec(raw: dict) -> dict:
    """A data view spec checked for shape (``preview`` checks it against the data)."""
    name = raw.get("source")
    if name not in SOURCES:
        raise ViewError(f"source must be one of: {', '.join(SOURCES)}.")
    source = SOURCES[name]
    display = raw.get("display")
    if display not in source.displays:
        raise ViewError(f"display for {name} must be one of: {', '.join(source.displays)}.")
    spec = {"kind": "data", "title": _field(raw.get("title"), "title", MAX_TITLE),
            "note": _field(raw.get("note"), "note", MAX_CAPTION, required=False), "source": name,
            "params": params_of(name, raw.get("params")), "display": display}
    columns = source.columns
    if display == "kpi" or source.rows is None:
        names = metric_names(name)
        metrics = _unique(raw.get("metrics")) or list(DEFAULT_METRICS.get(name, names[:4]))
        unknown = [metric for metric in metrics if metric not in names]
        if unknown:
            raise ViewError(f"Unknown metrics {unknown}. Metrics of {name}: {', '.join(names) or 'none'}.")
        if display == "kpi" and len(metrics) > MAX_METRICS:
            raise ViewError(f"At most {MAX_METRICS} KPI cards in one section; make two sections.")
        spec["metrics"] = metrics
    elif display in ("table", "list"):
        chosen = _unique(raw.get("columns")) or list(columns)
        unknown = [column for column in chosen if column not in columns]
        if unknown:
            raise ViewError(f"Unknown columns {unknown}. Columns of {name}: {', '.join(columns)}.")
        spec["columns"] = chosen
    else:
        numeric = [column for column, kind in columns.items() if kind in NUMERIC]
        labels = [column for column, kind in columns.items() if kind in ("text", "day")]
        x = raw.get("x") or (labels[0] if labels else None)
        if x not in labels:
            raise ViewError(f"x (the labels) must be one of: {', '.join(labels)}.")
        ys = _unique(raw.get("y"))
        if not ys or len(ys) > MAX_SERIES:
            raise ViewError(f"y must name 1-{MAX_SERIES} number columns of {name}: {', '.join(numeric)}.")
        unknown = [column for column in ys if column not in numeric]
        if unknown:
            raise ViewError(f"{unknown} are not number columns. Number columns of {name}: {', '.join(numeric)}.")
        if display == "pie" and (len(ys) != 1 or columns[ys[0]] != "int"):
            raise ViewError("A pie chart shows exactly one count column (y), e.g. complaints; shares and times "
                            "do not add up.")
        spec["x"], spec["y"] = x, ys
    return spec


def check(raw: dict, settings: Settings, selected: tu.Period) -> tuple[dict, dict]:
    """A checked spec plus what its source returns now (the stored spec never holds data)."""
    if raw.get("kind") == "note":
        return note_spec(raw), {}
    spec = data_spec(raw)
    return spec, preview(spec["source"], spec["params"], settings, selected)


def sources_text() -> str:
    """Every source with its params, displays, columns and KPI values (static: it sits in the view
    agent's cached prompt)."""
    lines = []
    for name, source in SOURCES.items():
        parts = [f"params: {', '.join(source.params) or 'none'}", f"displays: {', '.join(source.displays)}"]
        if source.rows is not None:
            parts.append("columns: " + ", ".join(f"{column} ({kind})" for column, kind in source.columns.items()))
        metrics = metric_names(name)
        if metrics:
            parts.append(("values" if source.rows is None else "KPI values") + ": " + ", ".join(
                f"{metric} ({source.columns[metric]})" if metric in source.columns else metric for metric in metrics))
        lines.append(f"- {name}: " + "; ".join(parts))
    return "\n".join(lines)


# --------------------------------------------------------------------------- data


def _prepared(name: str, params: dict, settings: Settings, selected: tu.Period) -> tuple[dict, tu.Period]:
    """Params with the period resolved now, so the cache key is the real time window."""
    if "period" not in SOURCES[name].params:
        return dict(params), selected
    period = selected
    rest = {key: value for key, value in params.items() if key not in _PERIOD}
    return {**rest, "period": "selected"}, period


def _compute(name: str, params: dict, period: tu.Period, settings: Settings) -> dict:
    raise ToolError(f"no data source for {name!r} in W3 (D-16)")


def _cached(name: str, params_json: str, key: str, start: str, end: str) -> dict:
    settings = load_settings()
    period = tu.Period(key, datetime.fromisoformat(start).astimezone(settings.tz),
                       datetime.fromisoformat(end).astimezone(settings.tz))
    return _compute(name, json.loads(params_json), period, settings)


@st.cache_data(ttl=_SETTINGS.ttl_open, max_entries=300, show_spinner=False)
def _cached_open(name: str, params_json: str, key: str, start: str, end: str, source: str) -> dict:
    return _cached(name, params_json, key, start, end)


@st.cache_data(ttl=_SETTINGS.ttl_closed, max_entries=300, show_spinner=False)
def _cached_closed(name: str, params_json: str, key: str, start: str, end: str, source: str) -> dict:
    return _cached(name, params_json, key, start, end)


def fetch(name: str, params: dict, settings: Settings, selected: tu.Period, cached: bool = True) -> dict:
    params, period = _prepared(name, params, settings, selected)
    if not cached:
        return _compute(name, params, period, settings)
    live = name == "waiting_now" or period.is_open(tu.now_local(settings.tz))
    function = _cached_open if live else _cached_closed
    return function(name, json.dumps(params, sort_keys=True, ensure_ascii=False), period.key,
                    period.start.isoformat(), period.end.isoformat(), settings.db.label)


def rows_of(name: str, result: dict) -> list[dict]:
    key = SOURCES[name].rows
    rows = result.get(key) if key else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def metric_values(name: str, result: dict) -> dict:
    values = {}
    for key, value in result.items():
        if isinstance(value, dict):
            values.update({f"{key}.{sub}": item for sub, item in value.items() if not isinstance(item, (dict, list))})
        elif not isinstance(value, list):
            values[key] = value
    source = SOURCES[name]
    if source.sums:
        rows = rows_of(name, result)
        for column, kind in source.columns.items():
            if kind == "int":
                values[f"sum.{column}"] = sum(int(row.get(column) or 0) for row in rows)
    return values


def preview(name: str, params: dict, settings: Settings, selected: tu.Period) -> dict:
    """What a source returns now, for the AI agent: columns, KPI values and a few rows (without the
    customers' own texts)."""
    if name not in SOURCES:
        raise ViewError(f"source must be one of: {', '.join(SOURCES)}.")
    params = params_of(name, params)
    result = fetch(name, params, settings, selected, cached=False)
    source = SOURCES[name]
    info = {"source": name, "period": result.get("period"), "displays": list(source.displays)}
    names = metric_names(name)
    if names:
        values = metric_values(name, result)
        info["metrics"] = {metric: values.get(metric) for metric in names}
    if source.rows is not None:
        rows = rows_of(name, result)
        info["columns"] = dict(source.columns)
        info["number_columns"] = [column for column, kind in source.columns.items() if kind in NUMERIC]
        info["rows_now"] = len(rows)
        info["first_rows"] = [{column: value for column, value in row.items() if column not in _PRIVATE}
                              for row in rows[:3]]
    return info


# --------------------------------------------------------------------------- drawing


def render(spec: dict, d, block_id: str = "") -> None:
    title = str(spec.get("title") or "")
    if spec.get("kind") == "note":
        c.section(title)
        text = str(spec.get("text") or "")
        style = spec.get("style") if spec.get("style") in NOTE_STYLES else "plain"
        if style == "plain":
            st.markdown(text)
        else:
            getattr(st, style)(text)
        return
    c.section(title, spec.get("note") or None)
    name = spec.get("source")
    try:
        if name not in SOURCES:
            raise ToolError(f"unknown source {name!r}")
        result = fetch(name, spec.get("params") or {}, d.settings, d.ctx.period)
    except ToolError as exc:  # e.g. a group that was renamed since the section was made
        log.info("custom block %s: %s", block_id, exc)
        st.warning(t("cv.unavailable", title=title))
        return
    key = f"cv-{block_id or abs(hash(json.dumps(spec, sort_keys=True)))}"
    display = spec.get("display")
    if display == "kpi":
        _kpi_cards(spec, name, result)
    elif SOURCES[name].rows is None:
        _metric_table(spec, name, result, key)
    else:
        rows = rows_of(name, result)
        if not rows:
            c.empty()
        elif display == "table":
            _table(spec, name, rows, key)
        elif display == "list":
            _cards(spec, name, rows)
        else:
            c.plot(figure(spec, rows), key=key)


def _kpi_cards(spec: dict, name: str, result: dict) -> None:
    values = metric_values(name, result)
    metrics = spec.get("metrics") or list(DEFAULT_METRICS.get(name, metric_names(name)[:4]))
    kinds = SOURCES[name].columns
    for column, metric in zip(st.columns(len(metrics)), metrics):
        with column:
            delta = values.get(DELTAS[metric]) if SOURCES[name].rows is None and metric in DELTAS else None
            c.kpi(label(metric), _text(metric, kinds.get(metric, "int"), values.get(metric)), delta,
                  inverse=metric in INVERSE)


def _metric_table(spec: dict, name: str, result: dict, key: str) -> None:
    values = metric_values(name, result)
    kinds = SOURCES[name].columns
    frame = pd.DataFrame([{t("cv.metric"): label(metric), t("cv.value"): _text(metric, kinds.get(metric, "int"),
                                                                                 values.get(metric))}
                          for metric in spec.get("metrics") or metric_names(name)])
    st.dataframe(frame, hide_index=True, width="stretch", key=key)


def _table(spec: dict, name: str, rows: list[dict], key: str) -> None:
    kinds = SOURCES[name].columns
    columns = [column for column in spec.get("columns") or kinds if column in kinds]
    frame = pd.DataFrame([{label(column): _cell(column, kinds[column], row.get(column)) for column in columns}
                          for row in rows])
    st.dataframe(frame, hide_index=True, width="stretch", key=key)


def _cards(spec: dict, name: str, rows: list[dict]) -> None:
    source = SOURCES[name]
    columns = [column for column in spec.get("columns") or source.columns if column in source.columns]
    head, rest = columns[0], columns[1:]
    cards = []
    for row in rows:
        meta, body = [], []
        for column in rest:
            kind = source.columns[column]
            if row.get(column) in (None, "", []):
                continue
            text = _text(column, kind, row.get(column))
            if column in source.long:
                body.append(f"<div>{esc(text)}</div>")
            else:
                meta.append(esc(f"{label(column)}: {text}" if kind in NUMERIC or kind == "bool" else text))
        cards.append(f'<div class="db-card"><div class="db-card-title">'
                     f'{esc(_text(head, source.columns[head], row.get(head)))}</div>'
                     + (f'<div class="db-muted">{" · ".join(meta)}</div>' if meta else "") + "".join(body) + "</div>")
    st.html("".join(cards))


def figure(spec: dict, rows: list[dict]) -> go.Figure:
    kinds = SOURCES[spec["source"]].columns
    display, x, ys = spec["display"], spec["x"], spec["y"]
    labels = [charts.esc(_text(x, kinds[x], row.get(x))) if kinds[x] != "day" else charts.day_label(row.get(x))
              for row in rows]
    colors = palette()
    fig = go.Figure()
    height = CHART_HEIGHT
    if display == "pie":  # empty slices are left out (they only add "0%" labels)
        parts = [(label_, number(kinds[ys[0]], row.get(ys[0])) or 0, colors[index % len(colors)])
                 for index, (label_, row) in enumerate(zip(labels, rows))]
        parts = [part for part in parts if part[1] > 0]
        fig.add_pie(labels=[part[0] for part in parts], values=[part[1] for part in parts], hole=0.55, sort=False,
                    direction="clockwise", marker=dict(colors=[part[2] for part in parts]), textinfo="percent",
                    hovertemplate="%{label}: %{value} (%{percent})<extra></extra>")
        fig.update_layout(template=theme.TEMPLATE_NAME, height=height,
                          legend=dict(orientation="v", y=0.5, yanchor="middle", x=1.02, xanchor="left"))
        return fig
    for index, column in enumerate(ys):
        name = charts.esc(_axis_name(column, kinds[column]))
        values = [number(kinds[column], row.get(column)) for row in rows]
        color = colors[index % len(colors)]
        if display == "hbar":
            fig.add_bar(x=values[::-1], y=labels[::-1], orientation="h", name=name, marker_color=color,
                        hovertemplate=f"%{{y}}<br>{name}: %{{x}}<extra></extra>")
        elif display in ("bar", "stacked_bar"):
            fig.add_bar(x=labels, y=values, name=name, marker_color=color,
                        hovertemplate=f"%{{x}}<br>{name}: %{{y}}<extra></extra>")
        else:
            fig.add_scatter(x=labels, y=values, name=name, mode="lines+markers", line=dict(color=color, width=2),
                            fill="tozeroy" if display == "area" else None,
                            hovertemplate=f"%{{x}}<br>{name}: %{{y}}<extra></extra>")
    options: dict = {"showlegend": len(ys) > 1}
    if display == "hbar":
        height = max(CHART_HEIGHT, 30 * len(rows) + 90)
        options.update(barmode="group", xaxis=dict(rangemode="tozero"), yaxis=dict(type="category"))
    else:
        options.update(xaxis=dict(type="category"), yaxis=dict(rangemode="tozero"))
        if display in ("bar", "stacked_bar"):
            options["barmode"] = "stack" if display == "stacked_bar" else "group"
    fig.update_layout(template=theme.TEMPLATE_NAME, height=height, **options)
    return fig
