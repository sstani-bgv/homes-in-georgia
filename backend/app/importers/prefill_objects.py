"""Fill the empty generator fields of existing objects from their ads (new objects get it on take).

    python -m app.importers.prefill_objects

Idempotent: only empty fields are filled, what an agent picked stays."""
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Listing, Property
from app.services import properties as P


def run(db) -> dict:
    n = filled = 0
    for p in db.scalars(select(Property).where(Property.listing_id.isnot(None), Property.status != "archived")):
        n += 1
        filled += P.prefill(p, db.get(Listing, p.listing_id))
    db.commit()
    return {"objects": n, "filled": filled}


if __name__ == "__main__":
    with SessionLocal() as db:
        print(run(db))
