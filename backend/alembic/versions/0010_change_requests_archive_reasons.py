"""Stage 6: field locks after verification, operator change requests, archive reasons.

properties.verified_at: the first operator approve / publish. Backfill: published objects (published_at, else reviewed_at,
else updated_at).
properties.archive_reason / archive_comment / archived_at. Backfill for archived objects from their latest archive event
(owner_refused → owner_refused, owner_rented_self → rented_by_owner, else other) and for rented ones (rented_by_us).
change_requests: «Запрос оператору».

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


# also run by tests/test_change_requests.py against seeded rows
BACKFILL = (
    """UPDATE properties SET verified_at = COALESCE(published_at, reviewed_at, updated_at)
       WHERE verified_at IS NULL AND (published_at IS NOT NULL OR status IN ('published', 'rented'))""",
    """UPDATE properties p SET archived_at = COALESCE(e.at, p.updated_at),
           archive_reason = CASE e.action WHEN 'owner_refused' THEN 'owner_refused'
                                          WHEN 'owner_rented_self' THEN 'rented_by_owner' ELSE 'other' END,
           archive_comment = NULLIF(e.data->>'comment', '')
       FROM properties p2 LEFT JOIN LATERAL (
           SELECT action, at, data FROM events
           WHERE entity = 'property' AND entity_id = p2.id::text AND data->>'status' = 'archived'
           ORDER BY at DESC, id DESC LIMIT 1) e ON true
       WHERE p.id = p2.id AND p.status = 'archived' AND p.archive_reason IS NULL""",
    "UPDATE properties SET archive_reason = 'rented_by_us' WHERE status = 'rented' AND archive_reason IS NULL",
)


def upgrade() -> None:
    for name, type_ in (("verified_at", TS), ("archive_reason", sa.Text), ("archive_comment", sa.Text), ("archived_at", TS)):
        op.add_column("properties", sa.Column(name, type_))
    op.create_table(
        "change_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", TS),
        sa.Column("decision_comment", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("change_requests_status_idx", "change_requests", ["status"])
    op.create_index("change_requests_entity_idx", "change_requests", ["entity", "entity_id"])

    for sql in BACKFILL:
        op.execute(sql)


def downgrade() -> None:
    op.drop_table("change_requests")
    for name in ("archived_at", "archive_comment", "archive_reason", "verified_at"):
        op.drop_column("properties", name)
