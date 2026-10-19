"""The view agent ("Ko'rinish agenti"): changes what the dashboard shows by editing the layout overlay
(ui.layout) through core.state. It can hide, show, move and rename any section, table column or page,
add new sections built from the dashboard's own read-only data views (ui.custom) or text notes, and
create or delete own pages (user's decision, 2026-10-17: the CEO may change everything except deleting
the chat or the data). It never writes SQL or code. All changes made while answering one CEO message
share that message's change group, so one «Qaytarish» undoes all of them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from assistant import data_tools, llm, prompts
from assistant.llm import Tool, ToolError, nullable, obj
from core import state
from core import timeutil as tu
from core.settings import Settings
from ui import custom, layout
from ui.i18n import t
from views import agents, categories, groups, overview, reports, response, search, staff, system, trends  # noqa: F401  (register the layouts)

RECENT_CHANGES = 10
VIEW_STEPS = 10  # building a new page can take several rounds of calls


@dataclass
class ViewResult:
    text: str
    changes: list[str]
    usage: llm.Usage


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _page_id(args: dict, key: str = "page") -> str:
    page_id = str(args.get(key) or "")
    layout.get_page(page_id, layout.current_overlay())  # LayoutError lists the valid pages
    return page_id


def _selected(context: dict, settings: Settings) -> tu.Period:
    period = context.get("period")
    if isinstance(period, tu.Period):
        return period
    return tu.resolve_period(tu.DEFAULT_PRESET, tu.now_local(settings.tz), settings.report_time)


def _raw_view(args: dict) -> dict:
    return {key: args.get(key) for key in ("title", "note", "source", "params", "display", "x", "y", "columns",
                                           "metrics")}


def _merged(old: dict, args: dict) -> dict:
    """An update: null keeps a value; params are merged key by key ("" removes one). A new source starts
    with no params unless some are given."""
    raw = {**old}
    for key in ("title", "note", "text", "style", "source", "display", "x", "y", "columns", "metrics"):
        if args.get(key) is not None:
            raw[key] = args[key]
    params = {} if args.get("source") not in (None, old.get("source")) else dict(old.get("params") or {})
    for key, value in (args.get("params") or {}).items():
        if value == "":
            params.pop(key, None)
        elif value is not None:
            params[key] = value
    if old.get("kind") == "data":
        raw["params"] = params
    return raw


def _tools(settings: Settings, context: dict, changes: list[str]) -> list[Tool]:
    group_id = context["group_id"]
    selected = _selected(context, settings)

    def apply(change, source: str = "chat", **extra) -> dict:
        summary = state.update_layout(settings, change, group_id=group_id, source=source)
        if summary is None:
            return {"result": "no change -- it was already like that"}
        changes.append(summary)
        return {"result": "done", "summary": summary, **extra}

    def get_layout(args: dict) -> dict:
        return layout.page_view(_page_id(args), layout.current_overlay())

    def set_visible(args: dict) -> dict:
        page_id, visible = _page_id(args), bool(args.get("visible"))
        targets = [{"block_id": item.get("block_id"), "item_ids": item.get("item_ids") or []}
                   for item in args.get("targets") or [] if isinstance(item, dict)]
        return apply(lambda overlay: layout.set_visible(overlay, page_id, targets, visible))

    def move_block(args: dict) -> dict:
        page_id = _page_id(args)
        return apply(lambda overlay: layout.move(overlay, page_id, str(args.get("block_id") or ""),
                                                 str(args.get("position") or ""), args.get("anchor_block_id")))

    def set_page_visible(args: dict) -> dict:
        return apply(lambda overlay: layout.set_page_visible(overlay, str(args.get("page") or ""),
                                                             bool(args.get("visible"))))

    def rename(args: dict) -> dict:
        page_id = _page_id(args)
        block_id = args.get("block_id") or None
        return apply(lambda overlay: layout.rename(overlay, page_id, block_id, str(args.get("title") or "")))

    def preview_data_view(args: dict) -> dict:
        return custom.preview(str(args.get("source") or ""), args.get("params") or {}, settings, selected)

    def place(args: dict, spec: dict, data: dict) -> dict:
        page_id, block_id = _page_id(args), new_id("c")
        position, anchor = args.get("position") or "bottom", args.get("anchor_block_id")
        now = {key: data[key] for key in ("rows_now", "metrics") if key in data}
        return apply(lambda overlay: layout.add_custom(overlay, page_id, block_id, spec, position, anchor),
                     block_id=block_id, **({"data_now": now} if now else {}))

    def add_data_view(args: dict) -> dict:
        spec, data = custom.check({"kind": "data", **_raw_view(args)}, settings, selected)
        return place(args, spec, data)

    def add_note(args: dict) -> dict:
        spec, _data = custom.check({"kind": "note", **{key: args.get(key) for key in ("title", "text", "style")}},
                                   settings, selected)
        return place(args, spec, {})

    def update_custom_block(args: dict) -> dict:
        page_id = _page_id(args)
        block_id = str(args.get("block_id") or "")
        page_state = layout.current_overlay().get("pages", {}).get(page_id, {})
        old = (page_state.get("custom") or {}).get(block_id)
        if not isinstance(old, dict):
            raise ToolError(f"{block_id!r} is not a section made in the chat on page {page_id} (only those can be "
                            f"changed with this tool; built-in sections are renamed, moved or hidden).")
        spec, _data = custom.check(_merged(old, args), settings, selected)
        return apply(lambda overlay: layout.update_custom(overlay, page_id, block_id, spec))

    def create_page(args: dict) -> dict:
        page_id = new_id("p")
        return apply(lambda overlay: layout.create_page(overlay, page_id, str(args.get("title") or ""),
                                                        str(args.get("icon") or "dashboard")), page_id=page_id)

    def delete_page(args: dict) -> dict:
        page_id = _page_id(args)
        return apply(lambda overlay: layout.delete_page(overlay, page_id))

    def undo(args: dict) -> dict:
        try:
            steps = max(1, min(10, int(args.get("steps") or 1)))
        except (TypeError, ValueError):
            steps = 1
        summary = state.undo(settings, group_id=group_id, steps=steps)
        if summary is None:
            return {"result": "nothing to undo"}
        changes.append(t("layout.undone", summary=summary))
        return {"result": "done", "reverted": summary}

    def reset_layout(args: dict) -> dict:
        scope = str(args.get("scope") or "")
        return apply(lambda overlay: layout.reset(overlay, scope), source="reset")

    page = {"type": "string", "description": "Page id: a built-in page from the catalog (overview, groups.detail ...) "
                                             "or an own page (p-...) from the context"}
    position = {"position": {"type": "string", "enum": list(layout.POSITIONS)},
                "anchor_block_id": nullable({"type": "string"}, "Required for before/after")}
    target = obj({"block_id": {"type": "string"},
                  "item_ids": nullable({"type": "array", "items": {"type": "string"}},
                                       "Only to hide/show columns or cards inside the block; null for the whole block")})
    strings = {"type": "array", "items": {"type": "string"}}
    params = obj({
        "period": nullable({"type": "string", "enum": list(data_tools.PERIODS)},
                           "selected (or null) = always the period chosen in the sidebar -- best for most sections; "
                           "a fixed preset otherwise; custom = date_from..date_to"),
        "date_from": nullable({"type": "string"}, "YYYY-MM-DD"), "date_to": nullable({"type": "string"}, "YYYY-MM-DD"),
        "group": nullable({"type": "string"}, "One group's title (or a unique part of it); null = all groups"),
        "category": nullable({"type": "string"}, "One category; null = all"),
        "sort_by": nullable({"type": "string"}, "groups_ranking only: risk, complaints, missed, messages, "
                                                "answer_rate_worst"),
        "limit": nullable({"type": "integer"}, "Rows (1-30, default 10)"),
        "text": nullable({"type": "string"}, "search_messages only: the word or phrase"),
        "sentiment": nullable({"type": "string"}, "search_messages only: Shikoyat, Ijobiy or Neytral"),
    })
    view_fields = {
        "title": {"type": "string", "description": "Section heading shown on the page (in the CEO's language)"},
        "note": nullable({"type": "string"}, "Short caption under the heading"),
        "source": {"type": "string", "enum": list(custom.SOURCES)},
        "params": params,
        "display": {"type": "string", "enum": list(custom.DISPLAYS)},
        "x": nullable({"type": "string"}, "Charts: the label column"),
        "y": nullable(strings, "Charts: 1-4 number columns"),
        "columns": nullable(strings, "table / list: columns in order (null = all; the first one is a list card's "
                                     "heading)"),
        "metrics": nullable(strings, "kpi (and the overview table): the values to show, 1-6"),
    }
    update_fields = {
        "title": nullable({"type": "string"}), "note": nullable({"type": "string"}),
        "source": nullable({"type": "string", "enum": list(custom.SOURCES)}),
        "params": nullable(params, "Only the params to change: null keeps one, \"\" removes it"),
        "display": nullable({"type": "string", "enum": list(custom.DISPLAYS)}),
        "x": nullable({"type": "string"}), "y": nullable(strings), "columns": nullable(strings),
        "metrics": nullable(strings), "text": nullable({"type": "string"}, "Notes: the new Markdown text"),
        "style": nullable({"type": "string", "enum": list(custom.NOTE_STYLES)}, "Notes only"),
    }
    return [
        Tool("get_layout", "The current layout of one page: its blocks in display order with visibility, own names, "
                           "the sections made in the chat (with their settings), items (columns, cards) and the "
                           "portable blocks of other pages that can be added to it.",
             obj({"page": page}), get_layout),
        Tool("set_visible", "Show (visible=true) or hide (visible=false) blocks or columns/cards (item_ids) of a page. "
                            "Showing a portable block that is not on the page adds it there. Hiding a section made in "
                            "the chat deletes it (undo brings it back). Hidden blocks can always be shown again.",
             obj({"page": page, "targets": {"type": "array", "items": target}, "visible": {"type": "boolean"}}),
             set_visible),
        Tool("move_block", "Move a block to the top or bottom of its page, or before/after another block.",
             obj({"page": page, "block_id": {"type": "string"}, **position}), move_block),
        Tool("set_page_visible", "Show or hide a built-in page in the navigation (at least one page stays). Own "
                                 "pages are removed with delete_page.",
             obj({"page": {"type": "string", "enum": list(layout.NAV_PAGES)}, "visible": {"type": "boolean"}}),
             set_page_visible),
        Tool("rename", "Give a block (block_id) or a page (block_id null) its own name; an empty title brings the "
                       "original name back.",
             obj({"page": page, "block_id": nullable({"type": "string"}, "null = rename the page itself"),
                  "title": {"type": "string"}}), rename),
        Tool("preview_data_view", "Run a data source once and see what it returns now: its columns (with kinds), "
                                  "number columns, KPI values and the first rows. Use it before add_data_view when "
                                  "unsure which columns or metrics to pick.",
             obj({"source": {"type": "string", "enum": list(custom.SOURCES)}, "params": params}), preview_data_view),
        Tool("add_data_view", "Add a new section built from one of the dashboard's data sources, shown as KPI cards, "
                              "a table, a list of cards or a chart (bar, stacked_bar, hbar, line, area, pie). The "
                              "call fails with the valid choices when a column, metric or display does not fit.",
             obj({"page": page, **view_fields, **position}), add_data_view),
        Tool("add_note", "Add a text section (Markdown): a note, a reminder, instructions, a list of links.",
             obj({"page": page, "title": {"type": "string"}, "text": {"type": "string"},
                  "style": {"type": "string", "enum": list(custom.NOTE_STYLES)}, **position}), add_note),
        Tool("update_custom_block", "Change a section made in the chat (data view or note): only the fields given are "
                                    "changed, the rest stays.",
             obj({"page": page, "block_id": {"type": "string"}, **update_fields}), update_custom_block),
        Tool("create_page", "Create an own page in the navigation (section «Mening sahifalarim»). It starts empty: "
                            "fill it in the same turn with add_data_view / add_note / set_visible (portable blocks). "
                            "Returns its page_id.",
             obj({"title": {"type": "string"}, "icon": {"type": "string", "enum": list(layout.PAGE_ICONS)}}),
             create_page),
        Tool("delete_page", "Delete an own page (p-...) with its sections. Built-in pages are hidden instead.",
             obj({"page": page}), delete_page),
        Tool("undo", "Undo the most recent changes (steps = how many earlier CEO requests to undo).",
             obj({"steps": nullable({"type": "integer"}, "Default 1")}), undo),
        Tool("reset_layout", "Put one page (with its detail view) back to its default layout, or everything (scope "
                             "all: every page, own pages, names and the design).",
             obj({"scope": {"type": "string", "description": "all, or a page id"}}), reset_layout),
    ]


def describe_tools(settings: Settings) -> list[Tool]:
    """The tools with a placeholder context, for the «AI agentlar» page (names and descriptions only)."""
    return _tools(settings, {"group_id": "-"}, [])


def run(client, settings: Settings, instruction: str, context: dict, pictures=()) -> ViewResult:
    changes: list[str] = []
    overlay = layout.current_overlay()
    recent = state.history(settings, RECENT_CHANGES)
    result = llm.run_agent(client, settings, name="view", system=prompts.system("view", settings),
                           messages=[llm.user_message(instruction, pictures)],
                           context=prompts.view_context(context, overlay, recent, settings.tz),
                           tools=_tools(settings, context, changes), max_steps=max(settings.chat.max_steps, VIEW_STEPS))
    return ViewResult(result.text, changes, result.usage)
