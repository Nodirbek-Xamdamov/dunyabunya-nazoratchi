"""The sender's answers to the bot's question about a file it could not place (needs_review).

Ways to answer:
* type the document number as a reply to the photo itself (reaction-only mode: there is no question),
  as a reply to the bot's question, or plainly when only one file is waiting;
* press a button under the question: "yes" (attach to the suggested record) or "skip" (not needed).

A decision moves the job to ``ready`` (a worker attaches it) or ``rejected``. Only the sender or an
admin may decide. Kept free of Telegram types so it can be tested directly.
"""

import logging
from dataclasses import dataclass

from docbot.db.models import Job
from docbot.db.repositories.jobs import JobRepository
from docbot.documents import Documents, RecordRef
from docbot.errors import InvalidTransition, LeaseLost
from docbot.odoo import OdooRecord
from docbot.pipeline.finder import RecordFinder
from docbot.pipeline.processor import record_fields
from docbot.pipeline.states import Method, Status

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answer:
    text: str  # what to tell the person (text mode)
    done: bool = False  # the question is settled (remove its buttons)
    ok: bool = False  # the answer was taken (reaction mode: 👌, otherwise 🤷)
    is_number: bool = True  # False: the text is not a document number at all (in a group: just chat, ignore it)


class ReviewService:
    def __init__(self, jobs: JobRepository, finder: RecordFinder, documents: Documents, admin_ids: list[int]):
        self._jobs = jobs
        self._finder = finder
        self._documents = documents
        self._admins = set(admin_ids)

    async def answer_text(
        self, user_id: int, text: str, *, reply_to: int | None, chat_id: int | None = None, plain_fallback: bool = True
    ) -> Answer | None:
        """A text message from ``user_id`` (in ``chat_id``). None: it is not an answer to any waiting file.

        ``plain_fallback``: a message that is not a reply may answer the sender's only waiting file (private
        chat). In a group only replies count, everything else is ordinary conversation.
        """
        job = None
        if reply_to is not None:
            job = await self._jobs.find_review_by_message(user_id, reply_to)
            if job is None and chat_id is not None:
                job = await self._jobs.find_review_by_file_message(chat_id, reply_to)
            if job is not None and job.telegram_user_id != user_id and user_id not in self._admins:
                return Answer("Bu faylga uni yuborgan odam javob beradi.")
        if job is None:
            if not plain_fallback:
                return None
            open_jobs = await self._jobs.list_review_for_user(user_id)
            if not open_jobs:
                return None
            if len(open_jobs) > 1:
                return Answer(f"{len(open_jobs)} ta fayl javob kutyapti. Raqamni kerakli rasmga javob (reply) qilib yozing.")
            job = open_jobs[0]

        refs = self._documents.from_number(text)
        if not refs:
            return Answer(f"«{text.strip()[:40]}» hujjat raqamiga o'xshamaydi. Masalan: {self._documents.examples()}.", is_number=False)
        records = await self._finder.find_all(refs)
        if not records:
            return Answer(f"{refs[0].name} Odoo'da topilmadi. Raqamni tekshirib, qayta yozing.")
        return await self._decide(job, records[0], actor=f"tg:{user_id}")

    async def press(self, user_id: int, job_id: int, action: str) -> Answer:
        """A button under the question: yes | skip."""
        job = await self._jobs.get(job_id)
        if job is None or job.status is not Status.NEEDS_REVIEW:
            return Answer("Bu savol allaqachon hal qilingan.", done=True)
        if user_id != job.telegram_user_id and user_id not in self._admins:
            return Answer("Bu savolga faylni yuborgan odam javob beradi.")
        if action == "skip":
            try:
                await self._jobs.transition(job.id, Status.REJECTED, worker_id=None, actor=f"tg:{user_id}", event="review_rejected")
            except (InvalidTransition, LeaseLost):
                return Answer("Bu savol allaqachon hal qilingan.", done=True)
            return Answer("🗑 O'tkazib yuborildi, fayl hech qayerga biriktirilmaydi.", done=True, ok=True)
        if action == "yes":
            proposal = (job.extracted_data or {}).get("candidate")
            if not proposal:
                return Answer(f"Taklif yo'q. Hujjat raqamini javob qilib yozing (masalan {self._documents.examples()}).")
            kind = self._documents.for_model(proposal["model"])
            record = None
            if kind is not None:
                record = await self._finder.find(RecordRef(kind.key, kind.model, record_id=int(proposal["id"])))
            if record is None:
                return Answer(f"{proposal['name']} endi Odoo'da topilmadi. Raqamni javob qilib yozing.")
            return await self._decide(job, record, actor=f"tg:{user_id}")
        return Answer(f"Hujjat raqamini shu savolga javob qilib yozing (masalan {self._documents.examples()}).")

    async def _decide(self, job: Job, record: OdooRecord, *, actor: str) -> Answer:
        title = self._documents.title(record.model)
        try:
            await self._jobs.transition(
                job.id, Status.READY, worker_id=None, actor=actor, event="review_answered", method=Method.HUMAN, **record_fields(record)
            )
        except (InvalidTransition, LeaseLost):
            return Answer("Bu savol allaqachon hal qilingan.", done=True)
        log.info("job %s: %s chose %s %s", job.id, actor, record.model, record.name)
        return Answer(f"👌 {title} {record.name} ga biriktiriladi.", done=True, ok=True)
