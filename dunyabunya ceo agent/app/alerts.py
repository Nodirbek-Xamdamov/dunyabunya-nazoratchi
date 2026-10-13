"""P1-2: tizim xatolari haqida Telegram orqali ogohlantirish.

`.env`da `ALERT_CHAT_ID` berilgan bo'lsa, ERROR darajasidagi har bir log yozuvi
(AI javob bermadi, Odoo'ga yozilmadi, kutilmagan istisno...) shu chatga yuboriladi —
dasturchi muammoni CEO aytishidan oldin biladi. Berilmagan bo'lsa hech narsa
yuborilmaydi (xatolar baribir logs/bot.log faylida qoladi).

Xabar toshqini bo'lmasligi uchun: bir xil xato `_REPEAT_SECONDS` ichida bir marta,
jami esa `_BATCH_SECONDS`da bitta (yig'ma) xabar yuboriladi.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings

log = logging.getLogger(__name__)

_BATCH_SECONDS = 30
_REPEAT_SECONDS = 600
_MAX_PENDING = 20
_MAX_MESSAGE_CHARS = 3500


class TelegramAlertHandler(logging.Handler):
    """ERROR loglarini navbatga qo'yadi; `run()` ularni Telegram'ga yuboradi."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._pending: list[str] = []
        self._last_sent: dict[str, float] = {}
        self._dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:  # ogohlantirishni yuborishdagi xato o'zini qayta chaqirmasin
            return
        try:
            text = f"{record.name}: {record.getMessage()}"
            if record.exc_info and record.exc_info[1] is not None:
                text += f"\n{type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
        except Exception:  # noqa: BLE001 — log formatidagi xato dasturni to'xtatmasligi kerak
            return
        key = text[:120]
        now = time.monotonic()
        if now - self._last_sent.get(key, -_REPEAT_SECONDS) < _REPEAT_SECONDS:
            return
        self._last_sent[key] = now
        if len(self._pending) >= _MAX_PENDING:
            self._dropped += 1
            return
        self._pending.append(text[:800])

    def drain(self) -> str | None:
        """Yig'ilgan xatolardan bitta xabar matni (bo'lmasa None)."""
        if not self._pending:
            return None
        lines = ["⚠️ dunyabunya bot — tizim xabari:", ""] + [f"• {t}" for t in self._pending]
        if self._dropped:
            lines.append(f"... va yana {self._dropped} ta xato (logs/bot.log ga qarang)")
        self._pending.clear()
        self._dropped = 0
        if len(self._last_sent) > 500:
            self._last_sent.clear()
        return "\n".join(lines)[:_MAX_MESSAGE_CHARS]

    async def run(self, bot) -> None:
        while True:
            await asyncio.sleep(_BATCH_SECONDS)
            message = self.drain()
            if not message:
                continue
            try:
                await bot.send_message(chat_id=settings.ALERT_CHAT_ID, text=message)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("ogohlantirishni Telegram'ga yuborib bo'lmadi: %s", e)


_active: TelegramAlertHandler | None = None


def notify(text: str) -> None:
    """Xato bo'lmagan muhim xabarni (masalan "Odoo bilan aloqa tiklandi") ogohlantirish
    chatiga navbatga qo'yadi. Ogohlantirishlar o'chiq bo'lsa — hech narsa qilmaydi."""
    if _active is not None and len(_active._pending) < _MAX_PENDING:
        _active._pending.append(text[:800])


def start_alerts(bot) -> asyncio.Task | None:
    """main.py lifespan ichida chaqiriladi. ALERT_CHAT_ID sozlanmagan bo'lsa None."""
    global _active
    if not settings.ALERT_CHAT_ID:
        log.info("alerts: ALERT_CHAT_ID sozlanmagan — xato ogohlantirishlari Telegram'ga yuborilmaydi")
        return None
    handler = TelegramAlertHandler()
    logging.getLogger().addHandler(handler)
    _active = handler
    log.info("alerts: xato ogohlantirishlari %s chatiga yuboriladi", settings.ALERT_CHAT_ID)
    return asyncio.create_task(handler.run(bot))
