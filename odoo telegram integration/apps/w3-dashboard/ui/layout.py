"""Page layout engine.

Every page is a list of named blocks (a section, a KPI card, a table ...). What a viewer sees is the
page's default layout plus the stored *overlay* (core.state). The CEO can change everything through
the chat (user's decision, 2026-10-17): hide or show any block, table column or page, move blocks,
rename blocks and pages, add sections built from the dashboard's own read-only data views or text
notes (ui/custom.py) and create own pages. Only the chat itself and
the data stay out of reach: the chat button is not a block, and nothing here writes the monitoring
database. The AI assistant and the chat buttons only ever change this overlay -- never code.

Overlay (JSON in core.state)::

    {"pages": {"overview": {"hidden": [...], "shown": [...], "hidden_items": {"block": [...]},
                            "shown_items": {"block": [...]}, "order": [...], "added": [...],
                            "titles": {"block": "own name"}, "custom": {"c-...": spec}}},
     "hidden_pages": [...], "shown_pages": [...], "page_titles": {"overview": "own name"},
     "custom_pages": [{"id": "p-...", "title": "...", "icon": "dashboard"}]}

``hidden`` / ``hidden_items`` / ``hidden_pages`` hide things that are visible by default; ``shown`` /
``shown_items`` / ``shown_pages`` show optional things (``Place(hidden=True)``, ``Block.hidden_items``,
DEFAULT_HIDDEN_PAGES). Unknown ids in a stored overlay are ignored.
"""

from __future__ import annotations

import copy
import functools
import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from typing import Callable

import streamlit as st

from core import db, state
from core.settings import load_settings
from ui import overrides
from ui.i18n import t

log = logging.getLogger("dashboard.layout")

NAV_PAGES = ("overview", "groups", "categories", "camera", "response", "staff", "trends", "search", "reports",
             "system")
DEFAULT_HIDDEN_PAGES = ("reports", "system")  # technical pages: shown on request
POSITIONS = ("top", "bottom", "before", "after")
PAGE_ICONS = ("dashboard", "insights", "analytics", "monitoring", "bar_chart", "pie_chart", "table_chart",
              "trending_up", "star", "bolt", "flag", "favorite", "person", "groups", "chat", "report", "list",
              "inventory_2", "local_shipping", "payments", "support_agent", "campaign", "task_alt", "home")
MAX_TITLE = 80
MAX_CUSTOM_PAGES = 20
MAX_CUSTOM_BLOCKS = 30  # per page
_LIST_KEYS = ("hidden", "shown", "order", "added")
_ITEM_KEYS = ("hidden_items", "shown_items")
_ID_RE = re.compile(r"^[cp]-[0-9a-f]{10}$")


class LayoutError(ValueError):
    """A change that cannot be applied. The message (read by the AI agent) says why and lists the
    valid choices."""


@dataclass(frozen=True)
class Block:
    """``items``: (item_id, title_key) pairs of things inside the block that can be hidden one by one
    (table columns, category cards); a callable is evaluated when needed. ``hidden_items``: items that
    are hidden until someone asks for them. ``portable`` blocks depend only on the global period, so
    they can be added to other pages. ``title_text`` is the literal name of a custom block."""

    id: str
    title_key: str
    render: Callable
    keywords: str = ""
    portable: bool = False
    items: tuple | Callable = ()
    hideable: bool = True
    hidden_items: tuple = ()
    title_text: str = ""

    @property
    def title(self) -> str:
        return self.title_text or t(self.title_key)

    @property
    def custom(self) -> bool:
        return bool(_ID_RE.match(self.id))

    def item_list(self) -> list[tuple[str, str]]:
        """(item_id, title) pairs."""
        raw = self.items() if callable(self.items) else self.items
        return [(str(item_id), t(key)) for item_id, key in raw]


@dataclass(frozen=True)
class Place:
    """Where a block sits on a page. Adjacent visible blocks with the same ``row`` share one row of
    columns (``weight`` = relative width). ``slots`` keeps card widths when some cards of a row are
    hidden (the row is then drawn with that many equal columns). ``hidden``: not shown by default."""

    block: Block
    row: str | None = None
    weight: float = 1.0
    slots: int | None = None
    gap: str = "large"
    hidden: bool = False


