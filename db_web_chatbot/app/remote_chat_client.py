# app/remote_chat_client.py
#
# .NET AuthApi'dagi X-Api-Key bilan himoyalangan chat tarixi API'si uchun
# ingichka client — faqat TEST/poll integratsiyasi uchun (192.168.89.x ichki
# tarmoq orqali kelayotgan /api/chatbot/ask chaqiruvi ishlamayotgani
# tasdiqlangani uchun vaqtincha shu yo'l sinalmoqda).
#
# Muhim: bu APIning yo'llari (/api/chat/messages) bizning o'zimizning
# app/main.py'dagi /api/chat/messages bilan bir xil nomlangan, lekin BUTUNLAY
# BOSHQA xizmat (demo.dunyabunya.uz'dagi .NET backend) va boshqa saqlagich —
# ular bilan aralashtirib yubormaslik kerak.
import httpx2

from app.config import settings
from app.models import ChatMessage


class RemoteChatApiError(Exception):
    pass


def _headers() -> dict[str, str]:
    return {"X-Api-Key": settings.remote_chat_api_key}


async def fetch_messages(conversation_id: str) -> list[ChatMessage]:
    """GET /api/chat/messages?conversationId=... — suhbatning to'liq tarixi
    (eskisidan yangisiga), ChatMessage modeliga o'girilgan holda."""
    url = f"{settings.remote_chat_api_base_url.rstrip('/')}/api/chat/messages"
    async with httpx2.AsyncClient(timeout=15) as client:
        response = await client.get(
            url,
            params={"conversationId": conversation_id},
            headers=_headers(),
        )
    if response.status_code != 200:
        raise RemoteChatApiError(
            f"GET /api/chat/messages -> {response.status_code}: {response.text[:300]}"
        )
    raw = response.json()
    return [
        ChatMessage(role=item["sender"], content=item["text"], created_at=item["createdAt"])
        for item in raw
    ]


async def post_bot_reply(conversation_id: str, text: str) -> None:
    """POST /api/chat/messages — bizning javobimizni "bot" xabari sifatida
    saqlaydi."""
    url = f"{settings.remote_chat_api_base_url.rstrip('/')}/api/chat/messages"
    payload = {"conversationId": conversation_id, "sender": "bot", "text": text}
    async with httpx2.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload, headers=_headers())
    if response.status_code != 200:
        raise RemoteChatApiError(
            f"POST /api/chat/messages -> {response.status_code}: {response.text[:300]}"
        )
