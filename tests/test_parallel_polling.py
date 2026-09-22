"""Parallel polling regression test.

`main._spawn_update` har update'ni alohida asyncio task sifatida ishga tushiradi —
bir topikdagi xabar uzoq ishlayotganda boshqa topiklardagi xabarlar kutib qolmasligi
shart. Bu test: 3 ta update'ni spawn qilganda ular KETMA-KET (0.2s * 3) emas,
PARALLEL (taxminan 0.2s) bajarilishini isbotlaydi.
"""
from __future__ import annotations

import asyncio
import time

from aiogram.types import Update

import app.main as main_module


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