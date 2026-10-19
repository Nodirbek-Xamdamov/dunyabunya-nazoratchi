"""The four agents with scripted fake model clients (no network, no API key) for both providers
(OpenAI chat completions and Anthropic messages): routing, hand-off, layout changes and undo, new
sections and own pages, the design, data tools running on the demo database, errors, refusals, step
limits and the exact request shape.

Scripts are written once in a neutral form (``say`` / ``call`` / ``response``); each fake client
turns them into its provider's response objects."""

from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from assistant import data_tools, design_agent, llm, orchestrator, ui_agent
from core import state
from core import timeutil as tu
from core.settings import load_settings
from ui.i18n import t


def block(kind: str, **fields):
    return SimpleNamespace(type=kind, **fields)


def response(*content, stop: str = "end_turn"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=50, cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(content), stop_reason=stop, usage=usage, _request_id="req_test",
                           stop_details=None)


def say(text: str):
    return response(block("text", text=text))


def call(*uses):
    """uses: (id, tool name, input) tuples -> one assistant turn with these tool calls."""
    return response(*[block("tool_use", id=use_id, name=name, input=args) for use_id, name, args in uses], stop="tool_use")


class _Fake:
    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[dict] = []

    def _next(self, kwargs: dict):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # as sent (the loop appends later)
        assert self.script, "the agent made more model calls than scripted"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeAnthropic(_Fake):
    provider = "anthropic"

    def __init__(self, *script):
        super().__init__(*script)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._next_kwargs))

    def _next_kwargs(self, **kwargs):
        return self._next(kwargs)


class FakeOpenAI(_Fake):
    provider = "openai"

    def __init__(self, *script):
        super().__init__(*script)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        item = self._next(kwargs)
        text = "\n\n".join(part.text for part in item.content if part.type == "text") or None
        calls = [SimpleNamespace(id=part.id, type="function",
                                 function=SimpleNamespace(name=part.name, arguments=json.dumps(part.input)))
                 for part in item.content if part.type == "tool_use"]
        finish = {"tool_use": "tool_calls", "max_tokens": "length"}.get(item.stop_reason, "stop")
        message = SimpleNamespace(content=text, tool_calls=calls or None,
                                  refusal="no" if item.stop_reason == "refusal" else None)
        usage = SimpleNamespace(prompt_tokens=150, completion_tokens=20)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage,
                               _request_id="req_test")


FAKES = {"anthropic": FakeAnthropic, "openai": FakeOpenAI}


@pytest.fixture(params=["openai", "anthropic"])
def fake(request, monkeypatch):
    monkeypatch.setenv("DASHBOARD_CHAT_PROVIDER", request.param)
    load_settings.cache_clear()
    yield FAKES[request.param]
    load_settings.cache_clear()


# --------------------------------------------------------------------------- reading requests the same way


def system_text(request: dict) -> str:
    if "system" in request:
        return request["system"][0]["text"]
    return request["messages"][0]["content"]


def last_message(request: dict) -> dict:
    return request["messages"][-1]


def tool_names(request: dict) -> set[str]:
    return {tool["name"] if "name" in tool else tool["function"]["name"] for tool in request.get("tools", [])}


def tool_results(request: dict) -> list[tuple[str, bool]]:
    """(content, is_error) of every tool result sent in a request."""
    found = []
    for message in request["messages"]:
        if message["role"] == "tool":
            content = message["content"]
            found.append((content.removeprefix("ERROR: "), content.startswith("ERROR: ")))
        elif message["role"] == "user" and isinstance(message["content"], list):
            found += [(part["content"], bool(part.get("is_error"))) for part in message["content"]
                      if isinstance(part, dict) and part.get("type") == "tool_result"]
    return found


def context(message_group: str = "chat-1", page: str = "overview") -> dict:
    settings = load_settings()
    now = tu.now_local(settings.tz)
    return {"group_id": message_group, "page_id": page, "period": tu.resolve_period("last7", now, settings.report_time),
            "now": now}


# --------------------------------------------------------------------------- tests


def test_general_question_is_answered_by_the_orchestrator_alone(fake):
    client = fake(say("Salom! Qanday yordam bera olaman?"))
    reply = orchestrator.handle(load_settings(), "Salom", [], context(), client=client)
    assert reply.text.startswith("Salom") and reply.agents == [] and reply.changes == []
    assert len(client.requests) == 1 and reply.usage.input_tokens > 0


