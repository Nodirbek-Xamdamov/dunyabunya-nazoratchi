import pytest

from docbot.pipeline.states import (
    ALLOWED_TRANSITIONS,
    CLAIMABLE,
    KEY_RELEASING,
    Status,
    can_transition,
)

ACTIVE = {Status.RECEIVED, Status.QR_OK, Status.AI_PROCESSING, Status.NEEDS_REVIEW, Status.READY}


def test_every_status_is_covered():
    assert set(ALLOWED_TRANSITIONS) == set(Status)


@pytest.mark.parametrize("status", [Status.ATTACHED, Status.DUPLICATE, Status.REJECTED, Status.IGNORED])
def test_final_statuses_have_no_way_out(status):
    assert ALLOWED_TRANSITIONS[status] == frozenset()


@pytest.mark.parametrize("status", sorted(ACTIVE))
def test_any_active_job_can_fail_or_turn_out_to_be_a_duplicate(status):
    assert can_transition(status, Status.FAILED)
    assert can_transition(status, Status.DUPLICATE)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (Status.RECEIVED, Status.QR_OK),
        (Status.RECEIVED, Status.AI_PROCESSING),  # QR unreadable
        (Status.RECEIVED, Status.NEEDS_REVIEW),  # QR unreadable and AI unavailable
        (Status.QR_OK, Status.READY),
        (Status.QR_OK, Status.AI_PROCESSING),  # QR text not in the expected format
        (Status.AI_PROCESSING, Status.READY),
        (Status.AI_PROCESSING, Status.NEEDS_REVIEW),  # low confidence
        (Status.NEEDS_REVIEW, Status.READY),  # approved / corrected
        (Status.NEEDS_REVIEW, Status.REJECTED),
        (Status.NEEDS_REVIEW, Status.RECEIVED),  # an admin re-runs it
        (Status.READY, Status.ATTACHED),
        (Status.RECEIVED, Status.IGNORED),  # not one of our documents
        (Status.AI_PROCESSING, Status.IGNORED),
        (Status.READY, Status.NEEDS_REVIEW),  # Odoo refused the data
        (Status.FAILED, Status.RECEIVED),  # admin retry
        (Status.FAILED, Status.READY),
        (Status.FAILED, Status.REJECTED),
    ],
)
def test_expected_moves_are_allowed(src, dst):
    assert can_transition(src, dst)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (Status.RECEIVED, Status.READY),  # cannot skip extraction
        (Status.RECEIVED, Status.ATTACHED),
        (Status.READY, Status.RECEIVED),
        (Status.NEEDS_REVIEW, Status.ATTACHED),  # must go through ready
        (Status.ATTACHED, Status.READY),
        (Status.DUPLICATE, Status.READY),
        (Status.REJECTED, Status.READY),
        (Status.READY, Status.READY),
        (Status.QR_OK, Status.IGNORED),  # our code was read: it is our document, never ignored
        (Status.READY, Status.IGNORED),
    ],
)
def test_illegal_moves_are_refused(src, dst):
    assert not can_transition(src, dst)


def test_workers_never_claim_jobs_waiting_for_a_human_or_finished_ones():
    assert CLAIMABLE == {Status.RECEIVED, Status.QR_OK, Status.AI_PROCESSING, Status.READY}


def test_only_dead_end_statuses_release_the_document_key():
    assert KEY_RELEASING == {Status.DUPLICATE, Status.FAILED, Status.REJECTED}
