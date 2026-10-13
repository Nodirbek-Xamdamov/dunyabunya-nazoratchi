"""app.bot.handlers.monitor_admin -- /test_report: faqat adminlar, faqat shaxsiy chat yoki hisobot guruhida.
app.llm_client -- modelga so'rov: lokal Ollama / OpenAI, xatolar."""

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest

from app import llm_client, monitor_db, monitor_report
from app.bot.handlers import monitor_admin
from app.llm_client import LLMUnavailable

REPORT_CHAT = -1009990000001
ADMIN_ID, GROUP_ADMIN_ID, STRANGER_ID = 555, 777, 999


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    settings = monitor_admin.settings
    monkeypatch.setattr(settings, "REPORT_CHAT_ID", REPORT_CHAT)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", ADMIN_ID)
    monkeypatch.setattr(settings, "ADMIN_USER_IDS", "556, 557")


def _bot():
    bot = MagicMock()

    async def get_chat_member(chat_id, user_id):
        if user_id == STRANGER_ID:
            raise TelegramBadRequest(method=MagicMock(), message="user not found")
        return SimpleNamespace(status="administrator" if user_id == GROUP_ADMIN_ID else "member")

    bot.get_chat_member = AsyncMock(side_effect=get_chat_member)
    return bot


def _message(bot, user_id, chat_id, chat_type="private"):
    return SimpleNamespace(
        bot=bot, from_user=SimpleNamespace(id=user_id), chat=SimpleNamespace(id=chat_id, type=chat_type), reply=AsyncMock()
    )


@pytest.mark.parametrize("user_id, allowed", [
    (ADMIN_ID, True), (556, True), (557, True), (GROUP_ADMIN_ID, True), (888, False), (STRANGER_ID, False),
])
async def test_only_configured_admins_and_report_group_admins_are_allowed(user_id, allowed):
    assert await monitor_admin.is_admin(_bot(), user_id) is allowed


def test_day_argument():
    today = monitor_db.report_day()
    assert monitor_admin.parse_day(None) == today and monitor_admin.parse_day(" Bugun ") == today
    assert monitor_admin.parse_day("kecha") == today - timedelta(days=1)
    assert monitor_admin.parse_day("2026-10-13") == date(2026, 10, 13)
    assert monitor_admin.parse_day("13.10.2026") is None


async def test_admin_gets_report_sent_now_without_marking_the_day_as_sent(monkeypatch):
    run = AsyncMock(return_value=True)
    monkeypatch.setattr(monitor_report, "run_report", run)
    bot = _bot()

    private = _message(bot, ADMIN_ID, ADMIN_ID)
    await monitor_admin.handle_test_report(private, SimpleNamespace(args="2026-10-13"))
    run.assert_awaited_once_with(bot, date(2026, 10, 13), mark_sent=False)
    assert [call.args[0][:1] for call in private.reply.await_args_list] == ["⏳", "✅"]

    in_group = _message(bot, GROUP_ADMIN_ID, REPORT_CHAT, "supergroup")
    await monitor_admin.handle_test_report(in_group, SimpleNamespace(args=None))
    assert run.await_args.args[1] == monitor_db.report_day()
    assert len(in_group.reply.await_args_list) == 1  # fayl shu guruhning o'ziga tushadi, "yuborildi" ortiqcha