def test_request_shape(fake):
    settings = load_settings()
    client = fake(say("ok"))
    orchestrator.handle(settings, "Salom", [{"role": "user", "text": "oldingi"}, {"role": "assistant", "text": "javob"}],
                        context(), client=client)
    request = client.requests[0]
    assert "Dashboard context" in last_message(request)["content"] and last_message(request)["role"] == "system"
    assert "You are the AI assistant" in system_text(request)
    if fake.provider == "anthropic":
        assert request["model"] == settings.chat.model == "claude-opus-5-5"
        assert request["output_config"] == {"effort": "low"}
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert request["betas"] == [llm.FALLBACK_BETA] and request["fallbacks"] == "default"
        assert "thinking" not in request and "tool_choice" not in request
        assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user", "system"]
        schemas = [tool["input_schema"] for tool in request["tools"]]
        assert all(tool["strict"] is True for tool in request["tools"])
    else:
        assert request["model"] == settings.chat.model == "gpt-5.4-mini"
        assert request["reasoning_effort"] == "none" and request["max_completion_tokens"] == llm.MAX_TOKENS  # tools: no reasoning
        assert [m["role"] for m in request["messages"]] == ["system", "user", "assistant", "user", "system"]
        schemas = [tool["function"]["parameters"] for tool in request["tools"]]
        assert all(tool["type"] == "function" and tool["function"]["strict"] is True for tool in request["tools"])
    for schema in schemas:
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])


def test_add_then_undo_through_the_view_agent(fake):
    settings = load_settings()
    add = call(("v1", "set_visible", {"page": "overview", "visible": True,
                                      "targets": [{"block_id": "ov.hours", "item_ids": None},
                                                  {"block_id": "ov.sentiment", "item_ids": None}]}))
    client = fake(call(("o1", "change_view", {"instruction": "Soatlar xaritasi va kayfiyat grafigini qo'sh (Umumiy)"})),
                  add, say("Soatlar xaritasi va kayfiyat grafigi qo'shildi."))
    reply = orchestrator.handle(settings, "Soatlar xaritasi va kayfiyatni qo'sh", [], context("chat-1"), client=client)
    assert reply.agents == ["view"] and len(client.requests) == 3  # the view agent's answer is passed through
    assert reply.text.startswith("Soatlar xaritasi") and reply.changes and "qo'shildi" in reply.changes[0]
    assert state.current_layout(settings)["pages"]["overview"]["shown"] == ["ov.hours", "ov.sentiment"]
    view_prompt = system_text(client.requests[1])
    assert "Built-in pages and their blocks" in view_prompt and "- daily_trend: params" in view_prompt

    client = fake(call(("o2", "change_view", {"instruction": "Oxirgi o'zgarishni qaytar"})),
                  call(("v2", "undo", {"steps": None})), say("Qaytarildi."))
    reply = orchestrator.handle(settings, "Qaytar", [], context("chat-2"), client=client)
    assert state.current_layout(settings) == {}
    assert reply.changes and reply.changes[0].startswith("Bekor qilindi")
    assert "ov.hours" in last_message(client.requests[1])["content"]  # the view context lists what was added


def test_any_block_can_be_removed_by_the_agent(fake):
    settings = load_settings()
    client = fake(call(("o1", "change_view", {"instruction": "Shikoyatlarni olib tashla"})),
                  call(("v1", "set_visible", {"page": "overview", "visible": False,
                                              "targets": [{"block_id": "ov.kpi.complaints", "item_ids": None},
                                                          {"block_id": "ov.latest", "item_ids": None}]})),
                  say("Shikoyatlar kartasi va so'nggi shikoyatlar olib tashlandi."))
    reply = orchestrator.handle(settings, "Shikoyatlarni olib tashla", [], context(), client=client)
    assert not tool_results(client.requests[2])[0][1]
    assert state.current_layout(settings)["pages"]["overview"]["hidden"] == ["ov.kpi.complaints", "ov.latest"]
    assert reply.changes and "olib tashlandi" in reply.changes[0]


def _ids(monkeypatch):
    numbers = iter(range(1, 100))
    monkeypatch.setattr(ui_agent, "new_id", lambda prefix: f"{prefix}-{next(numbers):010d}")


