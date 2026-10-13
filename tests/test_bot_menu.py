"""Bot menyusi (Orders/Quotation/Support/Info) endpointlarini sinaydi. Quotation va Info
Odoo'ga XML-RPC orqali real vaqtli so'rov yuboradi -- shu sabab app.odoo_client
funksiyalari monkeypatch orqali soxtalashtiriladi (haqiqiy tarmoqqa so'rov ketmaydi).
Eski /api/v1/odoo/balance/sync push endpointi hamon mavjud (orqaga moslik uchun) va
alohida sinaladi, lekin endi hech qanday bot javobiga ta'sir qilmaydi (2026-07-30'da
alohida "Balance" endpointi va menyu tugmasi butunlay olib tashlandi -- balans endi
Info javobining bir qismi)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings

API_HEADERS = {"X-API-Key": settings.API_KEY}

MENU_PATHS = [
    "/api/v1/bot/orders",
]


@pytest_asyncio.fixture
async def client():
    from app.fastapi_app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.parametrize("path", MENU_PATHS)
async def test_bot_menu_endpoint_returns_placeholder_message(client, path):
    resp = await client.post(
        path, json={"chat_id": 12345, "username": "testuser", "lang": "ru"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message"]  # bo'sh bo'lmasligi kifoya


async def test_bot_menu_endpoint_rejects_missing_api_key(client):
    resp = await client.post("/api/v1/bot/orders", json={"chat_id": 12345})
    assert resp.status_code == 422


# --- Support: 2026-08-06, endi Odoo'dan mijozga biriktirilgan menejerni so'raydi
# (avval doim menu_placeholder qaytaradigan placeholder edi) -------------------------


async def test_support_partner_not_found_shows_static_message(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)

    resp = await client.post(
        "/api/v1/bot/support", json={"chat_id": 12345, "username": "testuser", "lang": "uz"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == fastapi_app_module.t("support_contact_message", "uz")


async def test_support_partner_found_without_manager_shows_static_message(client, monkeypatch):
    """user_id Odoo'da o'rnatilmagan (False) -- get_partner_manager None qaytaradi."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test"})
    monkeypatch.setattr(fastapi_app_module, "get_partner_manager", lambda partner_id: None)

    resp = await client.post(
        "/api/v1/bot/support", json={"chat_id": 5817708955, "username": "testuser", "lang": "uz"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == fastapi_app_module.t("support_contact_message", "uz")


async def test_support_manager_without_phone_shows_static_message(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test"})
    monkeypatch.setattr(
        fastapi_app_module, "get_partner_manager", lambda partner_id: {"name": "Menejer Ism", "phone": None}
    )

    resp = await client.post(
        "/api/v1/bot/support", json={"chat_id": 5817708955, "username": "testuser", "lang": "uz"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == fastapi_app_module.t("support_contact_message", "uz")


async def test_support_manager_found_shows_both_general_and_manager_message(client, monkeypatch):
    """2026-08-06, mijoz aniq so'ragan: menejer topilsa umumiy support_contact_message
    ALMASHTIRILMAYDI -- ikkalasi (umumiy xabar + menejer qatori) BIRGA chiqadi."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test"})
    monkeypatch.setattr(
        fastapi_app_module,
        "get_partner_manager",
        lambda partner_id: {"name": "Hasanxo'jayev Sardor", "phone": "+998 90 970 28 58"},
    )

    resp = await client.post(
        "/api/v1/bot/support", json={"chat_id": 5817708955, "username": "testuser", "lang": "uz"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    message = resp.json()["message"]
    assert message == (
        fastapi_app_module.t("support_contact_message", "uz")
        + "\n\n"
        + fastapi_app_module.t("support_manager_line", "uz", phone="+998909702858")
    )
    # raqam ajratkichlarsiz va +998 bilan chiqishi kerak (asl "+998 90 970 28 58" edi)
    assert "+998909702858" in message
    assert "+998 90 970 28 58" not in message


async def test_support_manager_phone_without_country_code_gets_prefixed(client, monkeypatch):
    """Menejerning Odoo'dagi raqami 998'siz (9 xonali) bo'lsa ham +998 qo'shilishi kerak."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test"})
    monkeypatch.setattr(
        fastapi_app_module, "get_partner_manager", lambda partner_id: {"name": "Menejer", "phone": "909702858"}
    )

    resp = await client.post(
        "/api/v1/bot/support", json={"chat_id": 5817708955, "username": "testuser", "lang": "uz"}, headers=API_HEADERS
    )
    assert "+998909702858" in resp.json()["message"]


# --- Eski balance sync (push) endpointi -- endi bot javobiga ta'sir qilmaydi, lekin
# Odoo hali chaqirishi mumkinligi uchun ishlashda davom etadi ------------------------


async def test_sync_odoo_balance_rejects_missing_api_key(client):
    resp = await client.post("/api/v1/odoo/balance/sync", json={"chat_id": 1, "partner_name": "X"})
    assert resp.status_code == 422


async def test_sync_odoo_balance_creates_then_updates_same_chat_id(client):
    async def _sync(chat_id, partner_name, total_balance_uzs):
        resp = await client.post(
            "/api/v1/odoo/balance/sync",
            json={
                "chat_id": chat_id,
                "partner_name": partner_name,
                "total_balance_uzs": total_balance_uzs,
                "total_balance_usd": 0.0,
                "objects": [],
            },
            headers=API_HEADERS,
        )
        assert resp.status_code == 200

    await _sync(5, "Old Name", 100.0)
    await _sync(5, "New Name", 200.0)
    # Sinxronlash muvaffaqiyatli va bir xil chat_id uchun qayta yozish (create keyin
    # update) ishlayotganini tekshiramiz -- bot balance javobi endi bu keshni o'qimaydi.


# --- Tijorat takliflarim: Odoo'ga real vaqtli so'rov -------------------------------


async def test_quotation_no_partner_found(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)

    resp = await client.post(
        "/api/v1/bot/quotation", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["message"]


async def test_quotation_none_found(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test", "x_uzs_balance": 0.0}
    )
    monkeypatch.setattr(fastapi_app_module, "get_customer_objects", lambda partner_id: [])
    monkeypatch.setattr(fastapi_app_module, "get_quotations", lambda partner_ids: [])

    resp = await client.post(
        "/api/v1/bot/quotation", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["message"]


async def test_quotation_lists_results(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test", "x_uzs_balance": 0.0}
    )
    monkeypatch.setattr(
        fastapi_app_module, "get_customer_objects", lambda partner_id: [{"id": 1, "name": "Obyekt 1", "x_uzs_balance": 0.0}]
    )

    captured_partner_ids = []

    def fake_get_quotations(partner_ids):
        captured_partner_ids.extend(partner_ids)
        return [
            {"name": "S00123", "amount_total": 1500000.0, "date_order": "2026-07-01 10:00:00"},
        ]

    monkeypatch.setattr(fastapi_app_module, "get_quotations", fake_get_quotations)

    resp = await client.post(
        "/api/v1/bot/quotation", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS
    )
    assert resp.status_code == 200
    message = resp.json()["message"]
    assert "S00123" in message
    assert "1,500,000" in message
    assert captured_partner_ids == [42, 1]


async def test_quotation_groups_results_by_object(client, monkeypatch):
    """2026-08-03: mijoz aniq so'ragan -- takliflar obyektlari bo'yicha guruhlangan holda
    chiqishi kerak. Guruhlash `partner_shipping_id` ("Yetkazib berish manzili") orqali --
    `partner_id` shu Odoo instansiyasida har doim asosiy mijozning o'zi ekani live
    tekshirilib, avvalgi partner_id-asosli guruhlash shu maydonga o'tkazilgan edi (Har
    bir taklif Odoo'dan `partner_shipping_id` sifatida [id, display_name] qaytaradi --
    XML-RPC many2one standart shakli); aniq yetkazib berish manzili tanlanmagan (asosiy
    mijozning o'ziga teng) takliflar "Umumiy" sarlavhasi ostida chiqadi."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test", "x_uzs_balance": 0.0}
    )
    monkeypatch.setattr(
        fastapi_app_module,
        "get_customer_objects",
        lambda partner_id: [{"id": 1, "name": "NAZARBEK", "x_uzs_balance": 0.0}],
    )
    monkeypatch.setattr(
        fastapi_app_module,
        "get_quotations",
        lambda partner_ids: [
            {
                "name": "S001", "amount_total": 100.0, "date_order": "2026-07-30 10:00:00",
                "partner_id": [42, "Test"], "partner_shipping_id": [1, "NAZARBEK"],
            },
            {
                "name": "S002", "amount_total": 200.0, "date_order": "2026-07-23 10:00:00",
                "partner_id": [42, "Test"], "partner_shipping_id": [42, "Test"],
            },
        ],
    )

    resp = await client.post("/api/v1/bot/quotation", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    message = resp.json()["message"]

    assert "🏢 NAZARBEK" in message
    assert "🏢 Umumiy" in message
    assert message.index("🏢 NAZARBEK") < message.index("S001") < message.index("🏢 Umumiy") < message.index("S002")


# --- Buyurtma berish: mijozni Odoo'dan qidirish (Odoo'ga real vaqtli so'rov) -------


async def test_order_customer_lookup_no_partner_found(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)

    resp = await client.get("/api/v1/bot/order-customer-lookup", params={"chat_id": 999}, headers=API_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["objects"] == []


async def test_order_customer_lookup_found_without_objects(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {"id": 42, "name": "Test Partner", "phone": "+998901234567", "mobile": False},
    )
    monkeypatch.setattr(fastapi_app_module, "get_customer_objects", lambda partner_id: [])

    resp = await client.get("/api/v1/bot/order-customer-lookup", params={"chat_id": 1}, headers=API_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["partner_id"] == 42
    assert body["partner_name"] == "Test Partner"
    assert body["partner_phone"] == "+998901234567"
    assert body["objects"] == []


async def test_order_customer_lookup_found_with_objects(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {"id": 42, "name": "Test Partner", "phone": False, "mobile": "+998907654321"},
    )
    monkeypatch.setattr(
        fastapi_app_module,
        "get_customer_objects",
        lambda partner_id: [{"id": 1, "name": "NAZARBEK", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}],
    )

    resp = await client.get("/api/v1/bot/order-customer-lookup", params={"chat_id": 1}, headers=API_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["partner_phone"] == "+998907654321"  # phone yo'q bo'lgani uchun mobile'ga tushadi
    # partner_latitude/partner_longitude berilmagani uchun (Odoo 0.0/0.0 default) None bo'lishi kerak
    assert body["objects"] == [{"id": 1, "name": "NAZARBEK", "latitude": None, "longitude": None}]


async def test_order_customer_lookup_object_with_saved_location(client, monkeypatch):
    """2026-08-02: Odoo'ning o'z partner_latitude/partner_longitude maydonlari
    to'ldirilgan bo'lsa, o'sha qiymatlar objects ro'yxatida qaytishi kerak -- bot buni
    ko'rib, shu obyekt tanlanganda lokatsiyani qayta so'ramaydi."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test Partner"}
    )
    monkeypatch.setattr(
        fastapi_app_module,
        "get_customer_objects",
        lambda partner_id: [
            {
                "id": 1,
                "name": "NAZARBEK",
                "x_uzs_balance": 0.0,
                "x_usd_balance": 0.0,
                "partner_latitude": 41.311,
                "partner_longitude": 69.279,
            }
        ],
    )

    resp = await client.get("/api/v1/bot/order-customer-lookup", params={"chat_id": 1}, headers=API_HEADERS)
    body = resp.json()
    assert body["objects"] == [{"id": 1, "name": "NAZARBEK", "latitude": 41.311, "longitude": 69.279}]


async def test_order_customer_lookup_rejects_missing_api_key(client):
    resp = await client.get("/api/v1/bot/order-customer-lookup", params={"chat_id": 1})
    assert resp.status_code == 422


# --- Buyurtma berish: Odoo'da mavjud mijozning buyurtmasini crm.lead sifatida yozish ---


async def test_order_submit_creates_lead_and_returns_id(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return 123

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", fake_create)

    resp = await client.post(
        "/api/v1/bot/order-submit",
        json={
            "partner_id": 42,
            "partner_name": "Test Partner",
            "chat_id": 1,
            "order_details": "2 dona sement kerak",
            "object_name": "NAZARBEK",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["lead_id"] == 123
    assert captured["partner_id"] == 42
    assert captured["object_name"] == "NAZARBEK"
    assert captured["latitude"] is None
    assert captured["phone"] is None


async def test_order_submit_new_customer_without_partner_id(client, monkeypatch):
    """2026-08-02: partner_id yuborilmasa (butunlay yangi mijoz), endpoint xato
    qaytarmasligi va create_telegram_order_lead'ga partner_id=None, partner_phone
    bilan chaqirilishi kerak."""
    import app.fastapi_app as fastapi_app_module

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return 124

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", fake_create)

    resp = await client.post(
        "/api/v1/bot/order-submit",
        json={
            "partner_name": "Yangi Mijoz",
            "partner_phone": "+998900000000",
            "chat_id": 1,
            "order_details": "2 dona sement kerak",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["lead_id"] == 124
    assert captured["partner_id"] is None
    assert captured["phone"] == "+998900000000"


async def test_order_submit_with_photo_uploads_ir_attachment_and_saves_media_row(client, monkeypatch):
    """2026-08-03: photo_path berilsa, ORDER_MEDIA_DIR'dagi fayl o'qib base64'ga
    o'giriladi va create_ir_attachment(lead_id, filename, base64, mimetype) sifatida
    Odoo'ga (haqiqiy ir.attachment, URL-havola EMAS) yuboriladi -- shu bilan birga
    OrderMediaAttachment jadvaliga (lead_id bilan) mahalliy nusxa ham saqlanadi."""
    import base64

    import app.fastapi_app as fastapi_app_module
    from app.database import async_session_maker
    from app.models import OrderMediaAttachment
    from sqlalchemy import select

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: 125)

    captured = {}

    def fake_attachment(lead_id, filename, base64_data, mimetype):
        captured.update(lead_id=lead_id, filename=filename, base64_data=base64_data, mimetype=mimetype)
        return 999

    monkeypatch.setattr(fastapi_app_module, "create_ir_attachment", fake_attachment)

    fastapi_app_module.ORDER_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    media_file = fastapi_app_module.ORDER_MEDIA_DIR / "1_777.jpg"
    media_file.write_bytes(b"fakejpegdata")
    try:
        resp = await client.post(
            "/api/v1/bot/order-submit",
            json={
                "partner_id": 42,
                "partner_name": "Test Partner",
                "chat_id": 1,
                "order_details": "2 dona sement kerak",
                "photo_path": "1_777.jpg",
            },
            headers=API_HEADERS,
        )
    finally:
        media_file.unlink(missing_ok=True)

    assert resp.status_code == 200
    assert captured["lead_id"] == 125
    assert captured["filename"] == "1_777.jpg"
    assert captured["mimetype"] == "image/jpeg"
    assert base64.b64decode(captured["base64_data"]) == b"fakejpegdata"

    async with async_session_maker() as session:
        rows = (await session.execute(select(OrderMediaAttachment))).scalars().all()
    assert len(rows) == 1
    assert rows[0].chat_id == 1
    assert rows[0].lead_id == 125
    assert rows[0].photo_path == "1_777.jpg"


async def test_order_submit_with_document_uploads_ir_attachment_with_guessed_mimetype(client, monkeypatch):
    """2026-08-03: document_path berilsa (Telegram "fayl"), photo/video/audio'dan farqli
    o'laroq mimetype qat'iy emas -- fayl kengaytmasidan (mimetypes.guess_type) topiladi
    va create_ir_attachment'ga shu bilan yuboriladi; OrderMediaAttachment.document_path
    ham saqlanadi."""
    import base64

    import app.fastapi_app as fastapi_app_module
    from app.database import async_session_maker
    from app.models import OrderMediaAttachment
    from sqlalchemy import select

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: 130)

    captured = {}

    def fake_attachment(lead_id, filename, base64_data, mimetype):
        captured.update(lead_id=lead_id, filename=filename, base64_data=base64_data, mimetype=mimetype)
        return 1000

    monkeypatch.setattr(fastapi_app_module, "create_ir_attachment", fake_attachment)

    fastapi_app_module.ORDER_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    media_file = fastapi_app_module.ORDER_MEDIA_DIR / "1_777.pdf"
    media_file.write_bytes(b"fakepdfdata")
    try:
        resp = await client.post(
            "/api/v1/bot/order-submit",
            json={
                "partner_id": 42,
                "partner_name": "Test Partner",
                "chat_id": 1,
                "order_details": "shartnoma yuboraman",
                "document_path": "1_777.pdf",
            },
            headers=API_HEADERS,
        )
    finally:
        media_file.unlink(missing_ok=True)

    assert resp.status_code == 200
    assert captured["lead_id"] == 130
    assert captured["filename"] == "1_777.pdf"
    assert captured["mimetype"] == "application/pdf"
    assert base64.b64decode(captured["base64_data"]) == b"fakepdfdata"

    async with async_session_maker() as session:
        rows = (await session.execute(select(OrderMediaAttachment))).scalars().all()
    assert len(rows) == 1
    assert rows[0].document_path == "1_777.pdf"


async def test_order_submit_with_document_unknown_extension_falls_back_to_octet_stream(client, monkeypatch):
    """Kengaytma tanish bo'lmasa (yoki umuman yo'q bo'lsa), mimetype
    "application/octet-stream"ga tushadi -- Odoo'ga yuklash baribir davom etadi."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: 131)

    captured = {}

    def fake_attachment(lead_id, filename, base64_data, mimetype):
        captured.update(mimetype=mimetype)
        return 1001

    monkeypatch.setattr(fastapi_app_module, "create_ir_attachment", fake_attachment)

    fastapi_app_module.ORDER_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    media_file = fastapi_app_module.ORDER_MEDIA_DIR / "1_777"
    media_file.write_bytes(b"unknownbytes")
    try:
        resp = await client.post(
            "/api/v1/bot/order-submit",
            json={
                "partner_id": 42,
                "partner_name": "Test Partner",
                "chat_id": 1,
                "order_details": "fayl yuboraman",
                "document_path": "1_777",
            },
            headers=API_HEADERS,
        )
    finally:
        media_file.unlink(missing_ok=True)

    assert resp.status_code == 200
    assert captured["mimetype"] == "application/octet-stream"


async def test_order_submit_attachment_upload_failure_does_not_fail_whole_request(client, monkeypatch):
    """Agar ir.attachment yaratish muvaffaqiyatsiz bo'lsa (masalan Odoo xatosi), lead
    o'zi allaqachon yaratilgani uchun butun so'rov baribir success=True qaytarishi
    kerak -- faqat log qilinadi."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: 125)
    monkeypatch.setattr(fastapi_app_module, "create_ir_attachment", lambda *a: None)

    fastapi_app_module.ORDER_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    media_file = fastapi_app_module.ORDER_MEDIA_DIR / "1_777.jpg"
    media_file.write_bytes(b"fakejpegdata")
    try:
        resp = await client.post(
            "/api/v1/bot/order-submit",
            json={
                "partner_id": 42,
                "partner_name": "Test Partner",
                "chat_id": 1,
                "order_details": "2 dona sement kerak",
                "photo_path": "1_777.jpg",
            },
            headers=API_HEADERS,
        )
    finally:
        media_file.unlink(missing_ok=True)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["lead_id"] == 125


async def test_order_submit_without_media_does_not_create_attachment_row(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module
    from app.database import async_session_maker
    from app.models import OrderMediaAttachment
    from sqlalchemy import select

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: 126)

    await client.post(
        "/api/v1/bot/order-submit",
        json={"partner_id": 42, "partner_name": "Test Partner", "chat_id": 1, "order_details": "x"},
        headers=API_HEADERS,
    )

    async with async_session_maker() as session:
        rows = (await session.execute(select(OrderMediaAttachment))).scalars().all()
    assert rows == []


async def test_order_submit_media_row_saved_even_if_odoo_write_fails(client, monkeypatch):
    """Odoo yozuvi muvaffaqiyatsiz bo'lsa ham (lead_id=None), media mahalliy DB'da
    saqlanib qolishi kerak -- yuklab olingan fayl yo'qolib qolmasligi uchun."""
    import app.fastapi_app as fastapi_app_module
    from app.database import async_session_maker
    from app.models import OrderMediaAttachment
    from sqlalchemy import select

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: None)

    await client.post(
        "/api/v1/bot/order-submit",
        json={
            "partner_id": 42,
            "partner_name": "Test Partner",
            "chat_id": 1,
            "order_details": "x",
            "photo_path": "1_777.jpg",
        },
        headers=API_HEADERS,
    )

    async with async_session_maker() as session:
        rows = (await session.execute(select(OrderMediaAttachment))).scalars().all()
    assert len(rows) == 1
    assert rows[0].lead_id is None
    assert rows[0].photo_path == "1_777.jpg"


async def test_order_submit_odoo_failure_returns_success_false(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: None)

    resp = await client.post(
        "/api/v1/bot/order-submit",
        json={"partner_id": 42, "partner_name": "Test Partner", "chat_id": 1, "order_details": "x"},
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["lead_id"] is None


async def test_order_submit_rejects_missing_api_key(client):
    resp = await client.post(
        "/api/v1/bot/order-submit",
        json={"partner_id": 42, "partner_name": "Test Partner", "chat_id": 1, "order_details": "x"},
    )
    assert resp.status_code == 422


# --- Ma'lumot (Info): Odoo'ga real vaqtli so'rov ------------------------------------


async def test_info_no_partner_found(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)

    resp = await client.post("/api/v1/bot/info", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["message"]


async def test_info_returns_name_phone_and_objects(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {"id": 42, "name": "Nodir test uchun", "phone": "+998901234567", "mobile": False},
    )
    monkeypatch.setattr(
        fastapi_app_module,
        "get_customer_objects",
        lambda partner_id: [{"id": 1, "name": "NAZARBEK"}, {"id": 2, "name": "ISTIROXAT"}],
    )

    resp = await client.post("/api/v1/bot/info", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    message = resp.json()["message"]
    assert "Nodir test uchun" in message
    assert "+998901234567" in message
    assert "NAZARBEK" in message
    assert "ISTIROXAT" in message


async def test_info_includes_general_balance(client, monkeypatch):
    """2026-07-30 -- alohida "Balance" bo'limi olib tashlangach, Malumotlarim (Info)
    javobi endi mijozning umumiy (joriy) UZS/USD balansini ham o'z ichiga oladi."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {
            "id": 42,
            "name": "Test Partner",
            "phone": "+998901234567",
            "mobile": False,
            "x_uzs_balance": 160500.0,
            "x_usd_balance": 0.0,
        },
    )
    monkeypatch.setattr(fastapi_app_module, "get_customer_objects", lambda partner_id: [])

    resp = await client.post("/api/v1/bot/info", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    message = resp.json()["message"]
    assert "Balans" in message
    assert "Umumiy" not in message
    assert "Sizning ma'lumotlaringiz" not in message
    assert "160,500" in message


async def test_info_falls_back_to_mobile_when_no_phone(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {"id": 42, "name": "Test", "phone": False, "mobile": "+998907654321"},
    )
    monkeypatch.setattr(fastapi_app_module, "get_customer_objects", lambda partner_id: [])

    resp = await client.post("/api/v1/bot/info", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    assert "+998907654321" in resp.json()["message"]


async def test_info_missing_fields_all_show_none_value(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {"id": 42, "name": False, "phone": False, "mobile": False},
    )
    monkeypatch.setattr(fastapi_app_module, "get_customer_objects", lambda partner_id: [])

    resp = await client.post("/api/v1/bot/info", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    message = resp.json()["message"]
    assert "-" not in message
    # name, phone, obyektlar -- "yo'q". Balans (UZS/USD) 2026-08-03'dan buyon nolmas
    # bo'lmasa umuman ko'rsatilmaydi (balance_none_value/"yo'q" bilan emas) -- shu sabab
    # noma'lum (None) balans ham umuman chiqmaydi, faqat 3 ta "yo'q" qoladi.
    assert message.count("yo'q") == 3
    assert "Balans" not in message


async def test_info_shows_object_own_phone_and_hides_zero_balance(client, monkeypatch):
    """2026-08-03: har bir obyekt endi o'zining (asosiy mijoz raqamidan farqli
    bo'lishi mumkin bo'lgan) telefon raqami bilan ko'rsatiladi, va balansi 0 bo'lgan
    obyektda balans qatori umuman chiqmaydi -- to'liq FastAPI endpoint orqali."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {"id": 42, "name": "Nodir test uchun", "phone": "+998901234567", "mobile": False},
    )
    monkeypatch.setattr(
        fastapi_app_module,
        "get_customer_objects",
        lambda partner_id: [
            {
                "id": 1, "name": "Olmazor", "phone": "+998991112233", "mobile": False,
                "x_uzs_balance": -50000.0, "x_usd_balance": 0.0,
            },
            {
                "id": 2, "name": "Qo'yliq", "phone": False, "mobile": False,
                "x_uzs_balance": 0.0, "x_usd_balance": 0.0,
            },
        ],
    )

    resp = await client.post("/api/v1/bot/info", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    lines = resp.json()["message"].splitlines()

    olmazor_index = lines.index("🏢 Olmazor")
    assert lines[olmazor_index + 1] == "  📞 +998991112233"
    assert "50,000" in lines[olmazor_index + 3]

    qoyliq_index = lines.index("🏢 Qo'yliq")
    # na telefon, na balans (0 bo'lgani uchun), lekin lokatsiya qatori HAR DOIM
    # ko'rsatiladi (2026-08-04, mijoz aniq so'ragan: bo'lmasa "Lokatsiya: yo'q")
    assert lines[qoyliq_index + 1] == "  📍 Lokatsiya: yo'q"
    assert qoyliq_index + 1 == len(lines) - 1


async def test_object_update_writes_phone_via_odoo_client(client, monkeypatch):
    """2026-08-05: "Ma'lumotlarni tahrirlash" oqimidan kelgan so'rov odoo_client.
    update_object_contact'ga to'g'ri parametrlar bilan uzatilishini tekshiradi."""
    import app.fastapi_app as fastapi_app_module

    captured = {}

    def fake_update(object_id, phone=None, latitude=None, longitude=None):
        captured["object_id"] = object_id
        captured["phone"] = phone
        captured["latitude"] = latitude
        captured["longitude"] = longitude
        return True

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", fake_update)
    monkeypatch.setattr(fastapi_app_module, "post_object_edit_chatter_message", lambda *a, **kw: True)

    resp = await client.post(
        "/api/v1/bot/object-update",
        json={
            "object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874,
            "username": "testuser", "partner_id": 42, "phone": "+998900000111",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    assert captured == {"object_id": 80505, "phone": "+998900000111", "latitude": None, "longitude": None}


async def test_object_update_writes_location_via_odoo_client(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", lambda *a, **kw: True)
    monkeypatch.setattr(fastapi_app_module, "post_object_edit_chatter_message", lambda *a, **kw: True)

    resp = await client.post(
        "/api/v1/bot/object-update",
        json={
            "object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874,
            "latitude": 41.3, "longitude": 69.2,
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": True}


async def test_object_update_returns_false_on_odoo_failure(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", lambda *a, **kw: False)

    resp = await client.post(
        "/api/v1/bot/object-update",
        json={"object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874, "phone": "+998900000111"},
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": False}


async def test_object_update_writes_object_edit_log_on_success(client, monkeypatch):
    """2026-08-05: muvaffaqiyatli yozuvdan keyin ObjectEditLog'ga yozilishi va
    GET /api/v1/object-edits orqali qaytarilishi kerak (Odoo xodimlari "qaysi
    obyekt o'zgardi" so'rasa javob topishi uchun)."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", lambda *a, **kw: True)
    monkeypatch.setattr(fastapi_app_module, "post_object_edit_chatter_message", lambda *a, **kw: True)

    resp = await client.post(
        "/api/v1/bot/object-update",
        json={
            "object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874,
            "username": "testuser", "partner_id": 42, "phone": "+998900000111",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200

    log_resp = await client.get("/api/v1/object-edits", headers=API_HEADERS)
    assert log_resp.status_code == 200
    rows = log_resp.json()
    assert len(rows) == 1
    assert rows[0]["object_id"] == 80505
    assert rows[0]["object_name"] == "ansdbkjasnd"
    assert rows[0]["field"] == "phone"
    assert rows[0]["new_phone"] == "+998900000111"
    assert rows[0]["chat_id"] == 1532815874
    assert rows[0]["synced"] is False


async def test_object_update_posts_chatter_message_on_success(client, monkeypatch):
    """2026-08-06, mijoz aniq so'ragan: muvaffaqiyatli yozuvdan keyin obyektning
    o'z Odoo kartochkasiga ("chatter") ham xabar yozilishi kerak -- Odoo xodimi
    o'sha obyektni ochganda o'zgarishni DARHOL ko'rishi uchun."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", lambda *a, **kw: True)
    captured = {}

    def fake_chatter(object_id, message_body):
        captured["object_id"] = object_id
        captured["message_body"] = message_body
        return True

    monkeypatch.setattr(fastapi_app_module, "post_object_edit_chatter_message", fake_chatter)

    resp = await client.post(
        "/api/v1/bot/object-update",
        json={
            "object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874,
            "username": "testuser", "phone": "+998900000111",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    assert captured["object_id"] == 80505
    assert "+998900000111" in captured["message_body"]
    assert "@testuser" in captured["message_body"]


async def test_object_update_does_not_post_chatter_on_odoo_failure(client, monkeypatch):
    """update_object_contact muvaffaqiyatsiz bo'lsa, chatter xabari HAM yozilmasligi
    kerak (faqat haqiqiy o'zgarish bo'lgandagina eslatma yozish mantiqiy)."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", lambda *a, **kw: False)
    chatter_called = {"value": False}

    def fake_chatter(*a, **kw):
        chatter_called["value"] = True
        return True

    monkeypatch.setattr(fastapi_app_module, "post_object_edit_chatter_message", fake_chatter)

    await client.post(
        "/api/v1/bot/object-update",
        json={"object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874, "phone": "+998900000111"},
        headers=API_HEADERS,
    )
    assert chatter_called["value"] is False


async def test_object_update_does_not_log_on_odoo_failure(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", lambda *a, **kw: False)

    await client.post(
        "/api/v1/bot/object-update",
        json={"object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874, "phone": "+998900000111"},
        headers=API_HEADERS,
    )

    log_resp = await client.get("/api/v1/object-edits", headers=API_HEADERS)
    assert log_resp.json() == []


async def test_object_edits_mark_synced(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "update_object_contact", lambda *a, **kw: True)
    monkeypatch.setattr(fastapi_app_module, "post_object_edit_chatter_message", lambda *a, **kw: True)

    await client.post(
        "/api/v1/bot/object-update",
        json={"object_id": 80505, "object_name": "ansdbkjasnd", "chat_id": 1532815874, "phone": "+998900000111"},
        headers=API_HEADERS,
    )
    rows = (await client.get("/api/v1/object-edits", headers=API_HEADERS)).json()
    log_id = rows[0]["id"]

    mark_resp = await client.post(
        "/api/v1/object-edits/mark-synced", json={"ids": [log_id]}, headers=API_HEADERS
    )
    assert mark_resp.status_code == 200
    assert mark_resp.json()["success"] is True

    unsynced = (await client.get("/api/v1/object-edits?synced=false", headers=API_HEADERS)).json()
    assert unsynced == []


# --- Statistika: oxirgi 12 oylik xarajat/bekor qilingan (Odoo'ga real vaqtli so'rov, 2026-08-04) ---


async def test_statistics_no_partner_found(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)

    resp = await client.post("/api/v1/bot/statistics", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["months"] is None


async def test_statistics_returns_months_and_partner_name(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module,
        "find_partner_by_chat_id",
        lambda chat_id: {"id": 42, "name": "Test Partner", "x_uzs_balance": 20_000_000.0},
    )
    fake_months = [{"month": "2026-08", "spent": 1500.0, "returned": -200.0, "paid": 1000.0}]
    fake_data = {
        "months": fake_months,
        "by_object": [
            # object_id == partner_id (42) -- "Umumiy" (aniq obyektga bog'lanmagan)
            {"object_id": 42, "object_name": None, "spent": 1500.0, "returned": -200.0, "paid": 1000.0},
            # Haqiqiy nomlangan obyekt
            {"object_id": 5, "object_name": "Olmazor", "spent": 300.0, "returned": 0.0, "paid": 300.0},
        ],
    }
    monkeypatch.setattr(fastapi_app_module, "get_monthly_statistics", lambda partner_id: fake_data)

    resp = await client.post("/api/v1/bot/statistics", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["partner_name"] == "Test Partner"
    assert body["months"] == fake_months
    # 2026-08-04, mijoz aniq so'ragan: "Umumiy" (object_id == partner_id) qatori
    # BUTUNLAY olib tashlanadi -- faqat haqiqiy nomlangan obyektlar qoladi.
    assert len(body["by_object"]) == 1
    assert body["by_object"][0]["object_name"] == "Olmazor"
    assert body["by_object"][0]["paid"] == 300.0
    # 2026-08-04, mijoz aniq so'ragan: PDF'dagi balans Malumotlarim bilan bir xil bo'lishi
    # uchun current_balance ham qaytariladi -- Odoo'dagi belgi TESKARI (invert_balance_sign)
    assert body["current_balance"] == -20_000_000.0


async def test_statistics_returns_none_months_on_odoo_failure(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test Partner"}
    )
    monkeypatch.setattr(fastapi_app_module, "get_monthly_statistics", lambda partner_id: None)

    resp = await client.post("/api/v1/bot/statistics", json={"chat_id": 1, "lang": "uz"}, headers=API_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["months"] is None
    assert body["by_object"] is None
