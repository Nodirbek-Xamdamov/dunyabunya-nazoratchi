"""P1-5: app/rate_limiter.py — oddiy throttling testlari."""
from __future__ import annotations

import app.rate_limiter as rate_limiter


def _reset():
    rate_limiter._history.clear()


def test_reset_fixture_works():
    _reset()
    assert rate_limiter._history == {}


def test_allows_up_to_max_messages():
    _reset()
    for _ in range(10):
        assert rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60) is False


def test_blocks_after_max_messages_within_window():
    _reset()
    for _ in range(10):
        rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60)
    assert rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60) is True


def test_different_users_tracked_independently():
    _reset()
    for _ in range(10):
        rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60)
    # 1-foydalanuvchi limitga yetgan, lekin 2-chisi hali erkin
    assert rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60) is True
    assert rate_limiter.is_rate_limited(2, max_messages=10, window_seconds=60) is False


def test_old_entries_outside_window_do_not_count(monkeypatch):
    _reset()
    t = [1000.0]
    monkeypatch.setattr(rate_limiter.time, "monotonic", lambda: t[0])

    for _ in range(10):
        rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60)
    assert rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60) is True

    # Oyna vaqtidan tashqariga o'tamiz — eski yozuvlar endi hisoblanmasligi kerak
    t[0] += 61
    assert rate_limiter.is_rate_limited(1, max_messages=10, window_seconds=60) is False
