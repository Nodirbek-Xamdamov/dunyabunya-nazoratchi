"""Colours and the shared Plotly template. One brand colour plus semantic colours; the eight
categories keep the same colour everywhere (config/categories.toml)."""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

from core import metrics as m_

BRAND = "#4F46E5"
COMPLAINT = "#DC2626"
POSITIVE = "#16A34A"
NEUTRAL = "#94A3B8"
MISSED = "#EA580C"
ANSWERED = "#0EA5E9"
GRID = "rgba(128, 128, 128, 0.18)"

SENTIMENT_COLORS = {m_.COMPLAINT: COMPLAINT, m_.POSITIVE: POSITIVE, m_.NEUTRAL: NEUTRAL}
LEVEL_COLORS = {"green": "#16A34A", "yellow": "#D97706", "red": "#DC2626"}
LEVEL_ICONS = {"green": ":material/check_circle:", "yellow": ":material/warning:", "red": ":material/error:"}
LEVEL_SYMBOLS = {"green": "●", "yellow": "▲", "red": "■"}  # shape as well as colour

HEAT_SCALE = [[0.0, "rgba(79, 70, 229, 0.04)"], [0.25, "rgba(79, 70, 229, 0.30)"], [1.0, "rgba(79, 70, 229, 1)"]]
HEAT_RED = [[0.0, "rgba(220, 38, 38, 0.04)"], [0.25, "rgba(220, 38, 38, 0.35)"], [1.0, "rgba(185, 28, 28, 1)"]]

TEMPLATE_NAME = "dunyabunya"

pio.templates[TEMPLATE_NAME] = go.layout.Template(
    layout=dict(
        font=dict(family="Inter, 'Segoe UI', Roboto, sans-serif", size=13),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=28, b=8),
        xaxis=dict(gridcolor=GRID, zeroline=False, showline=False, automargin=True),
        yaxis=dict(gridcolor=GRID, zeroline=False, showline=False, automargin=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
        hoverlabel=dict(font_size=12),
        separators=", ",  # decimal comma, thin thousands separator (Uzbek style)
        colorway=[BRAND, MISSED, POSITIVE, COMPLAINT, NEUTRAL],
        bargap=0.25,
    )
)


def category_color(name: str) -> str:
    return m_.category_color(name)


def category_colors() -> dict[str, str]:
    return {item.name: item.color for item in m_.categories()}
