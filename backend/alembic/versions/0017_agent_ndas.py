"""agent_ndas: a new agent's NDA signed electronically (company signature + the agent's, confirmed with Telegram)

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0017'
down_revision: Union[str, Sequence[str], None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    txt = lambda n, **kw: sa.Column(n, sa.Text, **kw)  # noqa: E731
    op.create_table(
        "agent_ndas",
        sa.Column("id", sa.Integer, primary_key=True),
        txt("lang", nullable=False), txt("status", nullable=False, server_default="sent"), txt("token", nullable=False, unique=True),
        txt("full_name", nullable=False), txt("personal_id", nullable=False), txt("phone"), txt("telegram"), txt("role"),
        txt("html", nullable=False), txt("doc_sha256", nullable=False),
        txt("company_signature"), sa.Column("company_signed_at", ts),
        txt("tg_id"), txt("tg_username"), txt("tg_name"), sa.Column("tg_auth_date", ts), txt("tg_hash"),
        txt("signature"), txt("ip"), txt("user_agent"), sa.Column("signed_at", ts), txt("pdf_path"), txt("drive_file_id"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()), sa.Column("cancelled_at", ts),
        sa.CheckConstraint("status IN ('sent', 'signed', 'cancelled')", name="agent_ndas_status_check"),
        sa.CheckConstraint("lang IN ('KA', 'KA-RU', 'KA-EN')", name="agent_ndas_lang_check"),
    )
    op.create_index("agent_ndas_created_idx", "agent_ndas", ["created_at"])


def downgrade() -> None:
    op.drop_table("agent_ndas")
