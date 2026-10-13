"""app.balance.format_info_message formatlash funksiyasini sinaydi."""

from app.balance import _format_amount, format_info_message


# --- Belgi (musbat/manfiy) inversiyasi (2026-08-02) ---------------------------------
# Odoo'dagi x_uzs_balance/x_usd_balance belgisi mijozga ko'rsatilishi kerak bo'lgan
# ma'noga teskari saqlanadi: Odoo'da "-" bo'lsa mijozga "-"siz, Odoo'da "-" bo'lmasa
# (musbat yoki 0) mijozga "-" bilan ko'rsatilishi kerak.


def test_format_amount_inverts_negative_odoo_value_to_positive():
    assert _format_amount(-160000) == "160,000"


def test_format_amount_inverts_positive_odoo_value_to_negative():
    assert _format_amount(160000) == "-160,000"


def test_format_amount_zero_stays_zero_without_minus():
    assert _format_amount(0) == "0"
    assert _format_amount(0.0) == "0"


def test_format_amount_none_stays_zero():
    assert _format_amount(None) == "0"


def test_format_info_message_shows_inverted_sign_for_negative_odoo_balance():
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": -160000.0, "x_usd_balance": 0.0}
    text = format_info_message(partner, [], "uz")
    assert "160,000" in text
    assert "-160,000" not in text


def test_format_info_message_includes_general_balance():
    """2026-07-30 -- alohida "Balance" bo'limi olib tashlangach, Malumotlarim (Info)
    javobi mijozning joriy UZS/USD balansini ham o'z ichiga oladi (sarlavha qatori va
    "Umumiy" so'zi keyinchalik olib tashlandi -- shunchaki qisqa "Balans" ko'rsatiladi)."""
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 1500000.0, "x_usd_balance": 100.0}
    text = format_info_message(partner, [], "uz")
    assert "Balans" in text
    assert "Umumiy" not in text
    assert "Sizning ma'lumotlaringiz" not in text
    assert "1,500,000" in text
    assert "100" in text


def test_format_info_message_phone_appears_before_balance():
    """2026-08-05: mijoz aniq so'ragan -- umumiy bo'limda telefon raqami balansdan
    OLDIN chiqishi kerak (F.I.Sh -> Telefon -> Balans)."""
    # Odoo'dagi belgi mijozga ko'rsatiladigan ma'noga teskari (_format_amount'ga qarang)
    # -- shu sabab manfiy Odoo qiymati mijozga musbat ko'rinishda chiqadi.
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": -1500000.0, "x_usd_balance": 0.0}
    text = format_info_message(partner, [], "uz")
    lines = text.splitlines()

    assert lines[0] == "👤 F.I.Sh: Test Partner"
    assert lines[1] == "📞 Telefon: +998901234567"
    assert lines[2] == "💰 Balans (UZS): 1,500,000"


def test_format_info_message_strips_phone_separators_for_tap_to_call():
    """2026-08-05, mijoz aniq so'ragan: telefon raqami bo'sh joy/tire kabi
    ajratkichlarsiz, uzluksiz ko'rinishda chiqishi kerak -- Telegram shunday
    formatni "bosilsa qo'ng'iroq" havolasi sifatida ishonchliroq aniqlaydi."""
    partner = {
        "name": "Test Partner", "phone": "+998 90 123-45-67",
        "x_uzs_balance": 0.0, "x_usd_balance": 0.0,
    }
    text = format_info_message(partner, [], "uz")
    assert "📞 Telefon: +998901234567" in text
    assert "+998 90 123-45-67" not in text


def test_format_info_message_shows_yoq_when_phone_has_no_digits():
    """Odoo'da ba'zan telefon maydonida raqamsiz axlat qiymat ("q" kabi) uchraydi --
    tozalashdan keyin bo'sh qator chiqib qolmasligi, "yo'q" ko'rsatilishi kerak
    (2026-08-05, live tekshiruvda topilgan chekka holat)."""
    partner = {"name": "Test Partner", "phone": "q", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    text = format_info_message(partner, [], "uz")
    assert "📞 Telefon: yo'q" in text


def test_format_info_message_shows_yoq_when_balance_missing():
    partner = {"name": "Test Partner", "phone": "+998901234567"}
    text = format_info_message(partner, [], "uz")
    assert "yo'q" in text


def test_format_info_message_lists_each_object_with_its_balance():
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {"id": 1, "name": "NAZARBEK", "x_uzs_balance": 500000.0, "x_usd_balance": 0.0},
        {"id": 2, "name": "ISTIROXAT", "x_uzs_balance": 200000.0, "x_usd_balance": 15.5},
    ]
    text = format_info_message(partner, objects, "uz")
    assert "NAZARBEK" in text
    assert "500,000" in text
    assert "ISTIROXAT" in text
    assert "200,000" in text
    assert "15.5" in text


# --- Har bir obyekt o'z alohida blokida: nom + (bor bo'lsa) telefon + (nolmas bo'lsa)
# balans (2026-08-03, mijoz aniq so'ragan format) ------------------------------------


