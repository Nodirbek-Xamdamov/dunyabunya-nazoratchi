"""Worker pool: claims jobs from the queue and runs a *processor* on each.

Processor contract (the QR / AI / Odoo steps of later stages plug in here):

* One call advances the job by **one step**: it does the work for ``ctx.job.status`` and finishes by
  calling ``ctx.transition(...)``. Each step boundary is persisted, so a crash never loses more than
  the step in flight.
* Raise ``TransientError`` for retryable failures (Odoo down, network): the job keeps its status and
  is retried with backoff. Raise ``PermanentError`` to fail the job at once. Any other exception is a
  bug and is treated like a transient one (retried, then failed after ``max_attempts``, logged with
  its traceback).
* Returning without a transition is treated as a transient failure ("no progress"), so a buggy
  processor cannot spin on a job.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from docbot.config import QueueSettings
from docbot.db.models import Job
from docbot.db.repositories.jobs import DocKeyResult, JobRepository
from docbot.errors import LeaseLost, PermanentError, TransientError
from docbot.pipeline.states import Status

log = logging.getLogger(__name__)

# The processor is cancelled slightly before the lease runs out, so a hung step can never overlap
# with another worker that recovers the job.
_LEASE_SAFETY = 0.9


@dataclass
class JobContext:
    """The claimed job plus helpers bound to this worker's lease."""

    job: Job
    worker_id: str
    jobs: JobRepository
    progressed: bool = False

    @property
    def actor(self) -> str:
        return f"worker:{self.worker_id}"

    async def transition(self, to: Status, *, event: str = "status_changed", error: str | None = None, **fields: Any) -> Job:
        self.job = await self.jobs.transition(
            self.job.id, to, worker_id=self.worker_id, actor=self.actor, event=event, error=error, **fields
        )
        self.progressed = True
        return self.job

    async def update(self, **fields: Any) -> Job:
        """Save fields without changing status (renews the lease)."""
        self.job = await self.jobs.update_fields(self.job.id, worker_id=self.worker_id, actor=self.actor, **fields)
        return self.job

    async def claim_doc_key(self, doc_key: str) -> DocKeyResult:
        result = await self.jobs.set_doc_key(self.job.id, doc_key, worker_id=self.worker_id, actor=self.actor)
        if result.ok:
            self.job = await self.jobs.get(self.job.id) or self.job
        return result


Processor = Callable[[JobContext], Awaitable[None]]


class WorkerPool:
    def __init__(self, jobs: JobRepository, process: Processor, settings: QueueSettings, *, name: str = "w"):
        self._jobs = jobs
        self._process = process
        self._settings = settings
        self._name = name

    async def run(self, stop: asyncio.Event) -> None:
        """Run ``settings.workers`` workers until ``stop`` is set; each finishes its current job first."""
        tasks = [
            asyncio.create_task(self._loop(f"{self._name}{i}-{secrets.token_hex(3)}", stop), name=f"{self._name}{i}")
            for i in range(1, self._settings.workers + 1)
        ]
        log.info("worker pool started (%s workers)", len(tasks))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        log.info("worker pool stopped")

    async def _loop(self, worker_id: str, stop: asyncio.Event) -> None:
        poll = self._settings.poll_interval_seconds
        while not stop.is_set():
            try:
                job = await self._jobs.claim(worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s: could not claim a job (database problem?)", worker_id)
                await _idle(stop, poll)
                continue
            if job is None:
                await _idle(stop, poll)
                continue
            await self._run_job(job, worker_id)

    async def _run_job(self, job: Job, worker_id: str) -> None:
        ctx = JobContext(job, worker_id, self._jobs)
        limit = self._settings.lease_seconds * _LEASE_SAFETY
        deadline = asyncio.timeout(limit)
        try:
            async with deadline:
                await self._process(ctx)
        except asyncio.CancelledError:
            raise
        except LeaseLost:
            log.warning("job %s: lease lost while processing, result dropped", job.id)
        except PermanentError as exc:
            await self._fail(ctx, str(exc) or type(exc).__name__)
        except TransientError as exc:
            await self._retry(ctx, str(exc) or type(exc).__name__, exc.retry_after, counts=exc.counts)
        except TimeoutError as exc:
            reason = f"processing exceeded {limit:.0f}s" if deadline.expired() else f"TimeoutError: {exc}"
            await self._retry(ctx, reason)
        except Exception as exc:
            log.exception("job %s: unexpected error in processor", job.id)
            await self._retry(ctx, f"{type(exc).__name__}: {exc}")
        else:
            if not ctx.progressed:
                await self._retry(ctx, "processor returned without moving the job to another status")

    async def _retry(self, ctx: JobContext, error: str, retry_after: float | None = None, *, counts: bool = True) -> None:
        try:
            await self._jobs.retry_later(
                ctx.job.id, worker_id=ctx.worker_id, actor=ctx.actor, error=error, retry_after=retry_after, counts=counts
            )
        except LeaseLost:
            log.warning("job %s: lease lost before the failure could be recorded", ctx.job.id)
        except Exception:
            log.exception("job %s: could not record a failed attempt; the lease will expire and the job be recovered", ctx.job.id)

    async def _fail(self, ctx: JobContext, error: str) -> None:
        try:
            await self._jobs.transition(
                ctx.job.id, Status.FAILED, worker_id=ctx.worker_id, actor=ctx.actor, event="failed_permanent", error=error
            )
        except LeaseLost:
            log.warning("job %s: lease lost before the failure could be recorded", ctx.job.id)
        except Exception:
            log.exception("job %s: could not mark the job failed; the lease will expire and the job be recovered", ctx.job.id)


async def _idle(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)