@dataclass(frozen=True)
class Page:
    id: str
    title_key: str
    nav: str  # navigation page this layout belongs to (groups.detail -> groups)
    places: tuple[Place, ...]
    accepts_portable: bool = True
    title_text: str = ""
    icon: str = "dashboard"

    @property
    def title(self) -> str:
        return self.title_text or t(self.title_key)

    @property
    def custom(self) -> bool:
        return bool(_ID_RE.match(self.id))

    @property
    def block_ids(self) -> list[str]:
        return [place.block.id for place in self.places]

    def place(self, block_id: str) -> Place | None:
        return next((place for place in self.places if place.block.id == block_id), None)


PAGES: dict[str, Page] = {}
BLOCKS: dict[str, Block] = {}


def register(page: Page) -> Page:
    PAGES[page.id] = page
    for place in page.places:
        known = BLOCKS.get(place.block.id)
        if known is not None and known.title_key != place.block.title_key:
            raise ValueError(f"two different blocks use the id {place.block.id!r}")
        BLOCKS[place.block.id] = place.block
    return page


# --------------------------------------------------------------------------- overlay basics


def empty_overlay() -> dict:
    return {"pages": {}}


def normalize(overlay) -> dict:
    """A clean deep copy (tolerates a missing or damaged stored overlay)."""
    result = empty_overlay()
    if not isinstance(overlay, dict):
        return result
    pages = overlay.get("pages")
    if isinstance(pages, dict):
        for page_id, value in pages.items():
            if isinstance(value, dict):
                result["pages"][str(page_id)] = copy.deepcopy(value)
    for key in ("hidden_pages", "shown_pages"):
        value = overlay.get(key)
        if isinstance(value, list):
            result[key] = [str(item) for item in value]
    titles = overlay.get("page_titles")
    if isinstance(titles, dict):
        result["page_titles"] = {str(key): str(value) for key, value in titles.items() if value}
    custom_pages_ = overlay.get("custom_pages")
    if isinstance(custom_pages_, list):
        result["custom_pages"] = [dict(item) for item in custom_pages_
                                  if isinstance(item, dict) and _ID_RE.match(str(item.get("id", "")))]
    return result


def _state(overlay: dict, page_id: str) -> dict:
    value = overlay.get("pages", {}).get(page_id) if isinstance(overlay, dict) else None
    return value if isinstance(value, dict) else {}


def _list(page_state: dict, key: str) -> list[str]:
    value = page_state.get(key)
    return [str(item) for item in value] if isinstance(value, list) else []


def _items(page_state: dict, key: str) -> dict[str, list[str]]:
    value = page_state.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(name): [str(item) for item in items] for name, items in value.items() if isinstance(items, list)}


def _dict(container: dict, key: str) -> dict:
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, dict) else {}


def _without(values: list[str], *remove: str) -> list[str]:
    return [value for value in values if value not in remove]


def _with(values: list[str], *add: str) -> list[str]:
    return values + [value for value in add if value not in values]


def hidden_item_ids(page_state: dict, block_id: str) -> set[str]:
    """Items of a block that are hidden now: hidden on request, plus optional ones not shown."""
    block = BLOCKS.get(block_id)
    default = {str(item) for item in block.hidden_items} if block is not None else set()
    hidden = set(_items(page_state, "hidden_items").get(block_id, []))
    shown = set(_items(page_state, "shown_items").get(block_id, []))
    return hidden | (default - shown)


def _tidy_order(overlay: dict, page_id: str, page_state: dict) -> None:
    """A stored order without blocks that are gone, dropped when it is just the default order."""
    if not _list(page_state, "order"):
        return
    page = PAGES.get(page_id) or next((item for item in custom_pages(overlay) if item.id == page_id), None)
    if page is None:
        return
    places = _places(page, page_state)
    default = [place.block.id for place in page.places]
    default += [block_id for block_id in places if block_id not in default]
    if _order(places, page, page_state) == default:
        page_state.pop("order")
    else:
        page_state["order"] = [block_id for block_id in _list(page_state, "order") if block_id in places]


