"""Stage 6.5: the agent's hashtag lives in the profile, the owner belongs to an agent; the object keeps the post fields.

users.agent_tag: the agent's post hashtag without '#' (Zaza, ArturB), unique case-insensitive. Filled from the
`agent_tags` setting ({"#Tag": "User name"}) where the name resolves to exactly one user; the setting stays as the
fallback for tags of people without an account.
owners.agent_id: the agent who brought the owner. Backfill: the agent of the owner's earliest object (agent contacts
excluded: a realtor's number is nobody's owner).
properties.post_fields: the channel post fields the old HTML generator had (metro, building, design, heating,
amenities, pets, periods, tenants, deposit, deal/sale price…), JSONB so Stage 10 matching can query metro/pets/periods.

Revision ID: 0009
Revises: 0008
"""
import json
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("agent_tag", sa.Text))
    op.create_index("users_agent_tag_uq", "users", [sa.text("lower(agent_tag)")], unique=True)
    op.add_column("owners", sa.Column("agent_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.create_index("owners_agent_idx", "owners", ["agent_id"])
    op.add_column("properties", sa.Column("post_fields", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))

    op.execute("""
        UPDATE owners o SET agent_id = s.agent_id FROM (
            SELECT DISTINCT ON (owner_id) owner_id, agent_id FROM properties
            WHERE owner_id IS NOT NULL AND agent_id IS NOT NULL ORDER BY owner_id, created_at, id) s
        WHERE o.id = s.owner_id AND NOT o.is_agent""")

    c = op.get_bind()
    raw = c.execute(sa.text("SELECT value FROM settings WHERE key = 'agent_tags'")).scalar()
    try:
        tags = json.loads(raw) if raw else {}
    except ValueError:
        tags = {}
    for tag, name in (tags.items() if isinstance(tags, dict) else []):
        tag = str(tag).lstrip("#").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,31}", tag) or not name:
            continue
        ids = c.execute(sa.text("SELECT id FROM users WHERE lower(trim(name)) = lower(trim(:n)) AND agent_tag IS NULL"),
                        {"n": str(name)}).scalars().all()
        taken = c.execute(sa.text("SELECT 1 FROM users WHERE lower(agent_tag) = lower(:t)"), {"t": tag}).scalar()
        if len(ids) == 1 and not taken:
            c.execute(sa.text("UPDATE users SET agent_tag = :t WHERE id = :i"), {"t": tag, "i": ids[0]})


def downgrade() -> None:
    op.drop_column("properties", "post_fields")
    op.drop_index("owners_agent_idx", table_name="owners")
    op.drop_column("owners", "agent_id")
    op.drop_index("users_agent_tag_uq", table_name="users")
    op.drop_column("users", "agent_tag")