def test_format_info_message_adds_998_prefix_when_missing():
    """2026-08-06, mijoz aniq so'ragan: raqam 998 kodisiz 9 ta raqamdan iborat
    bo'lsa ("931234567" kabi), +998 avtomatik qo'shiladi."""
    partner = {"name": "Test Partner", "phone": "931234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    text = format_info_message(partner, [], "uz")
    assert "📞 Telefon: +998931234567" in text


def test_format_info_message_object_with_phone_and_nonzero_balance():
    """2026-08-05: obyekt blokida telefon -> lokatsiya -> balans tartibida chiqadi
    (mijoz aniq shunday so'ragan)."""
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {
            "id": 1, "name": "Olmazor", "phone": "+998991112233", "mobile": False,
            "x_uzs_balance": -50000.0, "x_usd_balance": 0.0,
        },
    ]
    text = format_info_message(partner, objects, "uz")
    lines = text.splitlines()

    obj_index = lines.index("🏢 Olmazor")
    assert lines[obj_index + 1] == "  📞 +998991112233"
    # lokatsiya (bo'lmasa ham "yo'q" sifatida, 2026-08-04) HAR DOIM ko'rsatiladi
    assert lines[obj_index + 2] == "  📍 Lokatsiya: yo'q"
    # USD 0 bo'lgani uchun umuman ko'rsatilmasligi kerak
    assert lines[obj_index + 3] == "  💰 Balans (UZS): 50,000"
    assert len(lines) == obj_index + 4


def test_format_info_message_object_with_zero_balance_hides_balance_lines():
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {
            "id": 1, "name": "Qo'yliq", "phone": "+998901112233", "mobile": False,
            "x_uzs_balance": 0.0, "x_usd_balance": 0.0,
        },
    ]
    text = format_info_message(partner, objects, "uz")
    lines = text.splitlines()

    obj_index = lines.index("🏢 Qo'yliq")
    assert lines[obj_index + 1] == "  📞 +998901112233"
    assert lines[obj_index + 2] == "  📍 Lokatsiya: yo'q"
    assert len(lines) == obj_index + 3  # sarlavha + telefon + lokatsiya, balans qatori yo'q


def test_format_info_message_object_without_phone_omits_phone_line():
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {"id": 1, "name": "Shiribom", "phone": False, "mobile": False, "x_uzs_balance": 0.0, "x_usd_balance": 0.0},
    ]
    text = format_info_message(partner, objects, "uz")
    lines = text.splitlines()

    obj_index = lines.index("🏢 Shiribom")
    assert lines[obj_index + 1] == "  📍 Lokatsiya: yo'q"
    assert len(lines) == obj_index + 2  # sarlavha + lokatsiya -- na telefon, na balans


def test_format_info_message_object_falls_back_to_mobile_when_no_phone():
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {"id": 1, "name": "Olmazor", "phone": False, "mobile": "+998997778899", "x_uzs_balance": 0.0, "x_usd_balance": 0.0},
    ]
    text = format_info_message(partner, objects, "uz")
    assert "+998997778899" in text


def test_format_info_message_multiple_objects_separated_by_blank_line():
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {"id": 1, "name": "Olmazor", "phone": False, "mobile": False, "x_uzs_balance": 0.0, "x_usd_balance": 0.0},
        {"id": 2, "name": "Chilonzor", "phone": False, "mobile": False, "x_uzs_balance": 0.0, "x_usd_balance": 0.0},
    ]
    text = format_info_message(partner, objects, "uz")
    lines = text.splitlines()

    olmazor_index = lines.index("🏢 Olmazor")
    chilonzor_index = lines.index("🏢 Chilonzor")
    assert lines[olmazor_index - 1] == ""
    assert lines[chilonzor_index - 1] == ""


# --- Obyekt GPS lokatsiyasi (2026-08-03, mijoz aniq so'ragan: Malumotlarim bosilganda
# obyektning lokatsiyasi ham birga chiqishi kerak) -----------------------------------


def test_format_info_message_object_with_location_shows_short_link():
    """2026-08-04, mijoz aniq so'ragan: uzun `https://maps.google.com/?q=lat,lon` URL'i
    o'rniga qisqa "Bor" matnli HTML havola ko'rsatiladi (xabar uzun bo'lib
    chiqmasligi uchun) -- shu sabab natija albatta `parse_mode="HTML"` bilan
    yuborilishi kerak."""
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {
            "id": 1, "name": "NAZARBEK", "phone": False, "mobile": False,
            "x_uzs_balance": 0.0, "x_usd_balance": 0.0,
            "partner_latitude": 41.311081, "partner_longitude": 69.279737,
        },
    ]
    text = format_info_message(partner, objects, "uz")
    lines = text.splitlines()

    obj_index = lines.index("🏢 NAZARBEK")
    assert lines[obj_index + 1] == (
        '  📍 Lokatsiya: <a href="https://maps.google.com/?q=41.311081,69.279737">Bor</a>'
    )


def test_format_info_message_object_without_location_shows_yoq():
    """0.0/0.0 -- Odoo'ning "lokatsiya kiritilmagan" degani (odoo_client.py'dagi izohga
    qarang) -- 2026-08-04'dan boshlab bu holatda ham qator ko'rsatiladi (avval umuman
    chiqmas edi), "Lokatsiya: yo'q" sifatida, mijoz aniq so'ragan."""
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}
    objects = [
        {
            "id": 1, "name": "ISTIROXAT", "phone": False, "mobile": False,
            "x_uzs_balance": 0.0, "x_usd_balance": 0.0,
            "partner_latitude": 0.0, "partner_longitude": 0.0,
        },
    ]
    text = format_info_message(partner, objects, "uz")
    lines = text.splitlines()

    obj_index = lines.index("🏢 ISTIROXAT")
    assert lines[obj_index + 1] == "  📍 Lokatsiya: yo'q"
    assert "maps.google.com" not in text


def test_format_info_message_no_objects_shows_none_value():
    partner = {"name": "Test Partner", "phone": "+998901234567"}
    text = format_info_message(partner, [], "uz")
    assert "yo'q" in text


def test_format_info_message_language_aware():
    partner = {"name": "Test Partner", "phone": "+998901234567", "x_uzs_balance": 100.0, "x_usd_balance": 0.0}
    text_uz = format_info_message(partner, [], "uz")
    text_ru = format_info_message(partner, [], "ru")
    assert "Balans" in text_uz
    assert "Баланс" in text_ru
