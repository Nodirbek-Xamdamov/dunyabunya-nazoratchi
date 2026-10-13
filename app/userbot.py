"""
Telethon (Premium) Userbot.

Vazifasi: Botga hali start bosmagan mijozlarga rasmiy botga kirish uchun taklif yuboradi.
"""

import asyncio
import logging

from sqlalchemy import select
from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    UserPrivacyRestrictedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityTextUrl

from app.config import settings
from app.database import async_session_maker, init_db
from app.models import GroupMembership, Order, OrderStatus, TelegramUser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("telethon_userbot")

client = TelegramClient(
    settings.TELETHON_SESSION_NAME,
    settings.TELEGRAM_API_ID,
    settings.TELEGRAM_API_HASH,
)

# Premium akkauntda tanlangan animatsiyali (custom) emojilarning document_id'lari.
# Bular foydalanuvchining o'z premium akkauntidan "Saqlangan xabarlar"ga yuborgan
# namunaviy xabaridan (Saved Messages) olingan — shu emojilar qimirlab ko'rinadi.
CUSTOM_EMOJI_IDS = {
    "👋": 5334607672375254726,
    "🛒": 5312361253610475399,
    "🤖": 5355051922862653659,
    "✅": 5265110183788423066,
    "🤝": 5372957680174384345,
    "👇": 5231102735817918643,
    "🟣": 5334936074164643886,
    "✈": 5334709531819648870,
    "🎞": 5332387599484990636,
}

MESSAGE_TEMPLATE = """👋 Assalomu alaykum, hurmatli mijoz!

Men — "dunyabunya" qurilish-savdo markazining rasmiy assistentiman.

🛒 Buyurtmangiz bo'yicha narxlar, mahsulot mavjudligi, yetkazib berish, shuningdek buyurtmani tasdiqlash va tahrirlash bo'yicha tezkor ma'lumot olish uchun rasmiy yordamchi botimizni ishga tushiring.

🤖 Rasmiy botimizga kirib, "START" tugmasini bosing.

Bot orqali murojaatingiz bizga tezroq yetib boradi va sizga kerakli ma'lumotlar qisqa fursatda taqdim etiladi. ✅

🤝 Hamkorligingiz uchun rahmat!

👇 Davom etish uchun pastdagi havolani bosing

🤖 Botga start bosish

🟣Instagram   ✈️Telegram   🎞Youtube"""


