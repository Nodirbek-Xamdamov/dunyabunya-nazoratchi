"""Telling the sender what happened to a file. Reads the jobs outbox (``notify_pending``) and sends.

* attached      👍 on the file's message; the last page of a document with a page not sent also gets a
                reply naming it (``notify.missing_pages_text``)
* duplicate     👌: the same file is already in that record
* needs_review  a reply to the file: "send a clearer photo" (``notify.unclear_text``), also in groups
* failed        👎; admins other than the sender get a message

Otherwise the bot only reacts (``notify.messages: false``: the user asked for reactions only). With
messages on, duplicate/failure get a short text and needs_review a question with buttons. The id of the
reply to a waiting file is saved, so answering it with a document number finds the job. Replies go to the
file's own chat as a reply to it. Undeliverable messages stay pending and are retried.
"""

import asyncio
import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, ReplyParameters

from docbot.db.models import Job
from docbot.db.repositories.jobs import JobRepository
from docbot.documents import Documents
from docbot.pipeline.states import Status
from docbot.telegram.chats import ChatRegistry

log = logging.getLogger(__name__)


def review_keyboard(job: Job) -> InlineKeyboardMarkup:
    rows = []
    proposal = (job.extracted_data or {}).get("candidate")
    if proposal:
        rows.append([InlineKeyboardButton(text=f"✅ Ha, {proposal['name']}", callback_data=f"rv:{job.id}:yes")])
    rows.append([InlineKeyboardButton(text="🗑 Kerak emas", callback_data=f"rv:{job.id}:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class Notifier:
    def __init__(
        self,
        bot: Bot,
        jobs: JobRepository,
        documents: Documents,
        *,
        admin_ids: list[int],
        chats: "ChatRegistry | None" = None,
        messages: bool = False,
        success_message: bool = False,
        unclear_text: str = "",
        missing_pages_text: str = "",
        interval: float = 5.0,
    ):
        self._bot = bot
        self._jobs = jobs
        self._documents = documents
        self._admins = list(admin_ids)
        self._chats = chats
        self._messages = messages
        self._success_message = success_message
        self._unclear_text = unclear_text
        self._missing_pages_text = missing_pages_text
        self._interval = interval
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._wake.clear()
            try:
                await self.flush()
            except Exception:
                log.exception("notifier: unexpected error")
            waiters = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(self._wake.wait())]
            try:
                await asyncio.wait(waiters, timeout=self._interval, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()

    async def flush(self) -> int:
        """Send everything pending. Returns how many jobs were settled."""
        settled = 0
        for job in await self._jobs.list_notify_pending():
            if self._chats is not None and self._chats.paused(job.chat_id):
                continue  # the group is paused: nothing is written there; told once it is live again
            try:
                question_id = await self._deliver(job)
            except TelegramRetryAfter as exc:
                log.warning("notifier: Telegram asks to wait %ss", exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                return settled
            except (TelegramNetworkError, TelegramServerError) as exc:
                log.warning("notifier: Telegram unavailable, will retry: %s", exc)
                return settled
            except TelegramForbiddenError as exc:
                log.warning("job %s: cannot write to user %s (%s)", job.id, job.telegram_user_id, exc.message)
                question_id = None
            await self._jobs.mark_notified(job.id, job.status, review_message_id=question_id)
            settled += 1
        return settled

    # ------------------------------------------------------------------ one job

    async def _react(self, job: Job, emoji: str) -> None:
        with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
            await self._bot.set_message_reaction(job.chat_id, job.message_id, reaction=[ReactionTypeEmoji(emoji=emoji)])

    async def _tell(self, job: Job, text: str, markup: InlineKeyboardMarkup | None = None) -> int:
        """A reply to the file's message in its own chat (a private chat or the group it was posted in), in its topic."""
        reply = ReplyParameters(message_id=job.message_id, allow_sending_without_reply=True)
        message = await self._bot.send_message(
            job.chat_id, text, message_thread_id=job.message_thread_id, reply_parameters=reply, reply_markup=markup
        )
        return message.message_id

    async def _deliver(self, job: Job) -> int | None:
        title = self._documents.title(job.odoo_model)
        if job.status is Status.ATTACHED:
            await self._react(job, "👍")
            if job.review_message_id:  # an earlier "send a clearer photo" is obsolete now: remove it
                with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
                    await self._bot.delete_message(job.chat_id, job.review_message_id)
            if self._messages and self._success_message:
                await self._tell(job, f"✅ {title} {job.odoo_record_name} ga biriktirildi.")
            missing = (job.extracted_data or {}).get("missing_pages")
            if missing and self._missing_pages_text:
                await self._tell(job, self._missing_pages_text.format(document=job.odoo_record_name, missing=missing))
            return None
        if job.status is Status.DUPLICATE:
            await self._react(job, "👌")
            if self._messages:
                await self._tell(job, f"ℹ️ Bu fayl {title} {job.odoo_record_name} da allaqachon bor, qayta biriktirilmadi.")
            return None
        if job.status is Status.NEEDS_REVIEW:
            if job.review_message_id:
                return None  # asked already (a re-run that ended the same way): never twice
            if (job.extracted_data or {}).get("quiet") or await self._jobs.album_asked(job):
                return None  # a lone middle page, or an album that got its one reply already
            if self._messages:
                await self._react(job, "🤔")
                return await self._tell(job, self._question(job), review_keyboard(job))
            return await self._tell(job, self._unclear_text) if self._unclear_text else None
        if job.status is Status.FAILED:
            await self._react(job, "👎")
            if self._messages:
                await self._tell(job, f"⚠️ Faylni biriktirib bo'lmadi: {job.error or 'noma`lum xato'}.\nAdmin xabardor qilindi.")
            for admin in self._admins:
                if admin != job.telegram_user_id:
                    with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
                        await self._bot.send_message(admin, f"⚠️ Job {job.id} ({job.sender_name or job.telegram_user_id}) failed: {job.error}")
            return None
        return None

    def _question(self, job: Job) -> str:
        reason = job.error or "hujjat aniqlanmadi"
        proposal = (job.extracted_data or {}).get("candidate")
        if proposal:
            title = self._documents.title(proposal["model"])
            return (
                f"🤔 Bu {title} {proposal['name']} ga o'xshaydi, lekin ishonch yetarli emas ({reason}).\n"
                f"Shunga biriktiraymi? Boshqa hujjat bo'lsa, raqamini shu xabarga javob qilib yozing."
            )
        return (
            f"🤔 Hujjat aniqlanmadi ({reason}).\n"
            f"Hujjat raqamini shu xabarga javob qilib yozing, masalan: {self._documents.examples()}."
        )
