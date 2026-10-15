"""The jobs table as a persistent work queue.

Queue model
-----------
* ``enqueue`` inserts a job in ``received``; the same Telegram message+file can never create a second one.
* ``claim`` hands the oldest *due* job in a claimable status to a worker and locks it for
  ``lease_seconds``. A worker that dies simply stops renewing: once the lease expires the job is
  claimable again (that counts as a failed attempt, so a job that kills its worker cannot loop forever).
* ``transition`` / ``update_fields`` / ``retry_later`` are *fenced*: they only apply while the caller
  still holds the lease, otherwise ``LeaseLost`` is raised and the stale worker's result is dropped.
* ``retry_later`` schedules the next attempt with exponential backoff; after ``max_attempts``
  consecutive failures the job becomes ``failed``. A successful transition resets the attempt counter.
* Entering a status in ``NOTIFY_ON`` sets ``notify_pending``: the notifier (an outbox reader) tells the
  sender and clears it with ``mark_notified``. A Telegram outage therefore delays a message, never loses it.

Every state change writes an audit row in the same transaction. Transactions are short and never span
network I/O (on SQLite each one holds the write lock, see ``db.session``).
"""

import logging
import random
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docbot.clock import Clock, utcnow
from docbot.config import QueueSettings
from docbot.db.models import Job
from docbot.db.repositories.audit import audit_entry, clean_error
from docbot.db.session import SessionFactory
from docbot.errors import InvalidTransition, LeaseLost
from docbot.pipeline.states import CLAIMABLE, KEY_RELEASING, NOTIFY_ON, Status, can_transition

log = logging.getLogger(__name__)

# How many exhausted jobs one claim() call may retire before giving up and returning None.
_CLAIM_SCAN_LIMIT = 20

# Columns a processing step may write. Status/queue columns change only through the methods below.
UPDATABLE_FIELDS = frozenset(
    {
        "method",
        "qr_value",
        "doc_key",
        "extracted_data",
        "odoo_model",
        "odoo_record_id",
        "odoo_record_name",
        "path",
        "mime_type",
        "file_size",
    }
)

# Finished jobs whose local file copy is no longer needed (Odoo has it, or nobody wants it).
FILE_DISPOSABLE = frozenset({Status.ATTACHED, Status.DUPLICATE, Status.REJECTED, Status.IGNORED})


@dataclass(frozen=True)
class NewJob:
    chat_id: int
    message_id: int
    telegram_user_id: int
    file_id: str
    file_unique_id: str
    update_id: int | None = None
    message_thread_id: int | None = None
    sender_name: str | None = None
    path: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    file_name: str | None = None
    media_group_id: str | None = None


@dataclass(frozen=True)
class EnqueueResult:
    job: Job
    created: bool  # False: this message+file was already queued (redelivered update)


@dataclass(frozen=True)
class DocKeyResult:
    ok: bool
    holder: Job | None = None  # the job that already owns the key


def backoff_seconds(attempts: int, settings: QueueSettings, rand: Callable[[], float] = random.random) -> float:
    """Delay before the next attempt, ``attempts`` = consecutive failures so far (>= 1)."""
    delay = min(settings.backoff_max_seconds, settings.backoff_base_seconds * 2 ** min(attempts - 1, 30))
    if settings.backoff_jitter:
        delay *= 1 + settings.backoff_jitter * (2 * rand() - 1)
    return delay


def _require_owner(job: Job, worker_id: str | None, now: datetime) -> None:
    if worker_id is not None:
        if job.locked_by != worker_id:
            raise LeaseLost(f"job {job.id} is not held by {worker_id}")
    elif job.locked_by is not None and job.locked_until is not None and job.locked_until > now:
        raise LeaseLost(f"job {job.id} is locked by {job.locked_by}")


def _check_fields(fields: dict[str, Any]) -> None:
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"fields not updatable through the queue: {sorted(unknown)}")


