"""Layout overlay (ui.layout) and the dashboard's own state database (core.state): everything can be
hidden and shown again (blocks, columns, pages), moving, renaming, sections made in the chat, own
pages, the design, reset, undo and restore, chat history, and the block registry."""

from __future__ import annotations

import pytest

from core import state
from core.settings import load_settings
from ui import custom, layout
from ui.i18n import UZ
from views import categories, groups, overview, reports, response, search, staff, system, trends  # noqa: F401

E = layout.empty_overlay
NOTE = {"kind": "note", "title": "Eslatma", "text": "Har dushanba **9:00** da yig'ilish", "style": "info"}
PIE = {"kind": "data", "title": "Shikoyatlar toifalar bo'yicha", "note": "", "source": "categories", "params": {},
       "display": "pie", "x": "category", "y": ["complaints"]}


def _visible(page_id: str, overlay: dict) -> list[str]:
    return [place.block.id for place, hidden in layout.arranged(page_id, overlay) if not hidden]


def _order(page_id: str, overlay: dict) -> list[str]:
    return [place.block.id for place, _hidden in layout.arranged(page_id, overlay)]


def _show(*blocks: str, page: str = "overview"):
    return lambda overlay: layout.set_visible(overlay, page, [{"block_id": block} for block in blocks], True)


def _hide(*blocks: str, page: str = "overview"):
    return lambda overlay: layout.set_visible(overlay, page, [{"block_id": block} for block in blocks], False)


# --------------------------------------------------------------------------- registry


def test_registry_ids_titles_and_pages():
    assert set(layout.NAV_PAGES) <= set(layout.PAGES)
    assert {"groups.detail", "categories.detail"} <= set(layout.PAGES)
    for page in layout.PAGES.values():
        assert page.title_key in UZ, page.id
        ids = [place.block.id for place in page.places]
        assert len(ids) == len(set(ids)), page.id
        assert page.nav in layout.NAV_PAGES and not page.custom
    for block in layout.BLOCKS.values():
        assert block.title_key in UZ and not block.custom, block.id
        titles = dict(block.item_list())
        for item_id, title in titles.items():
            assert item_id and title, block.id
        assert set(map(str, block.hidden_items)) <= set(titles), block.id
    assert len(layout.BLOCKS) >= 75


def test_catalog_lists_every_hideable_block():
    text = layout.catalog_text()
    for block in layout.BLOCKS.values():
        assert (f"- {block.id} |" in text) == block.hideable
    assert "hidden by default" in text and "portable" in text and "MAIN" not in text
    names = layout.block_names_text()
    assert "ov.latest (" in names and names.count("\n") == len(layout.PAGES) - 1


# --------------------------------------------------------------------------- hiding and showing anything


def test_any_block_can_be_hidden_and_shown_again():
    overlay, summary = _hide("ov.kpi.complaints", "ov.latest")(E())
    assert overlay == {"pages": {"overview": {"hidden": ["ov.kpi.complaints", "ov.latest"]}}}
    assert "olib tashlandi" in summary and "ov.latest" not in _visible("overview", overlay)
    overlay, summary = _show("ov.latest", "ov.kpi.complaints")(overlay)
    assert overlay == E() and "qo'shildi" in summary
    overlay, _ = _show("ov.hours", "ov.sentiment")(E())  # optional blocks
    assert overlay["pages"]["overview"]["shown"] == ["ov.hours", "ov.sentiment"]
    assert {"ov.hours", "ov.sentiment"} <= set(_visible("overview", overlay))
    assert _hide("ov.hours", "ov.sentiment")(overlay)[0] == E()
    assert _hide("ov.hours")(E())[0] == E() and _show("ov.daily")(E())[0] == E()  # already like that


def test_any_column_or_card_can_be_hidden_and_shown_again():
    overlay, summary = layout.set_visible(E(), "groups", [{"block_id": "gr.ranking", "item_ids": ["customer", "missed"]}],
                                          True)
    page_state = overlay["pages"]["groups"]
    assert page_state["shown_items"]["gr.ranking"] == ["customer"] and "Mijoz xabarlari" in summary
    overlay, _ = layout.set_visible(overlay, "groups", [{"block_id": "gr.ranking", "item_ids": ["customer", "missed"]}],
                                    False)
    page_state = overlay["pages"]["groups"]
    assert page_state == {"hidden_items": {"gr.ranking": ["missed"]}}
    assert {"missed", "customer", "spark"} <= layout.hidden_item_ids(page_state, "gr.ranking")
    assert layout.set_visible(overlay, "groups", [{"block_id": "gr.ranking", "item_ids": ["missed"]}], True)[0] == E()
    first = categories.m_.category_names()[0]
    overlay, _ = layout.set_visible(E(), "categories", [{"block_id": "cat.cards", "item_ids": [first]}], False)
    assert first in layout.hidden_item_ids(overlay["pages"]["categories"], "cat.cards")
    with pytest.raises(layout.LayoutError, match="no items"):
        layout.set_visible(E(), "groups", [{"block_id": "gr.ranking", "item_ids": ["nope"]}], True)


