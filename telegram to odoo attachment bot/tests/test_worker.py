import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from helpers import eventually, make_job, wait_for_status
from sqlalchemy import update

from docbot.config import QueueSettings
from docbot.db.models import Job as JobRow
from docbot.db.repositories.jobs import JobRepository
from docbot.errors import PermanentError, TransientError
from docbot.pipeline.states import Method, Status
from docbot.pipeline.worker import JobContext, WorkerPool

# Real time here (no FakeClock): the pool sleeps and the queue compares timestamps for real, so keep everything tiny.
FAST = QueueSettings(
    workers=2,
    poll_interval_seconds=0.01,
    lease_seconds=5,
    max_attempts=3,
    backoff_base_seconds=0.01,
    backoff_max_seconds=0.05,
    backoff_jitter=0,
)

NEXT_STEP = {Status.RECEIVED: Status.QR_OK, Status.QR_OK: Status.READY, Status.READY: Status.ATTACHED}


@pytest.fixture
def real_jobs(session_factory) -> JobRepository:
    return JobRepository(session_factory, FAST)


@asynccontextmanager
async def running(pool: WorkerPool):
    stop = asyncio.Event()
    task = asyncio.create_task(pool.run(stop))
    try:
        yield stop
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)


async def advance_one_step(ctx: JobContext) -> None:
    await ctx.transition(NEXT_STEP[ctx.job.status])


async def eventually_locked(jobs: JobRepository, job_id: int) -> None:
    async def check() -> bool:
        return (await jobs.get(job_id)).locked_by is not None

    await eventually(check)


async def test_a_job_is_carried_through_every_step_to_the_end(real_jobs, audit):
    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, advance_one_step, FAST)):
        await wait_for_status(real_jobs, job.id, Status.ATTACHED)
    trail = await audit.list_for_job(job.id)
    assert [a.to_status for a in trail] == ["received", "qr_ok", "ready", "attached"]
    assert all(a.actor.startswith("worker:w") for a in trail[1:])


async def test_each_step_runs_exactly_once_even_with_many_workers(real_jobs):
    seen: Counter = Counter()

    async def process(ctx: JobContext) -> None:
        seen[(ctx.job.id, ctx.job.status)] += 1
        await asyncio.sleep(0.005)
        await advance_one_step(ctx)

    settings = FAST.model_copy(update={"workers": 5})
    ids = [(await real_jobs.enqueue(make_job(n))).job.id for n in range(1, 21)]
    async with running(WorkerPool(real_jobs, process, settings)):
        for job_id in ids:
            await wait_for_status(real_jobs, job_id, Status.ATTACHED)
    assert len(seen) == 20 * 3
    assert set(seen.values()) == {1}


async def test_a_transient_failure_is_retried_and_the_job_recovers(real_jobs, audit):
    failures = {"left": 2}

    async def flaky(ctx: JobContext) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise TransientError("Odoo unavailable")
        await advance_one_step(ctx)

    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, flaky, FAST)):
        await wait_for_status(real_jobs, job.id, Status.ATTACHED)
    stored = await real_jobs.get(job.id)
    assert (stored.attempts, stored.error) == (0, None)  # the successful steps reset the budget
    events = [a.event for a in await audit.list_for_job(job.id)]
    assert events.count("retry_scheduled") == 2


async def test_a_job_that_keeps_failing_transiently_ends_up_failed_with_the_reason(real_jobs):
    async def down(ctx: JobContext) -> None:
        raise TransientError("Odoo unavailable")

    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, down, FAST)):
        await wait_for_status(real_jobs, job.id, Status.FAILED)
    stored = await real_jobs.get(job.id)
    assert (stored.attempts, stored.error) == (3, "Odoo unavailable")
    assert stored.status is Status.FAILED


async def test_retry_after_from_the_processor_is_honoured(real_jobs):
    async def rate_limited(ctx: JobContext) -> None:
        raise TransientError("rate limited", retry_after=3600)

    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, rate_limited, FAST)):
        await asyncio.sleep(0.3)
    stored = await real_jobs.get(job.id)
    assert (stored.status, stored.attempts) == (Status.RECEIVED, 1)  # waiting for an hour, not hammering
    assert await real_jobs.claim("someone") is None


async def test_a_permanent_error_fails_the_job_at_once(real_jobs, audit):
    async def reject(ctx: JobContext) -> None:
        raise PermanentError("image is not a document")

    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, reject, FAST)):
        await wait_for_status(real_jobs, job.id, Status.FAILED)
    stored = await real_jobs.get(job.id)
    assert (stored.error, stored.attempts) == ("image is not a document", 0)
    assert [a.event for a in await audit.list_for_job(job.id)][-1] == "failed_permanent"


async def test_an_unexpected_exception_is_a_bug_that_is_retried_then_failed(real_jobs, caplog):
    async def buggy(ctx: JobContext) -> None:
        return 1 / 0

    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, buggy, FAST)):
        await wait_for_status(real_jobs, job.id, Status.FAILED)
    stored = await real_jobs.get(job.id)
    assert stored.error.startswith("ZeroDivisionError")
    assert "unexpected error in processor" in caplog.text  # with a traceback, for the developer


async def test_returning_without_moving_the_job_counts_as_a_failed_attempt(real_jobs):
    async def forgetful(ctx: JobContext) -> None:
        return None

    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, forgetful, FAST)):
        await wait_for_status(real_jobs, job.id, Status.FAILED)
    assert "without moving the job" in (await real_jobs.get(job.id)).error


