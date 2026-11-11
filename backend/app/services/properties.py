"""Funnel 1: new listing → take → call → owner/agent → shoot → media → review → publish (§4.2, §5)."""
import logging
import math
import os
import hashlib
import json
import html as html_
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlencode

import jwt
from sqlalchemy import Text, and_, case, cast, func, or_, select
from sqlalchemy import update as update_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import storage
from app.config import settings as env
from app.models import (ARCHIVE_REASONS, FIELD, PUBLICATION_CHANNELS, STAFF, ChannelPost, Deal, Event, Listing, ListingMatch, Owner, Property,
                        PropertyListing, PropertyMedia, PropertyPublication, User)
from app.services import jobs
from app.services import users as users_svc
from app.services import settings as settings_svc
from app.services.common import aware, clean_cadastral, get_or_404, jload, log_event, now, now_text
from app.services.dedup import address_match, extract_phones, norm_address, norm_phone, phone_match
from app.services.errors import DomainError, forbidden
from app.telegram import notify

log = logging.getLogger(__name__)

BOARD = ("taken", "call_failed", "owner_confirmed", "agent_marked", "shoot_scheduled", "media_uploaded", "review",
         "returned", "published", "rented")
LEASE = ("rent_from", "rent_to", "tenant_name", "tenant_phone", "tenant_tg")  # «Сдан» by hand (no deal in the CRM)
EDITABLE = ("address", "district", "lat", "lng", "cadastral_code", "price", "currency", "bedrooms", "rooms", "area", "floor",
            "total_floors", "description", "is_exclusive", "source_url", "source_type", "checklist", "shoot_at",
            "post_fields") + LEASE
# Stage 6: once the operator has verified the object (verified_at), the agent can't change these himself: everything a
# cheat could use (owner contact and binding, address, cadastral code, the ad link, exclusive, price and deposit). His
# edit of them becomes a «Запрос оператору» (services/change_requests.py); archiving does too. deposit / sale_price are
# keys of post_fields; the other post fields (amenities, description, headline…) stay his, logged as usual.
LOCKED_AFTER_VERIFY = ("owner_name", "owner_phone", "address", "cadastral_code", "source_url", "is_exclusive", "price",
                       "currency", "deposit", "sale_price")
POST_LOCKED = ("deposit", "sale_price")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
MAX_UPLOAD = 300 * 1024 * 1024
# one Telegram album: 9 photos + 1 video per object (the channel post sends ≤10 media)
MAX_MEDIA = {"photo": 9, "video": 1}
# the owner's signed exclusive contract (PDF): a property_media row of its own kind, never in a post/album/slideshow
CONTRACT = "exclusive_contract"
POST_KINDS = ("photo", "video")
MAX_CONTRACT = 20 * 1024 * 1024
# the owner's documents next to the contract (exclusive objects): his passport/ID and the Public Registry extract
# (ამონაწერი, the owner downloads it himself from naprweb.reestri.gov.ge — its document view is behind reCAPTCHA, so
# we don't fetch it). Private: stored under private/, served only to staff and the object's agent (api media_file),
# never in a post/album/slideshow (not POST_KINDS).
OWNER_ID_DOC, REGISTRY_EXTRACT = "owner_id_doc", "registry_extract"
OWNER_DOCS = {OWNER_ID_DOC: ("паспорт / ID собственника", (".jpg", ".jpeg", ".png", ".pdf", ".heic", ".heif")),
              REGISTRY_EXTRACT: ("выписка из Публичного реестра (PDF)", (".pdf",))}
MAX_OWNER_DOC = 15 * 1024 * 1024
PRIVATE_PREFIX = "private/"
# the agent's media and checklist are open until «Готово — отправить оператору»; after that only staff change them,
# unless the operator returns the object (returned)
AGENT_MEDIA_STATUSES = ("owner_confirmed", "shoot_scheduled", "media_uploaded", "returned")
CHECKLIST_ROOMS = ("living_room", "kitchen", "bedroom", "bathroom", "balcony", "view", "entrance", "building")


def _notify(name: str, *args):
    try:
        return getattr(notify, name)(*args)
    except Exception:  # noqa: BLE001
        log.exception("notify.%s failed", name)


def _prop(db: Session, pid: int, lock: bool = False) -> Property:
    return get_or_404(db, Property, int(pid), "property", lock=lock)


def _require_owner(p: Property, actor: User):
    if actor.role in STAFF or p.agent_id == actor.id:
        return
    raise forbidden("object is taken by another agent")


def _require_media_editable(p: Property, actor: User):
    _require_owner(p, actor)
    if actor.role not in STAFF and p.status not in AGENT_MEDIA_STATUSES:
        raise DomainError("submitted", "the object is with the operator: media and checklist are read-only", 409)


def _require_status(p: Property, *allowed: str):
    if p.status not in allowed:
        raise DomainError("bad_status", f"object is {p.status}, expected one of {allowed}")


def _status(db: Session, p: Property, status: str, actor: User | None, action: str | None = None, **data):
    old = p.status
    p.status = status
    p.updated_at = now()
    p.board_hidden_at = None  # a new step brings the card back to the board
    log_event(db, "property", p.id, action or status, actor, previous=old, status=status, **data)


def _num(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(",", ".").replace("$", "").strip())
    except InvalidOperation:
        return None


def source_type_of(listing: Listing) -> str:
    lid, src = (listing.id or "").lower(), (listing.source or "").lower()
    if src == "agency":
        return "agency_channel"
    if lid.startswith("ss"):
        return "ss"
    if lid.startswith("mh"):
        return "myhome"
    if lid.startswith("lv"):
        return "livo"
    if lid.startswith("kr"):
        return "korter"
    if lid.startswith("th") or src.startswith("th:"):
        return "threads"
    if lid.startswith("ig") or src.startswith("ig:"):
        return "instagram"
    if lid.startswith("fb") or src.startswith("fb:"):
        return "facebook"
    if lid.startswith("tg") or src.startswith("tg:"):
        return "telegram"
    return "other"


def listing_flags(listing: Listing) -> dict:
    reasons = jload(listing.agent_reasons, []) or []
    dups = jload(listing.dup_sources, []) or []
    suspect = bool(listing.agent_suspect) or bool(reasons)
    # "duplicate" = the agency channel already has this flat; the same flat on several other sites is merged, not a dup
    in_channel = "agency" in dups
    return {"owner": not suspect, "agent_suspect": suspect, "duplicate": in_channel,
            "reasons": reasons + (["уже есть в канале агентства"] if in_channel else [])}


# ---------------------------------------------------------------- listings (C1)

def listing_out(listing: Listing, full: bool = False) -> dict:
    photos = jload(listing.photos, []) or []
    d = {"id": listing.id, "status": listing.status, "source": listing.source, "source_type": source_type_of(listing),
         "price_usd": listing.price_usd, "area": listing.area, "floor": listing.floor, "bedrooms": listing.bedrooms,
         "address": listing.address, "district": listing.district, "lat": listing.lat, "lng": listing.lng,
         "description": listing.description_ru, "photos": [f"/api/listing-photos/{listing.id}/{i}" for i in range(len(photos))],
         "ai_flags": listing_flags(listing), "first_seen": listing.first_seen, "updated_at": listing.updated_at,
         "status_reason": listing.status_reason}
    if full:  # agent-only fields: original links, text with phones
        d.update(urls=jload(listing.urls, []), text=listing.text, phones=extract_phones(listing.text or ""),
                 details=jload(listing.details, {}), dup_sources=jload(listing.dup_sources, []),
                 telegram_contacts=list(dict.fromkeys(re.findall(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})\b", listing.text or ""))))
    return d


SOURCE_PREFIX = {"ss": "ss", "myhome": "mh", "livo": "lv", "korter": "kr", "facebook": "fb", "instagram": "ig", "threads": "th", "telegram": "tg"}


def _metro():
    return cast(Listing.details, JSONB)["metro_ru"].astext


def _untaken():
    taken = select(Property.listing_id).where(Property.listing_id.isnot(None), Property.status != "archived")
    return (Listing.status.in_(("published", "pending_review")), Listing.source.not_in(("agency", "object")), Listing.id.not_in(taken))


def new_listings(db: Session, q: str | None = None, source: str | None = None, flag: str | None = None,
                 limit: int = 50, offset: int = 0, district: str | None = None, metro: str | None = None,
                 price_min: int | None = None, price_max: int | None = None, bedrooms: str | None = None,
                 known: bool = False) -> dict:
    """Listings not yet taken into work, newest first. `source` is a type (ss, facebook…) or an exact source (fb:group).
    Ads the matcher already linked to an object («уже известные») are hidden unless known=True; known_total counts them."""
    stmt = select(Listing).where(*_untaken())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Listing.address.ilike(like), Listing.district.ilike(like), Listing.text.ilike(like),
                              Listing.id.ilike(like)))
    if source:
        pref = SOURCE_PREFIX.get(source)
        stmt = stmt.where(Listing.id.ilike(f"{pref}%") if pref else Listing.source == source)
    if district:
        stmt = stmt.where(Listing.district == district)
    if metro:
        stmt = stmt.where(_metro() == metro)
    if price_min is not None:
        stmt = stmt.where(Listing.price_usd >= price_min)
    if price_max is not None:
        stmt = stmt.where(Listing.price_usd <= price_max)
    if bedrooms:  # "0,1,2,4": 0 = studio, 4 = 4 and more
        picked = {int(b) for b in bedrooms.split(",") if b.strip().isdigit()}
        conds = [Listing.bedrooms.in_(picked - {4})] + ([Listing.bedrooms >= 4] if 4 in picked else [])
        stmt = stmt.where(or_(*conds))
    if flag == "owner":
        stmt = stmt.where(Listing.agent_suspect == 0, Listing.agent_reasons == "[]")
    elif flag == "agent_suspect":
        stmt = stmt.where(or_(Listing.agent_suspect == 1, Listing.agent_reasons != "[]"))
    elif flag == "duplicate":
        stmt = stmt.where(Listing.dup_sources.contains('"agency"'))
    # an object's own origin ad is not "known": released objects (owner refused) come back to the feed through it
    linked = select(PropertyListing.listing_id).join(Property, Property.id == PropertyListing.property_id).where(
        Property.listing_id.is_distinct_from(PropertyListing.listing_id))
    known_total = db.scalar(select(func.count()).select_from(stmt.where(Listing.id.in_(linked)).subquery()))
    if not known:
        stmt = stmt.where(Listing.id.not_in(linked))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Listing.first_seen.desc(), Listing.id).limit(limit).offset(offset)))
    ids = [r.id for r in rows]
    sim, refused, objs = similar_in_base(db, rows), refusals_for(db, ids), known_objects(db, ids)
    from app.services import demand
    want = demand.listing_counts(db, rows)  # stage 10: «Под этот объект подходят N активных клиентов»
    return {"items": [{**listing_out(r), "similar": sim.get(r.id, []), "refused": refused.get(r.id), "demand": want.get(r.id, 0),
                       **objs.get(r.id, {"known_object": None, "matches": []})} for r in rows],
            "total": total, "known_total": known_total}


def known_objects(db: Session, listing_ids: list[str]) -> dict[str, dict]:
    """listing id → {known_object: the object it is linked to, matches: pending «похож» objects} (stage 4 matcher)."""
    out = {i: {"known_object": None, "matches": []} for i in listing_ids}
    if not listing_ids:
        return out
    for lid, pid, addr in db.execute(select(PropertyListing.listing_id, Property.id, Property.address)
                                     .join(Property, Property.id == PropertyListing.property_id)
                                     .where(PropertyListing.listing_id.in_(listing_ids))):
        out[lid]["known_object"] = {"id": pid, "address": addr}
    for m, addr in db.execute(select(ListingMatch, Property.address).join(Property, Property.id == ListingMatch.property_id)
                              .where(ListingMatch.listing_id.in_(listing_ids), ListingMatch.status == "pending")
                              .order_by(ListingMatch.score.desc(), ListingMatch.id)):
        out[m.listing_id]["matches"].append({"property_id": m.property_id, "address": addr, "score": m.score,
                                             "reasons": m.reasons})
    return out


def _near(a: Listing, b: Listing) -> float:
    """Metres between two pins (equirectangular; fine at city scale, 0.75 = cos(41.7°) for Tbilisi)."""
    return 111_000 * math.hypot(a.lat - b.lat, (a.lng - b.lng) * 0.75)


