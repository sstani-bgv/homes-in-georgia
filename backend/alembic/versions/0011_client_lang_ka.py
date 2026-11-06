"""Georgian client language: clients.lang may be ka.

Revision ID: 0011
Revises: 0010
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("clients_lang_check", "clients", type_="check")
    op.create_check_constraint("clients_lang_check", "clients", "lang IN ('ru', 'en', 'ka')")


def downgrade() -> None:
    op.execute("UPDATE clients SET lang = 'en' WHERE lang = 'ka'")
    op.drop_constraint("clients_lang_check", "clients", type_="check")
    op.create_check_constraint("clients_lang_check", "clients", "lang IN ('ru', 'en')")
