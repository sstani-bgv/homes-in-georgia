"""users.role: add 'superagent' (superadmin permissions + a working agent)

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("users_role_check", "users", type_="check")
    op.create_check_constraint("users_role_check", "users",
                               "role IN ('superadmin', 'superagent', 'admin', 'operator', 'agent', 'recruit')")


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'superadmin' WHERE role = 'superagent'")
    op.drop_constraint("users_role_check", "users", type_="check")
    op.create_check_constraint("users_role_check", "users", "role IN ('superadmin', 'admin', 'operator', 'agent', 'recruit')")
