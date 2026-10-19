"""Bo'limlar: the eight categories -- cards with trends, then one category in depth. Both views are
layouts of blocks (ui.layout): "categories" and "categories.detail"."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from core.text import esc, top_words
from ui import charts, data, layout, shell, theme
from ui import components as c
from ui.i18n import t
from views import common
from views.common import PageData

_ALL = "all"


def _selected(names: list[str]) -> str:
    url_value = st.query_params.get(shell.QP_CATEGORY)
    if url_value != st.session_state.get("_cat_url"):
        st.session_state["_cat_url"] = url_value
        index = int(url_value) - 1 if url_value and url_value.isdigit() else -1
        st.session_state["cat_pick"] = names[index] if 0 <= index < len(names) else _ALL
    return st.session_state.get("cat_pick", _ALL)


def _remember(choice: str, names: list[str]) -> None:
    value = str(names.index(choice) + 1) if choice in names else None
    shell.set_param(shell.QP_CATEGORY, value)
    st.session_state["_cat_url"] = value


def _category_daily(d: PageData) -> pd.DataFrame:
    window = d.window
    return d.memo("cat.daily", lambda: data.get("category_daily", window.start_db, window.end_db,
                                                 window.offset_modifier(), live=window.is_open(d.ctx.now)))


# --------------------------------------------------------------------------- all categories


def _cards(d: PageData) -> None:
    names = list(m_.category_names())
    cats, prev_cats = d.cats, d.prev_cats
    daily = _category_daily(d)
    days = [day.isoformat() for day in d.window.local_days()]
    total = int(cats["related"].sum())
    hidden = d.hidden_items("cat.cards")
    shown = [name for name in names if name not in hidden]
    for row_names in (shown[start:start + 4] for start in range(0, len(shown), 4)):
        cols = st.columns(len(row_names) if len(shown) <= 4 else 4)
        for col, name in zip(cols, row_names):
            current = cats[cats["category"] == name].iloc[0]
            previous = prev_cats[prev_cats["category"] == name].iloc[0] if prev_cats is not None else None
            series = daily[daily["category"] == name].set_index("day")["related"].reindex(days, fill_value=0)
            with col:
                with st.container(border=True, key=f"catcard-{names.index(name)}"):
                    st.html(f'<div class="db-card-title"><span class="db-dot" '
                            f'style="background:{theme.category_color(name)}"></span>{esc(name)}</div>')
                    c.kpi(t("kpi.related"), tu.fmt_int(current["related"]),
                          common.change_text(int(current["related"]),
                                             int(previous["related"]) if previous is not None else None),
                          neutral=True, spark=common.spark(series.values), border=False, chart_type="bar",
                          show_label=False)
                    rate = m_.complaint_rate(current["complaints"], current["related"])
                    share = m_.ratio(current["related"], total)
                    st.caption(f"{t('cat.share', share=tu.fmt_pct(share))} · {t('cat.complaint_share', rate=tu.fmt_pct(rate))}")
                    if st.button(t("cat.open"), key=f"catopen-{names.index(name)}", icon=":material/arrow_forward:",
                                 type="tertiary"):
                        st.session_state["cat_pick"] = name
                        _remember(name, names)
                        st.rerun()


def _heat(d: PageData) -> None:
    names = list(m_.category_names())
    window = d.window
    daily = _category_daily(d)
    days = [day.isoformat() for day in window.local_days()]
    c.section(t("cat.heat"), tu.fmt_period(window))
    if daily.empty:
        c.empty()
        return
    labels = {day: charts.day_label(day) for day in days}
    frame = daily.assign(day=daily["day"].map(labels))
    c.plot(charts.heat_matrix(frame, "category", "day", "related", names, [labels[day] for day in days],
                              theme.HEAT_SCALE, 340, t("series.related")), key="cat-heat")


# --------------------------------------------------------------------------- one category


def _rows(d: PageData) -> tuple[pd.Series, pd.Series | None, int]:
    cats, prev_cats, name = d.cats, d.prev_cats, d.category
    current = cats[cats["category"] == name].iloc[0]
    previous = prev_cats[prev_cats["category"] == name].iloc[0] if prev_cats is not None else None
    return current, previous, int(cats["related"].sum())


def _department(d: PageData) -> None:
    st.html(f'<div>{c.chip(t("cat.department", department=m_.category_department(d.category)), "brand")}</div>')
    st.caption(t("cat.department.note"))


def _kpi_related(d: PageData) -> None:
    current, previous, _total = _rows(d)
    c.kpi(t("kpi.related"), tu.fmt_int(current["related"]),
          common.change_text(int(current["related"]), int(previous["related"]) if previous is not None else None),
          neutral=True)


def _kpi_complaints(d: PageData) -> None:
    current, previous, _total = _rows(d)
    rate = m_.complaint_rate(current["complaints"], current["related"])
    c.kpi(t("kpi.complaints"), t("kpi.complaints.value", n=tu.fmt_int(current["complaints"]), rate=tu.fmt_pct(rate)),
          common.change_text(int(current["complaints"]), int(previous["complaints"]) if previous is not None else None),
          inverse=True)


def _kpi_positive(d: PageData) -> None:
    current, previous, _total = _rows(d)
    c.kpi(t("kpi.positive"), tu.fmt_int(current["positive"]),
          common.change_text(int(current["positive"]), int(previous["positive"]) if previous is not None else None))


def _kpi_share(d: PageData) -> None:
    current, _previous, total = _rows(d)
    c.kpi(t("kpi.share"), tu.fmt_pct(m_.ratio(current["related"], total)))


def _series(d: PageData) -> pd.DataFrame:
    def compute() -> pd.DataFrame:
        daily = _category_daily(d)
        days = pd.DataFrame({"day": [day.isoformat() for day in d.window.local_days()]})
        series = days.merge(daily[daily["category"] == d.category], on="day", how="left").fillna(0)
        for column in ("related", "complaints", "positive"):
            series[column] = series[column].astype(int)
        series["neutral"] = (series["related"] - series["complaints"] - series["positive"]).clip(lower=0)
        return series

    return d.memo("catd.series", compute)


def _dynamics(d: PageData) -> None:
    window, series = d.window, _series(d)
    c.section(t("gr.dynamics"), tu.fmt_period(window) if window is not d.ctx.period else None)
    c.empty() if series["related"].sum() == 0 else c.plot(charts.daily_dynamics(series, 300, with_missed=False),
                                                          key="cat-daily")


def _sentiment(d: PageData) -> None:
    current, _previous, _total = _rows(d)
    c.section(t("ov.sentiment"))
    neutral = int(current["related"] - current["complaints"] - current["positive"])
    c.empty() if not current["related"] else c.plot(
        charts.sentiment_donut(int(current["complaints"]), max(neutral, 0), int(current["positive"]), 300),
        key="cat-sent")


def _top_groups(d: PageData) -> None:
    ctx, name = d.ctx, d.category
    c.section(t("cat.top_groups"))
    top = data.get("category_top_groups", ctx.period.start_db, ctx.period.end_db, name, 10, live=ctx.live)
    if top.empty:
        c.empty()
    else:
        c.plot(charts.hbar(top["title"].tolist(), top["related"].tolist(), theme.category_color(name),
                           max(220, 34 * len(top) + 40), t("series.related"), top["complaints"].tolist(),
                           t("series.complaints")), key="cat-top-groups")


def _topics(d: PageData) -> None:
    ctx = d.ctx
    c.section(t("cat.topics"), t("cat.topics.note"))
    words = top_words(data.get("category_summaries", ctx.period.start_db, ctx.period.end_db, d.category,
                               live=ctx.live), 15)
    if not words:
        c.empty()
    else:
        c.plot(charts.hbar([word for word, _n in words], [n for _word, n in words], theme.BRAND,
                           max(220, 26 * len(words) + 40), t("axis.count")), key="cat-words")


_CUSTOMER_COLUMNS = (("col.customer", "col.customer"), ("username", "col.username"), ("col.groups", "col.groups"),
                     ("col.related", "col.related"), ("col.complaints", "col.complaints"))


def _top_customers(d: PageData) -> None:
    ctx = d.ctx
    c.section(t("cat.top_customers"))
    customers = data.get("category_top_customers", ctx.period.start_db, ctx.period.end_db, d.category, 10,
                         live=ctx.live)
    if customers.empty:
        c.empty()
        return
    table = pd.DataFrame({
        t("col.customer"): customers["name"].fillna(customers["username"]),
        t("col.username"): customers["username"].map(lambda v: f"@{v}" if isinstance(v, str) and v else None),
        t("col.groups"): customers["groups"], t("col.related"): customers["related"],
        t("col.complaints"): customers["complaints"],
    })
    st.dataframe(table, hide_index=True, width="stretch", placeholder="—",
                 column_order=layout.shown_columns(d, "catd.top_customers", table.columns))


def _ai(d: PageData) -> None:
    ctx = d.ctx
    c.section(t("cat.ai"))
    first, last = common.report_days(ctx)
    stored = data.get("stored_summaries", first, last, d.category, live=ctx.live)
    if stored.empty:
        st.caption(t("ov.summary.ai_none"))
    for index, row in enumerate(stored.head(10).itertuples()):
        day = pd.to_datetime(row.date).date()
        with st.expander(t("ov.summary.ai_window", window=common.report_window_text(day, ctx)), expanded=index == 0):
            st.html(f'<div class="db-small">{esc(row.summary_text)}</div>')


_MESSAGE_COLUMNS = (("col.time", "col.time"), ("col.group", "col.group"), ("col.author", "col.author"),
                    ("col.sentiment", "col.sentiment"), ("col.summary", "col.summary"), ("col.text", "col.text"))


def _messages(d: PageData) -> None:
    ctx, name = d.ctx, d.category
    c.section(t("cat.messages"))
    frame = data.get("messages_list", ctx.period.start_db, ctx.period.end_db, ctx.settings.result_limit,
                     live=ctx.live, category=name)
    if frame.empty:
        c.empty()
        return
    table = pd.DataFrame({
        t("col.time"): common.local_time_column(frame, "sent_at_utc", ctx.settings.tz),
        t("col.group"): frame["title"],
        t("col.author"): frame["sender_name"].fillna(frame["sender_username"]),
        t("col.sentiment"): frame["sentiment"].map(lambda v: t(f"sent.{v}") if isinstance(v, str) else None),
        t("col.summary"): frame["summary"],
        t("col.text"): [common.message_text(row) for row in frame.to_dict("records")],
    })
    if frame.attrs.get("truncated"):
        st.caption(t("se.limited", limit=ctx.settings.result_limit))
    st.dataframe(table, hide_index=True, width="stretch", height=420, placeholder="—",
                 column_order=layout.shown_columns(d, "catd.messages", table.columns),
                 column_config={t("col.time"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                                t("col.text"): st.column_config.TextColumn(width="large")})
    c.download_buttons(table, c.export_name(f"toifa_{m_.category_names().index(name) + 1}", ctx.first_day,
                                            ctx.last_day), key="cat-export")


# --------------------------------------------------------------------------- layouts

B, P = layout.Block, layout.Place


def _card_items() -> tuple:
    return tuple((name, name) for name in m_.category_names())


CARDS = B("cat.cards", "blk.cat.cards", _cards, items=_card_items, keywords="toifa kartalari, bo'limlar kartalari")
HEAT = B("cat.heat", "blk.cat.heat", _heat, keywords="toifa va kun issiqlik xaritasi")
CARDS_PAGE = layout.register(layout.Page("categories", "page.categories", "categories",
                                         (P(common.ISSUES, hidden=True), P(CARDS), P(HEAT, hidden=True))))

DEPARTMENT = B("catd.department", "blk.catd.department", _department, keywords="mas'ul bo'lim")
KPI_RELATED = B("catd.kpi.related", "blk.catd.kpi.related", _kpi_related, keywords="kompaniyaga oid xabarlar")
KPI_COMPLAINTS = B("catd.kpi.complaints", "blk.catd.kpi.complaints", _kpi_complaints, keywords="shikoyatlar, жалобы")
KPI_POSITIVE = B("catd.kpi.positive", "blk.catd.kpi.positive", _kpi_positive, keywords="ijobiy fikrlar")
KPI_SHARE = B("catd.kpi.share", "blk.catd.kpi.share", _kpi_share, keywords="ulushi, foiz")
DYNAMICS = B("catd.dynamics", "blk.catd.dynamics", _dynamics, keywords="dinamika grafigi")
SENTIMENT = B("catd.sentiment", "blk.catd.sentiment", _sentiment, keywords="kayfiyat ulushi")
TOP_GROUPS = B("catd.top_groups", "blk.catd.top_groups", _top_groups, keywords="qaysi guruhlarda ko'p")
TOPICS = B("catd.topics", "blk.catd.topics", _topics, keywords="takrorlanayotgan mavzular, so'zlar")
TOP_CUSTOMERS = B("catd.top_customers", "blk.catd.top_customers", _top_customers, items=_CUSTOMER_COLUMNS,
                  keywords="eng faol mijozlar")
AI = B("catd.ai", "blk.catd.ai", _ai, keywords="AI xulosalar, saqlangan xulosa")
MESSAGES = B("catd.messages", "blk.catd.messages", _messages, items=_MESSAGE_COLUMNS, hidden_items=("col.text",),
             keywords="xabarlar jadvali, yuklab olish")

_KPI = {"row": "catd-kpi", "slots": 3, "gap": "small"}
DETAIL_PAGE = layout.register(layout.Page("categories.detail", "page.categories.detail", "categories", (
    P(common.ISSUES, hidden=True), P(DEPARTMENT),
    P(KPI_RELATED, **_KPI), P(KPI_COMPLAINTS, **_KPI), P(KPI_POSITIVE, **_KPI), P(KPI_SHARE, hidden=True, **_KPI),
    P(DYNAMICS, row="catd-dyn", weight=3), P(SENTIMENT, row="catd-dyn", weight=2, hidden=True),
    P(TOP_GROUPS, row="catd-top"), P(TOPICS, row="catd-top", hidden=True),
    P(TOP_CUSTOMERS, hidden=True), P(AI), P(MESSAGES),
), accepts_portable=False))


@shell.guard
def render() -> None:
    ctx = shell.header(t("cat.title"))
    names = list(m_.category_names())
    d = PageData(ctx)
    counts = dict(zip(d.cats["category"], d.cats["related"]))
    _selected(names)
    st.session_state.setdefault("cat_pick", _ALL)
    choice = st.segmented_control(
        t("gr.feed.category"), [_ALL, *names], key="cat_pick", required=True,
        format_func=lambda v: f"{t('cat.all')} · {tu.fmt_int(sum(counts.values()))}" if v == _ALL
        else f"{v} · {tu.fmt_int(counts.get(v, 0))}")
    choice = choice or _ALL
    if choice != st.session_state.get("_cat_last"):
        st.session_state["_cat_last"] = choice
        _remember(choice, names)
    if choice == _ALL:
        layout.render_page(CARDS_PAGE, d)
    else:
        d.category = choice
        layout.render_page(DETAIL_PAGE, d)
