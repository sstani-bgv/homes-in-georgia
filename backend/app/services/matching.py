"""Stage 4: recognise a new ad as an object already in the base.

Verdict per (ad, object):
- same: the same link, the same cadastral code, 2+ shared photos (dHash), or the owner's phone AND (address or a shared
  photo) → the ad is linked to the object by itself (process(), bot/db.py sync calls it for every new listing), whatever
  floor and area the two say (David 11-17: 100 m² / 6th floor in the CRM, 110 m² / 5th in the ad, the same photos);
- similar: one shared photo or the owner's phone with a floor / area that roughly agree, or close descriptions (embeddings)
  → a listing_matches row («Похож на #N»): an operator compares the photos and presses «Тот же объект» / «Другой» in
  «Новые объявления» (decide()). Objects at the ad's address are address hints (properties.similar_in_base);
- none: nothing; an object is created as before when an agent takes the ad.
Photos: the ad's dHashes against the object's own photos (property_media.dhash, hashed on upload) and the ads linked
to it.
A newly linked outside ad of an exclusive object is an exclusive conflict: event + a task to the object's agent.

CLI (once after deploy; the bot's sync keeps both up to date afterwards):
    python -m app.services.matching hashes   # listings.photo_hashes for ads ingested before migration 0008
    python -m app.services.matching run      # match every live ad not linked yet (the ones that came before stage 4)
    python -m app.services.matching media    # property_media.dhash for object photos uploaded before migration 0030
    python -m app.services.matching rematch  # re-run the matcher on every live unlinked ad (after `media`)
"""
import io
import logging
import re
import sys
from types import SimpleNamespace

from sqlalchemy import delete, func, literal_column, or_, select, text
from sqlalchemy import update as update_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app import storage
from app.models import Listing, ListingMatch, Owner, Property, PropertyListing, PropertyMedia, PropertyPublication, User, at_least
from app.services import properties as P
from app.services.common import get_or_404, jdump, jload, log_event, now
from app.services.dedup import address_match, extract_phones, owner_phones, phone_match
from app.services.errors import DomainError, forbidden

log = logging.getLogger(__name__)

SAME, SIMILAR, NONE = "same", "similar", "none"
# ponytail: heuristic thresholds picked by eye, not measured on labelled pairs; tune once operators have decided a few
# hundred «похож» cards (listing_matches.status is the label)
SAME_PHOTOS = 2      # shared photos that make it the same flat (one shared photo can be a common building shot)
PHOTO_BITS = 4       # dHash distance of "the same photo" (bot/ingest.py same_photo: same 0-2 bits, different 8+)
U64 = (1 << 64) - 1
AREA_TOL = 0.10      # area difference for "similar", share of the larger one
TEXT_SIM = 0.92      # cosine similarity of the description embeddings for "similar"
MAX_CANDIDATES = 3   # similar objects stored per ad

# a Georgian cadastral code as people write it in ads: 01.14.05.003.017(.01.501)
CADASTRAL = re.compile(r"(?<![\d.])\d{2}\.\d{2}\.\d{2}\.\d{3}\.\d{3}(?:\.\d{2}\.\d{3})?(?![\d])")
SOURCE_NAME = {"ss": "ss.ge", "myhome": "myhome.ge", "livo": "livo.ge", "korter": "korter.ge", "facebook": "Facebook",
               "instagram": "Instagram", "threads": "Threads", "telegram": "Telegram"}


def _ingest():
    """bot/ingest.py: dHash photo helpers (the same ones its dedupe uses)."""
    if str(REPO_ROOT / "bot") not in sys.path:
        sys.path.append(str(REPO_ROOT / "bot"))
    import ingest
    return ingest


