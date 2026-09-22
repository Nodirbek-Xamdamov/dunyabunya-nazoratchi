"""app/services/assistant.py — fayl (Excel/HTML) generatsiyasi majburlanishi va
xato-diagnostikasi uchun testlar.

Ilgari: foydalanuvchi Excel/HTML tugmasini bosganda, Claude'ga tool chaqirish
shunchaki TAVSIYA qilinardi (majburlanmasdi) — model ba'zan hech qanday tool
chaqirmasdan faqat matn bilan javob berib, fayl umuman yaratilmasdi va sababi
hech qayerda ko'rsatilmasdi ("Fayl yaratishning iloji bo'lmadi" degan umumiy xabar).

Bu fayldagi testlar ikkalasini ham qamrab oladi: (1) tool_choice/force_tools
haqiqatan Anthropic so'roviga uzatilishi, (2) fayl topilmasa sababi
AssistantResult.file_error orqali chiqishi.

Barcha testlar mok'langan Anthropic/Odoo bilan ishlaydi — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

import app.services.assistant as assistant_module
from app.rbac import Role


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, args, tid="t1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=args, id=tid)


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch):
    """get_recent_conversation/add_conversation_message DB'ga chiqmasin — sof mantiq testi."""
    async def fake_get_recent(session, uid):
        return []

    async def fake_add(*a, **kw):
        return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(assistant_module.repo, "get_recent_conversation", fake_get_recent)
    monkeypatch.setattr(assistant_module.repo, "add_conversation_message", fake_add)
    monkeypatch.setattr(assistant_module, "SessionLocal", lambda: _FakeSession())
    yield


def _mock_create(monkeypatch, responses):
    """messages.create() ni ketma-ket berilgan javoblar bilan mock qiladi, har
    chaqiruvning kwargs'ini calls ro'yxatida saqlaydi."""
    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        idx = len(calls) - 1
        return responses[idx] if idx < len(responses) else responses[-1]

    monkeypatch.setattr(assistant_module._client.messages, "create", AsyncMock(side_effect=fake_create))
    return calls


# ── tool_choice majburlanishi ────────────────────────────────────────────────

async def test_force_tools_narrows_tools_list_to_requested_names(monkeypatch):
    calls = _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("export_excel", {"model": "hr.employee"})]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Hisobot tayyorlandi: http://x/reports/f.xlsx")]),
    ])

    async def fake_exec_tool(name, args):
        return '"http://x/reports/f.xlsx"'

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)

    await assistant_module.ask_with_files(
        1, "180 ta xodim ro'yxatini excel qil", role=Role.CEO,
        force_tools=["export_excel"],
        tool_choice={"type": "tool", "name": "export_excel"},
    )

    assert len(calls) == 2
    tool_names_iter0 = {t["name"] for t in calls[0]["tools"]}
    assert tool_names_iter0 == {"export_excel"}


async def test_tool_choice_only_forced_on_first_iteration(monkeypatch):
    """Majburlash faqat 1-chaqiriqda bo'lishi shart — aks holda Claude tool
    natijasini ko'rib matn bilan yakunlay olmay, tool'ni cheksiz qayta chaqirar edi."""
    calls = _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("generate_html_report", {"model": "sale.order"})]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Hisobot tayyorlandi: http://x/reports/g.html")]),
    ])

    async def fake_exec_tool(name, args):
        return '"http://x/reports/g.html"'

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)

    await assistant_module.ask_with_files(
        1, "hisobotni html qil", role=Role.CEO,
        force_tools=["generate_html_report", "generate_sales_report"],
        tool_choice={"type": "any"},
    )
    assert calls[0].get("tool_choice") == {"type": "any"}
    assert "tool_choice" not in calls[1]


async def test_force_tools_falls_back_to_full_list_if_role_has_none_of_them(monkeypatch):
    """Agar rolga forced tool'lardan birortasi ham ruxsat berilmagan bo'lsa
    (masalan xato konfiguratsiya), bo'sh tools=[] yuborilib Anthropic API'ni
    rad ettirmaslik uchun to'liq ro'yxatga qaytiladi."""
    calls = _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("javob")]),
    ])
    await assistant_module.ask_with_files(
        1, "savol", role=Role.SOTUV,
        force_tools=["bunday_tool_yoq"],
        tool_choice={"type": "any"},
    )
    assert len(calls[0]["tools"]) > 0


async def test_no_tool_choice_by_default_normal_conversation(monkeypatch):
    """Oddiy (formatga majburlanmagan) so'rovlarda tool_choice umuman yuborilmasligi
    kerak — model erkin (auto) qaror qiladi."""
    calls = _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Jami 40 ta faktura mavjud")]),
    ])
    await assistant_module.ask_with_files(1, "nechta faktura bor?", role=Role.CEO)
    assert "tool_choice" not in calls[0]


# ── file_error diagnostikasi ─────────────────────────────────────────────────

async def test_file_error_captured_when_tool_returns_error_dict(monkeypatch):
    """Tool ichida haqiqiy xato (masalan Odoo ulanish xatosi) bo'lsa — sababi
    AssistantResult.file_error orqali chiqishi shart (ilgari umuman ko'rinmasdi)."""
    calls = _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("export_excel", {"model": "hr.employee"})]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Kechirasiz, hozircha imkoni bo'lmadi.")]),
    ])

    async def fake_exec_tool(name, args):
        import json
        return json.dumps({"error": "Odoo 30s ichida javob bermadi (timeout)"})

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)

    result = await assistant_module.ask_with_files(
        1, "excel qil", role=Role.CEO,
        force_tools=["export_excel"], tool_choice={"type": "tool", "name": "export_excel"},
    )
    assert result.files == []
    assert result.file_error is not None
    assert "timeout" in result.file_error.lower() or "export_excel" in result.file_error


async def test_file_error_captured_when_tool_returns_not_found_string(monkeypatch):
    _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("export_excel", {"model": "hr.employee"})]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block("Ma'lumot topilmadi.")]),
    ])

    async def fake_exec_tool(name, args):
        import json
        return json.dumps("Bu so'rov bo'yicha ma'lumot topilmadi.")

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)
    result = await assistant_module.ask_with_files(
        1, "excel qil", role=Role.CEO,
        force_tools=["export_excel"], tool_choice={"type": "tool", "name": "export_excel"},
    )
    assert result.files == []
    assert result.file_error is not None


async def test_no_file_error_when_file_successfully_produced(monkeypatch, tmp_path):
    """Fayl haqiqatan yaratilsa — file_error bo'sh (None) bo'lishi kerak."""
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    fname = "hisobot_abc123.xlsx"
    (report_dir / fname).write_bytes(b"fake xlsx bytes")

    monkeypatch.setattr(assistant_module, "REPORTS_DIR", report_dir)

    base = assistant_module.settings.PUBLIC_BASE_URL.rstrip("/")
    url = f"{base}/reports/{fname}"

    _mock_create(monkeypatch, [
        types.SimpleNamespace(stop_reason="tool_use", content=[_tool_block("export_excel", {"model": "hr.employee"})]),
        types.SimpleNamespace(stop_reason="end_turn", content=[_text_block(f"Hisobot tayyorlandi: {url}")]),
    ])

    async def fake_exec_tool(name, args):
        import json
        return json.dumps(url)

    monkeypatch.setattr(assistant_module, "_exec_tool", fake_exec_tool)
    result = await assistant_module.ask_with_files(
        1, "excel qil", role=Role.CEO,
        force_tools=["export_excel"], tool_choice={"type": "tool", "name": "export_excel"},
    )
    assert len(result.files) == 1
    assert result.file_error is None
