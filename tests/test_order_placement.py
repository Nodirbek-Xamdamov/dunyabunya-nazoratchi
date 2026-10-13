"""app.bot.handlers.menu -- "Buyurtma berish" oqimini sinaydi: mijoz Odoo'da obyekti bilan/
obyektsiz topilganda, umuman topilmaganda ("yangi mijoz") va call-center guruhiga
yuboriladigan sarlavha to'g'ri shakllanishini tekshiradi. `fetch_order_customer_context`
monkeypatch orqali soxtalashtiriladi (haqiqiy HTTP so'rov yubormaydi)."""

from unittest.mock import AsyncMock, MagicMock

from aiogram.enums import ContentType

from app.bot.handlers import menu as menu_module
from app.bot.states import OrderPlacementState


def _make_message(
    text: str = "",
    chat_id: int = 1,
    user_id: int = 1,
    username: str | None = "testuser",
    content_type: str = ContentType.TEXT,
    caption: str | None = None,
    contact=None,
):
    message = MagicMock()
    message.text = text
    message.caption = caption
    message.content_type = content_type
    message.chat.id = chat_id
    message.message_id = 777
    message.from_user.id = user_id
    message.from_user.username = username
    message.from_user.full_name = "Test User"
    message.location = None
    message.contact = contact
    message.answer = AsyncMock()
    return message


def _make_callback(data: str, chat_id: int = 1):
    callback = MagicMock()
    callback.data = data
    callback.message.chat.id = chat_id
    callback.message.edit_text = AsyncMock()
    callback.message.edit_reply_markup = AsyncMock()
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


async def test_orders_button_not_found_shows_new_customer_button(monkeypatch):
    monkeypatch.setattr(
        menu_module, "fetch_order_customer_context", AsyncMock(return_value={"found": False, "objects": []})
    )

    message = _make_message(text="🛒 Buyurtma berish")
    state = _make_state()

    await menu_module.handle_menu_section_button(message, state)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "reply_markup" in kwargs
    state.update_data.assert_called_once()
    assert state.update_data.call_args.kwargs.get("is_new") is True


async def test_orders_button_found_without_objects_asks_for_receiver_name(monkeypatch):
    """2026-08-03: mijoz aniq so'ragan -- birinchi obyekt yaratilayotganda ham, mavjud
    obyekt uchun ham, avval o'sha obyektning buyurtma qabul qiluvchisi ismi so'raladi
    (har xil obyektda har xil menejer bo'lishi mumkin), keyin telefon, keyin obyekt
    nomi/lokatsiya -- "yangi mijoz" oqimidagi bilan bir xil ketma-ketlik."""
    monkeypatch.setattr(
        menu_module,
        "fetch_order_customer_context",
        AsyncMock(
            return_value={
                "found": True,
                "partner_id": 42,
                "partner_name": "Test Partner",
                "partner_phone": "+998901234567",
                "objects": [],
            }
        ),
    )

    message = _make_message(text="🛒 Buyurtma berish")
    state = _make_state()

    await menu_module.handle_menu_section_button(message, state)

    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_address_text)
    assert state.update_data.call_args.kwargs.get("partner_id") == 42
    assert state.update_data.call_args.kwargs.get("is_new_object") is True
    assert state.update_data.call_args.kwargs.get("object_id") is None
    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert args[0] == menu_module.t("order_ask_address_text", "uz")


async def test_orders_button_found_single_object_also_shows_selection(monkeypatch):
    """2026-08-02: bitta obyekt bo'lsa ham endi avtomatik tanlanmaydi -- ro'yxat
    ko'rsatiladi (tanlangan obyekt uchun ham lokatsiya so'raladi, va "Yangi obyekt
    yaratish" tugmasi ham shu ro'yxatda bo'ladi)."""
    monkeypatch.setattr(
        menu_module,
        "fetch_order_customer_context",
        AsyncMock(
            return_value={
                "found": True,
                "partner_id": 42,
                "partner_name": "Test Partner",
                "partner_phone": "+998901234567",
                "objects": [{"id": 1, "name": "NAZARBEK"}],
            }
        ),
    )

    message = _make_message(text="🛒 Buyurtma berish")
    state = _make_state()

    await menu_module.handle_menu_section_button(message, state)

    state.set_state.assert_not_called()
    assert state.update_data.call_args.kwargs.get("objects") == [{"id": 1, "name": "NAZARBEK"}]
    _, kwargs = message.answer.call_args
    assert "reply_markup" in kwargs