def _clean(overlay: dict) -> dict:
    own_ids = {page["id"] for page in overlay.get("custom_pages", [])}
    for page_id in list(overlay["pages"]):
        if _ID_RE.match(page_id) and page_id not in own_ids:
            del overlay["pages"][page_id]
            continue
        page_state = overlay["pages"][page_id]
        for key in _ITEM_KEYS:
            items = {name: value for name, value in _items(page_state, key).items() if value}
            if items:
                page_state[key] = items
            else:
                page_state.pop(key, None)
        for key in _LIST_KEYS:
            if not _list(page_state, key):
                page_state.pop(key, None)
        for key in ("titles", "custom"):
            if not _dict(page_state, key):
                page_state.pop(key, None)
        _tidy_order(overlay, page_id, page_state)
        if not page_state:
            del overlay["pages"][page_id]
    for key in ("hidden_pages", "shown_pages"):
        pages = [page for page in NAV_PAGES if page in overlay.get(key, [])]
        if pages:
            overlay[key] = pages
        else:
            overlay.pop(key, None)
    for key in ("page_titles", "custom_pages"):
        if not overlay.get(key):
            overlay.pop(key, None)
    return overlay


def current_overlay() -> dict:
    try:
        return normalize(state.current_layout(load_settings()))
    except (sqlite3.Error, OSError) as exc:
        log.warning("layout state unavailable (%s); default layout shown", type(exc).__name__)
        return empty_overlay()


def _nothing_left(overlay: dict) -> bool:
    """Every built-in page hidden and no own page: the change that leads here is refused."""
    hidden, shown = overlay.get("hidden_pages") or [], overlay.get("shown_pages") or []
    effective = [page for page in NAV_PAGES if page in hidden or (page in DEFAULT_HIDDEN_PAGES and page not in shown)]
    return len(effective) >= len(NAV_PAGES) and not overlay.get("custom_pages")


def hidden_nav_pages(overlay: dict) -> list[str]:
    """Built-in navigation pages that are hidden (on request, or optional and not shown). At least one
    page always stays."""
    overlay = overlay if isinstance(overlay, dict) else {}
    hidden, shown = overlay.get("hidden_pages") or [], overlay.get("shown_pages") or []
    result = [page for page in NAV_PAGES if page in hidden or (page in DEFAULT_HIDDEN_PAGES and page not in shown)]
    if len(result) >= len(NAV_PAGES) and not overlay.get("custom_pages"):
        result = [page for page in result if page != "overview"]
    return result


def is_own_page(page_id: str) -> bool:
    return bool(_ID_RE.match(str(page_id))) and str(page_id).startswith("p-")


def custom_pages(overlay: dict) -> list[Page]:
    return [Page(item["id"], "", item["id"], (), title_text=str(item.get("title") or item["id"]),
                 icon=str(item.get("icon") or "dashboard")) for item in (overlay or {}).get("custom_pages", [])]


def get_page(page_id: str, overlay: dict | None = None) -> Page:
    page = PAGES.get(page_id)
    if page is None and overlay is not None:
        page = next((item for item in custom_pages(overlay) if item.id == page_id), None)
    if page is None:
        names = [*PAGES, *(item.id for item in custom_pages(overlay or {}))]
        raise LayoutError(f"Unknown page {page_id!r}. Pages: {', '.join(names)}")
    return page


def page_title(page_id: str, overlay: dict, default: str | None = None) -> str:
    """The name a page shows: its own name if the CEO gave one."""
    own = _dict(overlay, "page_titles").get(page_id)
    if own:
        return own
    if default is not None:
        return default
    return get_page(page_id, overlay).title


# --------------------------------------------------------------------------- arranging a page


def custom_block(block_id: str, spec: dict) -> Block:
    from ui import custom  # noqa: PLC0415 -- the renderer needs streamlit and the data views

    return Block(block_id, "", functools.partial(custom.render, spec, block_id=block_id),
                 title_text=str(spec.get("title") or block_id), keywords="custom")


def _places(page: Page, page_state: dict) -> dict[str, Place]:
    places = {place.block.id: place for place in page.places}
    if page.accepts_portable:
        for block_id in _list(page_state, "added"):
            block = BLOCKS.get(block_id)
            if block is not None and block.portable and block_id not in places:
                places[block_id] = Place(block)
    for block_id, spec in _dict(page_state, "custom").items():
        if isinstance(spec, dict) and _ID_RE.match(block_id):
            places[block_id] = Place(custom_block(block_id, spec))
    return places


