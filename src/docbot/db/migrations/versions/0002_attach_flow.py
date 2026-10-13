"""attach flow: sender/file/model columns, notifier outbox, review question id; odoo_created -> attached

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sender_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("file_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("odoo_model", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("notify_pending", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column("review_message_id", sa.BigInteger(), nullable=True))
        batch_op.create_index(
            "ix_jobs_notify_pending",
            ["id"],
            unique=False,
            sqlite_where=sa.text("notify_pending = 1"),
            postgresql_where=sa.text("notify_pending IS TRUE"),
        )
        batch_op.create_index("ix_jobs_review_message_id", ["review_message_id"], unique=False)

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_audit_log_event_ts", ["event", "ts"], unique=False)

    # The final status was renamed when the scope became "attach to an existing record".
    op.execute("UPDATE jobs SET status = 'attached' WHERE status = 'odoo_created'")
    op.execute("UPDATE audit_log SET from_status = 'attached' WHERE from_status = 'odoo_created'")
    op.execute("UPDATE audit_log SET to_status = 'attached' WHERE to_status = 'odoo_created'")


def downgrade() -> None:
    op.execute("UPDATE audit_log SET to_status = 'odoo_created' WHERE to_status = 'attached'")
    op.execute("UPDATE audit_log SET from_status = 'odoo_created' WHERE from_status = 'attached'")
    op.execute("UPDATE jobs SET status = 'odoo_created' WHERE status = 'attached'")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_log_event_ts")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_review_message_id")
        batch_op.drop_index("ix_jobs_notify_pending")
        batch_op.drop_column("review_message_id")
        batch_op.drop_column("notify_pending")
        batch_op.drop_column("odoo_model")
        batch_op.drop_column("file_name")
        batch_op.drop_column("sender_name")
