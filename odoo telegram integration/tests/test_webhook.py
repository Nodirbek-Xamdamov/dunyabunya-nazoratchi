"""
/api/v1/webhook/odoo va bog'liq endpointlarni ASGI orqali (haqiqiy tarmoq so'rovisiz)
sinovdan o'tkazadi. Telegram Bot API chaqiruvlari (send_document, send_message,
get_chat_member) mock qilinadi, baza vaqtinchalik SQLite fayl bo'lib, har bir test
uchun conftest.py orqali tozalanadi.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings

API_HEADERS = {"X-API-Key": settings.API_KEY}


def _make_chat_member_mock(status: str = "member", first_name: str = "Test", username: str | None = "testuser"):
    """Bot API'ning ChatMember javobini taqlid qiluvchi minimal mock."""
    member = MagicMock()
    member.status = status
    member.user.first_name = first_name
    member.user.username = username
    return member


@pytest.fixture(autouse=True)
def mock_telegram_calls(monkeypatch):
    """
    Bot API'ga haqiqiy so'rov ketmasligi uchun send_document/send_message/
    get_chat_member'ni mock qiladi. get_chat_member standart holatda "member" (guruhda
    hali bor, akkaunt faol) qaytaradi -- guruhga PDF+tugmalar yuborilishi kerak bo'lgan
    testlarda shu standart qiymat ishlatiladi, aks holatlarni tekshiruvchi testlar
    return_value'ni o'zgartiradi.
    """
    import app.fastapi_app as fastapi_app_module

    mock_send_document = AsyncMock(return_value=None)
    mock_send_message = AsyncMock(return_value=None)
    mock_get_chat_member = AsyncMock(return_value=_make_chat_member_mock())

    monkeypatch.setattr(fastapi_app_module.bot, "send_document", mock_send_document)
    monkeypatch.setattr(fastapi_app_module.bot, "send_message", mock_send_message)
    monkeypatch.setattr(fastapi_app_module.bot, "get_chat_member", mock_get_chat_member)

    return {
        "send_document": mock_send_document,
        "send_message": mock_send_message,
        "get_chat_member": mock_get_chat_member,
    }


