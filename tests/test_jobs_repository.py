import asyncio
from datetime import timedelta

import pytest
from helpers import make_job

from docbot.config import QueueSettings
from docbot.db.repositories.audit import MAX_ERROR_LEN
from docbot.db.repositories.jobs import JobRepository, backoff_seconds
from docbot.db.session import create_engine, create_session_factory
from docbot.errors import InvalidTransition, LeaseLost
from docbot.pipeline.states import Method, Status
from docbot.redaction import register_secrets


async def move(jobs: JobRepository, job_id: int, *statuses: Status) -> None:
    """Walk an unclaimed job through statuses (the way a human/admin action would)."""
    for status in statuses:
        await jobs.transition(job_id, status, worker_id=None, actor="test")


# ----------------------------------------------------------------------------- intake


async def test_enqueue_creates_a_received_job_with_an_audit_trail(jobs, audit):
    result = await jobs.enqueue(make_job(1, path="2026/01/a.jpg", mime_type="image/jpeg", file_size=1234))
    job = result.job
    assert result.created
    assert (job.status, job.attempts, job.doc_key, job.locked_by) == (Status.RECEIVED, 0, None, None)
    assert (job.path, job.mime_type, job.file_size) == ("2026/01/a.jpg", "image/jpeg", 1234)
    trail = await audit.list_for_job(job.id)
    assert [(a.event, a.actor, a.to_status) for a in trail] == [("received", "tg:42", "received")]


async def test_redelivered_update_does_not_create_a_second_job(jobs):
    first = await jobs.enqueue(make_job(1))
    again = await jobs.enqueue(make_job(1, update_id=99999))
    assert not again.created
    assert again.job.id == first.job.id
    assert sum((await jobs.counts_by_status()).values()) == 1


async def test_album_items_and_forwarded_copies_are_separate_jobs(jobs):
    a = await jobs.enqueue(make_job(1))
    b = await jobs.enqueue(make_job(2))
    c = await jobs.enqueue(make_job(3, file_unique_id="uniq-1"))  # the same picture sent again later
    assert len({a.job.id, b.job.id, c.job.id}) == 3
    assert all(r.created for r in (a, b, c))


async def test_concurrent_enqueue_of_the_same_message_creates_one_job(jobs):
    results = await asyncio.gather(*(jobs.enqueue(make_job(1)) for _ in range(6)))
    assert sum(r.created for r in results) == 1
    assert len({r.job.id for r in results}) == 1


# ----------------------------------------------------------------------------- claiming


async def test_claim_on_an_empty_queue_returns_none(jobs):
    assert await jobs.claim("w1") is None


async def test_claim_hands_out_the_oldest_job_and_locks_it(jobs, clock, queue_settings):
    ids = [(await jobs.enqueue(make_job(n))).job.id for n in (1, 2, 3)]
    first = await jobs.claim("w1")
    assert first.id == ids[0]
    assert first.locked_by == "w1"
    assert first.locked_until == clock.now + timedelta(seconds=queue_settings.lease_seconds)
    assert (await jobs.claim("w2")).id == ids[1]


async def test_a_locked_job_is_not_handed_out_again(jobs):
    await jobs.enqueue(make_job(1))
    assert await jobs.claim("w1") is not None
    assert await jobs.claim("w2") is None


@pytest.mark.parametrize(
    "path",
    [
        (),
        (Status.QR_OK,),
        (Status.AI_PROCESSING,),
        (Status.QR_OK, Status.READY),
    ],
)
async def test_workers_claim_jobs_in_every_working_status(jobs, path):
    job = (await jobs.enqueue(make_job(1))).job
    await move(jobs, job.id, *path)
    assert (await jobs.claim("w1")).id == job.id