async def test_orders_button_found_multiple_objects_shows_selection_keyboard(monkeypatch):
    objects = [{"id": 1, "name": "NAZARBEK"}, {"id": 2, "name": "ISTIROXAT"}]
    monkeypatch.setattr(
        menu_module,
        "fetch_order_customer_context",
        AsyncMock(return_value={"found": True, "partner_name": "Test Partner", "partner_phone": "-", "objects": objects}),
    )

    message = _make_message(text="🛒 Buyurtma berish")
    state = _make_state()

    await menu_module.handle_menu_section_button(message, state)

    state.set_state.assert_not_called()  # obyekt tanlanmaguncha state o'zgarmaydi
    _, kwargs = message.answer.call_args
    assert "reply_markup" in kwargs


async def test_menu_section_button_clears_stale_fsm_state(monkeypatch):
    """Foydalanuvchi biror yarim qolgan oqim (masalan yangi mijoz ismini kutish)
    o'rtasida boshqa menyu tugmasini bossa, eski FSM holati/ma'lumoti darhol
    tozalanishi kerak -- aks holda keyingi xabar hamon eski qadam sifatida
    talqin qilinib qolaveradi."""
    monkeypatch.setattr(
        menu_module, "fetch_order_customer_context", AsyncMock(return_value={"found": False, "objects": []})
    )

    message = _make_message(text="🛒 Buyurtma berish")
    state = _make_state(data={"lang": "uz", "is_new": True})

    await menu_module.handle_menu_section_button(message, state)

    state.clear.assert_called_once()


async def test_cmd_menu_clears_stale_fsm_state():
    state = _make_state(data={"order_id": 1, "lang": "uz"})
    message = _make_message()

    await menu_module.cmd_menu(message, state)

    state.clear.assert_called_once()
    message.answer.assert_called_once()


async def test_order_object_selected_asks_for_location_now(monkeypatch):
    """2026-08-02: mavjud obyekt tanlangach endi to'g'ridan-to'g'ri buyurtma tafsilotiga
    o'tmaydi -- avval shu obyekt uchun GPS lokatsiyasi so'raladi (ReplyKeyboardMarkup
    talab qilgani uchun edit emas, YANGI xabar orqali)."""
    objects = [{"id": 1, "name": "NAZARBEK"}, {"id": 2, "name": "ISTIROXAT"}]
    state = _make_state(data={"lang": "uz", "objects": objects})
    callback = _make_callback("order_objsel_2")

    await menu_module.handle_order_object_selected(callback, state)

    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_location)
    state.update_data.assert_called_once_with(object_id=2, object_name="ISTIROXAT", is_new_object=False)
    callback.message.edit_text.assert_called_once()
    callback.message.answer.assert_called_once()
    _, kwargs = callback.message.answer.call_args
    assert "reply_markup" in kwargs


async def test_order_object_selected_shows_saved_location_for_confirmation(monkeypatch):
    """2026-08-03 (ikkinchi marta qayta ko'rib chiqilgan): Odoo'dagi obyekt uchun lokatsiya
    allaqachon bor bo'lsa, endi qayta SO'RALMAYDI -- saqlangan lokatsiya (native Telegram
    location xabari) ko'rsatiladi va "Buyurtma berish"/"Lokatsiyani o'zgartirish"
    tugmalari bilan tasdiqlash so'raladi (waiting_for_location_confirmation)."""
    objects = [{"id": 1, "name": "NAZARBEK", "latitude": 41.311, "longitude": 69.279}]
    state = _make_state(data={"lang": "uz", "objects": objects})
    callback = _make_callback("order_objsel_1")

    await menu_module.handle_order_object_selected(callback, state)

    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_location_confirmation)
    state.update_data.assert_called_once_with(
        object_id=1, object_name="NAZARBEK", is_new_object=False, latitude=41.311, longitude=69.279
    )
    callback.message.edit_text.assert_called_once_with("🏢 NAZARBEK")
    callback.message.answer_location.assert_called_once_with(latitude=41.311, longitude=69.279)
    args, kwargs = callback.message.answer.call_args
    assert args[0] == menu_module.t("order_confirm_location", "uz")
    assert "reply_markup" in kwargs


