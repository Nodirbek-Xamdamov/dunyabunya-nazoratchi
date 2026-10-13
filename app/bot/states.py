"""Aiogram FSM holatlari."""

from aiogram.fsm.state import State, StatesGroup


class OrderFeedbackState(StatesGroup):
    waiting_for_feedback = State()
    waiting_for_reject_reason = State()


class OrderPlacementState(StatesGroup):
    """"Buyurtma berish" tugmasi -- oqim: mijoz Odoo'dan chat_id bo'yicha qidiriladi, keyin
    obyekt tanlanadi YOKI (yangi obyekt yaratilayotganda) to'g'ridan-to'g'ri manzil matn
    bilan (obyekt nomi sifatida saqlanadi) va GPS lokatsiya bilan yig'iladi, so'ngra
    buyurtma tafsiloti so'ralib Odoo'ga (crm.lead) uzatiladi. `waiting_for_new_name`/
    `_phone` FAQAT mijoz Odoo'da UMUMAN topilmaganda ishlatiladi (butunlay yangi mijoz --
    partner_id yo'q, crm.lead'ga contact_name/phone sifatida yoziladi); Odoo'da MA'LUM
    mijozning yangi obyekt yaratishi endi ism/telefonni QAYTA so'ramaydi (2026-08-03'da
    qo'shilgan bu qadam 2026-08-05'da, mijoz aniq so'ragan bo'yicha, olib tashlandi --
    asosiy mijozning o'z partner_name/partner_phone'i ishlatiladi).

    MAVJUD obyekt tanlanganda, agar Odoo'da bu obyekt uchun lokatsiya ALLAQACHON bo'lsa
    (2026-08-03, ikkinchi marta qayta ko'rib chiqilgan): endi qayta so'ralmaydi, buning
    o'rniga saqlangan lokatsiya ko'rsatilib waiting_for_location_confirmation holatiga
    o'tiladi -- mijoz "Buyurtma berish" (shu lokatsiya to'g'ri) yoki "Lokatsiyani
    o'zgartirish" (waiting_for_location'ga o'tadi, yangisi so'raladi) tugmasidan birini
    tanlaydi. Lokatsiya umuman bo'lmasa (yangi obyekt) hamon to'g'ridan-to'g'ri
    waiting_for_location'ga o'tiladi."""

    waiting_for_new_name = State()
    waiting_for_new_phone = State()
    waiting_for_address_text = State()
    waiting_for_location = State()
    waiting_for_location_confirmation = State()
    waiting_for_order = State()


class EditInfoState(StatesGroup):
    """"Ma'lumotlarim" natijasi ostidagi "Ma'lumotlarni tahrirlash" tugmasi -- oqim:
    mijozning obyektlari ro'yxati ko'rsatiladi (waiting_for_object_selection), obyekt
    tanlangach mavjud lokatsiya ko'rsatilib raqam/lokatsiya o'zgartirish tugmalari
    chiqadi (waiting_for_field_choice), tanlangan maydonga qarab yangi qiymat so'raladi
    (waiting_for_new_phone -- OrderPlacementState bilan bir xil format qoidasi va
    kontakt-ulashish tugmasi; waiting_for_new_location -- OrderPlacementState bilan bir
    xil xarita/joriy-joylashuv tugmalari) va Odoo'ga (res.partner.write, FastAPI orqali)
    to'g'ridan-to'g'ri yoziladi (2026-08-05)."""

    waiting_for_object_selection = State()
    waiting_for_field_choice = State()
    waiting_for_new_phone = State()
    waiting_for_new_location = State()
