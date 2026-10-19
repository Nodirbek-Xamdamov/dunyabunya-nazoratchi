"""Every page renders on the demo database without errors (Streamlit AppTest)."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import DASHBOARD_DIR

PAGE_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from core.settings import load_settings
from ui import components as c, shell
c.inject_css()
shell.sidebar(load_settings())
from views import {name}
{name}.render()
"""

PAGES = ["overview", "groups", "categories", "agents", "response", "staff", "trends", "search", "reports", "system"]


def run_page(name: str, query: dict | None = None, state: dict | None = None) -> AppTest:
    app = AppTest.from_string(PAGE_SCRIPT.format(root=str(DASHBOARD_DIR), name=name), default_timeout=120)
    for key, value in (query or {}).items():
        app.query_params[key] = value
    for key, value in (state or {}).items():
        app.session_state[key] = value
    app.run()
    return app


def assert_clean(app: AppTest) -> None:
    assert not app.exception, [item.value for item in app.exception]
    assert not app.error, [item.value for item in app.error]


@pytest.mark.parametrize("name", PAGES)
def test_page_renders(name):
    assert_clean(run_page(name))


@pytest.mark.parametrize("preset", ["today", "yesterday", "last24h", "last30", "this_month", "last_month", "report"])
def test_overview_renders_for_every_preset(preset):
    assert_clean(run_page("overview", {"p": preset}))


def test_group_detail_and_category_detail_render(demo_db):
    import sqlite3

    conn = sqlite3.connect(demo_db.as_uri() + "?mode=ro", uri=True)
    chat_id = conn.execute("SELECT chat_id FROM messages GROUP BY chat_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
    conn.close()
    app = run_page("groups", {"g": str(chat_id), "p": "last30"})
    assert_clean(app)
    assert app.session_state["gr_pick"] == chat_id
    for index in range(1, 9):
        assert_clean(run_page("categories", {"c": str(index), "p": "last7"}))


def test_custom_period_and_comparison_off():
    assert_clean(run_page("overview", {"p": "custom", "from": "2000-01-01", "to": "2000-01-03", "cmp": "0"}))


@pytest.mark.parametrize("view", ["work", "off"])
def test_response_work_hour_views(view):
    assert_clean(run_page("response", {"p": "last30"}, {"rs_view": view}))


def test_search_with_text_and_filters():
    app = run_page("search", {"p": "last30"}, {"se_q": "armatura", "se_needs": True, "se_staff": True})
    assert_clean(app)
    assert_clean(run_page("search", {"p": "last30"}, {"se_q": "100%_"}))  # LIKE wildcards are escaped


def test_every_text_key_exists():
    import re

    from ui.i18n import UZ

    used = set()
    for folder in ("ui", "views"):
        for path in (DASHBOARD_DIR / folder).glob("*.py"):
            used |= set(re.findall(r'\bt\("([a-z_.]+)"', path.read_text(encoding="utf-8")))
    dynamic = [f"preset.{key}" for key in ("today", "yesterday", "last24h", "last7", "last30", "this_month",
                                            "last_month", "report", "custom")]
    dynamic += [f"sent.{key}" for key in ("Shikoyat", "Ijobiy", "Neytral")]
    dynamic += [f"gr.risk.{key}" for key in ("red", "yellow", "green")]
    dynamic += [f"status.{key}" for key in ("red", "yellow", "green")]
    dynamic += [f"gr.f.{key}" for key in ("complaints", "missed", "manager", "present")]
    dynamic += [f"rs.view.{key}" for key in ("all", "work", "off")]
    missing = sorted(key for key in used | set(dynamic) if key not in UZ)
    assert not missing, missing


def test_auto_refresh_and_group_name_filter():
    assert_clean(run_page("overview", {"p": "last7"}, {"f_auto": True}))
    assert_clean(run_page("groups", {"p": "last7"}, {"gr_name": "dunyabunya"}))
    assert_clean(run_page("groups", {"p": "last7"}, {"gr_name": "hech-qanday-guruh-yoq"}))


def test_full_app_entry_point():
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=120)
    app.run()
    assert_clean(app)


# --------------------------------------------------------------------------- layout overlay on real pages


def _save(overlay: dict) -> None:
    from core import state
    from core.settings import load_settings

    state.update_layout(load_settings(), lambda _current: (overlay, "test"), group_id="test", source="chat")


