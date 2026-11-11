"""contracts: generated contracts with their PDF and lifecycle draft → signed → deal | closed_no_deal

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    user = lambda name: sa.Column(name, sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"))  # noqa: E731
    op.create_table(
        "contracts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("deal_id", sa.Integer, sa.ForeignKey("deals.id", ondelete="SET NULL")),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="SET NULL")),
        user("created_by"),
        sa.Column("kind", sa.Text, nullable=False), sa.Column("lang", sa.Text, nullable=False),
        sa.Column("title", sa.Text), sa.Column("cadastral", sa.Text), sa.Column("address", sa.Text),
        sa.Column("client_name", sa.Text), sa.Column("owner_name", sa.Text),
        sa.Column("html", sa.Text, nullable=False), sa.Column("pdf_path", sa.Text), sa.Column("signed_pdf_path", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("signed_at", ts), user("signed_by"),
        sa.Column("close_reason", sa.Text), sa.Column("close_comment", sa.Text), sa.Column("closed_at", ts), user("closed_by"),
        sa.Column("share_token", sa.Text, unique=True), sa.Column("share_expires_at", ts),
        sa.Column("drive_file_id", sa.Text),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft', 'signed', 'deal', 'closed_no_deal')", name="contracts_status_check"),
        sa.CheckConstraint("close_reason IS NULL OR close_reason IN ('deal_failed', 'client_changed_mind', 'owner_refused', 'other')",
                           name="contracts_close_reason_check"),
    )
    op.create_index("contracts_lead_idx", "contracts", ["lead_id"])
    op.create_index("contracts_created_by_idx", "contracts", ["created_by"])


def downgrade() -> None:
    op.drop_table("contracts")
