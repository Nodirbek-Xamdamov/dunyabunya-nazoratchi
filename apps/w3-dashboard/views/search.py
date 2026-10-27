"""Qidiruv: text search over messages (text, voice transcript, summary) with filters. The form is
fixed; the results are a layout block whose table columns can be hidden."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from core.text import esc
from ui import auth, data, layout, nav, shell
from ui import components as c
from ui.i18n import t
from views import common
from views.common import PageData

_COLUMNS = (("col.time", "col.time"), ("col.group", "col.group"), ("col.author", "col.author"),
            ("col.staff", "col.staff"), ("col.category", "col.category"), ("col.sentiment", "col.sentiment"),
            ("col.summary", "col.summary"), ("col.text", "col.text"))


def _results(d: PageData) -> None:
    ctx, query = d.ctx, d.extra["query"]
    limit = ctx.settings.result_limit
    frame = data.get("messages_list", ctx.period.start_db, ctx.period.end_db, limit, live=ctx.live, **query)
    st.caption(t("se.count", n=tu.fmt_int(len(frame))) + (" · " + t("se.limited", limit=limit)
                                                          if frame.attrs.get("truncated") else ""))
    if frame.empty:
        c.empty()
        return
    rows = common.records(frame)
    table = pd.DataFrame({
        t("col.time"): common.local_time_column(frame, "sent_at_utc", ctx.settings.tz),
        t("col.group"): frame["title"],
        t("col.author"): frame["sender_name"].fillna(frame["sender_username"]),
        t("col.staff"): frame["is_staff"].map({1: t("yes"), 0: t("no")}),
        t("col.category"): frame["category"],
        t("col.sentiment"): frame["sentiment"].map(lambda v: t(f"sent.{v}") if isinstance(v, str) else None),
        t("col.summary"): frame["summary"],
        t("col.text"): [common.message_text(row) for row in rows],
    })
    st.caption(t("se.hint"))
    event = st.dataframe(table, hide_index=True, width="stretch", height=480, key="se_table", on_select="rerun",
                         selection_mode="single-row", placeholder="—",
                         column_order=layout.shown_columns(d, "se.results", table.columns), column_config={
                             t("col.time"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                             t("col.text"): st.column_config.TextColumn(width="large")})
    selected = event.selection.rows if event and event.selection else []
    if selected:
        row = rows[selected[0]]
        chips = [c.chip(row["category"])] if row["category"] else []
        if row["sentiment"]:
            chips.append(c.chip(t(f"sent.{row['sentiment']}"), {"Shikoyat": "red", "Ijobiy": "green"}.get(row["sentiment"], "")))
        if row["needs_manager"]:
            chips.append(c.chip(t("col.needs"), "yellow"))
        who = row["sender_name"] or (f"@{row['sender_username']}" if row["sender_username"] else "—")
        st.html(f'<div class="db-card"><div class="db-card-title">{esc(t("se.detail"))}</div>'
                f'<div class="db-muted">{esc(row["title"])} · {esc(who)} · '
                f'{esc(tu.fmt_dt(tu.from_db(row["sent_at_utc"], ctx.settings.tz), True))}</div>'
                f'<div style="margin:.3rem 0">{"".join(chips)}</div>'
                f'<div class="db-msg-text">{esc(common.message_text(row))}</div>'
                f'<div class="db-muted" style="margin-top:.3rem"><i>{esc(row["summary"] or "")}</i></div></div>')
        if nav.page("groups") is not None:
            st.page_link(nav.page("groups"), label=t("se.open_group"), icon=":material/arrow_forward:",
                         query_params=common.group_link_params(int(row["chat_id"])))
    c.download_buttons(table, c.export_name("qidiruv", ctx.first_day, ctx.last_day), key="se-export")


RESULTS = layout.Block("se.results", "blk.se.results", _results, items=_COLUMNS,
                       hidden_items=("col.staff", "col.sentiment", "col.text"),
                       keywords="qidiruv natijalari, topilgan xabarlar jadvali")
PAGE = layout.register(layout.Page("search", "page.search", "search", (layout.Place(RESULTS),)))


@shell.guard
def render() -> None:
    ctx = shell.header(t("se.title"))
    if not auth.require("raw", "page", "search"):  # W3-05: message texts
        return
    titles = data.get("group_titles", live=True)
    title_map = dict(zip(titles["chat_id"].astype(int), titles["title"]))
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        query = st.text_input(t("se.query"), placeholder=t("se.query.ph"), key="se_q", max_chars=200).strip()
    with right:
        author = st.text_input(t("se.author"), key="se_author", max_chars=100).strip()
    cols = st.columns([3, 2, 2, 1.5, 1.5], vertical_alignment="bottom")
    with cols[0]:
        chosen = st.multiselect(t("se.groups"), list(title_map), key="se_groups",
                                format_func=lambda chat: str(title_map.get(chat, chat)))
    with cols[1]:
        category = st.selectbox(t("col.category"), [None, *m_.category_names()], key="se_cat",
                                format_func=lambda v: t("cat.all") if v is None else v)
    with cols[2]:
        sentiment = st.selectbox(t("col.sentiment"), [None, *m_.SENTIMENTS], key="se_sent",
                                 format_func=lambda v: t("cat.all") if v is None else t(f"sent.{v}"))
    with cols[3]:
        needs = st.toggle(t("se.needs"), key="se_needs")
    with cols[4]:
        staff = st.toggle(t("se.staff"), key="se_staff")
    filters = {"text": query or None, "chat_ids": tuple(sorted(chosen)), "category": category, "sentiment": sentiment,
               "author": author or None, "needs_answer": needs, "include_staff": staff}
    layout.render_page(PAGE, PageData(ctx, query=filters))
