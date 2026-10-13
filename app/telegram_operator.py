# app/telegram_operator.py
#
# Bitta operator (Telegram shaxsiy chat) bilan ko'p mijozli veb-chatni
# bog'lash: mijoz operatorga ulanishni so'raganda, so'rovi operatorga alohida
# Telegram xabari sifatida yuboriladi (notify_operator). Operator o'sha aniq
# xabarga Telegram "Reply" qilib javob berganda, Telegram bu javobda
# reply_to_message.message_id'ni qaytaradi — shu ID orqali qaysi mijozning
# suhbati ekani aniqlanadi (operator_poll_loop), chunki bir vaqtning o'zida
# bir nechta mijoz operatorga ulanishni so'rashi mumkin va oddiy "oxirgi
# yozgan mijoz" taxmini noto'g'ri suhbatga javob yozib qo'yishi mumkin edi.
#
# Barcha holat xotirada saqlanadi (chat_store kabi) — server qayta ishga
# tushganda tozalanadi.
import asyncio
import logging
import time

import httpx2

from app.config import settings
from app.store import chat_store

logger = logging.getLogger(__name__)

# Telegram message_id (operatorga yuborilgan xabarniki) -> conversation_id
_PENDING: dict[int, str] = {}

# Operatorga ULANGAN (lekin hali javob kutayotgan bo'lishi ham mumkin)
# suhbatlar to'plami — is_operator_session_active shu yerni tekshiradi, shu
# tufayli mijozning keyingi xabarlari AI'ga emas, Telegram'ga ketadi.
_CONNECTED: set[str] = set()

# 2-daqiqalik jim-qolish taymeri — FAQAT operator birinchi marta javob
# yozgandan keyin boshlanadi (conversation_id -> time.monotonic()). Operator
# hali javob yozmagan bo'lsa bu yerda yo'q, shuning uchun operator xohlagancha
# (5 daqiqa ham) kechiktirsa bo'ladi — hech narsa avtomatik yopilmaydi.
_TIMERS: dict[str, float] = {}
SESSION_TIMEOUT_SECONDS = 120
_TIMEOUT_MESSAGE = (
    "Sizdan uzoq vaqtdan beri javob kelmagani sababli suhbatni yakunlaymiz. "
    "Yordam kerak bo'lsa, istalgan vaqtda qayta yozishingiz mumkin."
)


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def relay_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.operator_telegram_chat_id)


async def _send_telegram_message(text: str) -> int | None:
    """Operatorga xom Telegram xabari yuboradi, message_id qaytaradi (yoki
    xato bo'lsa None)."""
    try:
        async with httpx2.AsyncClient(timeout=15) as client:
            response = await client.post(
                _api_url("sendMessage"),
                json={"chat_id": settings.operator_telegram_chat_id, "text": text},
            )
        if response.status_code != 200:
            logger.error("Telegram sendMessage xato: %s %s", response.status_code, response.text[:300])
            return None
        return response.json().get("result", {}).get("message_id")
    except Exception:
        logger.exception("Telegram sendMessage yuborishda xato.")
        return None


async def notify_operator(conversation_id: str, customer_message: str) -> bool:
    """Mijoz operatorga ulanishni so'raganda chaqiriladi. True — xabar
    operatorga yuborildi (mijozga "shu yerda javob keladi" deyish mumkin).
    False — relay sozlanmagan/xato bo'ldi (mijozga zaxira sifatida Telegram
    username berish kerak). Bu bosqichda 2 daqiqalik taymer HALI
    boshlanmaydi — operator qancha kech javob yozsa ham bemalol."""
    if not relay_configured():
        return False

    text = (
        "Yangi mijoz so'rovi (veb-chat)\n"
        f"Suhbat: {conversation_id}\n"
        f"Savol: {customer_message}\n\n"
        "Javob berish uchun ushbu xabarga Reply qiling."
    )
    message_id = await _send_telegram_message(text)
    if message_id is None:
        return False

    _PENDING[message_id] = conversation_id
    _CONNECTED.add(conversation_id)
    logger.info("Operatorga xabar yuborildi: suhbat=%s, telegram_message_id=%s", conversation_id, message_id)
    return True


def is_operator_session_active(conversation_id: str) -> bool:
    """Suhbat hozir operatorga ulangan holatdami — shunday bo'lsa, mijozning
    keyingi xabarlari AI'ga emas, to'g'ridan-to'g'ri operatorga ketishi kerak
    (app/main.py'da tekshiriladi)."""
    return conversation_id in _CONNECTED