def _order(places: dict[str, Place], page: Page, page_state: dict) -> list[str]:
    default = [place.block.id for place in page.places]
    default += [block_id for block_id in places if block_id not in default]
    custom = list(dict.fromkeys(block_id for block_id in _list(page_state, "order") if block_id in places))
    if not custom:
        return default
    for index, block_id in enumerate(default):  # blocks missing from the stored order keep their neighbour
        if block_id in custom:
            continue
        position = 0
        for previous in reversed(default[:index]):
            if previous in custom:
                position = custom.index(previous) + 1
                break
        custom.insert(position, block_id)
    return custom


def _is_hidden(place: Place, page_state: dict) -> bool:
    if not place.block.hideable:
        return False
    block_id = place.block.id
    return block_id in _list(page_state, "hidden") or (place.hidden and block_id not in _list(page_state, "shown"))


def arranged(page_id: str, overlay: dict) -> list[tuple[Place, bool]]:
    """All places of a page in display order with their "hidden" flag."""
    page = get_page(page_id, overlay)
    page_state = _state(overlay, page_id)
    places = _places(page, page_state)
    return [(places[block_id], _is_hidden(places[block_id], page_state))
            for block_id in _order(places, page, page_state)]


def css_key(block_id: str) -> str:
    """Container key of a block: CSS can style it as ``.st-key-blk-<id with - instead of .>``."""
    return "blk-" + block_id.replace(".", "-")


def _run(block: Block, d, title: str | None) -> None:
    """One block in its own container; a data problem shows a small warning in its place instead of
    breaking the page. Streamlit's own control flow (st.rerun, st.switch_page, st.stop) is never
    caught here."""
    with st.container(key=css_key(block.id)), overrides.block_title(title):
        try:
            block.render(d)
        except db.DatabaseMissing:
            raise
        except db.QueryTimeout:
            st.warning(t("error.block_timeout", title=title or block.title))
        except (db.DataError, sqlite3.Error) as exc:
            log.warning("block %s failed: %s", block.id, type(exc).__name__)
            st.warning(t("error.block", title=title or block.title))


def render_page(page: Page, d, overlay: dict | None = None) -> None:
    overlay = overlay if overlay is not None else current_overlay()
    d.page_id = page.id
    d.page_state = _state(overlay, page.id)
    titles = _dict(d.page_state, "titles")
    visible = [place for place, hidden in arranged(page.id, overlay) if not hidden]
    d.visible_ids = {place.block.id for place in visible}
    index = 0
    while index < len(visible):
        group = [visible[index]]
        row = group[0].row
        while row and index + len(group) < len(visible) and visible[index + len(group)].row == row:
            group.append(visible[index + len(group)])
        index += len(group)
        first = group[0]
        if len(group) == 1 and not first.slots:
            _run(first.block, d, titles.get(first.block.id))
            continue
        weights = [place.weight for place in group]
        if first.slots and first.slots > len(group):
            weights = [1.0] * first.slots
        for column, place in zip(st.columns(weights, gap=first.gap), group):
            with column:
                _run(place.block, d, titles.get(place.block.id))


def shown_columns(d, block_id: str, columns) -> list:
    """Table columns minus the ones hidden for this block (item ids or their titles)."""
    columns = list(columns)
    hidden_ids = d.hidden_items(block_id)
    block = BLOCKS.get(block_id)
    if not hidden_ids or block is None:
        return columns
    titles = dict(block.item_list())
    hidden = hidden_ids | {titles[item_id] for item_id in hidden_ids if item_id in titles}
    return [column for column in columns if column not in hidden]


# --------------------------------------------------------------------------- changes (pure functions)


def _block_ids(page: Page, page_state: dict) -> str:
    return ", ".join(block_id for block_id, place in _places(page, page_state).items() if place.block.hideable)


def _set_items(page_state: dict, block: Block, item_ids: list[str], visible: bool) -> None:
    default = {str(item) for item in block.hidden_items}
    hidden, shown = _items(page_state, "hidden_items"), _items(page_state, "shown_items")
    current_hidden, current_shown = hidden.get(block.id, []), shown.get(block.id, [])
    for item in item_ids:
        if visible:
            current_hidden = _without(current_hidden, item)
            if item in default:
                current_shown = _with(current_shown, item)
        else:
            current_shown = _without(current_shown, item)
            if item not in default:
                current_hidden = _with(current_hidden, item)
    hidden[block.id], shown[block.id] = current_hidden, current_shown
    page_state["hidden_items"], page_state["shown_items"] = hidden, shown


