"""Parallel polling regression test.

`main._spawn_update` har update'ni alohida asyncio task sifatida ishga tushiradi —
bir topikdagi xabar uzoq ishlayotganda boshqa topiklardagi xabarlar kutib qolmasligi
shart. Bu test: 3 ta update'ni spawn qilganda ular KETMA-KET (0.2s * 3) emas,
PARALLEL (taxminan 0.2s) bajarilishini isbotlaydi.
"""
from __future__ import annotations

import asyncio
import time

from aiogram.types import Chat, Message, Update, User

import app.main as main_module
import app.rate_limiter as rate_limiter


async def test_updates_dispatched_in_parallel(monkeypatch):
    slow = 0.2

    async def fake_feed_update(bot, update):
        # Har update'ni ushlab turadigan og'ir ishni simulyatsiya qilamiz
        await asyncio.sleep(slow)
        return None

    monkeypatch.setattr(main_module.dp, "feed_update", fake_feed_update)

    # 3 ta update bir vaqtda keladi
    start = time.monotonic()
    for i in range(3):
        main_module._spawn_update(Update(update_id=i + 1))

    # Barcha tasklar tugashini kutamiz
    deadline = time.monotonic() + 5
    while main_module._ACTIVE_UPDATES and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    elapsed = time.monotonic() - start

    assert not main_module._ACTIVE_UPDATES, "update tasklari tugamadi"
    # Ketma-ket bo'lsa ~0.6s+ bo'lardi; parallel bo'lsa ~0.2-0.3s
    assert elapsed < slow * 2.5, f"update'lar PARALLEL emas: {elapsed:.2f}s (3*{slow}s bo'lishi kerak edi)"


async def test_spawn_returns_immediately(monkeypatch):
    """_spawn_update o'zi hech narsani kutmasligi kerak (create_task + qaytish)."""
    async def fake_feed_update(bot, update):
        await asyncio.sleep(10)  # juda uzoq ish
        return None

    monkeypatch.setattr(main_module.dp, "feed_update", fake_feed_update)

    start = time.monotonic()
    main_module._spawn_update(Update(update_id=999))
    elapsed = time.monotonic() - start

    # create_task darhol qaytadi — handler 10s ishlasa ham spawn 0.1s dan oshmasligi kerak
    assert elapsed < 0.1, f"_spawn_update bloklandi: {elapsed:.2f}s"
    # Taskni tozalaymiz
    for task in list(main_module._ACTIVE_UPDATES):
        task.cancel()
    await asyncio.gather(*main_module._ACTIVE_UPDATES, return_exceptions=True)


# ── P1-5: throttling _run_update darajasida ─────────────────────────────

def _make_message_update(update_id: int, user_id: int, username: str | None = None) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test", username=username)
    chat = Chat(id=user_id, type="private")
    msg = Message(message_id=1, date=int(time.time()), chat=chat, from_user=user, text="salom")
    return Update(update_id=update_id, message=msg)


def test_update_sender_id_extracts_from_message():
    update = _make_message_update(1, 555)
    assert main_module._update_sender_id(update) == 555


def test_update_sender_id_none_for_update_without_user():
    assert main_module._update_sender_id(Update(update_id=1)) is None


async def test_run_update_skips_dispatch_when_rate_limited(monkeypatch):
    rate_limiter._history.clear()
    called = {"n": 0}

    async def fake_feed_update(bot, update):
        called["n"] += 1

    monkeypatch.setattr(main_module.dp, "feed_update", fake_feed_update)
    monkeypatch.setattr(main_module, "is_rate_limited", lambda uid: True)

    await main_module._run_update(_make_message_update(1, 777))

    assert called["n"] == 0


async def test_run_update_dispatches_when_not_rate_limited(monkeypatch):
    rate_limiter._history.clear()
    called = {"n": 0}

    async def fake_feed_update(bot, update):
        called["n"] += 1

    monkeypatch.setattr(main_module.dp, "feed_update", fake_feed_update)
    monkeypatch.setattr(main_module, "is_rate_limited", lambda uid: False)

    await main_module._run_update(_make_message_update(1, 777))

    assert called["n"] == 1


async def test_run_update_remembers_ceo_chat_id_on_first_message(monkeypatch):
    """Ikkinchi CEO botga (qayerda bo'lsa ham) birinchi marta yozishi bilan
    uning raqamli chat_id'si ceo_state'ga saqlanadi — handler'ga bog'liq emas."""
    from app import ceo_state

    async def fake_feed_update(bot, update):
        pass

    monkeypatch.setattr(main_module.dp, "feed_update", fake_feed_update)
    monkeypatch.setattr(main_module, "is_rate_limited", lambda uid: False)
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USER_ID", 0)
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USERNAME", "AbdurRohman_Zafar,hasanxojayev_sardor")

    await main_module._run_update(_make_message_update(1, 7001, username="HasanXojayev_Sardor"))
    await main_module._run_update(_make_message_update(2, 8002, username="oddiy_xodim"))
    for _ in range(20):  # fon _capture vazifasi tugashini kutamiz
        await asyncio.sleep(0)

    assert await ceo_state.get_ceo_telegram_user_ids() == [7001]
    assert '"hasanxojayev_sardor": 7001' in ceo_state._PATH.read_text(encoding="utf-8")
