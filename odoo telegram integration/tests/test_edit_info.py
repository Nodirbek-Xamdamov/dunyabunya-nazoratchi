"""Ma'lumotlarni tahrirlash oqimini sinaydi (2026-08-05): app.bot.handlers.edit_info.
Haqiqiy Odoo/Telegram so'rovi yubormaydi -- FastAPI javobi monkeypatch orqali
soxtalashtiriladi, DB esa tests/conftest.py'dagi izolyatsiyalangan SQLite (get_user_language
uchun, chat_id topilmasa "uz" ga tushadi)."""

from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers import edit_info as edit_info_module
from app.bot.states import EditInfoState

SAMPLE_OBJECTS = [
    {"id": 80495, "name": "Qo'yliq", "latitude": 41.399214, "longitude": 69.238473},
    {"id": 80508, "name": "xcvlkm", "latitude": None, "longitude": None},
]


def _make_message(
    text: str = "", chat_id: int = 1, username: str | None = "testuser", contact=None, location=None
):
    message = MagicMock()
    message.text = text
    message.chat.id = chat_id
    message.from_user.username = username
    message.contact = contact
    message.location = location
    message.answer = AsyncMock()
    return message


def _make_callback(data: str, chat_id: int = 1):
    callback = MagicMock()
    callback.data = data
    callback.message.chat.id = chat_id
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.answer_location = AsyncMock()
    callback.answer = AsyncMock()
    return callback


def _make_state(data: dict | None = None):
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value=data or {})
    state.clear = AsyncMock()
    return state


async def test_edit_info_start_with_no_objects_shows_message(monkeypatch):
    monkeypatch.setattr(
        edit_info_module, "fetch_order_customer_context", AsyncMock(return_value={"found": True, "objects": []})
    )
    callback = _make_callback("edit_info_start")
    state = _make_state()

    await edit_info_module.handle_edit_info_start(callback, state)

    callback.message.answer.assert_called_once_with(edit_info_module.t("edit_info_no_objects", "uz"))
    state.set_state.assert_not_called()


async def test_edit_info_start_not_found_shows_message(monkeypatch):
    monkeypatch.setattr(
        edit_info_module, "fetch_order_customer_context", AsyncMock(return_value={"found": False, "objects": []})
    )
    callback = _make_callback("edit_info_start")
    state = _make_state()

    await edit_info_module.handle_edit_info_start(callback, state)

    callback.message.answer.assert_called_once_with(edit_info_module.t("edit_info_no_objects", "uz"))


async def test_edit_info_start_with_objects_shows_selection(monkeypatch):
    monkeypatch.setattr(
        edit_info_module,
        "fetch_order_customer_context",
        AsyncMock(return_value={"found": True, "partner_id": 42, "objects": SAMPLE_OBJECTS}),
    )
    callback = _make_callback("edit_info_start")
    state = _make_state()

    await edit_info_module.handle_edit_info_start(callback, state)

    state.set_state.assert_called_once_with(EditInfoState.waiting_for_object_selection)
    assert state.update_data.call_args.kwargs.get("objects") == SAMPLE_OBJECTS
    assert state.update_data.call_args.kwargs.get("partner_id") == 42
    callback.message.answer.assert_called_once()
    args, kwargs = callback.message.answer.call_args
    assert args[0] == edit_info_module.t("edit_info_choose_object", "uz")
    assert "reply_markup" in kwargs


async def test_edit_info_object_selected_with_location_shows_it():
    state = _make_state(data={"lang": "uz", "objects": SAMPLE_OBJECTS})
    callback = _make_callback("editinfo_objsel_80495")

    await edit_info_module.handle_edit_info_object_selected(callback, state)

    state.set_state.assert_called_once_with(EditInfoState.waiting_for_field_choice)
    assert state.update_data.call_args.kwargs == {"object_id": 80495, "object_name": "Qo'yliq"}
    callback.message.edit_text.assert_called_once_with("🏢 Qo'yliq")
    callback.message.answer_location.assert_called_once_with(latitude=41.399214, longitude=69.238473)
    # Ikkinchi answer() -- maydon tanlash tugmalari bilan
    assert callback.message.answer.call_count == 1
    args, kwargs = callback.message.answer.call_args
    assert args[0] == edit_info_module.t("edit_info_choose_field", "uz")
    assert "reply_markup" in kwargs


async def test_edit_info_object_selected_without_location_shows_none_text():
    state = _make_state(data={"lang": "uz", "objects": SAMPLE_OBJECTS})
    callback = _make_callback("editinfo_objsel_80508")

    await edit_info_module.handle_edit_info_object_selected(callback, state)

    callback.message.answer_location.assert_not_called()
    assert callback.message.answer.call_count == 2
    first_call_args, _ = callback.message.answer.call_args_list[0]
    assert first_call_args[0] == edit_info_module.t("edit_info_current_location_none", "uz")