def set_visible(overlay: dict, page_id: str, targets: list[dict], visible: bool) -> tuple[dict, str]:
    """Hide or show blocks / items. Showing a portable block that is not on the page adds it; hiding a
    custom block deletes it (undo brings it back)."""
    new = normalize(overlay)
    page = get_page(page_id, new)
    page_state = new["pages"].setdefault(page_id, {})
    if not targets:
        raise LayoutError("No blocks given.")
    parts = []
    for target in targets:
        block_id = str(target.get("block_id") or "")
        item_ids = [str(item) for item in (target.get("item_ids") or [])]
        places = _places(page, page_state)
        block = places[block_id].block if block_id in places else BLOCKS.get(block_id)
        if block is None or not block.hideable:
            raise LayoutError(f"Unknown block {block_id!r}. Blocks of page {page_id}: {_block_ids(page, page_state)}")
        on_page = block_id in places
        if item_ids:
            if not on_page:
                raise LayoutError(f"Block {block_id} is not on page {page_id}.")
            titles = dict(block.item_list())
            unknown = [item for item in item_ids if item not in titles]
            if unknown:
                raise LayoutError(f"Block {block_id} has no items {unknown}. Its items: {', '.join(titles) or 'none'}")
            _set_items(page_state, block, item_ids, visible)
            parts.append(f"{block.title} ({', '.join(titles[item] for item in item_ids)})")
            continue
        hidden, shown, added = (_list(page_state, key) for key in ("hidden", "shown", "added"))
        custom = _dict(page_state, "custom")
        default_hidden = on_page and places[block_id].hidden
        if visible:
            if on_page:
                hidden = _without(hidden, block_id)
                if default_hidden:
                    shown = _with(shown, block_id)
            elif block.portable and page.accepts_portable:
                added = _with(added, block_id)  # at the end of the page (a default order is dropped again)
                page_state["order"] = [*_without(_order(places, page, page_state), block_id), block_id]
            elif not page.accepts_portable:
                raise LayoutError(f"Page {page_id} does not accept blocks from other pages.")
            else:
                raise LayoutError(f"Block {block_id} belongs to another page and cannot be added to {page_id} "
                                  f"(it depends on that page's selection).")
        else:
            if not on_page:
                raise LayoutError(f"Block {block_id} is not on page {page_id}. Blocks: {_block_ids(page, page_state)}")
            if block_id in custom:
                custom.pop(block_id)
                page_state["custom"] = custom
                page_state["order"] = _without(_list(page_state, "order"), block_id)
            elif block_id in added and page.place(block_id) is None:
                added = _without(added, block_id)
            else:
                shown = _without(shown, block_id)
                if not default_hidden:
                    hidden = _with(hidden, block_id)
        page_state["hidden"], page_state["shown"], page_state["added"] = hidden, shown, added
        parts.append(_dict(page_state, "titles").get(block_id) or block.title)
    _clean(new)
    verb = t("layout.shown") if visible else t("layout.hidden")
    return new, f"{page_title(page.id, new, page.title)}: {verb} — {', '.join(parts)}"


def _position(order: list[str], block_id: str, position: str, anchor_id: str | None, page_id: str) -> list[str]:
    order = _without(order, block_id)
    if position == "top":
        return [block_id, *order]
    if position == "bottom":
        return [*order, block_id]
    if position not in POSITIONS:
        raise LayoutError(f"Position must be one of {', '.join(POSITIONS)}.")
    if not anchor_id or anchor_id not in order:
        raise LayoutError(f"Anchor block {anchor_id!r} is not on page {page_id}.")
    index = order.index(anchor_id) + (1 if position == "after" else 0)
    return [*order[:index], block_id, *order[index:]]


def move(overlay: dict, page_id: str, block_id: str, position: str, anchor_id: str | None = None) -> tuple[dict, str]:
    new = normalize(overlay)
    page = get_page(page_id, new)
    page_state = new["pages"].setdefault(page_id, {})
    places = _places(page, page_state)
    if block_id not in places or not places[block_id].block.hideable:
        raise LayoutError(f"Block {block_id!r} is not on page {page_id}. Blocks: {_block_ids(page, page_state)}")
    page_state["order"] = _position(_order(places, page, page_state), block_id, position, anchor_id, page_id)
    if _is_hidden(places[block_id], page_state):  # moving a hidden block means the CEO wants to see it
        page_state["hidden"] = _without(_list(page_state, "hidden"), block_id)
        if places[block_id].hidden:
            page_state["shown"] = _with(_list(page_state, "shown"), block_id)
    _clean(new)
    title = _dict(page_state, "titles").get(block_id) or places[block_id].block.title
    return new, f"{page_title(page.id, new, page.title)}: {t('layout.moved')} — {title}"


