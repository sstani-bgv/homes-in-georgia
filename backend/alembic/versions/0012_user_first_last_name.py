"""Employee name split into first_name / last_name (Stanislav 11-12); users.name stays as «first last»

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.Text))
    op.add_column("users", sa.Column("last_name", sa.Text))
    # split at the first space: «Заза Беридзе» -> Заза / Беридзе, «Заза» -> Заза / NULL
    op.execute("""UPDATE users SET first_name = split_part(btrim(name), ' ', 1),
                  last_name = nullif(btrim(substr(btrim(name), length(split_part(btrim(name), ' ', 1)) + 1)), '')""")


def downgrade() -> None:
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