def similar_in_base(db: Session, rows: list[Listing], radius: float = 60,
                    twin_radius: float = 1500) -> dict[str, list[dict]]:
    """Objects of our base at the ad's address, for the agent to compare by eye (David 2026-11-09: the agency channel
    is no longer a source, the base is). Every object in the same building (radius) is shown, whatever its parameters:
    the agent sees all flats we hold at this address. A «twin» (same bedrooms and floor, area ±10%) also counts up to
    twin_radius and ranks first: pins can be off by a kilometre (8/10 Shroshi geocoded 1 km away). An object's pin is
    the pin of its source ad (objects made by hand without an ad have none and are not compared)."""
    rows = [r for r in rows if r.lat is not None and r.lng is not None]
    if not rows:
        return {}
    objs = db.execute(select(Property, Listing).join(Listing, Listing.id == Property.listing_id)
                      .where(Listing.lat.isnot(None), Listing.lng.isnot(None))).all()  # archived count too (David 11-09)

    linked = set(db.execute(select(PropertyListing.listing_id, PropertyListing.property_id)
                            .where(PropertyListing.listing_id.in_([r.id for r in rows]))).all())  # already «known_object»

    def twin(l: Listing, p: Property) -> bool:
        return (l.bedrooms is not None and l.bedrooms == p.bedrooms and l.floor is not None and l.floor == p.floor
                and bool(l.area and p.area) and abs(float(l.area) - float(p.area)) <= 0.10 * max(float(l.area), float(p.area)))

    out = {}
    for l in rows:
        cand = [(not twin(l, p), _near(l, a), p, a) for p, a in objs if a.id != l.id and (l.id, p.id) not in linked]
        near = sorted((c for c in cand if c[1] <= (radius if c[0] else twin_radius)), key=lambda x: x[:2])
        if near:  # ponytail: a full scan of the base per ad; a PostGIS / bbox prefilter when the base passes ~10k
            out[l.id] = [{"id": p.id, "url": f"/properties/{p.id}", "address": p.address or a.address, "status": p.status,
                          "archive_reason": p.archive_reason,
                          "photo": f"/api/listing-photos/{a.id}/0" if jload(a.photos, []) else None,
                          "price_usd": float(p.price) if p.price is not None else a.price_usd, "bedrooms": p.bedrooms,
                          "area": float(p.area) if p.area is not None else None, "floor": p.floor, "distance_m": round(d)}
                         for _, d, p, a in near[:20]]
    return out


def dismiss_listing(db: Session, listing_id: str, actor: User) -> None:
    """"Неактуально": the ad is deleted at the source. Kept as a human decision so the next ingest won't revive it."""
    x = get_or_404(db, Listing, listing_id, "listing", lock=True)
    x.decision, x.status, x.status_reason = "rejected", "rejected", "неактуально"
    x.decided_by, x.decided_at, x.updated_at = actor.id, now_text(), now_text()
    log_event(db, "listing", x.id, "dismissed", actor)
    db.commit()


def mark_listing_agent(db: Session, listing_id: str, actor: User, name: str | None, phone: str | None) -> Property:
    """"Агент" straight from the feed: take the listing and record the agent contact in one go (C1 → agent_marked)."""
    if not (name or "").strip() and not (phone or "").strip():
        raise DomainError("owner_required", "owner name or phone is required")
    p = take(db, listing_id, actor)
    return owner_or_agent(db, p.id, actor, False, name, phone)


def new_listing_facets(db: Session) -> dict:
    """Filter options with counts over the same untaken listings: exact sources, districts, metro stations."""
    def facet(col):
        rows = db.execute(select(col, func.count()).where(*_untaken(), col.isnot(None), col != "")
                          .group_by(col).order_by(func.count().desc()))
        return [{"value": v, "count": n} for v, n in rows]
    # Threads is one search over every author (th:<author> per ad): one option, filtered by its type
    source = case((Listing.source.like("th:%"), "threads"), else_=Listing.source)
    return {"sources": facet(source), "districts": facet(Listing.district), "metros": facet(_metro())}


# ---------------------------------------------------------------- take (C1 → C2)

def take(db: Session, listing_id: str | None, agent: User, manual_fields: dict | None = None, commit: bool = True) -> Property:
    """"Беру в работу": the object is locked to this agent; 409 taken_by_other for anyone else."""
    if listing_id:
        listing = get_or_404(db, Listing, listing_id, "listing", lock=True)
        existing = db.scalar(select(Property).where(Property.listing_id == listing_id).with_for_update())
        if existing is None:  # the matcher linked this ad to an object: take that object, don't create a twin
            pid = db.scalar(select(PropertyListing.property_id).where(PropertyListing.listing_id == listing_id))
            existing = db.get(Property, pid, with_for_update=True) if pid else None
        if existing:
            link_listing(db, existing, listing)
            if existing.agent_id == agent.id and existing.status != "archived":
                return existing
            if existing.agent_id and existing.status != "archived":
                raise DomainError("taken_by_other", "object already taken by another agent")
            existing.agent_id, existing.taken_at = agent.id, now()
            existing.archive_reason = existing.archive_comment = existing.archived_at = None
            _bind_owner(db, existing, db.get(Owner, existing.owner_id) if existing.owner_id else None, agent)
            _status(db, existing, "taken", agent, "taken")
            db.commit()
            return existing
        urls = jload(listing.urls, []) or []
        p = Property(listing_id=listing.id, source_type=source_type_of(listing), source_url=urls[0] if urls else None,
                     source_external_id=listing.id, address=listing.address, district=listing.district,
                     price=listing.price_usd, bedrooms=listing.bedrooms, area=listing.area, floor=listing.floor,
                     description=listing.description_ru, ai_flags=listing_flags(listing))
        prefill(p, listing)
    else:
        f = manual_fields or {}
        p = Property(source_type=f.get("source_type") or "direct")
        _apply(p, f)
    p.agent_id, p.taken_at, p.status = agent.id, now(), "taken"
    db.add(p)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise DomainError("taken_by_other", "object already taken by another agent")
    if listing_id:
        link_listing(db, p, listing)
    log_event(db, "property", p.id, "taken", agent, listing_id=listing_id)
    if commit:
        db.commit()
    return p


def _ts(s: str | None) -> datetime | None:
    try:
        return aware(datetime.fromisoformat(s)) if s else None
    except ValueError:
        return None


def link_listing(db: Session, p: Property, listing: Listing) -> PropertyListing:
    """Attach an outside ad to the object (idempotent; one ad belongs to one object). No commit."""
    link = db.scalar(select(PropertyListing).where(PropertyListing.listing_id == listing.id))
    if link is None:
        urls = jload(listing.urls, []) or []
        link = PropertyListing(property_id=p.id, listing_id=listing.id, source=source_type_of(listing),
                               url=urls[0] if urls else None)
        db.add(link)
    link.first_seen_at, link.last_seen_at = _ts(listing.first_seen) or link.first_seen_at, _ts(listing.last_seen)
    db.flush()
    return link


def _apply(p: Property, data: dict):
    for k in EDITABLE:
        if k not in data:
            continue
        v = data[k]
        if k in ("price", "area"):
            v = _num(v)
        elif k in ("lat", "lng"):
            v = float(v) if v not in (None, "") else None
        elif k in ("bedrooms", "rooms", "floor", "total_floors"):
            v = int(v) if v not in (None, "") else None
        elif k == "shoot_at":
            v = aware(v)
        elif k == "post_fields":
            v = clean_post_fields(v)
        elif k == "cadastral_code":  # only a changed code is checked: old objects keep their legacy codes
            v = p.cadastral_code if str(v or "").strip() == (p.cadastral_code or "").strip() else clean_cadastral(v)
        elif k in ("rent_from", "rent_to"):
            from app.services.deals import parse_date
            v = parse_date(v) if v not in (None, "") else None
        elif k in LEASE:
            v = (str(v).strip() or None) if v is not None else None
        setattr(p, k, v)


def schedule_lease_reminders(db: Session, p: Property) -> None:
    """The agent hears CONTRACT_REMIND_DAYS (30,7,1) before the end of a hand-typed lease, at 10:00 Tbilisi."""
    from app.services.deals import _at_10
    jobs.cancel(db, "property_lease_expiry", ref_prefix=f"{p.id}:")
    for days in env.contract_remind_days if p.rent_to else ():
        due = _at_10(p.rent_to - timedelta(days=days))
        if due > now():
            jobs.schedule(db, "property_lease_expiry", f"{p.id}:{days}", due, {"days": days})


def is_locked_for(p: Property, user: User) -> bool:
    """The agent's key fields and archiving go through «Запрос оператору» (LOCKED_AFTER_VERIFY)."""
    return user.role not in STAFF and p.verified_at is not None


def _current(db: Session, p: Property, k: str):
    if k in ("owner_name", "owner_phone"):
        o = db.get(Owner, p.owner_id) if p.owner_id else None
        return (o.name if k == "owner_name" else o.phone) if o else None
    return (p.post_fields or {}).get(k) if k in POST_LOCKED else getattr(p, k)


def _norm(k: str, v):
    """Comparable, JSON-safe value of a locked field."""
    if k == "price":
        n = _num(v)
        return float(n) if n is not None else None
    if k in POST_LOCKED:
        n = _num(v)
        return int(n) if n is not None else None
    if k == "is_exclusive":
        return bool(v)
    return (str(v).strip() or None) if v is not None else None


def split_locked(db: Session, p: Property, data: dict) -> tuple[dict, dict]:
    """(what the agent may apply now, {field: {old, new}} that needs the operator). Unchanged locked fields are dropped."""
    data, locked = dict(data), {}
    if isinstance(data.get("post_fields"), dict):
        pf = clean_post_fields(data["post_fields"])
        for k in POST_LOCKED:
            old, new = _norm(k, _current(db, p, k)), _norm(k, pf.get(k))
            if old != new:
                locked[k] = {"old": old, "new": new}
            pf.pop(k, None)
            if old is not None:
                pf[k] = old
        data["post_fields"] = pf
    for k in LOCKED_AFTER_VERIFY:
        if k in data and k not in POST_LOCKED:
            old, new = _norm(k, _current(db, p, k)), _norm(k, data.pop(k))
            if old != new and not (k in ("owner_name", "owner_phone") and new is None):  # empty = "don't touch" there
                locked[k] = {"old": old, "new": new}
    return data, locked


def update(db: Session, pid: int, data: dict, actor: User, reason: str | None = None) -> Property:
    """Edit from the card. After verification an agent's change of LOCKED_AFTER_VERIFY fields is not applied but sent as
    a «Запрос оператору» (p.change_request is set then); the rest is applied as usual."""
    p = _prop(db, pid)
    _require_owner(p, actor)
    if oid := data.pop("owner_id", None):  # picked from the owners list: that contact, its phone may be hidden to agents
        o = get_or_404(db, Owner, int(oid), "owner")
        data["owner_name"], data["owner_phone"] = o.name, o.phone
    locked = {}
    if is_locked_for(p, actor):
        data, locked = split_locked(db, p, data)
    apply_update(db, p, data, actor)
    p.change_request = None  # transient, read by the API
    if locked:
        from app.services import change_requests  # local: change_requests imports this module
        p.change_request = change_requests.create(db, "property", p.id, "edit", locked, actor, reason)
    db.commit()
    if p.change_request is not None:
        _notify("change_request_new", p.change_request)
    return p


def apply_update(db: Session, p: Property, data: dict, actor: User) -> None:
    """No commit (an approved change request applies through here too)."""
    was_exclusive, was_rent_to = p.is_exclusive, p.rent_to
    _apply(p, data)
    if p.rent_to != was_rent_to:
        schedule_lease_reminders(db, p)
    _set_owner_contact(db, p, data.get("owner_name"), data.get("owner_phone"), actor)
    if p.is_exclusive and not was_exclusive:
        for a in external_ads(db, [p.id]).get(p.id, []):
            log_event(db, "property", p.id, "exclusive_conflict", actor, **a)
    p.updated_at = now()
    fields = sorted(k for k in data if k in EDITABLE or k in ("owner_name", "owner_phone"))
    if fields:
        log_event(db, "property", p.id, "updated", actor, fields=fields)


def _set_owner_contact(db: Session, p: Property, name: str | None, phone: str | None, actor: User) -> None:
    """Fix the owner's name/phone from the object card (a typo like "Sofa" for "Nadia"). A phone that already belongs
    to another owner in the base re-links the object to that owner instead of creating a twin."""
    name, phone = (name or "").strip() or None, (phone or "").strip() or None
    if not name and not phone:
        return
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    if o is None:
        o, _ = upsert_owner(db, name, phone, p.address, p.source_url, actor=actor)
        p.owner_id = o.id
        _bind_owner(db, p, o, actor)
    elif phone and norm_phone(phone) != o.phone_norm:
        other = find_owner(db, phone)
        if other and other.id != o.id:
            p.owner_id, o = other.id, other
            _bind_owner(db, p, o, actor)
        else:
            o.phone, o.phone_norm = phone, norm_phone(phone)
    if name:
        o.name = name


def owner_refused(db: Session, pid: int, agent: User, comment: str | None = None) -> Property:
    """Owner reached but won't work with the agency: the object goes back to the feed so another agent can try; the feed
    card says who called and when (refusals_for). Re-taking reuses this property, so history and owner stay."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed", "owner_confirmed", "shoot_scheduled")
    _archive_now(db, p, agent, "owner_refused", comment, action="owner_refused")
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    if o is not None and o.agent_id == p.agent_id and not db.scalar(select(Property.id).where(  # he never became ours
            Property.owner_id == o.id, Property.id != p.id, Property.agent_id == o.agent_id,
            Property.status != "archived").limit(1)):
        o.agent_id = None
        log_event(db, "owner", o.id, "agent_released", agent, property_id=p.id)
    db.commit()
    return p


def owner_thinking(db: Session, pid: int, agent: User, until, comment: str | None = None) -> Property:
    """Owner is reached but thinking (has own clients, may call back): the object stays with the agent and the bot reminds
    them to call again at `until`. Any next step (shoot, "сдал сам", "не хочет") clears the reminder."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed", "owner_confirmed")
    p.next_call_at = aware(until)
    jobs.cancel(db, "call_retry", p.id)
    jobs.schedule(db, "owner_followup", p.id, p.next_call_at)
    log_event(db, "property", p.id, "owner_thinking", agent, until=p.next_call_at.isoformat(),
              comment=(comment or "").strip() or None)
    p.updated_at = now()
    db.commit()
    return p