def set_page_visible(overlay: dict, nav_page: str, visible: bool) -> tuple[dict, str]:
    if nav_page not in NAV_PAGES:
        raise LayoutError(f"Unknown navigation page {nav_page!r}. Pages: {', '.join(NAV_PAGES)} "
                          f"(own pages are deleted with delete_page)")
    new = normalize(overlay)
    hidden = _without(new.get("hidden_pages", []), nav_page)
    shown = _without(new.get("shown_pages", []), nav_page)
    if visible and nav_page in DEFAULT_HIDDEN_PAGES:
        shown.append(nav_page)
    if not visible and nav_page not in DEFAULT_HIDDEN_PAGES:
        hidden.append(nav_page)
    new["hidden_pages"], new["shown_pages"] = hidden, shown
    if _nothing_left(new):
        raise LayoutError("At least one page must stay visible.")
    _clean(new)
    return new, t("layout.page_shown" if visible else "layout.page_hidden", page=page_title(nav_page, new))


def _title_text(title) -> str:
    text = " ".join(str(title or "").split())
    if len(text) > MAX_TITLE:
        raise LayoutError(f"A name can be at most {MAX_TITLE} characters.")
    return text


def rename(overlay: dict, page_id: str, block_id: str | None, title: str) -> tuple[dict, str]:
    """Own name for a block (block_id given) or for a page (block_id None). An empty title brings the
    original name back."""
    new = normalize(overlay)
    page = get_page(page_id, new)
    text = _title_text(title)
    if block_id is None:
        if page.custom:
            if not text:
                raise LayoutError("An own page needs a name.")
            old = page.title
            for item in new["custom_pages"]:
                if item["id"] == page_id:
                    item["title"] = text
        else:
            if page.id != page.nav:
                raise LayoutError(f"{page_id} has no name of its own; rename its navigation page {page.nav}.")
            titles = _dict(new, "page_titles")
            old = titles.get(page_id) or page.title
            if text:
                titles[page_id] = text
            else:
                titles.pop(page_id, None)
            new["page_titles"] = titles
        _clean(new)
        return new, t("layout.page_renamed", old=old, new=text or page.title)
    page_state = new["pages"].setdefault(page_id, {})
    places = _places(page, page_state)
    if block_id not in places:
        raise LayoutError(f"Block {block_id!r} is not on page {page_id}. Blocks: {_block_ids(page, page_state)}")
    block = places[block_id].block
    if block.custom:
        if not text:
            raise LayoutError("A custom block needs a name.")
        old = block.title
        page_state["custom"][block_id] = {**page_state["custom"][block_id], "title": text}
    else:
        titles = _dict(page_state, "titles")
        old = titles.get(block_id) or block.title
        if text:
            titles[block_id] = text
        else:
            titles.pop(block_id, None)
        page_state["titles"] = titles
    _clean(new)
    return new, f"{page_title(page.id, new, page.title)}: {t('layout.renamed')} — {old} → {text or block.title}"


def add_custom(overlay: dict, page_id: str, block_id: str, spec: dict, position: str = "bottom",
               anchor_id: str | None = None) -> tuple[dict, str]:
    """A new custom block (spec already checked by ui.custom) on a page."""
    if not _ID_RE.match(block_id) or not block_id.startswith("c-"):
        raise LayoutError("Bad custom block id.")
    new = normalize(overlay)
    page = get_page(page_id, new)
    page_state = new["pages"].setdefault(page_id, {})
    custom = _dict(page_state, "custom")
    if len(custom) >= MAX_CUSTOM_BLOCKS:
        raise LayoutError(f"A page can have at most {MAX_CUSTOM_BLOCKS} custom blocks.")
    custom[block_id] = dict(spec)
    page_state["custom"] = custom
    places = _places(page, page_state)
    page_state["order"] = _position(_order(places, page, page_state), block_id, position or "bottom", anchor_id,
                                    page_id)
    _clean(new)
    return new, f"{page_title(page.id, new, page.title)}: {t('layout.created')} — {spec.get('title', block_id)}"


