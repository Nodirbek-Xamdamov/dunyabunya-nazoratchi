"""Guruhlar: ranking of all groups (risk, complaints, answers) and a detail view for one group. Both
views are layouts of blocks (ui.layout): "groups" and "groups.detail"."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from core.text import esc
from ui import auth, charts, data, layout, media, narrative, shell, theme
from ui import components as c
from ui.i18n import t
from views import common
from views.common import PageData

_FILTERS = ("complaints", "missed", "manager", "present")
_LEVELS = ("all", "red", "yellow", "green")
# ranking table columns that can be hidden: (column, title key)
_RANKING_COLUMNS = (("level", "gr.c.level"), ("risk", "gr.c.risk"),
                    ("complaints", "gr.c.complaints"), ("rate", "gr.c.rate"), ("missed", "gr.c.missed"),
                    ("sla", "gr.c.sla"), ("median", "gr.c.median"), ("spark", "gr.c.spark"),
                    ("related", "gr.c.related"), ("last", "gr.c.last"))


def _percent(value: float | None) -> float | None:
    return None if value is None else round(value * 100, 1)


def _selected(options: list[int]) -> int | None:
    """Group chosen in the URL (?g=) wins when it changed (drill-down links); otherwise the widget."""
    url_value = st.query_params.get(shell.QP_GROUP)
    if url_value != st.session_state.get("_gr_url"):
        st.session_state["_gr_url"] = url_value
        try:
            chosen = int(url_value) if url_value else None
        except ValueError:
            chosen = None
        st.session_state["gr_pick"] = chosen if chosen in options else None
    return st.session_state.get("gr_pick")


def _remember(choice: int | None) -> None:
    value = str(choice) if choice is not None else None
    shell.set_param(shell.QP_GROUP, value)
    st.session_state["_gr_url"] = value


# --------------------------------------------------------------------------- ranking


def _ranking(d: PageData) -> None:
    ctx, groups = d.ctx, d.groups
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        chosen = st.pills(t("gr.f.filters"), _FILTERS, selection_mode="multi", key="gr_filters",
                          format_func=lambda key: t(f"gr.f.{key}"))
    with right:
        level = st.segmented_control(t("gr.f.risk"), _LEVELS, key="gr_level", default="all",
                                     format_func=lambda key: t("cat.all") if key == "all" else t(f"gr.risk.{key}"))
    frame = groups.copy()
    chosen = set(chosen or [])
    if "complaints" in chosen:
        frame = frame[frame["complaints"] > 0]
    if "missed" in chosen:
        frame = frame[frame["missed"] > 0]
    if "manager" in chosen:
        frame = frame[frame["has_manager"]]
    if "present" in chosen:
        frame = frame[frame["is_present"]]
    if level and level != "all":
        frame = frame[frame["risk_level"] == level]
    frame = frame.sort_values(["risk", "complaints", "related"], ascending=False)
    st.caption(t("gr.count", shown=len(frame), total=len(groups)) + " · " + t("gr.click_hint"))
    if frame.empty:
        c.empty()
        return

    window = tu.day_window(ctx.last_day - pd.Timedelta(days=6).to_pytimedelta(), ctx.last_day, ctx.settings.tz)
    sparks = data.get("group_sparks", window.start_db, window.end_db, window.offset_modifier(),
                      live=window.is_open(ctx.now))
    days = [day.isoformat() for day in window.local_days()]
    spark_map = {chat: grp.set_index("day")["n"].reindex(days, fill_value=0).astype(int).tolist()
                 for chat, grp in sparks.groupby("chat_id")}
    table = pd.DataFrame({
        "chat_id": frame["chat_id"],
        "level": [f"{theme.LEVEL_SYMBOLS[lv]} {t('gr.risk.' + lv)}" for lv in frame["risk_level"]],
        "risk": frame["risk"],
        "title": frame["title"],
        "complaints": frame["complaints"],
        "rate": [_percent(m_.complaint_rate(cmp, rel)) for cmp, rel in zip(frame["complaints"], frame["related"])],
        "missed": frame["missed"],
        "sla": [_percent(m_.sla_share(ans, mis)) for ans, mis in zip(frame["answered"], frame["missed"])],
        "median": frame["median_response_s"] / 60,
        "spark": [spark_map.get(chat, [0] * len(days)) for chat in frame["chat_id"]],
        "related": frame["related"],
        "last": common.local_time_column(frame, "last_activity", ctx.settings.tz),
    })
    config = {
        "chat_id": None,
        "risk": st.column_config.ProgressColumn(t("gr.c.risk"), min_value=0, max_value=100, format="%d", width="small"),
        "level": st.column_config.TextColumn(t("gr.c.level"), width="small", pinned=True),
        "title": st.column_config.TextColumn(t("gr.c.title"), width="large", pinned=True),
        "related": st.column_config.NumberColumn(t("gr.c.related"), format="%d"),
        "complaints": st.column_config.NumberColumn(t("gr.c.complaints"), format="%d"),
        "rate": st.column_config.ProgressColumn(t("gr.c.rate"), min_value=0, max_value=100, format="%.0f%%"),
        "missed": st.column_config.NumberColumn(t("gr.c.missed"), format="%d"),
        "sla": st.column_config.ProgressColumn(t("gr.c.sla"), min_value=0, max_value=100, format="%.0f%%"),
        "median": st.column_config.NumberColumn(t("gr.c.median"), format="%.0f daq"),
        "last": st.column_config.DatetimeColumn(t("gr.c.last"), format="DD.MM.YYYY HH:mm"),
        "spark": st.column_config.LineChartColumn(t("gr.c.spark"), y_min=0),
    }
    shown = layout.shown_columns(d, "gr.ranking", [column for column in table.columns if column != "chat_id"])
    event = st.dataframe(table, column_config=config, hide_index=True, width="stretch", key="gr_table",
                         height=min(36 * len(table) + 40, 640), on_select="rerun", selection_mode="single-row",
                         placeholder="—", column_order=shown)
    export = pd.DataFrame({
        t("gr.c.level"): table["level"], t("gr.c.risk"): table["risk"], t("gr.c.title"): table["title"],
        t("gr.c.complaints"): table["complaints"],
        t("gr.c.rate") + ", %": table["rate"], t("gr.c.missed"): table["missed"], t("gr.c.sla") + ", %": table["sla"],
        t("gr.c.median") + ", daq": table["median"].round(1), t("gr.c.related"): table["related"],
        t("gr.c.last"): table["last"],
    })
    st.caption(t("gr.export_table"))
    c.download_buttons(export, c.export_name("guruhlar_reytingi", ctx.first_day, ctx.last_day), key="gr-rank-export")
    rows = event.selection.rows if event and event.selection else []
    if rows:
        choice = int(table.iloc[rows[0]]["chat_id"])
        st.session_state["gr_pick"] = choice
        _remember(choice)
        st.rerun()


# --------------------------------------------------------------------------- one group


def _info(d: PageData) -> tuple[dict, pd.Series]:
    return d.extra["info"], d.extra["row"]


def _header(d: PageData) -> None:
    info, row = _info(d)
    tz = d.settings.tz
    chips = [c.chip(t("gr.bot_in"), "green") if info["is_present"] else c.chip(t("gr.bot_out"), "red")]
    chips.append(c.chip(f"{t('gr.manager')}: {info['manager_username']}", "brand") if info["manager_username"]
                 else c.chip(f"{t('gr.manager')}: {t('gr.no_manager')}", "yellow"))
    admins = ", ".join(info["admin_names"]) or "—"
    chips.append(c.chip(f"{t('gr.admins')}: {len(info['admin_ids'])}"))
    chips.append(c.chip(f"{t('gr.c.risk')}: {row['risk']}", row["risk_level"]))
    st.html(f'<div class="db-card"><div class="db-card-title" style="font-size:1.2rem">{esc(info["title"])}</div>'
            f'<div style="margin:.3rem 0">{"".join(chips)}</div>'
            f'<div class="db-muted">{esc(t("gr.admins"))}: {esc(admins)}</div>'
            f'<div class="db-muted">{esc(t("gr.first"))}: {esc(tu.fmt_dt(tu.from_db(info["first_at"], tz), True))} · '
            f'{esc(t("gr.last"))}: {esc(tu.fmt_dt(tu.from_db(info["last_at"], tz), True))}</div></div>')


def _averages(d: PageData) -> dict:
    def compute() -> dict:
        groups = d.groups
        active = groups[groups["related"] > 0]
        medians = groups["median_response_s"].dropna()
        avg_missed = active["missed"].mean() if not active.empty else None
        return {
            "rate": m_.complaint_rate(active["complaints"].sum(), active["related"].sum()),
            "sla": m_.sla_share(groups["answered"].sum(), groups["missed"].sum()),
            "related": active["related"].mean() if not active.empty else None,
            "missed": avg_missed,
            "median": medians.median() if not medians.empty else None,
        }

    return d.memo("grd.avg", compute)


def _kpi_related(d: PageData) -> None:
    cur, prev, avg = d.cur, d.prev, _averages(d)
    c.kpi(t("kpi.related"), tu.fmt_int(cur.related), common.change_text(cur.related, prev.related if prev else None),
          neutral=True)
    st.caption(t("kpi.avg_all", value=tu.fmt_int(avg["related"]) if avg["related"] is not None else "—"))


def _kpi_complaints(d: PageData) -> None:
    cur, prev, avg = d.cur, d.prev, _averages(d)
    c.kpi(t("kpi.complaints"), t("kpi.complaints.value", n=tu.fmt_int(cur.complaints), rate=tu.fmt_pct(cur.complaint_rate)),
          common.change_text(cur.complaints, prev.complaints if prev else None), inverse=True)
    st.caption(t("kpi.avg_all", value=tu.fmt_pct(avg["rate"])))


def _kpi_sla(d: PageData) -> None:
    cur, prev, avg = d.cur, d.prev, _averages(d)
    c.kpi(t("kpi.sla"), tu.fmt_pct(cur.sla),
          common.change_text(cur.sla, prev.sla, is_rate=True) if prev and cur.sla is not None and prev.sla is not None else None)
    st.caption(t("kpi.avg_all", value=tu.fmt_pct(avg["sla"])))


def _kpi_missed(d: PageData) -> None:
    cur, prev, avg = d.cur, d.prev, _averages(d)
    c.kpi(t("kpi.missed"), tu.fmt_int(cur.missed), common.change_text(cur.missed, prev.missed if prev else None),
          inverse=True)
    st.caption(t("kpi.avg_all", value=f"{avg['missed']:.1f}".replace(".", ",") if avg["missed"] is not None else "—"))


def _kpi_median(d: PageData) -> None:
    cur, prev, avg = d.cur, d.prev, _averages(d)
    c.kpi(t("kpi.median"), tu.fmt_duration(cur.median_response_s),
          common.duration_change(cur.median_response_s, prev.median_response_s if prev else None), inverse=True)
    st.caption(t("kpi.avg_all", value=tu.fmt_duration(avg["median"])))


def _summary(d: PageData) -> None:
    c.section(t("gr.summary"), t("gr.ai_note"))
    c.summary_list(narrative.summary(d.cur, d.prev, d.cats, None))


def _dynamics(d: PageData) -> None:
    window, days = d.window, d.days
    c.section(t("gr.dynamics"), tu.fmt_period(window) if window is not d.ctx.period else None)
    if days["related"].sum() == 0:
        c.empty()
    else:
        c.plot(charts.daily_dynamics(days, height=300), key="gr-daily")


def _categories(d: PageData) -> None:
    c.section(t("ov.categories"))
    cats = d.cats
    c.empty() if cats["related"].sum() == 0 else c.plot(charts.category_bars(cats, height=300), key="gr-cats")


def _sentiment(d: PageData) -> None:
    c.section(t("ov.sentiment"))
    cur = d.cur
    c.empty() if cur.related == 0 else c.plot(charts.sentiment_donut(cur.complaints, cur.neutral, cur.positive,
                                                                     height=300), key="gr-sent")


def _sla_history(d: PageData) -> None:
    days = d.days
    c.section(t("gr.sla_history"))
    if (days["answered"] + days["missed"]).sum() == 0:
        c.empty()
    else:
        c.plot(charts.answered_missed(days, "day", [charts.day_label(day) for day in days["day"]], height=280),
               key="gr-sla")


_MISSED_COLUMNS = (("col.time", "col.time"), ("col.customer", "col.customer"), ("col.request", "col.request"),
                   ("col.later", "col.later"), ("col.after", "col.after"))


def _missed(d: PageData) -> None:
    c.section(t("gr.missed_list"))
    frame = d.period_checks
    frame = frame[frame["status"] == m_.CHECK_MISSED]
    if frame.empty:
        c.empty(t("gr.missed.empty"), " ")
        return
    table = pd.DataFrame({
        t("col.time"): common.local_time_column(frame, "started_at", d.settings.tz),
        t("col.customer"): frame["asker_name"].fillna(frame["asker_username"]),
        t("col.request"): frame["request_text"],
        t("col.later"): [(name or (f"@{user}" if user else None)) if pd.notna(at) else t("rs.never")
                         for name, user, at in zip(frame["later_name"], frame["later_username"], frame["later_at"])],
        t("col.after"): (frame["later_s"] / 60).round(0),
    })
    st.dataframe(table, hide_index=True, width="stretch", placeholder="—",
                 column_order=layout.shown_columns(d, "grd.missed", table.columns), column_config={
                     t("col.time"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                     t("col.after"): st.column_config.NumberColumn(format="%.0f daq")})


_CUSTOMER_COLUMNS = (("col.customer", "col.customer"), ("username", "col.username"),
                     ("col.related", "col.related"), ("col.complaints", "col.complaints"), ("col.last", "col.last"))


def _customers(d: PageData) -> None:
    ctx = d.ctx
    c.section(t("gr.customers"))
    customers = data.get("group_customers", d.chat_id, ctx.period.start_db, ctx.period.end_db, live=ctx.live)
    if customers.empty:
        c.empty()
        return
    table = pd.DataFrame({
        t("col.customer"): customers["name"].fillna(customers["username"]),
        t("col.username"): customers["username"].map(lambda v: f"@{v}" if isinstance(v, str) and v else None),
        t("col.related"): customers["related"],
        t("col.complaints"): customers["complaints"],
        t("col.last"): common.local_time_column(customers, "last_at", ctx.settings.tz),
    })
    st.dataframe(table, hide_index=True, width="stretch", placeholder="—",
                 column_order=layout.shown_columns(d, "grd.customers", table.columns),
                 column_config={t("col.last"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm")})


_PICTURE_LABELS = {"photo": "gr.feed.photo", "animation": "gr.feed.gif"}


def _feed(d: PageData) -> None:
    ctx, chat_id = d.ctx, d.chat_id
    c.section(t("gr.feed"))
    if not auth.require("raw", "group_feed", str(chat_id)):  # W3-05: message texts
        return
    cols = st.columns([3, 2, 2, 2], vertical_alignment="bottom")
    with cols[0]:
        search = st.text_input(t("gr.feed.search"), key="gr_feed_q", max_chars=100).strip()
    with cols[1]:
        sentiment = st.selectbox(t("gr.feed.sentiment"), [None, *m_.SENTIMENTS], key="gr_feed_s",
                                 format_func=lambda v: t("cat.all") if v is None else t(f"sent.{v}"))
    with cols[2]:
        category = st.selectbox(t("gr.feed.category"), [None, *m_.category_names()], key="gr_feed_c",
                                format_func=lambda v: t("cat.all") if v is None else v)
    with cols[3]:
        customers_only = st.toggle(t("gr.feed.customers_only"), key="gr_feed_only")
    limit = ctx.settings.feed_limit
    feed = data.get("group_feed", chat_id, ctx.period.start_db, ctx.period.end_db, limit, live=ctx.live,
                    search=search or None, sentiment=sentiment, category=category, customers_only=customers_only)
    if feed.empty:
        c.empty(t("gr.feed.empty"), " ")
        return
    if feed.attrs.get("truncated"):
        st.caption(t("gr.feed.limit", n=limit))
    tz = ctx.settings.tz
    bubbles = []
    for row in (SimpleNamespace(**record) for record in common.records(feed)):
        staff = bool(row.is_staff)
        name = row.sender_name or (f"@{row.sender_username}" if row.sender_username else "—")
        head = [esc(name), esc(tu.fmt_dt(tu.from_db(row.sent_at_utc, tz)))]
        tags = []
        if staff:
            tags.append(c.chip(t("gr.feed.staff"), "brand"))
        if row.category:
            tags.append(c.chip(row.category))
        if row.sentiment and row.is_company_related:
            tags.append(c.chip(t(f"sent.{row.sentiment}"),
                               {"Shikoyat": "red", "Ijobiy": "green"}.get(row.sentiment, "")))
        if row.needs_manager:
            tags.append(c.chip(t("col.needs"), "yellow"))
        body = row.text or ""
        picture = ""
        if row.media_type in m_.SPEECH_MEDIA:
            label = t("gr.feed.voice") if row.transcript else t("gr.feed.voice_empty")
            body = f"[{label}] {row.transcript or ''}".strip()
        elif row.media_type in _PICTURE_LABELS:
            label = t(_PICTURE_LABELS[row.media_type])
            picture = media.html(row.media_type, row.media_status, row.media_path, ctx.settings.media_dir, label)
            if not picture:  # not saved: not a company-related conversation, daily limit, too big
                body = f"[{label}] {body}".strip()
        elif row.media_type != "text":
            body = f"[{row.media_type}] {body}".strip()
        reply = (f'<div class="db-msg-reply">↩ {esc(t("gr.feed.reply", name=row.reply_name or "—", text=row.reply_text or ""))}</div>'
                 if row.reply_to_message_id and row.reply_name else "")
        summary = f'<div class="db-msg-summary">{esc(row.summary)}</div>' if row.summary and not staff else ""
        kind = "staff" if staff else f"customer {row.sentiment or ''}"
        text = f'<div class="db-msg-text">{esc(body)}</div>' if body or not picture else ""
        bubbles.append(f'<div class="db-msg {kind}"><div class="db-msg-head">{" · ".join(head)} {"".join(tags)}</div>'
                       f'{reply}{picture}{text}{summary}</div>')
    st.html(f'<div class="db-feed">{"".join(bubbles)}</div>')


def _export(d: PageData) -> None:
    ctx, chat_id = d.ctx, d.chat_id
    if not auth.has("raw"):  # the export holds message texts (W3-05)
        return
    frame = data.get("messages_list", ctx.period.start_db, ctx.period.end_db, ctx.settings.result_limit,
                     live=ctx.live, chat_ids=(chat_id,), include_staff=True)
    if frame.empty:
        return
    export = pd.DataFrame({
        t("col.time"): common.local_time_column(frame, "sent_at_utc", ctx.settings.tz),
        t("col.author"): frame["sender_name"].fillna(frame["sender_username"]),
        t("col.staff"): frame["is_staff"].map({1: t("yes"), 0: t("no")}),
        t("col.media"): frame["media_type"],
        t("col.text"): [common.message_text(row) for row in frame.to_dict("records")],
        t("col.category"): frame["category"],
        t("col.sentiment"): frame["sentiment"],
        t("col.summary"): frame["summary"],
    })
    st.caption(t("gr.export"))
    c.download_buttons(export, c.export_name(f"guruh_{abs(chat_id)}", ctx.first_day, ctx.last_day), key="gr-export")


# --------------------------------------------------------------------------- layouts

B, P = layout.Block, layout.Place
RANKING = B("gr.ranking", "blk.gr.ranking", _ranking, items=_RANKING_COLUMNS,
            hidden_items=("risk", "rate", "median", "spark", "related", "last"),
            keywords="guruhlar reytingi, jadval, xavf bali, ustunlar")
RANKING_PAGE = layout.register(layout.Page("groups", "page.groups", "groups",
                                           (P(common.ISSUES, hidden=True), P(RANKING))))

HEADER = B("grd.header", "blk.grd.header", _header, keywords="guruh kartochkasi, menejer, adminlar")
KPI_RELATED = B("grd.kpi.related", "blk.grd.kpi.related", _kpi_related, keywords="kompaniyaga oid xabarlar")
KPI_COMPLAINTS = B("grd.kpi.complaints", "blk.grd.kpi.complaints", _kpi_complaints, keywords="shikoyatlar, жалобы")
KPI_SLA = B("grd.kpi.sla", "blk.grd.kpi.sla", _kpi_sla, keywords="5 daqiqada javob, SLA")
KPI_MISSED = B("grd.kpi.missed", "blk.grd.kpi.missed", _kpi_missed, keywords="javobsiz so'rovlar")
KPI_MEDIAN = B("grd.kpi.median", "blk.grd.kpi.median", _kpi_median, keywords="median javob vaqti")
SUMMARY = B("grd.summary", "blk.grd.summary", _summary, keywords="qisqa xulosa")
DYNAMICS = B("grd.dynamics", "blk.grd.dynamics", _dynamics, keywords="dinamika grafigi, kunlik")
CATEGORIES = B("grd.categories", "blk.grd.categories", _categories, keywords="toifalar grafigi")
SENTIMENT = B("grd.sentiment", "blk.grd.sentiment", _sentiment, keywords="kayfiyat ulushi")
SLA_HISTORY = B("grd.sla_history", "blk.grd.sla_history", _sla_history, keywords="javob tarixi grafigi")
MISSED = B("grd.missed", "blk.grd.missed", _missed, items=_MISSED_COLUMNS, keywords="javobsiz so'rovlar jadvali")
CUSTOMERS = B("grd.customers", "blk.grd.customers", _customers, items=_CUSTOMER_COLUMNS,
              keywords="mijozlar jadvali")
FEED = B("grd.feed", "blk.grd.feed", _feed, keywords="suhbat lentasi, xabarlar, chat")
EXPORT = B("grd.export", "blk.grd.export", _export, keywords="yuklab olish, eksport, excel, csv")

_KPI = {"row": "grd-kpi", "slots": 4, "gap": "small"}
DETAIL_PAGE = layout.register(layout.Page("groups.detail", "page.groups.detail", "groups", (
    P(common.ISSUES, hidden=True), P(HEADER),
    P(KPI_RELATED, hidden=True, **_KPI), P(KPI_COMPLAINTS, **_KPI), P(KPI_SLA, **_KPI), P(KPI_MISSED, **_KPI),
    P(KPI_MEDIAN, **_KPI),
    P(SUMMARY), P(DYNAMICS),
    P(CATEGORIES, row="grd-cats", weight=3), P(SENTIMENT, row="grd-cats", weight=2, hidden=True),
    P(SLA_HISTORY, row="grd-sla", weight=2, hidden=True), P(MISSED, row="grd-sla", weight=3),
    P(CUSTOMERS, hidden=True), P(FEED), P(EXPORT, hidden=True),
), accepts_portable=False))


def _detail(ctx, groups: pd.DataFrame, chat_id: int) -> None:
    info = data.get("group_info", chat_id, live=True)
    match = groups[groups["chat_id"] == chat_id]
    if info is None or match.empty:
        st.warning(t("error.unknown_group"))
        return
    if st.button(t("gr.back"), icon=":material/arrow_back:"):
        st.session_state["gr_pick"] = None
        _remember(None)
        st.rerun()
    d = PageData(ctx, chat_id=chat_id, info=info, row=match.iloc[0])
    d._memo["groups"] = groups
    layout.render_page(DETAIL_PAGE, d)


@shell.guard
def render() -> None:
    ctx = shell.header(t("gr.title"))
    groups = common.group_table(ctx)
    titles = dict(zip(groups["chat_id"].astype(int), groups["title"]))
    options = [None, *sorted(titles, key=lambda chat: str(titles[chat]).lower())]
    _selected(options)
    choice = st.selectbox(t("gr.select"), options, key="gr_pick",
                          format_func=lambda chat: t("gr.all") if chat is None else str(titles.get(chat, chat)))
    if (str(choice) if choice is not None else None) != st.query_params.get(shell.QP_GROUP):
        _remember(choice)
    if choice is None:
        d = PageData(ctx)
        d._memo["groups"] = groups
        layout.render_page(RANKING_PAGE, d)
    else:
        _detail(ctx, groups, int(choice))