NO_PARAMS = {key: None for key in ("period", "date_from", "date_to", "group", "category", "sort_by", "limit", "text",
                                   "sentiment")}


def test_the_view_agent_builds_an_own_page_with_new_sections(fake, monkeypatch):
    _ids(monkeypatch)
    settings = load_settings()
    chart = {"page": "p-0000000001", "title": "Kunlik shikoyatlar", "note": None, "source": "daily_trend",
             "params": NO_PARAMS, "display": "line", "x": None, "y": ["complaints"], "columns": None, "metrics": None,
             "position": "bottom", "anchor_block_id": None}
    client = fake(call(("o1", "change_view", {"instruction": "Shikoyatlar uchun alohida sahifa och, grafik bilan"})),
                  call(("v1", "create_page", {"title": "Shikoyatlar nazorati", "icon": "report"})),
                  call(("v2", "add_data_view", {**chart, "y": ["day"]})),  # wrong column: the error explains
                  call(("v3", "add_data_view", chart),
                       ("v4", "add_note", {"page": "p-0000000001", "title": "Eslatma", "text": "Har kuni ko'rib chiqing",
                                           "style": "info", "position": "top", "anchor_block_id": None}),
                       ("v5", "set_visible", {"page": "p-0000000001", "visible": True,
                                              "targets": [{"block_id": "ov.kpi.complaints", "item_ids": None}]})),
                  say("«Shikoyatlar nazorati» sahifasi tayyor."))
    reply = orchestrator.handle(settings, "Shikoyatlar sahifasini och", [], context(), client=client)
    assert reply.text == "«Shikoyatlar nazorati» sahifasi tayyor." and len(reply.changes) == 4
    assert json.loads(tool_results(client.requests[2])[0][0])["page_id"] == "p-0000000001"
    content, is_error = tool_results(client.requests[3])[1]
    assert is_error and "not number columns" in content
    results = tool_results(client.requests[4])[2:]
    assert len(results) == 3 and not any(error for _content, error in results)
    assert json.loads(results[0][0])["data_now"]["rows_now"] >= 7
    overlay = state.current_layout(settings)
    assert overlay["custom_pages"] == [{"id": "p-0000000001", "title": "Shikoyatlar nazorati", "icon": "report"}]
    page = overlay["pages"]["p-0000000001"]
    assert page["custom"]["c-0000000002"]["display"] == "line" and page["custom"]["c-0000000003"]["kind"] == "note"
    assert page["order"] == ["c-0000000003", "c-0000000002", "ov.kpi.complaints"]

    client = fake(call(("o2", "change_view", {"instruction": "Grafikni ustunli qil va 30 kunlik qil"})),
                  call(("v6", "update_custom_block", {"page": "p-0000000001", "block_id": "c-0000000002",
                                                      "title": None, "note": None, "source": None,
                                                      "params": {**NO_PARAMS, "period": "last30"}, "display": "bar",
                                                      "x": None, "y": None, "columns": None, "metrics": None,
                                                      "text": None, "style": None})),
                  say("Grafik o'zgartirildi."))
    orchestrator.handle(settings, "Grafikni ustunli qil", [], context("chat-2", page="p-0000000001"), client=client)
    spec = state.current_layout(settings)["pages"]["p-0000000001"]["custom"]["c-0000000002"]
    assert spec["display"] == "bar" and spec["params"] == {"period": "last30"} and spec["y"] == ["complaints"]
    assert "p-0000000001 — Shikoyatlar nazorati" in last_message(client.requests[1])["content"]


UNSET = {name: None for name in ("mode", "primary_color", "background_color", "surface_color", "text_color",
                                 "sidebar_color", "border_color", "link_color", "font", "heading_font", "radius",
                                 "font_size", "chart_colors", "custom_css", "reset")}


