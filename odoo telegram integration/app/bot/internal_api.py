"""Bot konteyneridan FastAPI konteyneriga (network_mode: host orqali localhost) ichki
so'rovlar uchun bitta doimiy, ulanishni qayta ishlatadigan httpx.AsyncClient.

Tezlik uchun (2026-08-03): avval har bir ichki so'rov (menyu tugmasi, buyurtma
joylashtirish, lokatsiya, guruh so'rovi -- handlers/menu.py va handlers/group_mentions.py)
o'z alohida `httpx.AsyncClient`ini `async with` orqali ochib-yopar edi -- bu har safar
yangi TCP ulanish ochish xarajatini keltirib chiqarardi. Endi process bo'yicha BITTA
client bir marta yaratiladi va qayta ishlatiladi (keep-alive connection pool), shu sabab
har bir tugma bosilganda ortiqcha ulanish ochish xarajati yo'qoladi. `odoo_client.py`dagi
thread-local ServerProxy keshlash bilan bir xil naqsh (lazy, process darajasida)."""

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def get_internal_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.INTERNAL_API_URL, timeout=30)
    return _client
