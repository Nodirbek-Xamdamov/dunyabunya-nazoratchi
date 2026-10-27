"""Plotly figure builders. All labels are Uzbek; user text (group titles, names) is escaped because
Plotly interprets a subset of HTML in labels."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go

from core import metrics as m_
from core.timeutil import MONTHS, WEEKDAYS, WEEKDAYS_SHORT, fmt_day
from ui import theme
from ui.i18n import t

_SHORT_MONTHS = [name[:3] for name in MONTHS]


def esc(text) -> str:
    """Plotly renders a small HTML subset in labels (tags start with "<"); it does not decode
    entities such as &quot;. Replacing the angle brackets is enough to keep user text inert."""
    return str("" if text is None else text).replace("<", "‹").replace(">", "›")


def day_label(value) -> str:
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{day.day}-{_SHORT_MONTHS[day.month - 1]}"


def _short(text: str, limit: int = 34) -> str:
    text = str(text)
    return esc(text if len(text) <= limit else text[: limit - 1] + "…")


def _finish(fig: go.Figure, height: int, **layout) -> go.Figure:
    fig.update_layout(template=theme.TEMPLATE_NAME, height=height, **layout)
    return fig


def daily_dynamics(frame: pd.DataFrame, height: int = 330, with_missed: bool = True) -> go.Figure:
    labels = [day_label(day) for day in frame["day"]]
    fig = go.Figure()
    for sentiment, column in ((m_.NEUTRAL, "neutral"), (m_.POSITIVE, "positive"), (m_.COMPLAINT, "complaints")):
        name = t(f"sent.{sentiment}")
        fig.add_bar(x=labels, y=frame[column], name=name, marker_color=theme.SENTIMENT_COLORS[sentiment],
                    hovertemplate=f"%{{x}}<br>{name}: %{{y}}<extra></extra>")
    if with_missed and "missed" in frame:
        name = t("series.missed")
        fig.add_scatter(x=labels, y=frame["missed"], name=name, mode="lines+markers", yaxis="y2",
                        line=dict(color=theme.MISSED, width=2), marker=dict(size=6, symbol="diamond"),
                        hovertemplate=f"%{{x}}<br>{name}: %{{y}}<extra></extra>")
    return _finish(fig, height, barmode="stack",
                   yaxis=dict(title=t("series.related"), rangemode="tozero"),
                   yaxis2=dict(tickmode="auto", title=t("series.missed"), overlaying="y", side="right", showgrid=False,
                               rangemode="tozero"),
                   xaxis=dict(type="category"))


def category_bars(frame: pd.DataFrame, height: int = 330) -> go.Figure:
    data = frame.sort_values("related")
    rates = [m_.complaint_rate(c, r) for c, r in zip(data["complaints"], data["related"])]
    custom = [[int(c), f"{round(r * 100)}%" if r is not None else "—"] for c, r in zip(data["complaints"], rates)]
    fig = go.Figure(go.Bar(
        x=data["related"], y=[esc(name) for name in data["category"]], orientation="h",
        marker_color=[theme.category_color(name) for name in data["category"]],
        text=data["related"], textposition="outside", cliponaxis=False, customdata=custom,
        hovertemplate=f"%{{y}}<br>{t('series.related')}: %{{x}}<br>"
                      f"{t('series.complaints')}: %{{customdata[0]}} (%{{customdata[1]}})<extra></extra>"))
    return _finish(fig, height, showlegend=False, xaxis=dict(title=None, rangemode="tozero"))


def sentiment_donut(complaints: int, neutral: int, positive: int, height: int = 300) -> go.Figure:
    labels = [t("sent.Shikoyat"), t("sent.Neytral"), t("sent.Ijobiy")]
    fig = go.Figure(go.Pie(
        labels=labels, values=[complaints, neutral, positive], hole=0.62, sort=False, direction="clockwise",
        marker=dict(colors=[theme.COMPLAINT, theme.NEUTRAL, theme.POSITIVE]), textinfo="percent",
        hovertemplate="%{label}: %{value} (%{percent})<extra></extra>"))
    total = complaints + neutral + positive
    fig.add_annotation(text=f"<b>{total:,}</b>".replace(",", " "), showarrow=False, font=dict(size=22))
    return _finish(fig, height, legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"))


def heat_hours(frame: pd.DataFrame, height: int = 300) -> go.Figure:
    grid = [[0] * 24 for _ in range(7)]
    for row in frame.itertuples():
        grid[int(row.wd)][int(row.hour)] = int(row.n)
    fig = go.Figure(go.Heatmap(
        z=grid, x=[f"{hour:02d}" for hour in range(24)], y=list(WEEKDAYS_SHORT), colorscale=theme.HEAT_SCALE,
        showscale=False, xgap=2, ygap=2, customdata=[[WEEKDAYS[i]] * 24 for i in range(7)],
        hovertemplate="%{customdata}, %{x}:00<br>" + t("series.related") + ": %{z}<extra></extra>"))
    return _finish(fig, height, yaxis=dict(autorange="reversed", showgrid=False), xaxis=dict(showgrid=False))


def heat_matrix(frame: pd.DataFrame, rows: str, cols: str, values: str, row_order: list, col_order: list,
                scale, height: int, value_label: str, row_labels: dict | None = None) -> go.Figure:
    pivot = frame.pivot_table(index=rows, columns=cols, values=values, aggfunc="sum", fill_value=0)
    pivot = pivot.reindex(index=row_order, columns=col_order, fill_value=0)
    labels = [_short((row_labels or {}).get(key, key)) for key in pivot.index]
    full = [[esc((row_labels or {}).get(key, key))] * len(pivot.columns) for key in pivot.index]
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[esc(str(col)) for col in pivot.columns], y=labels, colorscale=scale, showscale=False,
        xgap=2, ygap=2, customdata=full,
        hovertemplate="%{customdata}<br>%{x}<br>" + value_label + ": %{z}<extra></extra>"))
    return _finish(fig, height, yaxis=dict(autorange="reversed", showgrid=False), xaxis=dict(showgrid=False))


def hbar(labels: list[str], values: list, color: str, height: int, value_label: str,
         custom: list | None = None, custom_label: str | None = None) -> go.Figure:
    hover = f"%{{y}}<br>{value_label}: %{{x}}"
    if custom is not None and custom_label:
        hover += f"<br>{custom_label}: %{{customdata}}"
    fig = go.Figure(go.Bar(
        x=list(values)[::-1], y=[_short(label, 40) for label in labels][::-1], orientation="h", marker_color=color,
        text=list(values)[::-1], textposition="outside", cliponaxis=False,
        customdata=list(custom)[::-1] if custom is not None else None, hovertemplate=hover + "<extra></extra>"))
    return _finish(fig, height, showlegend=False, xaxis=dict(rangemode="tozero"))


def answered_missed(frame: pd.DataFrame, x: str, x_labels: list[str], height: int = 300) -> go.Figure:
    """Stacked answered / missed bars with the SLA % line (frame has answered, missed)."""
    fig = go.Figure()
    for column, color, key in (("answered", theme.ANSWERED, "series.answered"), ("missed", theme.MISSED, "series.missed")):
        name = t(key)
        fig.add_bar(x=x_labels, y=frame[column], name=name, marker_color=color,
                    hovertemplate=f"%{{x}}<br>{name}: %{{y}}<extra></extra>")
    total = frame["answered"] + frame["missed"]
    sla = (frame["answered"] / total.where(total > 0)).mul(100).round(0)
    name = t("series.sla")
    fig.add_scatter(x=x_labels, y=sla, name=name, mode="lines+markers", yaxis="y2", connectgaps=False,
                    line=dict(color=theme.BRAND, width=2), hovertemplate=f"%{{x}}<br>{name}: %{{y}}%<extra></extra>")
    return _finish(fig, height, barmode="stack", xaxis=dict(type="category"),
                   yaxis2=dict(tickmode="auto", overlaying="y", side="right", range=[0, 105], ticksuffix="%", showgrid=False))


def response_histogram(minutes: pd.Series, sla_minutes: int, height: int = 300) -> go.Figure:
    edges = [0, 1, 2, 3, 4, sla_minutes, 10, 30, 60, 180, float("inf")]
    edges = sorted(set(edges))
    labels = []
    for low, high in zip(edges[:-1], edges[1:]):
        labels.append(f"{low:g}–{high:g}" if high != float("inf") else f"{low:g}+")
    counts = pd.cut(minutes, bins=edges, right=False, labels=labels).value_counts().reindex(labels, fill_value=0)
    colors = [theme.POSITIVE if high <= sla_minutes else theme.MISSED for high in edges[1:]]
    fig = go.Figure(go.Bar(x=labels, y=counts.values, marker_color=colors, text=counts.values, textposition="outside",
                           cliponaxis=False,
                           hovertemplate="%{x} " + "daq" + "<br>" + t("axis.count") + ": %{y}<extra></extra>"))
    return _finish(fig, height, showlegend=False, xaxis=dict(type="category", title=t("rs.hist.x")))


def stacked_hbar(frame: pd.DataFrame, label: str, parts: list[tuple[str, str, str]], height: int) -> go.Figure:
    data = frame.iloc[::-1]
    fig = go.Figure()
    for column, color, name in parts:
        fig.add_bar(x=data[column], y=[_short(v, 36) for v in data[label]], orientation="h", name=name,
                    marker_color=color, hovertemplate=f"%{{y}}<br>{name}: %{{x}}<extra></extra>")
    return _finish(fig, height, barmode="stack")


def period_compare(frame: pd.DataFrame, height: int = 320) -> go.Figure:
    """frame: label, related, complaints, missed."""
    fig = go.Figure()
    fig.add_bar(x=frame["label"], y=frame["related"], name=t("series.related"), marker_color="rgba(79,70,229,0.35)",
                hovertemplate="%{x}<br>" + t("series.related") + ": %{y}<extra></extra>")
    fig.add_bar(x=frame["label"], y=frame["complaints"], name=t("series.complaints"), marker_color=theme.COMPLAINT,
                hovertemplate="%{x}<br>" + t("series.complaints") + ": %{y}<extra></extra>")
    fig.add_scatter(x=frame["label"], y=frame["missed"], name=t("series.missed"), mode="lines+markers", yaxis="y2",
                    line=dict(color=theme.MISSED, width=2),
                    hovertemplate="%{x}<br>" + t("series.missed") + ": %{y}<extra></extra>")
    return _finish(fig, height, barmode="group", xaxis=dict(type="category"),
                   yaxis2=dict(tickmode="auto", overlaying="y", side="right", showgrid=False, rangemode="tozero"))


def stacked_categories(frame: pd.DataFrame, x: str, x_order: list[str], value: str, height: int = 320) -> go.Figure:
    fig = go.Figure()
    for item in m_.categories():
        part = frame[frame["category"] == item.name].set_index(x)[value].reindex(x_order, fill_value=0)
        if part.sum() == 0:
            continue
        fig.add_bar(x=x_order, y=part.values, name=esc(item.name), marker_color=item.color,
                    hovertemplate="%{x}<br>" + esc(item.name) + ": %{y}<extra></extra>")
    return _finish(fig, height, barmode="stack", xaxis=dict(type="category"))


def calendar(daily: pd.Series, first: date, last: date, height: int = 230) -> go.Figure:
    """GitHub-style calendar: columns are weeks (Monday start), rows are weekdays."""
    start = first - timedelta(days=first.weekday())
    weeks = (last - start).days // 7 + 1
    z = [[None] * weeks for _ in range(7)]
    text = [[""] * weeks for _ in range(7)]
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        week, weekday = (day - start).days // 7, day.weekday()
        value = int(daily.get(day.isoformat(), 0))
        z[weekday][week] = value
        text[weekday][week] = f"{fmt_day(day)} ({WEEKDAYS[weekday]})"
    x_labels = [day_label(start + timedelta(weeks=i)) for i in range(weeks)]
    fig = go.Figure(go.Heatmap(z=z, x=x_labels, y=list(WEEKDAYS_SHORT), customdata=text, colorscale=theme.HEAT_RED,
                               showscale=False, xgap=3, ygap=3, hoverongaps=False,
                               hovertemplate="%{customdata}<br>" + t("series.complaints") + ": %{z}<extra></extra>"))
    return _finish(fig, height, yaxis=dict(autorange="reversed", showgrid=False),
                   xaxis=dict(showgrid=False, type="category"))


def anomalies(frame: pd.DataFrame, height: int = 300) -> go.Figure:
    """frame: day, complaints, mean, upper, anomaly (bool)."""
    labels = [day_label(day) for day in frame["day"]]
    fig = go.Figure()
    fig.add_scatter(x=labels, y=frame["upper"], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=labels, y=frame["mean"], mode="lines", name=t("series.mean"), fill="tonexty",
                    fillcolor="rgba(148,163,184,0.18)", line=dict(color=theme.NEUTRAL, dash="dash"),
                    hovertemplate="%{x}<br>" + t("series.mean") + ": %{y:.1f}<extra></extra>")
    fig.add_scatter(x=labels, y=frame["complaints"], mode="lines+markers", name=t("series.complaints"),
                    line=dict(color=theme.COMPLAINT, width=2),
                    hovertemplate="%{x}<br>" + t("series.complaints") + ": %{y}<extra></extra>")
    flagged = frame[frame["anomaly"]]
    if not flagged.empty:
        fig.add_scatter(x=[day_label(day) for day in flagged["day"]], y=flagged["complaints"], mode="markers",
                        name=t("series.anomaly"), marker=dict(size=13, color=theme.COMPLAINT, symbol="star",
                                                               line=dict(width=1, color="white")),
                        hovertemplate="%{x}<br>" + t("series.anomaly") + ": %{y}<extra></extra>")
    return _finish(fig, height, xaxis=dict(type="category"), yaxis=dict(rangemode="tozero"))