async def test_order_location_keep_goes_straight_to_order_prompt():
    """Saqlangan lokatsiya to'g'ri deb tasdiqlanganda -- FastAPI'ga qayta yozmasdan
    (Odoo'da bu lokatsiya allaqachon bor) to'g'ridan-to'g'ri buyurtma tafsilotiga o'tadi."""
    state = _make_state(data={"lang": "uz", "latitude": 41.311, "longitude": 69.279})
    callback = _make_callback("order_loc_keep")

    await menu_module.handle_order_location_keep(callback, state)

    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_order)
    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    args, kwargs = callback.message.answer.call_args
    assert args[0] == menu_module.t("order_placement_prompt", "uz")
    assert "reply_markup" in kwargs


async def test_order_location_change_asks_for_new_location():
    """Saqlangan lokatsiya noto'g'ri deb topilganda -- yangi GPS lokatsiyasi so'raladi,
    xuddi yangi obyekt uchun lokatsiya so'ralgandagi bilan bir xil."""
    state = _make_state(data={"lang": "uz", "latitude": 41.311, "longitude": 69.279})
    callback = _make_callback("order_loc_change")

    await menu_module.handle_order_location_change(callback, state)

    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_location)
    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    args, kwargs = callback.message.answer.call_args
    assert args[0] == menu_module.t("order_ask_location", "uz")
    assert "reply_markup" in kwargs


async def test_order_new_object_button_asks_for_address_text():
    """"Yangi obyekt yaratish" tugmasi -- 2026-08-05, mijoz aniq so'ragan: qabul
    qiluvchi ismini qayta so'rash (2026-08-03'da qo'shilgan) olib tashlandi, to'g'ridan-
    to'g'ri obyekt nomi (manzil matni) so'raladi."""
    state = _make_state(data={"lang": "uz", "partner_id": 42})
    callback = _make_callback("order_obj_new")

    await menu_module.handle_order_new_object(callback, state)

    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_address_text)
    state.update_data.assert_called_once_with(is_new_object=True, object_id=None)
    callback.message.edit_text.assert_called_once()
    callback.message.answer.assert_called_once()
    args, kwargs = callback.message.answer.call_args
    assert args[0] == menu_module.t("order_ask_address_text", "uz")
    assert "reply_markup" in kwargs


def test_object_list_keyboard_includes_create_new_button():
    from app.bot.keyboards import get_order_object_list_keyboard

    keyboard = get_order_object_list_keyboard("uz", [{"id": 1, "name": "NAZARBEK"}])

    callback_datas = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert "order_objsel_1" in callback_datas
    assert "order_obj_new" in callback_datas


def test_location_request_keyboard_includes_cancel_button():
    """2026-08-03: lokatsiya so'ralayotgan bosqichda ham "Bekor qilish" tugmasi
    ko'rsatilishi kerak -- mijoz manzil/lokatsiya berishni istamasa oqimdan chiqishi uchun."""
    button_texts = [btn.text for row in menu_module.get_location_request_keyboard("uz").keyboard for btn in row]
    assert menu_module.t("btn_cancel_order", "uz") in button_texts


def test_cancel_keyboard_has_expected_button_text():
    from app.bot.keyboards import get_cancel_keyboard

    keyboard = get_cancel_keyboard("uz")

    button_texts = [btn.text for row in keyboard.keyboard for btn in row]
    assert button_texts == [menu_module.t("btn_cancel_order", "uz")]


async def test_order_location_received_stores_coordinates_and_asks_for_details():
    message = _make_message()
    message.location = MagicMock(latitude=41.311, longitude=69.279)
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_location(message, state)

    state.update_data.assert_called_once_with(latitude=41.311, longitude=69.279)
    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_order)