class JobRepository:
    def __init__(
        self,
        session_factory: SessionFactory,
        queue: QueueSettings,
        *,
        clock: Clock = utcnow,
        rand: Callable[[], float] = random.random,
    ):
        self._sf = session_factory
        self._queue = queue
        self._clock = clock
        self._rand = rand

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[AsyncSession]:
        async with self._sf() as session, session.begin():
            yield session

    @staticmethod
    async def _load(session: AsyncSession, job_id: int) -> Job:
        job = await session.get(Job, job_id, with_for_update=True)
        if job is None:
            raise LookupError(f"job {job_id} does not exist")
        return job

    # ------------------------------------------------------------------ intake

    async def enqueue(self, new: NewJob) -> EnqueueResult:
        now = self._clock()
        try:
            async with self._tx() as session:
                existing = await self._find_message_file(session, new)
                if existing is not None:
                    return EnqueueResult(existing, created=False)
                job = Job(
                    chat_id=new.chat_id,
                    message_id=new.message_id,
                    message_thread_id=new.message_thread_id,
                    update_id=new.update_id,
                    telegram_user_id=new.telegram_user_id,
                    sender_name=new.sender_name,
                    file_id=new.file_id,
                    file_unique_id=new.file_unique_id,
                    path=new.path,
                    mime_type=new.mime_type,
                    file_size=new.file_size,
                    file_name=new.file_name,
                    media_group_id=new.media_group_id,
                    status=Status.RECEIVED,
                    attempts=0,
                    notify_pending=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
                await session.flush()
                session.add(
                    audit_entry(
                        now,
                        job_id=job.id,
                        actor=f"tg:{new.telegram_user_id}",
                        event="received",
                        to_status=Status.RECEIVED,
                        details={"chat_id": new.chat_id, "message_id": new.message_id, "update_id": new.update_id},
                    )
                )
        except IntegrityError:
            # Another writer inserted the same message+file between our check and insert (PostgreSQL).
            async with self._sf() as session:
                existing = await self._find_message_file(session, new)
            if existing is None:
                raise
            return EnqueueResult(existing, created=False)
        log.info("job %s queued (chat %s, message %s)", job.id, new.chat_id, new.message_id)
        return EnqueueResult(job, created=True)

    @staticmethod
    async def _find_message_file(session: AsyncSession, new: NewJob) -> Job | None:
        result = await session.execute(
            select(Job).where(
                Job.chat_id == new.chat_id,
                Job.message_id == new.message_id,
                Job.file_unique_id == new.file_unique_id,
            )
        )
        return result.scalars().first()

    # ------------------------------------------------------------------ worker side

    async def claim(self, worker_id: str) -> Job | None:
        """Lock and return the oldest due job, or None if there is nothing to do."""
        now = self._clock()
        due = (
            select(Job)
            .where(
                Job.status.in_(sorted(CLAIMABLE)),
                or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
                or_(Job.locked_until.is_(None), Job.locked_until <= now),
            )
            .order_by(Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        async with self._tx() as session:
            for _ in range(_CLAIM_SCAN_LIMIT):
                job = (await session.execute(due)).scalars().first()
                if job is None:
                    return None
                if job.locked_until is not None:
                    # A previous holder never finished (crash, hang, restart): that was a failed attempt.
                    previous = job.locked_by
                    job.attempts += 1
                    job.error = clean_error(f"worker {previous} did not finish the job (lease expired)")
                    exhausted = job.attempts >= self._queue.max_attempts
                    session.add(
                        audit_entry(
                            now,
                            job_id=job.id,
                            actor="system",
                            event="lease_expired",
                            from_status=job.status,
                            to_status=Status.FAILED if exhausted else job.status,
                            method=job.method,
                            result="error",
                            error=job.error,
                            details={"previous_worker": previous, "attempts": job.attempts},
                        )
                    )
                    if exhausted:
                        log.error("job %s failed: lease expired %s times", job.id, job.attempts)
                        job.status = Status.FAILED
                        job.notify_pending = True
                        job.locked_by = None
                        job.locked_until = None
                        job.next_attempt_at = None
                        job.updated_at = now
                        await session.flush()
                        continue
                    log.warning("job %s recovered from expired lease of %s", job.id, previous)
                job.locked_by = worker_id
                job.locked_until = now + timedelta(seconds=self._queue.lease_seconds)
                job.updated_at = now
                return job
            return None

    async def transition(
        self,
        job_id: int,
        to: Status,
        *,
        worker_id: str | None,
        actor: str,
        event: str = "status_changed",
        error: str | None = None,
        **fields: Any,
    ) -> Job:
        """Move a job to another status, optionally writing fields, and release its lock.

        ``worker_id`` must be the current lease holder. ``None`` is for human/admin actions on a job
        nobody is working on (e.g. a review decision on ``needs_review``).
        ``error`` is the reason for the new status (failure, review reason); it is cleared otherwise.
        """
        _check_fields(fields)
        now = self._clock()
        async with self._tx() as session:
            job = await self._load(session, job_id)
            _require_owner(job, worker_id, now)
            if not can_transition(job.status, to):
                raise InvalidTransition(job.status.value, to.value)
            src = job.status
            job.status = to
            for name, value in fields.items():
                setattr(job, name, value)
            if error is not None or to is not Status.FAILED:
                job.error = clean_error(error)
            if to is not Status.FAILED:
                job.attempts = 0
            job.notify_pending = to in NOTIFY_ON
            job.next_attempt_at = None
            job.locked_by = None
            job.locked_until = None
            job.updated_at = now
            session.add(
                audit_entry(
                    now,
                    job_id=job.id,
                    actor=actor,
                    event=event,
                    from_status=src,
                    to_status=to,
                    method=job.method,
                    result="error" if to is Status.FAILED else "ok",
                    error=job.error if (to is Status.FAILED or error is not None) else None,
                )
            )
        log.info("job %s: %s -> %s", job.id, src.value, to.value)
        return job

    async def update_fields(self, job_id: int, *, worker_id: str, actor: str, **fields: Any) -> Job:
        """Persist data mid-step without changing status (e.g. odoo_record_id right after create). Renews the lease."""
        _check_fields(fields)
        now = self._clock()
        async with self._tx() as session:
            job = await self._load(session, job_id)
            _require_owner(job, worker_id, now)
            for name, value in fields.items():
                setattr(job, name, value)
            job.locked_until = now + timedelta(seconds=self._queue.lease_seconds)
            job.updated_at = now
            session.add(
                audit_entry(
                    now,
                    job_id=job.id,
                    actor=actor,
                    event="fields_updated",
                    method=job.method,
                    details={"fields": sorted(fields)},
                )
            )
        return job

    async def retry_later(
        self,
        job_id: int,
        *,
        worker_id: str,
        actor: str,
        error: str,
        retry_after: float | None = None,
        counts: bool = True,
    ) -> Job:
        """Record a failed attempt. Reschedules with backoff, or fails the job when attempts run out.

        ``counts=False`` reschedules without using up an attempt (the job waits for the setup to be fixed).
        """
        now = self._clock()
        async with self._tx() as session:
            job = await self._load(session, job_id)
            _require_owner(job, worker_id, now)
            src = job.status
            if counts:
                job.attempts += 1
            job.error = clean_error(error)
            job.locked_by = None
            job.locked_until = None
            job.updated_at = now
            if job.attempts >= self._queue.max_attempts:
                job.status = Status.FAILED
                job.notify_pending = True
                job.next_attempt_at = None
                event, details = "failed_max_attempts", {"attempts": job.attempts}
                log.error("job %s failed after %s attempts: %s", job.id, job.attempts, job.error)
            else:
                delay = retry_after if retry_after is not None else backoff_seconds(max(job.attempts, 1), self._queue, self._rand)
                job.next_attempt_at = now + timedelta(seconds=delay)
                event, details = "retry_scheduled", {"attempts": job.attempts, "delay_seconds": round(delay, 1)}
                log.warning("job %s attempt %s failed, retry in %.0fs: %s", job.id, job.attempts, delay, job.error)
            session.add(
                audit_entry(
                    now,
                    job_id=job.id,
                    actor=actor,
                    event=event,
                    from_status=src,
                    to_status=job.status,
                    method=job.method,
                    result="error",
                    error=job.error,
                    details=details,
                )
            )
        return job

    async def set_doc_key(self, job_id: int, doc_key: str, *, worker_id: str | None, actor: str) -> DocKeyResult:
        """Claim the document key for this job. If another live job owns it, nothing is written and the owner is returned.

        ``worker_id`` ``None`` is a human decision on a job no worker holds (like ``transition``).
        """
        now = self._clock()
        try:
            async with self._tx() as session:
                job = await self._load(session, job_id)
                _require_owner(job, worker_id, now)
                holder = await self._find_key_holder(session, doc_key, exclude_job_id=job_id)
                if holder is not None:
                    return DocKeyResult(ok=False, holder=holder)
                job.doc_key = doc_key
                job.updated_at = now
                session.add(
                    audit_entry(now, job_id=job.id, actor=actor, event="doc_key_set", method=job.method, details={"doc_key": doc_key})
                )
                await session.flush()
        except IntegrityError:
            # PostgreSQL only: lost the race after the check; the unique index caught it.
            async with self._sf() as session:
                holder = await self._find_key_holder(session, doc_key, exclude_job_id=job_id)
            if holder is None:
                raise
            return DocKeyResult(ok=False, holder=holder)
        return DocKeyResult(ok=True)

    @staticmethod
    async def _find_key_holder(session: AsyncSession, doc_key: str, *, exclude_job_id: int | None) -> Job | None:
        query = select(Job).where(Job.doc_key == doc_key, Job.status.not_in(sorted(KEY_RELEASING)))
        if exclude_job_id is not None:
            query = query.where(Job.id != exclude_job_id)
        return (await session.execute(query.limit(1))).scalars().first()

    # ------------------------------------------------------------------ queries

    async def get(self, job_id: int) -> Job | None:
        async with self._sf() as session:
            return await session.get(Job, job_id)

    async def find_by_doc_key(self, doc_key: str) -> Job | None:
        """The live (non duplicate/failed/rejected) job that owns this document key."""
        async with self._sf() as session:
            return await self._find_key_holder(session, doc_key, exclude_job_id=None)

    async def list_by_status(self, statuses: Iterable[Status], *, limit: int = 100) -> list[Job]:
        async with self._sf() as session:
            rows = await session.execute(
                select(Job).where(Job.status.in_(sorted(statuses))).order_by(Job.id).limit(limit)
            )
            return list(rows.scalars())

    async def counts_by_status(self) -> dict[Status, int]:
        async with self._sf() as session:
            rows = await session.execute(select(Job.status, func.count()).group_by(Job.status))
            return {status: count for status, count in rows.all()}

    # ------------------------------------------------------------------ notifications and review

    async def list_notify_pending(self, *, limit: int = 50) -> list[Job]:
        """Jobs whose sender still has to be told about their current status (oldest first)."""
        async with self._sf() as session:
            rows = await session.execute(
                select(Job).where(Job.notify_pending.is_(True)).order_by(Job.id).limit(limit)
            )
            return list(rows.scalars())

    async def mark_notified(self, job_id: int, status: Status, *, review_message_id: int | None = None) -> bool:
        """The message about ``status`` went out. Ignored (False) if the job has moved on since: that status is told next."""
        async with self._tx() as session:
            job = await self._load(session, job_id)
            if job.status is not status:
                return False
            job.notify_pending = False
            if review_message_id is not None:
                job.review_message_id = review_message_id
            return True

    async def find_review_by_message(self, telegram_user_id: int, message_id: int) -> Job | None:
        """The job waiting for this user's answer to the bot's question ``message_id``."""
        async with self._sf() as session:
            rows = await session.execute(
                select(Job)
                .where(
                    Job.status == Status.NEEDS_REVIEW,
                    Job.telegram_user_id == telegram_user_id,
                    Job.review_message_id == message_id,
                )
                .limit(1)
            )
            return rows.scalars().first()

    async def find_review_by_file_message(self, chat_id: int, message_id: int) -> Job | None:
        """The waiting job of the file sent as ``message_id`` in ``chat_id`` (a reply to the photo itself)."""
        async with self._sf() as session:
            rows = await session.execute(
                select(Job)
                .where(Job.status == Status.NEEDS_REVIEW, Job.chat_id == chat_id, Job.message_id == message_id)
                .order_by(Job.id)
                .limit(1)
            )
            return rows.scalars().first()

    async def list_page_siblings(self, job: Job, *, window_seconds: float) -> list[Job]:
        """Other photos that may be pages of the same document: the same album, or the same person's photos
        in the same chat sent within ``window_seconds`` before or after this one."""
        span = timedelta(seconds=window_seconds)
        same_person = (Job.telegram_user_id == job.telegram_user_id) & Job.created_at.between(
            job.created_at - span, job.created_at + span
        )
        near = or_(same_person, Job.media_group_id == job.media_group_id) if job.media_group_id else same_person
        async with self._sf() as session:
            rows = await session.execute(
                select(Job).where(Job.chat_id == job.chat_id, Job.id != job.id, near).order_by(Job.id)
            )
            return list(rows.scalars())

    async def list_record_pages(self, job: Job, *, window_seconds: float) -> list[Job]:
        """The pages of ``job``'s document: this person's photos in this chat placed on the same record, sent one
        after another (never more than ``window_seconds`` apart) around ``job``. ``job`` itself included."""
        async with self._sf() as session:
            rows = await session.execute(
                select(Job)
                .where(
                    Job.chat_id == job.chat_id,
                    Job.telegram_user_id == job.telegram_user_id,
                    Job.odoo_model == job.odoo_model,
                    Job.odoo_record_id == job.odoo_record_id,
                    Job.status.in_(sorted({Status.READY, Status.ATTACHED})),
                )
                .order_by(Job.created_at, Job.id)
            )
            pages = list(rows.scalars())
        at = next((i for i, page in enumerate(pages) if page.id == job.id), None)
        if at is None:
            return []
        span = timedelta(seconds=window_seconds)
        start = end = at
        while start > 0 and pages[start].created_at - pages[start - 1].created_at <= span:
            start -= 1
        while end + 1 < len(pages) and pages[end + 1].created_at - pages[end].created_at <= span:
            end += 1
        return pages[start:end + 1]

    async def list_record_reviews(self, job: Job, *, window_seconds: float) -> list[Job]:
        """This person's photos in this chat placed on ``job``'s record but waiting in ``needs_review``, sent within
        ``window_seconds`` of ``job`` (e.g. pages told "not signed" before the signed page came)."""
        span = timedelta(seconds=window_seconds)
        async with self._sf() as session:
            rows = await session.execute(
                select(Job)
                .where(
                    Job.chat_id == job.chat_id,
                    Job.telegram_user_id == job.telegram_user_id,
                    Job.odoo_model == job.odoo_model,
                    Job.odoo_record_id == job.odoo_record_id,
                    Job.status == Status.NEEDS_REVIEW,
                    Job.id != job.id,
                    Job.created_at.between(job.created_at - span, job.created_at + span),
                )
                .order_by(Job.id)
            )
            return list(rows.scalars())

    async def album_asked(self, job: Job) -> bool:
        """Another photo of this album already got the "send a clearer photo" reply (one reply per album)."""
        if not job.media_group_id:
            return False
        async with self._sf() as session:
            rows = await session.execute(
                select(Job.id).where(
                    Job.chat_id == job.chat_id,
                    Job.media_group_id == job.media_group_id,
                    Job.id != job.id,
                    Job.review_message_id.is_not(None),
                ).limit(1)
            )
            return rows.first() is not None

    async def list_review_for_user(self, telegram_user_id: int, *, limit: int = 20) -> list[Job]:
        async with self._sf() as session:
            rows = await session.execute(
                select(Job)
                .where(Job.status == Status.NEEDS_REVIEW, Job.telegram_user_id == telegram_user_id)
                .order_by(Job.id)
                .limit(limit)
            )
            return list(rows.scalars())

    # ------------------------------------------------------------------ housekeeping

    async def list_disposable_files(self, before: datetime, *, limit: int = 500) -> list[Job]:
        """Finished jobs, last changed before ``before``, that still reference a local file."""
        async with self._sf() as session:
            rows = await session.execute(
                select(Job)
                .where(Job.status.in_(sorted(FILE_DISPOSABLE)), Job.updated_at < before, Job.path.is_not(None))
                .order_by(Job.id)
                .limit(limit)
            )
            return list(rows.scalars())

    async def forget_file(self, job_id: int) -> None:
        """The local copy of a finished job was deleted; Odoo keeps the attached one."""
        async with self._tx() as session:
            job = await self._load(session, job_id)
            if job.status in FILE_DISPOSABLE:
                job.path = None
