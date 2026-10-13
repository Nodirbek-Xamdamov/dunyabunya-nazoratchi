# app/rate_limit.py
#
# Xotiradagi, process-level cheklovchi: yangi DB jadval yoki Redis shart emas —
# bitta uvicorn processida yetarli (qayta ishga tushganda tozalanadi).
import time
from collections import defaultdict, deque

from app.config import settings


class SessionLimiter:
    def __init__(self):
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._total: dict[str, int] = defaultdict(int)

    def check(self, conversation_id: str) -> str | None:
        """Limit oshgan bo'lsa, mijozga qaytariladigan xabarni qaytaradi; aks holda None."""
        now = time.monotonic()
        window = self._requests[conversation_id]
        while window and now - window[0] > 60:
            window.popleft()

        if len(window) >= settings.rate_limit_per_minute:
            return "Juda tez-tez xabar yuboryapsiz, biroz kuting va qayta urinib ko'ring."

        if self._total[conversation_id] >= settings.session_message_limit:
            return (
                "Bu suhbatda xabarlar soni chegarasiga yetdi. "
                f"Batafsil ma'lumot uchun: {settings.company_phone}"
            )

        window.append(now)
        self._total[conversation_id] += 1
        return None


session_limiter = SessionLimiter()
