"""Tizim va ma'lumot sifati: freshness, classification queue, voice messages, group health, backups,
consistency with the bot's stored report numbers, known issues. Every section is a layout block."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from core import issues as issues_mod
from core import metrics as m_
from core import timeutil as tu
from core.text import esc
from ui import data, layout, shell
from ui import components as c
from ui.i18n import t
from views.common import PageData

_BACKUP = re.compile(r"^monitor_\d{4}-\d{2}-\d{2}\.db$")


def _size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "—"
    return f"{size / 1024 / 1024:.1f} MB".replace(".", ",")


def _fresh_info(d: PageData) -> dict:
    return d.memo("sy.fresh", lambda: data.get("freshness"))


def _fresh(d: PageData) -> None:
    settings, fresh = d.settings, _fresh_info(d)
    c.section(t("sy.fresh"))
    cols = st.columns(3)
    for col, key, label in ((cols[0], "last_message", "sy.last_message"),
                            (cols[1], "last_classified", "sy.last_classified"),
                            (cols[2], "last_report", "sy.last_report")):
        moment = tu.from_db(fresh.get(key), settings.tz)
        with col:
            c.kpi(t(label), tu.fmt_ago(moment, d.ctx.now), help=tu.fmt_dt(moment, True))


def _queue(d: PageData) -> None:
    ctx, settings, fresh = d.ctx, d.settings, _fresh_info(d)
    c.section(t("sy.queue"))
    quality = data.get("quality", ctx.period.start_db, ctx.period.end_db, live=ctx.live)
    oldest = tu.from_db(fresh.get("oldest_pending"), settings.tz)
    classified = (quality["model"] or 0) + (quality["rule"] or 0)
    cols = st.columns(4)
    with cols[0]:
        c.kpi(t("sy.pending"), tu.fmt_int(fresh.get("pending") or 0),
              help=t("sy.pending.oldest", ago=tu.fmt_ago(oldest, ctx.now)) if oldest else None)
    with cols[1]:
        c.kpi(t("sy.failed"), f"{tu.fmt_int(quality['failed'])} · {tu.fmt_pct(m_.ratio(quality['failed'], quality['customer']))}")
    with cols[2]:
        c.kpi(t("sy.model_share"),
              f"{tu.fmt_pct(m_.ratio(quality['model'], classified))} / {tu.fmt_pct(m_.ratio(quality['rule'], classified))}")
    with cols[3]:
        c.kpi(t("sy.voice.empty"), f"{tu.fmt_int(quality['voice_empty'])} / {tu.fmt_int(quality['speech'])}",
              help=t("sy.voice.total"))


def _groups(d: PageData) -> None:
    settings = d.settings
    c.section(t("sy.groups"))
    health = data.get("groups_health", live=True)
    last = pd.to_datetime(health["last_at"], utc=True, errors="coerce")
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.silent_days)
    present = health["is_present"].fillna(1).astype(int) == 1
    silent = present & (last.isna() | (last < cutoff))
    cols = st.columns(4)
    with cols[0]:
        c.kpi(t("sy.groups.total"), tu.fmt_int(len(health)))
    with cols[1]:
        c.kpi(t("sy.groups.out"), tu.fmt_int(int((~present).sum())))
    with cols[2]:
        c.kpi(t("sy.groups.no_manager"), tu.fmt_int(int((present & health["manager_username"].isna()).sum())))
    with cols[3]:
        c.kpi(t("sy.groups.silent", days=settings.silent_days), tu.fmt_int(int(silent.sum())))
    if silent.any():
        with st.expander(t("sy.silent_list")):
            quiet = health[silent].assign(last=last[silent]).sort_values("last")
            st.dataframe(pd.DataFrame({
                t("col.group"): quiet["title"],
                t("col.last"): quiet["last"].dt.tz_convert(settings.tz).dt.tz_localize(None),
            }), hide_index=True, width="stretch", placeholder="—",
                column_config={t("col.last"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm")})


def _backups(d: PageData) -> None:
    settings = d.settings
    c.section(t("sy.backups"))
    st.caption(t("sy.db_size", db=_size(settings.db_path), wal=_size(Path(f"{settings.db_path}-wal"))))
    backups = []
    if not settings.demo and settings.backup_dir.is_dir():
        for path in settings.backup_dir.glob("monitor_*.db"):
            if _BACKUP.match(path.name) and path.is_file():
                backups.append((path.name, _size(path), datetime.fromtimestamp(path.stat().st_mtime)))
    if not backups:
        c.empty(t("sy.backups.empty"), " ")
        return
    backups.sort(key=lambda item: item[0], reverse=True)
    st.dataframe(pd.DataFrame(backups, columns=[t("col.file"), t("col.size"), t("col.modified")]),
                 hide_index=True, width="stretch",
                 column_config={t("col.modified"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm")})


def _consistency(d: PageData) -> None:
    ctx, settings = d.ctx, d.settings
    c.section(t("sy.consistency"), t("sy.consistency.note", time=settings.report_time.strftime("%H:%M")))
    days = data.get("report_dates", live=True)[:7]
    if not days:
        c.empty(t("rp.none"), " ")
        return
    stored = data.get("daily_stats_range", min(days), max(days), live=True)
    rows = []
    for day in days:
        start, end = tu.report_bounds(date.fromisoformat(day), settings.report_time, settings.tz)
        now_numbers = data.get("totals", tu.to_db(start), tu.to_db(end), None, live=end > ctx.now)
        part = stored[stored["date"] == day].set_index("category")
        if part.empty:
            continue
        pairs = {
            t("series.related"): (part.loc[m_.STAT_TOTAL, "total"] if m_.STAT_TOTAL in part.index else None,
                                  now_numbers["related"]),
            t("series.complaints"): (part.loc[m_.STAT_TOTAL, "complaints"] if m_.STAT_TOTAL in part.index else None,
                                     now_numbers["complaints"]),
            t("sent.Ijobiy"): (part.loc[m_.STAT_TOTAL, "positive"] if m_.STAT_TOTAL in part.index else None,
                               now_numbers["positive"]),
            "Filtrlangan": (part.loc[m_.STAT_FILTERED, "total"] if m_.STAT_FILTERED in part.index else None,
                            now_numbers["filtered"]),
            t("series.missed"): (part.loc[m_.STAT_MISSED, "total"] if m_.STAT_MISSED in part.index else None,
                                 now_numbers["missed"]),
        }
        row = {t("col.date"): tu.fmt_day(date.fromisoformat(day), True)}
        same = True
        for label, (old, new) in pairs.items():
            old_value = None if old is None or pd.isna(old) else int(old)
            row[f"{label} · {t('sy.c.stored')}"] = old_value
            row[f"{label} · {t('sy.c.now')}"] = int(new)
            same = same and old_value == int(new)
        row[t("rp.day")] = t("sy.consistency.ok") if same else t("sy.consistency.diff")
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", placeholder="—")


def _issues(d: PageData) -> None:
    c.section(t("sy.issues"))
    cards = []
    for issue in issues_mod.known_issues():
        span = f"{tu.fmt_day(issue.start, True) if issue.start else '…'} — " + (
            tu.fmt_day(issue.end, True) if issue.end else t("sy.issues.ongoing"))
        kind = "yellow" if issue.severity == "warning" else "brand"
        cards.append(f'<div class="db-card"><div class="db-card-title">{esc(issue.title)} {c.chip(span, kind)}</div>'
                     f'<div class="db-small">{esc(issue.text)}</div></div>')
    st.html("".join(cards) or f'<div class="db-muted">{esc(t("empty.period"))}</div>')


B, P = layout.Block, layout.Place
FRESH = B("sy.fresh", "blk.sy.fresh", _fresh, portable=True, keywords="ma'lumot yangiligi, oxirgi xabar, oxirgi hisobot")
QUEUE = B("sy.queue", "blk.sy.queue", _queue, portable=True, keywords="tasniflash navbati, ovozli xabarlar")
GROUPS = B("sy.groups", "blk.sy.groups", _groups, portable=True, keywords="guruhlar holati, jim guruhlar")
BACKUPS = B("sy.backups", "blk.sy.backups", _backups, portable=True, keywords="zaxira nusxalar, baza hajmi")
CONSISTENCY = B("sy.consistency", "blk.sy.consistency", _consistency, portable=True,
                keywords="hisobot bilan moslik, solishtirish")
ISSUES = B("sy.issues", "blk.sy.issues", _issues, portable=True, keywords="ma'lum muammolar")
PAGE = layout.register(layout.Page("system", "page.system", "system", (
    P(FRESH), P(QUEUE), P(GROUPS), P(BACKUPS), P(CONSISTENCY), P(ISSUES))))


@shell.guard
def render() -> None:
    ctx = shell.header(t("sy.title"))
    st.caption(t("sy.mode", mode=t("sy.mode.demo") if ctx.settings.demo else t("sy.mode.real")))
    layout.render_page(PAGE, PageData(ctx))
