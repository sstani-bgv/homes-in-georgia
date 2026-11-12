"""listing_matches.status 'address': «В архиве / в базе по этому адресу» hints stored when ads are parsed
(services.properties.store_similar), no operator decision

Revision ID: 0014
Revises: 0013
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('listing_matches_status_check', 'listing_matches')
    op.create_check_constraint('listing_matches_status_check', 'listing_matches',
                               "status IN ('pending', 'confirmed', 'rejected', 'address')")


def downgrade() -> None:
    op.execute("DELETE FROM listing_matches WHERE status = 'address'")
    op.drop_constraint('listing_matches_status_check', 'listing_matches')
    op.create_check_constraint('listing_matches_status_check', 'listing_matches',
                               "status IN ('pending', 'confirmed', 'rejected')")
