"""app.bot.handlers.orders uchun testlar: guruhga yuborilgan buyurtmalarda faqat
mas'ul shaxs (order.chat_id) Tasdiqlash/Rad etish tugmalarini bosa olishini,
individual buyurtmalarda esa bunday cheklov yo'qligini, va rad etish/o'zgartirish
sababi kutilayotganda menyu tugmasi (yoki /start, /menu) bosilsa bu matn SABAB
sifatida saqlanmasligini tekshiradi."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import orders as orders_module
from app.bot.states import OrderFeedbackState
from app.database import async_session_maker
from app.i18n import t
from app.models import ConfirmationStatus, Order


def _make_callback(data: str, from_user_id: int, chat_id: int):
    callback = MagicMock()
    callback.data = data
    callback.from_user.id = from_user_id
    callback.message.chat.id = chat_id
    callback.message.message_id = 42424242
    callback.message.caption = "Test caption"
    callback.message.edit_caption = AsyncMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot.id = 900900900
    callback.bot.send_message = AsyncMock()
    callback.bot.edit_message_caption = AsyncMock()
    return callback


def _make_message(text: str, chat_id: int = 1):
    message = MagicMock()
    message.text = text
    message.caption = None
    message.photo = None
    message.voice = None
    message.audio = None
    message.document = None
    message.chat.id = chat_id
    message.answer = AsyncMock()
    return message


def _make_state():
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    state.get_data = AsyncMock(return_value={})
    state.clear = AsyncMock()
    # Guruh buyurtmalarida `_start_group_aware_feedback` HAQIQIY FSMContext'ni bosgan
    # shaxsning shaxsiy chat kaliti bilan yasab, shu `storage`ga yozadi (2026-08-06) --
    # shu sabab bu yerda haqiqiy (lekin xotiradagi, tashqi bog'lanishsiz) MemoryStorage
    # kerak, faqat mock emas.
    state.storage = MemoryStorage()
    return state


async def _make_order(session, **overrides) -> Order:
    defaults = dict(
        raw_username="user",
        normalized_username="user",
        order_details="{}",
        confirmation_status=ConfirmationStatus.PENDING.value,
    )
    defaults.update(overrides)
    order = Order(**defaults)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def test_group_order_confirm_rejected_for_wrong_user():
    async with async_session_maker() as session:
        order = await _make_order(session, group_id=-100123, chat_id=555)

    callback = _make_callback(f"confirm_{order.id}", from_user_id=999, chat_id=-100123)
    await orders_module.process_confirm(callback, _make_state())

    callback.answer.assert_called_once()
    _, kwargs = callback.answer.call_args
    assert kwargs.get("show_alert") is True
    callback.message.edit_caption.assert_not_called()

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.PENDING.value
        assert refreshed.feedback_text is None


async def test_group_order_confirm_allowed_for_authorized_user():
    async with async_session_maker() as session:
        order = await _make_order(session, group_id=-100123, chat_id=555)

    callback = _make_callback(f"confirm_{order.id}", from_user_id=555, chat_id=-100123)
    await orders_module.process_confirm(callback, _make_state())

    callback.message.edit_caption.assert_called_once()

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.CONFIRMED.value
        assert refreshed.feedback_text == orders_module.CONFIRMED_NOTICE_TEXT


async def test_individual_order_confirm_has_no_authorization_restriction():
    """Individual (guruh bo'lmagan) buyurtmalarda group_id yo'q -- shu sabab
    from_user tekshiruvi qo'llanilmaydi, chat_id boshqacha bo'lsa ham tasdiqlanadi."""
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555)  # group_id=None

    callback = _make_callback(f"confirm_{order.id}", from_user_id=999, chat_id=555)
    await orders_module.process_confirm(callback, _make_state())

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.CONFIRMED.value
        assert refreshed.feedback_text == orders_module.CONFIRMED_NOTICE_TEXT


async def test_group_order_reject_rejected_for_wrong_user():
    async with async_session_maker() as session:
        order = await _make_order(session, group_id=-100123, chat_id=555)

    callback = _make_callback(f"reject_{order.id}", from_user_id=999, chat_id=-100123)
    state = _make_state()

    await orders_module.process_reject(callback, state)

    callback.answer.assert_called_once()
    _, kwargs = callback.answer.call_args
    assert kwargs.get("show_alert") is True
    state.set_state.assert_not_called()


async def test_group_order_reject_allowed_for_authorized_user(monkeypatch):
    """Guruh buyurtmasi uchun sabab endi GURUHDA emas, bosgan shaxsning shaxsiy (DM)
    chatida so'raladi (2026-08-06) -- shu sabab ORIGINAL `state` (guruh kaliti bilan
    bog'langan) ENDI ishlatilmaydi; o'rniga bosgan shaxsning shaxsiy chat kaliti bilan
    yasalgan alohida (haqiqiy) FSMContext'ga yoziladi. `asyncio.sleep` mock qilingani
    uchun 300s kutish ONA (darhol qaytadi) -- bu haqiqiy javob uchun vaqt qoldirmaydi,
    shu sabab vaqt-tugashi bo'yicha avto-rad etish ham SHU test ichida ishga tushadi
    (buni ham tekshiramiz -- ikkalasi ham to'g'ri DM'ga borishi kerak)."""
    monkeypatch.setattr(orders_module.asyncio, "sleep", AsyncMock())

    async with async_session_maker() as session:
        order = await _make_order(session, group_id=-100123, chat_id=555)

    callback = _make_callback(f"reject_{order.id}", from_user_id=555, chat_id=-100123)
    state = _make_state()

    await orders_module.process_reject(callback, state)

    state.set_state.assert_not_called()

    assert callback.bot.send_message.call_count == 2
    for call in callback.bot.send_message.call_args_list:
        assert call.kwargs["chat_id"] == 555

    private_key = StorageKey(bot_id=callback.bot.id, chat_id=555, user_id=555)
    private_state = FSMContext(storage=state.storage, key=private_key)
    assert (await private_state.get_state()) is None  # timeout orqali tozalandi

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.REJECTED.value


async def test_group_order_reject_falls_back_to_group_when_dm_fails(monkeypatch):
    """Bosgan shaxsga DM yuborib bo'lmasa (masalan botni bloklagan bo'lsa) -- xavfsizlik
    uchun eski xatti-harakatga (guruhning o'ziga so'rash) qaytiladi."""
    monkeypatch.setattr(orders_module.asyncio, "sleep", AsyncMock())

    async with async_session_maker() as session:
        order = await _make_order(session, group_id=-100124, chat_id=555)

    callback = _make_callback(f"reject_{order.id}", from_user_id=555, chat_id=-100124)
    callback.bot.send_message = AsyncMock(side_effect=Exception("bloklangan"))
    state = _make_state()
    state.get_state = AsyncMock(return_value="some_other_state")

    await orders_module.process_reject(callback, state)

    state.set_state.assert_called_once_with(OrderFeedbackState.waiting_for_reject_reason)
    callback.message.answer.assert_called_once()


# --- Rad etish/o'zgartirish sababi kutilayotganda menyu tugmasi bosilsa ------------
# ("Malumot" muammosi: foydalanuvchi sababni yozmasdan boshqa menyu tugmasini bossa,
# o'sha tugma matni SABAB sifatida saqlanib, Odoo'ga shu holda ketmasligi kerak.)


async def test_reject_reason_ignores_menu_button_text_and_leaves_order_pending():
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555)

    state = _make_state()
    state.get_data = AsyncMock(return_value={"order_id": order.id, "lang": "uz"})

    message = _make_message(t("menu_info", "uz"), chat_id=555)

    with pytest.raises(SkipHandler):
        await orders_module.handle_reject_reason(message, state, bot=MagicMock())

    state.clear.assert_called_once()
    message.answer.assert_not_called()

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.PENDING.value
        assert refreshed.feedback_text is None


@pytest.mark.parametrize("command_text", ["/start", "/menu"])
async def test_reject_reason_ignores_start_and_menu_commands(command_text):
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555)

    state = _make_state()
    state.get_data = AsyncMock(return_value={"order_id": order.id, "lang": "uz"})

    message = _make_message(command_text, chat_id=555)

    with pytest.raises(SkipHandler):
        await orders_module.handle_reject_reason(message, state, bot=MagicMock())

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.PENDING.value


