from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from docbot.db.base import Base
from docbot.db.types import UTCDateTime
from docbot.pipeline.states import KEY_RELEASING, Method, Status


def _enum(cls: type) -> Enum:
    # Store the lowercase values ("qr_ok"), not the member names; no CHECK constraint, so adding a
    # status later does not need a table rebuild on SQLite (the application enum is the authority).
    return Enum(cls, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])


_KEY_RELEASING_SQL = ", ".join(f"'{status.value}'" for status in sorted(KEY_RELEASING))


class Job(Base):
    """One image = one job. This table is also the work queue (see JobRepository)."""

    __tablename__ = "jobs"
    __table_args__ = (
        # A redelivered Telegram update (same message, same file) can never create a second job.
        UniqueConstraint("chat_id", "message_id", "file_unique_id", name="uq_jobs_chat_message_file"),
        # Two jobs racing for the same document: exactly one owns the key.
        Index(
            "uq_jobs_doc_key_active",
            "doc_key",
            unique=True,
            sqlite_where=text(f"doc_key IS NOT NULL AND status NOT IN ({_KEY_RELEASING_SQL})"),
            postgresql_where=text(f"doc_key IS NOT NULL AND status NOT IN ({_KEY_RELEASING_SQL})"),
        ),
        Index("ix_jobs_status_next_attempt_at", "status", "next_attempt_at"),
        # The notifier's outbox: only the few rows that still have to tell somebody something.
        Index(
            "ix_jobs_notify_pending",
            "id",
            sqlite_where=text("notify_pending = 1"),
            postgresql_where=text("notify_pending IS TRUE"),
        ),
        # A text reply to the bot's question is matched to its job through the question's message id.
        Index("ix_jobs_review_message_id", "review_message_id"),
        # Pages of one document: an album, or one person's photos sent one after another.
        Index("ix_jobs_chat_id_media_group_id", "chat_id", "media_group_id"),
        Index("ix_jobs_chat_id_telegram_user_id_created_at", "chat_id", "telegram_user_id", "created_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Where it came from
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    message_thread_id: Mapped[int | None] = mapped_column(BigInteger)  # forum topic: replies must go to the same one
    update_id: Mapped[int | None] = mapped_column(BigInteger)  # informational
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    sender_name: Mapped[str | None] = mapped_column(String(255))  # "Full Name (@username)", for the Odoo note
    file_id: Mapped[str] = mapped_column(String(512))
    file_unique_id: Mapped[str] = mapped_column(String(128))

    # The file on disk (path relative to the images directory)
    path: Mapped[str | None] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(Integer)
    file_name: Mapped[str | None] = mapped_column(String(255))  # original name of a document upload
    media_group_id: Mapped[str | None] = mapped_column(String(64))  # Telegram album the photo was sent in

    # Processing state
    status: Mapped[Status] = mapped_column(_enum(Status), default=Status.RECEIVED)
    method: Mapped[Method | None] = mapped_column(_enum(Method))
    qr_value: Mapped[str | None] = mapped_column(Text)  # every code text found, one per line
    doc_key: Mapped[str | None] = mapped_column(String(512))  # "<model>:<record id>:<file sha1>"
    extracted_data: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    odoo_model: Mapped[str | None] = mapped_column(String(128))
    odoo_record_id: Mapped[int | None] = mapped_column(Integer)
    odoo_record_name: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)  # last error / review reason

    # Telling the sender: set when the job enters a status in NOTIFY_ON, cleared once the message went out
    notify_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    review_message_id: Mapped[int | None] = mapped_column(BigInteger)  # the bot's question, see NEEDS_REVIEW

    # Queue bookkeeping
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # NULL = due now
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class AuditLog(Base):
    """Who did what to which image, when, by which method, with what result."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_job_id_ts", "job_id", "ts"),
        Index("ix_audit_log_event_ts", "event", "ts"),  # e.g. counting today's AI calls for the daily limit
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))  # NULL: not tied to a job (link/unlink)
    actor: Mapped[str] = mapped_column(String(64))  # "tg:123", "worker:w1-ab12cd", "system"
    event: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    method: Mapped[str | None] = mapped_column(String(16))
    result: Mapped[str | None] = mapped_column(String(16))  # ok | error
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
