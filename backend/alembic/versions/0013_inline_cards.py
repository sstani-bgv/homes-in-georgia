"""Stage 9 rework: everything but the listing cards reaches the client from the operator's account.

inline_cards: a short random token per client card (kind slots | remind | survey | reason | t14; ref = the lead id,
for t14 the deal id). The operator's userbot sends the inline query «<kind>:<token>»; the bot resolves it (only for the
userbot's Telegram id) into the card with its buttons. A new card of the same kind/ref replaces the old token.

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inline_cards",
        sa.Column("token", sa.Text, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("ref", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("inline_cards_kind_ref_idx", "inline_cards", ["kind", "ref"])


def downgrade() -> None:
    op.drop_table("inline_cards")
