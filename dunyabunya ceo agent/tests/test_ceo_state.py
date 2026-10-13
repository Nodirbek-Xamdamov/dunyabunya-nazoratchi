"""app/ceo_state.py — CEO'ning raqamli Telegram ID'sini avtomatik aniqlab
saqlash mexanizmi uchun testlar. Har test o'z vaqtinchalik faylida ishlaydi."""
from __future__ import annotations

import pytest

from app import ceo_state


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ceo_state, "_PATH", tmp_path / "ceo_state.json")
    ceo_state._cached_id = None
    ceo_state._loaded = False
    yield
    ceo_state._cached_id = None
    ceo_state._loaded = False


async def test_returns_none_when_nothing_known(monkeypatch):
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USER_ID", 0)
    assert await ceo_state.get_ceo_telegram_user_id() is None


async def test_falls_back_to_env_setting(monkeypatch):
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USER_ID", 12345)
    assert await ceo_state.get_ceo_telegram_user_id() == 12345


async def test_remember_persists_and_takes_priority_over_env(monkeypatch):
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USER_ID", 12345)
    await ceo_state.remember_ceo_telegram_user_id(999888)
    assert await ceo_state.get_ceo_telegram_user_id() == 999888


async def test_remembered_id_persists_across_reload():
    await ceo_state.remember_ceo_telegram_user_id(555)
    ceo_state._cached_id = None
    ceo_state._loaded = False
    assert await ceo_state.get_ceo_telegram_user_id() == 555


async def test_remember_same_id_twice_is_a_noop(monkeypatch):
    calls = {"save": 0}
    real_save = ceo_state._save

    def counting_save():
        calls["save"] += 1
        real_save()

    monkeypatch.setattr(ceo_state, "_save", counting_save)
    await ceo_state.remember_ceo_telegram_user_id(42)
    await ceo_state.remember_ceo_telegram_user_id(42)
    assert calls["save"] == 1


async def test_two_ceos_by_username(monkeypatch):
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USER_ID", 0)
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USERNAME", "AbdurRohman_Zafar, @hasanxojayev_sardor")
    assert ceo_state.is_ceo(1, "abdurrohman_zafar")
    assert ceo_state.is_ceo(2, "HasanXojayev_Sardor")
    assert not ceo_state.is_ceo(3, "someone_else")
    assert not ceo_state.is_ceo(4, None)
    await ceo_state.remember_ceo_telegram_user_id(1, "AbdurRohman_Zafar")
    await ceo_state.remember_ceo_telegram_user_id(2, "hasanxojayev_sardor")
    assert await ceo_state.get_ceo_telegram_user_ids() == [1, 2]


async def test_removed_ceo_gets_no_proactive_messages(monkeypatch):
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USER_ID", 0)
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USERNAME", "a_ceo,b_ceo")
    await ceo_state.remember_ceo_telegram_user_id(1, "a_ceo")
    await ceo_state.remember_ceo_telegram_user_id(2, "b_ceo")
    monkeypatch.setattr(ceo_state.settings, "CEO_TELEGRAM_USERNAME", "a_ceo")
    assert await ceo_state.get_ceo_telegram_user_ids() == [1]


async def test_legacy_single_id_file_is_read(tmp_path):
    ceo_state._PATH.write_text('{"telegram_user_id": 777}', encoding="utf-8")
    ceo_state._loaded = False
    assert await ceo_state.get_ceo_telegram_user_ids() == [777]
