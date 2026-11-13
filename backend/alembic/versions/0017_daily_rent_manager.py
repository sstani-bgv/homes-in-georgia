"""Daily Rent manager role (Irakli 11-13): sees only «Посуточная аренда» (the Daily Rent / ApartFlow app) in the CRM.

Not part of the level ladder (role_level = 0): every CRM endpoint refuses it except /auth/me and /daily-rent/*
(api/deps.py current_user), so it can never see objects, leads, deals or people.

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0017'
down_revision: Union[str, Sequence[str], None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW = "role IN ('superadmin', 'admin', 'operator', 'agent', 'recruit', 'daily_rent_manager')"
OLD = "role IN ('superadmin', 'admin', 'operator', 'agent', 'recruit')"


def upgrade() -> None:
    for table, name in (("users", "users_role_check"), ("invites", "invites_role_check")):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, NEW)


def downgrade() -> None:
    op.execute("UPDATE invites SET revoked_at = now() WHERE role = 'daily_rent_manager' AND revoked_at IS NULL")
    op.execute("UPDATE invites SET role = 'recruit' WHERE role = 'daily_rent_manager'")
    op.execute("UPDATE users SET role = 'recruit', is_active = false WHERE role = 'daily_rent_manager'")
    for table, name in (("users", "users_role_check"), ("invites", "invites_role_check")):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, OLD)
