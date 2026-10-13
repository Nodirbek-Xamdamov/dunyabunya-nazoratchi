"""Botning barcha klaviaturalarini generatsiya qiluvchi funksiyalar."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.i18n import t


def get_order_inline_keyboard(order_id: int, lang: str) -> InlineKeyboardMarkup:
    """Buyurtma bo'yicha harakat tugmalari (tanlangan tilda)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("btn_confirm", lang), callback_data=f"confirm_{order_id}"),
                InlineKeyboardButton(text=t("btn_reject", lang), callback_data=f"reject_{order_id}"),
            ],
            [
                InlineKeyboardButton(text=t("btn_request_changes", lang), callback_data=f"request_changes_{order_id}")
            ],
        ]
    )


def get_order_object_list_keyboard(lang: str, objects: list[dict]) -> InlineKeyboardMarkup:
    """Buyurtma berish -- mijozning mavjud obyektlaridan birini tanlash, YOKI ularning
    ostidagi tugma orqali butunlay yangi obyekt yaratishni boshlash (2026-08-02: obyekt
    soni 1 ta bo'lsa ham endi shu ro'yxat ko'rsatiladi, avtomatik tanlanmaydi -- har bir
    obyekt uchun lokatsiya qo'shish qadami endi majburiy)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=obj["name"], callback_data=f"order_objsel_{obj['id']}")] for obj in objects
        ]
        + [[InlineKeyboardButton(text=t("btn_create_new_object", lang), callback_data="order_obj_new")]]
    )


def get_order_new_customer_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Buyurtma berish -- mijoz Odoo'da topilmaganda: "Yangi mijoz" oqimini boshlash tugmasi."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_order_new_customer", lang), callback_data="order_new_customer")]
        ]
    )


def get_location_request_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Buyurtma berish -- yetkazib berish nuqtasini olish uchun ikkita tugma: (1) Telegram'ning
    o'z "joriy joylashuvni yuborish" tugmasi (native, request_location=True -- Bot API bo'yicha
    bu FAQAT qurilmaning joriy GPS joylashuvini yuboradi, xaritadan boshqa nuqta tanlashni
    to'g'ridan-to'g'ri ochib bera olmaydi); (2) oddiy matn tugmasi -- bosilganda foydalanuvchiga
    Telegram'ning o'z xarita-tanlash interfeysidan (📎 qog'oz-qisqich → Location → xaritadan
    nuqta belgilash) qanday foydalanishni tushuntiradi (app.bot.handlers.menu.
    handle_order_location_map_choice). Ikkala yo'l bilan kelgan location ham backend'da bir
    xil qabul qilinadi (handle_order_location, F.location filtri manba bo'yicha farqlamaydi)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_share_location", lang), request_location=True)],
            [KeyboardButton(text=t("btn_choose_from_map", lang))],
            [KeyboardButton(text=t("btn_cancel_order", lang))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_phone_request_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Buyurtma berish -- telefon raqami so'ralayotganda, Telegram'ning o'z "kontakt
    ulashish" (request_contact=True) tugmasi -- bosilsa foydalanuvchining Telegram
    profiliga bog'langan raqami avtomatik, kontakt ko'rinishida yuboriladi, qo'lda
    yozish shart emas (2026-08-05, mijoz aniq so'ragan)."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("btn_share_phone", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_location_confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Buyurtma berish -- mavjud obyekt uchun Odoo'da ALLAQACHON saqlangan lokatsiya
    ko'rsatilgandan keyin (2026-08-03): mijoz shu lokatsiyani tasdiqlab to'g'ridan-to'g'ri
    buyurtma tafsilotiga o'tishi (keep), yoki noto'g'ri deb topib yangisini yuborishi
    (change) mumkin."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("btn_keep_location", lang), callback_data="order_loc_keep"),
                InlineKeyboardButton(text=t("btn_change_location", lang), callback_data="order_loc_change"),
            ]
        ]
    )


def get_cancel_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Buyurtma berish oqimida (manzil/lokatsiya/buyurtma tafsiloti so'ralayotgan
    bosqichlarda) chiqariladigan yagona "Bekor qilish" tugmasi -- mijoz ism/telefonni
    kiritgandan keyin lokatsiya yoki buyurtma tafsilotini berishni istamasa, oqimdan
    aniq chiqib keta olishi uchun (2026-08-03). Ism/telefon so'ralayotganda (oqimning eng
    boshida) ataylab ko'rsatilmaydi -- shu ikki qadam juda qisqa, foydalanuvchi so'rovi
    aynan "ism va telefondan KEYIN" bekor qilish imkoniyati haqida edi."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("btn_cancel_order", lang))]],
        resize_keyboard=True,
    )


def get_edit_info_button_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Ma'lumotlarim (Info) natijasi ostida ko'rsatiladigan yagona "Ma'lumotlarni
    tahrirlash" tugmasi (2026-08-05)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("btn_edit_info", lang), callback_data="edit_info_start")]]
    )


def get_edit_info_object_list_keyboard(lang: str, objects: list[dict]) -> InlineKeyboardMarkup:
    """Ma'lumotlarni tahrirlash -- tahrirlamoqchi bo'lgan obyektni tanlash (2026-08-05).
    `get_order_object_list_keyboard`dan farqli -- bu yerda "Yangi obyekt yaratish"
    tugmasi yo'q (mavjud obyektni tahrirlash uchun, yangisini yaratish uchun emas)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=obj["name"], callback_data=f"editinfo_objsel_{obj['id']}")]
            for obj in objects
        ]
    )


