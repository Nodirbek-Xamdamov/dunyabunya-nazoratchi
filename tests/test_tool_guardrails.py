"""`_exec_tool` himoyalari: rol ruxsati (har qanday `model` qabul qiluvchi tool uchun) va
uzun natijani model uchun to'g'ri qisqartirish.

Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import json

import pytest

import app.services.assistant as assistant_module
import app.services.odoo_tools as odoo_tools
from app.rbac import Role


def _forbid_odoo(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        raise AssertionError(f"Odoo'ga chiqilmasligi kerak edi (model={model})")

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)
    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)


@pytest.mark.parametrize("tool", ["export_excel", "export_word", "export_pdf", "export_csv",
                                  "export_text", "generate_html_report"])
async def test_export_tools_respect_role_model_restriction(monkeypatch, tool):
    """P0-3 ning ochiq qolgan qismi: HR menejeri export_excel(model="account.move") orqali
    Moliya ma'lumotini fayl qilib olib chiqa olardi (cheklov faqat 4 ta toolda bor edi)."""
    _forbid_odoo(monkeypatch)
    raw = await assistant_module._exec_tool(tool, {"model": "account.move", "fields": ["name"]}, Role.HR)
    assert "kira olmaydi" in json.loads(raw)["error"]


async def test_tool_outside_the_role_list_is_rejected(monkeypatch):
    """Model ro'yxatda yo'q tool nomini yuborsa ham (masalan Sotuv menejeri uchun to'lov
    qoralamasi) — bajarilmaydi."""
    called = []

    async def fake_create_payment_draft(**kwargs):
        called.append(kwargs)
        return 1

    monkeypatch.setitem(assistant_module.ALL_TOOLS, "create_payment_draft", fake_create_payment_draft)
    raw = await assistant_module._exec_tool(
        "create_payment_draft", {"partner_id": 5, "amount": 100}, Role.SOTUV)

    assert "ruxsat etilmagan" in json.loads(raw)["error"] and called == []
    # O'z ro'yxatidagi rol uchun ishlaydi
    assert json.loads(await assistant_module._exec_tool(
        "create_payment_draft", {"partner_id": 5, "amount": 100}, Role.MOLIYA)) == 1


async def test_role_none_cannot_run_any_role_tool(monkeypatch):
    _forbid_odoo(monkeypatch)
    raw = await assistant_module._exec_tool("search_payments", {}, Role.NONE)
    assert "error" in json.loads(raw)


async def test_long_list_result_is_cut_at_row_boundary_with_a_warning(monkeypatch):
    """Ilgari JSON 8000-belgida shunchaki kesilardi — model yarim ro'yxatni to'liq deb o'ylardi."""
    rows = [{"id": i, "name": f"Xodim {i:04d} " + "x" * 80} for i in range(400)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        return rows

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)
    raw = await assistant_module._exec_tool(
        "odoo_search_read", {"model": "hr.employee", "domain": [], "fields": ["name"], "limit": 500}, Role.CEO)

    assert len(raw) <= assistant_module._TOOL_RESULT_MAX_CHARS
    result = json.loads(raw)  # kesilgan bo'lsa ham to'g'ri JSON
    assert result["jami_qaytgan"] == 400 and 0 < result["korsatilgan"] < 400
    assert result["qatorlar"] == rows[:result["korsatilgan"]]
    assert "odoo_count" in result["ogohlantirish"]


async def test_search_read_hitting_its_limit_warns_that_more_may_exist(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return [{"id": i} for i in range(kwargs["limit"])]

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)
    raw = await assistant_module._exec_tool(
        "odoo_search_read", {"model": "hr.employee", "domain": [], "fields": ["id"]}, Role.CEO)

    result = json.loads(raw)
    assert result["korsatilgan"] == 50 and "Limit (50)" in result["ogohlantirish"]


async def test_read_group_with_two_groups_is_not_lazy(monkeypatch):
    seen = {}

    async def fake_execute_kw(model, method, args, kwargs=None):
        seen.update(kwargs or {})
        return []

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)
    await assistant_module._exec_tool(
        "odoo_read_group",
        {"model": "account.payment", "domain": [], "fields": ["amount"],
         "group_by": ["currency_id", "payment_type"]},
        Role.CEO,
    )
    assert seen == {"lazy": False}


async def test_history_starting_with_assistant_message_is_trimmed(monkeypatch):
    """Anthropic API birinchi xabar "user" bo'lishini talab qiladi."""
    import types

    history = [types.SimpleNamespace(role="assistant", content="eski javob"),
               types.SimpleNamespace(role="user", content="savol"),
               types.SimpleNamespace(role="assistant", content="javob")]

    async def fake_get_recent(session, uid):
        return history

    async def fake_add(*a, **kw):
        return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    seen = {}

    async def fake_create(**kwargs):
        seen["roles"] = [m["role"] for m in kwargs["messages"]]
        return types.SimpleNamespace(stop_reason="end_turn",
                                     content=[types.SimpleNamespace(type="text", text="Salom.")])

    monkeypatch.setattr(assistant_module.repo, "get_recent_conversation", fake_get_recent)
    monkeypatch.setattr(assistant_module.repo, "add_conversation_message", fake_add)
    monkeypatch.setattr(assistant_module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(assistant_module._client.messages, "create", fake_create)

    await assistant_module.ask_with_files(1, "salom")
    assert seen["roles"] == ["user", "assistant", "user"]
