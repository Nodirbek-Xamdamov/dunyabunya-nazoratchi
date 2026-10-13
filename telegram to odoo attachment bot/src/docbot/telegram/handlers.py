"""Telegram handlers. They are quick: queue every picture/PDF (one job per file) and return; workers do the rest.

Served chats: private chats plus ALLOWED_CHAT_IDS and groups an admin added the bot to (``ChatRegistry``).
In a group the bot must be an admin to see every photo. Every picture is queued, whoever sent it: the
worker decides whether it is one of our documents; anything else is dropped without a word. The bot
writes only "welcome" on /start and "send a clearer photo" for a document it could not place.
Videos, GIF animations, video notes and stickers are never downloaded or analysed (the user's rule).
Messages sent on behalf of a channel/anonymous admin (``sender_chat``) are queued like any other.
"""

import contextlib
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

from docbot.config import AppConfig
from docbot.db.repositories.jobs import JobRepository, NewJob
from docbot.pipeline.review import ReviewService
from docbot.pipeline.states import Status
from docbot.telegram.chats import ChatRegistry

log = logging.getLogger(__name__)

WELCOME = "Assalomu alaykum, xush kelibsiz!"
# Still pictures and PDFs only. GIFs (image/gif) are animations; videos are never analysed.
_DOCUMENT_MIMES = frozenset({"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"})
_DOCUMENT_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
_PRESENT = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.RESTRICTED}


def sender_name(message: Message) -> str | None:
    user = message.from_user
    if message.sender_chat is not None:
        return message.sender_chat.title
    if user is None:
        return None
    return f"{user.full_name} (@{user.username})" if user.username else user.full_name


def accepts_document(mime: str | None, file_name: str | None) -> bool:
    if mime and mime.lower() in _DOCUMENT_MIMES:
        return True
    if mime and mime.lower() not in ("application/octet-stream", "binary/octet-stream"):
        return False  # a declared type that is not ours (video/*, image/gif, zip, ...)
    return bool(file_name) and file_name.lower().endswith(_DOCUMENT_SUFFIXES)


def membership_change(old: str, new: str) -> str | None:
    """What happened to the bot in a chat: added | promoted | demoted | removed | None."""
    if old not in _PRESENT and new in _PRESENT:
        return "added"
    if old in _PRESENT and new not in _PRESENT:
        return "removed"
    if old != ChatMemberStatus.ADMINISTRATOR and new == ChatMemberStatus.ADMINISTRATOR:
        return "promoted"
    if old == ChatMemberStatus.ADMINISTRATOR and new in _PRESENT:
        return "demoted"
    return None


