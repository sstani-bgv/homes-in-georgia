"""deals.tg_ref: idempotency key of deals parsed from Telegram (#Rented in the deal topic / channel / forwards)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('deals', sa.Column('tg_ref', sa.Text(), nullable=True))
    op.create_index('deals_tg_ref_uq', 'deals', ['tg_ref'], unique=True, postgresql_where=sa.text('tg_ref IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('deals_tg_ref_uq', table_name='deals', postgresql_where=sa.text('tg_ref IS NOT NULL'))
    op.drop_column('deals', 'tg_ref')