def _target(page_id: str, demo_db) -> tuple[str, dict]:
    if page_id == "groups.detail":
        import sqlite3

        conn = sqlite3.connect(demo_db.as_uri() + "?mode=ro", uri=True)
        chat_id = conn.execute("SELECT chat_id FROM messages GROUP BY chat_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
        conn.close()
        return "groups", {"g": str(chat_id), "p": "last30"}
    if page_id == "categories.detail":
        return "categories", {"c": "1", "p": "last30"}
    return page_id, {"p": "last30"}


def _layout_pages() -> list[str]:
    from ui import layout
    from views import agents, categories, groups, overview, reports, response, search, staff, system, trends  # noqa: F401

    return list(layout.PAGES)


@pytest.mark.parametrize("page_id", _layout_pages())
def test_page_with_every_optional_block_added_and_in_reverse_order(page_id, demo_db):
    from ui import layout

    page = layout.PAGES[page_id]
    optional = [place.block.id for place in page.places if place.hidden]
    name, query = _target(page_id, demo_db)
    _save({"pages": {page_id: {"shown": optional}}, "shown_pages": []})
    assert_clean(run_page(name, query))
    _save({"pages": {page_id: {"shown": optional, "order": [place.block.id for place in page.places][::-1]}},
           "shown_pages": []})
    assert_clean(run_page(name, query))


@pytest.mark.parametrize("page_id", ["overview", "trends", "search", "groups"])
def test_every_portable_block_added_to_another_page(page_id, demo_db):
    from ui import layout

    own = set(layout.PAGES[page_id].block_ids)
    added = [block.id for block in layout.BLOCKS.values() if block.portable and block.id not in own]
    _save({"pages": {page_id: {"added": added}}, "shown_pages": []})
    name, query = _target(page_id, demo_db)
    assert_clean(run_page(name, query))


def test_optional_columns_and_filter_added(demo_db):
    from ui import layout

    shown_items = {page_id: {"shown_items": {block_id: list(map(str, layout.BLOCKS[block_id].hidden_items))}}
                   for page_id, block_id in (("groups", "gr.ranking"), ("staff", "st.table"), ("search", "se.results"))}
    shown_items["response"] = {"shown": ["rs.filter"]}
    _save({"pages": shown_items, "shown_pages": []})
    for name in ("groups", "staff", "search"):
        assert_clean(run_page(name, {"p": "last30"}))
    assert_clean(run_page("response", {"p": "last30"}, {"rs_view": "work"}))


def _app(query: dict | None = None) -> AppTest:
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=120)
    for key, value in (query or {}).items():
        app.query_params[key] = value
    app.run()
    assert_clean(app)
    return app


def test_hidden_main_blocks_and_pages():
    _save({"pages": {"overview": {"hidden": ["ov.kpi.sla", "ov.latest"]}}})
    app = run_page("overview", {"p": "last7"})
    assert_clean(app)
    labels = [metric.label for metric in app.metric]
    assert labels and not any("5 daqiqada javob" in label for label in labels)
    _save({"pages": {}, "hidden_pages": ["overview", "trends"]})
    _app()  # the first visible page (groups) becomes the default


def test_own_names_reach_page_titles_and_section_headings():
    _save({"pages": {"overview": {"titles": {"ov.latest": "Eng so'nggi shikoyatlar", "ov.kpi.complaints": "Arizalar"}}},
           "page_titles": {"overview": "Bosh sahifa"}})
    app = _app()
    assert app.title[0].value == "Bosh sahifa"
    assert "Eng so'nggi shikoyatlar" in [header.value for header in app.subheader]
    assert any(metric.label == "Arizalar" for metric in app.metric)