def test_any_page_can_be_hidden_but_one_stays():
    assert layout.hidden_nav_pages(E()) == list(layout.DEFAULT_HIDDEN_PAGES)
    overlay, summary = layout.set_page_visible(E(), "system", True)
    assert layout.hidden_nav_pages(overlay) == ["reports"] and "sahifasi qo'shildi" in summary
    assert layout.set_page_visible(overlay, "system", False)[0] == E()
    overlay, summary = layout.set_page_visible(E(), "trends", False)  # a main page
    assert overlay == {"pages": {}, "hidden_pages": ["trends"]} and "sahifasi olib tashlandi" in summary
    for page in layout.NAV_PAGES[:-3]:
        overlay, _ = layout.set_page_visible(overlay, page, False)
    with pytest.raises(layout.LayoutError, match="At least one page"):
        layout.set_page_visible(overlay, layout.NAV_PAGES[-3], False)
    assert layout.hidden_nav_pages({"hidden_pages": list(layout.NAV_PAGES)}) == [
        page for page in layout.NAV_PAGES if page != "overview"]  # a damaged overlay still leaves one page
    overlay, _ = layout.create_page(overlay, "p-0000000001", "Mening hisobotim")
    overlay, _ = layout.set_page_visible(overlay, layout.NAV_PAGES[-3], False)  # an own page is enough
    assert layout.hidden_nav_pages(overlay) == list(layout.NAV_PAGES)
    with pytest.raises(layout.LayoutError, match="At least one page"):
        layout.delete_page(overlay, "p-0000000001")


def test_portable_blocks_can_be_added_and_removed():
    overlay, _ = _show("rs.hist", "ov.kpi.complaints", page="staff")(E())
    assert _visible("staff", overlay)[-2:] == ["rs.hist", "ov.kpi.complaints"]
    overlay, _ = _hide("rs.hist", page="staff")(overlay)
    assert "rs.hist" not in _visible("staff", overlay)
    assert overlay["pages"]["staff"]["added"] == ["ov.kpi.complaints"]
    with pytest.raises(layout.LayoutError, match="another page"):
        _show("grd.feed")(E())
    with pytest.raises(layout.LayoutError, match="does not accept"):
        _show("rs.hist", page="groups.detail")(E())


def test_unknown_ids_are_explained():
    with pytest.raises(layout.LayoutError, match="Unknown page"):
        layout.set_visible(E(), "nope", [{"block_id": "x"}], False)
    with pytest.raises(layout.LayoutError, match="Blocks of page overview"):
        _hide("nope")(E())
    with pytest.raises(layout.LayoutError, match="not on page"):
        _hide("rs.hist")(E())
    with pytest.raises(layout.LayoutError):  # internal blocks cannot be touched
        _hide("rs.empty", page="response")(E())


def test_move_and_order_merge():
    overlay, summary = layout.move(E(), "overview", "ov.latest", "top")
    assert _visible("overview", overlay)[0] == "ov.latest" and "ko'chirildi" in summary
    overlay, _ = layout.move(overlay, "overview", "ov.daily", "before", "ov.latest")
    assert _visible("overview", overlay)[:2] == ["ov.daily", "ov.latest"]
    overlay, _ = layout.move(overlay, "overview", "ov.daily", "bottom")
    assert _visible("overview", overlay)[-1] == "ov.daily"
    with pytest.raises(layout.LayoutError, match="Anchor"):
        layout.move(overlay, "overview", "ov.daily", "after", "nope")
    # a block missing from a stored order (new in the code) keeps its place next to its default neighbour
    default = [place.block.id for place in overview.PAGE.places]
    stored = {"pages": {"overview": {"order": [b for b in default if b != "ov.hours"][::-1]}}}
    order = _order("overview", stored)
    assert order.index("ov.hours") == order.index("ov.summary") + 1
    assert order[0] == "ov.latest"
    overlay, _ = layout.move(E(), "overview", "ov.hours", "top")  # moving an optional block shows it
    assert _visible("overview", overlay)[0] == "ov.hours"
    overlay, _ = _hide("ov.latest")(E())
    overlay, _ = layout.move(overlay, "overview", "ov.latest", "top")  # ... and a hidden one too
    assert _visible("overview", overlay)[0] == "ov.latest"


