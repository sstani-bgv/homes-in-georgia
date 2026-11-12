"""Contract drafts in the deal: the wizard autosaves a partly filled contract (no html / PDF yet, kind / lang may be
unset), regeneration keeps the previous PDFs (versions), generated_at tells «PDF устарел» after a later edit

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in ("kind", "lang", "html"):
        op.alter_column("contracts", col, nullable=True)
    op.add_column("contracts", sa.Column("versions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("contracts", sa.Column("generated_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE contracts SET generated_at = updated_at WHERE pdf_path IS NOT NULL")
    op.create_index("contracts_deal_idx", "contracts", ["deal_id"])


def downgrade() -> None:
    op.drop_index("contracts_deal_idx", "contracts")
    op.drop_column("contracts", "generated_at")
    op.drop_column("contracts", "versions")
    op.execute("DELETE FROM contracts WHERE html IS NULL OR kind IS NULL OR lang IS NULL")  # unfinished drafts
    for col in ("kind", "lang", "html"):
        op.alter_column("contracts", col, nullable=False)