def owner_rented_self(db: Session, pid: int, agent: User, comment: str | None = None) -> Property:
    """The owner rented the flat out himself: the object is closed and its listing never comes back to the feed."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed", "owner_confirmed", "shoot_scheduled")
    _archive_now(db, p, agent, "rented_by_owner", comment, action="owner_rented_self")
    listing = db.get(Listing, p.listing_id) if p.listing_id else None
    if listing:
        listing.decision, listing.status, listing.status_reason = "rejected", "rejected", "сдал сам"
        listing.decided_by, listing.decided_at, listing.updated_at = agent.id, now_text(), now_text()
    db.commit()
    return p


def refusals_for(db: Session, listing_ids: list[str]) -> dict[str, dict]:
    """listing id → the latest "owner won't work" note of its released object."""
    props = dict(db.execute(select(Property.id, Property.listing_id).where(
        Property.listing_id.in_(listing_ids), Property.status == "archived")).all()) if listing_ids else {}
    out = {}
    for e in db.scalars(select(Event).where(Event.entity == "property", Event.entity_id.in_([str(i) for i in props]),
                                            Event.action == "owner_refused").order_by(Event.at)):
        out[props[int(e.entity_id)]] = {"by": e.actor_label, "at": e.at.isoformat(), "comment": (e.data or {}).get("comment")}
    return out


def check_archive(db: Session, p: Property, reason: str | None, comment: str | None, duplicate_of: int | None) -> dict:
    """Validated archive fields: a reason of ARCHIVE_REASONS; «Другое» needs a comment, «Дубль» the object it duplicates."""
    comment = (comment or "").strip() or None
    if reason not in ARCHIVE_REASONS:
        raise DomainError("bad_reason", "Укажите причину архивации")
    if reason == "other" and not comment:
        raise DomainError("comment_required", "Для «Другое» нужен комментарий")
    dup = None
    if reason == "duplicate":
        dup = int(duplicate_of) if str(duplicate_of or "").strip().lstrip("#").isdigit() else None
        if dup is None or dup == p.id or db.get(Property, dup) is None:
            raise DomainError("bad_duplicate", "Укажите номер объекта, дублем которого является этот")
    return {"reason": reason, "comment": comment, "duplicate_of": dup}


def _archive_now(db: Session, p: Property, actor: User | None, reason: str, comment: str | None = None,
                 duplicate_of: int | None = None, action: str | None = None) -> None:
    """Into the archive with a reason (ARCHIVE_REASONS); reminders are cancelled. No commit, no validation."""
    p.archive_reason, p.archive_comment, p.archived_at = reason, (comment or "").strip() or None, now()
    if duplicate_of:
        p.duplicate_of = duplicate_of
    p.next_call_at = None
    for kind in ("call_retry", "owner_followup"):
        jobs.cancel(db, kind, p.id)
    _status(db, p, "archived", actor, action, reason=reason, comment=p.archive_comment,
            **({"duplicate_of": duplicate_of} if duplicate_of else {}))


def archive(db: Session, pid: int, actor: User, reason: str | None = None, comment: str | None = None,
            duplicate_of: int | None = None) -> Property:
    """«В архив» with a mandatory reason. Objects are never deleted. Staff (and the agent before verification) archive
    right away; a verified object's agent sends a «Запрос оператору» (p.change_request)."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, actor)
    if p.status == "archived":
        raise DomainError("bad_status", "Объект уже в архиве")
    a = check_archive(db, p, reason, comment, duplicate_of)
    p.change_request = None
    if is_locked_for(p, actor):
        from app.services import change_requests
        payload = {"archive_reason": {"old": p.archive_reason, "new": a["reason"]}}
        if a["comment"]:
            payload["archive_comment"] = {"old": None, "new": a["comment"]}
        if a["duplicate_of"]:
            payload["duplicate_of"] = {"old": p.duplicate_of, "new": a["duplicate_of"]}
        p.change_request = change_requests.create(db, "property", p.id, "archive", payload, actor, a["comment"])
    else:
        _archive_now(db, p, actor, a["reason"], a["comment"], a["duplicate_of"])
    db.commit()
    if p.change_request is not None:
        _notify("change_request_new", p.change_request)
    return p


# ---------------------------------------------------------------- call / owner (C2a, C2b)

def call_result(db: Session, pid: int, agent: User, reached: bool) -> Property:
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed")
    p.call_attempts = (p.call_attempts or 0) + 1
    if reached:
        p.next_call_at = None
        jobs.cancel(db, "call_retry", p.id)
        _status(db, p, "taken", agent, "call_reached", attempt=p.call_attempts)
    else:
        p.next_call_at = now() + timedelta(hours=env.CALL_RETRY_HOURS)
        jobs.schedule(db, "call_retry", p.id, p.next_call_at)
        _status(db, p, "call_failed", agent, "call_failed", attempt=p.call_attempts)
    db.commit()
    return p


def find_owner(db: Session, phone: str | None = None, address: str | None = None) -> Owner | None:
    pn = norm_phone(phone)
    if len(pn) > 4:
        o = db.scalar(select(Owner).where(Owner.phone_norm == pn).order_by(Owner.id))
        if o:
            return o
    return None


def upsert_owner(db: Session, name: str | None, phone: str | None, address: str | None = None, link: str | None = None,
                 is_agent: bool = False, actor: User | None = None) -> tuple[Owner, bool]:
    """Owner_Base row by normalized phone (apps-script addToOwnerBase). Returns (owner, created)."""
    o = find_owner(db, phone)
    if o:
        if is_agent:
            o.is_agent = True
        if name and (not o.name or o.name == "—"):
            o.name = name
        if address and not o.address:
            o.address, o.address_norm = address, norm_address(address)
        return o, False
    o = Owner(name=name, phone=phone, phone_norm=norm_phone(phone), address=address, address_norm=norm_address(address),
              link=link, is_agent=is_agent, created_by=actor.id if actor else None)
    db.add(o)
    db.flush()
    log_event(db, "owner", o.id, "created", actor, is_agent=is_agent)
    return o, True


def owner_or_agent(db: Session, pid: int, agent: User, is_owner: bool, owner_name: str | None = None,
                   owner_phone: str | None = None) -> Property:
    """C2b / C3: an agent answered → object stays in the base marked "agent"; an owner → owner_confirmed.
    The owner is saved to the base right away; dup_check records whether the phone was already known."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed", "owner_confirmed", "agent_marked")
    if not owner_phone and not owner_name:
        raise DomainError("owner_required", "owner name or phone is required")
    mark_agent = not is_owner
    if mark_agent:  # don't brand a known owner (with other real objects) as an agent because of one call
        known = find_owner(db, owner_phone)
        if known is not None and db.scalar(select(Property.id).where(
                Property.owner_id == known.id, Property.id != p.id, Property.status != "agent_marked").limit(1)):
            mark_agent = False
    owner, created = upsert_owner(db, owner_name, owner_phone, p.address, p.source_url, is_agent=mark_agent, actor=agent)
    _bind_owner(db, p, owner, agent)
    p.owner_id = owner.id
    if is_owner:
        dup = None if created else db.scalar(select(Property).where(Property.owner_id == owner.id, Property.id != p.id)
                                            .order_by(Property.id))
        p.dup_check = "added" if created and not dup else "exists"
        p.duplicate_of = dup.id if dup else None
        _status(db, p, "owner_confirmed", agent, dup_check=p.dup_check)
    else:
        flags = dict(p.ai_flags or {})
        flags.update(agent_suspect=True, owner=False, agent_confirmed=True)
        p.ai_flags = flags
        _status(db, p, "agent_marked", agent)
    jobs.cancel(db, "call_retry", p.id)
    db.commit()
    return p


def _bind_owner(db: Session, p: Property, o: Owner | None, actor: User) -> None:
    """Stage 6.5: an owner belongs to the agent who brought him, and so do all his objects. A free owner is bound to the
    object's agent. An owner bound to another agent is never re-assigned silently: an agent is stopped («Собственник
    закреплён за #Tag»), staff may link him anyway (logged) or move him with transfer_owner. Agent contacts (is_agent)
    are nobody's. No commit."""
    if o is None or o.is_agent or p.agent_id is None or o.agent_id == p.agent_id:
        return
    if o.agent_id is None:
        o.agent_id = p.agent_id
        log_event(db, "owner", o.id, "agent_bound", actor, agent_id=p.agent_id, property_id=p.id)
    elif actor.role not in STAFF:
        raise DomainError("owner_taken", f"Собственник закреплён за {users_svc.tag_of(db.get(User, o.agent_id))}", 409)
    else:
        log_event(db, "property", p.id, "owner_agent_override", actor, owner_id=o.id, owner_agent_id=o.agent_id)


def transfer_owner(db: Session, owner_id: int, to_agent_id: int, by_user: User, reason: str | None = None,
                   commit: bool = True) -> Owner:
    """«Передать собственника агенту…»: staff only (stage 6's agent «Запрос оператору» is approved through here, e.g. the
    agent left). The owner and his live objects of the previous agent move to the new one; every object of the owner
    and the owner himself get a history event."""
    if by_user.role not in STAFF:
        raise forbidden("only operators transfer owners")
    o = get_or_404(db, Owner, int(owner_id), "owner", lock=True)
    to = get_or_404(db, User, int(to_agent_id), "user")
    if to.role not in FIELD or not to.is_active:
        raise DomainError("bad_agent", "Передать можно только активному агенту")
    if o.agent_id == to.id:
        raise DomainError("bad_status", "Собственник уже закреплён за этим агентом")
    old = db.get(User, o.agent_id) if o.agent_id else None
    reason = (reason or "").strip() or None
    o.agent_id = to.id
    data = {"from_agent_id": old.id if old else None, "to_agent_id": to.id, "reason": reason,
            "comment": f"{old.name if old else '—'} → {to.name}" + (f" · {reason}" if reason else "")}
    log_event(db, "owner", o.id, "owner_transferred", by_user, **data)
    for p in db.scalars(select(Property).where(Property.owner_id == o.id).with_for_update()):
        moved = p.agent_id == (old.id if old else None) and p.status not in ("archived", "rented")
        if moved:
            p.agent_id, p.updated_at = to.id, now()
        log_event(db, "property", p.id, "owner_transferred", by_user, owner_id=o.id, moved=moved, **data)
    if commit:
        db.commit()
    return o


def schedule_shoot(db: Session, pid: int, agent: User, shoot_at) -> tuple[Property, str]:
    """C3: shoot date set; owner dedup by phone/address → 'added' | 'exists' (apps-script saveMakeFoto)."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "owner_confirmed", "shoot_scheduled")
    p.shoot_at = aware(shoot_at)
    p.next_call_at = None
    jobs.cancel(db, "owner_followup", p.id)
    owner = db.get(Owner, p.owner_id) if p.owner_id else None
    if not p.dup_check:
        res = check_duplicate(db, owner.phone if owner else None, p.address, exclude_property=p.id)
        p.dup_check = "exists" if res["level"] != "none" else "added"
    if p.address and not p.duplicate_of:
        for other in db.scalars(select(Property).where(Property.id != p.id, Property.address.isnot(None),
                                                       Property.status.not_in(("archived",)))):
            if address_match(p.address, other.address):
                p.duplicate_of, p.dup_check = other.id, "exists"
                break
    _status(db, p, "shoot_scheduled", agent, shoot_at=p.shoot_at.isoformat() if p.shoot_at else None, dup_check=p.dup_check)
    db.commit()
    from app.services import reports  # local import: reports imports nothing from here, keep module graph flat
    reports.save(db, "makefoto", {"address": p.address, "phone": owner.phone if owner else "", "owner": owner.name if owner else "",
                                  "date": p.shoot_at.astimezone(_tz()).strftime("%d.%m.%Y %H:%M") if p.shoot_at else "",
                                  "agent": agent.name, "link": p.source_url or "", "status": p.dup_check,
                                  "property_id": p.id}, agent)
    return p, p.dup_check


def _tz():
    from app.services.common import TZ
    return TZ


# ---------------------------------------------------------------- media, checklist, review (C4, B7)

def media_root() -> Path:
    return Path(env.MEDIA_DIR)


def upload_media(db: Session, pid: int, files: list[tuple[str, bytes]], room: str | None, actor: User) -> list[PropertyMedia]:
    p = _prop(db, pid, lock=True)
    _require_media_editable(p, actor)
    _require_status(p, *AGENT_MEDIA_STATUSES, "review", "published")
    pos = db.scalar(select(func.coalesce(func.max(PropertyMedia.position), -1)).where(PropertyMedia.property_id == p.id))
    have = dict(db.execute(select(PropertyMedia.kind, func.count()).where(PropertyMedia.property_id == p.id)
                           .group_by(PropertyMedia.kind)).all())
    for name, content in files:
        ext = os.path.splitext(name or "")[1].lower() or ".jpg"
        if ext not in PHOTO_EXT | VIDEO_EXT:
            raise DomainError("bad_file", f"Можно загружать только фото и видео: {name}")
        if len(content) > MAX_UPLOAD:
            raise DomainError("too_large", f"Файл слишком большой: {name}")
        kind = "video" if ext in VIDEO_EXT else "photo"
        have[kind] = have.get(kind, 0) + 1
        if have[kind] > MAX_MEDIA[kind]:
            raise DomainError("media_limit", f"У объекта максимум {MAX_MEDIA['photo']} фото и {MAX_MEDIA['video']} видео "
                              "(столько уходит в пост Telegram). Удалите лишнее, чтобы добавить новое", 422)
    out = []
    for name, content in files:
        ext = os.path.splitext(name or "")[1].lower() or ".jpg"
        key = f"properties/{p.id}/{uuid.uuid4().hex}{ext}"
        storage.put(key, content)
        storage.put_preview(key, content)
        pos += 1
        kind = "video" if ext in VIDEO_EXT else "photo"
        m = PropertyMedia(property_id=p.id, kind=kind, path=key, room=room if kind == "photo" else None,  # rooms: photos only
                          position=pos, uploaded_by=actor.id)
        db.add(m)
        out.append(m)
    if p.status in ("owner_confirmed", "shoot_scheduled", "returned"):
        _status(db, p, "media_uploaded", actor, files=len(files))
    else:
        log_event(db, "property", p.id, "media_added", actor, files=len(files))
    db.commit()
    return out


def _store_contract(db: Session, p: Property, content: bytes, uploaded_by: int | None) -> PropertyMedia:
    """Save the PDF as the object's exclusive contract (replacing the previous one); the caller commits."""
    if not content.startswith(b"%PDF"):
        raise DomainError("bad_file", "Договор — только PDF")
    if len(content) > MAX_CONTRACT:
        raise DomainError("too_large", "PDF договора слишком большой")
    for old in db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == p.id, PropertyMedia.kind == CONTRACT)):
        try:
            storage.delete(old.path)
        except Exception:
            log.warning("could not delete %s", old.path)
        db.delete(old)
    key = f"properties/{p.id}/{uuid.uuid4().hex}.pdf"
    storage.put(key, content)
    m = PropertyMedia(property_id=p.id, kind=CONTRACT, path=key, position=0, uploaded_by=uploaded_by)
    db.add(m)
    return m


