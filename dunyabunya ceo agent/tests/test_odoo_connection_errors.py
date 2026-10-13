"""Odoo bilan aloqa yo'qligining ANIQ sababi foydalanuvchiga yetib borishi uchun testlar.

Ilgari: har qanday ulanish xatosi (jumladan DNS — manzil umuman yo'q) "Odoo 30s ichida
javob bermadi (timeout)" deb yozilardi, model esa buni "timeout xatosi" deb qisqartirib,
"nima uchun aloqa yo'q?" savoliga "Buni qila olmayman" deb javob berardi.

Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import json
import socket
import ssl
import types
import xmlrpc.client
from unittest.mock import AsyncMock

import pytest

import app.services.assistant as assistant_module
from app.rbac import Role
from app.services.odoo import OdooClient, OdooError


@pytest.fixture
def client():
    return OdooClient(
        url="https://dunyabunya-test-1.dev.odoo.com",
        db="test_db", login="test@test.com", api_key="test-key", timeout=7,
    )


def _failing_common(exc):
    class _FakeCommonProxy:
        def authenticate(self, *a, **kw):
            raise exc
    return _FakeCommonProxy()


# ── OdooClient: xato turi va sababi ─────────────────────────────────────────

@pytest.mark.parametrize("exc, kind, fragment", [
    (socket.gaierror(11004, "getaddrinfo failed"), "dns", "dunyabunya-test-1.dev.odoo.com"),
    (TimeoutError("timed out"), "timeout", "7s ichida javob bermadi"),
    (ConnectionRefusedError(10061, "refused"), "refused", "rad etdi"),
    (ssl.SSLError("CERTIFICATE_VERIFY_FAILED"), "ssl", "SSL"),
    (OSError(10051, "network unreachable"), "network", "tarmoq"),
    (xmlrpc.client.ProtocolError("h/xmlrpc/2/common", 402, "Payment Required", {}), "http", "obunasi"),
    (xmlrpc.client.ProtocolError("h/xmlrpc/2/common", 503, "Service Unavailable", {}), "http", "HTTP 503"),
])
def test_authenticate_failure_is_classified_with_its_real_reason(client, exc, kind, fragment):
    client._local.common_proxy = _failing_common(exc)
    with pytest.raises(OdooError) as err:
        client._get_uid()
    assert err.value.kind == kind
    assert fragment in str(err.value)


def test_dns_failure_is_not_reported_as_timeout(client):
    """Aynan shu holat prod'da yuz bergan: Odoo.sh bazasi qayta qurilib, eski manzil yo'qolgan."""
    client._local.common_proxy = _failing_common(socket.gaierror(11004, "getaddrinfo failed"))
    with pytest.raises(OdooError) as err:
        client._get_uid()
    assert "timeout" not in str(err.value).lower()
    assert "DNS" in str(err.value)


def test_wrong_credentials_are_reported_as_auth_error(client):
    class _FakeCommonProxy:
        def authenticate(self, *a, **kw):
            return False

    client._local.common_proxy = _FakeCommonProxy()
    with pytest.raises(OdooError) as err:
        client._get_uid()
    assert err.value.kind == "auth"
    assert "API kalit" in str(err.value)


def test_odoo_side_fault_has_no_connection_kind(client):
    """Odoo so'rovni qabul qilib rad etsa (noto'g'ri field) — bu aloqa xatosi EMAS."""
    client._local.uid = 1

    class _FakeObjectProxy:
        def execute_kw(self, *a, **kw):
            raise xmlrpc.client.Fault(1, "Invalid field 'is_closed' on model 'project.task'")

    class _FakeCommonProxy:
        def authenticate(self, *a, **kw):
            return 1

    client._local.object_proxy = _FakeObjectProxy()
    client._local.common_proxy = _FakeCommonProxy()
    with pytest.raises(OdooError) as err:
        client._execute_kw_sync("project.task", "search_read", [[]])
    assert err.value.kind is None


# ── _exec_tool: aloqa xatosi sabab + tavsiya bilan qaytadi ──────────────────

async def test_exec_tool_returns_connection_reason_and_hint(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        raise OdooError("Odoo manzili (x.dev.odoo.com) internetda topilmadi (DNS xatosi)", kind="dns")

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)
    raw = await assistant_module._exec_tool(
        "odoo_count", {"model": "hr.employee", "domain": []}, Role.CEO,
    )
    result = json.loads(raw)
    assert result["odoo_connection"] == "dns"
    assert "DNS" in result["error"]
    assert "ODOO_URL" in result["tavsiya"]


