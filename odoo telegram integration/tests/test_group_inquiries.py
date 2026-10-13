"""
/api/v1/bot/group-inquiry va /api/v1/group-inquiries endpointlarini sinaydi (2026-08-03):
guruhda botni mention qilgan foydalanuvchi Odoo'da topilganda/topilmaganda HAQIQIY crm.lead
yozilishi (ikkinchi pivot, shu kunning o'zida -- avval faqat topilgan holatda passiv
GroupInquiry saqlanardi), topilgan holatda GroupInquiry ham qo'shimcha saqlanishi, va Odoo
uchun GET/mark-synced poll naqshi. `find_partner_by_chat_id`/`create_telegram_order_lead`
monkeypatch orqali soxtalashtiriladi (haqiqiy Odoo XML-RPC'ga bog'lanmaydi)."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings

API_HEADERS = {"X-API-Key": settings.API_KEY}


@pytest_asyncio.fixture
async def client():
    from app.fastapi_app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _payload(**overrides):
    payload = {
        "chat_id": 555111222,
        "username": "testuser",
        "full_name": "Test User",
        "group_id": -1009999999999,
        "group_title": "Test Guruh",
        "message_text": "@test_bot narxlar qanday?",
    }
    payload.update(overrides)
    return payload


async def test_group_inquiry_rejects_missing_api_key(client):
    resp = await client.post("/api/v1/bot/group-inquiry", json=_payload())
    assert resp.status_code == 422


async def test_group_inquiry_not_found_creates_lead_as_new_customer(client, monkeypatch):
    """2026-08-03: mijoz Odoo'da topilmasa ham, endi "yangi mijoz" sifatida (partner_id
    yo'q, contact_name=full_name/username) crm.lead yaratiladi -- GroupInquiry jadvaliga
    hech narsa saqlanmaydi (partner_id majburiy ustun, topilmagan mijoz uchun yo'q), faqat
    lead o'zi Odoo'da qoladi."""
    import app.fastapi_app as fastapi_app_module

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return 321

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)
    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", fake_create)

    resp = await client.post("/api/v1/bot/group-inquiry", json=_payload(), headers=API_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"found": False, "partner_id": None, "lead_created": True}

    assert captured["partner_id"] is None
    assert captured["partner_name"] == "Test User"
    assert captured["chat_id"] == 555111222
    assert captured["order_details"] == "[Guruhdan: Test Guruh] @test_bot narxlar qanday?"

    list_resp = await client.get("/api/v1/group-inquiries", headers=API_HEADERS)
    assert list_resp.json() == []


async def test_group_inquiry_not_found_falls_back_to_username_without_full_name(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return 322

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)
    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", fake_create)

    resp = await client.post(
        "/api/v1/bot/group-inquiry", json=_payload(full_name=None), headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert captured["partner_name"] == "testuser"


async def test_group_inquiry_not_found_lead_creation_fails_returns_lead_created_false(client, monkeypatch):
    """Odoo yozuvi muvaffaqiyatsiz bo'lsa (masalan Odoo o'chgan/xato), bot buni bilib
    mos ravishda umumiy xatolik xabarini ko'rsatishi uchun lead_created=False qaytishi
    kerak."""
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: None)
    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: None)

    resp = await client.post("/api/v1/bot/group-inquiry", json=_payload(), headers=API_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"found": False, "partner_id": None, "lead_created": False}


async def test_group_inquiry_found_creates_lead_and_saves_group_inquiry(client, monkeypatch):
    """2026-08-03: mijoz Odoo'da topilsa, lead partner_id bilan yoziladi VA (avvalgidek)
    GroupInquiry jadvaliga ham audit nusxa sifatida saqlanadi."""
    import app.fastapi_app as fastapi_app_module

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return 323

    monkeypatch.setattr(
        fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Jonibek Yorqulov"}
    )
    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", fake_create)

    resp = await client.post("/api/v1/bot/group-inquiry", json=_payload(), headers=API_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"found": True, "partner_id": 42, "lead_created": True}

    assert captured["partner_id"] == 42
    assert captured["partner_name"] == "Jonibek Yorqulov"

    list_resp = await client.get("/api/v1/group-inquiries", headers=API_HEADERS)
    rows = list_resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["chat_id"] == 555111222
    assert row["partner_id"] == 42
    assert row["partner_name"] == "Jonibek Yorqulov"
    assert row["message_text"] == "@test_bot narxlar qanday?"
    assert row["synced"] is False


async def test_group_inquiries_synced_filter_and_mark_synced(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    monkeypatch.setattr(
        fastapi_app_module, "find_partner_by_chat_id", lambda chat_id: {"id": 42, "name": "Test Partner"}
    )
    monkeypatch.setattr(fastapi_app_module, "create_telegram_order_lead", lambda **k: 324)
    resp = await client.post("/api/v1/bot/group-inquiry", json=_payload(), headers=API_HEADERS)
    inquiry_id = (await client.get("/api/v1/group-inquiries", headers=API_HEADERS)).json()[0]["id"]

    unsynced = await client.get("/api/v1/group-inquiries", params={"synced": False}, headers=API_HEADERS)
    assert len(unsynced.json()) == 1

    mark_resp = await client.post(
        "/api/v1/group-inquiries/mark-synced", json={"ids": [inquiry_id]}, headers=API_HEADERS
    )
    assert mark_resp.status_code == 200
    assert mark_resp.json()["updated_count"] == 1

    unsynced_after = await client.get("/api/v1/group-inquiries", params={"synced": False}, headers=API_HEADERS)
    assert unsynced_after.json() == []

    synced_after = await client.get("/api/v1/group-inquiries", params={"synced": True}, headers=API_HEADERS)
    assert len(synced_after.json()) == 1


async def test_mark_group_inquiries_synced_requires_ids(client):
    resp = await client.post("/api/v1/group-inquiries/mark-synced", json={"ids": []}, headers=API_HEADERS)
    assert resp.status_code == 400
