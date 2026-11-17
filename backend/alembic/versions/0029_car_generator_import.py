"""cars: the car post generator's fields and the import from the car channels (CarsSaleInGeorgia / CarsRentInGeorgia).

post_fields — what the generator needs beyond the columns (engine, HP, drive, colour, options, condition, VIN, insurance,
extra services...); source — crm (typed by an agent) | telegram (read from a car channel, services/car_import.py);
tg_channel + tg_message_id — the channel post a car came from (or was published to), the importer's key;
source_text — the channel post's text as read (an edit in the channel re-parses the car).

Revision ID: 0029
Revises: 0028
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0029'
down_revision: Union[str, Sequence[str], None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cars", sa.Column("post_fields", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("cars", sa.Column("source", sa.Text, nullable=False, server_default="crm"))
    op.add_column("cars", sa.Column("tg_channel", sa.Text))
    op.add_column("cars", sa.Column("tg_message_id", sa.Integer))
    op.add_column("cars", sa.Column("source_text", sa.Text))
    op.create_index("cars_tg_post_idx", "cars", ["tg_channel", "tg_message_id"], unique=True)


def downgrade() -> None:
    op.drop_index("cars_tg_post_idx", table_name="cars")
    for c in ("source_text", "tg_message_id", "tg_channel", "source", "post_fields"):
        op.drop_column("cars", c)
