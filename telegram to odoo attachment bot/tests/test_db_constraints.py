"""Uniqueness rules that must hold at the database level, independent of application code."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from docbot.db.models import Job
from docbot.db.types import UTCDateTime
from docbot.pipeline.states import KEY_RELEASING, Status

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def new_job(**overrides) -> Job:
    fields = dict(
        chat_id=-100,
        message_id=1,
        telegram_user_id=7,
        file_id="f",
        file_unique_id="u1",
        status=Status.RECEIVED,
        created_at=NOW,
        updated_at=NOW,
    )
    return Job(**{**fields, **overrides})


async def insert(session_factory, *rows) -> None:
    async with session_factory() as session, session.begin():
        session.add_all(rows)


# ----------------------------------------------------------------------------- jobs


async def test_same_message_and_file_cannot_be_queued_twice(session_factory):
    await insert(session_factory, new_job())
    with pytest.raises(IntegrityError):
        await insert(session_factory, new_job())


async def test_same_message_with_another_file_or_another_message_is_fine(session_factory):
    await insert(session_factory, new_job(), new_job(file_unique_id="u2"), new_job(message_id=2))


async def test_two_live_jobs_cannot_own_the_same_document_key(session_factory):
    await insert(session_factory, new_job(doc_key="DOC-1"))
    with pytest.raises(IntegrityError):
        await insert(session_factory, new_job(message_id=2, doc_key="DOC-1"))


@pytest.mark.parametrize("status", sorted(KEY_RELEASING))
async def test_dead_end_jobs_do_not_block_the_key(session_factory, status):
    await insert(session_factory, new_job(doc_key="DOC-1", status=status))
    await insert(session_factory, new_job(message_id=2, doc_key="DOC-1"))  # a live job may take it
    await insert(session_factory, new_job(message_id=3, doc_key="DOC-1", status=status))  # and dead ones may repeat


async def test_many_jobs_without_a_key_are_fine(session_factory):
    await insert(session_factory, new_job(message_id=1), new_job(message_id=2), new_job(message_id=3))


async def test_key_index_predicate_matches_the_state_machine(session_factory):
    """The index hardcodes the statuses (SQL); make sure it cannot drift from KEY_RELEASING."""
    async with session_factory() as session:
        sql = (await session.execute(text("SELECT sql FROM sqlite_master WHERE name = 'uq_jobs_doc_key_active'"))).scalar_one()
    for status in Status:
        assert (f"'{status.value}'" in sql) == (status in KEY_RELEASING), status


# ----------------------------------------------------------------------------- types


async def test_datetimes_round_trip_as_aware_utc(session_factory):
    await insert(session_factory, new_job(next_attempt_at=NOW))
    async with session_factory() as session:
        job = (await session.execute(text("SELECT id FROM jobs"))).scalar_one()
        loaded = await session.get(Job, job)
    assert loaded.next_attempt_at == NOW
    assert loaded.next_attempt_at.tzinfo is not None
    assert loaded.locked_until is None


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError, match="naive"):
        UTCDateTime().process_bind_param(datetime(2026, 1, 1), None)


async def test_json_columns_keep_non_ascii_text_readable(session_factory):
    data = {"customer": "Oʻzbekiston savdo MChJ", "total": "1 250 000"}
    await insert(session_factory, new_job(extracted_data=data))
    async with session_factory() as session:
        raw = (await session.execute(text("SELECT extracted_data FROM jobs"))).scalar_one()
        job = (await session.execute(text("SELECT id FROM jobs"))).scalar_one()
        loaded = await session.get(Job, job)
    assert "Oʻzbekiston" in raw  # stored as UTF-8, not \u escapes
    assert loaded.extracted_data == data


async def test_missing_json_is_sql_null_not_json_null(session_factory):
    await insert(session_factory, new_job())
    async with session_factory() as session:
        raw = (await session.execute(text("SELECT extracted_data FROM jobs"))).scalar_one()
    assert raw is None
