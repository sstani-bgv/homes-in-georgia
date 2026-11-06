"""Stages 8 + 10: what each client was shown (kept for good), owner-first fast track of an object.

client_views: one row per card the client bot sent (search results, «Ещё», the owner-first push); clients.state.shown is
still the per-request list the bot filters by.
properties.fast_track_at / fast_track_by: the agent pressed «Собственник согласен показать клиентам»: the object goes to
clients with the ad's photos before the shoot and the operator's publication.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "client_views",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("listing_id", sa.Text, sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="SET NULL")),
        sa.Column("search_id", sa.Integer, sa.ForeignKey("searches.id", ondelete="SET NULL")),
        sa.Column("source", sa.Text, nullable=False, server_default="search"),  # search | push
        sa.Column("shown_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("client_views_client_idx", "client_views", ["client_id", "shown_at"])
    op.create_index("client_views_listing_idx", "client_views", ["listing_id"])
    op.add_column("properties", sa.Column("fast_track_at", TS))
    op.add_column("properties", sa.Column("fast_track_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")))


def downgrade() -> None:
    op.drop_column("properties", "fast_track_by")
    op.drop_column("properties", "fast_track_at")
    op.drop_table("client_views")