def test_the_design_agent_changes_the_look(fake):
    settings = load_settings()
    css = '[data-testid="stMetric"] { box-shadow: 0 2px 8px #000000; }'
    client = fake(call(("o1", "change_design", {"instruction": "Qorong'i va zamonaviy qil"})),
                  call(("d1", "set_design", {**UNSET, "mode": "dark", "background_color": "#FFFFFF"})),
                  call(("d2", "set_design", {**UNSET, "mode": "dark", "primary_color": "#22C55E", "font": "Inter",
                                             "custom_css": css})),
                  say("Dizayn qorong'i rejimga o'tdi; sahifa bir lahzada yangilanadi."))
    reply = orchestrator.handle(settings, "Qorong'i qil", [], context(), client=client)
    assert reply.agents == ["design"] and reply.text.startswith("Dizayn qorong'i")
    content, is_error = tool_results(client.requests[2])[0]
    assert is_error and "hard to read" in content
    design = state.current_layout(settings)["theme"]
    assert design == {"mode": "dark", "primary_color": "#22C55E", "font": "Inter", "custom_css": css}
    assert reply.changes and reply.changes[0].startswith("Dizayn: o'zgartirildi")
    assert "Design settings" in system_text(client.requests[1]) and "set_design" in tool_names(client.requests[1])

    client = fake(call(("o2", "change_design", {"instruction": "Chat tugmasini yashir"})),
                  call(("d3", "set_design", {**UNSET, "custom_css": ".st-key-chat_fab { color: transparent; }"})),
                  say("Chatni yashirib bo'lmaydi."))
    reply = orchestrator.handle(settings, "Chatni yashir", [], context("chat-2"), client=client)
    content, is_error = tool_results(client.requests[2])[0]
    assert is_error and "may not style the chat" in content and reply.changes == []
    assert '"mode": "dark"' in last_message(client.requests[1])["content"]  # the design context shows the settings


def _strict(schema: dict, where: str) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"]), where
        for name, value in schema["properties"].items():
            _strict(value, f"{where}.{name}")
    for option in schema.get("anyOf", []):
        _strict(option, where)
    if isinstance(schema.get("items"), dict):
        _strict(schema["items"], where + "[]")


def test_every_tool_schema_is_strict():
    settings = load_settings()
    tools = [*ui_agent._tools(settings, context(), []), *design_agent._tools(settings, context(), []),
             *data_tools.build(settings, context())]
    assert {"add_data_view", "create_page", "rename", "set_design", "overview"} <= {tool.name for tool in tools}
    for tool in tools:
        _strict(tool.schema, tool.name)


def test_tool_error_goes_back_to_the_model(fake):
    settings = load_settings()
    client = fake(call(("o1", "change_view", {"instruction": "x"})),
                  call(("v1", "set_visible", {"page": "overview", "visible": True,
                                              "targets": [{"block_id": "nope", "item_ids": None}]})),
                  call(("v2", "set_visible", {"page": "overview", "visible": True,
                                              "targets": [{"block_id": "ov.hours", "item_ids": None}]})),
                  say("Soatlar xaritasi qo'shildi."))
    reply = orchestrator.handle(settings, "Soatlar xaritasini qo'sh", [], context(), client=client)
    content, is_error = tool_results(client.requests[2])[0]
    assert is_error and "Blocks of page overview" in content
    assert state.current_layout(settings)["pages"]["overview"]["shown"] == ["ov.hours"]
    assert len(reply.changes) == 1


def test_data_question_runs_real_tools_on_the_demo_database(fake):
    client = fake(call(("o1", "ask_data", {"question": "Oxirgi 7 kunda nechta shikoyat bo'ldi?"})),
                  call(("d1", "overview", {"period": "last7", "date_from": None, "date_to": None, "group": None}),
                       ("d2", "groups_ranking", {"period": "selected", "date_from": None, "date_to": None,
                                                 "sort_by": "complaints", "limit": 3})),
                  say("Oxirgi 7 kunda 12 ta shikoyat."))
    reply = orchestrator.handle(load_settings(), "Shikoyatlar nechta?", [], context(), client=client)
    assert reply.agents == ["data"] and reply.text == "Oxirgi 7 kunda 12 ta shikoyat." and reply.changes == []
    results = tool_results(client.requests[2])
    assert len(results) == 2 and not any(is_error for _content, is_error in results)
    overview = json.loads(results[0][0])
    assert {"complaints", "requests", "previous_period", "change_vs_previous"} <= set(overview)
    ranking = json.loads(results[1][0])
    assert len(ranking["rows"]) <= 3 and ranking["sorted_by"] == "complaints"
    assert "set_visible" not in tool_names(client.requests[1])  # the data agent cannot change views