def update_custom(overlay: dict, page_id: str, block_id: str, spec: dict) -> tuple[dict, str]:
    new = normalize(overlay)
    page = get_page(page_id, new)
    page_state = new["pages"].setdefault(page_id, {})
    custom = _dict(page_state, "custom")
    if block_id not in custom:
        raise LayoutError(f"{block_id!r} is not a custom block of page {page_id}. Custom blocks: "
                          f"{', '.join(custom) or 'none'}")
    custom[block_id] = dict(spec)
    page_state["custom"] = custom
    _clean(new)
    return new, f"{page_title(page.id, new, page.title)}: {t('layout.updated')} — {spec.get('title', block_id)}"


def create_page(overlay: dict, page_id: str, title: str, icon: str = "dashboard") -> tuple[dict, str]:
    if not _ID_RE.match(page_id) or not page_id.startswith("p-"):
        raise LayoutError("Bad page id.")
    text = _title_text(title)
    if not text:
        raise LayoutError("A new page needs a name.")
    if icon not in PAGE_ICONS:
        raise LayoutError(f"icon must be one of: {', '.join(PAGE_ICONS)}.")
    new = normalize(overlay)
    pages = new.setdefault("custom_pages", [])
    if len(pages) >= MAX_CUSTOM_PAGES:
        raise LayoutError(f"At most {MAX_CUSTOM_PAGES} own pages.")
    pages.append({"id": page_id, "title": text, "icon": icon})
    _clean(new)
    return new, t("layout.page_created", page=text)


def delete_page(overlay: dict, page_id: str) -> tuple[dict, str]:
    new = normalize(overlay)
    page = get_page(page_id, new)
    if not page.custom:
        raise LayoutError(f"{page_id} is a built-in page: it can be hidden (set_page_visible), not deleted.")
    new["custom_pages"] = [item for item in new.get("custom_pages", []) if item["id"] != page_id]
    new["pages"].pop(page_id, None)
    if _nothing_left(new):
        raise LayoutError("At least one page must stay visible: show a built-in page first.")
    _clean(new)
    return new, t("layout.page_deleted", page=page.title)


def reset(overlay: dict, scope: str) -> tuple[dict, str]:
    """``scope`` = "all" (everything: layout, own pages and names) or a page id (a
    navigation page also resets its detail view; an own page loses its blocks)."""
    if scope == "all":
        return empty_overlay(), t("layout.reset_all")
    new = normalize(overlay)
    page = get_page(scope, new)
    related = [page_id for page_id, item in PAGES.items() if item.nav == page.id] if page.id == page.nav else [page.id]
    for page_id in [*related, page.id]:
        new["pages"].pop(page_id, None)
    if page.id == page.nav and not page.custom:
        for key in ("hidden_pages", "shown_pages"):
            new[key] = _without(new.get(key, []), page.id)
        titles = _dict(new, "page_titles")
        titles.pop(page.id, None)
        new["page_titles"] = titles
    _clean(new)
    return new, t("layout.reset_page", page=page.title)


# --------------------------------------------------------------------------- views for the AI agent


def catalog_text() -> str:
    """Every built-in page and block (static for a code version, so it can sit in a cached prompt)."""
    lines = []
    for page in PAGES.values():
        accepts = "accepts portable blocks from other pages" if page.accepts_portable else "own blocks only"
        hidden_page = "; page hidden by default" if page.id in DEFAULT_HIDDEN_PAGES else ""
        lines.append(f"## {page.id} — {page.title} (navigation page: {page.nav}; {accepts}{hidden_page})")
        for place in page.places:
            block = place.block
            if not block.hideable:
                continue
            extra = []
            if place.hidden:
                extra.append("hidden by default")
            if block.portable:
                extra.append("portable")
            if block.keywords:
                extra.append(f"keywords: {block.keywords}")
            items = block.item_list()
            if items:
                default = {str(item) for item in block.hidden_items}
                extra.append("columns/items: " + ", ".join(
                    f"{item_id}={title}" + (" (hidden by default)" if item_id in default else "")
                    for item_id, title in items))
            lines.append(f"- {block.id} | {block.title}" + "".join(f" | {part}" for part in extra))
    return "\n".join(lines)


