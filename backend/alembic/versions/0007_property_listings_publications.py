"""The object (property) is the main entity: listings and our publications attach to it.

property_listings: every outside ad (ss, myhome, fb, old TG post) of one real flat. properties.listing_id stays as the
"origin" listing; the backfill links it here.
property_publications: where we published the object (channel is free text, only 'tg' for now; site/IG come later
without a migration). Backfilled from properties.post_url / post_message_id.
channel_posts.property_id: agency channel posts matched exactly to an object by the post link. Unmatched posts stay as
they are (stage 3 imports them as objects).

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "property_listings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("listing_id", sa.Text, sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("source", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("first_seen_at", TS),
        sa.Column("last_seen_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "property_publications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("channel", sa.Text, nullable=False, server_default="tg"),
        sa.Column("status", sa.Text, nullable=False, server_default="published"),
        sa.Column("url", sa.Text),
        sa.Column("message_id", sa.Text),
        sa.Column("published_at", TS),
        sa.Column("removed_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft', 'published', 'removed')", name="property_publications_status_check"),
    )
    op.add_column("channel_posts", sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="SET NULL")))
    op.create_index("ix_channel_posts_property_id", "channel_posts", ["property_id"])

    # listings keep ISO TEXT timestamps (bot schema); a malformed one just stays NULL
    op.execute("""
        INSERT INTO property_listings (property_id, listing_id, source, url, first_seen_at, last_seen_at)
        SELECT p.id, p.listing_id, p.source_type, p.source_url,
               CASE WHEN l.first_seen ~ '^\\d{4}-\\d{2}-\\d{2}' THEN l.first_seen::timestamptz END,
               CASE WHEN l.last_seen ~ '^\\d{4}-\\d{2}-\\d{2}' THEN l.last_seen::timestamptz END
        FROM properties p JOIN listings l ON l.id = p.listing_id
        ON CONFLICT (listing_id) DO NOTHING""")
    op.execute("""
        INSERT INTO property_publications (property_id, channel, status, url, message_id, published_at)
        SELECT id, 'tg', 'published', post_url, post_message_id, COALESCE(published_at, updated_at)
        FROM properties WHERE post_url IS NOT NULL OR post_message_id IS NOT NULL""")
    op.execute("""
        UPDATE channel_posts c SET property_id = pp.property_id
        FROM property_publications pp WHERE pp.channel = 'tg' AND pp.url IS NOT NULL AND c.link = pp.url""")


def downgrade() -> None:
    op.drop_index("ix_channel_posts_property_id", table_name="channel_posts")
    op.drop_column("channel_posts", "property_id")
    op.drop_table("property_publications")
    op.drop_table("property_listings")