@pytest_asyncio.fixture
async def client():
    from app.fastapi_app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health_check(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_webhook_rejects_missing_api_key_header(client, sample_pdf_base64):
    # X-API-Key header'ning o'zi berilmasa, FastAPI so'rovni bizning kodimizga
    # yetkazmasdan turib 422 (majburiy header yo'q) qaytaradi.
    resp = await client.post("/api/v1/webhook/odoo", json={"pdf_base64": sample_pdf_base64})
    assert resp.status_code == 422


async def test_webhook_rejects_wrong_api_key(client, sample_pdf_base64):
    resp = await client.post(
        "/api/v1/webhook/odoo",
        json={"pdf_base64": sample_pdf_base64},
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


async def test_webhook_rejects_missing_pdf(client):
    resp = await client.post(
        "/api/v1/webhook/odoo", json={"user": {"username": "x"}}, headers=API_HEADERS
    )
    assert resp.status_code == 422


async def test_webhook_individual_order_without_chat_id_does_not_send_yet(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "doc_id": "SO-TEST-001",
        "pdf_base64": sample_pdf_base64,
        "message": "Sizning buyurtmangiz tayyor.",
        "user": {"username": "TestUser1"},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    body = resp.json()
    assert body["success"] is True
    assert body["group"] is None
    assert body["individual"]["created_orders_count"] == 1
    order = body["individual"]["orders"][0]
    assert order["normalized_username"] == "testuser1"
    assert order["chat_id"] is None
    assert order["notified"] is False
    mock_telegram_calls["send_document"].assert_not_called()


async def test_webhook_individual_order_sends_immediately_when_chat_id_known(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "pdf_base64": sample_pdf_base64,
        "message": "Xabar matni",
        "user": {"username": "knownuser", "chat_id": 555111222},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    order = resp.json()["individual"]["orders"][0]
    assert order["chat_id"] == 555111222
    assert order["notified"] is True
    mock_telegram_calls["send_document"].assert_called_once()

    _, kwargs = mock_telegram_calls["send_document"].call_args
    assert kwargs["chat_id"] == 555111222


async def test_webhook_multiple_usernames_creates_separate_orders(client, sample_pdf_base64):
    payload = {
        "pdf_base64": sample_pdf_base64,
        "user": {"username": "user_a, user_b"},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    body = resp.json()

    assert body["individual"]["created_orders_count"] == 2
    usernames = {o["normalized_username"] for o in body["individual"]["orders"]}
    assert usernames == {"user_a", "user_b"}


async def test_webhook_group_order_without_target_user_sends_fallback_message(client, sample_pdf_base64, mock_telegram_calls):
    """group_id berilgan, lekin mas'ul shaxs (user) ko'rsatilmagan -- guruhga PDF/tugma
    o'rniga qo'llab-quvvatlashga murojaat matni yuborilishi kerak."""
    payload = {
        "pdf_base64": sample_pdf_base64,
        "message": "Guruh uchun xabar",
        "group_id": -1001234567890,
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    body = resp.json()
    assert body["individual"] is None
    assert body["group"]["group_id"] == -1001234567890
    assert body["group"]["chat_id"] is None
    mock_telegram_calls["send_document"].assert_not_called()
    mock_telegram_calls["send_message"].assert_called_once()

    _, kwargs = mock_telegram_calls["send_message"].call_args
    assert kwargs["chat_id"] == -1001234567890
    assert "mas'ul shaxs topilmadi" in kwargs["text"]


# --- Odoo group_id bilan yuborsa-yu, mas'ul shaxsni (user) ko'rsatmasa -- o'z bazamizdan
# (group_memberships) shu guruhda ma'lum a'zoni tanlash (2026-08-06, mijoz aniq so'ragan:
# "Odoo'dan username qo'shib jo'natilmaydi, o'zimizni bazamizdan qaysi usernamega
# tasdiqlash/rad etish/o'zgartrishni bering") -------------------------------------------


async def test_webhook_group_order_without_user_resolves_from_group_memberships(
    client, sample_pdf_base64, mock_telegram_calls
):
    from app.database import async_session_maker
    from app.models import GroupMembership, TelegramUser

    group_id = -1005551111
    async with async_session_maker() as session:
        session.add(TelegramUser(chat_id=333444555, username="knownmember", normalized_username="knownmember"))
        session.add(GroupMembership(chat_id=333444555, group_id=group_id, group_title="Real group"))
        await session.commit()

    payload = {"pdf_base64": sample_pdf_base64, "group_id": group_id}
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    body = resp.json()
    assert body["group"]["chat_id"] == 333444555
    assert body["group"]["notified"] is True

    group_calls = [
        call for call in mock_telegram_calls["send_document"].call_args_list
        if call.kwargs["chat_id"] == group_id
    ]
    assert len(group_calls) == 1
    assert group_calls[0].kwargs["reply_markup"] is not None
    mock_telegram_calls["send_message"].assert_not_called()


async def test_webhook_group_order_without_user_skips_unreachable_member(
    client, sample_pdf_base64, mock_telegram_calls
):
    """Bazada ma'lum bo'lgan a'zo endi guruhda emas (chiqib ketgan) bo'lsa -- shu odam
    o'tkazib yuborilib, hali ham faol bo'lgan KEYINGI ma'lum a'zo tanlanadi."""
    from datetime import datetime, timedelta, timezone

    from app.database import async_session_maker
    from app.models import GroupMembership, TelegramUser

    group_id = -1005551112

    async def fake_get_chat_member(chat_id, user_id):
        if user_id == 111000111:
            return _make_chat_member_mock(status="left")
        return _make_chat_member_mock(status="member")

    mock_telegram_calls["get_chat_member"].side_effect = fake_get_chat_member

    # discovered_at ANIQ (server default'ga tayanmasdan) belgilanadi -- SQLite'ning
    # CURRENT_TIMESTAMP'i sekund aniqligida bo'lib, ikkala yozuv ham bir sekund ichida
    # qo'shilsa tartib noaniq (flaky) bo'lib qolar edi.
    base = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        session.add(TelegramUser(chat_id=111000111, username="left_member", normalized_username="left_member"))
        session.add(TelegramUser(chat_id=222000222, username="active_member", normalized_username="active_member"))
        # discovered_at DESC bo'yicha tartiblanadi -- left_member KEYINROQ aniqlangan
        # (eng "ustuvor") deb belgilanadi, lekin u endi faol emas, shu sabab undan oldingi
        # active_member tanlanishi kerak.
        session.add(GroupMembership(chat_id=222000222, group_id=group_id, group_title="G", discovered_at=base))
        session.add(
            GroupMembership(
                chat_id=111000111, group_id=group_id, group_title="G", discovered_at=base + timedelta(seconds=5)
            )
        )
        await session.commit()

    payload = {"pdf_base64": sample_pdf_base64, "group_id": group_id}
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    body = resp.json()
    assert body["group"]["chat_id"] == 222000222


async def test_webhook_group_order_explicit_user_takes_priority_over_group_memberships(
    client, sample_pdf_base64, mock_telegram_calls
):
    """Odoo aniq user ko'rsatgan bo'lsa -- group_memberships fallback ISHLAMAYDI,
    Odoo aytgan shaxs ustuvor (o'zgarmagan eski xatti-harakat)."""
    from app.database import async_session_maker
    from app.models import GroupMembership, TelegramUser

    group_id = -1005551113
    async with async_session_maker() as session:
        session.add(TelegramUser(chat_id=999888777, username="otherknown", normalized_username="otherknown"))
        session.add(GroupMembership(chat_id=999888777, group_id=group_id, group_title="G"))
        await session.commit()

    payload = {
        "pdf_base64": sample_pdf_base64,
        "group_id": group_id,
        "user": {"username": "explicituser", "chat_id": 123123123},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    body = resp.json()
    assert body["group"]["chat_id"] == 123123123


async def test_webhook_group_order_with_reachable_target_sends_pdf_and_buttons(client, sample_pdf_base64, mock_telegram_calls):
    """group_id + user (chat_id ma'lum, guruhda hali a'zo) -- PDF + tasdiqlash tugmalari
    guruhga yuboriladi (poll emas). `user` berilgani individual DM oqimini ham mustaqil
    ishga tushiradi -- shu sabab send_document ikki marta chaqiriladi (biri guruhga,
    biri bevosita DM'ga); bu yerda faqat GURUH tomonini tekshiramiz."""
    payload = {
        "pdf_base64": sample_pdf_base64,
        "group_id": -1001234567891,
        "user": {"username": "groupuser", "chat_id": 444555666},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    body = resp.json()
    assert body["group"]["chat_id"] == 444555666
    assert body["group"]["notified"] is True
    mock_telegram_calls["send_message"].assert_not_called()

    group_calls = [
        call for call in mock_telegram_calls["send_document"].call_args_list
        if call.kwargs["chat_id"] == -1001234567891
    ]
    assert len(group_calls) == 1
    assert group_calls[0].kwargs["reply_markup"] is not None


async def test_webhook_group_order_target_left_group_sends_fallback(client, sample_pdf_base64, mock_telegram_calls):
    """Mas'ul shaxs guruhdan chiqib ketgan bo'lsa -- guruhga fallback matn yuboriladi
    (individual DM oqimi mustaqil ishlashda davom etadi, guruh a'zoligiga bog'liq emas)."""
    mock_telegram_calls["get_chat_member"].return_value = _make_chat_member_mock(status="left")

    payload = {
        "pdf_base64": sample_pdf_base64,
        "group_id": -1001234567892,
        "user": {"username": "leftuser", "chat_id": 111222333},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    group_calls = [
        call for call in mock_telegram_calls["send_document"].call_args_list
        if call.kwargs["chat_id"] == -1001234567892
    ]
    assert group_calls == []
    mock_telegram_calls["send_message"].assert_called_once()
    _, kwargs = mock_telegram_calls["send_message"].call_args
    assert kwargs["chat_id"] == -1001234567892


async def test_webhook_group_order_deleted_account_sends_fallback(client, sample_pdf_base64, mock_telegram_calls):
    """Mas'ul shaxsning akkaunti o'chirilgan bo'lsa -- guruhga fallback matn yuboriladi."""
    mock_telegram_calls["get_chat_member"].return_value = _make_chat_member_mock(
        status="member", first_name="Deleted Account", username=None
    )

    payload = {
        "pdf_base64": sample_pdf_base64,
        "group_id": -1001234567893,
        "user": {"username": "deleteduser", "chat_id": 222333444},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    group_calls = [
        call for call in mock_telegram_calls["send_document"].call_args_list
        if call.kwargs["chat_id"] == -1001234567893
    ]
    assert group_calls == []
    mock_telegram_calls["send_message"].assert_called_once()


async def test_webhook_group_and_individual_run_independently_together(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "pdf_base64": sample_pdf_base64,
        "group_id": -100999,
        "user": {"username": "bothflow", "chat_id": 888999000},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    body = resp.json()

    assert body["group"] is not None
    assert body["individual"] is not None
    assert body["individual"]["created_orders_count"] == 1
    # Ikkalasi ham alohida Order yozuvi -- biri guruhga (tugmalar bilan), biri bevosita DM'ga
    assert mock_telegram_calls["send_document"].call_count == 2
    mock_telegram_calls["send_message"].assert_not_called()


async def test_chat_ids_and_mark_synced_flow(client, sample_pdf_base64):
    payload = {
        "pdf_base64": sample_pdf_base64,
        "user": {"username": "syncuser", "chat_id": 777888999},
    }
    create_resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    order_id = create_resp.json()["individual"]["orders"][0]["order_id"]

    list_resp = await client.get("/api/v1/chat-ids", headers=API_HEADERS)
    assert list_resp.status_code == 200
    chat_ids = [o["chat_id"] for o in list_resp.json()]
    assert 777888999 in chat_ids

    sync_resp = await client.post(
        "/api/v1/chat-ids/mark-synced", json={"ids": [order_id]}, headers=API_HEADERS
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["updated_count"] == 1

    synced_list = await client.get(
        "/api/v1/chat-ids", params={"synced": "true"}, headers=API_HEADERS
    )
    assert any(o["chat_id"] == 777888999 for o in synced_list.json())


async def test_group_orders_endpoint_reflects_confirmation_status(client, sample_pdf_base64, mock_telegram_calls):
    payload = {"pdf_base64": sample_pdf_base64, "group_id": -100555}
    await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)

    resp = await client.get("/api/v1/group-orders", headers=API_HEADERS)
    assert resp.status_code == 200

    data = resp.json()
    assert len(data) == 1
    assert data[0]["group_id"] == -100555
    assert data[0]["confirmation_status"] == "pending"


async def test_group_memberships_endpoint_returns_empty_list_by_default(client):
    resp = await client.get("/api/v1/group-memberships", headers=API_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


# --- "Holat yangilanishi" push (2026-08-02): Odoo tarafida menejer tomonidan tasdiqlangan/
# bekor qilingan buyurtma haqida oddiy matnli xabar -- PDF/tugmalarsiz -------------------


async def test_webhook_status_update_sends_plain_message_no_pdf(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "doc_id": "S56629",
        "pdf_base64": sample_pdf_base64,
        "subject": "Holat yangilanishi",
        "message": "Buyurtmangiz Hasanxo'jayev Sardor tomonidan tasdiqlandi.",
        "user": {"username": None, "chat_id": 1532815874},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    body = resp.json()
    assert body["success"] is True
    assert "group" not in body
    assert "individual" not in body
    assert body["status_update"]["targets"] == [
        {"username": "no_username", "chat_id": 1532815874, "sent": True}
    ]

    mock_telegram_calls["send_document"].assert_not_called()
    mock_telegram_calls["send_message"].assert_called_once()
    _, kwargs = mock_telegram_calls["send_message"].call_args
    assert kwargs["chat_id"] == 1532815874
    assert kwargs["text"] == "Buyurtmangiz Hasanxo'jayev Sardor tomonidan tasdiqlandi."


async def test_webhook_status_update_unknown_chat_id_does_not_send(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "pdf_base64": sample_pdf_base64,
        "subject": "Holat yangilanishi",
        "message": "Buyurtmangiz bekor qilindi.",
        "user": {"username": "unknown_user"},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    body = resp.json()
    assert body["status_update"]["targets"] == [
        {"username": "unknown_user", "chat_id": None, "sent": False}
    ]
    mock_telegram_calls["send_message"].assert_not_called()


async def test_webhook_status_update_group_target_sends_to_group(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "pdf_base64": sample_pdf_base64,
        "subject": "Holat yangilanishi",
        "message": "Buyurtma tasdiqlandi.",
        "group_id": -1009998887,
        "user": {"chat_id": 111222333},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    # Guruhga HAM, mas'ul shaxsning shaxsiy chat'iga HAM yuboriladi (mustaqil, asosiy
    # oqim bilan bir xil qoida) -- ikkalasi ham send_message orqali (send_document emas).
    mock_telegram_calls["send_document"].assert_not_called()
    assert mock_telegram_calls["send_message"].call_count == 2
    chat_ids_called = {call.kwargs["chat_id"] for call in mock_telegram_calls["send_message"].call_args_list}
    assert chat_ids_called == {-1009998887, 111222333}


async def test_webhook_status_update_does_not_create_order_row(client, sample_pdf_base64, mock_telegram_calls):
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import Order

    payload = {
        "pdf_base64": sample_pdf_base64,
        "subject": "Holat yangilanishi",
        "message": "Buyurtmangiz tasdiqlandi.",
        "user": {"chat_id": 222333444},
    }
    resp = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    assert resp.status_code == 201

    async with async_session_maker() as session:
        orders = (await session.execute(select(Order))).scalars().all()
        assert orders == []


# --- Takroriy "Holat yangilanishi" push'lar (2026-08-03): Odoo bitta o'zgarish uchun
# aynan bir xil xabarni bir necha marta yuborishi mumkin (live tasdiqlangan: S56653
# uchun "bekor qilindi" matni 3 marta ketma-ket, ~1 daqiqa ichida) -- faqat BIRINCHISI
# haqiqiy Telegram xabari sifatida yetib borishi kerak ----------------------------------


async def test_webhook_status_update_duplicate_individual_sent_only_once(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "doc_id": "S56653",
        "pdf_base64": sample_pdf_base64,
        "subject": "Holat yangilanishi",
        "message": "S56653-sonli buyurtmangiz menejer Hasanxo'jayev Sardor tomonidan bekor qilindi.",
        "user": {"username": None, "chat_id": 1532815874},
    }

    first = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    second = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    third = await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)

    assert first.status_code == second.status_code == third.status_code == 201
    assert first.json()["status_update"]["targets"][0]["sent"] is True
    assert second.json()["status_update"]["targets"][0] == {
        "username": "no_username", "chat_id": 1532815874, "sent": False, "duplicate": True,
    }
    assert third.json()["status_update"]["targets"][0]["duplicate"] is True

    mock_telegram_calls["send_message"].assert_called_once()
    mock_telegram_calls["send_document"].assert_not_called()


async def test_webhook_status_update_duplicate_group_sent_only_once(client, sample_pdf_base64, mock_telegram_calls):
    payload = {
        "doc_id": "S56653",
        "pdf_base64": sample_pdf_base64,
        "subject": "Holat yangilanishi",
        "message": "Buyurtma tasdiqlandi.",
        "group_id": -1009998887,
        "user": {"chat_id": 111222333},
    }

    await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)
    await client.post("/api/v1/webhook/odoo", json=payload, headers=API_HEADERS)

    # Guruhga HAM, individual chat'ga HAM faqat 1 martadan -- jami 2 ta send_message.
    assert mock_telegram_calls["send_message"].call_count == 2


async def test_webhook_status_update_different_message_same_doc_id_still_sends(
    client, sample_pdf_base64, mock_telegram_calls
):
    """Takroriylik tekshiruvi (doc_id, message) juftligi bo'yicha -- xuddi shu doc_id
    uchun HAQIQIY farqli matn (masalan avval "tasdiqlandi", keyin "bekor qilindi")
    kelsa, bu takroriy DEB HISOBLANMAYDI, ikkalasi ham yuboriladi."""
    base_payload = {
        "doc_id": "S56700",
        "pdf_base64": sample_pdf_base64,
        "subject": "Holat yangilanishi",
        "user": {"username": None, "chat_id": 1532815874},
    }

    await client.post(
        "/api/v1/webhook/odoo", json={**base_payload, "message": "Buyurtmangiz tasdiqlandi."}, headers=API_HEADERS
    )
    await client.post(
        "/api/v1/webhook/odoo", json={**base_payload, "message": "Buyurtmangiz bekor qilindi."}, headers=API_HEADERS
    )

    assert mock_telegram_calls["send_message"].call_count == 2
