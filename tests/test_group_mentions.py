"""app.bot.handlers.group_mentions -- guruhda botni (@BOT_USERNAME) mention qilib yozilgan
xabarlarni tekshiradi: mention bo'lmasa/bot yuborsa e'tiborsiz qoldirilishi, va (2026-10-07)
hech qaysi holatda guruhga javob YOZILMASLIGI. `submit_group_inquiry` monkeypatch
orqali soxtalashtiriladi (haqiqiy HTTP so'rov yubormaydi)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.handlers import group_mentions as group_mentions_module
from app.bot.handlers.group_mentions import handle_group_mention


def _make_message(text: str, chat_type: str = "supergroup", user_id: int = 1, is_bot: bool = False):
    message = MagicMock()
    message.text = text
    message.chat.type = chat_type
    message.chat.id = -1009999999999
    message.chat.title = "Test Guruh"
    message.from_user.id = user_id
    message.from_user.username = "testuser"
    message.from_user.full_name = "Test User"
    message.from_user.is_bot = is_bot
    message.reply = AsyncMock()
    return message


async def test_ignores_message_without_mention(monkeypatch):
    submit_mock = AsyncMock(return_value={"found": True, "lead_created": True})
    monkeypatch.setattr(group_mentions_module, "submit_group_inquiry", submit_mock)

    message = _make_message("Salom hammaga, qandaysizlar?")
    await handle_group_mention(message)

    submit_mock.assert_not_called()
    message.reply.assert_not_called()


async def test_ignores_mention_from_another_bot(monkeypatch):
    submit_mock = AsyncMock(return_value={"found": True, "lead_created": True})
    monkeypatch.setattr(group_mentions_module, "submit_group_inquiry", submit_mock)

    message = _make_message("@test_bot xabar", is_bot=True)
    await handle_group_mention(message)

    submit_mock.assert_not_called()
    message.reply.assert_not_called()


async def test_mention_is_case_insensitive(monkeypatch):
    submit_mock = AsyncMock(return_value={"found": True, "lead_created": True})
    monkeypatch.setattr(group_mentions_module, "submit_group_inquiry", submit_mock)

    message = _make_message("@TEST_BOT narxlar qanday?")
    await handle_group_mention(message)

    submit_mock.assert_awaited_once()


async def test_found_customer_forwards_silently_no_reply(monkeypatch):
    """2026-08-03: mijoz Odoo'da topilsa (crm.lead partner_id bilan yozilgan bo'lsa ham),
    bot guruhda hech narsa demaydi -- call-center Odoo'dagi lead orqali o'zi bog'lanadi."""
    submit_mock = AsyncMock(return_value={"found": True, "lead_created": True})
    monkeypatch.setattr(group_mentions_module, "submit_group_inquiry", submit_mock)

    message = _make_message("@test_bot buyurtmam qayerda?")
    await handle_group_mention(message)

    submit_mock.assert_awaited_once_with(
        chat_id=1,
        username="testuser",
        full_name="Test User",
        group_id=-1009999999999,
        group_title="Test Guruh",
        message_text="@test_bot buyurtmam qayerda?",
    )
    message.reply.assert_not_called()


@pytest.mark.parametrize("lead_created", [True, False])
async def test_customer_not_found_never_replies_in_group(monkeypatch, lead_created):
    """2026-10-07: bot mijoz guruhlariga hech narsa yozmaydi -- mijoz Odoo'da topilmasa ham
    (lead yaratilgan-yaratilmaganidan qat'i nazar) guruhga javob berilmaydi."""
    submit_mock = AsyncMock(return_value={"found": False, "lead_created": lead_created})
    monkeypatch.setattr(group_mentions_module, "submit_group_inquiry", submit_mock)

    message = _make_message("@test_bot salom, men mijozman")
    await handle_group_mention(message)

    submit_mock.assert_awaited_once()
    message.reply.assert_not_called()


async def test_network_error_sends_no_reply(monkeypatch):
    submit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(group_mentions_module, "submit_group_inquiry", submit_mock)

    message = _make_message("@test_bot salom")
    await handle_group_mention(message)

    message.reply.assert_not_called()