# --------------------------------------------------------------------------- names


def test_blocks_and_pages_can_be_renamed_and_named_back():
    overlay, summary = layout.rename(E(), "overview", "ov.latest", "  Bugungi   shikoyatlar ")
    assert overlay["pages"]["overview"]["titles"] == {"ov.latest": "Bugungi shikoyatlar"}
    assert "nomi o'zgartirildi" in summary and "→ Bugungi shikoyatlar" in summary
    entry = next(item for item in layout.page_view("overview", overlay)["blocks_in_display_order"]
                 if item["id"] == "ov.latest")
    assert entry["title"] == "Bugungi shikoyatlar" and entry["original_title"]
    assert layout.rename(overlay, "overview", "ov.latest", "")[0] == E()
    overlay, summary = layout.rename(E(), "groups", None, "Mijoz guruhlari")
    assert overlay["page_titles"] == {"groups": "Mijoz guruhlari"} and "deb nomlandi" in summary
    assert layout.nav_title("groups", overlay) == "Mijoz guruhlari" == layout.page_title("groups", overlay)
    assert layout.with_title(layout.PAGES["groups"], overlay).title == "Mijoz guruhlari"
    assert layout.rename(overlay, "groups", None, "")[0] == E()
    with pytest.raises(layout.LayoutError, match="navigation page groups"):
        layout.rename(E(), "groups.detail", None, "X")
    with pytest.raises(layout.LayoutError, match="at most"):
        layout.rename(E(), "overview", "ov.latest", "x" * 81)


# --------------------------------------------------------------------------- sections made in the chat


def test_custom_blocks_are_added_changed_and_deleted():
    spec = custom.data_spec(PIE)
    overlay, summary = layout.add_custom(E(), "overview", "c-0000000001", spec, "after", "ov.status")
    assert "yangi bo'lim qo'shildi" in summary
    order = _visible("overview", overlay)
    assert order[order.index("ov.status") + 1] == "c-0000000001"
    overlay, _ = layout.add_custom(overlay, "overview", "c-0000000002", custom.note_spec(NOTE))
    assert _visible("overview", overlay)[-1] == "c-0000000002"
    entry = next(item for item in layout.page_view("overview", overlay)["blocks_in_display_order"]
                 if item["id"] == "c-0000000001")
    assert entry["custom"]["display"] == "pie" and entry["title"] == PIE["title"]
    assert "custom blocks: c-0000000001 «Shikoyatlar toifalar bo'yicha» (data: categories as pie)" in \
        layout.overlay_text(overlay)
    overlay, summary = layout.update_custom(overlay, "overview", "c-0000000001", {**spec, "display": "hbar"})
    assert overlay["pages"]["overview"]["custom"]["c-0000000001"]["display"] == "hbar" and "o'zgartirildi" in summary
    overlay, _ = layout.rename(overlay, "overview", "c-0000000001", "Toifalar")
    assert overlay["pages"]["overview"]["custom"]["c-0000000001"]["title"] == "Toifalar"
    overlay, _ = layout.move(overlay, "overview", "c-0000000002", "top")
    assert _visible("overview", overlay)[0] == "c-0000000002"
    overlay, summary = _hide("c-0000000001", "c-0000000002")(overlay)  # hiding a section made in the chat deletes it
    assert overlay == E() and "Toifalar" in summary
    with pytest.raises(layout.LayoutError, match="Bad custom block id"):
        layout.add_custom(E(), "overview", "ov.new", spec)
    with pytest.raises(layout.LayoutError, match="not a custom block"):
        layout.update_custom(E(), "overview", "c-0000000009", spec)
    full = E()
    for number in range(layout.MAX_CUSTOM_BLOCKS):
        full, _ = layout.add_custom(full, "trends", f"c-{number:010d}", spec)
    with pytest.raises(layout.LayoutError, match="at most"):
        layout.add_custom(full, "trends", "c-9999999999", spec)


