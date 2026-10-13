"""P1-5: oddiy throttling — bitta foydalanuvchidan qisqa vaqtda (sukut: 1
daqiqada 10+) xabar kelsa, ortiqchasi e'tiborsiz qoldiriladi.

Xotirada (jarayon ichida) ishlaydi — Redis/DB shart emas, chunki maqsad
faqat tasodifiy/qasddan xabar toshqinidan (masalan ovozli xabar tsikli,
buzilgan klient) himoya qilish, aniq/qattiq limit emas. Bot qayta ishga
tushsa hisoblagich tozalanadi — bu qabul qilinadigan holat."""

from __future__ import annotations

import time
from collections import defaultdict, deque

_WINDOW_SECONDS = 60
_MAX_MESSAGES = 10

_history: dict[int, deque] = defaultdict(deque)


def is_rate_limited(user_id: int, *, max_messages: int = _MAX_MESSAGES,
                     window_seconds: float = _WINDOW_SECONDS) -> bool:
    """True qaytarsa — bu xabar e'tiborsiz qoldirilishi kerak (limit oshgan).
    Aks holda xabar hisoblanadi va False qaytariladi."""
    now = time.monotonic()
    dq = _history[user_id]
    while dq and now - dq[0] > window_seconds:
        dq.popleft()
    if len(dq) >= max_messages:
        return True
    dq.append(now)
    return False


_last_notice: dict[int, float] = {}


def claim_throttle_notice(user_id: int, *, window_seconds: float = _WINDOW_SECONDS) -> bool:
    """Limit oshgani haqida foydalanuvchiga xabar berish navbati: bir oynada faqat
    birinchi chaqiruv True qaytaradi (har tashlab yuborilgan xabarga alohida javob ketmasin)."""
    now = time.monotonic()
    last = _last_notice.get(user_id)
    if last is not None and now - last < window_seconds:
        return False
    _last_notice[user_id] = now
    return True
