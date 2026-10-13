"""
/api/v1/delivery-confirmations endpointlarini sinaydi (2026-08-02): taklif-bot'dan kelgan
yetkazib berish rasmini qabul qilish, mijozni doc_id bo'yicha topib xabar yuborish, va
Odoo uchun GET/mark-synced poll naqshini tekshiradi.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import async_session_maker
from app.models import Order

API_HEADERS = {"X-API-Key": settings.API_KEY}


@pytest.fixture(autouse=True)
def mock_send_message(monkeypatch):
    import app.fastapi_app as fastapi_app_module

    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(fastapi_app_module.bot, "send_message", mock)
    return mock


@pytest_asyncio.fixture
async def client():
    from app.fastapi_app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_order_with_chat_id(doc_id: str, chat_id: int) -> None:
    async with async_session_maker() as session:
        session.add(
            Order(
                doc_id=doc_id,
                raw_username="testuser",
                normalized_username="testuser",
                order_details="{}",
                chat_id=chat_id,
            )
        )
        await session.commit()


async def test_delivery_confirmation_rejects_missing_api_key(client, sample_pdf_base64):
    resp = await client.post(
        "/api/v1/delivery-confirmations", json={"doc_id": "S56617", "photo_base64": sample_pdf_base64}
    )
    assert resp.status_code == 422


async def test_delivery_confirmation_saves_and_notifies_known_customer(client, sample_pdf_base64, mock_send_message):
    await _seed_order_with_chat_id("S56617", 111222333)

    resp = await client.post(
        "/api/v1/delivery-confirmations",
        json={
            "doc_id": "S56617",
            "manager_name": "Hasanxo'jayev Sardor",
            "photo_base64": sample_pdf_base64,
            "uploaded_by_name": "Haydovchi Aziz",
        },
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["customer_chat_id"] == 111222333
    assert body["customer_notified"] is True

    mock_send_message.assert_called_once()
    _, kwargs = mock_send_message.call_args
    assert kwargs["chat_id"] == 111222333
    assert "S56617" in kwargs["text"]

    list_resp = await client.get("/api/v1/delivery-confirmations", headers=API_HEADERS)
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) == 1
    # 2026-08-02: Odoo'ga faqat kerakli maydonlar ko'rsatiladi -- customer_chat_id/
    # customer_notified/uploaded_by_name/created_at Telegram-ichki, endi javobda yo'q.
    assert rows[0] == {
        "id": rows[0]["id"],
        "doc_id": "S56617",
        "manager_name": "Hasanxo'jayev Sardor",
        "photo_url": rows[0]["photo_url"],
        "synced": False,
    }
    assert rows[0]["photo_url"].startswith("/delivery_photos/")


async def test_delivery_confirmation_unknown_doc_id_saves_without_notifying(client, sample_pdf_base64, mock_send_message):
    resp = await client.post(
        "/api/v1/delivery-confirmations",
        json={"doc_id": "S99999", "photo_base64": sample_pdf_base64},
        headers=API_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["customer_chat_id"] is None
    assert body["customer_notified"] is False
    mock_send_message.assert_not_called()


async def test_delivery_confirmation_rejects_missing_photo(client):
    resp = await client.post(
        "/api/v1/delivery-confirmations", json={"doc_id": "S56617"}, headers=API_HEADERS
    )
    assert resp.status_code == 422


async def test_delivery_confirmations_mark_synced_updates_and_filters(client, sample_pdf_base64):
    create_resp = await client.post(
        "/api/v1/delivery-confirmations",
        json={"doc_id": "S56617", "photo_base64": sample_pdf_base64},
        headers=API_HEADERS,
    )
    row_id = create_resp.json()["id"]

    pending = await client.get("/api/v1/delivery-confirmations", params={"synced": "false"}, headers=API_HEADERS)
    assert len(pending.json()) == 1

    sync_resp = await client.post(
        "/api/v1/delivery-confirmations/mark-synced", json={"ids": [row_id]}, headers=API_HEADERS
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["updated_count"] == 1

    pending_after = await client.get(
        "/api/v1/delivery-confirmations", params={"synced": "false"}, headers=API_HEADERS
    )
    assert pending_after.json() == []

    synced_after = await client.get(
        "/api/v1/delivery-confirmations", params={"synced": "true"}, headers=API_HEADERS
    )
    assert len(synced_after.json()) == 1


async def test_delivery_confirmations_mark_synced_rejects_empty_ids(client):
    resp = await client.post("/api/v1/delivery-confirmations/mark-synced", json={"ids": []}, headers=API_HEADERS)
    assert resp.status_code == 400
