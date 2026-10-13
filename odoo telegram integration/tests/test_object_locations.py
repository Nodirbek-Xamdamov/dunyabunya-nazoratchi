"""
/api/v1/bot/object-location-submit va /api/v1/object-locations endpointlarini sinaydi
(2026-08-02): "Buyurtma berish" oqimida bot mijoz obyekti (mavjud yoki yangi yaratilgan)
uchun GPS lokatsiyasini yozadi, Odoo esa GET/mark-synced orqali o'zi so'rab oladi --
xuddi chat-ids/delivery-confirmations bilan bir xil poll+mark-synced naqsh.
"""

from unittest.mock import MagicMock

import pytest
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


async def test_object_location_submit_rejects_missing_api_key(client):
    resp = await client.post(
        "/api/v1/bot/object-location-submit",
        json={"partner_id": 42, "object_name": "NAZARBEK", "chat_id": 1, "latitude": 41.3, "longitude": 69.2},
    )
    assert resp.status_code == 422


@pytest.fixture
def mock_set_object_location(monkeypatch):
    """set_object_location -- haqiqiy Odoo XML-RPC so'rovi ketmasligi uchun soxtalashtiriladi."""
    import app.fastapi_app as fastapi_app_module

    mock = MagicMock(return_value=True)
    monkeypatch.setattr(fastapi_app_module, "set_object_location", mock)
    return mock


async def test_object_location_submit_saves_existing_object_and_writes_to_odoo(client, mock_set_object_location):
    """2026-08-02: mavjud obyekt (object_id berilgan) uchun Odoo'ning o'z
    partner_latitude/partner_longitude maydonlariga to'g'ridan-to'g'ri yoziladi
    (set_object_location). Yozish MUVAFFAQIYATLI bo'lgani uchun bu obyekt endi Odoo'da
    lokatsiyaga ega -- shu sabab GET /api/v1/object-locations orqali ENDI KO'RSATILMAYDI
    (qayta ko'rsatish keraksiz, Odoo'da allaqachon bor)."""
    resp = await client.post(
        "/api/v1/bot/object-location-submit",
        json={
            "partner_id": 42,
            "object_id": 7,
            "object_name": "NAZARBEK",
            "is_new_object": False,
            "chat_id": 111,
            "latitude": 41.311,
            "longitude": 69.279,
            "username": "testuser",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["odoo_synced"] is True
    mock_set_object_location.assert_called_once_with(7, 41.311, 69.279)

    rows = (await client.get("/api/v1/object-locations", headers=API_HEADERS)).json()
    assert rows == []


async def test_object_location_submit_reports_odoo_write_failure_but_still_saves_locally(
    client, mock_set_object_location
):
    """Agar Odoo'ga to'g'ridan-to'g'ri yozish muvaffaqiyatsiz bo'lsa, bu obyektning
    lokatsiyasi Odoo'da HALI YO'Q -- shu sabab ma'lumot yo'qolmasligi uchun GET orqali
    baribir ko'rsatilishi kerak (zaxira sifatida, Odoo tarafi qo'lda tekshirishi uchun)."""
    mock_set_object_location.return_value = False

    resp = await client.post(
        "/api/v1/bot/object-location-submit",
        json={
            "partner_id": 42,
            "object_id": 7,
            "object_name": "NAZARBEK",
            "chat_id": 111,
            "latitude": 41.311,
            "longitude": 69.279,
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["odoo_synced"] is False

    rows = (await client.get("/api/v1/object-locations", headers=API_HEADERS)).json()
    assert len(rows) == 1
    assert rows[0]["object_id"] == 7


async def test_object_location_submit_saves_new_object_without_object_id(client, mock_set_object_location):
    """object_id=None (yangi obyekt) bo'lsa Odoo'ga yozib bo'lmaydi (hali mavjud emas) --
    set_object_location umuman chaqirilmasligi kerak."""
    resp = await client.post(
        "/api/v1/bot/object-location-submit",
        json={
            "partner_id": 42,
            "object_id": None,
            "object_name": "Chilonzor tumani, 5-uy",
            "is_new_object": True,
            "chat_id": 111,
            "latitude": 41.0,
            "longitude": 69.0,
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["odoo_synced"] is False
    mock_set_object_location.assert_not_called()

    rows = (await client.get("/api/v1/object-locations", headers=API_HEADERS)).json()
    assert rows[0]["object_id"] is None
    assert rows[0]["is_new_object"] is True


async def test_object_location_submit_stores_partner_name_and_phone_for_new_object(client, mock_set_object_location):
    """2026-08-02: yangi obyekt (object_id yo'q) Odoo xodimi tomonidan qo'lda
    yaratilishi kerak -- shu safar kim uchun ekanini partner_id'ni qayta qidirmasdan
    ko'rishi uchun partner_name/partner_phone ham saqlanib, GET orqali qaytarilishi kerak."""
    resp = await client.post(
        "/api/v1/bot/object-location-submit",
        json={
            "partner_id": 42,
            "object_id": None,
            "object_name": "Chilonzor tumani, 5-uy",
            "is_new_object": True,
            "chat_id": 111,
            "latitude": 41.0,
            "longitude": 69.0,
            "partner_name": "Test Partner",
            "partner_phone": "+998901234567",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200

    rows = (await client.get("/api/v1/object-locations", headers=API_HEADERS)).json()
    assert rows[0]["partner_name"] == "Test Partner"
    assert rows[0]["partner_phone"] == "+998901234567"


async def test_object_locations_mark_synced_updates_and_filters(client):
    create_resp = await client.post(
        "/api/v1/bot/object-location-submit",
        json={
            "partner_id": 42,
            "object_name": "NAZARBEK",
            "chat_id": 111,
            "latitude": 41.311,
            "longitude": 69.279,
        },
        headers=API_HEADERS,
    )
    row_id = create_resp.json()["id"]

    pending = await client.get("/api/v1/object-locations", params={"synced": "false"}, headers=API_HEADERS)
    assert len(pending.json()) == 1

    sync_resp = await client.post(
        "/api/v1/object-locations/mark-synced", json={"ids": [row_id]}, headers=API_HEADERS
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["updated_count"] == 1

    pending_after = await client.get("/api/v1/object-locations", params={"synced": "false"}, headers=API_HEADERS)
    assert pending_after.json() == []

    synced_after = await client.get("/api/v1/object-locations", params={"synced": "true"}, headers=API_HEADERS)
    assert len(synced_after.json()) == 1


async def test_object_locations_mark_synced_rejects_empty_ids(client):
    resp = await client.post("/api/v1/object-locations/mark-synced", json={"ids": []}, headers=API_HEADERS)
    assert resp.status_code == 400


async def test_object_locations_get_requires_api_key(client):
    resp = await client.get("/api/v1/object-locations")
    assert resp.status_code == 422