def _check_owner_doc(kind: str, name: str, content: bytes) -> str:
    """-> the file extension; the type is checked by the content's magic bytes, not the name alone."""
    if kind not in OWNER_DOCS:
        raise DomainError("not_found", "unknown document kind")
    label, exts = OWNER_DOCS[kind]
    ext = os.path.splitext(name or "")[1].lower()
    head = content[:16]
    magic = {".pdf": head.startswith(b"%PDF"), ".jpg": head.startswith(b"\xff\xd8"), ".jpeg": head.startswith(b"\xff\xd8"),
             ".png": head.startswith(b"\x89PNG"), ".heic": head[4:8] == b"ftyp", ".heif": head[4:8] == b"ftyp"}
    if ext not in exts or not magic.get(ext):
        raise DomainError("bad_file", f"{label}: можно {', '.join(e.lstrip('.').upper() for e in exts)}")
    if len(content) > MAX_OWNER_DOC:
        raise DomainError("too_large", f"{label}: файл больше {MAX_OWNER_DOC // 1024 // 1024} МБ")
    return ext


def _store_owner_doc(db: Session, p: Property, kind: str, name: str, content: bytes, uploaded_by: int | None) -> PropertyMedia:
    """Save (replacing the previous one) in private storage; the caller commits."""
    ext = _check_owner_doc(kind, name, content)
    for old in db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == p.id, PropertyMedia.kind == kind)):
        try:
            storage.delete(old.path)
        except Exception:
            log.warning("could not delete %s", old.path)
        db.delete(old)
    key = f"{PRIVATE_PREFIX}properties/{p.id}/{uuid.uuid4().hex}{ext}"
    storage.put(key, content)
    m = PropertyMedia(property_id=p.id, kind=kind, path=key, position=0, uploaded_by=uploaded_by)
    db.add(m)
    return m


def upload_owner_doc(db: Session, pid: int, kind: str, name: str, content: bytes, actor: User) -> PropertyMedia:
    """The agent uploads the owner's passport / registry extract himself (contract signed on paper)."""
    p = _prop(db, pid, lock=True)
    _require_media_editable(p, actor)
    _require_status(p, *AGENT_MEDIA_STATUSES, "review", "published")
    m = _store_owner_doc(db, p, kind, name, content, actor.id)
    log_event(db, "property", p.id, f"{kind}_uploaded", actor)
    db.commit()
    return m


def owner_docs(db: Session, pid: int) -> dict[str, PropertyMedia]:
    return {m.kind: m for m in db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == pid,
                                                                      PropertyMedia.kind.in_(OWNER_DOCS)))}


def missing_owner_docs(db: Session, p: Property) -> list[str]:
    """Exclusive objects: the owner's documents «Отправить оператору» still waits for."""
    if not p.is_exclusive:
        return []
    have = owner_docs(db, p.id)
    return [label for kind, (label, _) in OWNER_DOCS.items() if kind not in have]


def upload_contract(db: Session, pid: int, name: str, content: bytes, actor: User) -> PropertyMedia:
    """The agent uploads a contract signed on paper / elsewhere; replaces the previous one. Needed by submit_for_review."""
    p = _prop(db, pid, lock=True)
    _require_media_editable(p, actor)
    _require_status(p, *AGENT_MEDIA_STATUSES, "review", "published")
    m = _store_contract(db, p, content, actor.id)
    log_event(db, "property", p.id, "exclusive_contract_uploaded", actor)
    db.commit()
    return m


# ---- the owner signs the exclusive confirmation himself (ported from hig-sign): a public link, no CRM login.
# The token is the object id signed with SECRET_KEY (no table): it works while the object is exclusive and unsigned.

def sign_token(pid: int) -> str:
    return jwt.encode({"pid": int(pid), "purpose": "exclusive"}, env.SECRET_KEY, algorithm="HS256")


def _signable(db: Session, token: str, lock: bool = False) -> Property:
    try:
        d = jwt.decode(token, env.SECRET_KEY, algorithms=["HS256"])
        if d.get("purpose") != "exclusive":
            raise jwt.PyJWTError()
    except jwt.PyJWTError:
        raise DomainError("not_found", "Ссылка недействительна")
    p = db.get(Property, int(d["pid"]), with_for_update=lock, populate_existing=lock)
    if not p or not p.is_exclusive or p.status == "archived":
        raise DomainError("not_found", "Ссылка больше не действует. Попросите агента прислать новую")
    return p


def sign_view(db: Session, token: str) -> dict:
    """What the owner's page prefills (he corrects it): the object and the owner contacts from the card."""
    p = _signable(db, token)
    owner = db.get(Owner, p.owner_id) if p.owner_id else None
    c = contract(db, p.id)
    return {"id": p.id, "address": p.address or "", "cadastral_code": p.cadastral_code or "",
            "owner_name": owner and owner.name or "", "owner_phone": owner and owner.phone or "",
            "signed_at": c.created_at if c else None}


def sign_contract(db: Session, token: str, data: dict, pdf: bytes, ip: str | None, user_agent: str | None,
                  docs: dict[str, tuple[str, bytes]] | None = None) -> PropertyMedia:
    """The owner confirmed and signed: his PDF (built in the browser, hig-sign style) becomes the object's contract.
    docs: {OWNER_ID_DOC: (name, bytes), REGISTRY_EXTRACT: (name, bytes)} — both required, kept private."""
    p = _signable(db, token, lock=True)
    if contract(db, p.id):
        raise DomainError("already_signed", "Договор уже подписан")
    f = {k: str(data.get(k) or "").strip()[:200] for k in ("full_name", "personal_id", "phone", "cadastral_code")}
    if not (f["full_name"] and f["personal_id"] and f["phone"]):
        raise DomainError("missing_fields", "Заполните ФИО, личный номер и телефон")
    if data.get("consent") is not True:
        raise DomainError("consent_required", "Нужно согласие с условиями")
    if f["cadastral_code"] != (p.cadastral_code or "").strip():
        f["cadastral_code"] = clean_cadastral(f["cadastral_code"]) or ""
    docs = docs or {}
    for kind, (label, _) in OWNER_DOCS.items():
        if not (docs.get(kind) and docs[kind][1]):
            raise DomainError("missing_fields", f"Загрузите: {label}")
        _check_owner_doc(kind, *docs[kind])  # everything is checked before anything is stored
    app = _registry_app(data.get("registry_app"))
    m = _store_contract(db, p, pdf, None)
    for kind, (name, content) in docs.items():
        if kind in OWNER_DOCS:
            _store_owner_doc(db, p, kind, name, content, None)
    log_event(db, "property", p.id, "exclusive_contract_signed", None, label=f["full_name"], **f, ip=ip, registry_app=app,
              user_agent=(user_agent or "")[:300], pdf_sha256=hashlib.sha256(pdf).hexdigest())
    db.commit()
    return m


def _registry_app(raw) -> dict | None:
    """The NAPR application the owner picked on the sign page (registry.search item): JSON from the form, trimmed."""
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not str(d.get("app_id") or "").isdigit():
        return None
    return {k: str(d.get(k) or "")[:300] for k in ("app_id", "reg_number", "date", "type", "address")}


def registry_app(db: Session, pid: int) -> dict | None:
    """The application picked at the last signing (from its event), for the CRM next to the extract."""
    ev = db.scalars(select(Event).where(Event.entity == "property", Event.entity_id == str(pid),
                                        Event.action == "exclusive_contract_signed").order_by(Event.id.desc())).first()
    return (ev.data or {}).get("registry_app") if ev else None


def contract(db: Session, pid: int) -> PropertyMedia | None:
    return db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == pid, PropertyMedia.kind == CONTRACT)).first()


def set_media_room(db: Session, pid: int, mid: int, room: str | None, actor: User) -> PropertyMedia:
    """Tag one uploaded file with its room after a batch upload (the agent taps the photo)."""
    p = _prop(db, pid)
    _require_media_editable(p, actor)
    m = db.get(PropertyMedia, mid)
    if not m or m.property_id != p.id:
        raise DomainError("not_found", "media not found")
    m.room = ((room or "").strip() or None) if m.kind == "photo" else None  # a video has no room
    db.commit()
    return m


def reorder_media(db: Session, pid: int, ids: list[int], actor: User) -> None:
    """The agent's order of the photos/videos (drag-and-drop / arrows): position = index; the post album follows it."""
    p = _prop(db, pid, lock=True)
    _require_media_editable(p, actor)
    rows = {m.id: m for m in media(db, p.id)}
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        ids = []
    if sorted(ids) != sorted(rows):
        raise DomainError("bad_order", "Порядок файлов устарел — обновите страницу", 409)
    for i, mid in enumerate(ids):
        rows[mid].position = i
    log_event(db, "property", p.id, "media_reordered", actor, ids=ids)
    db.commit()


def delete_media(db: Session, pid: int, mid: int, actor: User) -> None:
    p = _prop(db, pid)
    _require_media_editable(p, actor)
    m = db.get(PropertyMedia, mid)
    if not m or m.property_id != p.id:
        raise DomainError("not_found", "media not found")
    try:
        storage.delete(m.path)
    except Exception:
        log.warning("could not delete %s", m.path)
    db.delete(m)
    log_event(db, "property", p.id, "media_deleted", actor, media_id=mid)
    db.commit()


def media(db: Session, pid: int) -> list[PropertyMedia]:
    """The post's photos and videos (not the exclusive contract PDF)."""
    return list(db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == pid, PropertyMedia.kind.in_(POST_KINDS))
                           .order_by(PropertyMedia.position)))


def set_checklist(db: Session, pid: int, checklist: dict, actor: User) -> Property:
    p = _prop(db, pid)
    _require_media_editable(p, actor)
    p.checklist = {str(k): bool(v) for k, v in (checklist or {}).items()}
    log_event(db, "property", p.id, "checklist", actor, checklist=p.checklist)
    db.commit()
    return p


REQUIRED_FIELDS = {"district": "район", "address": "адрес", "price": "цена", "bedrooms": "спальни", "area": "площадь",
                   "floor": "этаж", "total_floors": "этажей в доме"}
# the post generator's groups the agent must pick (frontend PostFieldsForm); rent-only ones are skipped for a sale
REQUIRED_POST = {"prop_type": "тип", "building": "дом", "design": "дизайн", "heating": "отопление", "pets": "животные",
                 "periods": "сроки аренды"}
RENT_ONLY_POST = ("pets", "periods")


def missing_fields(p: Property) -> list[str]:
    """Everything the agent must fill before the operator sees the object (one pass, one moderation): the card, the owner,
    and the post fields, so the post (TG now, other channels later) is built from the form, not typed by the operator."""
    out = [label for f, label in REQUIRED_FIELDS.items() if getattr(p, f) in (None, "")]
    if not p.owner_id:
        out.append("собственник")
    pf = p.post_fields or {}
    sale = pf.get("deal") == "Sale"
    out += [label for k, label in REQUIRED_POST.items() if not (sale and k in RENT_ONLY_POST) and not pf.get(k)]
    return out