def get_edit_info_field_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Ma'lumotlarni tahrirlash -- obyekt tanlangandan keyin: qaysi maydonni
    o'zgartirish (raqam yoki lokatsiya) tanlash tugmalari (2026-08-05)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_edit_phone", lang), callback_data="editinfo_field_phone")],
            [InlineKeyboardButton(text=t("btn_edit_location", lang), callback_data="editinfo_field_location")],
        ]
    )


def get_group_not_found_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    """Guruhda botni mention qilgan, lekin Odoo'da topilmagan foydalanuvchiga ko'rsatiladigan
    tugma -- botni shaxsiy chatda ochib /start bosishga olib boradigan link (2026-08-03).
    Guruh xabarlari doim O'zbekcha (bitta aniq qabul qiluvchi til afzalligi yo'q, xuddi
    call-center forward va userbot invite matni kabi) -- shu sabab bu yerda t()/lang shart
    emas."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Ro'yxatdan o'tish", url=f"https://t.me/{bot_username.lstrip('@')}")]
        ]
    )


def get_menu_reply_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """
    Asosiy menyu: Orders, Quotation, Support, Malumotlarim, Statistika -- doimiy pastki
    (Reply) klaviatura sifatida. Alohida "hamburger"/trigger tugma shart emas: Telegram
    ReplyKeyboardMarkup biriktirilgan xabar yozish maydoni yonida o'zining tabiiy
    ochish/yopish (toggle) belgisini avtomatik ko'rsatadi (`is_persistent=False`
    bo'lgani uchun klaviatura boshida yopiq holatda turadi). Alohida "Balans" tugmasi
    2026-07-30'da olib tashlandi -- balans endi "Malumotlarim" (Info) tarkibida.
    "Statistika" (2026-08-04) -- oxirgi 12 oylik xarajat/bekor qilingan hisobotini PDF
    shaklida yuboradi, shu sabab yakka o'zi alohida qatorda (juft tugmalar qatoridan
    farqli, chunki uni boshqa tugma bilan bir qatorga qo'shish shart emas edi).
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("menu_orders", lang)), KeyboardButton(text=t("menu_quotation", lang))],
            [KeyboardButton(text=t("menu_support", lang)), KeyboardButton(text=t("menu_info", lang))],
            [KeyboardButton(text=t("menu_statistics", lang))],
        ],
        resize_keyboard=True,
        is_persistent=False,
    )