def test_own_pages_are_created_filled_renamed_and_deleted():
    overlay, summary = layout.create_page(E(), "p-00000000aa", "Haftalik nazorat", "insights")
    assert summary == "«Haftalik nazorat» sahifasi yaratildi"
    page = layout.get_page("p-00000000aa", overlay)
    assert page.custom and page.title == "Haftalik nazorat" and page.icon == "insights"
    assert layout.is_own_page("p-00000000aa") and not layout.is_own_page("overview")
    overlay, _ = layout.add_custom(overlay, "p-00000000aa", "c-00000000bb", custom.note_spec(NOTE))
    overlay, _ = _show("ov.kpi.complaints", "waiting", page="p-00000000aa")(overlay)
    assert _visible("p-00000000aa", overlay) == ["c-00000000bb", "ov.kpi.complaints", "waiting"]
    assert layout.page_view("p-00000000aa", overlay)["own_page"] is True
    overlay, summary = layout.rename(overlay, "p-00000000aa", None, "Nazorat")
    assert layout.custom_pages(overlay)[0].title == "Nazorat" and "deb nomlandi" in summary
    assert "Own pages: p-00000000aa «Nazorat»" in layout.overlay_text(overlay)
    overlay, summary = layout.delete_page(overlay, "p-00000000aa")
    assert overlay == E() and "o'chirildi" in summary
    with pytest.raises(layout.LayoutError, match="built-in page"):
        layout.delete_page(E(), "overview")
    with pytest.raises(layout.LayoutError, match="icon must be"):
        layout.create_page(E(), "p-00000000ab", "X", "rocket-ship")
    with pytest.raises(layout.LayoutError, match="needs a name"):
        layout.create_page(E(), "p-00000000ab", " ")
    orphan = {"pages": {"p-00000000cc": {"custom": {"c-00000000dd": NOTE}}}}  # its page is gone
    assert _show("ov.hours")(orphan)[0] == {"pages": {"overview": {"shown": ["ov.hours"]}}}


# --------------------------------------------------------------------------- the design


def test_design_is_part_of_the_overlay():
    overlay, summary = layout.set_theme(E(), {"mode": "dark", "primary_color": "#22c55e", "radius": "large"})
    assert overlay["theme"] == {"mode": "dark", "primary_color": "#22C55E", "radius": "large"}
    assert summary.startswith("Dizayn: o'zgartirildi — ") and "asosiy rang" in summary
    overlay, summary = layout.set_theme(overlay, {"radius": None, "font": "Inter"})
    assert overlay["theme"] == {"mode": "dark", "primary_color": "#22C55E", "font": "Inter"}
    assert "Design: mode=dark" in layout.overlay_text(overlay)
    with pytest.raises(layout.LayoutError, match="contrast"):
        layout.set_theme(overlay, {"background_color": "#FFFFFF"})  # light background, dark-mode text
    overlay, summary = layout.reset_theme(overlay)
    assert overlay == E() and summary == "Dizayn asl holatiga qaytarildi"


def test_reset_puts_everything_back():
    overlay, _ = _show("ov.hours")(E())
    overlay, _ = layout.set_page_visible(overlay, "groups", False)
    overlay, _ = layout.set_page_visible(overlay, "reports", True)
    overlay, _ = layout.rename(overlay, "groups", None, "Guruhlarim")
    overlay, _ = layout.set_visible(overlay, "groups.detail", [{"block_id": "grd.customers"}], True)
    overlay, _ = layout.reset(overlay, "groups")  # the list page also resets its detail view, its name and visibility
    assert overlay == {"pages": {"overview": {"shown": ["ov.hours"]}}, "shown_pages": ["reports"]}
    overlay, _ = layout.create_page(overlay, "p-0000000001", "Mening")
    overlay, _ = layout.add_custom(overlay, "p-0000000001", "c-0000000001", custom.note_spec(NOTE))
    overlay, _ = layout.reset(overlay, "p-0000000001")  # an own page loses its sections but stays
    assert [page.id for page in layout.custom_pages(overlay)] == ["p-0000000001"]
    assert _visible("p-0000000001", overlay) == []
    overlay, _ = layout.set_theme(overlay, {"mode": "dark"})
    overlay, summary = layout.reset(overlay, "all")
    assert overlay == E() and "asl holatiga" in summary


def test_page_view_and_overlay_text():
    overlay, _ = _show("ov.hours")(E())
    overlay, _ = _show("rs.hist")(overlay)
    overlay, _ = _hide("ov.latest")(overlay)
    view = layout.page_view("overview", overlay)
    entries = {entry["id"]: entry for entry in view["blocks_in_display_order"]}
    assert entries["ov.latest"] == {"id": "ov.latest", "title": entries["ov.latest"]["title"], "visible": False}
    assert entries["ov.hours"]["visible"] is True and entries["ov.hours"]["hidden_by_default"] is True
    assert entries["ov.sentiment"]["visible"] is False and entries["rs.hist"]["added_from_other_page"] is True
    assert all(item["id"] != "rs.hist" for item in view["portable_blocks_that_can_be_added"])
    text = layout.overlay_text(overlay)
    assert "ov.hours" in text and "rs.hist" in text and "hidden on request: ov.latest" in text
    assert "default layout" in layout.overlay_text(E())