async def test_strangers_and_other_groups_get_no_answer_at_all(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(monitor_report, "run_report", run)
    bot = _bot()
    stranger = _message(bot, 888, 888)
    customer_group = _message(bot, ADMIN_ID, -1002222222222, "supergroup")  # admin, lekin mijoz guruhida yozgan

    await monitor_admin.handle_test_report(stranger, SimpleNamespace(args=None))
    await monitor_admin.handle_test_report(customer_group, SimpleNamespace(args=None))
    run.assert_not_called()
    stranger.reply.assert_not_called()
    customer_group.reply.assert_not_called()


async def test_wrong_date_and_failures_are_reported_to_the_admin(monkeypatch):
    bot = _bot()
    message = _message(bot, ADMIN_ID, ADMIN_ID)
    await monitor_admin.handle_test_report(message, SimpleNamespace(args="ertaga"))
    assert "Sana noto'g'ri" in message.reply.await_args.args[0]

    monkeypatch.setattr(monitor_report, "run_report", AsyncMock(return_value=False))
    await monitor_admin.handle_test_report(message, SimpleNamespace(args=None))
    assert message.reply.await_args.args[0].startswith("❌")

    monkeypatch.setattr(monitor_report, "run_report", AsyncMock(side_effect=RuntimeError("xato")))
    await monitor_admin.handle_test_report(message, SimpleNamespace(args=None))
    assert message.reply.await_args.args[0].startswith("❌")


# --- llm_client --------------------------------------------------------------------------------

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}


def _ollama(monkeypatch, post):
    monkeypatch.setattr(llm_client.settings, "GROUP_AI_PROVIDER", "ollama")
    monkeypatch.setattr(llm_client.settings, "GROUP_AI_MODEL", "gemma4:12b")
    monkeypatch.setattr(llm_client, "_get_ollama_client", lambda: SimpleNamespace(post=post))


def _ollama_answer(content):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"message": {"content": content}})


async def test_local_model_gets_schema_and_answer_is_parsed(monkeypatch):
    post = AsyncMock(return_value=_ollama_answer(json.dumps({"summary": "Xulosa"})))
    _ollama(monkeypatch, post)

    assert await llm_client.request_json("tizim", "matn", SCHEMA) == {"summary": "Xulosa"}
    payload = post.await_args.kwargs["json"]
    assert post.await_args.args == ("/api/chat",)
    assert (payload["model"], payload["format"], payload["think"], payload["stream"]) == ("gemma4:12b", SCHEMA, False, False)
    assert [m["content"] for m in payload["messages"]] == ["tizim", "matn"]
    assert llm_client.model_name() == "ollama:gemma4:12b"


async def test_unreadable_answer_is_none_and_unreachable_model_raises(monkeypatch):
    _ollama(monkeypatch, AsyncMock(return_value=_ollama_answer("bu JSON emas")))
    assert await llm_client.request_json("tizim", "matn", SCHEMA) is None

    _ollama(monkeypatch, AsyncMock(side_effect=httpx.ConnectError("connection refused")))
    with pytest.raises(LLMUnavailable):
        await llm_client.request_json("tizim", "matn", SCHEMA)

    request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
    error = httpx.HTTPStatusError("500", request=request, response=httpx.Response(500, request=request, text="oom"))
    _ollama(monkeypatch, AsyncMock(return_value=SimpleNamespace(raise_for_status=MagicMock(side_effect=error))))
    with pytest.raises(LLMUnavailable):
        await llm_client.request_json("tizim", "matn", SCHEMA)


@pytest.mark.parametrize("model, has_reasoning", [("gpt-4o-mini", False), ("gpt-5.4-mini", True)])
async def test_openai_request_and_missing_key(monkeypatch, model, has_reasoning):
    choice = SimpleNamespace(message=SimpleNamespace(content=json.dumps({"summary": "Xulosa"})))
    create = AsyncMock(return_value=SimpleNamespace(choices=[choice]))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_client.settings, "GROUP_AI_PROVIDER", "openai")
    monkeypatch.setattr(llm_client.settings, "GROUP_AI_MODEL", model)
    monkeypatch.setattr(llm_client.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_client, "_get_openai_client", lambda: client)

    assert await llm_client.request_json("tizim", "matn", SCHEMA, "summary") == {"summary": "Xulosa"}
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == model and kwargs["response_format"]["json_schema"]["schema"] == SCHEMA
    assert ("reasoning_effort" in kwargs) is has_reasoning

    monkeypatch.setattr(llm_client.settings, "OPENAI_API_KEY", None)
    with pytest.raises(LLMUnavailable):
        await llm_client.request_json("tizim", "matn", SCHEMA)