async def test_order_location_received_skips_submission_without_partner_id(monkeypatch):
    """Butunlay yangi mijoz oqimida (Odoo'da topilmagan) partner_id yo'q -- bu holda
    obyekt lokatsiyasi FastAPI'ga yozilmasligi kerak (biriktiriladigan Odoo mijozi yo'q)."""
    submit_mock = AsyncMock()
    monkeypatch.setattr(menu_module, "submit_object_location", submit_mock)

    message = _make_message()
    message.location = MagicMock(latitude=41.311, longitude=69.279)
    state = _make_state(data={"lang": "uz", "is_new": True, "partner_name": "Yangi Mijoz"})

    await menu_module.handle_order_location(message, state)

    submit_mock.assert_not_called()


async def test_order_location_received_submits_for_existing_selected_object(monkeypatch):
    submit_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(menu_module, "submit_object_location", submit_mock)

    message = _make_message(user_id=1, username="testuser")
    message.location = MagicMock(latitude=41.311, longitude=69.279)
    state = _make_state(
        data={
            "lang": "uz",
            "partner_id": 42,
            "object_id": 7,
            "object_name": "NAZARBEK",
            "is_new_object": False,
            "partner_name": "Test Partner",
            "partner_phone": "+998901234567",
        }
    )

    await menu_module.handle_order_location(message, state)

    submit_mock.assert_called_once_with(
        partner_id=42,
        object_id=7,
        object_name="NAZARBEK",
        is_new_object=False,
        chat_id=1,
        latitude=41.311,
        longitude=69.279,
        username="testuser",
        partner_name="Test Partner",
        partner_phone="+998901234567",
    )


async def test_order_location_received_submits_for_newly_created_object(monkeypatch):
    submit_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(menu_module, "submit_object_location", submit_mock)

    message = _make_message(username="testuser")
    message.location = MagicMock(latitude=41.0, longitude=69.0)
    state = _make_state(
        data={
            "lang": "uz",
            "partner_id": 42,
            "object_id": None,
            "object_name": "Chilonzor tumani, 5-uy",
            "is_new_object": True,
            "partner_name": "Test Partner",
            "partner_phone": "+998901234567",
        }
    )

    await menu_module.handle_order_location(message, state)

    submit_mock.assert_called_once_with(
        partner_id=42,
        object_id=None,
        object_name="Chilonzor tumani, 5-uy",
        is_new_object=True,
        chat_id=1,
        latitude=41.0,
        longitude=69.0,
        username="testuser",
        partner_name="Test Partner",
        partner_phone="+998901234567",
    )


async def test_order_location_map_choice_sends_instructions_without_changing_state():
    """"Xaritadan tanlayman" tugmasi bosilganda: Bot API orqali xarita-ekranini majburan
    ochib bo'lmagani uchun, foydalanuvchiga Telegram'ning o'z (paperclip -> Location)
    interfeysidan foydalanish yo'l-yo'rig'i ko'rsatiladi, holat o'zgarmaydi -- keyin
    kelgan location (qaysi yo'l bilan bo'lishidan qat'i nazar) hamon qabul qilinadi."""
    message = _make_message(text=menu_module.t("btn_choose_from_map", "uz"))
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_location_map_choice(message, state)

    state.set_state.assert_not_called()
    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert args[0] == menu_module.t("order_map_instructions", "uz")


async def test_order_new_customer_button_asks_for_name():
    callback = _make_callback("order_new_customer")
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_new_customer(callback, state)

    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_new_name)
    callback.message.edit_text.assert_called_once()


async def test_order_new_name_stores_name_and_asks_for_phone():
    message = _make_message(text="Nodir Xamdamov")
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_new_name(message, state)

    state.update_data.assert_called_once_with(partner_name="Nodir Xamdamov")
    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_new_phone)


async def test_order_new_phone_stores_phone_and_asks_for_address_text():
    message = _make_message(text="+998901234567")
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_new_phone(message, state)

    state.update_data.assert_called_once_with(partner_phone="+998901234567")
    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_address_text)


async def test_order_new_phone_accepts_local_format_without_country_code():
    """9 ta raqam (998'siz) ham to'g'ri format deb hisoblanadi."""
    message = _make_message(text="901234567")
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_new_phone(message, state)

    state.update_data.assert_called_once_with(partner_phone="901234567")
    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_address_text)