def _same_object(db: Session, p: Property) -> Property | None:
    """Another live object with the same cadastral code, source link, or owner phone + address. Address alone is not
    enough: one building holds many flats (that case stays a duplicate_of hint for the operator)."""
    q = select(Property).where(Property.id != p.id, Property.status != "archived")
    if p.cadastral_code and p.cadastral_code.strip():
        if hit := db.scalars(q.where(Property.cadastral_code == p.cadastral_code.strip())).first():
            return hit
    if p.source_url:
        if hit := db.scalars(q.where(Property.source_url == p.source_url)).first():
            return hit
    owner = db.get(Owner, p.owner_id) if p.owner_id else None
    if owner and owner.phone and p.address:
        for other in db.scalars(q.where(Property.owner_id.isnot(None), Property.address.isnot(None))):
            o = db.get(Owner, other.owner_id)
            if p.cadastral_code and other.cadastral_code:
                continue  # both have codes and they differ (checked above): the owner's second flat in the house
            if o and phone_match(owner.phone, o.phone) and address_match(p.address, other.address):
                return other
    return None


def submit_for_review(db: Session, pid: int, agent: User) -> Property:
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "media_uploaded", "returned", "shoot_scheduled")
    if not media(db, p.id):
        raise DomainError("no_media", "upload photos or videos first")
    if p.is_exclusive and not contract(db, p.id):
        raise DomainError("no_exclusive_contract", "Эксклюзив: загрузите подписанный собственником эксклюзивный договор (PDF)")
    if docs := missing_owner_docs(db, p):
        raise DomainError("no_owner_docs", "Эксклюзив: загрузите " + ", ".join(docs))
    if missing := missing_fields(p):
        raise DomainError("missing_fields", "Заполните перед отправкой: " + ", ".join(missing))
    if other := _same_object(db, p):
        raise DomainError("object_exists", f"Этот объект уже есть в базе: #{other.id} {other.address or ''}".strip())
    _status(db, p, "review", agent)
    db.commit()
    _notify("property_review_request", p)
    return p


def review(db: Session, pid: int, operator: User, approve: bool, comment: str | None = None) -> Property:
    """B7a: approve → publish; return → back to the agent with a comment."""
    if operator.role not in STAFF:
        raise forbidden("only operators review objects")
    p = _prop(db, pid, lock=True)
    _require_status(p, "review")
    if approve:
        return publish(db, p.id, operator, comment=comment)
    p.review_comment, p.reviewed_by, p.reviewed_at = comment, operator.id, now()
    _status(db, p, "returned", operator, comment=comment)
    db.commit()
    _notify("property_returned", p)
    return p


# CRM district -> the channel's hashtag (the agency's old HTML post generator, buildRentPost)
DISTRICT_TAG = {"Ваке": "#Vake", "Ваке-Багеби": "#Vake", "Сабуртало": "#Saburtalo", "Мтацминда": "#Mtatsminda", "Вере": "#Vera",
                "Ортачала": "#Ortachala", "Дидубе": "#Didube", "Чугурети": "#Chugureti", "Исани": "#Isani", "Самгори": "#Samgori",
                "Глдани": "#Gldani", "Надзаладеви": "#Nadzaladevi", "Дигоми": "#Digomi", "Сололаки": "#Sololaki",
                "Авлабари": "#Avlabari", "Нахаловка": "#Naxalovka", "Ваке-Сабуртало": "#Vake"}
PRICE_TAGS = ((300, "#Price0to300"), (500, "#Price300to500"), (700, "#Price500to700"), (900, "#Price700to900"),
              (1200, "#Price900to1200"))
SALE_PRICE_STEPS = (30000, 50000, 70000, 90000, 120000, 150000, 180000, 210000, 240000, 270000, 300000, 330000, 360000,
                    390000, 420000, 450000, 480000, 510000, 600000, 700000, 800000, 900000, 1000000)
# properties.post_fields: the old generator's tag groups (stored without '#', in this order). One value / a list / a number.
POST_ONE = {
    "deal": ("Rent", "Sale"),
    "prop_type": ("Apartment", "House", "Commercial", "Land", "Retail", "Business", "Cafe", "Restaurant", "BeautySalon",
                  "Warehouses", "Manufacture", "Duplex", "Townhouse"),
    "metro": ("LibertySquare", "Rustaveli", "Marjanishvili", "StationSquare", "Tsereteli", "Gotsiridze", "Nadzaladevi",
              "Didube", "Grmagele", "Guramishvili", "Sarajishvili", "AhmeteliTheatre", "STUniversity", "VazhaPshavela",
              "Delisi", "TCUniversity", "MCUniversity", "AvlabariMetro", "IsaniMetro", "300Aragveli", "SamgoriMetro",
              "Varketili"),
    "building": ("NewBuilding", "OldBuilding", "HistoricalBuilding", "Reconstruction"),
    "design": ("New", "White", "Grey", "Yellow", "Mixed", "Old", "Retro", "UnderRepair"),
    "condition": ("BlackFrame", "WhiteFrame", "GreenFrame", "FullyRenovated"),  # sale only
    "heating": ("CentralHeating", "GasHeating", "ElectricalHeating", "ACHeating", "Fireplace"),
    "bathrooms": ("2Bathroom", "3Bathroom", "4Bathroom"),
    "pets": ("Allowed", "NotAllowed", "ByAgreement"),
}
FIXED_AMENITIES = ("Elevator", "Oven", "ParkingPlace", "Conditioner")  # a rent post shows ✖️ for the missing ones
POST_MANY = {
    "amenities": ("Balcony", "Terrace", "Courtyard", "WiFi", "TV", "SmartTV", "Bath", "Shower", "Dishwasher", "Stove",
                  "Microwave", "WashingMachine", "VacuumCleaner", "Fireplace") + FIXED_AMENITIES,
    "periods": ("1Day", "1Month", "2Month", "3Month", "4Month", "5Month", "6Month", "9Month", "12Month"),
}
# the old generator's sale block offers only these amenities, in this order (s-amenity); others stay out of a sale post
SALE_AMENITIES = ("Balcony", "Terrace", "Courtyard", "ParkingPlace", "Conditioner", "Dishwasher", "Oven", "Stove", "Bath",
                  "Shower", "Elevator", "Fireplace")
POST_NUM = ("tenants", "deposit", "sale_price")  # tenants max (1-N); deposit $ (default = price, 0 = none); sale price $


def clean_post_fields(v) -> dict:
    """Validate the post fields against the generator's vocabulary; empty values are dropped, lists keep POST_MANY order."""
    if not isinstance(v, dict):
        raise DomainError("bad_post_fields", "Поля поста: ожидается объект")
    out = {}
    for k, x in v.items():
        if x is None or x == "" or x == []:
            continue
        if k in POST_ONE:
            x = str(x).lstrip("#")
            ok = x in POST_ONE[k]
        elif k in POST_MANY:
            got = {str(i).lstrip("#") for i in x} if isinstance(x, list) else {None}
            ok, x = got <= set(POST_MANY[k]), [i for i in POST_MANY[k] if i in got]
        elif k in POST_NUM:
            n = _num(x)
            ok, x = n is not None and 0 <= n < 10 ** 9, int(n or 0)
        elif k == "headline":
            ok, x = True, str(x).strip()[:120]
        elif k == "discount":  # «Скидка»: the post gets ❗️#Discount, as the agency marks a lowered price
            if not x:
                continue
            ok, x = True, True
        else:
            ok = False
        if not ok:
            raise DomainError("bad_post_fields", f"Поля поста: недопустимое значение {k}={x}")
        out[k] = x
    return out


# ---------------------------------------------------------------- generator prefill from the parsed ad (David 2026-11-08)

# Tbilisi metro stations (OpenStreetMap railway=station subway, 2026-11-08) -> the generator's metro tag
METRO = {"LibertySquare": (41.69452, 44.80056), "Rustaveli": (41.7035, 44.78965), "Marjanishvili": (41.70959, 44.79687),
         "StationSquare": (41.72256, 44.7971), "Tsereteli": (41.72641, 44.78766), "Gotsiridze": (41.74294, 44.78405),
         "Nadzaladevi": (41.7334, 44.79643), "Didube": (41.74945, 44.77999), "Grmagele": (41.76497, 44.78996),
         "Guramishvili": (41.77576, 44.79558), "Sarajishvili": (41.78406, 44.79992), "AhmeteliTheatre": (41.79106, 44.815),
         "STUniversity": (41.72035, 44.77664), "VazhaPshavela": (41.72403, 44.73085), "Delisi": (41.72549, 44.74534),
         "TCUniversity": (41.72283, 44.71851), "MCUniversity": (41.72728, 44.76384), "AvlabariMetro": (41.69226, 44.81584),
         "IsaniMetro": (41.68663, 44.83996), "300Aragveli": (41.68799, 44.82623), "SamgoriMetro": (41.68552, 44.85448),
         "Varketili": (41.6919, 44.87088)}
METRO_NAME = {"Liberty Square": "LibertySquare", "Rustaveli": "Rustaveli", "Marjanishvili": "Marjanishvili",
              "Station Square": "StationSquare", "Tsereteli": "Tsereteli", "Gotsiridze": "Gotsiridze",
              "Nadzaladevi": "Nadzaladevi", "Didube": "Didube", "Grmagele": "Grmagele", "Ghrmaghele": "Grmagele",
              "Guramishvili": "Guramishvili", "Sarajishvili": "Sarajishvili", "Akhmeteli Theatre": "AhmeteliTheatre",
              "Technical University": "STUniversity", "Vazha-Pshavela": "VazhaPshavela", "Delisi": "Delisi",
              "State University": "TCUniversity", "Medical University": "MCUniversity", "Avlabari": "AvlabariMetro",
              "Isani": "IsaniMetro", "300 Aragveli": "300Aragveli", "Samgori": "SamgoriMetro", "Varketili": "Varketili"}
METRO_RADIUS_M = 8000  # the nearest station always fills (Stanislav 11-11); only points clearly outside Tbilisi get none
_P_TYPE = {"apartment": "Apartment", "house": "House", "commercial": "Commercial"}
_P_BUILDING = {"new": "NewBuilding", "old": "OldBuilding", "historical": "HistoricalBuilding"}
_P_DESIGN = {"new": "New", "mixed": "Mixed", "old": "Old", "retro": "Retro", "needs_repair": "UnderRepair"}
_P_HEATING = {"central": "CentralHeating", "gas": "GasHeating", "electric": "ElectricalHeating"}
_P_PETS = {"allowed": "Allowed", "not_allowed": "NotAllowed", "by_agreement": "ByAgreement"}
_P_AMENITY = {"balcony": "Balcony", "terrace": "Terrace", "courtyard": "Courtyard", "wifi": "WiFi", "tv": "TV",
              "dishwasher": "Dishwasher", "stove": "Stove", "microwave": "Microwave", "washing_machine": "WashingMachine",
              "vacuum": "VacuumCleaner", "fireplace": "Fireplace", "elevator": "Elevator", "oven": "Oven",
              "parking": "ParkingPlace", "conditioner": "Conditioner"}


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def nearest_metro_m(lat: float, lng: float) -> tuple[float, str]:
    """(metres, POST_ONE metro tag) of the closest station, any distance."""
    return min((111_000 * math.hypot(lat - a, (lng - b) * 0.75), t) for t, (a, b) in METRO.items())


def nearest_metro(lat: float | None, lng: float | None) -> str | None:
    if lat is None or lng is None:
        return None
    d, tag = nearest_metro_m(lat, lng)
    return tag if d <= METRO_RADIUS_M else None


def listing_prefill(listing: Listing) -> tuple[dict, dict]:
    """What the parsed ad already knows, as (card fields, post_fields) for the agent's generator: total floors, type,
    building, design, bathrooms, heating, amenities, pets, tenants, terms, deposit, metro (named in the ad, else the
    nearest station by the pin)."""
    d = jload(listing.details, {}) or {}
    card = {"total_floors": _int(d.get("floors"))}
    yes = [_P_AMENITY[a] for a in d.get("yes") or [] if a in _P_AMENITY]
    yes += {"bath": ["Bath"], "shower": ["Shower"], "both": ["Bath", "Shower"]}.get(d.get("bath"), [])
    baths = _int(d.get("bathrooms"))
    tenants = re.findall(r"\d+", str(d.get("tenants") or ""))
    terms = [f"{m}Month" for m in d.get("terms") or [] if isinstance(m, int) and f"{m}Month" in POST_MANY["periods"]]
    pf = {"prop_type": _P_TYPE.get(d.get("property")), "building": _P_BUILDING.get(d.get("building")),
          "design": _P_DESIGN.get(d.get("condition")), "heating": _P_HEATING.get(d.get("heating")),
          "pets": _P_PETS.get(d.get("pets")),
          "bathrooms": f"{min(baths, 4)}Bathroom" if baths and baths >= 2 else None,
          "metro": METRO_NAME.get(d.get("metro_en") or "") or nearest_metro(listing.lat, listing.lng),
          "amenities": yes, "periods": terms,
          "tenants": int(tenants[-1]) if tenants else None, "deposit": _int(d.get("deposit_usd"))}
    return {k: v for k, v in card.items() if v}, clean_post_fields({k: v for k, v in pf.items() if v not in (None, [])})


def prefill(p: Property, listing: Listing | None) -> bool:
    """Fill the object's EMPTY generator fields from its ad; what the agent already picked is never touched."""
    if listing is None:
        return False
    card, pf = listing_prefill(listing)
    changed = False
    for k, v in card.items():
        if getattr(p, k) in (None, ""):
            setattr(p, k, v)
            changed = True
    cur = dict(p.post_fields or {})
    for k, v in pf.items():
        if cur.get(k) in (None, "", []):
            cur[k], changed = v, True
    if changed:
        p.post_fields = cur
    return changed


