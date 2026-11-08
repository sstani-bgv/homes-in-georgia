"""Agency channel posts → published objects (the verified base), so clients get objects only.

    python -m app.importers.channel_objects

Temporary bridge for the staging test (2026-11-08): every live agency-channel listing (text + photos from the parser)
becomes a published, verified Property; the agent comes from the Google Sheets post log (channel_posts by message id),
the owner from Owner_Base by a phone in the post text. Idempotent: a listing that already has an object is skipped.
# ponytail: only the ~100 posts the web parser sees; the full archive needs a Telegram-account fetch of the channel.
"""
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ChannelPost, Listing, Owner, Property
from app.services import properties as P
from app.services.common import jload, log_event, now
from app.services.dedup import extract_phones, norm_phone


def _owner(db: Session, text: str | None) -> Owner | None:
    for ph in extract_phones(text or ""):
        n = norm_phone(ph)
        if len(n) >= 9:
            o = db.scalar(select(Owner).where(Owner.phone_norm.like(f"%{n[-9:]}")).limit(1))
            if o:
                return o
    return None


def run(db: Session) -> dict:
    counts = {"created": 0, "skipped": 0, "with_agent": 0, "with_owner": 0}
    rows = db.scalars(select(Listing).where(Listing.source == "agency", Listing.status == "published")).all()
    for l in rows:
        if db.scalar(select(Property.id).where(Property.listing_id == l.id)):
            counts["skipped"] += 1
            continue
        mid = l.id[2:] if l.id.startswith("tg") else l.id
        post = db.scalar(select(ChannelPost).where(ChannelPost.message_id == mid))
        owner = _owner(db, l.text)
        url = (jload(l.urls, []) or [None])[0]
        t = now()
        p = Property(listing_id=l.id, source_type="channel", source_url=url, source_external_id=l.id,
                     address=l.address, district=l.district, price=l.price_usd, bedrooms=l.bedrooms, area=l.area,
                     floor=l.floor, description=l.description_ru or l.text, status="published",
                     post_url=url, post_message_id=mid, published_at=t, verified_at=t,
                     agent_id=post.agent_id if post else None, owner_id=owner.id if owner else None,
                     is_exclusive=bool(post and post.is_exclusive))
        P.prefill(p, l)
        db.add(p)
        db.flush()
        P.link_listing(db, p, l)
        if post:
            post.property_id = p.id
        log_event(db, "property", p.id, "imported_from_channel", None, listing_id=l.id)
        counts["created"] += 1
        counts["with_agent"] += bool(p.agent_id)
        counts["with_owner"] += bool(p.owner_id)
    db.commit()
    return counts


def main(argv=None):
    with SessionLocal() as db:
        print(run(db))


if __name__ == "__main__":
    main(sys.argv[1:])
