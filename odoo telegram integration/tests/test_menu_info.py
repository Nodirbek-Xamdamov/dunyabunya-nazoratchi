"""'Malumotlarim' (info) menyu bo'limi uchun: 2026-08-04, mijoz aniq so'ragan --
obyekt lokatsiyasi bo'lsa qisqa "Bor" HTML havolasi (app.balance) ko'rsatiladi, shu
sabab bu xabar albatta `parse_mode="HTML"` bilan yuborilishi kerak (aks holda havola
foydalanuvchiga oddiy matn -- `<a href=...>` -- sifatida ko'rinadi). Havola URL'i uchun
Telegram avtomatik xarita preview kartasi qo'shib qo'yishi ham o'chirilgan
(`link_preview_options`) -- mijoz aniq so'ragan: xabar oxirida shu karta chiqmasin."""

from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers import menu as menu_module


def _make_message(text: str = "ℹ️ Malumotlarim", chat_id: int = 1, username: str | None = "testuser"):
    message = MagicMock()
    message.text = text
    message.chat.id = chat_id
    message.from_user.username = username
    message.answer = AsyncMock()
    return message


def _make_state():
    state = MagicMock()
    state.clear = AsyncMock()
    return state


async def test_info_section_sends_message_with_html_parse_mode_and_no_link_preview(monkeypatch):
    monkeypatch.setattr(menu_module, "fetch_menu_section", AsyncMock(return_value="👤 F.I.Sh: Test"))

    message = _make_message()
    await menu_module.handle_menu_section_button(message, _make_state())

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert args[0] == "👤 F.I.Sh: Test"
    assert kwargs.get("parse_mode") == "HTML"
    assert kwargs.get("link_preview_options").is_disabled is True
