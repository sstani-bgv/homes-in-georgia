"""Stage 4: every new ad is matched against the objects (app/services/matching.py).

listings.photo_hashes: the ingest's 64-bit dHash per photo (bot/ingest.py dhash), JSON list, so the matcher compares
photos without opening files.
listing_matches: "similar" candidates (ad, object) waiting for an operator: pending → confirmed (linked) / rejected.
property_listings.removed_checked_*: the operator checked the outside ad is taken down (exclusive conflict cleared).

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("listings", sa.Column("photo_hashes", sa.Text, nullable=False, server_default="[]"))
    op.add_column("property_listings", sa.Column("removed_checked_at", TS))
    op.add_column("property_listings", sa.Column("removed_checked_by", sa.Integer,
                                                 sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.create_table(
        "listing_matches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("listing_id", sa.Text, sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column("reasons", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("listing_id", "property_id", name="listing_matches_pair_uq"),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="listing_matches_status_check"),
    )


def downgrade() -> None:
    op.drop_table("listing_matches")
    op.drop_column("property_listings", "removed_checked_by")
    op.drop_column("property_listings", "removed_checked_at")
    op.drop_column("listings", "photo_hashes")
