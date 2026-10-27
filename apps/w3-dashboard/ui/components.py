"""Reusable UI pieces. Every piece of user text that goes into HTML is escaped with core.text.esc."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from core.export import to_csv_bytes, to_xlsx_bytes
from core.text import esc
from ui import overrides, theme
from ui.i18n import t

_CSS = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~:$])")

CHART_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
                "toImageButtonOptions": {"format": "png", "scale": 2}}


def md_escape(text) -> str:
    """Escape Markdown (and Streamlit directives like :red[...]) in user text for st.markdown labels."""
    return _MD_SPECIAL.sub(r"\\\1", "" if text is None else str(text))


def inject_css() -> None:
    st.html(f"<style>{_CSS}</style>")


def html(markup: str) -> None:
    st.html(markup)


def demo_banner() -> None:
    st.html(f'<div class="db-demo">{esc(t("demo.banner"))}</div>')


def status_bar(level: str, reasons: list[str]) -> None:
    title = t(f"status.{level}")
    text = " · ".join(esc(reason) for reason in reasons) if reasons else esc(t("status.ok_reason"))
    st.html(f'<div class="db-status {level}"><div class="db-status-symbol">{theme.LEVEL_SYMBOLS[level]}</div>'
            f'<div><div class="db-status-title">{esc(title)}</div><div class="db-status-reasons">{text}</div></div></div>')


def chip(text: str, kind: str = "") -> str:
    return f'<span class="db-chip {kind}">{esc(text)}</span>'


def dot(level: str) -> str:
    return f'<span class="db-dot" style="background:{theme.LEVEL_COLORS.get(level, "#94A3B8")}"></span>'


def empty(title: str | None = None, hint: str | None = None) -> None:
    st.html(f'<div class="db-empty"><div class="db-empty-title">{esc(title or t("empty.period"))}</div>'
            f'<div class="db-muted">{esc(hint or t("empty.hint"))}</div></div>')


def kpi(label: str, value: str, delta: str | None = None, *, inverse: bool = False, neutral: bool = False,
        help: str | None = None, spark: list | None = None, border: bool = True, chart_type: str = "area",
        show_label: bool = True, compare: bool = False) -> None:
    """KPI card. ``inverse``: growth is bad (complaints, missed); ``neutral``: growth is neither good
    nor bad (message volume); a zero change gets no arrow. ``compare``: comparison is on, so a missing
    change is shown as a grey "—" (keeps the cards of a row the same height)."""
    if delta is None and compare:
        delta = "—"
    flat = delta is not None and (delta == "—" or delta.lstrip("+-").startswith("0"))
    color = "off" if (neutral or flat) else ("inverse" if inverse else "normal")
    label = overrides.take_block_title(label)
    st.metric(label, value, delta=delta, delta_color=color, delta_arrow="off" if flat else "auto", help=help,
              border=border, chart_data=spark if spark and len(spark) > 1 else None, chart_type=chart_type,
              label_visibility="visible" if show_label else "collapsed")


def section(title: str, caption: str | None = None) -> None:
    st.subheader(overrides.take_block_title(title), anchor=False)
    if caption:
        st.caption(caption)


def plot(fig, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", theme="streamlit", config=CHART_CONFIG, key=key)


def summary_list(sentences: list[str]) -> None:
    items = "".join(f"<li>{esc(sentence)}</li>" for sentence in sentences)
    st.html(f'<ul class="db-summary">{items}</ul>')


def download_buttons(frame: pd.DataFrame, base_name: str, key: str) -> None:
    safe_name = re.sub(r"[^0-9A-Za-z_\-]+", "_", base_name).strip("_") or "export"
    left, right, _rest = st.columns([1, 1, 4])
    with left:
        st.download_button(t("export.csv"), to_csv_bytes(frame), file_name=f"{safe_name}.csv", mime="text/csv",
                           icon=":material/download:", key=f"{key}-csv", on_click="ignore", width="stretch")
    with right:
        st.download_button(t("export.xlsx"), to_xlsx_bytes(frame), file_name=f"{safe_name}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/table_view:", key=f"{key}-xlsx", on_click="ignore", width="stretch")


def export_name(prefix: str, first: date, last: date) -> str:
    return f"{prefix}_{first.isoformat()}_{last.isoformat()}"
