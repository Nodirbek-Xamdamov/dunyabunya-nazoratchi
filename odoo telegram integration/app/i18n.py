"""
Botning statik matnlari uchun 2 tilli (o'zbek/rus) tarjima lug'ati.
Odoo'dan kelgan dinamik matn (tijorat taklifi matni) uchun app.translate ishlatiladi.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

LANGUAGES = ("ru", "uz")
DEFAULT_LANGUAGE = "uz"

LANGUAGE_LABELS = {
    "ru": "🇷🇺 Русский",
    "uz": "🇺🇿 O'zbek",
}

CHOOSE_LANGUAGE_TEXT = "🌐 Tilni tanlang / Пожалуйста, выберите язык"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "username_required": {
        "uz": "Assalomu alaykum! Botdan foydalanish uchun Telegram username o'rnatilgan bo'lishi kerak.",
        "ru": "Здравствуйте! Для использования бота необходимо установить имя пользователя (username) в Telegram.",
    },
    "welcome": {
        "uz": "Assalomu alaykum, {name}! 👋\n\nTizimga muvaffaqiyatli ulandiz. Sizning buyurtmalaringiz tekshirilmoqda...",
        "ru": "Здравствуйте, {name}! 👋\n\nВы успешно подключились. Проверяем ваши заказы...",
    },
    "no_pending_orders": {
        "uz": "🔍 Sizning nomingizga kutilayotgan yangi buyurtmalar topilmadi (yoki ularni allaqachon ko'rib chiqqansiz).",
        "ru": "🔍 Ожидающих заказов на ваше имя не найдено (либо вы их уже просмотрели).",
    },
    "btn_confirm": {"uz": "✅ Tasdiqlash", "ru": "✅ Подтвердить"},
    "btn_reject": {"uz": "❌ Rad etish", "ru": "❌ Отклонить"},
    "btn_request_changes": {
        "uz": "✏️ O'zgartirish kiritish",
        "ru": "✏️ Запросить изменения",
    },
    "pdf_caption_fallback": {
        "uz": (
            "Assalomu alaykum! 👋\n\n"
            "Hurmatli mijoz, siz uchun {doc_name}-raqamli tijorat taklifi tayyorlandi va ko'rib chiqishingiz uchun yuborildi.\n\n"
            "Ushbu hujjatda tanlangan mahsulotlar, ularning miqdori, narxlari va buyurtmaning umumiy summasi keltirilgan. "
            "Iltimos, tijorat taklifini diqqat bilan ko'rib chiqing.\n\n"
            "Savollaringiz yoki qo'shimcha ma'lumot kerak bo'lsa, biz bilan bog'lanishingiz mumkin:\n\n"
            "📞 Aloqa uchun: +998 78 113 09 90\n\n"
            "🤝 dunyabunya'ni tanlaganingiz uchun tashakkur!"
        ),
        "ru": (
            "Здравствуйте! 👋\n\n"
            "Уважаемый клиент, для вас подготовлено коммерческое предложение № {doc_name} и отправлено на рассмотрение.\n\n"
            "В этом документе указаны выбранные товары, их количество, цены и общая сумма заказа. "
            "Пожалуйста, внимательно ознакомьтесь с предложением.\n\n"
            "Если у вас есть вопросы или нужна дополнительная информация, свяжитесь с нами:\n\n"
            "📞 Контакт: +998 78 113 09 90\n\n"
            "🤝 Спасибо, что выбрали dunyabunya!"
        ),
    },
    "confirm_caption_suffix": {
        "uz": "\n\n✅ Buyurtma siz tomonidan tasdiqlandi. Rahmat!",
        "ru": "\n\n✅ Заказ подтверждён вами. Спасибо!",
    },
    "delivery_confirmed": {
        "uz": "🚚 Buyurtmangiz (№{doc_name}) yetkazib berildi! Bizni tanlaganingiz uchun tashakkur.",
        "ru": "🚚 Ваш заказ (№{doc_name}) доставлен! Спасибо, что выбрали нас.",
    },
    "confirm_alert": {
        "uz": "Rahmat! Buyurtmangiz tasdiqlandi.",
        "ru": "Спасибо! Ваш заказ подтверждён.",
    },
    "order_not_found": {
        "uz": "Buyurtma topilmadi!",
        "ru": "Заказ не найден!",
    },
    "not_authorized_group_action": {
        "uz": "⛔ Bu tugma faqat ushbu buyurtma uchun mas'ul shaxsga tegishli.",
        "ru": "⛔ Эта кнопка предназначена только для ответственного за этот заказ.",
    },
    "invalid_id": {
        "uz": "Xato ID format!",
        "ru": "Неверный формат ID!",
    },
    "reject_prompt": {
        "uz": "❌ Buyurtmani rad etish sababini qisqacha yozib yuboring.",
        "ru": "❌ Пожалуйста, кратко напишите причину отклонения заказа.",
    },
    "reject_timeout": {
        "uz": "⏰ 5 daqiqa o'tdi va sabab kiritilmadi. Buyurtma rad etildi.",
        "ru": "⏰ Прошло 5 минут, причина не указана. Заказ отклонён.",
    },
    "reject_accepted": {
        "uz": "✅ Rad etish sababingiz menejerga yuborildi. Rahmat!",
        "ru": "✅ Причина отклонения отправлена менеджеру. Спасибо!",
    },
    "group_action_check_dm": {
        "uz": "📩 Guruhda emas, sizga shaxsiy xabar yubordik -- javobingizni o'sha yerda yozing.",
        "ru": "📩 Не здесь -- мы отправили вам личное сообщение, ответьте там.",
    },
    "reject_caption_suffix": {
        "uz": "\n\n❌ Buyurtma rad etildi.",
        "ru": "\n\n❌ Заказ отклонён.",
    },
    "changes_caption_suffix": {
        "uz": "\n\n✏️ Buyurtmaga o'zgartirish so'raldi.",
        "ru": "\n\n✏️ Запрошено изменение заказа.",
    },
    "changes_prompt": {
        "uz": (
            "<b>✏️ Buyurtmaga o'zgartirish kiritish</b>\n\n"
            "Iltimos, buyurtmada nimani o'zgartirish kerakligini aniq yozing: mahsulotni qo'shish yoki olib "
            "tashlash, miqdorini o'zgartirish yoxud boshqa tuzatishlar.\n\n"
            "So'rovingizni matn, rasm, video, ovozli xabar yoki fayl orqali yuborishingiz mumkin.\n\n"
            "⚠️ <i>Barcha materiallarni bitta xabarda yuboring. Fayllarning umumiy hajmi 10 MB dan oshmasligi kerak.</i>\n\n"
            "So'rovingiz ko'rib chiqilgach, yangilangan tijorat taklifi sizga bot orqali yuboriladi."
        ),
        "ru": (
            "<b>✏️ Запрос на изменение заказа</b>\n\n"
            "Пожалуйста, чётко опишите, что нужно изменить в заказе: добавить или убрать товар, "
            "изменить количество или другие исправления.\n\n"
            "Вы можете отправить свой запрос текстом, фото, видео, голосовым сообщением или файлом.\n\n"
            "⚠️ <i>Отправьте все материалы одним сообщением. Общий размер файлов не должен превышать 10 МБ.</i>\n\n"
            "После рассмотрения вашего запроса обновлённое коммерческое предложение будет отправлено вам через бота."
        ),
    },
    "changes_accepted": {
        "uz": "So'rovingiz qabul qilinib xabar menejerga yetkazildi.",
        "ru": "Ваш запрос принят и передан менеджеру.",
    },
    "generic_error": {
        "uz": "Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
        "ru": "Произошла ошибка. Пожалуйста, попробуйте снова.",
    },
    "menu_title": {
        "uz": "📋 Menyu — kerakli bo'limni tanlang:",
        "ru": "📋 Меню — выберите раздел:",
    },
    "menu_orders": {"uz": "🛒 Buyurtma berish", "ru": "🛒 Оформить заказ"},
    "menu_quotation": {"uz": "📄 Tijorat takliflarim", "ru": "📄 Мои предложения"},
    "menu_support": {"uz": "🆘 Yordam", "ru": "🆘 Поддержка"},
    "menu_info": {"uz": "ℹ️ Malumotlarim", "ru": "ℹ️ Мои данные"},
    "menu_statistics": {"uz": "📊 Statistika", "ru": "📊 Статистика"},
    "statistics_title": {
        "uz": "{year} YILGI BUYURTMALAR STATISTIKASI",
        "ru": "СТАТИСТИКА ЗАКАЗОВ ЗА {year} ГОД",
    },
    "statistics_by_object_title": {"uz": "Obyektlar bo'yicha", "ru": "По объектам"},
    "statistics_col_object": {"uz": "Obyekt", "ru": "Объект"},
    "statistics_col_month": {"uz": "Oy", "ru": "Месяц"},
    "statistics_col_spent": {"uz": "Buyurtma (so'm)", "ru": "Заказ (сум)"},
    "statistics_col_returned": {"uz": "Qaytarilgan (so'm)", "ru": "Возвращено (сум)"},
    "statistics_col_paid": {"uz": "To'langan (so'm)", "ru": "Оплачено (сум)"},
    "statistics_row_total": {"uz": "Jami", "ru": "Итого"},
    "menu_placeholder": {
        "uz": "⏳ Bu bo'lim tez orada ishga tushiriladi.",
        "ru": "⏳ Этот раздел скоро будет доступен.",
    },
    "balance_choose_object": {
        "uz": "🏢 Obyektni tanlang:",
        "ru": "🏢 Выберите объект:",
    },
    "balance_no_objects": {
        "uz": "🔍 Sizning nomingizga biriktirilgan obyekt topilmadi.",
        "ru": "🔍 Объект, привязанный к вашему аккаунту, не найден.",
    },
    "balance_total_label": {"uz": "Umumiy balans", "ru": "Общий баланс"},
    "balance_objects_label": {"uz": "Obyektlar", "ru": "Объекты"},
    "balance_none_value": {"uz": "yo'q", "ru": "нет"},
    "balance_no_partner": {
        "uz": "🔍 Sizning Telegram akkountingiz \"dunyabunya\" tizimida topilmadi. Iltimos, supportga murojaat qiling.",
        "ru": "🔍 Ваш Telegram аккаунт не найден в системе «dunyabunya». Пожалуйста, обратитесь в поддержку.",
    },
    "quotation_list_header": {
        "uz": "📄 Sizning tijorat takliflaringiz:",
        "ru": "📄 Ваши коммерческие предложения:",
    },
    "quotation_none_found": {
        "uz": "🔍 Hozircha sizga tegishli tijorat taklifi topilmadi.",
        "ru": "🔍 На данный момент коммерческих предложений для вас не найдено.",
    },
    "quotation_general_label": {"uz": "Umumiy", "ru": "Общие"},
    "info_name_label": {"uz": "F.I.Sh", "ru": "ФИО"},
    "info_phone_label": {"uz": "Telefon", "ru": "Телефон"},
    "info_objects_label": {"uz": "Obyekt(lar)", "ru": "Объект(ы)"},
    "info_balance_label": {"uz": "Balans", "ru": "Баланс"},
    "info_location_label": {"uz": "Lokatsiya", "ru": "Локация"},
    "info_location_link_text": {"uz": "Bor", "ru": "Есть"},
    "statistics_col_balance": {"uz": "Balans", "ru": "Баланс"},
    "support_contact_message": {
        "uz": "🆘 Yordam kerak bo'lsa:\n📞 +998781130990 raqamiga qo'ng'iroq qiling\n💬 Yoki @dunyabunya_support ga yozing",
        "ru": "🆘 Если нужна помощь:\n📞 Позвоните по номеру +998781130990\n💬 Или напишите @dunyabunya_support",
    },
    "support_manager_line": {
        "uz": "👨‍💼 Menejer bilan bog'lanish uchun:\n📞 {phone} ga qo'ng'iroq qiling",
        "ru": "👨‍💼 Для связи с вашим менеджером:\n📞 Позвоните по номеру {phone}",
    },
    "order_placement_prompt": {
        "uz": "🛒 Buyurtma berish uchun nima kerakligini yozing (matn, rasm yoki fayl yuborishingiz mumkin):",
        "ru": "🛒 Опишите, что вы хотите заказать (можно отправить текст, фото или файл):",
    },
    "order_placement_accepted": {
        "uz": "✅ Buyurtmangiz qabul qilindi! Tez orada operatorlarimiz siz bilan bog'lanishadi.",
        "ru": "✅ Ваш заказ принят! Наши операторы скоро свяжутся с вами.",
    },
    "btn_order_new_customer": {
        "uz": "🆕 Yangi mijoz sifatida davom etish",
        "ru": "🆕 Продолжить как новый клиент",
    },
    "btn_create_new_object": {
        "uz": "🆕 Yangi obyekt yaratish",
        "ru": "🆕 Создать новый объект",
    },
    "flow_reset_on_start": {
        "uz": "🔄 Avvalgi amal bekor qilindi.",
        "ru": "🔄 Предыдущее действие отменено.",
    },
    "btn_share_location": {
        "uz": "📍 Turgan joyimni yuboraman",
        "ru": "📍 Отправить моё текущее местоположение",
    },
    "btn_choose_from_map": {
        "uz": "🗺 Xaritadan tanlayman",
        "ru": "🗺 Выбрать на карте",
    },
    "order_no_partner_found": {
        "uz": "🔍 Siz hozircha mijozlar bazamizda topilmadingiz. Yangi mijoz sifatida davom etishingiz mumkin:",
        "ru": "🔍 Вы пока не найдены в нашей базе клиентов. Вы можете продолжить как новый клиент:",
    },
    "order_ask_new_name": {
        "uz": "👤 Ismingizni kiriting:",
        "ru": "👤 Введите ваше имя:",
    },
    "order_ask_new_phone": {
        "uz": "📞 Telefon raqamingizni kiriting:",
        "ru": "📞 Введите ваш номер телефона:",
    },
    "btn_share_phone": {
        "uz": "📱 Raqamni ulashish",
        "ru": "📱 Поделиться номером",
    },
    "order_phone_invalid_format": {
        "uz": "⚠️ Telefon raqami noto'g'ri ko'rinishda. Iltimos, raqamni quyidagi ko'rinishda yuboring: +998 90 123 45 67",
        "ru": "⚠️ Неверный формат номера телефона. Пожалуйста, отправьте номер в следующем формате: +998 90 123 45 67",
    },
    "btn_edit_info": {
        "uz": "✏️ Ma'lumotlarni tahrirlash",
        "ru": "✏️ Редактировать данные",
    },
    "edit_info_no_objects": {
        "uz": "Tahrirlash uchun obyektingiz topilmadi.",
        "ru": "Не найдено объектов для редактирования.",
    },
    "edit_info_choose_object": {
        "uz": "Tahrirlamoqchi bo'lgan obyektingizni tanlang:",
        "ru": "Выберите объект, который хотите отредактировать:",
    },
    "btn_edit_phone": {
        "uz": "📞 Raqamni o'zgartirish",
        "ru": "📞 Изменить номер",
    },
    "btn_edit_location": {
        "uz": "📍 Lokatsiyani o'zgartirish",
        "ru": "📍 Изменить местоположение",
    },
    "edit_info_current_location_none": {
        "uz": "📍 Bu obyektning saqlangan lokatsiyasi yo'q.",
        "ru": "📍 У этого объекта нет сохранённого местоположения.",
    },
    "edit_info_choose_field": {
        "uz": "Nimani o'zgartirmoqchisiz?",
        "ru": "Что вы хотите изменить?",
    },
    "edit_info_ask_new_phone": {
        "uz": "📞 Yangi telefon raqamini kiriting:",
        "ru": "📞 Введите новый номер телефона:",
    },
    "edit_info_ask_new_location": {
        "uz": "📍 Xaritadan yangi lokatsiyani tanlang, yoki turgan joyingizni yuboring:",
        "ru": "📍 Выберите новое местоположение на карте, или отправьте своё текущее местоположение:",
    },
    "edit_info_phone_updated": {
        "uz": "✅ Telefon raqami muvaffaqiyatli yangilandi.",
        "ru": "✅ Номер телефона успешно обновлён.",
    },
    "edit_info_location_updated": {
        "uz": "✅ Lokatsiya muvaffaqiyatli yangilandi.",
        "ru": "✅ Местоположение успешно обновлено.",
    },
    "edit_info_update_failed": {
        "uz": "❌ Yangilashda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.",
        "ru": "❌ Произошла ошибка при обновлении. Пожалуйста, попробуйте позже.",
    },
    "order_ask_address_text": {
        "uz": "🏢 Yetkazib berish obyektini yarating (masalan: Chilonzor tumani, 5-uy):",
        "ru": "🏢 Создайте объект доставки (например: Чиланзарский район, дом 5):",
    },
    "order_ask_location": {
        "uz": "📍 Xaritadan yetkazib berish manzilini tanlang, yoki turgan joyingizni yuboring:",
        "ru": "📍 Выберите место доставки на карте, или отправьте своё текущее местоположение:",
    },
    "order_confirm_location": {
        "uz": "📍 Obyektning saqlangan lokatsiyasi shu. To'g'rimi?",
        "ru": "📍 Это сохранённое местоположение объекта. Всё верно?",
    },
    "btn_keep_location": {"uz": "✅ Buyurtma berish", "ru": "✅ Оформить заказ"},
    "btn_change_location": {
        "uz": "📍 Lokatsiyani o'zgartirish",
        "ru": "📍 Изменить местоположение",
    },
    "order_map_instructions": {
        "uz": "🗺 Xabar yozish maydoni yonidagi 📎 (qog'oz-qisqich) belgisini bosing → \"Location\" → xaritada kerakli nuqtani belgilab, \"Yuborish\"ni bosing.",
        "ru": "🗺 Нажмите на значок 📎 (скрепка) рядом с полем ввода → \"Геопозиция\" → отметьте нужную точку на карте и нажмите \"Отправить\".",
    },
    "order_invalid_location": {
        "uz": "⚠️ Iltimos, pastdagi tugmalardan birini tanlang: xaritadan joy belgilang yoki turgan joyingizni yuboring.",
        "ru": "⚠️ Пожалуйста, выберите одну из кнопок ниже: отметьте место на карте или отправьте своё текущее местоположение.",
    },
    "btn_cancel_order": {"uz": "❌ Bekor qilish", "ru": "❌ Отмена"},
    "order_cancelled": {
        "uz": "❌ Buyurtma berish bekor qilindi. Kerak bo'lsa, qaytadan boshlashingiz mumkin.",
        "ru": "❌ Оформление заказа отменено. Вы можете начать заново в любое время.",
    },
    "contact_auto_reply": {
        "uz": (
            "Ushbu bot faqat xabar yuborish uchun ishlatiladi va yozgan xabaringizga javob qaytarmaydi.\n\n"
            "Savollaringiz bo'lsa, quyidagi raqamga murojaat qiling:\n"
            "📞 +998 78 113 09 90"
        ),
        "ru": (
            "Этот бот используется только для отправки уведомлений и не отвечает на ваши сообщения.\n\n"
            "Если у вас есть вопросы, обращайтесь:\n"
            "📞 +998 78 113 09 90"
        ),
    },
}


def t(key: str, lang: str | None, **kwargs) -> str:
    """Berilgan kalit uchun `lang` tilidagi matnni qaytaradi (topilmasa o'zbekchaga tushadi)."""
    lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE
    entry = TRANSLATIONS.get(key, {})
    text = entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or ""
    if kwargs:
        text = text.format(**kwargs)
    return text


def get_language_keyboard() -> InlineKeyboardMarkup:
    """Til tanlash uchun 2 ta bayroqli tugma (bitta qatorda: ru, uz)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=LANGUAGE_LABELS[code], callback_data=f"lang_{code}") for code in LANGUAGES]
        ]
    )