@pytest.mark.parametrize(
    "path",
    [
        (Status.NEEDS_REVIEW,),
        (Status.NEEDS_REVIEW, Status.REJECTED),
        (Status.QR_OK, Status.READY, Status.ATTACHED),
        (Status.DUPLICATE,),
        (Status.FAILED,),
    ],
)
async def test_workers_never_claim_waiting_or_finished_jobs(jobs, path):
    job = (await jobs.enqueue(make_job(1))).job
    await move(jobs, job.id, *path)
    assert await jobs.claim("w1") is None


# ----------------------------------------------------------------------------- crash recovery


async def test_expired_lease_is_recovered_and_counts_as_a_failed_attempt(jobs, clock, audit):
    created = (await jobs.enqueue(make_job(1))).job
    await jobs.claim("dead-worker")
    clock.advance(59)
    assert await jobs.claim("w2") is None  # lease still valid
    clock.advance(2)
    recovered = await jobs.claim("w2")
    assert (recovered.id, recovered.locked_by, recovered.attempts) == (created.id, "w2", 1)
    assert "dead-worker" in recovered.error
    trail = await audit.list_for_job(created.id)
    lease_events = [a for a in trail if a.event == "lease_expired"]
    assert len(lease_events) == 1
    assert lease_events[0].details["previous_worker"] == "dead-worker"


async def test_a_job_that_keeps_killing_its_workers_is_eventually_failed(jobs, clock, audit):
    created = (await jobs.enqueue(make_job(1))).job
    await jobs.claim("w1")
    for expected_attempts in (1, 2):
        clock.advance(61)
        assert (await jobs.claim("w2")).attempts == expected_attempts
    clock.advance(61)
    assert await jobs.claim("w2") is None  # third expired lease = max_attempts: the job is retired
    stored = await jobs.get(created.id)
    assert stored.status is Status.FAILED
    assert stored.locked_by is None
    trail = await audit.list_for_job(created.id)
    assert trail[-1].event == "lease_expired"
    assert trail[-1].to_status == "failed"


async def test_exhausting_one_job_does_not_stop_a_healthy_one_from_being_claimed(jobs, clock):
    bad = (await jobs.enqueue(make_job(1))).job
    good = (await jobs.enqueue(make_job(2))).job
    await jobs.claim("w1")  # takes `bad`
    for _ in range(2):
        clock.advance(61)
        again = await jobs.claim("w1")
        assert again.id == bad.id
    clock.advance(61)
    claimed = await jobs.claim("w1")  # `bad` is out of attempts: it is retired and `good` is returned
    assert claimed.id == good.id
    assert (await jobs.get(bad.id)).status is Status.FAILED


async def test_a_worker_that_lost_its_lease_cannot_change_the_job(jobs, clock):
    created = (await jobs.enqueue(make_job(1))).job
    await jobs.claim("slow")
    clock.advance(61)
    await jobs.claim("fast")  # recovers the job
    with pytest.raises(LeaseLost):
        await jobs.transition(created.id, Status.QR_OK, worker_id="slow", actor="slow")
    with pytest.raises(LeaseLost):
        await jobs.update_fields(created.id, worker_id="slow", actor="slow", qr_value="x")
    with pytest.raises(LeaseLost):
        await jobs.retry_later(created.id, worker_id="slow", actor="slow", error="late")
    with pytest.raises(LeaseLost):
        await jobs.set_doc_key(created.id, "DOC-1", worker_id="slow", actor="slow")
    stored = await jobs.get(created.id)
    assert (stored.status, stored.locked_by, stored.qr_value, stored.doc_key) == (Status.RECEIVED, "fast", None, None)
    assert (await jobs.transition(created.id, Status.QR_OK, worker_id="fast", actor="fast")).status is Status.QR_OK


async def test_an_unclaimed_job_cannot_be_changed_in_a_workers_name(jobs):
    created = (await jobs.enqueue(make_job(1))).job
    with pytest.raises(LeaseLost):
        await jobs.transition(created.id, Status.QR_OK, worker_id="w1", actor="w1")


# ----------------------------------------------------------------------------- transitions


