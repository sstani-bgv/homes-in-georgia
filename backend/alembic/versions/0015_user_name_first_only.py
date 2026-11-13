"""users.name = the first name only: the CRM shows it everywhere, the full name only on the agent's card (Irakli 11-13)

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET name = btrim(first_name) WHERE coalesce(btrim(first_name), '') <> ''")


def downgrade() -> None:
    op.execute("UPDATE users SET name = btrim(concat_ws(' ', first_name, last_name)) WHERE coalesce(btrim(first_name), '') <> ''")
