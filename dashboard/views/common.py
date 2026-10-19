"""Data assembly shared by the pages (no SQL here: everything goes through ui.data -> core.queries),
the per-run PageData that a page's layout blocks share, and the blocks used on several pages."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Callable

import pandas as pd
import streamlit as st

from core import issues as issues_mod
from core import metrics as m_
from core import timeutil as tu
from core.text import esc
from ui import components as c
from ui import data, layout
from ui.i18n import t
from ui.shell import Ctx

SPARK_DAYS = 14


def kpis(ctx: Ctx, period: tu.Period | None, chat_id: int | None = None) -> m_.Kpis | None:
    if period is None:
        return None
    live = period.is_open(ctx.now)
    totals = data.get("totals", period.start_db, period.end_db, chat_id, live=live)
    checks = data.get("checks", period.start_db, period.end_db, chat_id, live=live)
    return m_.build_kpis(totals, checks)


def checks(ctx: Ctx, period: tu.Period, chat_id: int | None = None) -> pd.DataFrame:
    return data.get("checks", period.start_db, period.end_db, chat_id, live=period.is_open(ctx.now))


def trend_window(ctx: Ctx) -> tu.Period:
    """The period itself when it is at least a week long, else the last 14 days up to its end."""
    if ctx.period.days >= 7:
        return ctx.period
    return tu.day_window(ctx.last_day - timedelta(days=SPARK_DAYS - 1), ctx.last_day, ctx.settings.tz, "trend")


def daily(ctx: Ctx, window: tu.Period, chat_id: int | None = None) -> pd.DataFrame:
    frame = data.get("daily", window.start_db, window.end_db, window.offset_modifier(), chat_id,
                     live=window.is_open(ctx.now))
    days = pd.DataFrame({"day": [day.isoformat() for day in window.local_days()]})
    frame = days.merge(frame, on="day", how="left").fillna(0)
    for column in ("customer", "related", "complaints", "positive", "filtered", "answered", "missed", "neutral"):
        frame[column] = frame[column].astype(int) if column in frame else 0
    return frame


def daily_medians(check_frame: pd.DataFrame, tz) -> pd.Series:
    """Median answer time (minutes) per local day of the request."""
    if check_frame.empty:
        return pd.Series(dtype="float64")
    frame = pd.DataFrame({
        "day": check_frame["started_at_ts"].dt.tz_convert(tz).dt.date.astype(str),
        "s": check_frame["response_s"].fillna(check_frame["later_s"]),
    }).dropna()
    return frame.groupby("day")["s"].median() / 60


def spark(series) -> list:
    values = [float(v) for v in pd.Series(series, dtype="float64").tolist() if pd.notna(v)]
    return values if len(values) > 1 else []


def group_table(ctx: Ctx) -> pd.DataFrame:
    """All groups with period numbers and the risk score (growth always uses the previous period)."""
    period = ctx.period
    previous = ctx.previous or period.previous(ctx.now)
    frame = data.get("groups_overview", period.start_db, period.end_db, previous.start_db, previous.end_db,
                     live=ctx.live)
    return m_.add_risk(frame, checks(ctx, period), ctx.settings)


def report_days(ctx: Ctx) -> tuple[date, date]:
    """Bot report days that overlap the selected period."""
    report_time = ctx.settings.report_time
    first = tu.report_day_of(ctx.period.start, report_time)
    last = tu.report_day_of(min(ctx.period.end, ctx.now) - timedelta(microseconds=1), report_time)
    return first, max(first, last)


def report_window_text(day: date, ctx: Ctx) -> str:
    start, end = tu.report_bounds(day, ctx.settings.report_time, ctx.settings.tz)
    return f"{tu.fmt_dt(start)} — {tu.fmt_dt(end)}"


def change_text(current, previous, *, is_rate: bool = False) -> str | None:
    if previous is None:
        return None
    result = m_.change(current, previous, is_rate=is_rate)
    return result.text if result else None


def duration_change(current_s: float | None, previous_s: float | None) -> str | None:
    if current_s is None or previous_s is None:
        return None
    diff = (current_s - previous_s) / 60
    if abs(diff) < 1:
        return f"{diff * 60:+.0f} soniya"
    return f"{diff:+.0f} daq"


def issues_notice(ctx: Ctx, chat_id: int | None = None) -> None:
    """Discreet data-quality warning: known issues for the period plus automatically found ones."""
    found = [(issue.title, issue.text) for issue in issues_mod.issues_for(ctx.first_day, ctx.last_day)]
    quality = data.get("quality", ctx.period.start_db, ctx.period.end_db, live=ctx.live)
    if quality.get("voice_empty"):
        found.append((t("sy.voice"), t("issues.auto_voice", n=quality["voice_empty"])))
    if quality.get("failed"):
        found.append((t("sy.failed"), t("issues.auto_failed", n=quality["failed"])))
    if quality.get("pending"):
        found.append((t("sy.pending"), t("issues.auto_pending", n=quality["pending"])))
    if not found:
        return
    with st.expander(t("issues.title", n=len(found)), icon=":material/info:"):
        st.html("".join(f'<div class="db-small" style="margin-bottom:.45rem"><b>{esc(title)}</b> — {esc(text)}</div>'
                        for title, text in found))


YOUNG_DATA_DAYS = 14


def young_data_note(ctx: Ctx) -> None:
    """While monitoring has collected less than two weeks of data, say so (charts look sparse)."""
    if ctx.settings.demo:
        return
    first = tu.from_db(data.get("freshness").get("first_message"), ctx.settings.tz)
    if first is None:
        return
    days = (ctx.now.date() - first.date()).days + 1
    if days < YOUNG_DATA_DAYS:
        st.caption(f":material/hourglass_top: {t('ov.young', start=tu.fmt_day(first.date()), days=days)}")


def freshness_level(ctx: Ctx) -> str:
    info = data.get("freshness")
    last = tu.from_db(info.get("last_message"), ctx.settings.tz)
    if last is None:
        return "red"
    return m_.freshness_level((ctx.now - last).total_seconds() / 60, ctx.settings)


def risk_badge(level: str, score: int) -> str:
    return f'<span class="db-risk-score {level}">{score}</span>'


def group_link_params(chat_id: int) -> dict[str, str]:
    from ui.shell import QP_GROUP, global_params

    return {**global_params(), QP_GROUP: str(chat_id)}


def records(frame: pd.DataFrame) -> list[dict]:
    """Rows as dicts with missing values as None (pandas uses NaN, which is truthy)."""
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def message_text(row) -> str:
    """Display text of a message row: text / caption, or the voice transcript."""
    text = row.get("text") if isinstance(row, dict) else getattr(row, "text", None)
    transcript = row.get("transcript") if isinstance(row, dict) else getattr(row, "transcript", None)
    parts = [part for part in (text, transcript) if isinstance(part, str) and part]
    return "\n".join(parts)


def local_time_column(frame: pd.DataFrame, column: str, tz) -> pd.Series:
    return pd.to_datetime(frame[column], utc=True, errors="coerce").dt.tz_convert(tz).dt.tz_localize(None)


def empty_if(condition: bool) -> bool:
    if condition:
        c.empty()
    return condition


@st.fragment(run_every="45s")
def waiting_fragment(tz) -> None:
    c.section(t("ov.waiting"))
    frame = data.get("waiting_now", live=True)
    now = datetime.now(tz)
    if frame.empty:
        c.empty(t("ov.waiting.empty"), " ")
    else:
        cards = []
        for row in (SimpleNamespace(**record) for record in records(frame.head(12))):
            deadline = tu.from_db(row.deadline, tz)
            started = tu.from_db(row.started_at, tz)
            left = (deadline - now).total_seconds() / 60 if deadline else 0
            state = (c.chip(t("ov.waiting.left", minutes=max(0, int(left) + 1)), "yellow") if left > 0
                     else c.chip(t("ov.waiting.overdue"), "red"))
            who = row.sender_name or (f"@{row.sender_username}" if row.sender_username else "—")
            cards.append(f'<div class="db-card"><div class="db-card-title">{esc(row.title)}</div>'
                         f'<div class="db-muted">{esc(who)} · {esc(tu.fmt_dt(started))}</div>'
                         f'<div>{esc(row.request_text or "")}</div><div>{state}</div></div>')
        st.html("".join(cards))
    st.caption(t("ov.waiting.refreshed", time=now.strftime("%H:%M:%S")))


# --------------------------------------------------------------------------- shared by the layout blocks


class PageData:
    """What the blocks of one page run share. Everything is computed on first use, so a hidden block
    costs nothing, and a portable block works on any page (``chat_id`` is set only on a group's detail
    view, ``category`` only on a category's detail view)."""

    def __init__(self, ctx: Ctx, chat_id: int | None = None, category: str | None = None, **extra) -> None:
        self.ctx = ctx
        self.settings = ctx.settings
        self.chat_id = chat_id
        self.category = category
        self.extra = extra
        self.page_id = ""  # set by layout.render_page
        self.page_state: dict = {}
        self.visible_ids: set[str] = set()
        self._memo: dict = {}

    def memo(self, key: str, compute: Callable):
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    def hidden_items(self, block_id: str) -> set[str]:
        return layout.hidden_item_ids(self.page_state, block_id)

    @property
    def live(self) -> bool:
        return self.ctx.live

    @property
    def cur(self) -> m_.Kpis:
        return self.memo("cur", lambda: kpis(self.ctx, self.ctx.period, self.chat_id))

    @property
    def prev(self) -> m_.Kpis | None:
        return self.memo("prev", lambda: kpis(self.ctx, self.ctx.previous, self.chat_id))

    @property
    def window(self) -> tu.Period:
        return self.memo("window", lambda: trend_window(self.ctx))

    @property
    def days(self) -> pd.DataFrame:
        return self.memo("days", lambda: daily(self.ctx, self.window, self.chat_id))

    @property
    def period_checks(self) -> pd.DataFrame:
        return self.memo("checks", lambda: checks(self.ctx, self.ctx.period, self.chat_id))

    @property
    def groups(self) -> pd.DataFrame:
        return self.memo("groups", lambda: group_table(self.ctx))

    @property
    def cats(self) -> pd.DataFrame:
        period = self.ctx.period
        return self.memo("cats", lambda: data.get("by_category", period.start_db, period.end_db, self.chat_id,
                                                  live=self.ctx.live))

    @property
    def prev_cats(self) -> pd.DataFrame | None:
        previous = self.ctx.previous
        return self.memo("prev_cats", lambda: data.get("by_category", previous.start_db, previous.end_db, None,
                                                       live=False) if previous else None)


ISSUES = layout.Block("issues", "blk.issues", lambda d: issues_notice(d.ctx),
                      keywords="ogohlantirish, ma'lumot sifati, предупреждение")
WAITING = layout.Block("waiting", "blk.waiting", lambda d: waiting_fragment(d.settings.tz), portable=True,
                       keywords="hozir javob kutayotganlar, kutmoqda, navbat, ожидают ответа")