async def test_transition_releases_the_lock_resets_attempts_and_saves_fields(jobs, clock):
    created = (await jobs.enqueue(make_job(1))).job
    claimed = await jobs.claim("w1")
    await jobs.retry_later(claimed.id, worker_id="w1", actor="w1", error="Odoo down")
    clock.advance(10)
    claimed = await jobs.claim("w1")
    assert claimed.attempts == 1

    moved = await jobs.transition(
        created.id,
        Status.QR_OK,
        worker_id="w1",
        actor="worker:w1",
        method=Method.QR,
        qr_value="https://example.test/doc/1",
        extracted_data={"doc_number": "A-1"},
    )
    stored = await jobs.get(created.id)
    for job in (moved, stored):
        assert job.status is Status.QR_OK
        assert (job.attempts, job.locked_by, job.locked_until, job.next_attempt_at, job.error) == (0, None, None, None, None)
        assert job.method is Method.QR
        assert job.qr_value == "https://example.test/doc/1"
        assert job.extracted_data == {"doc_number": "A-1"}


async def test_illegal_transition_is_refused_and_changes_nothing(jobs):
    created = (await jobs.enqueue(make_job(1))).job
    claimed = await jobs.claim("w1")
    with pytest.raises(InvalidTransition, match="received -> attached"):
        await jobs.transition(created.id, Status.ATTACHED, worker_id="w1", actor="w1")
    stored = await jobs.get(created.id)
    assert (stored.status, stored.locked_by) == (Status.RECEIVED, "w1")
    assert claimed.id == stored.id


async def test_transition_and_update_only_accept_data_columns(jobs):
    created = (await jobs.enqueue(make_job(1))).job
    await jobs.claim("w1")
    with pytest.raises(ValueError, match="attempts"):
        await jobs.transition(created.id, Status.QR_OK, worker_id="w1", actor="w1", attempts=0)
    with pytest.raises(ValueError, match="status"):
        await jobs.update_fields(created.id, worker_id="w1", actor="w1", status=Status.READY)


async def test_saving_fields_keeps_the_status_and_renews_the_lease(jobs, clock, queue_settings):
    created = (await jobs.enqueue(make_job(1))).job
    await jobs.claim("w1")
    clock.advance(50)
    job = await jobs.update_fields(created.id, worker_id="w1", actor="w1", odoo_record_id=77, odoo_record_name="SO0077")
    assert (job.status, job.odoo_record_id, job.odoo_record_name) == (Status.RECEIVED, 77, "SO0077")
    assert job.locked_until == clock.now + timedelta(seconds=queue_settings.lease_seconds)
    clock.advance(30)  # 80s after the claim, but only 30s after the renewal
    assert await jobs.claim("w2") is None


async def test_review_decision_needs_a_job_no_worker_is_holding(jobs):
    created = (await jobs.enqueue(make_job(1))).job
    await jobs.claim("w1")
    with pytest.raises(LeaseLost):
        await jobs.transition(created.id, Status.NEEDS_REVIEW, worker_id=None, actor="reviewer:1")

    parked = await jobs.transition(
        created.id, Status.NEEDS_REVIEW, worker_id="w1", actor="worker:w1", error="AI confidence 0.41 < 0.80"
    )
    assert parked.error == "AI confidence 0.41 < 0.80"
    approved = await jobs.transition(
        created.id, Status.READY, worker_id=None, actor="reviewer:5", method=Method.HUMAN, extracted_data={"doc_number": "A-1"}
    )
    assert (approved.status, approved.method, approved.error) == (Status.READY, Method.HUMAN, None)


