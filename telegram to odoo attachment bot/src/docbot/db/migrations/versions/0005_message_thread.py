"""jobs.message_thread_id: in a forum group the reply goes to the photo's own topic

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("message_thread_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("message_thread_id")