async def test_a_hung_step_is_cancelled_before_its_lease_runs_out(real_jobs):
    async def hangs(ctx: JobContext) -> None:
        await asyncio.sleep(60)

    settings = FAST.model_copy(update={"lease_seconds": 0.3, "workers": 1})
    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, hangs, settings)):
        await wait_for_status(real_jobs, job.id, Status.FAILED, timeout=15)
    assert "exceeded" in (await real_jobs.get(job.id)).error


async def test_fields_saved_mid_step_survive_a_later_failure(real_jobs):
    """E.g. the Odoo record was created, then the attachment upload failed: the record id must not be lost."""
    calls = {"n": 0}

    async def process(ctx: JobContext) -> None:
        calls["n"] += 1
        if ctx.job.odoo_record_id is None:
            await ctx.update(odoo_record_id=77, odoo_record_name="SO0077")
            raise TransientError("attachment upload failed")
        await ctx.transition(Status.NEEDS_REVIEW)  # any waiting status: nothing else may touch the job

    job = (await real_jobs.enqueue(make_job(1))).job
    async with running(WorkerPool(real_jobs, process, FAST)):
        await wait_for_status(real_jobs, job.id, Status.NEEDS_REVIEW)
    stored = await real_jobs.get(job.id)
    assert (stored.odoo_record_id, stored.odoo_record_name) == (77, "SO0077")
    assert calls["n"] == 2  # the retry saw the saved id and did not create a second record


async def test_the_processor_can_detect_a_duplicate_document(real_jobs):
    async def process(ctx: JobContext) -> None:
        if ctx.job.status is not Status.RECEIVED:
            await ctx.transition(Status.NEEDS_REVIEW)  # park the winner
            return
        result = await ctx.claim_doc_key("DOC-1")
        if not result.ok:
            await ctx.transition(Status.DUPLICATE, doc_key="DOC-1", error=f"already queued as job {result.holder.id}")
            return
        await ctx.transition(Status.QR_OK, method=Method.QR)

    first = (await real_jobs.enqueue(make_job(1))).job
    second = (await real_jobs.enqueue(make_job(2))).job
    async with running(WorkerPool(real_jobs, process, FAST)):
        for job in (first, second):
            await wait_for_status(real_jobs, job.id, Status.NEEDS_REVIEW, Status.DUPLICATE)
    stored = [await real_jobs.get(j.id) for j in (first, second)]
    assert {j.status for j in stored} == {Status.NEEDS_REVIEW, Status.DUPLICATE}
    winner = next(j for j in stored if j.status is Status.NEEDS_REVIEW)
    loser = next(j for j in stored if j.status is Status.DUPLICATE)
    assert loser.error == f"already queued as job {winner.id}"
    assert (await real_jobs.find_by_doc_key("DOC-1")).id == winner.id


async def test_a_job_taken_over_by_another_worker_is_not_overwritten(real_jobs, session_factory, caplog):
    """A slow processor whose lease was recovered must not clobber the new owner's work."""
    release = asyncio.Event()
    late_result_dropped = asyncio.Event()

    async def slow(ctx: JobContext) -> None:
        await release.wait()  # meanwhile the job is stolen...
        try:
            await ctx.transition(Status.QR_OK)  # ...and this late result must be dropped
        finally:
            late_result_dropped.set()

    job = (await real_jobs.enqueue(make_job(1))).job
    settings = FAST.model_copy(update={"workers": 1})
    async with running(WorkerPool(real_jobs, slow, settings)):
        await eventually_locked(real_jobs, job.id)
        async with session_factory() as session, session.begin():  # another worker takes over
            await session.execute(
                update(JobRow).where(JobRow.id == job.id).values(locked_by="thief", locked_until=job.created_at + timedelta(days=1))
            )
        release.set()
        await asyncio.wait_for(late_result_dropped.wait(), timeout=5)
    stored = await real_jobs.get(job.id)
    assert (stored.status, stored.locked_by) == (Status.RECEIVED, "thief")
    assert "lease lost" in caplog.text


async def test_an_idle_pool_stops_promptly(real_jobs):
    pool = WorkerPool(real_jobs, advance_one_step, FAST)
    stop = asyncio.Event()
    task = asyncio.create_task(pool.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_the_pool_survives_database_errors_while_claiming(real_jobs, caplog):
    class Flaky(JobRepository):
        broken = 3

        async def claim(self, worker_id):
            if Flaky.broken:
                Flaky.broken -= 1
                raise RuntimeError("database is locked")
            return await super().claim(worker_id)

    flaky = Flaky(real_jobs._sf, FAST)
    job = (await flaky.enqueue(make_job(1))).job
    async with running(WorkerPool(flaky, advance_one_step, FAST)):
        await wait_for_status(flaky, job.id, Status.ATTACHED)
    assert "could not claim a job" in caplog.text


async def test_jobs_resume_after_the_pool_is_restarted(real_jobs):
    """Stop after the first step, start a new pool: the job continues from where it was."""
    stop = asyncio.Event()

    async def one_step_then_stop(ctx: JobContext) -> None:
        await advance_one_step(ctx)
        stop.set()  # the single worker sees this before it would claim the next step

    job = (await real_jobs.enqueue(make_job(1))).job
    settings = FAST.model_copy(update={"workers": 1})
    await asyncio.wait_for(WorkerPool(real_jobs, one_step_then_stop, settings).run(stop), timeout=5)
    midway = await real_jobs.get(job.id)
    assert (midway.status, midway.locked_by) == (Status.QR_OK, None)

    async with running(WorkerPool(real_jobs, advance_one_step, FAST)):
        await wait_for_status(real_jobs, job.id, Status.ATTACHED)