def photo_hash(data: bytes) -> int | None:
    """property_media.dhash of an image: bot/ingest.py dhash (the ads' photo_hashes) of the upright picture, as a signed
    64-bit int. None when Pillow can't read it (a video, a broken file)."""
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # iPhone originals
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        with Image.open(io.BytesIO(data)) as im:
            px = list(ImageOps.exif_transpose(im).convert("L").resize((9, 8)).getdata())
    except Exception:  # noqa: BLE001
        return None
    h = sum(1 << n for n in range(64) if px[n // 8 * 9 + n % 8] > px[n // 8 * 9 + n % 8 + 1])
    return h - (1 << 64) if h >> 63 else h


def shared_photos(a: list[int], b: list[int]) -> int:
    """Photos of `a` that have a twin among `b` (dHash ≤ PHOTO_BITS apart)."""
    return sum(1 for x in a if any(((x ^ y) & U64).bit_count() <= PHOTO_BITS for y in b))


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _feat(l: Listing) -> dict:
    t = l.text or ""
    return {"l": l, "urls": set(jload(l.urls, []) or []), "phones": extract_phones(t),
            "cads": {_digits(c) for c in CADASTRAL.findall(t)}, "hashes": jload(l.photo_hashes, []) or []}


def _objects(db: Session, pids: list[int] | None = None) -> dict[int, dict]:
    """Snapshot of the objects to compare with: owner phone, every known link and photo hash of their ads.
    ponytail: the whole base in memory and a pairwise scan per new ad; fine for a few thousand objects, bucket by
    district / bedrooms (or move the photo check into SQL) when it grows."""
    q = select(Property) if pids is None else select(Property).where(Property.id.in_(pids))
    props = list(db.scalars(q))
    owners = {p.owner_id for p in props if p.owner_id}
    owners_ = {o.id: o for o in db.scalars(select(Owner).where(Owner.id.in_(owners)))} if owners else {}
    # a number behind 3+ objects is an agent's (or a landlord's with many flats): it names no flat by itself
    # (eval 11-17: one number matched 6 different objects). Counted over the whole base, also for a pids snapshot
    last9 = func.right(Owner.phone_norm, literal_column("9"))
    many = set(db.scalars(select(last9).join(Property, Property.owner_id == Owner.id).where(Owner.phone_norm.isnot(None))
                          .group_by(last9).having(func.count(Property.id) >= 3)))
    objs = {p.id: {"p": p, "phones": [r["phone"] for r in owner_phones(owners_.get(p.owner_id))],
                   "agent": getattr(owners_.get(p.owner_id), "is_agent", False)
                   or (getattr(owners_.get(p.owner_id), "phone_norm", None) or "")[-9:] in many,
                   "urls": {p.source_url, p.post_url} - {None, ""}, "hashes": []} for p in props}
    # our own channel posts too (Irakli 11-25: a deal typed with the link of our post made a second object)
    for pid, url in db.execute(select(PropertyPublication.property_id, PropertyPublication.url)
                               .where(PropertyPublication.property_id.in_(objs), PropertyPublication.url.isnot(None))):
        objs[pid]["urls"].add(url)
    for pid, url, urls, hashes in db.execute(
            select(PropertyListing.property_id, PropertyListing.url, Listing.urls, Listing.photo_hashes)
            .join(Listing, Listing.id == PropertyListing.listing_id).where(PropertyListing.property_id.in_(objs))):
        objs[pid]["urls"] |= {url, *(jload(urls, []) or [])} - {None}
        objs[pid]["hashes"] += jload(hashes, []) or []
    media = select(PropertyMedia.property_id, PropertyMedia.dhash).where(PropertyMedia.kind == "photo",
                                                                         PropertyMedia.dhash.isnot(None), PropertyMedia.dhash != 0)
    for pid, h in db.execute(media if pids is None else media.where(PropertyMedia.property_id.in_(pids))):
        if pid in objs:
            objs[pid]["hashes"].append(h & U64)
    return objs


def _params_equal(l: Listing, p: Property) -> bool:
    if None in (l.district, p.district, l.bedrooms, p.bedrooms, l.floor, p.floor, l.area, p.area):
        return False
    a, b = float(l.area), float(p.area)
    return l.district == p.district and l.bedrooms == p.bedrooms and l.floor == p.floor and abs(a - b) <= AREA_TOL * max(a, b)


def verdict(f: dict, o: dict, sim: float | None = None) -> tuple[str, float, list[str]]:
    """(same | similar | none, score for ranking, reasons in Russian for the operator)."""
    l, p = f["l"], o["p"]
    why = []
    if f["urls"] & o["urls"]:
        why.append("та же ссылка")
    if p.cadastral_code and _digits(p.cadastral_code) in f["cads"]:
        why.append("тот же кадастровый код")
    shared = shared_photos(f["hashes"], o["hashes"]) if f["hashes"] and o["hashes"] else 0
    if shared >= SAME_PHOTOS:
        why.append(f"общих фото: {shared}")
    # an agent's number (is_agent, or behind 3+ objects) names no flat
    phone = not o.get("agent") and any(phone_match(x, y) for x in f["phones"] for y in o["phones"])
    if phone and address_match(l.address or "", p.address or ""):
        why.append("тот же телефон и адрес")
    elif phone and shared:
        why.append("тот же телефон и общее фото")
    if why:  # photos and the phone beat a floor or area typed differently (David 11-17)
        return SAME, 1.0, why
    # weaker evidence, a person compares: one shared photo (it can be the building or the view) or the owner's number
    # (his other flat), each only with a floor / area that roughly agree (eval 11-17: alone they were another flat 7
    # times of 9). The params alone (district, bedrooms, floor, area) were another flat 5 times of 5: only a reason more;
    # the same house with these params is an address hint (properties.similar_in_base)
    fits = P.could_be_same(l, p)
    strong = (["одно общее фото"] if shared and fits else []) + (["тот же телефон"] if phone and fits else [])
    if sim is not None and sim >= TEXT_SIM and P.same_flat_possible(l, p):  # a similar text: another floor / area / house
        strong.append(f"похожее описание ({sim:.2f})")
    if not strong:
        return NONE, 0.0, []
    if _params_equal(l, p):
        why.append("район, спальни, этаж и площадь совпадают")
    return SIMILAR, round(0.5 + 0.2 * len(strong) + 0.1 * len(why) + (sim or 0) / 10, 3), strong + why


def _text_sims(db: Session, l: Listing) -> dict[int, float]:
    """Object id → cosine similarity of the ad's description to the object's text embedding. {} when no object has an
    embedding yet or the encoder (torch) is not installed."""
    t = " ".join(str(x) for x in (l.description_ru, l.district, l.address) if x).strip()
    if not t or not db.scalar(text("SELECT 1 FROM properties WHERE text_embedding IS NOT NULL LIMIT 1")):
        return {}
    from app import embeddings
    enc = embeddings.encoder()
    if enc is None:
        return {}
    v = embeddings._vec(enc.encode_texts([t])[0])
    rows = db.execute(text("SELECT id, 1 - (text_embedding <=> CAST(:v AS vector)) FROM properties "
                           "WHERE text_embedding IS NOT NULL ORDER BY text_embedding <=> CAST(:v AS vector) LIMIT 5"),
                      {"v": v})
    return {pid: float(s) for pid, s in rows}


def match(db: Session, l: Listing, objs: dict[int, dict] | None = None) -> list[dict]:
    """Candidate objects for an ad, best first: [{property_id, verdict, score, reasons}] (verdict same or similar)."""
    objs = _objects(db) if objs is None else objs
    f, sims = _feat(l), _text_sims(db, l)
    out = []
    for pid, o in objs.items():
        v, score, why = verdict(f, o, sims.get(pid))
        if v != NONE:
            out.append({"property_id": pid, "verdict": v, "score": score, "reasons": why})
    return sorted(out, key=lambda m: (m["verdict"] != SAME, -m["score"], m["property_id"]))


def same_as(db: Session, address: str | None = None, cadastral: str | None = None, phone: str | None = None,
            url: str | None = None) -> list[dict]:
    """Stage 7: objects of the base the matcher calls `same` for an object typed by hand in the deal form (no ad
    behind it, so no photos): the same link, cadastral code, or the owner's phone and address."""
    l = SimpleNamespace(address=address, district=None, bedrooms=None, floor=None, area=None)
    f = {"l": l, "urls": {(url or "").strip()} - {""}, "phones": [phone] if phone else [],
         "cads": {_digits(cadastral)} - {""}, "hashes": []}
    out = []
    for pid, o in _objects(db).items():
        v, _, why = verdict(f, o)
        if v == SAME:
            out.append({"property_id": pid, "address": o["p"].address, "reasons": why})
    return out


def attach(db: Session, p: Property, l: Listing, actor: User | None, reasons: list[str], via: str) -> list[tuple]:
    """Link the ad to the object + history event. -> exclusive alerts to send after the commit. No commit."""
    link = P.link_listing(db, p, l)
    if link.property_id != p.id:
        old = db.get(Property, link.property_id)
        if old is None or old.listing_id != l.id or old.status != "archived":
            raise DomainError("conflict", f"listing {l.id} is already linked to object #{link.property_id}")
        link.property_id = p.id  # the ad of an archived object came back to the feed, and it is this object of ours
    log_event(db, "property", p.id, "listing_linked", actor, listing_id=l.id, source=link.source, url=link.url,
              reasons=reasons, via=via)
    if p.is_exclusive and l.source not in ("agency", "object") and l.status != "gone":
        log_event(db, "property", p.id, "exclusive_conflict", actor, listing_id=l.id, source=link.source, url=link.url)
        return [(p, SOURCE_NAME.get(link.source or "", link.source or "другом сайте"), link.url)]
    return []


def _send(alerts: list[tuple]) -> None:
    for p, source, url in alerts:
        P._notify("exclusive_conflict", p, source, url)


def process(db: Session, ids: list[str]) -> dict:
    """Run the matcher on new listings: same → linked to the object, similar → listing_matches for an operator."""
    counts = {SAME: 0, SIMILAR: 0, NONE: 0}
    if not ids or not db.scalar(select(Property.id).limit(1)):
        counts[NONE] = len(ids or [])
        return counts
    objs = _objects(db)
    links = dict(db.execute(select(PropertyListing.listing_id, PropertyListing.property_id)
                            .where(PropertyListing.listing_id.in_(ids))).all())
    # an ad taken into work and archived is back in the feed: its old object is no answer, another object can be
    # (David 11-17: «Vake shrosha st. 1» was our archived #15461 and is our channel's #15032)
    released = dict(db.execute(select(Property.listing_id, Property.id).where(Property.listing_id.in_(ids),
                                                                              Property.status == "archived")).all())
    alerts, fresh = [], []
    for l in db.scalars(select(Listing).where(Listing.id.in_(ids)).order_by(Listing.id)):
        if l.id in links and links[l.id] != released.get(l.id):
            continue
        found = [m for m in match(db, l, objs) if m["property_id"] != released.get(l.id)]
        if found and found[0]["verdict"] == SAME:
            m = found[0]
            o = objs[m["property_id"]]
            alerts += attach(db, o["p"], l, None, m["reasons"], "auto")
            db.execute(update_(ListingMatch).where(ListingMatch.listing_id == l.id, ListingMatch.status == "pending")
                       .values(status="rejected", decided_at=now()))  # an earlier «похож» guess is settled now
            f = _feat(l)  # the object now also has this ad's link and photos
            o["urls"] |= f["urls"]
            o["hashes"] += f["hashes"]
            counts[SAME] += 1
        elif found:
            for m in found[:MAX_CANDIDATES]:
                db.execute(insert(ListingMatch).values(listing_id=l.id, property_id=m["property_id"], score=m["score"],
                                                       reasons=m["reasons"]).on_conflict_do_nothing())
            counts[SIMILAR] += 1
        else:
            counts[NONE] += 1
        if not (found and found[0]["verdict"] == SAME):
            fresh.append(l)
    P.store_similar(db, fresh)  # «В архиве по этому адресу: N» on the ad's card
    from app.services import similar  # local: similar imports properties too
    for pid in similar.exclusives_hit(db, [l.id for l in fresh]):  # an exclusive's new possible twin: re-scan it
        P.schedule_dup_scan(db, db.get(Property, pid), "new_ads")
    db.commit()
    _send(alerts)
    return counts


def decide(db: Session, listing_id: str, pid: int, actor: User, same: bool) -> ListingMatch:
    """Operator's answer on a «похож» card: same → the ad is linked (other candidates rejected), different → rejected."""
    if not at_least(actor, "operator"):
        raise forbidden("only operators decide on similar objects")
    m = db.scalar(select(ListingMatch).where(ListingMatch.listing_id == listing_id, ListingMatch.property_id == pid)
                  .with_for_update())
    if m is None:
        raise DomainError("not_found", "match not found")
    l, p = get_or_404(db, Listing, listing_id, "listing"), get_or_404(db, Property, pid, "property")
    m.status, m.decided_by, m.decided_at = ("confirmed" if same else "rejected"), actor.id, now()
    alerts = []
    if same:
        alerts = attach(db, p, l, actor, m.reasons, "operator")
        db.execute(update_(ListingMatch).where(ListingMatch.listing_id == listing_id, ListingMatch.id != m.id,
                                               ListingMatch.status == "pending")
                   .values(status="rejected", decided_by=actor.id, decided_at=now()))
    log_event(db, "listing", l.id, "match_confirmed" if same else "match_rejected", actor, property_id=p.id)
    db.commit()
    _send(alerts)
    return m


def elsewhere(db: Session, p: Property) -> list[dict]:
    """Where else the object is advertised (shown when an agent marks it exclusive): its live outside ads, plus live ads
    not linked anywhere that the matcher calls same / similar. Read only.
    ponytail: scans every live outside ad in Python, ~1 s on a few thousand; prefilter in SQL when the catalog grows."""
    out = [{**a, "verdict": "linked", "reasons": []} for a in P.external_ads(db, [p.id]).get(p.id, [])]
    o = _objects(db, [p.id])[p.id]
    linked = select(PropertyListing.listing_id)
    for l in db.scalars(select(Listing).where(Listing.status.in_(("published", "pending_review")),
                                              Listing.source.not_in(("agency", "object")), Listing.id.not_in(linked))):
        v, _, why = verdict(_feat(l), o)
        if v != NONE:
            out.append({"listing_id": l.id, "source": P.source_type_of(l), "url": (jload(l.urls, []) or [None])[0],
                        "verdict": v, "reasons": why})
    return out


def backfill_hashes(db: Session) -> int:
    """dHash the local photo files of listings that have none stored yet (bot/raw/img, paths relative to bot/)."""
    ing, n = _ingest(), 0
    for l in db.scalars(select(Listing).where(Listing.photo_hashes == "[]", Listing.photos != "[]")):
        files = [REPO_ROOT / "bot" / str(f) for f in jload(l.photos, []) or [] if not str(f).startswith(("http://", "https://"))]
        hashes = [ing.dhash(str(f)) for f in files if f.is_file()]
        if hashes:
            l.photo_hashes, n = jdump(hashes), n + 1
    db.commit()
    return n


def backfill_media_hashes(db: Session, batch: int = 500, max_batches: int | None = None, out=print) -> int:
    """property_media.dhash of object photos that have none, from the ~640 px preview (or the original) in storage.
    The worker runs one batch per pass (max_batches=1): the newest objects first. An unreadable file gets 0 (all bits
    off), which matches no real photo (a flat grey picture), so it is not fetched again on every pass."""
    from concurrent.futures import ThreadPoolExecutor

    def hash_of(key):
        data = storage.read(storage.preview_key(key)) or storage.read(key)
        return photo_hash(data) if data else None
    n, done = 0, 0
    with ThreadPoolExecutor(16) as pool:
        while (max_batches is None or done < max_batches) and (rows := list(db.scalars(
                select(PropertyMedia).where(PropertyMedia.kind == "photo", PropertyMedia.dhash.is_(None))
                .order_by(PropertyMedia.id.desc()).limit(batch)))):
            for m, h in zip(rows, pool.map(hash_of, [m.path for m in rows])):
                m.dhash, n = (0 if h is None else h), n + (h is not None)
            done += 1
            db.commit()
            out(f"media hashes: {n} (down to media #{rows[-1].id})")
    return n


def live_unlinked(db: Session) -> list[str]:
    """Live outside ads not linked to an object (or only to their own archived one): what «Новые объявления» and the
    competitor list show."""
    known = (select(PropertyListing.listing_id).join(Property, Property.id == PropertyListing.property_id)
             .where(or_(Property.listing_id.is_distinct_from(PropertyListing.listing_id), Property.status != "archived")))
    return list(db.scalars(select(Listing.id).where(Listing.status.in_(("published", "pending_review")),
                                                    Listing.source.not_in(("object", "agency")), Listing.id.not_in(known))))


def rematch(db: Session) -> dict:
    """Forget the undecided «Похож на» cards and address hints of the live ads and match them again (after the rules or
    the object photos changed). Operators' decisions (confirmed / rejected) stay."""
    ids = live_unlinked(db)
    db.execute(delete(ListingMatch).where(ListingMatch.listing_id.in_(ids), ListingMatch.status.in_(("pending", P.BASE_HINT))))
    db.commit()
    return process(db, ids)


if __name__ == "__main__":
    from app.db import SessionLocal
    cmd = sys.argv[1:2]
    if cmd not in (["hashes"], ["run"], ["media"], ["rematch"]):
        sys.exit(__doc__)
    with SessionLocal() as s:
        if cmd == ["hashes"]:
            print(f"photo hashes: {backfill_hashes(s)} listings")
        elif cmd == ["media"]:
            print(f"object photo hashes: {backfill_media_hashes(s)}")
        elif cmd == ["rematch"]:
            print("rematched:", rematch(s))
        else:
            print("matched:", process(s, live_unlinked(s)))
