"""Stage 9 rework: viewing slots go to the client from the operator's account as the bot's inline result.

leads.slots_token: a short random id of the lead's current slot set (services.leads.add_slots). The operator's userbot
sends the inline query «slots:<token>»; the bot resolves it (only for the userbot's Telegram id) into the slot buttons.

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
    op.add_column("leads", sa.Column("slots_token", sa.Text))
    op.create_index("leads_slots_token_key", "leads", ["slots_token"], unique=True)


def downgrade() -> None:
    op.drop_index("leads_slots_token_key", "leads")
    op.drop_column("leads", "slots_token")
