"""The bot's client catalog = CRM objects (Stanislav 2026-11-10).

Every published object has one catalog row in `listings`: id `obj<property id>`, source `object`, rebuilt from the
object (its fields, the post the CRM generator makes from it, its photos and video in the storage). The bot searches
only these rows (bot/core.py CLIENT_SEARCH). Scraped ads never reach the search: a client sees one only when the agent
fast-tracks its object and the CRM pushes it to the matching clients (services/demand.py).

The catalog row is a copy kept in sync, never edited by hand: `sync(db, p)` on publish, `sync_all` in the worker
(every tick: rewrites only rows that changed, retires rows whose object left the base)."""
import json
import re
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.models import Listing, Property, PropertyMedia

PREFIX = "obj"
SOURCE = "object"
MEDIA = "s3:"  # catalog photo refs: s3:<storage key> (bot/core.album and /api/listings/<id>/photo resolve them)
ALBUM = 10


def catalog_id(pid: int) -> str:
    return f"{PREFIX}{pid}"


def property_id_of(listing_id: str | None) -> int | None:
    m = re.fullmatch(rf"{PREFIX}(\d+)", listing_id or "")
    return int(m[1]) if m else None


def property_for(db: Session, listing_id: str | None) -> Property | None:
    """The object behind a listing the client saw: its catalog row, else the ad it was taken from."""
    if not listing_id:
        return None
    if (pid := property_id_of(listing_id)) is not None:
        return db.get(Property, pid)
    return db.scalar(select(Property).where(Property.listing_id == listing_id))


def visible(p: Property) -> bool:
    """In the client catalog: published with its own channel post (the same rule the bot always had)."""
    return p.status == "published" and bool((p.post_url or "").strip()) and bool((p.post_message_id or "").strip())


def _facts():
    if str(REPO_ROOT / "bot") not in sys.path:
        sys.path.append(str(REPO_ROOT / "bot"))
    import facts
    return facts


def fields(db: Session, p: Property) -> dict:
    """The catalog row for an object: the card facts parsed from its generated post (the agency format the bot's card
    is made of), the object's own fields on top, photos first then one video from the object's media."""
    from app.services import properties as P
    post = P.post_text(db, p)
    d = _facts().parse_agency(post)
    for k in ("price_usd", "area", "floor", "bedrooms", "commission"):
        d.pop(k, None)
    if p.total_floors:
        d["floors"] = p.total_floors
    src = db.get(Listing, p.listing_id) if p.listing_id else None  # the ad it was taken from: the map pin
    media = db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == p.id)
                       .order_by(PropertyMedia.position, PropertyMedia.id)).all()
    photos = [MEDIA + m.path for m in media if m.kind == "photo"][:ALBUM]
    video = next((m for m in media if m.kind == "video"), None)
    if video:
        d["videos"] = [{"key": video.path}]
    return {
        "status": "published" if visible(p) else "gone", "source": SOURCE, "owner_id": "agency",
        "urls": json.dumps([p.post_url] if p.post_url else []),
        "price_usd": int(p.price) if p.price is not None else None, "area": float(p.area) if p.area is not None else None,
        "floor": p.floor, "bedrooms": p.bedrooms, "address": p.address or "", "district": p.district or "",
        "district_en": d.get("district_en") or p.district, "lat": src.lat if src else None,
        "lng": src.lng if src else None, "geo_precision": src.geo_precision if src else None,
        "photos": json.dumps(photos), "features": json.dumps(d.get("yes") or []),
        "renovation": d.get("condition"), "text": post, "details": json.dumps(d, ensure_ascii=False),
    }


SYNCED = ("status", "urls", "price_usd", "area", "floor", "bedrooms", "address", "district", "district_en", "lat", "lng",
          "geo_precision", "photos", "features", "renovation", "text", "details")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync(db: Session, p: Property) -> bool:
    """Upsert the object's catalog row (retired to 'gone' when the object left the base). -> changed. No commit."""
    row = db.get(Listing, catalog_id(p.id))
    if row is None and not visible(p):
        return False
    new = fields(db, p)
    if row is None:
        db.add(Listing(id=catalog_id(p.id), first_seen=_now(), last_seen=_now(), updated_at=_now(), **new))
        return True
    if all(getattr(row, k) == new[k] for k in SYNCED):
        return False
    for k, v in new.items():
        setattr(row, k, v)
    row.updated_at = row.last_seen = _now()
    return True


def sync_all(db: Session) -> int:
    """Every published object and every existing catalog row (its object may have left the base). -> rows changed."""
    ids = set(db.scalars(select(Property.id).where(Property.status == "published")))
    ids |= {pid for lid in db.scalars(select(Listing.id).where(Listing.source == SOURCE, Listing.status != "gone"))
            if (pid := property_id_of(lid)) is not None}
    n = 0
    for pid in sorted(ids):
        if (p := db.get(Property, pid)) is not None:
            n += sync(db, p)
    for lid in db.scalars(select(Listing.id).where(Listing.source == SOURCE, Listing.status != "gone")):
        if db.get(Property, property_id_of(lid)) is None:  # the object was deleted
            db.get(Listing, lid).status = "gone"
            n += 1
    db.commit()
    return n


if __name__ == "__main__":
    from app.db import SessionLocal
    with SessionLocal() as s:
        print({"changed": sync_all(s)})
