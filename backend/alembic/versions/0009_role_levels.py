"""Hierarchical roles (Irakli 11-12): superagent → superadmin + users.works_as_agent («+ как агент»)

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("works_as_agent", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.execute("UPDATE users SET role = 'superadmin', works_as_agent = true WHERE role = 'superagent'")
    op.drop_constraint("users_role_check", "users", type_="check")
    op.create_check_constraint("users_role_check", "users", "role IN ('superadmin', 'admin', 'operator', 'agent', 'recruit')")


def downgrade() -> None:
    op.drop_constraint("users_role_check", "users", type_="check")
    op.execute("UPDATE users SET role = 'superagent' WHERE role = 'superadmin' AND works_as_agent")
    op.create_check_constraint("users_role_check", "users",
                               "role IN ('superadmin', 'superagent', 'admin', 'operator', 'agent', 'recruit')")
    op.drop_column("users", "works_as_agent")