def maps_url(db: Session, p: Property) -> str | None:
    """Google Maps link for the post address: the agent's point, the origin ad's pin, else a search by the address."""
    if p.lat is not None and p.lng is not None:
        return f"https://maps.google.com/?q={p.lat},{p.lng}"
    listing = db.get(Listing, p.listing_id) if p.listing_id else None
    if listing is not None and listing.lat is not None and listing.lng is not None:
        return f"https://maps.google.com/?q={listing.lat},{listing.lng}"
    if not p.address:
        return None
    q = f"{p.address}, Tbilisi".replace(" ", "+")  # non-ASCII stays readable in the editable text; Telegram takes IRIs
    return "https://www.google.com/maps/search/?api=1&query=" + "".join(c if ord(c) > 127 else quote(c, safe=",+") for c in q)


def phone_line(agent: User | None) -> str:
    """The number is David's, so #David sits next to it; the object's agent follows as the LAST hashtag of the line
    (inbound.parse_listing attributes a channel post to the last tag on the +995 line)."""
    tag = users_svc.tag_of(agent)
    return "+995 599 20 67 16 #David" + (f" · Agent {tag}" if tag and tag.lower() != "#david" else "")


def post_text(db: Session, p: Property) -> str:
    """Channel caption in the agency's own format: a port of buildRentPost() / buildSalePost() from the agency's old HTML
    post generator, fed by the object's fields and post_fields (empty ones are left out, as the generator does). The
    address is `📍[address](maps link)`: notify.post_html turns it into a link. The hashtag is the agent's agent_tag."""
    f = p.post_fields or {}
    sale = f.get("deal") == "Sale"
    tag = lambda k: f"#{f[k]}" if f.get(k) else None  # noqa: E731
    district = DISTRICT_TAG.get((p.district or "").strip(), f"#{(p.district or 'Tbilisi').replace(' ', '')}")
    lines = [" ".join(x for x in (district, f.get("metro") and f"🚇 #{f['metro']}") if x)]
    if p.address:
        url = maps_url(db, p)
        lines.append(f"📍[{p.address}]({url})" if url else f"📍{p.address}")
    if f.get("headline"):
        lines += ["", f"🌟 {f['headline']} 🌟" if sale else f"⭐️ {f['headline']} ⭐️"]
    lines.append("")
    if p.is_exclusive:
        lines += ["❗️#Exclusive", ""]
    if f.get("discount"):
        lines += ["❗️#Discount", ""]
    beds = "#Studio " if p.bedrooms == 0 else (f"#{p.bedrooms}Bed " if p.bedrooms else "")
    if sale and p.bedrooms and p.bedrooms >= 6:
        beds = "#6Bedplus "  # the old sale block: #5Bed, then #6Bedplus
    lines.append(f"🏢 {beds}{tag('prop_type') or '#Apartment'} for {'#Sale' if sale else '#Rent'}")
    design = "#NewApartment" if sale and f.get("design") == "New" else tag("design")  # the old sale block's tag
    style = [x for x in (tag("building"), design, sale and tag("condition")) if x]
    if style:
        lines.append("✨ " + " | ".join(style))
    spec = []
    if p.area:
        spec.append(f"{float(p.area):g} {'Sq.m' if sale else 'sq.m'}")
    if p.floor:
        spec.append(f"{p.floor}/{p.total_floors} Floor" if p.total_floors else f"{p.floor} Floor")
    spec += [x for x in (tag("heating"), tag("bathrooms")) if x]
    if spec:
        lines.append("🏠 " + " | ".join(spec))
    lines.append("")
    am = f.get("amenities") or []
    if sale:
        am = [a for a in SALE_AMENITIES if a in am]
        lines += [" ".join(f"✅ #{a}" for a in am[i:i + 3]) for i in range(0, len(am), 3)]
    else:
        has = [a for a in am if a not in FIXED_AMENITIES] + [a for a in FIXED_AMENITIES if a in am]
        lines += ["  ".join(f"✅\u00a0#{a}" for a in has[i:i + 3]) for i in range(0, len(has), 3)]
        missing = [a for a in FIXED_AMENITIES if a not in am]
        if am and missing:
            lines.append("  ".join(f"✖️\u00a0#{a}" for a in missing))
        lines.append("")
        if f.get("tenants"):
            lines.append(f"👬Tenants: 1-{f['tenants']}")
        if f.get("pets"):
            lines.append(f"🐕Pets: #{f['pets']}")
        if f.get("periods"):
            lines.append("🕐 " + " ".join(f"#{x}" for x in f["periods"]))
    lines.append("")
    if sale:
        price = f.get("sale_price") or (int(p.price) if p.price is not None else None)
        if price:
            lines.append(f"💰{price:,}$".replace(",", "."))
        lines.append("0% Commission")
        if price:
            lines.append(next((f"#Price{lo}to{hi}" for lo, hi in zip((0,) + SALE_PRICE_STEPS, SALE_PRICE_STEPS)
                               if price <= hi), "#Price1000000plus"))
    else:
        if p.price is not None:
            price = int(p.price)
            deposit = f.get("deposit", price)
            lines.append(f"💰 {price}$" + (f" + Deposit {deposit}$" if deposit else ""))
        lines.append("0% Commission")
        if p.price is not None:
            lines.append(next((t for top, t in PRICE_TAGS if int(p.price) <= top), "#Price1200plus"))
    agent = db.get(User, p.agent_id) if p.agent_id else None
    lines += ["", f"📲 @David_Tibelashvili | \n{phone_line(agent)}", "", "🌟 Check all listings | Reviews",
              "", "📍APARTMENTS ON MAP📍", "", "📱 Instagram 🗳 FB 🎥 YouTube"]
    if sale:
        lines += ["", "SALE IN TBILISI🇬🇪"]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _split(tag: str) -> str:
    """VazhaPshavela → Vazha Pshavela, 6Month → 6 Month."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=\d)(?=[A-Za-z])", " ", tag)


def _human(tag: str) -> str:
    """Generator tag → words: CentralHeating → Central heating, 6Month → 6 month, SmartTV → Smart TV."""
    if tag in ("WiFi", "TV", "SmartTV"):
        return {"SmartTV": "Smart TV"}.get(tag, tag)
    w = _split(tag).split()
    return " ".join([w[0]] + [x if x.isupper() else x.lower() for x in w[1:]]) if w else tag


# a line of an agency post that the rich post already shows as fields (old-base objects keep the whole old post as
# their description): hashtags, the 📍 maps link, amenities, price/deposit, contacts, the «APARTMENTS ON MAP» footer
MAPS_POINT = re.compile(r"!3d(4[01]\.\d+)!4d(4[3-6]\.\d+)|@(4[01]\.\d+),(4[3-6]\.\d+)")  # Georgia only
POST_SERVICE_LINE = re.compile(r"#\w|^\s*[📍🏢✨🏠✅✖️👬🕐💰📲🚇🌟📱🗳🎥❗]|\]\(https?://|commission|deposit|sq\.?\s?m|\+995|apartments on map", re.I)


def prose(text: str | None) -> str:
    """The description's own words for the rich post: the agency post's service lines dropped, blank runs squeezed."""
    lines = [ln.strip() for ln in (text or "").replace("\xa0", " ").splitlines()]
    keep = [ln for ln in lines if not POST_SERVICE_LINE.search(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


def post_rich(db: Session, p: Property, media: list[str]) -> str:
    """The post as a Telegram rich message (Bot API sendRichMessage) in the @tbilisi_rent style (Irakli's video
    2026-11-09): photo slideshow, «$price · beds · district» title, «building · m² · floor · ID» line, 📍 address (the
    map link) and Ⓜ metro, the description, collapsed «Parameters and amenities» (a table + one ✅ line) and «Tags», then
    two buttons in a row: «On the map» (our /map page with the pin) and «I like it» (a bot button: a lead). No map block,
    no footer (the contacts sit in Tags); an old-base description is cut to its own words (prose()). media: the slideshow items as rich-message links (tg://photo?id=m0, tg://video?id=m1)."""
    f, h = p.post_fields or {}, lambda x: html_.escape(str(x), quote=True)
    sale = f.get("deal") == "Sale"
    district = DISTRICT_TAG.get((p.district or "").strip(), "#" + (p.district or "Tbilisi").replace(" ", "")).lstrip("#")
    beds = "Studio" if p.bedrooms == 0 else (f"{p.bedrooms} bedroom{'s' if p.bedrooms != 1 else ''}" if p.bedrooms else None)
    commercial = f.get("prop_type") not in (None, "Apartment", "House", "Duplex", "Townhouse")
    price = f.get("sale_price") or (int(p.price) if p.price is not None else None)
    head = " · ".join(x for x in (price and f"${price:,}".replace(",", " "),
                                  _human(f["prop_type"]).lower() if commercial else beds, _human(district)) if x)
    marks = [x for x, on in (("Exclusive", p.is_exclusive), ("Discount", f.get("discount"))) if on]
    sub = " · ".join(x for x in (*marks, f.get("building") and _human(f["building"]), p.area and f"{float(p.area):g} m²",
                                 p.floor and (f"{p.floor}/{p.total_floors} floor" if p.total_floors else f"{p.floor} floor"),
                                 f"ID: {p.id}") if x)
    out = []
    if media:
        out.append("<tg-slideshow>" + "".join(f'<video src="{m}"/>' if "video" in m else f'<img src="{m}"/>' for m in media)
                   + "</tg-slideshow>")
    out.append(f"<h2>{h(head)}</h2><p>{h(sub)}</p>")
    url = maps_url(db, p) if p.address else None
    where = [f'📍 <a href="{h(url)}">{h(p.address)}</a>' if url else (p.address and f"📍 {h(p.address)}"),
             f.get("metro") and f"Ⓜ️ {h(_split(f['metro']))}"]
    if any(where):
        out.append("<p>" + "<br>".join(x for x in where if x) + "</p>")
    text = "\n".join(x for x in (f.get("headline") and f"⭐️ {f['headline']}", prose(p.description)) if x)
    if text:
        out.append("<p>" + h(text).replace("\n", "<br>") + "</p>")
    am = f.get("amenities") or []
    rows = [("Rooms", p.rooms), ("Bathrooms", (f.get("bathrooms") or "")[:1] or (1 if p.bedrooms is not None else None)),
            ("Air conditioners", "yes" if "Conditioner" in am else None), ("Balconies", "yes" if "Balcony" in am else None),
            ("Heating", f.get("heating") and _human(f["heating"])), ("Design", f.get("design") and _human(f["design"])),
            ("Condition", sale and f.get("condition") and _human(f["condition"])),
            ("Pets", f.get("pets") and _human(f["pets"])), ("Tenants", f.get("tenants") and f"up to {f['tenants']}"),
            ("Lease term", f.get("periods") and ", ".join(_human(x) + ("s" if x not in ("1Day", "1Month") else "") for x in f["periods"])),
            ("Deposit", not sale and price and (f"${f.get('deposit', price)}" if f.get("deposit", price) else "none")),
            ("Commission", "0%")]
    table = "".join(f"<tr><td>{h(k)}</td><td>{h(v)}</td></tr>" for k, v in rows if v)
    rest = [_human(a) for a in am if a not in ("Conditioner", "Balcony")]
    params = f"<table striped compact>{table}</table>" + (f"<p>✅ {h(' · '.join(rest))}</p>" if rest else "")
    out.append(f"<details><summary>Parameters and amenities</summary>{params}</details>")
    agent = db.get(User, p.agent_id) if p.agent_id else None
    tags = " ".join(hashtags(post_text(db, p)))
    out.append(f"<details><summary>Tags</summary><p>ID: {p.id}<br>{h(tags)}</p>"
               f"<p>📲 {h(phone_line(agent))}<br>"
               '<a href="https://t.me/rent_tbilisi_ge/9859">🌟 All listings</a> · <a href="https://t.me/reviews_rent_tbilisi">Reviews</a> · '
               '<a href="https://www.instagram.com/rent_in_tbilisi/profilecard/?igsh=N3kxbTd4bTdkN2Vk">Instagram</a> · '
               '<a href="https://www.facebook.com/share/18GSsdio3b/?mibextid=LQQJ4d">FB</a> · '
               '<a href="https://youtube.com/@rentintbilisi">YouTube</a></p></details>')
    btn = lambda text, href, style="": f'<tg-button type="url"{style} url="{h(href)}">{h(text)}</tg-button>'  # noqa: E731
    # «On the map»: our map Mini App page with the object's pin (as «📍 На карте» on the bot's cards; a channel can't
    # open a web_app button, so it's a link), Google Maps without a point. «I like it»: a plain bot button (callback
    # plike:<id>); the bot answers with t.me/<bot>?start=p<id> -> the same lead as before (bot.on_button)
    listing = db.get(Listing, p.listing_id) if p.listing_id else None
    pin, at = None, None
    if p.lat is not None and p.lng is not None:  # the agent checked it on the map (AddressField)
        at, prec = (float(p.lat), float(p.lng)), "точный"
    elif listing is not None and listing.lat is not None and listing.lng is not None:
        at, prec = (float(listing.lat), float(listing.lng)), listing.geo_precision or ""
    elif m := MAPS_POINT.search(p.description or ""):  # an old-base post's Google Maps link: !3d<lat>!4d<lng> or @lat,lng
        at, prec = (float(m[1] or m[3]), float(m[2] or m[4])), "точный"
    if at:
        pin = f"{env.PUBLIC_URL.rstrip('/')}/map?" + urlencode({"pin": f"{at[0]:.6f},{at[1]:.6f}", "prec": prec,
                                                                "title": head, "lang": "en"})
    like = f'<tg-button type="callback_data" style="primary" data="plike:{p.id}">❤️ I like it</tg-button>'
    where = hge_map_url(p.address) if p.address else pin or url  # Irakli's map on this object, else our pin map
    out.append("<tg-button-row>" + (btn("📍 On the map", where) if where else "") + like + "</tg-button-row>")
    # «Contact» (back by Stanislav 11-11, own full-width row): /start p<id> -> journey.on_post_start, a lead as before
    out.append("<tg-button-row>" + btn("💬 Contact", f"https://t.me/{env.BOT_USERNAME.lstrip('@')}?start=p{p.id}")
               + "</tg-button-row>")
    return "".join(out)


def hge_map_url(address: str | None) -> str:
    """Irakli's site map (hge.ge/map/, built from the channel posts, whose titles are these addresses) filtered to one
    object: ?q=<address> shows it alone and zooms in; without an address, the whole map (Irakli / Stanislav 11-11)."""
    q = " ".join(w.strip(",.;") for w in (address or "").split())
    return "https://hge.ge/map/" + (f"?{urlencode({'q': q})}" if q else "")


def record_publication(db: Session, p: Property, channel: str, url: str | None, message_id: str | None) -> PropertyPublication:
    """One row per post: the same post (by message id or link) reported again updates it, a new post adds a row.
    Links a matching agency channel post (channel_posts) to the object. No commit."""
    q = select(PropertyPublication).where(PropertyPublication.property_id == p.id, PropertyPublication.channel == channel)
    keys = [c for c in ((PropertyPublication.message_id, message_id), (PropertyPublication.url, url)) if c[1]]
    pub = db.scalar(q.where(or_(*(col == v for col, v in keys)))) if keys else None
    if pub is None:
        pub = PropertyPublication(property_id=p.id, channel=channel)
        db.add(pub)
    pub.status, pub.removed_at, pub.published_at = "published", None, p.published_at or now()
    pub.url, pub.message_id = url or pub.url, message_id or pub.message_id
    if channel == "tg" and pub.url:
        db.execute(update_(ChannelPost).where(ChannelPost.link == pub.url).values(property_id=p.id))
    db.flush()
    return pub


def hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"#\w+", text or "")))