async def test_edit_info_field_phone_asks_for_new_phone():
    state = _make_state(data={"lang": "uz", "object_id": 80495})
    callback = _make_callback("editinfo_field_phone")

    await edit_info_module.handle_edit_info_field_phone(callback, state)

    state.set_state.assert_called_once_with(EditInfoState.waiting_for_new_phone)
    callback.message.answer.assert_called_once()
    args, kwargs = callback.message.answer.call_args
    assert args[0] == edit_info_module.t("edit_info_ask_new_phone", "uz")
    assert "reply_markup" in kwargs


async def test_edit_info_field_location_asks_for_new_location():
    state = _make_state(data={"lang": "uz", "object_id": 80495})
    callback = _make_callback("editinfo_field_location")

    await edit_info_module.handle_edit_info_field_location(callback, state)

    state.set_state.assert_called_once_with(EditInfoState.waiting_for_new_location)
    callback.message.answer.assert_called_once()
    args, _ = callback.message.answer.call_args
    assert args[0] == edit_info_module.t("edit_info_ask_new_location", "uz")


async def test_edit_info_new_phone_valid_updates_and_clears_state(monkeypatch):
    monkeypatch.setattr(edit_info_module, "_update_object", AsyncMock(return_value=True))
    state = _make_state(data={"lang": "uz", "object_id": 80495, "object_name": "Qo'yliq", "partner_id": 42})
    message = _make_message(text="+998901234567", chat_id=1, username="testuser")

    await edit_info_module.handle_edit_info_new_phone(message, state)

    edit_info_module._update_object.assert_awaited_once_with(
        80495, "Qo'yliq", 1, "testuser", 42, phone="+998901234567"
    )
    state.clear.assert_called_once()
    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert args[0] == edit_info_module.t("edit_info_phone_updated", "uz")


async def test_edit_info_new_phone_invalid_format_shows_error_and_does_not_clear():
    state = _make_state(data={"lang": "uz", "object_id": 80495})
    message = _make_message(text="+99890123456")  # 998 + 8 raqam, noto'g'ri

    await edit_info_module.handle_edit_info_new_phone(message, state)

    state.clear.assert_not_called()
    message.answer.assert_called_once_with(edit_info_module.t("order_phone_invalid_format", "uz"))


async def test_edit_info_new_phone_accepts_shared_contact(monkeypatch):
    monkeypatch.setattr(edit_info_module, "_update_object", AsyncMock(return_value=True))
    contact = MagicMock()
    contact.phone_number = "998901234567"
    state = _make_state(data={"lang": "uz", "object_id": 80495, "object_name": "Qo'yliq", "partner_id": 42})
    message = _make_message(text=None, chat_id=1, username="testuser", contact=contact)

    await edit_info_module.handle_edit_info_new_phone(message, state)

    edit_info_module._update_object.assert_awaited_once_with(
        80495, "Qo'yliq", 1, "testuser", 42, phone="998901234567"
    )


async def test_edit_info_new_phone_update_failure_shows_error_message(monkeypatch):
    monkeypatch.setattr(edit_info_module, "_update_object", AsyncMock(return_value=False))
    state = _make_state(data={"lang": "uz", "object_id": 80495})
    message = _make_message(text="+998901234567")

    await edit_info_module.handle_edit_info_new_phone(message, state)

    state.clear.assert_called_once()
    args, _ = message.answer.call_args
    assert args[0] == edit_info_module.t("edit_info_update_failed", "uz")


async def test_edit_info_new_location_updates_and_clears_state(monkeypatch):
    monkeypatch.setattr(edit_info_module, "_update_object", AsyncMock(return_value=True))
    state = _make_state(data={"lang": "uz", "object_id": 80495, "object_name": "Qo'yliq", "partner_id": 42})
    location = MagicMock()
    location.latitude = 41.3
    location.longitude = 69.2
    message = _make_message(chat_id=1, username="testuser", location=location)

    await edit_info_module.handle_edit_info_new_location(message, state)

    edit_info_module._update_object.assert_awaited_once_with(
        80495, "Qo'yliq", 1, "testuser", 42, latitude=41.3, longitude=69.2
    )
    state.clear.assert_called_once()
    args, _ = message.answer.call_args
    assert args[0] == edit_info_module.t("edit_info_location_updated", "uz")


async def test_edit_info_new_phone_cancel_button_clears_state():
    state = _make_state(data={"lang": "uz", "object_id": 80495})
    message = _make_message(text=edit_info_module.t("btn_cancel_order", "uz"))

    await edit_info_module.handle_edit_info_new_phone(message, state)

    state.clear.assert_called_once()
    args, _ = message.answer.call_args
    assert args[0] == edit_info_module.t("order_cancelled", "uz")
