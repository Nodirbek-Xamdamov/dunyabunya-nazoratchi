# app/main.py
import asyncio
import logging
from contextlib import asynccontextmanager

# Ilgari root logger'ga hech qanday handler ulanmagan edi, shuning uchun
# app/*.py'dagi logger.info/logger.exception chaqiruvlari (poll loop,
# operator relay va h.k.) hech qayerga yozilmay yo'qolib ketardi.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.ai_service import get_answer
from app.catalog import refresh_loop, refresh_products
from app.config import settings
from app.models import AskRequest, AskResponse, ChatHistoryResponse, ChatMessage, SaveMessageRequest
from app.poller import poll_loop, process_conversation
from app.rate_limit import session_limiter
from app.store import chat_store
from app.telegram_operator import (
    forward_customer_message,
    is_operator_session_active,
    mark_customer_activity,
    operator_poll_loop,
    operator_timeout_loop,
    relay_configured,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Katalog birinchi so'rovdan oldin tayyor bo'lsin; API javob bermasa ham
    # server ishga tushadi, refresh_loop qayta urinadi.
    try:
        await refresh_products()
    except Exception:
        logger.exception("Ishga tushishda katalogni yuklab bo'lmadi, refresh_loop qayta urinadi.")
    catalog_task = asyncio.create_task(refresh_loop())

    poll_task: asyncio.Task | None = None
    if settings.remote_chat_api_base_url and settings.remote_chat_poll_conversation_ids_list:
        poll_task = asyncio.create_task(poll_loop())
    else:
        logger.info("Remote chat poll loop o'chiq (base_url yoki conversationId ro'yxati bo'sh).")

    operator_task: asyncio.Task | None = None
    timeout_task: asyncio.Task | None = None
    if relay_configured():
        operator_task = asyncio.create_task(operator_poll_loop())
        timeout_task = asyncio.create_task(operator_timeout_loop())
    else:
        logger.info("Telegram operator relay o'chiq (bot token yoki operator chat_id bo'sh).")

    yield

    for task in (catalog_task, poll_task, operator_task, timeout_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Construction Materials Store Chatbot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/chat/messages", response_model=ChatMessage, response_model_by_alias=True)
async def save_message(payload: SaveMessageRequest) -> ChatMessage:
    return await chat_store.add_message(payload.conversation_id, payload.role, payload.content)


@app.get("/api/chat/messages", response_model=ChatHistoryResponse, response_model_by_alias=True)
async def get_history(conversationId: str = Query(...)) -> ChatHistoryResponse:
    messages = await chat_store.get_messages(conversationId)
    return ChatHistoryResponse(conversation_id=conversationId, messages=messages)


@app.post("/api/chatbot/ask", response_model=AskResponse, response_model_by_alias=True)
async def ask_chatbot(payload: AskRequest) -> AskResponse:
    limit_message = session_limiter.check(payload.conversation_id)
    if limit_message is not None:
        return AskResponse(conversation_id=payload.conversation_id, answer=limit_message)

    await chat_store.add_message(payload.conversation_id, "user", payload.user_message)
    mark_customer_activity(payload.conversation_id)

    # Suhbat operatorga ulangan bo'lsa, AI bu yerda umuman ishtirok etmaydi —
    # mijozning har bir keyingi xabari to'g'ridan-to'g'ri Telegram'ga ketadi,
    # operator javob berguncha.
    if is_operator_session_active(payload.conversation_id):
        await forward_customer_message(payload.conversation_id, payload.user_message)
        return AskResponse(conversation_id=payload.conversation_id, answer="")

    history = await chat_store.get_messages(payload.conversation_id)

    try:
        answer = await get_answer(history, payload.conversation_id)
    except Exception:
        logger.exception("AI service error for conversation %s", payload.conversation_id)
        raise HTTPException(
            status_code=502,
            detail="AI xizmatida xatolik yuz berdi, birozdan keyin qayta urinib ko'ring.",
        )

    await chat_store.add_message(payload.conversation_id, "bot", answer)
    return AskResponse(conversation_id=payload.conversation_id, answer=answer)


@app.post("/api/test/poll-once")
async def poll_once_now(conversationId: str = Query(...)) -> dict:
    """TEST uchun qo'lda ishga tushirish: interval kutmasdan, berilgan
    conversationId'ni darhol bir marta tekshirib, kerak bo'lsa javob yozadi.
    Faqat remote poll integratsiyasi sozlangan bo'lsa ishlaydi."""
    if not settings.remote_chat_api_base_url:
        raise HTTPException(status_code=400, detail="remote_chat_api_base_url sozlanmagan.")
    await process_conversation(conversationId)
    return {"status": "ok", "conversationId": conversationId}