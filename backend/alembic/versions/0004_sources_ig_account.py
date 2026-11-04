"""sources.kind: ig_account (Instagram accounts read through Apify)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04 12:00:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('sources_kind_check', 'sources', type_='check')
    op.create_check_constraint('sources_kind_check', 'sources', "kind IN ('tg_agency', 'tg_competitor', 'fb_group', 'ig_account')")


def downgrade() -> None:
    op.execute("DELETE FROM sources WHERE kind = 'ig_account'")
    op.drop_constraint('sources_kind_check', 'sources', type_='check')
    op.create_check_constraint('sources_kind_check', 'sources', "kind IN ('tg_agency', 'tg_competitor', 'fb_group')")