async def test_full_flow_leaves_a_complete_audit_trail(jobs, audit):
    created = (await jobs.enqueue(make_job(1))).job
    for target, method in ((Status.QR_OK, Method.QR), (Status.READY, None), (Status.ATTACHED, None)):
        claimed = await jobs.claim("w1")
        await jobs.transition(
            claimed.id, target, worker_id="w1", actor="worker:w1", **({"method": method} if method else {})
        )
    trail = await audit.list_for_job(created.id)
    assert [(a.from_status, a.to_status) for a in trail] == [
        (None, "received"),
        ("received", "qr_ok"),
        ("qr_ok", "ready"),
        ("ready", "attached"),
    ]
    assert [a.method for a in trail] == [None, "qr", "qr", "qr"]
    assert {a.actor for a in trail[1:]} == {"worker:w1"}
    assert (await jobs.get(created.id)).status is Status.ATTACHED


# ----------------------------------------------------------------------------- retry and backoff


async def test_retry_waits_for_the_backoff_before_the_job_is_due_again(jobs, clock):
    created = (await jobs.enqueue(make_job(1))).job
    claimed = await jobs.claim("w1")
    retried = await jobs.retry_later(claimed.id, worker_id="w1", actor="w1", error="Odoo unavailable")
    assert (retried.status, retried.attempts, retried.locked_by, retried.error) == (
        Status.RECEIVED,
        1,
        None,
        "Odoo unavailable",
    )
    assert retried.next_attempt_at == clock.now + timedelta(seconds=10)
    assert await jobs.claim("w1") is None
    clock.advance(9.9)
    assert await jobs.claim("w1") is None
    clock.advance(0.2)
    assert (await jobs.claim("w1")).id == created.id


async def test_backoff_doubles_and_the_job_fails_when_attempts_run_out(jobs, clock, audit):
    created = (await jobs.enqueue(make_job(1))).job
    delays = []
    for _ in range(3):  # max_attempts = 3
        claimed = await jobs.claim("w1")
        job = await jobs.retry_later(claimed.id, worker_id="w1", actor="w1", error="still down")
        if job.next_attempt_at:
            delays.append((job.next_attempt_at - clock.now).total_seconds())
            clock.advance(delays[-1])
    assert delays == [10, 20]
    assert (job.status, job.attempts, job.next_attempt_at, job.error) == (Status.FAILED, 3, None, "still down")
    assert await jobs.claim("w1") is None
    events = [a.event for a in await audit.list_for_job(created.id)]
    assert events == ["received", "retry_scheduled", "retry_scheduled", "failed_max_attempts"]


async def test_a_setup_problem_makes_the_job_wait_without_using_up_attempts(jobs, clock, queue_settings):
    created = (await jobs.enqueue(make_job(1))).job
    for _ in range(queue_settings.max_attempts + 2):
        job = await jobs.claim("w1")
        await jobs.retry_later(job.id, worker_id="w1", actor="w1", error="Odoo login rad etildi", retry_after=60, counts=False)
        clock.advance(61)
    waiting = await jobs.get(created.id)
    assert (waiting.status, waiting.attempts) == (Status.RECEIVED, 0)  # never failed: it waits for the key to be fixed


async def test_explicit_retry_after_overrides_the_backoff(jobs, clock):
    await jobs.enqueue(make_job(1))
    claimed = await jobs.claim("w1")
    job = await jobs.retry_later(claimed.id, worker_id="w1", actor="w1", error="rate limited", retry_after=300)
    assert job.next_attempt_at == clock.now + timedelta(seconds=300)


async def test_a_successful_step_gives_the_next_step_a_fresh_attempt_budget(jobs, clock):
    created = (await jobs.enqueue(make_job(1))).job
    for _ in range(2):  # two failures of step 1 (budget is 3)
        claimed = await jobs.claim("w1")
        await jobs.retry_later(claimed.id, worker_id="w1", actor="w1", error="flaky")
        clock.advance(100)
    claimed = await jobs.claim("w1")
    await jobs.transition(claimed.id, Status.QR_OK, worker_id="w1", actor="w1")
    for _ in range(2):  # two more failures of step 2 must not fail the job
        claimed = await jobs.claim("w1")
        await jobs.retry_later(claimed.id, worker_id="w1", actor="w1", error="flaky")
        clock.advance(100)
    assert (await jobs.get(created.id)).status is Status.QR_OK