async def _tell_admins(bot: Bot, admins: set[int], text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    for admin in admins:
        with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
            await bot.send_message(admin, text, reply_markup=markup)


def build_router(cfg: AppConfig, chats: ChatRegistry) -> Router:
    root = Router(name="docbot")
    admins = set(cfg.env.admin_ids)
    max_bytes = int(cfg.settings.files.max_mb * 1024 * 1024)
    texts = cfg.settings.notify.messages  # False (default): answers by text/buttons are switched off

    # ------------------------------------------------------------------ the bot joins / leaves groups (any chat)

    @root.my_chat_member()
    async def on_membership(event: ChatMemberUpdated, bot: Bot) -> None:
        if event.chat.type == ChatType.PRIVATE:
            return
        change = membership_change(event.old_chat_member.status, event.new_chat_member.status)
        title, chat_id, who = event.chat.title or str(event.chat.id), event.chat.id, event.from_user
        is_admin_now = event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR
        hint = "" if is_admin_now else "\nBot barcha rasmlarni ko'rishi uchun uni guruhda admin qiling."
        if change in ("added", "promoted"):
            if chats.allowed(chat_id):
                if change == "promoted":
                    await _tell_admins(bot, admins, f"✅ Bot «{title}» guruhida admin: endi barcha rasmlarni ko'radi.")
                return
            if who is not None and who.id in admins:
                chats.add(chat_id, title=title, added_by=who.id)
                await _tell_admins(bot, admins, f"✅ Bot «{title}» guruhiga qo'shildi va ishlayapti (id {chat_id}).{hint}")
            else:
                name = who.full_name if who else "noma'lum"
                button = InlineKeyboardButton(text="✅ Ruxsat berish", callback_data=f"chat:allow:{chat_id}")
                await _tell_admins(
                    bot, admins,
                    f"⚠️ Bot «{title}» guruhiga {name} tomonidan qo'shildi (id {chat_id}). Bu guruhdagi hujjatlar biriktirilsinmi?",
                    InlineKeyboardMarkup(inline_keyboard=[[button]]),
                )
        elif change == "removed" and chats.remove(chat_id):
            await _tell_admins(bot, admins, f"Bot «{title}» guruhidan chiqarildi (id {chat_id}).")

    @root.callback_query(F.data.startswith("chat:allow:"))
    async def on_allow_chat(callback: CallbackQuery, bot: Bot) -> None:
        if callback.from_user.id not in admins:
            await callback.answer("Faqat admin ruxsat beradi.")
            return
        chat_id = int(callback.data.rsplit(":", 1)[1])
        title = None
        with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
            title = (await bot.get_chat(chat_id)).title  # for `docbot groups list`
        chats.add(chat_id, title=title, added_by=callback.from_user.id)
        await callback.answer("Ruxsat berildi.")
        if isinstance(callback.message, Message):
            with contextlib.suppress(TelegramBadRequest):
                await callback.message.edit_text(f"{callback.message.text}\n\n✅ Ruxsat berildi.", reply_markup=None)

    @root.message(
        Command("start", "ulash"),
        F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
        lambda m: not chats.allowed(m.chat.id),
    )
    async def on_connect_group(message: Message) -> None:
        """Fallback when the "bot added" event was missed: an admin types /start in the group."""
        if message.from_user is None or message.from_user.id not in admins:
            return
        chats.add(message.chat.id, title=message.chat.title, added_by=message.from_user.id)
        await message.answer(WELCOME)

    @root.message(Command("pauza"), F.chat.type == ChatType.PRIVATE)
    async def on_pause(message: Message) -> None:
        if message.from_user is None or message.from_user.id not in admins:
            return
        names = chats.pause_all()
        await message.answer("⏸ Guruhlar to'xtatildi: " + (", ".join(names) or "yo'q") + ". Shaxsiy chatdagi fayllar ishlanaveradi.")

    @root.message(Command("tayyor"), F.chat.type == ChatType.PRIVATE)
    async def on_live(message: Message) -> None:
        if message.from_user is None or message.from_user.id not in admins:
            return
        names = chats.go_live()
        await message.answer("▶️ Guruhlar ishga tushdi: " + (", ".join(names) or "yo'q") + ". Faqat hozirdan keyingi xabarlar olinadi.")

    @root.message(F.migrate_to_chat_id)
    async def on_migrate_to(message: Message) -> None:
        chats.migrate(message.chat.id, message.migrate_to_chat_id)

    @root.message(F.migrate_from_chat_id)
    async def on_migrate_from(message: Message) -> None:
        chats.migrate(message.migrate_from_chat_id, message.chat.id)

    # ------------------------------------------------------------------ served chats

    router = Router(name="served")

    def served(chat) -> bool:
        return chat.type == ChatType.PRIVATE or chats.live(chat.id)  # a paused group is not touched at all

    router.message.filter(lambda m: served(m.chat))
    router.callback_query.filter(lambda c: c.message is not None and served(c.message.chat))
    root.include_router(router)

    # Last: a group message nobody took is logged with its chat, so "not served" can be told from "never
    # delivered" (Telegram does not hand a bot the messages of other bots, for example).
    rest = Router(name="rest")
    root.include_router(rest)

    @rest.message(F.chat.type != ChatType.PRIVATE)
    async def on_not_taken(message: Message) -> None:
        by_bot = " (yuboruvchi bot)" if message.from_user is not None and message.from_user.is_bot else ""
        log.info("message %s in chat %s (%s) not taken: %s%s, served=%s",
                 message.message_id, message.chat.id, message.chat.title, message.content_type, by_bot, served(message.chat))

    async def enqueue(message: Message, jobs: JobRepository, update: Update | None, **file) -> None:
        if not chats.is_new(message.chat.id, message.date):
            log.info("message %s in chat %s predates the connection of the group: skipped", message.message_id, message.chat.id)
            return
        user = message.from_user
        await jobs.enqueue(
            NewJob(
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id if message.is_topic_message else None,
                media_group_id=message.media_group_id,
                telegram_user_id=user.id if user else message.chat.id,
                sender_name=sender_name(message),
                update_id=update.update_id if update else None,
                **file,
            )
        )

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(WELCOME)

    @router.message(Command("status"))
    async def on_status(message: Message, jobs: JobRepository) -> None:
        if message.from_user is None or message.from_user.id not in admins:
            return
        counts = await jobs.counts_by_status()
        lines = [f"{status.value}: {counts.get(status, 0)}" for status in Status if counts.get(status)]
        await message.answer("Navbat holati:\n" + ("\n".join(lines) or "bo'sh"))

    @router.message(F.photo)
    async def on_photo(message: Message, jobs: JobRepository, event_update: Update | None = None) -> None:
        photo = message.photo[-1]  # the largest size Telegram kept
        await enqueue(
            message, jobs, event_update,
            file_id=photo.file_id, file_unique_id=photo.file_unique_id, mime_type="image/jpeg", file_size=photo.file_size,
        )

    @router.message(F.document)
    async def on_document(message: Message, jobs: JobRepository, event_update: Update | None = None) -> None:
        doc = message.document
        if message.animation is not None or message.video is not None or message.video_note is not None:
            return  # Telegram also fills `document` for GIF animations
        if not accepts_document(doc.mime_type, doc.file_name):
            return  # not a picture or PDF: none of our business
        if doc.file_size and doc.file_size > max_bytes:
            log.warning("message %s in chat %s: %s is too big for a bot to download (%s bytes)",
                        message.message_id, message.chat.id, doc.file_name, doc.file_size)
            return
        await enqueue(
            message, jobs, event_update,
            file_id=doc.file_id, file_unique_id=doc.file_unique_id, mime_type=doc.mime_type,
            file_size=doc.file_size, file_name=doc.file_name,
        )

    if texts:  # optional text mode (notify.messages: true): answers to the bot's questions

        @router.message(F.text, F.from_user.is_not(None))
        async def on_text(message: Message, review: ReviewService) -> None:
            private = message.chat.type == ChatType.PRIVATE
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            if not private and reply_to is None:
                return  # ordinary group chat
            answer = await review.answer_text(
                message.from_user.id, message.text, reply_to=reply_to, chat_id=message.chat.id, plain_fallback=private
            )
            if answer is not None and (private or answer.is_number):
                await message.reply(answer.text)

        @router.callback_query(F.data.startswith("rv:"))
        async def on_button(callback: CallbackQuery, review: ReviewService) -> None:
            try:
                _, job_id, action = callback.data.split(":", 2)
                answer = await review.press(callback.from_user.id, int(job_id), action)
            except ValueError:
                await callback.answer()
                return
            await callback.answer(answer.text[:190])
            if answer.done and isinstance(callback.message, Message):
                with contextlib.suppress(TelegramBadRequest):
                    await callback.message.edit_text(f"{callback.message.text}\n\n{answer.text}", reply_markup=None)

    return root
