"""role_permissions: the superadmin's Ч/С/И/У matrix per role and section (services/perms.py), only changed cells;
custom_roles made on the basis of a ladder role, users.custom_role_id.

Revision ID: 0037
Revises: 0036
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0037'
down_revision: Union[str, Sequence[str], None] = '0036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("role_permissions",
                    sa.Column("role", sa.Text(), primary_key=True),
                    sa.Column("section", sa.Text(), primary_key=True),
                    sa.Column("actions", sa.Text(), nullable=False, server_default=""))
    op.create_table("custom_roles",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("label", sa.Text(), nullable=False),
                    sa.Column("base", sa.Text(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                    sa.CheckConstraint("base IN ('recruit', 'agent', 'operator', 'admin')", name="custom_roles_base_check"))
    op.add_column("users", sa.Column("custom_role_id", sa.Integer(), sa.ForeignKey("custom_roles.id", ondelete="SET NULL")))


def downgrade() -> None:
    op.drop_column("users", "custom_role_id")
    op.drop_table("custom_roles")
    op.drop_table("role_permissions")
