"""properties: rent_from / rent_to / tenant_* (an object rented outside the CRM's deals, typed by hand)

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLS = (("rent_from", sa.Date()), ("rent_to", sa.Date()), ("tenant_name", sa.Text()), ("tenant_phone", sa.Text()),
        ("tenant_tg", sa.Text()))


def upgrade() -> None:
    for name, kind in COLS:
        op.add_column("properties", sa.Column(name, kind, nullable=True))
    op.create_index("properties_rent_to_idx", "properties", ["rent_to"], postgresql_where=sa.text("rent_to IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("properties_rent_to_idx", table_name="properties")
    for name, _ in reversed(COLS):
        op.drop_column("properties", name)