def page_view(page_id: str, overlay: dict) -> dict:
    """The current layout of one page (for the get_layout tool)."""
    page = get_page(page_id, overlay)
    page_state = _state(overlay, page_id)
    added = set(_list(page_state, "added"))
    titles = _dict(page_state, "titles")
    custom = _dict(page_state, "custom")
    blocks = []
    for place, hidden in arranged(page_id, overlay):
        block = place.block
        if not block.hideable:
            continue
        entry = {"id": block.id, "title": titles.get(block.id) or block.title, "visible": not hidden}
        if block.id in titles:
            entry["original_title"] = block.title
        if place.hidden:
            entry["hidden_by_default"] = True
        if block.id in custom:
            entry["custom"] = custom[block.id]
        elif block.id in added and page.place(block.id) is None:
            entry["added_from_other_page"] = True
        items = block.item_list()
        if items:
            hidden_set = hidden_item_ids(page_state, block.id)
            entry["items"] = [{"id": item_id, "title": title, "visible": item_id not in hidden_set}
                              for item_id, title in items]
        blocks.append(entry)
    present = {entry["id"] for entry in blocks}
    addable = ([{"id": block.id, "title": block.title} for block in BLOCKS.values()
                if block.portable and block.hideable and block.id not in present] if page.accepts_portable else [])
    return {"page": page.id, "title": page_title(page.id, overlay, page.title),
            "page_visible": page.nav not in hidden_nav_pages(overlay), "own_page": page.custom,
            "blocks_in_display_order": blocks, "portable_blocks_that_can_be_added": addable}


def overlay_text(overlay: dict) -> str:
    """Everything that differs from the default layout, in a few lines (for the agent's context)."""
    def named(ids) -> str:
        return ", ".join(f"{block_id} ({BLOCKS[block_id].title})" for block_id in ids if block_id in BLOCKS)

    hidden_pages = hidden_nav_pages(overlay)
    lines = ["Hidden built-in pages: " + (", ".join(hidden_pages) if hidden_pages else "none")
             + f" (hidden by default: {', '.join(DEFAULT_HIDDEN_PAGES)})"]
    own_pages = custom_pages(overlay)
    if own_pages:
        lines.append("Own pages: " + ", ".join(f"{item.id} «{item.title}» (icon {item.icon})" for item in own_pages))
    page_titles = _dict(overlay, "page_titles")
    if page_titles:
        lines.append("Renamed pages: " + ", ".join(f"{key} → «{value}»" for key, value in page_titles.items()))
    for page_id in [*PAGES, *(item.id for item in own_pages)]:
        page_state = _state(overlay, page_id)
        parts = []
        for key, label in (("hidden", "hidden on request"), ("shown", "shown on request (normally hidden)"),
                           ("added", "added from other pages")):
            ids = [block_id for block_id in _list(page_state, key) if block_id in BLOCKS]
            if ids:
                parts.append(f"{label}: {named(ids)}")
        for key, label in (("hidden_items", "hidden items"), ("shown_items", "shown items (normally hidden)")):
            for block_id, item_ids in _items(page_state, key).items():
                if item_ids and block_id in BLOCKS:
                    titles = dict(BLOCKS[block_id].item_list())
                    parts.append(f"{label} of {block_id}: " + ", ".join(f"{item} ({titles.get(item, item)})"
                                                                        for item in item_ids))
        renamed = _dict(page_state, "titles")
        if renamed:
            parts.append("renamed blocks: " + ", ".join(f"{key} → «{value}»" for key, value in renamed.items()))
        custom = _dict(page_state, "custom")
        if custom:
            parts.append("custom blocks: " + ", ".join(
                f"{key} «{spec.get('title')}» ({spec.get('kind')}"
                + (f": {spec.get('source')} as {spec.get('display')}" if spec.get("kind") == "data" else "") + ")"
                for key, spec in custom.items()))
        if _list(page_state, "order"):
            parts.append("custom order")
        if parts:
            lines.append(f"{page_id}: " + "; ".join(parts))
    if len(lines) == 1 and hidden_pages == list(DEFAULT_HIDDEN_PAGES):
        lines.append("Every page is in its default layout.")
    return "\n".join(lines)


def nav_title(page_id: str, overlay: dict) -> str:
    """Navigation label of a built-in page (its own name if the CEO gave one)."""
    return _dict(overlay, "page_titles").get(page_id) or PAGES[page_id].title


def with_title(page: Page, overlay: dict) -> Page:
    own = _dict(overlay, "page_titles").get(page.id)
    return replace(page, title_text=own) if own else page