def publish(db: Session, pid: int, actor: User, text: str | None = None, media_ids: list[int] | None = None,
            channel: str = "tg", comment: str | None = None) -> Property:
    """B7: the operator publishes from the object card: the caption as edited (default post_text), the picked media in
    the picked order (default all), to a channel of PUBLICATION_CHANNELS. Publishing an object in review approves it.
    Without BOT_TOKEN in dev the post is a dry run (recorded without a link); a Telegram failure leaves it unpublished."""
    p = _prop(db, pid, lock=True)
    if actor.role not in STAFF:
        raise forbidden("only operators publish")
    if channel not in PUBLICATION_CHANNELS:
        raise DomainError("bad_channel", f"unknown channel {channel}")
    _require_status(p, "review", "published")
    _require_no_exclusive_conflict(db, p)
    if media_ids is not None:
        own = {m.id for m in media(db, p.id)}
        if not set(media_ids) <= own:
            raise DomainError("not_found", "media not found")
    auto = post_text(db, p)
    p.post_text = (text or "").strip() or auto  # transient attributes read by notify.publish_post
    # the rich post is built from the fields: a caption the operator rewrote by hand goes out as the plain album
    p.post_rich = (lambda ids: post_rich(db, p, ids)) if p.post_text == auto else None
    p.post_media_ids = media_ids
    ref = _notify("publish_post", p)
    dry_run = ref is None and not env.BOT_TOKEN and env.APP_ENV == "dev"
    if ref is None and not dry_run:
        raise DomainError("publish_failed", "Telegram did not accept the post", 502)
    if ref:
        p.post_message_id, p.post_url = str(ref[0]), ref[1]
    p.published_at = now()
    p.verified_at = p.verified_at or p.published_at
    if p.status == "review":
        p.review_comment, p.reviewed_by, p.reviewed_at = comment, actor.id, now()
        log_event(db, "property", p.id, "approved", actor, comment=comment)
    record_publication(db, p, channel, p.post_url if ref else None, p.post_message_id if ref else None)
    _status(db, p, "published", actor, post_url=p.post_url if ref else None, channel=channel,
            **({"dry_run": True} if dry_run else {}))
    db.commit()
    from app.services import catalog
    if catalog.sync(db, p):  # clients find it in the bot at once
        db.commit()
    _notify("property_published", p)
    return p


def resume(db: Session, pid: int, actor: User) -> Property:
    """«Снять с паузы»: a paused object goes back to the base as it was before (published, if it ever was)."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, actor)
    if p.status != "archived" or p.archive_reason != "paused":
        raise DomainError("bad_status", "Объект не на паузе")
    p.archive_reason, p.archive_comment, p.archived_at = None, None, None
    _status(db, p, "published" if p.published_at else "media_uploaded", actor, "resumed")
    db.commit()
    return p


def mark_rented(db: Session, pid: int, actor: User | None) -> None:
    p = db.get(Property, pid)
    if p and p.status != "rented":
        p.archive_reason = "rented_by_us"
        _status(db, p, "rented", actor, reason="rented_by_us")


def unmark_rented(db: Session, pid: int, actor: User | None) -> None:
    """Confirmed deal cancelled → the object is back on the market."""
    p = db.get(Property, pid)
    if p and p.status == "rented":
        p.archive_reason = None
        _status(db, p, "published", actor, "rent_cancelled")


# ---------------------------------------------------------------- exclusives (stage 4)

def external_ads(db: Session, pids: list[int]) -> dict[int, list[dict]]:
    """Object id → its outside ads that are still live (not gone from the ingest, not checked as removed by an operator).
    An exclusive object must have none: they are its «Эксклюзив висит на …» conflict."""
    out: dict[int, list[dict]] = {}
    if not pids:
        return out
    for pid, lid, src, url in db.execute(
            select(PropertyListing.property_id, PropertyListing.listing_id, PropertyListing.source, PropertyListing.url)
            .join(Listing, Listing.id == PropertyListing.listing_id)
            .where(PropertyListing.property_id.in_(pids), PropertyListing.removed_checked_at.is_(None),
                   Listing.status != "gone", Listing.source.not_in(("agency", "object"))).order_by(PropertyListing.id)):
        out.setdefault(pid, []).append({"listing_id": lid, "source": src, "url": url})
    return out


def _require_no_exclusive_conflict(db: Session, p: Property) -> None:
    ads = external_ads(db, [p.id]).get(p.id) if p.is_exclusive else None
    if ads:
        raise DomainError("exclusive_conflict", "the exclusive is still advertised on "
                          + ", ".join(sorted({a["source"] or "?" for a in ads})), 409)


def ad_removed(db: Session, pid: int, listing_id: str, actor: User) -> Property:
    """«Проверено: снято»: the operator opened the outside ad and saw it is taken down; it stops blocking the exclusive.
    A manual check on purpose: ss.ge / myhome block server requests (Cloudflare), an HTTP probe can't tell "removed"
    from "blocked". The ingest marking the ad gone clears it too."""
    if actor.role not in STAFF:
        raise forbidden("only operators confirm a removed ad")
    p = _prop(db, pid, lock=True)
    link = db.scalar(select(PropertyListing).where(PropertyListing.property_id == p.id,
                                                   PropertyListing.listing_id == listing_id))
    if link is None:
        raise DomainError("not_found", "the ad is not linked to this object")
    link.removed_checked_at, link.removed_checked_by = now(), actor.id
    log_event(db, "property", p.id, "ad_removed_checked", actor, listing_id=listing_id, source=link.source, url=link.url)
    db.commit()
    return p


# ---------------------------------------------------------------- dedup

def check_duplicate(db: Session, phone: str | None = None, address: str | None = None, url: str | None = None,
                    cadastral: str | None = None, exclude_property: int | None = None) -> dict:
    """Port of apps-script checkProperty (Owner_Base phone/address) + link and cadastral matches.
    level: none | warn (one signal) | danger (phone and address, or same link/cadastral)."""
    results = []
    if (phone and len(norm_phone(phone)) > 4) or (address and len(norm_address(address)) > 2):
        for o in db.scalars(select(Owner)):
            pm = bool(phone) and phone_match(phone, o.phone)
            am = bool(address) and address_match(address, o.address)
            if pm or am:
                results.append({"source": "owners", "owner_id": o.id, "owner": o.name, "phone": o.phone,
                                "address": o.address, "link": o.link, "is_agent": o.is_agent,
                                "phone_match": pm, "addr_match": am})
    if url:
        q = select(Property).where(Property.source_url == url.strip())
        if exclude_property:
            q = q.where(Property.id != exclude_property)
        for p in db.scalars(q):
            results.append({"source": "properties", "property_id": p.id, "address": p.address, "status": p.status,
                            "link": p.source_url, "url_match": True})
    if cadastral and cadastral.strip():
        c = cadastral.strip()
        for p in db.scalars(select(Property).where(Property.cadastral_code == c)):
            if p.id != exclude_property:
                results.append({"source": "properties", "property_id": p.id, "address": p.address, "status": p.status,
                                "cadastral_match": True})
        for d in db.scalars(select(Deal).where(Deal.cadastral == c)):
            results.append({"source": "deals", "deal_id": d.id, "address": d.address, "status": d.status,
                            "rent_to": d.rent_to.isoformat() if d.rent_to else None, "cadastral_match": True})
    level = "none"
    for r in results:
        if (r.get("phone_match") and r.get("addr_match")) or r.get("url_match") or r.get("cadastral_match"):
            level = "danger"
            break
        level = "warn"
    return {"level": level, "results": results}


# ---------------------------------------------------------------- reading

def can_see_owner(p: Property, user: User) -> bool:
    return user.role in STAFF or p.agent_id == user.id


def prefetch(db: Session, rows: list[Property], cache: dict) -> None:
    """Batch-load agents, owners and media counts for out() (lists/boards without N+1)."""
    def load(model, ids):
        ids = {i for i in ids if i is not None and (model.__name__, i) not in cache}
        if ids:
            for obj in db.scalars(select(model).where(model.id.in_(ids))):
                cache[(model.__name__, obj.id)] = obj
            for i in ids:
                cache.setdefault((model.__name__, i), None)
    load(Owner, {p.owner_id for p in rows})
    owners = [cache[("Owner", p.owner_id)] for p in rows if p.owner_id is not None]
    load(User, {p.agent_id for p in rows} | {o.agent_id for o in owners if o is not None})
    ex = [p.id for p in rows if p.is_exclusive and ("conflict", p.id) not in cache]
    if ex:
        ads = external_ads(db, ex)
        for pid in ex:
            cache[("conflict", pid)] = ads.get(pid, [])
    pids = [p.id for p in rows if ("media_count", p.id) not in cache]
    if pids:
        counts = dict(db.execute(select(PropertyMedia.property_id, func.count()).where(PropertyMedia.property_id.in_(pids),
                                                                                         PropertyMedia.kind.in_(POST_KINDS))
                                 .group_by(PropertyMedia.property_id)).all())
        for pid in pids:
            cache[("media_count", pid)] = counts.get(pid, 0)
    _covers(db, [p for p in rows if ("cover", p.id) not in cache], cache)


def _covers(db: Session, rows: list[Property], cache: dict) -> None:
    """List cards' photo: the object's first own photo, else the origin ad's first photo."""
    if not rows:
        return
    own = {}
    for pid, path in db.execute(select(PropertyMedia.property_id, PropertyMedia.path)
                                .where(PropertyMedia.property_id.in_([p.id for p in rows]), PropertyMedia.kind == "photo")
                                .order_by(PropertyMedia.position, PropertyMedia.id)):
        own.setdefault(pid, f"/api/media/{path}")
    lids = {p.listing_id for p in rows if p.listing_id and p.id not in own}
    with_ad = {lid for lid, ph in db.execute(select(Listing.id, Listing.photos).where(Listing.id.in_(lids))) if jload(ph, [])} if lids else set()
    for p in rows:
        cache[("cover", p.id)] = own.get(p.id) or (f"/api/listing-photos/{p.listing_id}/0" if p.listing_id in with_ad else None)


def outs(db: Session, rows, user: User) -> list[dict]:
    rows, cache = list(rows), {}
    prefetch(db, rows, cache)
    return [out(db, p, user, cache=cache) for p in rows]


