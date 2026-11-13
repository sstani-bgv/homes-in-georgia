"""contract_signers: electronic signing of a contract by its parties (owner | tenant), one link each

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "contract_signers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("contract_id", sa.Integer, sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("token", sa.Text, nullable=False, unique=True),
        sa.Column("doc_sha256", sa.Text, nullable=False),
        sa.Column("full_name", sa.Text), sa.Column("personal_id", sa.Text), sa.Column("phone", sa.Text),
        sa.Column("signature", sa.Text), sa.Column("lang", sa.Text), sa.Column("ip", sa.Text), sa.Column("user_agent", sa.Text),
        sa.Column("signed_at", ts),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('owner', 'tenant')", name="contract_signers_role_check"),
        sa.UniqueConstraint("contract_id", "role", name="contract_signers_role_uq"),
    )
    op.create_index("contract_signers_contract_idx", "contract_signers", ["contract_id"])


def downgrade() -> None:
    op.drop_table("contract_signers")