async def forward_customer_message(conversation_id: str, text: str) -> None:
    """Mijoz operatorga ulangan suhbatda yana yozganda chaqiriladi — AI'ni
    chetlab, xabarni to'g'ridan-to'g'ri operatorga Telegram orqali uzatadi.
    Yangi xabar_id ham _PENDING'ga qo'shiladi — operator shu aniq xabarga
    Reply qilsa ham to'g'ri suhbatga bog'lanadi (faqat birinchi xabarga emas).
    Taymerni bu yerda YANGILAMAYMIZ — mark_customer_activity buni allaqachon
    qildi (agar taymer operator javobidan keyin allaqachon boshlangan bo'lsa)."""
    message = f"Mijoz ({conversation_id}): {text}"
    message_id = await _send_telegram_message(message)
    if message_id is not None:
        _PENDING[message_id] = conversation_id


def mark_customer_activity(conversation_id: str) -> None:
    """Mijoz xabar yozganda har doim chaqiriladi — agar shu suhbat uchun
    taymer allaqachon ishlab turgan bo'lsa (operator kamida bir marta javob
    yozgan bo'lsa), 2 daqiqalik hisobni qaytadan boshlaydi. Operator hali
    javob yozmagan (_TIMERS'da yo'q) suhbatlarga tegmaydi."""
    if conversation_id in _TIMERS:
        _TIMERS[conversation_id] = time.monotonic()


async def _handle_update(update: dict) -> None:
    message = update.get("message")
    if not message:
        logger.info("Telegram update xabarsiz (o'tkazib yuborildi): %s", list(update.keys()))
        return
    text = message.get("text")
    if not text:
        logger.info("Operator xabari matnsiz (o'tkazib yuborildi).")
        return

    reply_to = message.get("reply_to_message")
    conversation_id = None
    if reply_to:
        conversation_id = _PENDING.pop(reply_to["message_id"], None)
        if conversation_id is None:
            logger.warning(
                "Operator 'Reply' bilan yozdi, lekin mos suhbat topilmadi (message_id=%s) — "
                "server qayta ishga tushgan yoki eski xabar bo'lishi mumkin.",
                reply_to.get("message_id"),
            )

    if conversation_id is None:
        # Operator "Reply" qilmasdan oddiy xabar yozgan bo'lishi mumkin —
        # agar hozir faqat BITTA mijoz operatorga ulangan bo'lsa, noaniqlik
        # yo'q, o'shanga yo'naltiramiz.
        if len(_CONNECTED) == 1:
            conversation_id = next(iter(_CONNECTED))
            logger.info(
                "Operator xabari 'Reply'siz keldi, yagona ulangan suhbatga (%s) yo'naltirildi.",
                conversation_id,
            )
        else:
            logger.warning(
                "Operator xabari 'Reply'siz keldi, %d ta ulangan suhbat bor — kimga ekani noaniq.",
                len(_CONNECTED),
            )
            await _send_telegram_message(
                "Hozir bir nechta (yoki hech qanday) ulangan mijoz bor — qaysi mijozga "
                "javob yozayotganingizni bilishim uchun, o'sha mijozning xabariga "
                "Telegram'ning \"Reply\" (javob berish) funksiyasi bilan javob bering."
            )
            return

    await chat_store.add_message(conversation_id, "bot", f"Operator: {text}")
    # 2 daqiqalik taymer aynan shu yerda (birinchi operator javobida)
    # boshlanadi va har keyingi operator javobida qayta yangilanadi.
    _TIMERS[conversation_id] = time.monotonic()
    logger.info("Operator javobi %s suhbatiga qo'shildi.", conversation_id)


async def operator_timeout_loop() -> None:
    """Operator kamida bir marta javob yozgan suhbatlarni davriy tekshiradi:
    mijozdan SESSION_TIMEOUT_SECONDS davomida yangi xabar kelmasa, bot
    suhbatni o'zi yakunlaydi va operator-rejimidan chiqaradi."""
    logger.info("Operator session timeout loop boshlandi (%ss).", SESSION_TIMEOUT_SECONDS)
    while True:
        await asyncio.sleep(20)
        now = time.monotonic()
        expired = [cid for cid, last in _TIMERS.items() if now - last > SESSION_TIMEOUT_SECONDS]
        for conversation_id in expired:
            _TIMERS.pop(conversation_id, None)
            _CONNECTED.discard(conversation_id)
            await chat_store.add_message(conversation_id, "bot", _TIMEOUT_MESSAGE)
            logger.info("Operator sessiyasi %s uchun jim-qolish tufayli yakunlandi.", conversation_id)


async def operator_poll_loop() -> None:
    """Telegram getUpdates'ni uzoq-polling bilan tekshiradi (webhook shart
    emas — poll.py'dagi mavjud yondashuvga mos)."""
    logger.info("Telegram operator poll loop boshlandi.")
    offset = 0
    async with httpx2.AsyncClient(timeout=40) as client:
        while True:
            try:
                response = await client.get(
                    _api_url("getUpdates"),
                    params={"offset": offset, "timeout": 30},
                )
                data = response.json()
                updates = data.get("result", [])
                if updates:
                    logger.info("Telegram'dan %d ta yangilanish keldi.", len(updates))
                for update in updates:
                    offset = update["update_id"] + 1
                    await _handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram operator poll xatosi.")
                await asyncio.sleep(5)
