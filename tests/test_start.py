"""app.bot.handlers.start.cmd_start ni sinaydi (2026-08-02 bug fix): agar foydalanuvchi
biror FSM oqimi (masalan "Buyurtma berish" -- lokatsiya so'ralayotgan bosqich) o'rtasida
/start bossa, eski holat tozalanishi va pastda osilib qolgan ReplyKeyboardMarkup (masalan
"joylashuv yuborish" tugmalari) aniq olib tashlanishi kerak."""

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import ReplyKeyboardRemove

from app.bot.handlers import start as start_module


def _make_message(username: str | None = "testuser"):
    message = MagicMock()
    message.from_user.username = username
    message.from_user.first_name = "Test"
    message.answer = AsyncMock()
    return message


def _make_state(current_state: str | None):
    state = MagicMock()
    state.get_state = AsyncMock(return_value=current_state)
    state.clear = AsyncMock()
    return state


async def test_cmd_start_clears_state_and_removes_keyboard_when_mid_flow():
    message = _make_message()
    state = _make_state(current_state="OrderPlacementState:waiting_for_location")

    await start_module.cmd_start(message, state)

    state.clear.assert_called_once()
    assert message.answer.call_count == 2
    first_call_kwargs = message.answer.call_args_list[0].kwargs
    assert isinstance(first_call_kwargs.get("reply_markup"), ReplyKeyboardRemove)


async def test_cmd_start_skips_reset_notice_when_not_mid_flow():
    message = _make_message()
    state = _make_state(current_state=None)

    await start_module.cmd_start(message, state)

    state.clear.assert_not_called()
    message.answer.assert_called_once()


async def test_cmd_start_requires_username():
    message = _make_message(username=None)
    state = _make_state(current_state=None)

    await start_module.cmd_start(message, state)

    state.get_state.assert_not_called()
    message.answer.assert_called_once()
