"""P0-3: odoo_search_read/odoo_read_group/odoo_count are BASE_TOOLS handed to
every role and accept an arbitrary `model` string — without an explicit check,
a Sotuv (sales) manager could call odoo_search_read(model="account.move", ...)
and read Moliya's financial records, even though their ROLE_PROMPTS text says
"boshqa bo'limlarga kirish huquqingiz yo'q" (that text is an instruction to
the LLM, not a technical control). These tests exercise the real
`_exec_tool` directly to prove the model allowlist is actually enforced.

Barcha testlar mok'langan Odoo bilan ishlaydi — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import json

import pytest

import app.services.assistant as assistant_module
from app.rbac import Role


async def test_sotuv_cannot_read_finance_model(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        raise AssertionError(f"Odoo'ga chiqilmasligi kerak edi (model={model})")

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)

    raw = await assistant_module._exec_tool(
        "odoo_search_read",
        {"model": "account.move", "domain": [], "fields": ["id", "name"]},
        Role.SOTUV,
    )
    result = json.loads(raw)
    assert "error" in result


async def test_sotuv_can_read_own_model(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        assert model == "sale.order"
        return [{"id": 1, "name": "S00001"}]

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)

    raw = await assistant_module._exec_tool(
        "odoo_search_read",
        {"model": "sale.order", "domain": [], "fields": ["id", "name"]},
        Role.SOTUV,
    )
    result = json.loads(raw)
    assert result == [{"id": 1, "name": "S00001"}]


async def test_hr_cannot_read_sales_model(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        raise AssertionError(f"Odoo'ga chiqilmasligi kerak edi (model={model})")

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)

    raw = await assistant_module._exec_tool(
        "odoo_count", {"model": "sale.order", "domain": []}, Role.HR,
    )
    assert "error" in json.loads(raw)


async def test_hr_can_read_employee_model(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        assert model == "hr.employee"
        return 42

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)

    raw = await assistant_module._exec_tool(
        "odoo_count", {"model": "hr.employee", "domain": []}, Role.HR,
    )
    assert json.loads(raw) == 42


async def test_ceo_unrestricted_across_any_model(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return [{"id": 1}]

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)

    for model in ("account.move", "sale.order", "hr.employee", "stock.quant"):
        raw = await assistant_module._exec_tool(
            "odoo_search_read", {"model": model, "domain": [], "fields": ["id"]}, Role.CEO,
        )
        assert "error" not in json.loads(raw)


async def test_role_none_rejects_every_model(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        raise AssertionError("Role.NONE Odoo'ga hech qachon chiqmasligi kerak")

    monkeypatch.setattr(assistant_module._odoo, "execute_kw", fake_execute_kw)

    raw = await assistant_module._exec_tool(
        "odoo_read_group",
        {"model": "hr.employee", "domain": [], "fields": [], "group_by": []},
        Role.NONE,
    )
    assert "error" in json.loads(raw)


async def test_non_base_tools_are_unaffected_by_model_restriction(monkeypatch):
    """Rolga xos tool'lar (masalan check_partner_debt) model-cheklovga tegishli
    emas — ular ALL_TOOLS orqali o'ziga xos parametrlar bilan ishlaydi."""
    async def fake_check_partner_debt(partner_id):
        return {"partner_id": partner_id, "total_debt": 0}

    monkeypatch.setitem(assistant_module.ALL_TOOLS, "check_partner_debt", fake_check_partner_debt)

    raw = await assistant_module._exec_tool("check_partner_debt", {"partner_id": 5}, Role.SOTUV)
    result = json.loads(raw)
    assert result["partner_id"] == 5