async def test_check_odoo_connection_tool_reports_live_status(monkeypatch):
    async def fake_diagnose():
        return {"aloqa": False, "server": "x.dev.odoo.com", "xato_turi": "dns", "sabab": "DNS xatosi"}

    monkeypatch.setattr(assistant_module._odoo, "diagnose", fake_diagnose)
    raw = await assistant_module._exec_tool("check_odoo_connection", {}, Role.SOTUV)
    assert json.loads(raw)["sabab"] == "DNS xatosi"


def test_check_odoo_connection_is_offered_to_every_role():
    for role in (Role.CEO, Role.MOLIYA, Role.OMBOR, Role.SOTUV, Role.HR):
        names = {t["name"] for t in assistant_module._get_tools_for_role(role)}
        assert "check_odoo_connection" in names


# ── ask_with_files: aloqa yo'q bo'lsa sabab to'g'ridan-to'g'ri foydalanuvchiga ──

def _tool_block(name, args, tid="t1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=args, id=tid)


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


@pytest.fixture
def _no_real_db(monkeypatch):
    async def fake_get_recent(session, uid):
        return []

    saved = []

    async def fake_add(session, uid, role, content):
        saved.append((role, content))

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(assistant_module.repo, "get_recent_conversation", fake_get_recent)
    monkeypatch.setattr(assistant_module.repo, "add_conversation_message", fake_add)
    monkeypatch.setattr(assistant_module, "SessionLocal", lambda: _FakeSession())
    return saved


def _mock_create(monkeypatch, responses):
    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        idx = len(calls) - 1
        return responses[idx] if idx < len(responses) else responses[-1]

    monkeypatch.setattr(assistant_module, "_call_model", AsyncMock(side_effect=fake_create))
    return calls


async def test_unreachable_odoo_gives_full_reason_without_second_model_call(monkeypatch, _no_real_db):
    calls = _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("odoo_search_read", {})]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Buni qila olmayman.")]),
    ])

    async def fake_exec_tool(name, args, role=None):
        return json.dumps({"error": "Odoo manzili (x.dev.odoo.com) internetda topilmadi (DNS xatosi)",
                           "odoo_connection": "dns", "tavsiya": "ODOO_URL ni yangilash kerak."})

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)
    result = await assistant_module.ask_with_files(1, "xodimlar ro'yxati", role=Role.CEO)

    assert len(calls) == 1  # model "Buni qila olmayman" deyishiga imkon berilmadi
    assert result.odoo_error == "dns"
    assert "aloqa yo'q" in result.text
    assert "Sabab: Odoo manzili (x.dev.odoo.com) internetda topilmadi (DNS xatosi)" in result.text
    assert "ODOO_URL ni yangilash kerak." in result.text
    assert assistant_module._odoo.host in result.text
    # Keyingi "nega aloqa yo'q?" savoli uchun sabab suhbat tarixida ham qoladi
    assert _no_real_db[-1] == ("assistant", result.text)


async def test_partial_failure_lets_the_model_continue(monkeypatch, _no_real_db):
    """Bir so'rov yiqilib, boshqasi natija bergan bo'lsa — model javobni o'zi yakunlaydi."""
    calls = _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[
            _tool_block("odoo_count", {}, "t1"), _tool_block("odoo_read_group", {}, "t2"),
        ]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Jami 187 ta xodim.")]),
    ])

    async def fake_exec_tool(name, args, role=None):
        if name == "odoo_count":
            return "187"
        return json.dumps({"error": "Odoo 30s ichida javob bermadi (timeout)",
                           "odoo_connection": "timeout", "tavsiya": "Qayta urinib ko'ring."})

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)
    result = await assistant_module.ask_with_files(1, "nechta xodim bor?", role=Role.CEO)

    assert len(calls) == 2
    assert result.odoo_error is None
    assert result.text == "Jami 187 ta xodim."


# ── on_tool_start: "tayyorlanmoqda" faqat Odoo'ga murojaat boshlanganda ─────

async def test_on_tool_start_not_called_for_plain_chat(monkeypatch, _no_real_db):
    _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Va alaykum assalom.")]),
    ])
    started = []

    async def on_tool_start():
        started.append(True)

    result = await assistant_module.ask_with_files(1, "Assalomu alaykum", role=Role.CEO,
                                                   on_tool_start=on_tool_start)
    assert started == []
    assert result.text == "Va alaykum assalom."


async def test_on_tool_start_called_once_before_first_tool_runs(monkeypatch, _no_real_db):
    _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("odoo_count", {}, "t1")]),
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("odoo_count", {}, "t2")]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Jami 187 ta xodim.")]),
    ])
    events = []

    async def on_tool_start():
        events.append("notify")

    async def fake_exec_tool(name, args, role=None):
        events.append("tool")
        return "187"

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)
    await assistant_module.ask_with_files(1, "nechta xodim bor?", role=Role.CEO,
                                          on_tool_start=on_tool_start)
    assert events == ["notify", "tool", "tool"]
