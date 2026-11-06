"""pgvector + property embedding columns for similar-object search (app/embeddings.py).

768 = multilingual SigLIP (bot/embed.py, google/siglip-base-patch16-256-multilingual): its text and image towers share
one space, so property text/photos compare with each other and with the bot's catalog vectors.
Needs the pgvector extension in the server (deploy: pgvector/pgvector:pg16 image, see docs/DEPLOY.md).

Revision ID: 0006
Revises: 0005
"""
from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
    # qualified by the extension's schema: the bot tests migrate with search_path = their own schema only
    s = op.get_bind().scalar(text("SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'vector'"))
    op.execute(f"ALTER TABLE properties ADD COLUMN text_embedding {s}.vector({DIM}), "
               f"ADD COLUMN photo_embedding {s}.vector({DIM})")
    for col in ("text_embedding", "photo_embedding"):
        op.execute(f"CREATE INDEX properties_{col}_idx ON properties USING hnsw ({col} {s}.vector_cosine_ops)")


def downgrade() -> None:
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS text_embedding, DROP COLUMN IF EXISTS photo_embedding")