def out(db: Session, p: Property, user: User, full: bool = False, cache: dict | None = None) -> dict:
    cache = cache if cache is not None else {}

    def get(model, ident):
        if ident is None:
            return None
        k = (model.__name__, ident)
        if k not in cache:
            cache[k] = db.get(model, ident)
        return cache[k]

    agent = get(User, p.agent_id)
    d = {c: getattr(p, c) for c in ("id", "listing_id", "source_type", "source_url", "status", "agent_id", "call_attempts",
                                    "address", "district", "lat", "lng", "cadastral_code", "currency", "bedrooms", "rooms", "floor",
                                    "total_floors", "description", "is_exclusive", "checklist", "ai_flags",
                                    "duplicate_of", "dup_check", "review_comment", "reviewed_by", "post_url",
                                    "post_message_id", "owner_id", "post_fields", "archive_reason", "archive_comment")}
    for c in ("taken_at", "next_call_at", "shoot_at", "reviewed_at", "published_at", "verified_at", "archived_at",
              "fast_track_at", "created_at", "updated_at"):
        v = getattr(p, c)
        d[c] = v.isoformat() if v else None
    d["price"] = float(p.price) if p.price is not None else None
    d["area"] = float(p.area) if p.area is not None else None
    d.update({c: getattr(p, c) for c in LEASE})
    d["rent_from"], d["rent_to"] = (p.rent_from.isoformat() if p.rent_from else None), (p.rent_to.isoformat() if p.rent_to else None)
    ref = lambda u: {"id": u.id, "name": u.name, "username": u.username, "agent_tag": u.agent_tag} if u else None  # noqa: E731
    d["agent"] = ref(agent)
    d["locked"] = bool(p.agent_id) and p.agent_id != user.id and user.role not in STAFF
    d["locked_fields"] = list(LOCKED_AFTER_VERIFY) if is_locked_for(p, user) else []
    d["needs_check"] = needs_check(p)
    owner = get(Owner, p.owner_id)
    if owner:
        visible = can_see_owner(p, user)
        # stage 6.5: the owner's agent is the agent of all his objects (may differ from this object's after a staff override)
        d["owner"] = {"id": owner.id, "name": owner.name, "is_agent": owner.is_agent,
                      "phone": owner.phone if visible else None, "agent": ref(get(User, owner.agent_id))}
    else:
        d["owner"] = None
    k = ("conflict", p.id)
    if p.is_exclusive and k not in cache:
        cache[k] = external_ads(db, [p.id]).get(p.id, [])
    visible = can_see_owner(p, user)  # ad links lead to the owner's phone
    d["exclusive_conflict"] = [{**a, "url": a["url"] if visible else None} for a in cache.get(k, [])] if p.is_exclusive else []
    if full:
        d["media"] = [{"id": m.id, "kind": m.kind, "room": m.room, "position": m.position, "url": f"/api/media/{m.path}",
                       "preview": f"/api/media/{m.path}?p=1"}
                      for m in media(db, p.id)]
        d["missing"] = missing_fields(p)  # what still blocks «Отправить оператору»
        d["room_picker"] = settings_svc.get(db, "room_picker") != "0"  # Управление → Настройки; rooms never required
        c = contract(db, p.id) if p.is_exclusive else None
        d["exclusive_contract"] = {"id": c.id, "url": f"/api/media/{c.path}", "created_at": c.created_at} if c else None
        if p.is_exclusive and not c:
            d["missing"].append("эксклюзивный договор (PDF)")
        d["missing"] += missing_owner_docs(db, p)
        # the passport and the extract: only staff and the object's agent (the files themselves: api media_file)
        d["owner_docs"] = {k: {"id": m.id, "url": f"/api/media/{m.path}", "created_at": m.created_at}
                           for k, m in owner_docs(db, p.id).items()} if p.is_exclusive and can_see_owner(p, user) else {}
        if app := d["owner_docs"] and registry_app(db, p.id):
            d["registry_app"] = {**app, "url": f"https://naprweb.reestri.gov.ge/_dea/#/view/{app['app_id']}"}
        d["exclusive_sign_token"] = sign_token(p.id) if p.is_exclusive and can_see_owner(p, user) else None
        listing = db.get(Listing, p.listing_id) if p.listing_id else None
        d["listing"] = listing_out(listing, full=can_see_owner(p, user)) if listing else None
        d.update(card(db, p, user))
        if can_see_owner(p, user):
            from app.services import change_requests
            d["change_requests"] = change_requests.outs(db, change_requests.pending_for(db, p))
    else:
        k = ("media_count", p.id)
        if k not in cache:
            cache[k] = db.scalar(select(func.count()).where(PropertyMedia.property_id == p.id, PropertyMedia.kind.in_(POST_KINDS)))
        d["media_count"] = cache[k]
        if ("cover", p.id) not in cache:
            _covers(db, [p], cache)
        d["photo"] = cache[("cover", p.id)]
    return d


def card(db: Session, p: Property, user: User) -> dict:
    """Full object card extras: our publications, the object's ads on other sites, its deals."""
    pubs = db.scalars(select(PropertyPublication).where(PropertyPublication.property_id == p.id)
                      .order_by(PropertyPublication.published_at.desc().nullslast(), PropertyPublication.id.desc()))
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    contacts = can_see_owner(p, user)  # ad links lead to the owner's phone: same rule as the owner block
    ads = []
    for link, l in db.execute(select(PropertyListing, Listing).join(Listing, Listing.id == PropertyListing.listing_id)
                              .where(PropertyListing.property_id == p.id).order_by(PropertyListing.id)):
        ads.append({"listing_id": l.id, "source": link.source or source_type_of(l), "status": l.status,
                    "url": (link.url or (jload(l.urls, []) or [None])[0]) if contacts else None,
                    "price_usd": l.price_usd, "first_seen": iso(link.first_seen_at) or l.first_seen,
                    "last_seen": iso(link.last_seen_at) or l.last_seen, "origin": l.id == p.listing_id,
                    "removed_checked_at": iso(link.removed_checked_at)})
    deals = []
    if contacts:
        names = {}
        for dl in db.scalars(select(Deal).where(Deal.property_id == p.id).order_by(Deal.id.desc())):
            if dl.agent_id and dl.agent_id not in names:
                names[dl.agent_id] = db.scalar(select(User.name).where(User.id == dl.agent_id))
            deals.append({"id": dl.id, "status": dl.status, "deal_type": dl.deal_type,
                          "price": float(dl.price) if dl.price is not None else None, "agent": names.get(dl.agent_id),
                          "rent_from": iso(dl.rent_from), "rent_to": iso(dl.rent_to), "created_at": iso(dl.created_at)})
    return {"publications": [{"id": x.id, "channel": x.channel, "status": x.status, "url": x.url, "message_id": x.message_id,
                              "published_at": iso(x.published_at), "removed_at": iso(x.removed_at)} for x in pubs],
            "listings": ads, "deals": deals}


# «База объектов»: published (shot + operator approved), or rented after that; published_at keeps out an object that got
# «rented»/«published» through a deal without ever being published (mark_rented / unmark_rented)
BASE_STATUSES = ("published", "rented")


def query(db: Session, user: User, status: str | None = None, agent_id: int | None = None, q: str | None = None,
          mine: bool = False, everyone: bool = False, base: bool = False, rented: bool = False):
    stmt = select(Property)
    if rented:  # the calendar: objects with a hand-typed lease end
        stmt = stmt.where(Property.rent_to.isnot(None))
    if base:
        stmt = stmt.where(Property.status.in_(BASE_STATUSES), Property.published_at.isnot(None))
    # agents see only their own objects in every list, «База объектов» too (Stanislav 11-11); staff see all.
    # everyone: the deal form's picker only (deal_search), where another agent's object comes blocked, without data
    if mine or (user.role not in STAFF and not everyone):
        stmt = stmt.where(Property.agent_id == user.id)
    elif agent_id:
        stmt = stmt.where(Property.agent_id == agent_id)
    if status:
        stmt = stmt.where(Property.status.in_(status.split(",")))
    if q and (words := [w.lstrip("#") for w in q.split() if w.lstrip("#")]):
        def word(w: str):  # every word must hit some field: «Vake 2Bed», «Saburtalo 600», «Zaza», an id
            like = f"%{w}%"
            conds = [Property.address.ilike(like), Property.district.ilike(like), Property.cadastral_code.ilike(like),
                     Property.description.ilike(like),
                     Property.agent_id.in_(select(User.id).where(or_(User.name.ilike(like), User.agent_tag.ilike(like))))]
            if w.isdigit():
                conds += [Property.id == int(w), Property.price == int(w)]
            return or_(*conds)
        cond = and_(*map(word, words))
        pn = norm_phone(re.sub(r"[^\d+]", "", q))
        if len(pn) >= 5 and not re.search(r"[^\d\s+()\-]", q):  # the owner's phone, any format
            cond = or_(cond, Property.owner_id.in_(select(Owner.id).where(Owner.phone_norm.like(f"%{pn}%"))))
        stmt = stmt.where(cond)
    # the base by publication date, the archive by archiving date; work lists by the last change
    first = (Property.published_at if base else Property.archived_at if status == "archived" else Property.updated_at)
    return stmt.order_by(first.desc().nulls_last(), Property.id.desc())


def status_counts(db: Session) -> dict[str, int]:
    return dict(db.execute(select(Property.status, func.count()).group_by(Property.status)).all())


# ---------------------------------------------------------------- deals (stage 7)

def needs_check(p: Property) -> bool:
    """«Новый объект из сделки — проверить»: typed in the deal form, not checked by an operator yet."""
    return p.source_type == "deal" and p.verified_at is None


def deal_block(db: Session, p: Property, user: User) -> str | None:
    """Why this agent can't file a deal on the object: it is another agent's (the owner's agent, stage 6.5, else the
    object's). Staff are never blocked."""
    if user.role in STAFF:
        return None
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    bound = o.agent_id if o is not None and o.agent_id and not o.is_agent else p.agent_id
    if bound and bound != user.id:
        return f"Объект закреплён за {users_svc.tag_of(db.get(User, bound))}"
    return None


def deal_fields(db: Session, p: Property, user: User | None, deal_type: str = "rent") -> dict:
    """The deal's object fields taken from the object (read-only in the form). Owner and ad link only for those who see
    the owner (user None = the system)."""
    pf = p.post_fields or {}
    price = pf.get("sale_price") if deal_type == "sale" and pf.get("sale_price") else p.price
    d = {"address": p.address, "cadastral": p.cadastral_code, "metro": pf.get("metro"), "is_exclusive": p.is_exclusive,
         "price": float(price) if price is not None else None}
    if user is None or can_see_owner(p, user):
        o = db.get(Owner, p.owner_id) if p.owner_id else None
        d.update(ad_link=p.post_url or p.source_url, owner_name=o.name if o else None, owner_phone=o.phone if o else None)
    return {k: v for k, v in d.items() if v not in (None, "")}


def deal_search(db: Session, user: User, q: str | None = None, pid: int | None = None, deal_type: str = "rent",
                allow: int | None = None) -> list[dict]:
    """The deal form's object picker: by address, #id, cadastral code or the owner's phone, among all agents' objects.
    Another agent's object comes with `blocked` and without its data. allow = the object of the agent's own lead."""
    if pid:
        rows = [p for p in [db.get(Property, int(pid))] if p is not None]
    elif q and q.strip().lstrip("#"):
        rows = list(db.scalars(query(db, user, q=q, everyone=True).limit(20)))
    else:
        return []
    out = []
    for p in rows:
        blocked = None if p.id == allow else deal_block(db, p, user)
        agent = db.get(User, p.agent_id) if p.agent_id else None
        out.append({"id": p.id, "address": p.address, "district": p.district, "status": p.status, "bedrooms": p.bedrooms,
                    "agent_tag": users_svc.tag_of(agent) if agent else None, "needs_check": needs_check(p),
                    "blocked": blocked, "fields": None if blocked else deal_fields(db, p, user, deal_type)})
    return out


def verify(db: Session, pid: int, actor: User) -> Property:
    """The operator checked the object (stage 7: a new object from a deal; its deal can be confirmed then)."""
    if actor.role not in STAFF:
        raise forbidden("only operators verify objects")
    p = _prop(db, pid, lock=True)
    if p.verified_at is None:
        p.verified_at = now()
        log_event(db, "property", p.id, "verified", actor)
        db.commit()
    return p


def board(db: Session, user: User, per_column: int = 100) -> dict[str, list[dict]]:
    columns = {}
    for st in BOARD:
        stmt = select(Property).where(Property.status == st, Property.board_hidden_at.is_(None))
        if user.role not in STAFF:
            stmt = stmt.where(Property.agent_id == user.id)
        columns[st] = list(db.scalars(stmt.order_by(Property.updated_at.desc()).limit(per_column)))
    cache: dict = {}
    prefetch(db, [p for rows in columns.values() for p in rows], cache)
    return {st: [out(db, p, user, cache=cache) for p in rows] for st, rows in columns.items()}


BOARD_HIDEABLE = ("published", "rented")


def board_hide(db: Session, user: User, status: str) -> int:
    """«Убрать с доски»: every card of a done column (staff: all, agent: his own) leaves the board; the object keeps its
    status (still in the channel, the bot and the lists). -> how many."""
    if status not in BOARD_HIDEABLE:
        raise DomainError("bad_status", "only published / rented cards can leave the board")
    stmt = select(Property).where(Property.status == status, Property.board_hidden_at.is_(None))
    if user.role not in STAFF:
        stmt = stmt.where(Property.agent_id == user.id)
    rows = list(db.scalars(stmt))
    for p in rows:
        p.board_hidden_at = now()
    log_event(db, "property", "-", "board_hidden", user, status=status, count=len(rows))
    db.commit()
    return len(rows)


def history(db: Session, pid: int) -> list[dict]:
    rows = db.scalars(select(Event).where(Event.entity == "property", Event.entity_id == str(pid)).order_by(Event.at, Event.id))
    return [{"id": e.id, "action": e.action, "actor_id": e.actor_id, "actor": e.actor_label, "data": e.data,
             "at": e.at.isoformat()} for e in rows]
