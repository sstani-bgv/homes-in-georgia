"""leads.status: add 'contract' («Договор»: after the viewing, before the deal); contracts.data: the wizard's fields

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = ("new", "operator_taken", "agent_assigned", "slots_sent", "time_chosen", "viewing_done",
       "deal_pending", "rented", "refused", "thinking", "in_work", "viewing")
NEW = OLD[:6] + ("contract",) + OLD[6:]


def upgrade() -> None:
    op.drop_constraint("leads_status_check", "leads", type_="check")
    op.create_check_constraint("leads_status_check", "leads", f"status IN {NEW}")
    op.add_column("contracts", sa.Column("data", postgresql.JSONB))  # CxData: the deal page prefills from it


def downgrade() -> None:
    op.drop_column("contracts", "data")
    op.execute("UPDATE leads SET status = 'deal_pending' WHERE status = 'contract'")
    op.drop_constraint("leads_status_check", "leads", type_="check")
    op.create_check_constraint("leads_status_check", "leads", f"status IN {OLD}")