async def test_order_new_phone_rejects_wrong_digit_count(monkeypatch):
    """2026-08-05, mijoz aniq so'ragan: 1 ta raqam ko'p yoki kam bo'lsa xatolik
    xabari ko'rsatiladi, bosqich O'ZGARMAYDI (keyingi qadamga o'tilmaydi)."""
    message = _make_message(text="+99890123456")  # 998 + 8 raqam, 9 bo'lishi kerak
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_new_phone(message, state)

    state.update_data.assert_not_called()
    state.set_state.assert_not_called()
    message.answer.assert_called_once_with(menu_module.t("order_phone_invalid_format", "uz"))


async def test_order_new_phone_accepts_shared_contact_without_validation():
    """2026-08-05, mijoz aniq so'ragan: Telegram'ning "kontakt ulashish" tugmasi
    orqali kelgan raqam formatga qaramasdan to'g'ridan-to'g'ri qabul qilinadi."""
    contact = MagicMock()
    contact.phone_number = "998901234567"
    message = _make_message(text=None, contact=contact)
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_new_phone(message, state)

    state.update_data.assert_called_once_with(partner_phone="998901234567")
    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_address_text)


async def test_order_address_text_stores_as_object_name_and_asks_for_location():
    message = _make_message(text="Chilonzor tumani, 5-uy")
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_address_text(message, state)

    state.update_data.assert_called_once_with(object_name="Chilonzor tumani, 5-uy")
    state.set_state.assert_called_once_with(OrderPlacementState.waiting_for_location)


async def test_order_location_invalid_reprompts_without_changing_state():
    message = _make_message(text="bu lokatsiya emas")
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_location_invalid(message, state)

    state.set_state.assert_not_called()
    message.answer.assert_called_once()


async def test_order_address_text_cancel_button_clears_state():
    """2026-08-03: manzil so'ralayotganda "Bekor qilish" bosilsa, matn manzil sifatida
    SAQLANMAYDI -- holat tozalanadi va bosh menyu klaviaturasi bilan tasdiq xabari
    ko'rsatiladi."""
    message = _make_message(text=menu_module.t("btn_cancel_order", "uz"))
    state = _make_state(data={"lang": "uz", "is_new": True, "partner_name": "Yangi Mijoz"})

    await menu_module.handle_order_address_text(message, state)

    state.update_data.assert_not_called()
    state.set_state.assert_not_called()
    state.clear.assert_called_once()
    args, kwargs = message.answer.call_args
    assert args[0] == menu_module.t("order_cancelled", "uz")
    assert "reply_markup" in kwargs


async def test_order_location_invalid_cancel_button_clears_state():
    message = _make_message(text=menu_module.t("btn_cancel_order", "uz"))
    state = _make_state(data={"lang": "uz"})

    await menu_module.handle_order_location_invalid(message, state)

    state.clear.assert_called_once()
    args, kwargs = message.answer.call_args
    assert args[0] == menu_module.t("order_cancelled", "uz")
    assert "reply_markup" in kwargs


async def test_order_placement_cancel_button_clears_state_without_submitting(monkeypatch):
    """2026-08-03: buyurtma tafsiloti so'ralayotganda "Bekor qilish" bosilsa,
    submit_order_to_odoo umuman chaqirilmasligi kerak -- hech narsa Odoo'ga yozilmaydi."""
    submit_mock = AsyncMock()
    monkeypatch.setattr(menu_module, "submit_order_to_odoo", submit_mock)
    download_mock = AsyncMock()
    monkeypatch.setattr(menu_module, "_download_order_media", download_mock)

    message = _make_message(text=menu_module.t("btn_cancel_order", "uz"))
    state = _make_state(
        data={"lang": "uz", "is_new": False, "partner_id": 42, "partner_name": "Test Partner", "partner_phone": "-"}
    )
    bot = MagicMock()

    await menu_module.handle_order_placement(message, state, bot)

    submit_mock.assert_not_called()
    download_mock.assert_not_called()
    state.clear.assert_called_once()
    args, kwargs = message.answer.call_args
    assert args[0] == menu_module.t("order_cancelled", "uz")
    assert "reply_markup" in kwargs