async def test_reject_reason_still_accepts_real_text_as_before():
    """Naqsh haqiqiy (menyu tugmasi bo'lmagan) matnni buzmasligini tekshiradi --
    bu himoya faqat aniq zaxira/buyruq matnlarini tutib qoladi."""
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555)

    state = _make_state()
    state.get_data = AsyncMock(return_value={"order_id": order.id, "lang": "uz"})

    message = _make_message("Narxi juda qimmat, shuning uchun bekor qilaman", chat_id=555)

    await orders_module.handle_reject_reason(message, state, bot=MagicMock())

    state.clear.assert_called_once()
    message.answer.assert_called_once()

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.REJECTED.value
        assert refreshed.feedback_text == "Narxi juda qimmat, shuning uchun bekor qilaman"


# --- Guruh buyurtmasi: DM orqali sabab/o'zgartirish qabul qilingach, guruhdagi asl
# PDF xabarining sarlavhasi yakuniy holat bilan yangilanadi (2026-08-06) -------------


async def test_reject_reason_updates_group_caption_when_group_order():
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555, group_id=-100125)

    state = _make_state()
    state.get_data = AsyncMock(return_value={
        "order_id": order.id,
        "lang": "uz",
        "group_chat_id": -100125,
        "group_message_id": 777,
        "group_caption": "Original caption",
    })
    message = _make_message("Narxi qimmat", chat_id=555)
    bot = MagicMock()
    bot.edit_message_caption = AsyncMock()

    await orders_module.handle_reject_reason(message, state, bot=bot)

    bot.edit_message_caption.assert_called_once()
    _, kwargs = bot.edit_message_caption.call_args
    assert kwargs["chat_id"] == -100125
    assert kwargs["message_id"] == 777
    assert kwargs["caption"] == "Original caption" + t("reject_caption_suffix", "uz")


