"""Ma'lumotlarni tahrirlash -- Malumotlarim (Info) natijasi ostidagi "Ma'lumotlarni
tahrirlash" tugmasi orqali, mijoz o'z obyektlaridan birini tanlab, o'sha obyektning
mas'ul shaxs telefon raqami va/yoki GPS lokatsiyasini o'zgartirishi mumkin (2026-08-05).
Yozish FastAPI orqali Odoo'ga (res.partner.write) amalga oshiriladi -- arxitektura
qoidasiga ko'ra bot konteyneri odoo_client'ga to'g'ridan-to'g'ri murojaat qilmaydi
(app/bot/__init__.py'dagi arxitektura qoidasiga qarang). O'zgartirilgan qiymat
to'g'ridan-to'g'ri Odoo'ning o'z res.partner yozuviga yoziladi -- boshqa poll+mark-
synced patternlardan (chat-ids/group-memberships/object-locations) farqli, bu yerda
Odoo tarafida alohida GET endpoint/cron KERAK EMAS, chunki o'zgarish o'sha zahoti
Odoo'ning o'z bazasida ko'rinadi (odoo_client.update_object_contact'ga qarang)."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.menu import (
    CANCEL_BUTTON_TEXTS,
    MAP_CHOICE_BUTTON_TEXTS,
    _is_valid_phone_format,
    fetch_order_customer_context,
)
from app.bot.internal_api import get_internal_client
from app.bot.keyboards import (
    get_edit_info_field_keyboard,
    get_edit_info_object_list_keyboard,
    get_location_request_keyboard,
    get_menu_reply_keyboard,
    get_phone_request_keyboard,
)
from app.bot.states import EditInfoState
from app.config import settings
from app.database import async_session_maker
from app.i18n import t
from app.utils import get_user_language

logger = logging.getLogger("aiogram_bot")

router = Router(name="edit_info")


async def _update_object(
    object_id: int,
    object_name: str,
    chat_id: int,
    username: str | None,
    partner_id: int | None,
    *,
    phone: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> bool:
    """Obyektning telefon raqami/lokatsiyasini FastAPI (`POST /api/v1/bot/object-update`)
    orqali Odoo'ga yozadi. `object_name`/`chat_id`/`username`/`partner_id` Odoo'ga
    yozilmaydi -- muvaffaqiyatli bo'lsa FAQAT ObjectEditLog'ga log sifatida saqlanadi
    (Odoo xodimlari GET /api/v1/object-edits orqali "qaysi obyekt o'zgardi" deb
    so'rashi mumkin, 2026-08-05)."""
    try:
        client = get_internal_client()
        resp = await client.post(
            "/api/v1/bot/object-update",
            json={
                "object_id": object_id,
                "object_name": object_name,
                "chat_id": chat_id,
                "username": username,
                "partner_id": partner_id,
                "phone": phone,
                "latitude": latitude,
                "longitude": longitude,
            },
            headers={"X-API-Key": settings.API_KEY},
        )
        if resp.status_code == 200:
            return bool(resp.json().get("success"))
    except Exception:
        logger.exception("Obyekt ma'lumotini yangilashda xatolik (object_id=%s)", object_id)
    return False


@router.callback_query(F.data == "edit_info_start")
async def handle_edit_info_start(callback: CallbackQuery, state: FSMContext):
    """"Ma'lumotlarni tahrirlash" tugmasi bosilganda: mijozning obyektlarini Odoo'dan
    oladi (xuddi "Buyurtma berish" oqimidagi bilan bir xil lookup) va tanlash uchun
    ro'yxat ko'rsatadi. Obyekt topilmasa (mijoz Odoo'da yo'q, yoki hali obyekti yo'q)
    tushuntiruvchi xabar ko'rsatiladi, FSM holati o'zgarmaydi."""
    chat_id = callback.message.chat.id
    async with async_session_maker() as session:
        lang = await get_user_language(session, chat_id)

    context = await fetch_order_customer_context(chat_id)
    objects = context.get("objects") or []

    if not context.get("found") or not objects:
        await callback.message.answer(t("edit_info_no_objects", lang))
        await callback.answer()
        return

    await state.update_data(lang=lang, objects=objects, partner_id=context.get("partner_id"))
    await state.set_state(EditInfoState.waiting_for_object_selection)
    await callback.message.answer(
        t("edit_info_choose_object", lang),
        reply_markup=get_edit_info_object_list_keyboard(lang, objects),
    )
    await callback.answer()


@router.callback_query(
    EditInfoState.waiting_for_object_selection, F.data.startswith("editinfo_objsel_")
)
async def handle_edit_info_object_selected(callback: CallbackQuery, state: FSMContext):
    """Obyekt tanlangach: saqlangan lokatsiyani (bor bo'lsa native Telegram lokatsiya
    xabari, bo'lmasa "yo'q" matni bilan) ko'rsatadi, so'ngra raqam/lokatsiya o'zgartirish
    tugmalarini chiqaradi -- xuddi handle_order_object_selected'dagi bilan bir xil
    edit_text(qisqa yorliq) + answer_location + answer(keyingi so'rov) naqshi."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    try:
        object_id = int(callback.data.removeprefix("editinfo_objsel_"))
    except ValueError:
        await callback.answer()
        return

    selected = next((o for o in data.get("objects", []) if o.get("id") == object_id), None)
    if not selected:
        await callback.message.edit_text(t("balance_no_objects", lang))
        await callback.answer()
        return

    object_name = selected["name"]
    latitude, longitude = selected.get("latitude"), selected.get("longitude")

    await state.update_data(object_id=object_id, object_name=object_name)
    await state.set_state(EditInfoState.waiting_for_field_choice)

    await callback.message.edit_text(f"🏢 {object_name}")
    if latitude is not None and longitude is not None:
        await callback.message.answer_location(latitude=latitude, longitude=longitude)
    else:
        await callback.message.answer(t("edit_info_current_location_none", lang))
    await callback.message.answer(t("edit_info_choose_field", lang), reply_markup=get_edit_info_field_keyboard(lang))
    await callback.answer()


@router.callback_query(EditInfoState.waiting_for_field_choice, F.data == "editinfo_field_phone")
async def handle_edit_info_field_phone(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.set_state(EditInfoState.waiting_for_new_phone)
    await callback.message.answer(
        t("edit_info_ask_new_phone", lang), reply_markup=get_phone_request_keyboard(lang)
    )
    await callback.answer()


@router.callback_query(EditInfoState.waiting_for_field_choice, F.data == "editinfo_field_location")
async def handle_edit_info_field_location(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.set_state(EditInfoState.waiting_for_new_location)
    await callback.message.answer(
        t("edit_info_ask_new_location", lang), reply_markup=get_location_request_keyboard(lang)
    )
    await callback.answer()


@router.message(EditInfoState.waiting_for_new_phone)
async def handle_edit_info_new_phone(message: Message, state: FSMContext):
    """Raqam qabul qilinadi (qo'lda yozilgan matn -- `_is_valid_phone_format` orqali
    tekshiriladi, yoki kontakt-ulashish tugmasi orqali -- qayta tekshirilmaydi, xuddi
    OrderPlacementState.waiting_for_new_phone bilan bir xil qoida, 2026-08-05, mijoz
    aniq so'ragan: "hozirgi qoida unga ham")."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    if message.contact:
        phone = message.contact.phone_number
    else:
        if message.text in CANCEL_BUTTON_TEXTS:
            await state.clear()
            await message.answer(t("order_cancelled", lang), reply_markup=get_menu_reply_keyboard(lang))
            return
        phone = (message.text or "").strip()
        if not phone:
            await message.answer(t("edit_info_ask_new_phone", lang))
            return
        if not _is_valid_phone_format(phone):
            await message.answer(t("order_phone_invalid_format", lang))
            return

    username = message.from_user.username if message.from_user else None
    success = await _update_object(
        data["object_id"], data.get("object_name", "-"), message.chat.id, username, data.get("partner_id"),
        phone=phone,
    )
    await state.clear()
    result_key = "edit_info_phone_updated" if success else "edit_info_update_failed"
    await message.answer(t(result_key, lang), reply_markup=get_menu_reply_keyboard(lang))


@router.message(EditInfoState.waiting_for_new_location, F.location)
async def handle_edit_info_new_location(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    latitude, longitude = message.location.latitude, message.location.longitude

    username = message.from_user.username if message.from_user else None
    success = await _update_object(
        data["object_id"], data.get("object_name", "-"), message.chat.id, username, data.get("partner_id"),
        latitude=latitude, longitude=longitude,
    )
    await state.clear()
    result_key = "edit_info_location_updated" if success else "edit_info_update_failed"
    await message.answer(t(result_key, lang), reply_markup=get_menu_reply_keyboard(lang))


@router.message(EditInfoState.waiting_for_new_location, F.text.in_(MAP_CHOICE_BUTTON_TEXTS))
async def handle_edit_info_location_map_choice(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await message.answer(t("order_map_instructions", lang))


@router.message(EditInfoState.waiting_for_new_location)
async def handle_edit_info_location_invalid(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")

    if message.text in CANCEL_BUTTON_TEXTS:
        await state.clear()
        await message.answer(t("order_cancelled", lang), reply_markup=get_menu_reply_keyboard(lang))
        return

    await message.answer(t("order_invalid_location", lang), reply_markup=get_location_request_keyboard(lang))
