"""Remove the obsolete client catalog mode; eligibility is now mandatory.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM settings WHERE key = 'client_catalog'")


def downgrade() -> None:
    op.execute("INSERT INTO settings (key, value) VALUES ('client_catalog', 'properties') ON CONFLICT (key) DO NOTHING")