async def test_order_placement_existing_customer_writes_to_odoo(monkeypatch):
    submit_mock = AsyncMock(return_value=99)
    monkeypatch.setattr(menu_module, "submit_order_to_odoo", submit_mock)
    download_mock = AsyncMock(return_value=(None, None, None, None))
    monkeypatch.setattr(menu_module, "_download_order_media", download_mock)

    message = _make_message(text="2 dona sement kerak")
    state = _make_state(
        data={
            "lang": "uz",
            "is_new": False,
            "partner_id": 42,
            "partner_name": "Test Partner",
            "partner_phone": "+998901234567",
            "object_name": "NAZARBEK",
        }
    )
    bot = MagicMock()

    await menu_module.handle_order_placement(message, state, bot)

    submit_mock.assert_called_once_with(
        partner_id=42,
        partner_name="Test Partner",
        partner_phone="+998901234567",
        chat_id=1,
        order_details="2 dona sement kerak",
        object_name="NAZARBEK",
        latitude=None,
        longitude=None,
        photo_path=None,
        video_path=None,
        audio_path=None,
        document_path=None,
    )
    bot.send_message.assert_not_called()  # call-center guruhi endi umuman ishlatilmaydi
    bot.forward_message.assert_not_called()
    message.answer.assert_called_once()
    state.clear.assert_called_once()


async def test_order_placement_odoo_failure_shows_generic_error(monkeypatch):
    """Call-center guruhiga zaxira forward qilish 2026-08-03'da butunlay olib tashlandi --
    Odoo yozuvi muvaffaqiyatsiz bo'lsa endi shunchaki mijozga xatolik xabari ko'rsatiladi."""
    submit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(menu_module, "submit_order_to_odoo", submit_mock)
    monkeypatch.setattr(menu_module, "_download_order_media", AsyncMock(return_value=(None, None, None, None)))

    message = _make_message(text="2 dona sement kerak")
    state = _make_state(
        data={"lang": "uz", "is_new": False, "partner_id": 42, "partner_name": "Test Partner", "partner_phone": "-"}
    )
    bot = MagicMock()

    await menu_module.handle_order_placement(message, state, bot)

    submit_mock.assert_called_once()
    bot.send_message.assert_not_called()
    bot.forward_message.assert_not_called()
    args, _ = message.answer.call_args
    assert args[0] == menu_module.t("generic_error", "uz")
    state.clear.assert_called_once()


async def test_order_placement_new_customer_writes_to_odoo(monkeypatch):
    """Butunlay yangi mijoz (Odoo'da topilmagan, partner_id yo'q) ham to'g'ridan-to'g'ri
    Odoo'ga (crm.lead, contact_name/phone bilan) yoziladi -- call-center guruhi umuman
    ishlatilmaydi, muvaffaqiyatli bo'lsin yoki bo'lmasin."""
    submit_mock = AsyncMock(return_value=99)
    monkeypatch.setattr(menu_module, "submit_order_to_odoo", submit_mock)
    monkeypatch.setattr(menu_module, "_download_order_media", AsyncMock(return_value=(None, None, None, None)))

    message = _make_message(text="Salom, menga sement kerak")
    state = _make_state(
        data={
            "lang": "uz",
            "is_new": True,
            "partner_name": "Yangi Mijoz",
            "partner_phone": "+998900000000",
            "object_name": "Dunyabunya",
            "latitude": 41.39881,
            "longitude": 69.238418,
        }
    )
    bot = MagicMock()

    await menu_module.handle_order_placement(message, state, bot)

    submit_mock.assert_called_once_with(
        partner_id=None,
        partner_name="Yangi Mijoz",
        partner_phone="+998900000000",
        chat_id=1,
        order_details="Salom, menga sement kerak",
        object_name="Dunyabunya",
        latitude=41.39881,
        longitude=69.238418,
        photo_path=None,
        video_path=None,
        audio_path=None,
        document_path=None,
    )
    bot.send_message.assert_not_called()
    bot.forward_message.assert_not_called()
    message.answer.assert_called_once()
    state.clear.assert_called_once()


