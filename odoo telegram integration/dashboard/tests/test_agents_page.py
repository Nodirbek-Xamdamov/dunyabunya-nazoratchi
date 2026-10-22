"""The «AI agentlar» page and the editable system prompts: storage, how a prompt is put together, the
agents calling the model with an edited prompt (scripted fake clients, no network), and the page
itself -- the tree, every agent's settings, save, reset and restore."""

from __future__ import annotations

from assistant import orchestrator, prompts
from core import state
from core.settings import load_settings
from tests.test_agents import call, context, fake, say, system_text  # noqa: F401  (fake is a fixture)
from tests.test_views import assert_clean, run_page


def test_prompts_are_built_from_instructions_and_the_dashboards_reference():
    settings = load_settings()
    for agent in prompts.AGENTS:
        assert prompts.saved_instructions(agent, settings) is None
        assert prompts.system(agent, settings).startswith(prompts.DEFAULTS[agent].strip())
    assert prompts.reference("orchestrator", settings) == ""
    assert prompts.system("orchestrator", settings) == prompts.ORCHESTRATOR.strip()
    assert f"within {settings.sla_minutes} minutes" in prompts.reference("data", settings)
    assert "## agents — AI agentlar" in prompts.reference("view", settings)  # the new page is in the catalog
    assert prompts.AGENTS == ("orchestrator", "data", "view")  # no design agent since 2026-10-22

    state.set_agent_prompt(settings, "data", "Sen qisqa javob beradigan agentsan.", "save")
    composed = prompts.system("data", settings)
    assert composed.startswith("Sen qisqa javob beradigan agentsan.\n\n" + prompts.REFERENCE_HEADING)
    assert composed.endswith(prompts.reference("data", settings))  # the reference is always there
    assert prompts.system("view", settings).startswith(prompts.VIEW.strip())  # the other agents keep theirs

    state.set_agent_prompt(settings, "data", "", "reset")
    assert prompts.saved_instructions("data", settings) is None
    assert prompts.system("data", settings).startswith(prompts.DATA.strip())
    history = state.agent_prompt_history(settings, "data")
    assert [row["action"] for row in history] == ["reset", "save"]
    assert state.agent_prompt_version(settings, "data", history[1]["id"])["text"].startswith("Sen qisqa")
    assert state.agent_prompt_version(settings, "view", history[1]["id"]) is None


def test_agents_are_called_with_the_edited_prompts(fake):
    settings = load_settings()
    state.set_agent_prompt(settings, "orchestrator", "Sen Boshqaruvchisan. Ma'lumot savolini ask_data ga ber.", "save")
    state.set_agent_prompt(settings, "data", "Sen ma'lumot agentisan. Juda qisqa javob ber.", "save")
    client = fake(call(("o1", "ask_data", {"question": "Bugun nechta shikoyat bor?"})), say("Bugun 3 ta shikoyat."))
    reply = orchestrator.handle(settings, "Bugun nechta shikoyat?", [], context(), client=client)
    assert reply.agents == ["data"] and reply.text == "Bugun 3 ta shikoyat."
    assert system_text(client.requests[0]) == "Sen Boshqaruvchisan. Ma'lumot savolini ask_data ga ber."
    data_prompt = system_text(client.requests[1])
    assert data_prompt.startswith("Sen ma'lumot agentisan.") and "Definitions used by the dashboard" in data_prompt
    assert [tool.name for tool in orchestrator.describe_tools()] == ["ask_data", "change_view"]


def test_tree_and_every_agents_settings_render():
    app = run_page("agents")
    assert_clean(app)
    assert app.title[0].value == "AI agentlar"
    headings = [header.value.replace("\\", "") for header in app.subheader]  # names are Markdown-escaped
    for name in ("Boshqaruvchi", "Savol-javob agenti", "Ko'rinish agenti"):
        assert any(name in heading for heading in headings), headings
    assert not any("Dizayn" in heading for heading in headings)
    for agent in prompts.AGENTS:
        app = run_page("agents", {"a": agent})
        assert_clean(app)
        assert app.text_area[0].value == prompts.DEFAULTS[agent]
        assert any(button.label == "Saqlash" for button in app.button)
    app = run_page("agents", {"a": "yoq-agent"})
    assert_clean(app)
    assert "Bunday agent yo'q" in app.warning[0].value


def test_clicking_an_agent_opens_its_settings_and_back():
    app = run_page("agents")
    next(button for button in app.button if button.key == "agopen-view").click().run()
    assert_clean(app)
    assert app.query_params["a"] == "view" and "Ko'rinish agenti" in app.title[0].value
    next(button for button in app.button if button.key == "agrel-orchestrator").click().run()
    assert app.query_params["a"] == "orchestrator"
    next(button for button in app.button if button.key == "agback").click().run()
    assert_clean(app)
    assert "a" not in app.query_params and app.title[0].value == "AI agentlar"


def _button(app, key: str):
    return next(button for button in app.button if button.key == key)


def _submit(app):
    return next(button for button in app.button if button.label == "Saqlash")


def test_save_reset_and_restore_a_prompt():
    settings = load_settings()
    app = run_page("agents", {"a": "view"})
    assert _button(app, "agreset-view").disabled  # already the default
    app.text_area[0].set_value("Sen ko'rinish agentisan. Faqat so'ralganini o'zgartir.")
    _submit(app).click().run()
    assert_clean(app)
    assert prompts.instructions("view", settings) == "Sen ko'rinish agentisan. Faqat so'ralganini o'zgartir."
    assert app.success[0].value.startswith("Saqlandi")
    assert app.text_area[0].value == "Sen ko'rinish agentisan. Faqat so'ralganini o'zgartir."  # the new version is shown

    _submit(app).click().run()  # nothing changed
    assert "o'zgarmagan" in app.info[0].value and len(state.agent_prompt_history(settings, "view")) == 1
    app.text_area[0].set_value("   ")
    _submit(app).click().run()
    assert "Bo'sh prompt" in app.warning[0].value and len(state.agent_prompt_history(settings, "view")) == 1

    _button(app, "agreset-view").click().run()
    assert_clean(app)
    assert prompts.saved_instructions("view", settings) is None
    assert app.text_area[0].value == prompts.VIEW

    saved_id = state.agent_prompt_history(settings, "view")[1]["id"]
    _button(app, f"agrestore-{saved_id}").click().run()
    assert_clean(app)
    assert prompts.instructions("view", settings) == "Sen ko'rinish agentisan. Faqat so'ralganini o'zgartir."
    assert [row["action"] for row in state.agent_prompt_history(settings, "view")] == ["restore", "reset", "save"]

    app.text_area[0].set_value(prompts.VIEW)  # the default typed back in counts as the default
    _submit(app).click().run()
    assert prompts.saved_instructions("view", settings) is None
    assert state.agent_prompt_history(settings, "view")[0]["action"] == "reset"


def test_the_page_can_be_hidden_and_renamed_like_any_other():
    from ui import layout

    overlay, summary = layout.set_page_visible({}, "agents", False)
    assert "agents" in layout.hidden_nav_pages(overlay) and "olib tashlandi" in summary
    overlay, _ = layout.rename({}, "agents", None, "Yordamchilar")
    assert layout.nav_title("agents", overlay) == "Yordamchilar"