OWN_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from core.settings import load_settings
from ui import components as c, shell
c.inject_css()
shell.sidebar(load_settings())
from views import own
own.make({page_id!r})()
"""


def _every_section() -> list[dict]:
    from ui import custom

    specs = []
    for name, source in custom.SOURCES.items():
        numbers = [column for column, kind in source.columns.items() if kind in custom.NUMERIC]
        counts = [column for column, kind in source.columns.items() if kind == "int"]
        for display in source.displays:
            raw = {"kind": "data", "title": f"{name} {display}", "source": name, "display": display,
                   "params": {"text": "a"} if name == "search_messages" else
                   {"period": "last30"} if "period" in source.params else {"period": "selected"}}
            if display in custom.CHARTS:
                raw["y"] = counts[:1] if display == "pie" else numbers[:2]
            specs.append(custom.data_spec(raw))
    for style in custom.NOTE_STYLES:
        specs.append(custom.note_spec({"title": f"Eslatma {style}", "text": "**Muhim** matn", "style": style}))
    return specs


def test_own_pages_render_every_kind_of_section():
    from ui import layout

    specs = _every_section()
    pages = ["p-0000000001", "p-0000000002"]
    overlay = {"pages": {}, "custom_pages": [{"id": page_id, "title": f"Sahifa {index}", "icon": "insights"}
                                             for index, page_id in enumerate(pages, start=1)]}
    half = len(specs) // 2
    for page_id, part in ((pages[0], specs[:half]), (pages[1], specs[half:])):
        assert len(part) <= layout.MAX_CUSTOM_BLOCKS
        overlay["pages"][page_id] = {"custom": {f"c-{index:010d}": spec for index, spec in enumerate(part)},
                                     "added": ["waiting", "ov.kpi.complaints"]}
    _save(overlay)
    metrics = 0
    for page_id in pages:
        app = AppTest.from_string(OWN_SCRIPT.format(root=str(DASHBOARD_DIR), page_id=page_id), default_timeout=120)
        app.run()
        assert not app.exception, [item.value for item in app.exception]
        for item in [*app.error, *app.warning]:  # only the notes styled as error / warning
            assert item.value == "**Muhim** matn", item.value
        metrics += len(app.metric)
    assert metrics >= 12  # KPI sections plus the added KPI card
    _save({"pages": {}, "custom_pages": [{"id": pages[0], "title": "Bo'sh", "icon": "star"}]})
    app = AppTest.from_string(OWN_SCRIPT.format(root=str(DASHBOARD_DIR), page_id=pages[0]), default_timeout=120)
    app.run()
    assert_clean(app)
    assert "bo'sh" in app.info[0].value
    app = AppTest.from_string(OWN_SCRIPT.format(root=str(DASHBOARD_DIR), page_id="p-0000000404"), default_timeout=120)
    app.run()
    assert_clean(app)
    assert "o'chirilgan" in app.info[0].value


def test_full_app_with_own_pages_and_sections_on_built_in_pages():
    from ui import custom

    pie = custom.data_spec({"kind": "data", "title": "Toifalar", "source": "categories", "display": "pie",
                            "y": ["complaints"]})
    broken = {**pie, "title": "Eski guruh", "params": {"group": "hech-qanday-guruh-yoq"}}
    _save({"pages": {"overview": {"custom": {"c-0000000001": pie, "c-0000000002": broken}}},
           "custom_pages": [{"id": "p-0000000001", "title": "Mening sahifam", "icon": "star"}],
           "hidden_pages": list(__import__("ui.layout", fromlist=["NAV_PAGES"]).NAV_PAGES)})
    app = _app()  # only the own page is left: it is the default page
    assert app.title[0].value == "Mening sahifam"
    _save({"pages": {"overview": {"custom": {"c-0000000001": pie, "c-0000000002": broken}}}})
    app = _app()
    assert "Toifalar" in [header.value for header in app.subheader]
    assert any("Eski guruh" in item.value for item in app.warning)  # a section that no longer fits says so


def test_design_is_applied_and_the_chat_reopens_after_the_reload():
    from core import theme
    from core.settings import load_settings
    from ui import design

    tokens = {"mode": "dark", "primary_color": "#22C55E", "custom_css": "h1 { letter-spacing: 1px; }"}
    _save({"pages": {}, "theme": tokens})
    app = _app({"chat": "1"})
    config = load_settings().theme_config_path.read_text(encoding="utf-8")
    assert f"{theme.MARKER} {theme.fingerprint(tokens)}" in config and 'base = "dark"' in config
    assert app.session_state["chat_fab"] is True and "chat" not in app.query_params
    assert app.session_state[design.SEEN] == design.RELOAD_PENDING  # config.toml was written: the page reloads
    app.run()
    assert app.session_state[design.SEEN] == design.RELOAD_PENDING  # ... and keeps asking until it has
    fresh = _app()  # the reloaded page is a new session with the new design
    assert fresh.session_state[design.SEEN] == theme.fingerprint(tokens)
    _save({"pages": {}})
    fresh.run()  # the design changed while the page was open
    assert_clean(fresh)
    assert fresh.session_state[design.SEEN] == design.RELOAD_PENDING
    assert f"{theme.MARKER} default" in load_settings().theme_config_path.read_text(encoding="utf-8")
    assert _app().session_state[design.SEEN] == "default"


def test_app_with_optional_pages_added():
    _save({"pages": {}, "shown_pages": ["reports", "system"]})
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=120)
    app.run()
    assert_clean(app)


def test_chat_end_to_end_with_a_fake_client(monkeypatch):
    from assistant import llm
    from core import state
    from core.settings import load_settings
    from tests.test_agents import FakeOpenAI, call, say

    client = FakeOpenAI(call(("o1", "change_view", {"instruction": "Soatlar xaritasini qo'sh"})),
                        call(("v1", "set_visible", {"page": "overview", "visible": True,
                                                    "targets": [{"block_id": "ov.hours", "item_ids": None}]})),
                        say("Hafta kuni × soat xaritasi qo'shildi."))
    monkeypatch.setenv("DASHBOARD_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "client_factory", lambda settings: client)
    load_settings.cache_clear()
    try:
        app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=120)
        app.run()
        assert_clean(app)
        app.chat_input[0].set_value("Soatlar xaritasini qo'sh").run()
        assert_clean(app)
        settings = load_settings()
        assert state.current_layout(settings)["pages"]["overview"]["shown"] == ["ov.hours"]
        messages = state.chat_recent(settings, 10)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["text"] == "Hafta kuni × soat xaritasi qo'shildi." and messages[1]["changes"]
        assert not client.script  # every scripted model call was used
    finally:
        monkeypatch.setenv("DASHBOARD_OPENAI_API_KEY", "")
        load_settings.cache_clear()