async def test_feedback_input_updates_group_caption_when_group_order():
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555, group_id=-100126)

    state = _make_state()
    state.get_data = AsyncMock(return_value={
        "order_id": order.id,
        "lang": "uz",
        "group_chat_id": -100126,
        "group_message_id": 888,
        "group_caption": "Original caption 2",
    })
    message = _make_message("Rangini o'zgartiring", chat_id=555)
    bot = MagicMock()
    bot.edit_message_caption = AsyncMock()

    await orders_module.handle_feedback_input(message, state, bot=bot)

    bot.edit_message_caption.assert_called_once()
    _, kwargs = bot.edit_message_caption.call_args
    assert kwargs["chat_id"] == -100126
    assert kwargs["message_id"] == 888
    assert kwargs["caption"] == "Original caption 2" + t("changes_caption_suffix", "uz")


async def test_reject_reason_does_not_touch_group_caption_for_individual_order():
    """Individual (DM) buyurtmada `group_chat_id`/`group_message_id` FSM data'da yo'q --
    guruh sarlavhasini yangilashga urinilmasligi kerak (no-op)."""
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555)

    state = _make_state()
    state.get_data = AsyncMock(return_value={"order_id": order.id, "lang": "uz"})
    message = _make_message("Narxi qimmat", chat_id=555)
    bot = MagicMock()
    bot.edit_message_caption = AsyncMock()

    await orders_module.handle_reject_reason(message, state, bot=bot)

    bot.edit_message_caption.assert_not_called()


# --- synced=False reset (2026-08-06): Odoo pollaydi (`GET /api/v1/chat-ids?synced=false`)
# va allaqachon synced=True deb belgilagan bo'lishi mumkin (masalan hali "pending"
# bo'lganida bir marta so'rab ulgurgan bo'lsa) -- shu sabab confirmation_status har
# safar o'zgarganda `synced` MAJBURIY False'ga qaytarilishi kerak, aks holda Odoo
# tasdiqlash/rad etish/o'zgartirish natijasini HECH QACHON ko'rmaydi (real production
# bug -- tugmalar ishlayapti, lekin Odoo hech narsa olmayapti deb xabar berilgan).


async def test_confirm_resets_synced_flag_so_odoo_repolls():
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555, synced=True)

    callback = _make_callback(f"confirm_{order.id}", from_user_id=555, chat_id=555)
    await orders_module.process_confirm(callback, _make_state())

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.CONFIRMED.value
        assert refreshed.synced is False


async def test_reject_reason_resets_synced_flag():
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555, synced=True)

    state = _make_state()
    state.get_data = AsyncMock(return_value={"order_id": order.id, "lang": "uz"})
    message = _make_message("Narxi juda qimmat", chat_id=555)

    await orders_module.handle_reject_reason(message, state, bot=MagicMock())

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.REJECTED.value
        assert refreshed.synced is False


async def test_reject_timeout_resets_synced_flag(monkeypatch):
    monkeypatch.setattr(orders_module.asyncio, "sleep", AsyncMock())

    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555, synced=True)

    callback = _make_callback(f"reject_{order.id}", from_user_id=555, chat_id=555)
    state = _make_state()
    state.get_state = AsyncMock(return_value=OrderFeedbackState.waiting_for_reject_reason.state)
    state.get_data = AsyncMock(return_value={"order_id": order.id})

    await orders_module.process_reject(callback, state)

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.REJECTED.value
        assert refreshed.synced is False


async def test_feedback_input_resets_synced_flag():
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555, synced=True)

    state = _make_state()
    state.get_data = AsyncMock(return_value={"order_id": order.id, "lang": "uz"})
    message = _make_message("Rangini o'zgartiring", chat_id=555)

    await orders_module.handle_feedback_input(message, state, bot=MagicMock())

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.CHANGES_REQUESTED.value
        assert refreshed.synced is False


async def test_feedback_input_ignores_menu_button_text_and_leaves_order_pending():
    async with async_session_maker() as session:
        order = await _make_order(session, chat_id=555)

    state = _make_state()
    state.get_data = AsyncMock(return_value={"order_id": order.id, "lang": "uz"})

    message = _make_message(t("menu_quotation", "uz"), chat_id=555)

    with pytest.raises(SkipHandler):
        await orders_module.handle_feedback_input(message, state, bot=MagicMock())

    state.clear.assert_called_once()

    async with async_session_maker() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed.confirmation_status == ConfirmationStatus.PENDING.value
        assert refreshed.feedback_text is None
