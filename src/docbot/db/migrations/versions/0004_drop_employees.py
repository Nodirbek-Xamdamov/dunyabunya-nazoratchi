"""drop employees: files are attached whoever sends them (the employee registration was removed)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("employees")


def downgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("odoo_employee_id", sa.Integer(), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.create_index(
            "uq_employees_odoo_employee_id_active", ["odoo_employee_id"], unique=True,
            sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active IS TRUE"),
        )
        batch_op.create_index(
            "uq_employees_telegram_user_id_active", ["telegram_user_id"], unique=True,
            sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active IS TRUE"),
        )