def test_mixed_message_uses_both_agents_and_the_orchestrator_answers(fake):
    client = fake(call(("o1", "ask_data", {"question": "Bugun nechta shikoyat?"}),
                       ("o2", "change_view", {"instruction": "Soatlar xaritasini qo'sh"})),
                  say("Bugun 3 ta shikoyat."),
                  call(("v1", "set_visible", {"page": "overview", "visible": True,
                                              "targets": [{"block_id": "ov.hours", "item_ids": None}]})),
                  say("Qo'shildi."),
                  say("Bugun 3 ta shikoyat bo'ldi; soatlar xaritasini qo'shdim."))
    reply = orchestrator.handle(load_settings(), "Bugun nechta shikoyat? Va soatlar xaritasini qo'sh", [],
                                context(), client=client)
    assert reply.agents == ["data", "view"] and len(client.requests) == 5
    assert reply.text.startswith("Bugun 3 ta shikoyat bo'ldi")


def _connection_error(provider: str) -> Exception:
    request = httpx2.Request("POST", "https://example.invalid")
    return (anthropic.APIConnectionError(request=request) if provider == "anthropic"
            else openai.APIConnectionError(request=request))


def test_connection_error_refusal_and_step_limit(fake):
    settings = load_settings()
    with pytest.raises(llm.AgentError) as error:
        orchestrator.handle(settings, "Salom", [], context(), client=fake(_connection_error(fake.provider)))
    assert str(error.value) == t("chat.err.network")

    refusing = fake(response(stop="refusal"))
    assert orchestrator.handle(settings, "x", [], context(), client=refusing).text == t("chat.err.refusal")

    steps = max(settings.chat.max_steps, ui_agent.VIEW_STEPS)
    looping = fake(call(("o1", "change_view", {"instruction": "x"})),
                   *[call((f"v{i}", "get_layout", {"page": "overview"})) for i in range(steps)])
    with pytest.raises(llm.AgentError) as error:
        orchestrator.handle(settings, "x", [], context(), client=looping)
    assert str(error.value) == t("chat.err.steps")


def test_openai_quota_error_is_explained(monkeypatch):
    monkeypatch.setenv("DASHBOARD_CHAT_PROVIDER", "openai")
    load_settings.cache_clear()
    request = httpx2.Request("POST", "https://example.invalid")
    quota = openai.RateLimitError("You exceeded your current quota", response=httpx2.Response(429, request=request),
                                  body={"code": "insufficient_quota"})
    with pytest.raises(llm.AgentError) as error:
        orchestrator.handle(load_settings(), "x", [], context(), client=FakeOpenAI(quota))
    assert str(error.value) == t("chat.err.quota")
    load_settings.cache_clear()


def test_settings_pick_the_key_and_default_model_of_the_provider(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPENAI_API_KEY", "sk-test")
    for provider, model, key in (("openai", "gpt-5.4-mini", "sk-test"), ("anthropic", "claude-opus-5-5", ""),
                                 ("nonsense", "gpt-5.4-mini", "sk-test")):
        monkeypatch.setenv("DASHBOARD_CHAT_PROVIDER", provider)
        load_settings.cache_clear()
        chat = load_settings().chat
        assert (chat.model, chat.api_key) == (model, key)
        assert "sk-test" not in repr(chat)
    load_settings.cache_clear()


def test_repeated_lines_are_dropped():
    assert orchestrator.without_repeats("A gap.\nA gap.\n\n- x\n- x\nB") == "A gap.\n\n- x\nB"
    assert orchestrator.without_repeats("Bir\n\nIkki\n\nBir") == "Bir\n\nIkki\n\nBir"  # not consecutive


def test_history_messages():
    history = [
        {"role": "assistant", "text": "eski javob", "agents": "view", "changes": []},  # dropped: must start with user
        {"role": "user", "text": "Shikoyatlarni olib tashla", "agents": "", "changes": []},
        {"role": "assistant", "text": "Yashirildi.", "agents": "view", "changes": ["Umumiy: yashirildi — A"]},
        {"role": "user", "text": "nimadir", "agents": "", "changes": []},
        {"role": "assistant", "text": "AI'ga ulanib bo'lmadi", "agents": "error", "changes": []},
    ]
    messages = orchestrator.history_messages(history, "Qaytar")
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "user"]
    assert "Umumiy: yashirildi — A" in messages[1]["content"] and messages[-1]["content"] == "Qaytar"
