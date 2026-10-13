"""employee registration: phone, department, source; odoo_employee_id optional (admin-approved people)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phone", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("department", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("source", sa.String(length=16), nullable=True))
        batch_op.alter_column("odoo_employee_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM employees WHERE odoo_employee_id IS NULL")
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.alter_column("odoo_employee_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("source")
        batch_op.drop_column("department")
        batch_op.drop_column("phone")
