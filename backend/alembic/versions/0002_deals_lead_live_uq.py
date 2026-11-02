"""one live (non-cancelled) deal per lead

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # double-submitted pending duplicates: keep the confirmed one (or the oldest), cancel the other pending rows
    op.execute("""
        UPDATE deals d SET status = 'cancelled'
        WHERE d.status = 'pending' AND d.lead_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM deals o
            WHERE o.lead_id = d.lead_id AND o.id <> d.id AND o.status <> 'cancelled'
              AND (o.status = 'confirmed' OR o.id < d.id))
    """)
    op.create_index('deals_lead_live_uq', 'deals', ['lead_id'], unique=True,
                    postgresql_where=sa.text("status <> 'cancelled' AND lead_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index('deals_lead_live_uq', table_name='deals',
                  postgresql_where=sa.text("status <> 'cancelled' AND lead_id IS NOT NULL"))
