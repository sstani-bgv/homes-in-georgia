"""contracts.deal_due_at: both parties signed → the agent enters the deal within 24 h, after that his CRM is locked to
that deal (David 11-23, services/contracts.overdue_for). Null = no deadline (older contracts, or lifted by an operator).

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
    op.add_column("contracts", sa.Column("deal_due_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("contracts", "deal_due_at")
