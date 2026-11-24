"""dr_apartments.report_lang: the owner's reports in Russian, English or Georgian (ru | en | ka) — the language the
report card opens in and the monthly report on the 1st goes out in (services/daily_rent.T).

Revision ID: 0038
Revises: 0037
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0038'
down_revision: Union[str, Sequence[str], None] = '0037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dr_apartments", sa.Column("report_lang", sa.Text(), nullable=False, server_default="ru"))
    op.create_check_constraint("dr_apartments_report_lang_check", "dr_apartments", "report_lang IN ('ru', 'en', 'ka')")


def downgrade() -> None:
    op.drop_constraint("dr_apartments_report_lang_check", "dr_apartments")
    op.drop_column("dr_apartments", "report_lang")
