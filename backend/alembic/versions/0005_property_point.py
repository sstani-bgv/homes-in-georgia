"""properties.lat / properties.lng: the address point the agent confirmed on the map (AddressField, OSM)

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("properties", sa.Column("lng", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "lng")
    op.drop_column("properties", "lat")
