"""Drawing a desk zone on the camera picture with the mouse, using only Streamlit + Plotly.

The picture is the chart background; an invisible grid of points lies over it. A click selects the
nearest grid point, which becomes the next corner of the zone; with the lasso tool of the chart's toolbar a
free shape can be drawn instead, which is simplified to a polygon. Clicks only select while the drag mode
is not lasso/select (Streamlit then switches clickmode to "event"), so the chart starts in pan mode with
fixed axes. Each processed selection gets a fresh chart key, so the same click is never added twice.
Zones are stored as fractions of the picture (0..1).
"""

from __future__ import annotations

import base64
import math

import numpy as np
import plotly.graph_objects as go
import streamlit as st

GRID_STEP = 20  # picture pixels between the invisible click targets (~1% of a 1920 px frame)
MAX_POINTS = 20
COLORS = ("#16A34A", "#0EA5E9", "#EA580C", "#A855F7", "#DC2626", "#EAB308")


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on an open path."""
    if len(points) < 3:
        return points

    def dist(p, a, b):
        if a == b:
            return math.dist(p, a)
        return abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / math.dist(a, b)

    index, best = 0, 0.0
    for i in range(1, len(points) - 1):
        d = dist(points[i], points[0], points[-1])
        if d > best:
            index, best = i, d
    if best <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[: index + 1], tolerance)[:-1] + simplify(points[index:], tolerance)


def lasso_polygon(xs: list[float], ys: list[float], width: int, height: int) -> list[list[float]]:
    path = [(float(x), float(y)) for x, y in zip(xs, ys)]
    tolerance = 0.006 * math.hypot(width, height)
    poly = simplify(path, tolerance)
    while len(poly) > MAX_POINTS:
        tolerance *= 1.5
        poly = simplify(path, tolerance)
    if len(poly) > 2 and math.dist(poly[0], poly[-1]) < tolerance:
        poly = poly[:-1]
    return [[x / width, y / height] for x, y in poly]


def read_selection(state, width: int, height: int) -> tuple[str, list[list[float]]] | None:
    """('add', [point]) for a click, ('replace', polygon) for a lasso/box, None when nothing new."""
    if not state:
        return None
    selection = state.get("selection") if hasattr(state, "get") else None
    if not selection:
        return None
    for lasso in selection.get("lasso") or []:
        xs, ys = lasso.get("x") or [], lasso.get("y") or []
        if len(xs) >= 3:
            return "replace", lasso_polygon(xs, ys, width, height)
    points = [(float(p["x"]), float(p["y"])) for p in selection.get("points") or [] if "x" in p and "y" in p]
    if len(points) == 1:
        return "add", [[points[0][0] / width, points[0][1] / height]]
    if len(points) >= 3:
        return "replace", [[x / width, y / height] for x, y in convex_hull(points)]
    return None


def figure(image: bytes, width: int, height: int, zones: list[dict], draft: list[list[float]], editing: int | str,
           display_height: int = 520) -> go.Figure:
    """zones: [{"id", "label", "zone"}]; the zone being edited is shown as the draft instead."""
    fig = go.Figure()
    xs, ys = np.meshgrid(np.arange(GRID_STEP / 2, width, GRID_STEP), np.arange(GRID_STEP / 2, height, GRID_STEP))
    fig.add_trace(go.Scatter(x=xs.ravel(), y=ys.ravel(), mode="markers", hoverinfo="none", showlegend=False,
                             marker=dict(size=GRID_STEP, opacity=0, color="#000"), name="grid"))
    for index, zone in enumerate(zones):
        if zone["id"] == editing or len(zone["zone"]) < 3:
            continue
        px = [p[0] * width for p in zone["zone"]] + [zone["zone"][0][0] * width]
        py = [p[1] * height for p in zone["zone"]] + [zone["zone"][0][1] * height]
        color = COLORS[index % len(COLORS)]
        fig.add_trace(go.Scatter(x=px, y=py, mode="lines", fill="toself", line=dict(color=color, width=2),
                                 fillcolor=_alpha(color, 0.15), hoverinfo="skip", showlegend=False))
        cx, cy = sum(px[:-1]) / (len(px) - 1), min(py)
        fig.add_annotation(x=cx, y=cy, text=zone["label"], showarrow=False, yshift=12,
                           font=dict(color="#fff", size=13), bgcolor=_alpha(color, 0.85))
    if draft:
        px = [p[0] * width for p in draft]
        py = [p[1] * height for p in draft]
        if len(draft) >= 3:
            px, py = px + [px[0]], py + [py[0]]
        fig.add_trace(go.Scatter(x=px, y=py, mode="lines+markers", fill="toself" if len(draft) >= 3 else None,
                                 line=dict(color="#FACC15", width=3, dash="dash"), marker=dict(size=9, color="#FACC15"),
                                 fillcolor="rgba(250, 204, 21, 0.18)", hoverinfo="skip", showlegend=False))
    fig.update_layout(
        images=[dict(source="data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"), xref="x", yref="y",
                     x=0, y=0, sizex=width, sizey=height, sizing="stretch", layer="below")],
        xaxis=dict(range=[0, width], visible=False, constrain="domain", constraintoward="left", fixedrange=True),
        yaxis=dict(range=[height, 0], visible=False, scaleanchor="x", constrain="domain", constraintoward="top",
                   fixedrange=True),
        margin=dict(l=0, r=0, t=0, b=0), height=display_height, dragmode="pan", hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


def _alpha(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"


def editor(image: bytes, width: int, height: int, zones: list[dict], editing: int | str, draft_key: str) -> list:
    """Shows the picture, applies a new click/lasso to the draft (Session State) and returns the draft."""
    counter_key = f"{draft_key}-n"
    number = st.session_state.get(counter_key, 0)
    previous = st.session_state.get(f"{draft_key}-chart-{number}")
    change = read_selection(previous, width, height)
    draft = list(st.session_state.get(draft_key, []))
    if change:
        kind, points = change
        draft = (draft + points)[:MAX_POINTS] if kind == "add" else points
        st.session_state[draft_key] = draft
        number += 1
        st.session_state[counter_key] = number
    fig = figure(image, width, height, zones, draft, editing)
    st.plotly_chart(fig, key=f"{draft_key}-chart-{number}", on_select="rerun", selection_mode=("points", "lasso"),
                    width="stretch", config={"displaylogo": False, "scrollZoom": False, "displayModeBar": True,
                                             "modeBarButtonsToRemove": ["zoom2d", "zoomIn2d", "zoomOut2d",
                                                                        "autoScale2d", "resetScale2d", "toImage"]})
    return draft
