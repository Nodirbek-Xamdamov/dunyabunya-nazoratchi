"""Agents created on the «AI agentlar» page: the tree rules, the agent designer, created agents working
in the chat with their own model and tools, the model chosen for a built-in agent, read-only Odoo access
(a local fake Odoo server), encrypted connections and the agents' Telegram bots (a local fake Telegram
server), and the page itself. No real model, Odoo or Telegram is called."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

import pytest

import agent_bots
from assistant import builder, llm, odoo_tools, orchestrator, prompts, registry
from assistant.llm import AgentError, ToolError
from core import secrets, state
from core import telegram as tg
from core.settings import load_settings
from tests.test_agents import FakeOpenAI, call, context, fake, last_message, say, system_text, tool_names, tool_results  # noqa: F401
from tests.test_views import assert_clean, run_page


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("DASHBOARD_ANTHROPIC_API_KEY", "test-anthropic")
    load_settings.cache_clear()
    yield
    load_settings.cache_clear()


def make_agent(settings, agent_id: str, name: str, parent: str = "orchestrator", model: str | None = None,
               groups=("data",), prompt: str = "You are the test agent. Answer briefly.", routing: str = "Test requests.",
               odoo: str | None = None, telegram: str | None = None) -> None:
    state.save_custom_agent(settings, {
        "id": agent_id, "name": name, "summary": f"{name}: test", "routing": routing, "task": "test", "parent": parent,
        "model": model or settings.chat.model, "tool_groups": list(groups), "odoo_connection": odoo,
        "telegram_connection": telegram, "generated_prompt": prompt, "missing": []})


# --------------------------------------------------------------------------- the tree


def test_tree_rules_and_deleting_moves_sub_agents_up(keys):
    settings = load_settings()
    make_agent(settings, "ag-00000001", "Sotuv")
    make_agent(settings, "ag-00000002", "Qarzdorlar", parent="ag-00000001")
    make_agent(settings, "ag-00000003", "Shikoyatlar", parent="data")
    agents = registry.load(settings)
    assert registry.depth(agents, "ag-00000002") == registry.depth(agents, "ag-00000003") == 3
    assert set(registry.parent_options(agents)) == {"orchestrator", "data", "view", "ag-00000001"}
    assert registry.parent_options(agents, "ag-00000001") == ["orchestrator"]  # its sub-agent would go too deep
    make_agent(settings, "ag-00000004", "Yetim", parent="ag-99999999")
    assert registry.load(settings)["ag-00000004"].parent == "orchestrator"  # a lost parent: under the root
    assert state.delete_custom_agent(settings, "ag-00000001") == ["ag-00000002"]
    agents = registry.load(settings)
    assert "ag-00000001" not in agents and agents["ag-00000002"].parent == "orchestrator"


def test_models_and_the_settings_an_agent_runs_with(keys):
    from assistant import models

    settings = load_settings()
    ids = [model.id for model in models.available(settings)]
    assert ids[:4] == ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4", "gpt-5.5"] and "claude-opus-5-5" in ids
    claude = models.for_model(settings, "claude-sonnet-5-5")
    assert (claude.chat.provider, claude.chat.model) == ("anthropic", "claude-sonnet-5-5")
    assert models.for_model(settings, "no-such-model") is settings
    assert models.builder_model(settings) == "gpt-5.4"


def test_without_a_claude_key_claude_models_are_not_offered(monkeypatch):
    from assistant import models

    monkeypatch.setenv("DASHBOARD_OPENAI_API_KEY", "test-openai")
    load_settings.cache_clear()
    try:
        settings = load_settings()
        assert not any(model.provider == "anthropic" for model in models.available(settings))
        assert models.for_model(settings, "claude-opus-5-5") is settings  # falls back to the default model
    finally:
        load_settings.cache_clear()


# --------------------------------------------------------------------------- the designer


def _design(**changes) -> dict:
    design = {"summary": "Mijozlar qarzini ko'rsatadi.", "routing": "Customer debts and payments in Odoo.",
              "model": "gpt-5.4-mini", "model_reason": "Oddiy savol-javob.", "parent": "orchestrator",
              "parent_reason": "Alohida vazifa.", "tool_groups": ["odoo", "odoo"], "tools_reason": "Odoo'dan o'qiydi.",
              "telegram_bot": True, "telegram_reason": "Menejerlar Telegram'da so'raydi.",
              "missing": [{"need": "Har kuni 9:00 da xabar yuborish", "why": "Rejalashtirish hozircha yo'q."}],
              "questions": [], "system_prompt": "You are «Qarzdorlar». Find customers with unpaid invoices."}
    design.update(changes)
    return design


TASK = "Odoo'dagi hisob-fakturalarni ko'rib, qaysi mijozlar qarzdor ekanini menejerlarga Telegram'da aytib bersin."


def test_the_designer_proposes_an_agent_with_one_forced_tool_call(fake, keys):
    settings = load_settings()
    model = "gpt-5.4-mini" if fake.provider == "openai" else "claude-haiku-5-5"
    client = fake(call(("b1", "submit_design", _design(model=model))))
    draft = builder.design(settings, "  Qarzdorlar ", TASK, client=client)
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["model"] == ("gpt-5.4" if fake.provider == "openai" else "claude-opus-5-5")
    assert request["tool_choice"] == ({"type": "function", "function": {"name": "submit_design"}}
                                      if fake.provider == "openai" else {"type": "tool", "name": "submit_design"})
    assert tool_names(request) == {"submit_design"} and TASK in last_message(request)["content"]
    assert draft.name == "Qarzdorlar" and draft.model == model and draft.parent == "orchestrator"
    assert draft.tool_groups == ["odoo"] and draft.telegram and draft.missing[0]["need"].startswith("Har kuni")
    assert draft.system_prompt.startswith("You are «Qarzdorlar»")

    client = fake(call(("b1", "submit_design", _design(model="gpt-unknown", parent="ag-x", tool_groups=["sql"]))))
    draft = builder.design(settings, "Qarzdorlar", TASK, client=client)
    assert (draft.model, draft.parent, draft.tool_groups) == (settings.chat.model, "orchestrator", [])

    client = fake(call(("b1", "submit_design", _design(system_prompt="  "))))
    with pytest.raises(AgentError):
        builder.design(settings, "Qarzdorlar", TASK, client=client)


# --------------------------------------------------------------------------- created agents at work


def test_the_orchestrator_hands_work_to_a_created_agent_with_its_own_model_and_tools(fake, keys):
    settings = load_settings()
    model = "gpt-5.4" if fake.provider == "openai" else "claude-sonnet-5-5"
    make_agent(settings, "ag-00000001", "Shikoyat tahlilchisi", model=model,
               prompt="You are the complaint analyst. Use the complaints tool.")
    client = fake(call(("o1", "agent_ag_00000001", {"request": "Oxirgi 7 kundagi shikoyatlar"})),
                  call(("c1", "complaints", {"period": "last7"})),
                  say("Oxirgi 7 kunda 3 ta shikoyat."))
    reply = orchestrator.handle(settings, "Shikoyatlar qanday?", [], context(), client=client)
    assert reply.text == "Oxirgi 7 kunda 3 ta shikoyat." and reply.agents == ["ag-00000001"]
    first, agent_request = client.requests[0], client.requests[1]
    assert "agent_ag_00000001" in tool_names(first) and "«Shikoyat tahlilchisi»" in system_text(first)
    assert agent_request["model"] == model
    prompt = system_text(agent_request)
    assert prompt.startswith("You are the complaint analyst.") and prompts.REFERENCE_HEADING in prompt
    assert "Definitions used by the dashboard" in prompt and "## Rules set by the dashboard" in prompt
    assert "complaints" in tool_names(agent_request) and "set_visible" not in tool_names(agent_request)
    assert last_message(agent_request)["content"].startswith("Context (set by the dashboard")
    content, is_error = tool_results(client.requests[2])[0]
    assert not is_error and json.loads(content)


def test_a_built_in_agent_runs_with_the_model_chosen_on_the_page(fake, keys):
    settings = load_settings()
    model = "gpt-5.5" if fake.provider == "openai" else "claude-haiku-5-5"
    state.set_agent_model(settings, "data", model)
    client = fake(call(("o1", "ask_data", {"question": "Nechta shikoyat bor?"})), say("3 ta."))
    reply = orchestrator.handle(settings, "Nechta shikoyat?", [], context(), client=client)
    assert reply.text == "3 ta."
    assert client.requests[0]["model"] == settings.chat.model and client.requests[1]["model"] == model
    state.set_agent_model(settings, "data", None)
    assert registry.load(settings)["data"].model == settings.chat.model


def test_a_sub_agent_of_a_specialist_is_called_through_it(fake, keys):
    settings = load_settings()
    make_agent(settings, "ag-00000002", "Xodimlar tahlili", parent="data", routing="Staff answer speed.")
    client = fake(call(("o1", "ask_data", {"question": "Xodimlar qanday ishlayapti?"})),
                  call(("d1", "agent_ag_00000002", {"request": "Xodimlarning javob tezligi"})),
                  say("Xodimlar o'rtacha 4 daqiqada javob beradi."),
                  say("Xodimlar yaxshi ishlayapti: o'rtacha 4 daqiqa."))
    reply = orchestrator.handle(settings, "Xodimlar qanday?", [], context(), client=client)
    assert reply.agents == ["data", "ag-00000002"] and reply.text.startswith("Xodimlar yaxshi")
    assert "Savol-javob agenti → «Xodimlar tahlili»" in system_text(client.requests[0])
    data_request = client.requests[1]
    assert "agent_ag_00000002" in tool_names(data_request) and "## Agents you can call" in system_text(data_request)


# --------------------------------------------------------------------------- Odoo (a fake server)


class FakeOdoo:
    PARTNERS = [{"id": 1, "name": "Ali Qurilish", "phone": "+998901112233"},
                {"id": 2, "name": "Vali Savdo", "phone": "+998907778899"}]
    ORDERS = [{"id": 10, "name": "S00010", "partner_id": [1, "Ali Qurilish"], "amount_total": 1500000.0, "state": "sale"},
              {"id": 11, "name": "S00011", "partner_id": [1, "Ali Qurilish"], "amount_total": 500000.0, "state": "sale"},
              {"id": 12, "name": "S00012", "partner_id": [2, "Vali Savdo"], "amount_total": 700000.0, "state": "draft"}]

    def __init__(self) -> None:
        self.methods: list[str] = []

    def authenticate(self, db, login, key, _env):
        return 7 if (db, login, key) == ("testdb", "agent@dunyabunya.uz", "secret-key") else False

    def version(self):
        return {"server_version": "18.0"}

    @staticmethod
    def _match(row, domain):
        for field, operator, value in [item for item in domain if isinstance(item, list)]:
            current = row.get(field)
            current = current[0] if isinstance(current, list) else current
            if operator == "=" and current != value:
                return False
            if operator == "ilike" and str(value).lower() not in str(row.get(field)).lower():
                return False
        return True

    def execute_kw(self, db, uid, key, model, method, args, kwargs):
        self.methods.append(method)
        if model == "res.users" and method == "read":
            return [{"id": uid, "name": "Agent foydalanuvchi"}]
        rows = {"res.partner": self.PARTNERS, "sale.order": self.ORDERS}.get(model, [])
        if method == "fields_get":
            return {"name": {"string": "Nomi", "type": "char"}, "amount_total": {"string": "Jami", "type": "monetary"},
                    "partner_id": {"string": "Mijoz", "type": "many2one", "relation": "res.partner"}}
        found = [row for row in rows if self._match(row, args[0])]
        if method == "search_count":
            return len(found)
        if method == "search_read":
            return [{key: row.get(key) for key in ["id", *kwargs["fields"]]} for row in found][: kwargs["limit"]]
        if method == "read_group":
            groups: dict = {}
            for row in found:
                group = groups.setdefault(row["partner_id"][0], {"partner_id": row["partner_id"], "amount_total": 0.0,
                                                                 "__count": 0, "__domain": []})
                group["amount_total"] += row["amount_total"]
                group["__count"] += 1
            return list(groups.values())
        raise ValueError(f"{method} is not supported here")


@pytest.fixture
def odoo_server():
    class Handler(SimpleXMLRPCRequestHandler):
        rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")

    fake_odoo = FakeOdoo()
    server = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=Handler, allow_none=True, logRequests=False)
    for name in ("authenticate", "version", "execute_kw"):
        server.register_function(getattr(fake_odoo, name), name)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", fake_odoo
    server.shutdown()


def test_odoo_connection_check_and_read_only_tools(odoo_server):
    url, fake_odoo = odoo_server
    assert odoo_tools.normalize_url("https://x.odoo.com/odoo/") == "https://x.odoo.com"
    with pytest.raises(odoo_tools.OdooError, match="noto'g'ri"):
        odoo_tools.Odoo(url, "testdb", "agent@dunyabunya.uz", "wrong").check()
    odoo = odoo_tools.Odoo(url + "/odoo/", "testdb", "agent@dunyabunya.uz", "secret-key")
    assert odoo.check() == {"user": "Agent foydalanuvchi", "version": "18.0"}
    tools = {tool.name: tool for tool in odoo_tools.build(odoo, ("res.partner", "sale.order"))}
    found = tools["odoo_search"].handler({"model": "sale.order", "domain": '[["state", "=", "sale"]]',
                                          "fields": ["name", "partner_id", "amount_total"], "limit": 10})
    assert found["rows"] == 2 and found["records"][0]["partner_id"] == "Ali Qurilish (#1)"
    assert tools["odoo_count"].handler({"model": "res.partner", "domain": '[["name", "ilike", "vali"]]'})["count"] == 1
    groups = tools["odoo_group"].handler({"model": "sale.order", "domain": None, "aggregates": ["amount_total:sum"],
                                          "groupby": ["partner_id"]})["groups"]
    assert groups[0]["amount_total"] == 2000000.0 and "__domain" not in groups[0]
    assert "partner_id (many2one, res.partner)" in tools["odoo_fields"].handler({"model": "sale.order"})["fields"][-1]
    for bad, message in (({"model": "account.move"}, "not allowed"),
                         ({"model": "sale.order", "domain": '[["state", "exec", "x"]]'}, "not allowed"),
                         ({"model": "sale.order", "domain": "[('state', '=', 'sale')]"}, "JSON"),
                         ({"model": "sale.order", "domain": '[["state; drop", "=", 1]]'}, "bad field")):
        with pytest.raises(ToolError, match=message):
            tools["odoo_count"].handler(bad)
    with pytest.raises(odoo_tools.OdooError):
        odoo.execute("sale.order", "write", [[10], {"state": "cancel"}])
    assert set(fake_odoo.methods) <= {"read", "search_read", "search_count", "read_group", "fields_get"}


def test_a_created_agent_reads_odoo_through_its_encrypted_connection(fake, keys, odoo_server):
    url, _fake_odoo = odoo_server
    settings = load_settings()
    state.save_connection(settings, "k-00000001", "odoo", "agent @ testdb",
                          {"url": url, "db": "testdb", "login": "agent@dunyabunya.uz", "user": "Agent"},
                          secrets.protect("secret-key"))
    make_agent(settings, "ag-00000001", "Sotuv", groups=("odoo",), odoo="k-00000001")
    client = fake(call(("o1", "agent_ag_00000001", {"request": "Nechta mijoz bor?"})),
                  call(("c1", "odoo_count", {"model": "res.partner", "domain": None})),
                  say("Odoo'da 2 ta mijoz bor."))
    reply = orchestrator.handle(settings, "Odoo'da nechta mijoz bor?", [], context(), client=client)
    assert reply.text == "Odoo'da 2 ta mijoz bor."
    assert "Connected to Odoo" in system_text(client.requests[1]) and "secret-key" not in system_text(client.requests[1])
    content, is_error = tool_results(client.requests[2])[0]
    assert not is_error and json.loads(content)["count"] == 2
    state.set_connection_status(settings, "k-00000001", "error", "kalit eskirgan")
    agents = registry.load(settings)
    assert "NOT connected" in prompts.system("ag-00000001", settings, agents)


def test_secrets_are_encrypted_and_masked():
    blob = secrets.protect("123456:ABC-secret")
    assert b"ABC-secret" not in blob and secrets.unprotect(blob) == "123456:ABC-secret"
    assert secrets.mask("123456:ABC-secret") == "••••cret"
    with pytest.raises(secrets.SecretsUnavailable):
        secrets.unprotect(b"not a secret")


# --------------------------------------------------------------------------- Telegram (a fake server)

TOKEN = "123456789:AAEabcdefghijklmnopqrstuvwxyz012345678"


class FakeTelegram:
    def __init__(self) -> None:
        self.bots = {TOKEN: {"id": 555, "username": "sotuv_agent_bot", "first_name": "Sotuv"},
                     "987654321:AAEzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz": {"id": 777, "username": "dunyabunya_assistant_bot",
                                                                          "first_name": "Asosiy"}}
        self.webhooks: dict[str, str] = {}
        self.updates: list[dict] = []
        self.sent: list[tuple[int, str]] = []
        self.conflict = False

    def handle(self, token: str, method: str, params: dict):
        if token not in self.bots:
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        if method == "getMe":
            return 200, {"ok": True, "result": {**self.bots[token], "is_bot": True}}
        if method == "getWebhookInfo":
            return 200, {"ok": True, "result": {"url": self.webhooks.get(token, "")}}
        if method == "getUpdates":
            if self.conflict:
                return 409, {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates"}
            offset = params.get("offset") or 0
            pending = [update for update in self.updates if update["update_id"] >= offset]
            if not pending:
                time.sleep(0.2)
            return 200, {"ok": True, "result": pending}
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return 200, {"ok": True, "result": {"message_id": len(self.sent)}}
        return 200, {"ok": True, "result": True}


@pytest.fixture
def telegram_server(monkeypatch):
    fake_tg = FakeTelegram()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            _, bot, method = self.path.split("/", 2)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            status, payload = fake_tg.handle(bot.removeprefix("bot"), method, body)
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(tg, "API_BASE", f"http://127.0.0.1:{server.server_address[1]}")
    yield fake_tg
    server.shutdown()


def test_bot_tokens_are_checked_before_use(telegram_server):
    info = tg.check_token(TOKEN, ("dunyabunya_assistant_bot",))
    assert (info.id, info.username) == (555, "sotuv_agent_bot")
    with pytest.raises(tg.TelegramError, match="ishlab turgan bot"):
        tg.check_token("987654321:AAEzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", ("dunyabunya_assistant_bot",))
    telegram_server.webhooks[TOKEN] = "https://example.uz/hook"
    with pytest.raises(tg.TelegramError, match="webhook"):
        tg.check_token(TOKEN)
    with pytest.raises(tg.TelegramError, match="shakli"):
        tg.check_token("not-a-token")
    with pytest.raises(tg.TelegramError) as error:
        tg.check_token("111111111:AAEnot-known-tokennnnnnnnnnnnnnnnnnnnnnnn")
    assert error.value.code == 401
    assert tg.parse_allowed("42, @Ali_Vali  bad!name 7") == ([7, 42], ["ali_vali"])
    assert [len(part) for part in tg.split_text("a\n" * 3000)] == [3999, 1999]


def _bot_agent(settings) -> None:
    state.save_connection(settings, "k-00000009", "telegram", "@sotuv_agent_bot",
                          {"username": "sotuv_agent_bot", "bot_id": 555, "allowed_ids": [42], "allowed_names": ["vali"]},
                          secrets.protect(TOKEN))
    make_agent(settings, "ag-00000009", "Sotuv boti", telegram="k-00000009")


def _message(user_id: int, text: str, username: str = "") -> dict:
    return {"chat": {"id": user_id, "type": "private"}, "from": {"id": user_id, "first_name": "Test", "username": username},
            "text": text}


def test_the_agents_bot_answers_allowed_people_only(telegram_server, keys, monkeypatch):
    settings = load_settings()
    _bot_agent(settings)
    client = FakeOpenAI(say("Kecha 2 ta shikoyat bo'ldi."), say("Bugun hali yo'q."))
    monkeypatch.setattr(llm, "client_factory", lambda _settings: client)
    reader = agent_bots.BotReader(settings, "ag-00000009", "k-00000009", TOKEN)
    reader.handle(_message(42, "Kecha nechta shikoyat bo'ldi?"))
    assert telegram_server.sent[-1] == (42, "Kecha 2 ta shikoyat bo'ldi.")
    assert "Telegram bot, a private chat with Test" in last_message(client.requests[0])["content"]
    reader.handle(_message(5, "Bugun-chi?", username="Vali"))  # allowed by username
    assert telegram_server.sent[-1] == (5, "Bugun hali yo'q.")
    assert [item["role"] for item in state.bot_history(settings, "k-00000009", 42, 10)] == ["user", "assistant"]
    reader.handle(_message(99, "Salom"))
    reader.handle(_message(99, "Salom?"))
    denied = [text for chat_id, text in telegram_server.sent if chat_id == 99]
    assert len(denied) == 1 and "99" in denied[0]  # a stranger is told their ID once
    reader.handle(_message(42, "/start"))
    assert "Sotuv boti" in telegram_server.sent[-1][1]
    reader.handle(_message(42, "/new"))
    assert state.bot_history(settings, "k-00000009", 42, 10) == []
    reader.handle({"chat": {"id": -100, "type": "group"}, "from": {"id": 42}, "text": "guruhda"})
    assert telegram_server.sent[-1][0] != -100  # groups are ignored
    assert not client.script


def test_a_bot_used_by_another_program_is_stopped_not_fought_over(telegram_server, keys):
    settings = load_settings()
    _bot_agent(settings)
    telegram_server.conflict = True
    reader = agent_bots.BotReader(settings, "ag-00000009", "k-00000009", TOKEN)
    reader.run()  # returns by itself after the 409
    assert state.connection(settings, "k-00000009")["status"] == "conflict"
    assert agent_bots.wanted(settings) == {}  # not started again until «Qayta ulash»


def test_sync_runs_one_reader_per_bot(telegram_server, keys):
    settings = load_settings()
    _bot_agent(settings)
    readers, stopping = {}, {}
    agent_bots.sync(settings, readers, stopping)
    agent_bots.sync(settings, readers, stopping)
    assert list(readers) == ["k-00000009"] and readers["k-00000009"].is_alive()
    state.delete_custom_agent(settings, "ag-00000009")  # deleting the agent deletes its bot connection
    assert state.connection(settings, "k-00000009") is None
    agent_bots.sync(settings, readers, stopping)
    assert readers == {} and list(stopping) == ["k-00000009"]
    stopping["k-00000009"].join(timeout=5)
    assert not stopping["k-00000009"].is_alive()


# --------------------------------------------------------------------------- the page


def _press(app, label: str):
    return next(button for button in app.button if button.label == label).click().run()


def test_the_page_creates_an_agent_from_a_described_task(keys, monkeypatch):
    settings = load_settings()
    client = FakeOpenAI(call(("b1", "submit_design", _design(telegram_bot=False, tool_groups=["data"], missing=[],
                                                              questions=["Qaysi davr uchun?"]))),
                        call(("b2", "submit_design", _design(telegram_bot=False, tool_groups=["data"], missing=[]))))
    monkeypatch.setattr(llm, "client_factory", lambda _settings: client)
    app = run_page("agents", {"a": "new"})
    assert_clean(app)
    app.text_input(key="agnew-name").set_value("Qarzdorlar")
    app.text_area(key="agnew-task").set_value("qisqa")
    _press(app, "Tahlil qilish")
    assert "to'liqroq" in app.warning[0].value and not client.requests  # too short: no model call
    app.text_area(key="agnew-task").set_value(TASK)
    _press(app, "Tahlil qilish")
    assert_clean(app)
    assert len(client.requests) == 1 and "AI taklifi" in [header.value for header in app.subheader]
    assert app.selectbox(key="agd1-model").value == "gpt-5.4-mini"

    # the designer's question is answered right there; the answer goes into the task, analysed again
    assert app.text_input(key="agd1-answer-0").label == "Qaysi davr uchun?"
    _press(app, "Javoblar bilan qayta tahlil qilish")
    assert "Kamida bitta" in app.warning[0].value and len(client.requests) == 1  # no answer: no model call
    app.text_input(key="agd1-answer-0").set_value("Oxirgi 7 kun")
    _press(app, "Javoblar bilan qayta tahlil qilish")
    assert_clean(app)
    assert len(client.requests) == 2 and "Qaysi davr uchun? — Oxirgi 7 kun" in last_message(client.requests[1])["content"]
    assert app.text_area(key="agnew-task").value.endswith("- Qaysi davr uchun? — Oxirgi 7 kun")
    assert app.selectbox(key="agd2-model").value == "gpt-5.4-mini"  # a fresh proposal, no questions left
    assert not [box for box in app.text_input if box.key and box.key.startswith("agd2-answer")]

    _press(app, "Agentni yaratish")
    assert_clean(app)
    created = state.custom_agents(settings)
    assert len(created) == 1 and created[0]["name"] == "Qarzdorlar" and created[0]["tool_groups"] == ["data"]
    assert created[0]["task"].endswith("Oxirgi 7 kun")
    assert app.query_params["a"] == created[0]["id"] and "yaratildi" in app.success[0].value
    assert "Sinab ko'rish" in [header.value for header in app.subheader]  # the new agent can be tried at once
    assert prompts.instructions(created[0]["id"], settings).startswith("You are «Qarzdorlar»")


def test_examples_fill_the_form_and_a_taken_name_is_refused(keys, monkeypatch):
    settings = load_settings()
    client = FakeOpenAI()
    monkeypatch.setattr(llm, "client_factory", lambda _settings: client)
    app = run_page("agents", {"a": "new"})
    assert_clean(app)
    app.button(key="agtpl-odoo").click().run()
    assert app.text_input(key="agnew-name").value == "Odoo savdo yordamchisi"
    assert "Odoo" in app.text_area(key="agnew-task").value and not client.requests
    make_agent(settings, "ag-00000001", "Odoo savdo yordamchisi")
    _press(app, "Tahlil qilish")
    assert "allaqachon bor" in app.warning[0].value and not client.requests


def test_try_an_agent_on_its_page(keys, monkeypatch):
    settings = load_settings()
    make_agent(settings, "ag-00000001", "Shikoyatlar", groups=("data",))
    client = FakeOpenAI(call(("t1", "overview", {"period": "last7", "date_from": None, "date_to": None, "group": None})),
                        say("Oxirgi 7 kunda 4 ta shikoyat bo'ldi."), say("Kecha 1 ta."))
    monkeypatch.setattr(llm, "client_factory", lambda _settings: client)
    app = run_page("agents", {"a": "ag-00000001"})
    assert_clean(app)
    _press(app, "Yuborish")
    assert "Avval savol" in app.warning[0].value and not client.requests  # nothing written: no model call
    app.text_area(key="agtry-text-ag-00000001").set_value("Shikoyatlar qancha?")
    _press(app, "Yuborish")
    assert_clean(app)
    assert len(client.requests) == 2 and "overview" in tool_names(client.requests[0])
    assert system_text(client.requests[0]).startswith("You are the test agent.")  # its own prompt, not the chat's
    assert [message.markdown[0].value for message in app.chat_message] == ["Shikoyatlar qancha?",
                                                                           "Oxirgi 7 kunda 4 ta shikoyat bo'ldi."]
    assert "vositalar: overview" in app.chat_message[1].caption[0].value
    app.text_area(key="agtry-text-ag-00000001").set_value("Kechachi?")
    _press(app, "Yuborish")
    assert "Shikoyatlar qancha?" in str(client.requests[2]["messages"])  # the earlier turn is remembered
    assert len(app.chat_message) == 4 and state.chat_recent(settings, 10) == []  # never stored in the chat
    _press(app, "Tozalash")
    assert len(app.chat_message) == 0


def test_a_changed_task_rewrites_the_prompt_and_keeps_the_old_one(keys, monkeypatch):
    settings = load_settings()
    make_agent(settings, "ag-00000001", "Shikoyatlar", groups=("data",))
    new_prompt = "You are «Shikoyatlar». Compare complaints week by week."
    client = FakeOpenAI(call(("b1", "submit_design", _design(summary="Haftalik shikoyatlar.", tool_groups=["data", "view"],
                                                              telegram_bot=False, system_prompt=new_prompt))))
    monkeypatch.setattr(llm, "client_factory", lambda _settings: client)
    app = run_page("agents", {"a": "ag-00000001"})
    task_box = next(box for box in app.text_area if box.value == "test")
    task_box.set_value("Shikoyatlarni haftama-hafta solishtirib, o'sish bo'lsa ogohlantirsin va sababini aytsin.")
    _press(app, "AI bilan promptni qayta yozish")
    assert_clean(app)
    assert len(client.requests) == 1 and "haftama-hafta" in last_message(client.requests[0])["content"]
    assert "Agents created earlier (avoid duplicating them):\n- none" in last_message(client.requests[0])["content"]
    assert any("Dashboard ko'rinishi" in caption.value for caption in app.caption)  # the suggested tools differ
    assert prompts.instructions("ag-00000001", settings) == "You are the test agent. Answer briefly."  # not yet
    _press(app, "Qabul qilish va saqlash")
    assert_clean(app)
    assert prompts.instructions("ag-00000001", settings) == new_prompt
    record = state.custom_agent(settings, "ag-00000001")
    assert record["task"].startswith("Shikoyatlarni haftama-hafta") and record["summary"] == "Haftalik shikoyatlar."
    assert record["tool_groups"] == ["data"]  # tools change only in «Agent sozlamalari»
    assert prompts.default_instructions("ag-00000001", settings) == "You are the test agent. Answer briefly."
    assert "saqlandi" in app.success[0].value


def test_the_tree_invites_to_create_the_first_agent():
    app = run_page("agents")
    assert_clean(app)
    assert any("Hali o'zingiz yaratgan agent yo'q" in item.value for item in app.info)


def test_the_page_changes_models_and_deletes_an_agent(keys):
    settings = load_settings()
    make_agent(settings, "ag-00000001", "Sotuv")
    make_agent(settings, "ag-00000002", "Qarzdorlar", parent="ag-00000001", groups=("odoo",))
    tree = run_page("agents")
    assert_clean(tree)
    names = [header.value.replace("\\", "") for header in tree.subheader]
    assert any("Qarzdorlar" in name for name in names) and any("Sotuv" in name for name in names)
    app = run_page("agents", {"a": "data"})
    app.selectbox(key="agmodel-data").set_value("gpt-5.5").run()
    assert_clean(app)
    assert state.agent_model(settings, "data") == "gpt-5.5"
    app = run_page("agents", {"a": "ag-00000001"})
    assert_clean(app)
    app.selectbox(key="agmodel-ag-00000001").set_value("gpt-5.4-nano").run()
    assert state.custom_agent(settings, "ag-00000001")["model"] == "gpt-5.4-nano"
    app.checkbox(key="agdel-ok-ag-00000001").check().run()
    next(button for button in app.button if button.key == "agdel-ag-00000001").click().run()
    assert_clean(app)
    assert state.custom_agent(settings, "ag-00000001") is None
    assert state.custom_agent(settings, "ag-00000002")["parent"] == "orchestrator"
    assert "a" not in app.query_params
    assert_clean(run_page("agents", {"a": "ag-00000002"}))  # an Odoo agent without a connection still renders
