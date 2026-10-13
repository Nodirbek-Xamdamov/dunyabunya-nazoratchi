"""Foydalanuvchiga ko'rsatiladigan xato sabablari.

Ichki xato matni ("Error code: 529 - {'type': 'error', ...}") CEO uchun ma'nosiz — bu
yerda u nima bo'lgani va nima qilish kerakligini aytadigan qisqa o'zbekcha jumlaga
aylantiriladi. To'liq texnik matn baribir logda (va ALERT_CHAT_ID sozlangan bo'lsa —
dasturchiga yuboriladigan ogohlantirishda) qoladi.
"""

from __future__ import annotations

import anthropic

from app.services.odoo import OdooError


def friendly_error(exc: BaseException) -> str:
    """Xatoning foydalanuvchiga tushunarli sababi (jumla oxirida nuqtasiz)."""
    if isinstance(exc, OdooError):
        return str(exc)[:300]  # Odoo xatolari allaqachon tushunarli matn bilan yaratiladi

    # Claude (Anthropic) — eng aniq turdan umumiyiga qarab
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "AI xizmatiga kirish rad etildi (API kalit yoki ruxsat muammosi) — tizim administratoriga xabar bering"
    if isinstance(exc, anthropic.RateLimitError):
        return "AI xizmatiga so'rovlar limiti to'ldi — bir-ikki daqiqadan keyin qayta urinib ko'ring"
    if isinstance(exc, anthropic.APITimeoutError):
        return "AI xizmati javobi juda kechikdi — birozdan keyin qayta yuboring yoki savolni soddalashtiring"
    if isinstance(exc, anthropic.APIConnectionError):
        return "AI xizmatiga ulanib bo'lmadi (internet aloqasi) — birozdan keyin qayta urinib ko'ring"
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return "AI xizmati hozir band yoki vaqtincha ishlamayapti — bir daqiqadan keyin qayta urinib ko'ring"
        if exc.status_code == 402:
            return "AI xizmati hisobida mablag' tugagan — tizim administratoriga xabar bering"
        if exc.status_code == 404:
            return "AI modeli topilmadi (sozlamada model nomi noto'g'ri) — tizim administratoriga xabar bering"
        return "AI xizmati so'rovni qabul qilmadi — tizim administratoriga xabar bering"

    return str(exc)[:200] or type(exc).__name__