# --------------------------------------------------------------------------- state database


def test_versions_undo_restore():
    settings = load_settings()
    assert state.update_layout(settings, _show("ov.hours"), group_id="g1", source="chat")
    assert state.update_layout(settings, _show("ov.sentiment"), group_id="g2", source="chat")
    assert state.update_layout(settings, _show("ov.sentiment"), group_id="g3", source="chat") is None  # no change
    assert {"ov.hours", "ov.sentiment"} <= set(_visible("overview", state.current_layout(settings)))
    assert state.can_undo(settings)

    assert "Kayfiyat" in state.undo(settings, group_id="u1")
    assert "ov.sentiment" not in _visible("overview", state.current_layout(settings))
    assert "ov.hours" in _visible("overview", state.current_layout(settings))
    assert "Hafta kuni" in state.undo(settings, group_id="u2")  # skips the undo, reverts g1
    assert state.current_layout(settings) == {}
    assert state.undo(settings, group_id="u3") is None and not state.can_undo(settings)

    versions = state.history(settings, 10)
    assert [v["source"] for v in versions[:2]] == ["undo", "undo"]
    first = versions[-1]
    assert state.restore(settings, first["id"], group_id="r1", summary="tiklandi")
    assert "ov.hours" in _visible("overview", state.current_layout(settings))
    assert state.undo(settings, group_id="u4")  # a restore can be undone too
    assert state.current_layout(settings) == {}


def test_a_design_only_change_is_a_change():
    settings = load_settings()
    assert state.update_layout(settings, lambda o: layout.set_theme(o, {"mode": "dark"}), group_id="g1", source="chat")
    assert state.current_layout(settings)["theme"] == {"mode": "dark"}
    assert state.update_layout(settings, lambda o: layout.set_theme(o, {"mode": "dark"}), group_id="g2",
                               source="chat") is None
    assert state.undo(settings, group_id="u1") and "theme" not in state.current_layout(settings)


def test_reset_of_the_default_layout_changes_nothing():
    settings = load_settings()
    assert state.update_layout(settings, lambda o: layout.reset(o, "all"), group_id="g", source="reset") is None
    assert state.history(settings) == []
    state.update_layout(settings, lambda o: layout.set_page_visible(o, "system", True), group_id="g1", source="chat")
    assert state.update_layout(settings, lambda o: layout.reset(o, "all"), group_id="g2", source="reset")
    assert state.update_layout(settings, lambda o: layout.reset(o, "all"), group_id="g3", source="reset") is None
    assert len(state.history(settings)) == 2


def test_failed_change_leaves_state_untouched():
    settings = load_settings()
    for change in (_hide("nope"), lambda o: layout.set_theme(o, {"font": "Comic Sans"}),
                   lambda o: layout.delete_page(o, "p-0000000404")):
        with pytest.raises(layout.LayoutError):
            state.update_layout(settings, change, group_id="g", source="chat")
    assert state.history(settings) == []


def test_multi_step_undo_and_group_of_one_message():
    settings = load_settings()
    for block in ("ov.hours", "ov.sentiment"):  # one chat message, two changes = one undo step
        state.update_layout(settings, _show(block), group_id="msg-1", source="chat")
    state.update_layout(settings, lambda o: layout.set_page_visible(o, "system", True), group_id="msg-2", source="chat")
    assert state.undo(settings, group_id="u", steps=2)
    assert state.current_layout(settings) == {}


def test_chat_history():
    settings = load_settings()
    first = state.chat_add(settings, "user", "Soatlar xaritasini qo'sh")
    state.chat_add(settings, "assistant", "Qo'shildi.", agents="view", changes=["Umumiy: qo'shildi — X"],
                   request_ids=["req_1"], input_tokens=10, output_tokens=3)
    messages = state.chat_recent(settings, 10)
    assert [m["role"] for m in messages] == ["user", "assistant"] and messages[0]["id"] == first
    assert messages[1]["changes"] == ["Umumiy: qo'shildi — X"]
    assert state.chat_recent(settings, 1)[0]["role"] == "assistant"
    assert state.chat_count_since(settings, state.local_midnight_utc(settings.tz)) == 1
