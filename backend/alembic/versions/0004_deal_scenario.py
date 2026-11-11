"""deals.scenario / deals.distribution: Molecula 90/10 scenario and the commission split rows

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("scenario", JSONB(), nullable=True))
    op.add_column("deals", sa.Column("distribution", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("deals", "distribution")
    op.drop_column("deals", "scenario")
