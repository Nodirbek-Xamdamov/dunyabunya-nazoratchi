# app/poller.py
#
# TEST integratsiyasi: haqiqiy "yangi suhbatni qanday topamiz" savoli hali hal
# qilinmagani uchun, hozircha qo'lda berilgan (settings.remote_chat_poll_
# conversation_ids) conversationId ro'yxatini davriy ravishda tekshiramiz.
# Har biri uchun: agar oxirgi xabar "user"dan bo'lsa (hali javob yozilmagan),
# to'liq tarixni AI'ga uzatib javob olamiz va uni "bot" sifatida yozib
# qo'yamiz. Keyingi tsiklda oxirgi xabar allaqachon "bot" bo'lib qoladi,
# shuning uchun qayta javob yozilmaydi — qo'shimcha holat (state) saqlashga
# hojat yo'q.
import asyncio
import logging

from app.ai_service import get_answer
from app.config import settings
from app.remote_chat_client import RemoteChatApiError, fetch_messages, post_bot_reply

logger = logging.getLogger(__name__)


async def process_conversation(conversation_id: str) -> None:
    history = await fetch_messages(conversation_id)
    if not history or history[-1].role != "user":
        return  # javobsiz mijoz xabari yo'q

    answer = await get_answer(history, conversation_id)
    await post_bot_reply(conversation_id, answer)
    logger.info("Poll: %s uchun javob yozildi.", conversation_id)


async def poll_once() -> None:
    for conversation_id in settings.remote_chat_poll_conversation_ids_list:
        try:
            await process_conversation(conversation_id)
        except RemoteChatApiError:
            logger.exception("Poll: %s uchun remote chat API xatosi.", conversation_id)
        except Exception:
            logger.exception("Poll: %s ni qayta ishlashda kutilmagan xato.", conversation_id)


async def poll_loop() -> None:
    logger.info(
        "Poll loop boshlandi: %s | har %ss",
        settings.remote_chat_poll_conversation_ids_list,
        settings.remote_chat_poll_interval_seconds,
    )
    while True:
        await poll_once()
        await asyncio.sleep(settings.remote_chat_poll_interval_seconds)