async def test_order_placement_new_customer_odoo_failure_shows_generic_error(monkeypatch):
    submit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(menu_module, "submit_order_to_odoo", submit_mock)
    monkeypatch.setattr(menu_module, "_download_order_media", AsyncMock(return_value=(None, None, None, None)))

    message = _make_message(text="Salom, menga sement kerak")
    state = _make_state(
        data={"lang": "uz", "is_new": True, "partner_name": "Yangi Mijoz", "partner_phone": "+998900000000"}
    )
    bot = MagicMock()

    await menu_module.handle_order_placement(message, state, bot)

    submit_mock.assert_called_once()
    bot.send_message.assert_not_called()
    bot.forward_message.assert_not_called()
    args, _ = message.answer.call_args
    assert args[0] == menu_module.t("generic_error", "uz")


async def test_order_placement_with_photo_passes_downloaded_path_to_odoo(monkeypatch):
    """Buyurtma tafsiloti rasm bo'lsa, avval yuklab olinadi (_download_order_media),
    keyin qaytgan fayl nomi submit_order_to_odoo'ga photo_path sifatida uzatiladi."""
    submit_mock = AsyncMock(return_value=99)
    monkeypatch.setattr(menu_module, "submit_order_to_odoo", submit_mock)
    download_mock = AsyncMock(return_value=("1_777.jpg", None, None, None))
    monkeypatch.setattr(menu_module, "_download_order_media", download_mock)

    message = _make_message(content_type=ContentType.PHOTO, caption="rasm izohi")
    state = _make_state(
        data={"lang": "uz", "is_new": False, "partner_id": 42, "partner_name": "Test Partner", "partner_phone": "-"}
    )
    bot = MagicMock()

    await menu_module.handle_order_placement(message, state, bot)

    download_mock.assert_called_once_with(message, bot)
    assert submit_mock.call_args.kwargs["photo_path"] == "1_777.jpg"
    assert submit_mock.call_args.kwargs["video_path"] is None
    assert submit_mock.call_args.kwargs["audio_path"] is None
    assert submit_mock.call_args.kwargs["document_path"] is None
    bot.send_message.assert_not_called()
    bot.forward_message.assert_not_called()
    message.answer.assert_called_once()


async def test_order_placement_with_document_passes_downloaded_path_to_odoo(monkeypatch):
    """Buyurtma tafsiloti fayl (document, masalan PDF) bo'lsa, avval yuklab olinadi
    (_download_order_media), keyin qaytgan fayl nomi submit_order_to_odoo'ga
    document_path sifatida uzatiladi."""
    submit_mock = AsyncMock(return_value=99)
    monkeypatch.setattr(menu_module, "submit_order_to_odoo", submit_mock)
    download_mock = AsyncMock(return_value=(None, None, None, "1_777.pdf"))
    monkeypatch.setattr(menu_module, "_download_order_media", download_mock)

    message = _make_message(content_type=ContentType.DOCUMENT, caption="shartnoma")
    state = _make_state(
        data={"lang": "uz", "is_new": False, "partner_id": 42, "partner_name": "Test Partner", "partner_phone": "-"}
    )
    bot = MagicMock()

    await menu_module.handle_order_placement(message, state, bot)

    download_mock.assert_called_once_with(message, bot)
    assert submit_mock.call_args.kwargs["document_path"] == "1_777.pdf"
    assert submit_mock.call_args.kwargs["photo_path"] is None
    assert submit_mock.call_args.kwargs["video_path"] is None
    assert submit_mock.call_args.kwargs["audio_path"] is None
    bot.send_message.assert_not_called()
    bot.forward_message.assert_not_called()
    message.answer.assert_called_once()


async def test_download_order_media_photo_downloads_largest_size(monkeypatch):
    message = _make_message(chat_id=1, content_type=ContentType.PHOTO)
    message.message_id = 777
    message.photo = [MagicMock(file_id="small"), MagicMock(file_id="large")]
    bot = MagicMock()
    bot.download = AsyncMock()
    monkeypatch.setattr(menu_module.settings, "ORDER_MEDIA_DIR", "/tmp/order_media_test")

    photo_path, video_path, audio_path, document_path = await menu_module._download_order_media(message, bot)

    assert photo_path == "1_777.jpg"
    assert video_path is None
    assert audio_path is None
    assert document_path is None
    bot.download.assert_called_once()
    assert bot.download.call_args.args[0] == "large"


