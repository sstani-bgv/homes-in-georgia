"""Staff invites (Stanislav 11-12): a one-time link binds the Telegram account that opens it to a new user

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.Text, nullable=False, unique=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("level", sa.Integer),
        sa.Column("agent_tag", sa.Text),
        sa.Column("districts", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("works_as_agent", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.Text),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.CheckConstraint("role IN ('superadmin', 'admin', 'operator', 'agent', 'recruit')", name="invites_role_check"),
    )


def downgrade() -> None:
    op.drop_table("invites")