async def test_stored_errors_are_masked_and_capped(jobs):
    register_secrets(["supersecretapikey"])
    token = "123456789:AAHdummyTokenForTestsOnly_0123456789abcd"
    await jobs.enqueue(make_job(1))
    claimed = await jobs.claim("w1")
    job = await jobs.retry_later(
        claimed.id,
        worker_id="w1",
        actor="w1",
        error=f"GET https://api.telegram.org/file/bot{token}/x failed, key supersecretapikey " + "x" * 5000,
    )
    assert token not in job.error
    assert "supersecretapikey" not in job.error
    assert len(job.error) <= MAX_ERROR_LEN


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [(1, 10), (2, 20), (3, 40), (4, 80), (5, 100), (6, 100), (50, 100), (5000, 100)],
)
def test_backoff_grows_exponentially_up_to_the_cap(queue_settings, attempts, expected):
    assert backoff_seconds(attempts, queue_settings) == expected


def test_backoff_jitter_stays_within_bounds():
    settings = QueueSettings(backoff_base_seconds=10, backoff_max_seconds=100, backoff_jitter=0.2)
    assert backoff_seconds(1, settings, rand=lambda: 0.0) == pytest.approx(8)
    assert backoff_seconds(1, settings, rand=lambda: 0.5) == pytest.approx(10)
    assert backoff_seconds(1, settings, rand=lambda: 1.0) == pytest.approx(12)


# ----------------------------------------------------------------------------- document key


async def claimed_jobs(jobs: JobRepository, count: int):
    for n in range(1, count + 1):
        await jobs.enqueue(make_job(n))
    return [await jobs.claim(f"w{n}") for n in range(1, count + 1)]


async def test_first_job_owns_the_document_key(jobs):
    a, b = await claimed_jobs(jobs, 2)
    first = await jobs.set_doc_key(a.id, "DOC-1", worker_id="w1", actor="w1")
    second = await jobs.set_doc_key(b.id, "DOC-1", worker_id="w2", actor="w2")
    assert first.ok and first.holder is None
    assert not second.ok
    assert second.holder.id == a.id
    assert (await jobs.get(b.id)).doc_key is None  # nothing was written for the loser
    assert (await jobs.find_by_doc_key("DOC-1")).id == a.id

    duplicate = await jobs.transition(b.id, Status.DUPLICATE, worker_id="w2", actor="w2", doc_key="DOC-1")
    assert (duplicate.status, duplicate.doc_key) == (Status.DUPLICATE, "DOC-1")
    assert (await jobs.find_by_doc_key("DOC-1")).id == a.id  # the duplicate did not steal it


async def test_a_job_can_set_its_own_key_again(jobs):
    (a,) = await claimed_jobs(jobs, 1)
    assert (await jobs.set_doc_key(a.id, "DOC-1", worker_id="w1", actor="w1")).ok
    assert (await jobs.set_doc_key(a.id, "DOC-1", worker_id="w1", actor="w1")).ok


@pytest.mark.parametrize("dead_end", [Status.FAILED, Status.DUPLICATE])
async def test_a_dead_end_job_gives_up_its_document_key(jobs, dead_end):
    a, b = await claimed_jobs(jobs, 2)
    await jobs.set_doc_key(a.id, "DOC-1", worker_id="w1", actor="w1")
    await jobs.transition(a.id, dead_end, worker_id="w1", actor="w1", error="gave up")
    assert await jobs.find_by_doc_key("DOC-1") is None
    assert (await jobs.set_doc_key(b.id, "DOC-1", worker_id="w2", actor="w2")).ok


async def test_a_rejected_job_gives_up_its_document_key(jobs):
    a, b = await claimed_jobs(jobs, 2)
    await jobs.set_doc_key(a.id, "DOC-1", worker_id="w1", actor="w1")
    await jobs.transition(a.id, Status.NEEDS_REVIEW, worker_id="w1", actor="w1")
    await jobs.transition(a.id, Status.REJECTED, worker_id=None, actor="reviewer:1")
    assert (await jobs.set_doc_key(b.id, "DOC-1", worker_id="w2", actor="w2")).ok


