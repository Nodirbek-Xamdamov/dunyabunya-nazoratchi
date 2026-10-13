"""jobs.media_group_id and indexes to find the other pages of a multi-page document

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("media_group_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_jobs_chat_id_media_group_id", ["chat_id", "media_group_id"], unique=False)
        batch_op.create_index(
            "ix_jobs_chat_id_telegram_user_id_created_at", ["chat_id", "telegram_user_id", "created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_chat_id_telegram_user_id_created_at")
        batch_op.drop_index("ix_jobs_chat_id_media_group_id")
        batch_op.drop_column("media_group_id")