async def test_download_order_media_video(monkeypatch):
    message = _make_message(chat_id=1, content_type=ContentType.VIDEO)
    message.message_id = 777
    message.video = MagicMock(file_id="video_file_id")
    bot = MagicMock()
    bot.download = AsyncMock()
    monkeypatch.setattr(menu_module.settings, "ORDER_MEDIA_DIR", "/tmp/order_media_test")

    photo_path, video_path, audio_path, document_path = await menu_module._download_order_media(message, bot)

    assert video_path == "1_777.mp4"
    assert photo_path is None
    assert audio_path is None
    assert document_path is None


async def test_download_order_media_voice(monkeypatch):
    message = _make_message(chat_id=1, content_type=ContentType.VOICE)
    message.message_id = 777
    message.voice = MagicMock(file_id="voice_file_id")
    bot = MagicMock()
    bot.download = AsyncMock()
    monkeypatch.setattr(menu_module.settings, "ORDER_MEDIA_DIR", "/tmp/order_media_test")

    photo_path, video_path, audio_path, document_path = await menu_module._download_order_media(message, bot)

    assert audio_path == "1_777.ogg"
    assert photo_path is None
    assert video_path is None
    assert document_path is None


async def test_download_order_media_document(monkeypatch):
    """Fayl (document) yuborilganda asl kengaytma saqlanadi (masalan ".pdf"), lekin
    asl fayl nomi emas -- xavfsizlik uchun disk nomi to'liq {chat_id}_{message_id}
    qolipida generatsiya qilinadi."""
    message = _make_message(chat_id=1, content_type=ContentType.DOCUMENT)
    message.message_id = 777
    message.document = MagicMock(file_id="document_file_id", file_name="shartnoma.pdf")
    bot = MagicMock()
    bot.download = AsyncMock()
    monkeypatch.setattr(menu_module.settings, "ORDER_MEDIA_DIR", "/tmp/order_media_test")

    photo_path, video_path, audio_path, document_path = await menu_module._download_order_media(message, bot)

    assert document_path == "1_777.pdf"
    assert photo_path is None
    assert video_path is None
    assert audio_path is None
    bot.download.assert_called_once()
    assert bot.download.call_args.args[0] == "document_file_id"


async def test_download_order_media_document_without_filename_has_no_suffix(monkeypatch):
    """Telegram ba'zan document.file_name'ni bermasligi mumkin -- shu holda kengaytmasiz
    (lekin baribir chat_id/message_id asosida) fayl nomi generatsiya qilinadi, xato
    berilmaydi."""
    message = _make_message(chat_id=1, content_type=ContentType.DOCUMENT)
    message.message_id = 777
    message.document = MagicMock(file_id="document_file_id", file_name=None)
    bot = MagicMock()
    bot.download = AsyncMock()
    monkeypatch.setattr(menu_module.settings, "ORDER_MEDIA_DIR", "/tmp/order_media_test")

    photo_path, video_path, audio_path, document_path = await menu_module._download_order_media(message, bot)

    assert document_path == "1_777"


async def test_download_order_media_text_returns_nothing():
    message = _make_message(text="matn buyurtma", content_type=ContentType.TEXT)
    bot = MagicMock()
    bot.download = AsyncMock()

    result = await menu_module._download_order_media(message, bot)

    assert result == (None, None, None, None)
    bot.download.assert_not_called()


async def test_download_order_media_error_returns_none(monkeypatch):
    """Yuklab olishda xato (masalan tarmoq) yuz bersa, media None qaytadi -- buyurtma
    matni baribir Odoo'ga yozilishi kerak, faqat media biriktirilmay qoladi."""
    message = _make_message(chat_id=1, content_type=ContentType.PHOTO)
    message.message_id = 777
    message.photo = [MagicMock(file_id="x")]
    bot = MagicMock()
    bot.download = AsyncMock(side_effect=Exception("boom"))
    monkeypatch.setattr(menu_module.settings, "ORDER_MEDIA_DIR", "/tmp/order_media_test")

    result = await menu_module._download_order_media(message, bot)

    assert result == (None, None, None, None)