def build_formatted_message(text: str, bot_username: str):
    """
    Matndagi har bir animatsiyali emoji belgisi va havola so'zlari uchun MTProto
    entity'larini (MessageEntityCustomEmoji / MessageEntityTextUrl) hisoblab chiqadi.
    Offset/length UTF-16 kod birliklarida bo'lishi kerak (Telegram talabi), shu sabab
    qidiruv matnni UTF-16-LE bayt ko'rinishida amalga oshiriladi.
    """
    utf16 = text.encode("utf-16-le")
    entities = []

    for char, doc_id in CUSTOM_EMOJI_IDS.items():
        needle = char.encode("utf-16-le")
        pos = 0
        while True:
            idx = utf16.find(needle, pos)
            if idx == -1:
                break
            entities.append(MessageEntityCustomEmoji(offset=idx // 2, length=len(needle) // 2, document_id=doc_id))
            pos = idx + len(needle)

    links = {
        "Botga start bosish": f"https://t.me/{bot_username}",
        "Instagram": "https://www.instagram.com/dunyabunya.uz/",
        "Telegram": "https://t.me/dunyabunya",
        "Youtube": "https://www.youtube.com/@dunyabunyauz",
    }
    for phrase, url in links.items():
        needle = phrase.encode("utf-16-le")
        idx = utf16.find(needle)
        if idx != -1:
            entities.append(MessageEntityTextUrl(offset=idx // 2, length=len(needle) // 2, url=url))

    return entities


AUTO_REPLY_TEXT = (
    "Assalomu alaykum! Ushbu akkaunt xabarlarga javob qaytarmaydi.\n\n"
    "🆘 Yordam kerak bo'lsa, +998 78 113 09 90 raqamiga qo'ng'iroq qiling yoki quyidagi "
    "akkauntga yozing:\n"
    "👉 @dunyabunya_support\n\n"
    "🛒 Biror narsa buyurtma qilmoqchi bo'lsangiz, rasmiy botimiz orqali berishingiz mumkin:\n"
    f"👉 https://t.me/{settings.BOT_USERNAME.lstrip('@')}"
)


@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def handle_incoming_private_message(event) -> None:
    """
    Mijoz premium akkauntga to'g'ridan-to'g'ri yozganda avtomatik javob beradi (bu
    akkaunt hech qaerga xabar uzatmaydi -- foydalanuvchiga yordam uchun
    @dunyabunya_support'ga, buyurtma uchun esa rasmiy botga murojaat qilish tavsiya
    etiladi). Boshqa botlardan (masalan @stickers) kelgan xabarlarga javob
    berilmaydi -- aks holda ikkala tomon ham avtomatik javob berib, cheksiz tsiklga
    (infinite loop) kirib qoladi.
    """
    try:
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return

        await event.respond(AUTO_REPLY_TEXT)
    except Exception:
        logger.exception("Avtomatik javob yuborishda xatolik (chat_id=%s)", event.chat_id)


async def notify_pending_orders() -> None:
    """Hali xabar berilmagan va botga start berilmagan 'pending' buyurtmalarga Userbot orqali yozadi."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Order).where(
                Order.notified.is_(False), 
                Order.status == OrderStatus.PENDING,
                Order.chat_id.is_(None)  # Chat ID yo'q bo'lsa (ya'ni botga start bermagan bo'lsa) Userbot yozadi
            )
        )
        orders = result.scalars().all()

        for order in orders:
            try:
                bot_user = settings.BOT_USERNAME.replace("@", "")
                entities = build_formatted_message(MESSAGE_TEMPLATE, bot_user)

                await client.send_message(
                    f"@{order.normalized_username}",
                    MESSAGE_TEMPLATE,
                    formatting_entities=entities,
                )
                order.notified = True
                logger.info("Userbot orqali xabar yuborildi: @%s (order_id=%s)", order.normalized_username, order.id)

            except (UsernameNotOccupiedError, UsernameInvalidError):
                logger.warning(
                    "Username Telegram'da topilmadi, o'chirilgan yoki noto'g'ri: @%s",
                    order.normalized_username,
                )
            except UserPrivacyRestrictedError:
                logger.warning(
                    "Foydalanuvchi maxfiylik sozlamalari tufayli xabar qabul qila olmaydi: @%s",
                    order.normalized_username,
                )
            except FloodWaitError as exc:
                logger.warning("Telegram FloodWait: %s soniya kutish talab qilinmoqda", exc.seconds)
                await asyncio.sleep(exc.seconds)
            except Exception:
                logger.exception(
                    "Xabar yuborishda kutilmagan xatolik: @%s (order_id=%s)",
                    order.normalized_username,
                    order.id,
                )

        await session.commit()


async def poll_loop() -> None:
    while True:
        try:
            await notify_pending_orders()
        except Exception:
            logger.exception("Userbot poll tsiklida xatolik yuz berdi")
        await asyncio.sleep(settings.USERBOT_POLL_INTERVAL_SECONDS)


async def sync_group_memberships() -> None:
    """
    Userbot admin sifatida a'zo bo'lgan guruhlarni skanerlab, botga /start bosgan
    (bizga `telegram_users` jadvalida ma'lum bo'lgan) foydalanuvchilarning qaysi
    guruh(lar)da ekanini aniqlaydi va `group_memberships`ni yangilaydi.
    """
    async with async_session_maker() as session:
        known_chat_ids = set((await session.execute(select(TelegramUser.chat_id))).scalars().all())

    if not known_chat_ids:
        return

    async for dialog in client.iter_dialogs():
        if not dialog.is_group:
            continue

        group_id = dialog.id

        try:
            perms = await client.get_permissions(dialog.entity, "me")
            if not perms.is_admin:
                continue
            participants = await client.get_participants(dialog.entity)
        except FloodWaitError as exc:
            logger.warning("Guruh a'zolarini olishda FloodWait: %s soniya (guruh_id=%s)", exc.seconds, group_id)
            await asyncio.sleep(exc.seconds)
            continue
        except Exception:
            logger.exception("Guruh a'zolarini olishda xatolik (guruh_id=%s)", group_id)
            continue

        matched_chat_ids = {p.id for p in participants if not p.bot} & known_chat_ids

        async with async_session_maker() as session:
            existing = (
                await session.execute(select(GroupMembership).where(GroupMembership.group_id == group_id))
            ).scalars().all()
            existing_by_chat_id = {m.chat_id: m for m in existing}

            for chat_id in matched_chat_ids - existing_by_chat_id.keys():
                session.add(GroupMembership(chat_id=chat_id, group_id=group_id, group_title=dialog.title))

            for chat_id in existing_by_chat_id.keys() - matched_chat_ids:
                await session.delete(existing_by_chat_id[chat_id])

            await session.commit()

        if matched_chat_ids:
            logger.info(
                "Guruh '%s' (id=%s) uchun %s ta tanish a'zo topildi",
                dialog.title, group_id, len(matched_chat_ids),
            )


async def group_sync_loop() -> None:
    while True:
        try:
            await sync_group_memberships()
        except Exception:
            logger.exception("Guruh a'zoligini sinxronlash tsiklida xatolik yuz berdi")
        await asyncio.sleep(settings.GROUP_SYNC_INTERVAL_SECONDS)


async def main() -> None:
    await init_db()
    await client.start()
    logger.info("Telethon Userbot ishga tushdi va bazani kuzatmoqda.")
    await asyncio.gather(poll_loop(), group_sync_loop())


if __name__ == "__main__":
    asyncio.run(main())
