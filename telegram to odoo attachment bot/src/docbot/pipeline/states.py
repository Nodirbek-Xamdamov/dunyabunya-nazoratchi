"""Job statuses and the allowed transitions between them (the state machine from docs/ARCHITECTURE.md)."""

from enum import StrEnum


class Status(StrEnum):
    RECEIVED = "received"  # file queued; next: read QR/barcodes (or the PDF text layer)
    QR_OK = "qr_ok"  # a code or document number was read; next: find the record in Odoo
    AI_PROCESSING = "ai_processing"  # no usable code; next: the AI reads the document
    NEEDS_REVIEW = "needs_review"  # one of our documents, but not placed: the sender is asked for a clearer photo
    READY = "ready"  # the Odoo record is known; next: attach the file
    ATTACHED = "attached"  # the file is attached to the Odoo record (done)
    DUPLICATE = "duplicate"  # the same file is already attached (or being attached) to the same record
    FAILED = "failed"
    REJECTED = "rejected"  # the sender said the file does not belong anywhere
    IGNORED = "ignored"  # not one of our documents (any other picture in the chat): silently dropped


class Method(StrEnum):
    QR = "qr"  # QR code or barcode
    TEXT = "text"  # text layer of a PDF
    AI = "ai"
    HUMAN = "human"


# Statuses a worker may pick up. `needs_review` waits for a human and is never claimed by workers.
CLAIMABLE = frozenset({Status.RECEIVED, Status.QR_OK, Status.AI_PROCESSING, Status.READY})

# Statuses that do not own their document key: another job may use the same key (see the partial
# unique index on jobs.doc_key). A rejected/failed/duplicate job must not block a later good one.
KEY_RELEASING = frozenset({Status.DUPLICATE, Status.FAILED, Status.REJECTED})

# Entering one of these tells the sender something (result, question, problem): the notifier picks them up.
NOTIFY_ON = frozenset({Status.ATTACHED, Status.NEEDS_REVIEW, Status.DUPLICATE, Status.FAILED})

_ACTIVE = (Status.RECEIVED, Status.QR_OK, Status.AI_PROCESSING, Status.NEEDS_REVIEW, Status.READY)

_ALLOWED: dict[Status, set[Status]] = {
    Status.RECEIVED: {Status.QR_OK, Status.AI_PROCESSING, Status.NEEDS_REVIEW, Status.IGNORED},
    Status.QR_OK: {Status.READY, Status.AI_PROCESSING, Status.NEEDS_REVIEW},
    Status.AI_PROCESSING: {Status.READY, Status.NEEDS_REVIEW, Status.IGNORED},
    # An admin may also re-run it from the start (``docbot requeue``), e.g. after a fix or a database switch.
    Status.NEEDS_REVIEW: {Status.READY, Status.REJECTED, Status.RECEIVED},
    Status.READY: {Status.ATTACHED, Status.NEEDS_REVIEW},
    # `failed` is a dead letter: an admin can push it back into the flow or close it.
    Status.FAILED: {Status.RECEIVED, Status.READY, Status.REJECTED},
    Status.ATTACHED: set(),
    Status.DUPLICATE: set(),
    Status.REJECTED: set(),
    Status.IGNORED: set(),
}
for _status in _ACTIVE:
    _ALLOWED[_status] |= {Status.DUPLICATE, Status.FAILED}

ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {k: frozenset(v) for k, v in _ALLOWED.items()}


def can_transition(src: Status, dst: Status) -> bool:
    return dst in ALLOWED_TRANSITIONS[src]