async def test_only_one_of_many_racing_jobs_gets_the_key(jobs):
    claimed = await claimed_jobs(jobs, 6)
    results = await asyncio.gather(
        *(jobs.set_doc_key(job.id, "DOC-1", worker_id=job.locked_by, actor="t") for job in claimed)
    )
    assert sum(r.ok for r in results) == 1
    winner = (await jobs.find_by_doc_key("DOC-1")).id
    assert all(r.holder.id == winner for r in results if not r.ok)


# ----------------------------------------------------------------------------- concurrency, queries, restart


async def test_concurrent_claims_never_hand_out_a_job_twice(jobs):
    for n in range(1, 6):
        await jobs.enqueue(make_job(n))
    results = await asyncio.gather(*(jobs.claim(f"w{i}") for i in range(12)))
    ids = [job.id for job in results if job is not None]
    assert len(ids) == 5
    assert len(set(ids)) == 5


async def test_counting_and_listing_by_status(jobs):
    ids = [(await jobs.enqueue(make_job(n))).job.id for n in range(1, 5)]
    await move(jobs, ids[0], Status.NEEDS_REVIEW)
    await move(jobs, ids[1], Status.NEEDS_REVIEW)
    await move(jobs, ids[2], Status.FAILED)
    assert await jobs.counts_by_status() == {Status.RECEIVED: 1, Status.NEEDS_REVIEW: 2, Status.FAILED: 1}
    waiting = await jobs.list_by_status([Status.NEEDS_REVIEW])
    assert [j.id for j in waiting] == ids[:2]


async def test_queue_state_survives_reopening_the_database(db_url, jobs, clock, queue_settings):
    await jobs.enqueue(make_job(1))
    await jobs.enqueue(make_job(2))
    first = await jobs.claim("w1")
    retried = await jobs.retry_later(first.id, worker_id="w1", actor="w1", error="Odoo down")

    engine = create_engine(db_url)  # a "restart": brand new engine, session factory and repository
    try:
        reopened = JobRepository(create_session_factory(engine), queue_settings, clock=clock)
        assert sum((await reopened.counts_by_status()).values()) == 2
        stored = await reopened.get(first.id)
        assert (stored.attempts, stored.error, stored.next_attempt_at) == (1, "Odoo down", retried.next_attempt_at)
        second = await reopened.claim("w2")
        assert second.id != first.id  # the first job is still backing off
        clock.advance(10)
        assert (await reopened.claim("w2")).id == first.id
    finally:
        await engine.dispose()


# ----------------------------------------------------------------------------- pages of one document


async def test_the_pages_of_a_document_are_its_senders_photos_sent_one_after_another(jobs, clock):
    async def placed(n: int, record: int = 7, **overrides):
        job = (await jobs.enqueue(make_job(n, **overrides))).job
        await jobs.transition(job.id, Status.QR_OK, worker_id=None, actor="test")
        await jobs.transition(job.id, Status.READY, worker_id=None, actor="test", odoo_model="sale.order", odoo_record_id=record)
        return job.id

    earlier = await placed(1)  # the same document sent five minutes before: another sending
    clock.advance(300)
    first = await placed(2)
    clock.advance(100)
    second = await placed(3)
    await placed(4, record=8)  # another document
    await placed(5, telegram_user_id=43)  # another person
    await jobs.enqueue(make_job(6))  # not placed
    clock.advance(100)
    last = await placed(7)
    pages = await jobs.list_record_pages(await jobs.get(last), window_seconds=120)
    assert [p.id for p in pages] == [first, second, last]  # 200 s from the first page, but no pause over 120 s
    assert earlier not in [p.id for p in await jobs.list_record_pages(await jobs.get(first), window_seconds=120)]
