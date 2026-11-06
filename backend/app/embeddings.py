"""Property vectors for similar-object search: properties.text_embedding / photo_embedding (pgvector, migration 0006).

The encoder is the bot's multilingual SigLIP (bot/embed.py), so property and catalog vectors share one space. It needs
torch + transformers (bot/requirements-embed.txt; not in deploy/Dockerfile yet); without them everything is a no-op.

CLI:  python -m app.embeddings backfill          # properties that have no text vector yet
      python -m app.embeddings backfill --all    # re-embed every property
"""
import sys
import tempfile

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import storage
from app.config import REPO_ROOT
from app.models import Property, PropertyMedia

DIM = 768


def encoder():
    """bot/embed.py, or None when torch/transformers are not installed."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return None
    if str(REPO_ROOT / "bot") not in sys.path:
        sys.path.append(str(REPO_ROOT / "bot"))
    import embed
    return embed


def property_text(p: Property) -> str:
    # SigLIP reads only ~64 tokens: the description first
    return " ".join(str(x) for x in (p.description, p.district, p.address) if x).strip()


def _vec(v):
    return None if v is None else "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


def embed_property(db: Session, p: Property, enc=None) -> bool:
    """Computes and stores both vectors of one property (photo = normalized mean of its photos). False = no encoder."""
    import numpy as np
    enc = enc or encoder()
    if enc is None:
        return False
    t = property_text(p)
    tv = enc.encode_texts([t])[0] if t else None
    keys = db.scalars(select(PropertyMedia.path).where(PropertyMedia.property_id == p.id, PropertyMedia.kind == "photo")
                      .order_by(PropertyMedia.position)).all()
    with tempfile.TemporaryDirectory() as tmp:
        paths = [f for k in keys if (f := storage.fetch(k, tmp))]
        img = np.asarray(enc.encode_images(paths)) if paths else None
    pv = None
    if img is not None and len(img):
        m = img.mean(0)
        pv = m / max(float(np.linalg.norm(m)), 1e-9)
    db.execute(text("UPDATE properties SET text_embedding = CAST(:t AS vector), photo_embedding = CAST(:p AS vector) "
                    "WHERE id = :id"), {"t": _vec(tv), "p": _vec(pv), "id": p.id})
    db.commit()
    return True


def backfill(all_: bool = False) -> int:
    from app.db import SessionLocal
    enc = encoder()
    if enc is None:
        print("embeddings: torch/transformers are not installed (bot/requirements-embed.txt), nothing done")
        return 0
    with SessionLocal() as db:
        ids = db.scalars(text("SELECT id FROM properties" + ("" if all_ else " WHERE text_embedding IS NULL") +
                              " ORDER BY id")).all()
        for pid in ids:
            embed_property(db, db.get(Property, pid), enc)
    print(f"embeddings: {len(ids)} properties embedded")
    return len(ids)


if __name__ == "__main__":
    if sys.argv[1:2] != ["backfill"]:
        sys.exit(__doc__)
    backfill(all_="--all" in sys.argv)
