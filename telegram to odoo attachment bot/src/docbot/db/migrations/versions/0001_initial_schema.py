"""initial schema: employees, jobs (work queue), audit_log

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Keep in sync with docbot.pipeline.states.KEY_RELEASING (a test checks this).
_DOC_KEY_ACTIVE = "doc_key IS NOT NULL AND status NOT IN ('duplicate', 'failed', 'rejected')"


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("odoo_employee_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.create_index(
            "uq_employees_odoo_employee_id_active",
            ["odoo_employee_id"],
            unique=True,
            sqlite_where=sa.text("active = 1"),
            postgresql_where=sa.text("active IS TRUE"),
        )
        batch_op.create_index(
            "uq_employees_telegram_user_id_active",
            ["telegram_user_id"],
            unique=True,
            sqlite_where=sa.text("active = 1"),
            postgresql_where=sa.text("active IS TRUE"),
        )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("file_id", sa.String(length=512), nullable=False),
        sa.Column("file_unique_id", sa.String(length=128), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "received", "qr_ok", "ai_processing", "needs_review", "ready",
                "odoo_created", "duplicate", "failed", "rejected",
                name="status", native_enum=False, length=20,
            ),
            nullable=False,
        ),
        sa.Column("method", sa.Enum("qr", "ai", "human", name="method", native_enum=False, length=20), nullable=True),
        sa.Column("qr_value", sa.Text(), nullable=True),
        sa.Column("doc_key", sa.String(length=512), nullable=True),
        sa.Column("extracted_data", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("odoo_record_id", sa.Integer(), nullable=True),
        sa.Column("odoo_record_name", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("chat_id", "message_id", "file_unique_id", name="uq_jobs_chat_message_file"),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_status_next_attempt_at", ["status", "next_attempt_at"], unique=False)
        batch_op.create_index(
            "uq_jobs_doc_key_active",
            ["doc_key"],
            unique=True,
            sqlite_where=sa.text(_DOC_KEY_ACTIVE),
            postgresql_where=sa.text(_DOC_KEY_ACTIVE),
        )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(none_as_null=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_audit_log_job_id_jobs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_audit_log_job_id_ts", ["job_id", "ts"], unique=False)


def downgrade() -> None:
    # Dropping a table drops its indexes.
    op.drop_table("audit_log")
    op.drop_table("jobs")
    op.drop_table("employees")
