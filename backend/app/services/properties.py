"""Funnel 1: new listing → take → call → owner/agent → shoot → media → review → publish (§4.2, §5)."""
import logging
import math
import os
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy import update as update_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import storage
from app.config import settings as env
from app.models import (FIELD, PUBLICATION_CHANNELS, STAFF, ChannelPost, Deal, Event, Listing, ListingMatch, Owner, Property,
                        PropertyListing, PropertyMedia, PropertyPublication, User)
from app.services import jobs
from app.services import users as users_svc
from app.services.common import aware, get_or_404, jload, log_event, now, now_text
from app.services.dedup import address_match, extract_phones, norm_address, norm_phone, phone_match
from app.services.errors import DomainError, forbidden
from app.telegram import notify

log = logging.getLogger(__name__)

BOARD = ("taken", "call_failed", "owner_confirmed", "agent_marked", "shoot_scheduled", "media_uploaded", "review",
         "returned", "published", "rented")
EDITABLE = ("address", "district", "cadastral_code", "price", "currency", "bedrooms", "rooms", "area", "floor",
            "total_floors", "description", "is_exclusive", "source_url", "source_type", "checklist", "shoot_at",
            "post_fields")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
MAX_UPLOAD = 300 * 1024 * 1024
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


SOURCE_PREFIX = {"ss": "ss", "myhome": "mh", "livo": "lv", "korter": "kr", "facebook": "fb", "instagram": "ig", "telegram": "tg"}


def _metro():
    return cast(Listing.details, JSONB)["metro_ru"].astext


def _untaken():
    taken = select(Property.listing_id).where(Property.listing_id.isnot(None), Property.status != "archived")
    return (Listing.status.in_(("published", "pending_review")), Listing.source != "agency", Listing.id.not_in(taken))


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
    sim, refused, objs = similar_in_channel(db, rows), refusals_for(db, ids), known_objects(db, ids)
    return {"items": [{**listing_out(r), "similar": sim.get(r.id, []), "refused": refused.get(r.id),
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


def similar_in_channel(db: Session, rows: list[Listing], radius: float = 60) -> dict[str, list[dict]]:
    """Agency-channel flats in the same building with close parameters (bedrooms ±1, area ±35%), for the agent to
    compare by eye. Not a dup verdict: two flats in one house look alike on paper (Uznadze 4: 1st vs 4th floor)."""
    rows = [r for r in rows if r.lat is not None and r.lng is not None and not listing_flags(r)["duplicate"]]
    if not rows:
        return {}
    agency = db.scalars(select(Listing).where(Listing.source == "agency", Listing.status == "published",
                                              Listing.lat.isnot(None), Listing.lng.isnot(None))).all()

    def close(l: Listing, a: Listing) -> bool:
        if l.bedrooms is not None and a.bedrooms is not None and abs(l.bedrooms - a.bedrooms) > 1:
            return False
        return not (l.area and a.area and abs(l.area - a.area) > 0.35 * max(l.area, a.area))

    out = {}
    for l in rows:
        near = sorted(((_near(l, a), a) for a in agency if close(l, a)), key=lambda x: x[0])
        near = [(d, a) for d, a in near if d <= radius][:2]
        if near:
            out[l.id] = [{"id": a.id, "url": (jload(a.urls, []) or [None])[0], "address": a.address,
                          "photo": f"/api/listing-photos/{a.id}/0" if jload(a.photos, []) else None,
                          "price_usd": a.price_usd, "bedrooms": a.bedrooms, "area": a.area, "floor": a.floor,
                          "distance_m": round(d)} for d, a in near]
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
    return {"sources": facet(Listing.source), "districts": facet(Listing.district), "metros": facet(_metro())}


# ---------------------------------------------------------------- take (C1 → C2)

def take(db: Session, listing_id: str | None, agent: User, manual_fields: dict | None = None) -> Property:
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
            _bind_owner(db, existing, db.get(Owner, existing.owner_id) if existing.owner_id else None, agent)
            _status(db, existing, "taken", agent, "taken")
            db.commit()
            return existing
        urls = jload(listing.urls, []) or []
        p = Property(listing_id=listing.id, source_type=source_type_of(listing), source_url=urls[0] if urls else None,
                     source_external_id=listing.id, address=listing.address, district=listing.district,
                     price=listing.price_usd, bedrooms=listing.bedrooms, area=listing.area, floor=listing.floor,
                     description=listing.description_ru, ai_flags=listing_flags(listing))
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
        elif k in ("bedrooms", "rooms", "floor", "total_floors"):
            v = int(v) if v not in (None, "") else None
        elif k == "shoot_at":
            v = aware(v)
        elif k == "post_fields":
            v = clean_post_fields(v)
        setattr(p, k, v)


def update(db: Session, pid: int, data: dict, actor: User) -> Property:
    p = _prop(db, pid)
    _require_owner(p, actor)
    was_exclusive = p.is_exclusive
    _apply(p, data)
    _set_owner_contact(db, p, data.get("owner_name"), data.get("owner_phone"), actor)
    if p.is_exclusive and not was_exclusive:
        for a in external_ads(db, [p.id]).get(p.id, []):
            log_event(db, "property", p.id, "exclusive_conflict", actor, **a)
    p.updated_at = now()
    log_event(db, "property", p.id, "updated", actor,
              fields=sorted(k for k in data if k in EDITABLE or k in ("owner_name", "owner_phone")))
    db.commit()
    return p


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
    _status(db, p, "archived", agent, "owner_refused", comment=(comment or "").strip() or None)
    p.next_call_at = None
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    if o is not None and o.agent_id == p.agent_id and not db.scalar(select(Property.id).where(  # he never became ours
            Property.owner_id == o.id, Property.id != p.id, Property.agent_id == o.agent_id,
            Property.status != "archived").limit(1)):
        o.agent_id = None
        log_event(db, "owner", o.id, "agent_released", agent, property_id=p.id)
    for kind in ("call_retry", "owner_followup"):
        jobs.cancel(db, kind, p.id)
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
    p.next_call_at = None
    for kind in ("call_retry", "owner_followup"):
        jobs.cancel(db, kind, p.id)
    _status(db, p, "archived", agent, "owner_rented_self", comment=(comment or "").strip() or None)
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


def archive(db: Session, pid: int, actor: User) -> Property:
    p = _prop(db, pid)
    _require_owner(p, actor)
    _status(db, p, "archived", actor)
    jobs.cancel(db, "call_retry", p.id)
    db.commit()
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


def transfer_owner(db: Session, owner_id: int, to_agent_id: int, by_user: User, reason: str | None = None) -> Owner:
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
    out = []
    for name, content in files:
        ext = os.path.splitext(name or "")[1].lower() or ".jpg"
        if ext not in PHOTO_EXT | VIDEO_EXT:
            raise DomainError("bad_file", f"only photos and videos are allowed: {name}")
        if len(content) > MAX_UPLOAD:
            raise DomainError("too_large", f"file is too large: {name}")
        key = f"properties/{p.id}/{uuid.uuid4().hex}{ext}"
        storage.put(key, content)
        pos += 1
        m = PropertyMedia(property_id=p.id, kind="video" if ext in VIDEO_EXT else "photo",
                          path=key, room=room, position=pos, uploaded_by=actor.id)
        db.add(m)
        out.append(m)
    if p.status in ("owner_confirmed", "shoot_scheduled", "returned"):
        _status(db, p, "media_uploaded", actor, files=len(files))
    else:
        log_event(db, "property", p.id, "media_added", actor, files=len(files))
    db.commit()
    return out


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
    return list(db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == pid).order_by(PropertyMedia.position)))


def set_checklist(db: Session, pid: int, checklist: dict, actor: User) -> Property:
    p = _prop(db, pid)
    _require_media_editable(p, actor)
    p.checklist = {str(k): bool(v) for k, v in (checklist or {}).items()}
    log_event(db, "property", p.id, "checklist", actor, checklist=p.checklist)
    db.commit()
    return p


def submit_for_review(db: Session, pid: int, agent: User) -> Property:
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "media_uploaded", "returned", "shoot_scheduled")
    if not db.scalar(select(func.count()).where(PropertyMedia.property_id == p.id)):
        raise DomainError("no_media", "upload photos or videos first")
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
        else:
            ok = False
        if not ok:
            raise DomainError("bad_post_fields", f"Поля поста: недопустимое значение {k}={x}")
        out[k] = x
    return out


def maps_url(db: Session, p: Property) -> str | None:
    """Google Maps link for the post address: the origin ad's pin, else a search by the address."""
    listing = db.get(Listing, p.listing_id) if p.listing_id else None
    if listing is not None and listing.lat is not None and listing.lng is not None:
        return f"https://maps.google.com/?q={listing.lat},{listing.lng}"
    if not p.address:
        return None
    q = f"{p.address}, Tbilisi".replace(" ", "+")  # non-ASCII stays readable in the editable text; Telegram takes IRIs
    return "https://www.google.com/maps/search/?api=1&query=" + "".join(c if ord(c) > 127 else quote(c, safe=",+") for c in q)


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
    beds = "#Studio " if p.bedrooms == 0 else (f"#{p.bedrooms}Bed " if p.bedrooms else "")
    lines.append(f"🏢 {beds}{tag('prop_type') or '#Apartment'} for {'#Sale' if sale else '#Rent'}")
    style = [x for x in (tag("building"), tag("design"), sale and tag("condition")) if x]
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
    lines += ["", f"📲 @David_Tibelashvili | \n+995 599 20 67 16 {users_svc.tag_of(agent)}", "", "🌟 Check all listings | Reviews",
              "", "📍APARTMENTS ON MAP📍", "", "📱 Instagram 🗳 FB 🎥 YouTube"]
    if sale:
        lines += ["", "SALE IN TBILISI🇬🇪"]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


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
    p.post_text = (text or "").strip() or post_text(db, p)  # transient attributes read by notify.publish_post
    p.post_media_ids = media_ids
    ref = _notify("publish_post", p)
    dry_run = ref is None and not env.BOT_TOKEN and env.APP_ENV == "dev"
    if ref is None and not dry_run:
        raise DomainError("publish_failed", "Telegram did not accept the post", 502)
    if ref:
        p.post_message_id, p.post_url = str(ref[0]), ref[1]
    p.published_at = now()
    if p.status == "review":
        p.review_comment, p.reviewed_by, p.reviewed_at = comment, actor.id, now()
        log_event(db, "property", p.id, "approved", actor, comment=comment)
    record_publication(db, p, channel, p.post_url if ref else None, p.post_message_id if ref else None)
    _status(db, p, "published", actor, post_url=p.post_url if ref else None, channel=channel,
            **({"dry_run": True} if dry_run else {}))
    db.commit()
    _notify("property_published", p)
    return p


def mark_rented(db: Session, pid: int, actor: User | None) -> None:
    p = db.get(Property, pid)
    if p and p.status != "rented":
        _status(db, p, "rented", actor)


def unmark_rented(db: Session, pid: int, actor: User | None) -> None:
    """Confirmed deal cancelled → the object is back on the market."""
    p = db.get(Property, pid)
    if p and p.status == "rented":
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
                   Listing.status != "gone", Listing.source != "agency").order_by(PropertyListing.id)):
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
        counts = dict(db.execute(select(PropertyMedia.property_id, func.count()).where(PropertyMedia.property_id.in_(pids))
                                 .group_by(PropertyMedia.property_id)).all())
        for pid in pids:
            cache[("media_count", pid)] = counts.get(pid, 0)


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
                                    "address", "district", "cadastral_code", "currency", "bedrooms", "rooms", "floor",
                                    "total_floors", "description", "is_exclusive", "checklist", "ai_flags",
                                    "duplicate_of", "dup_check", "review_comment", "reviewed_by", "post_url",
                                    "post_message_id", "owner_id", "post_fields")}
    for c in ("taken_at", "next_call_at", "shoot_at", "reviewed_at", "published_at", "created_at", "updated_at"):
        v = getattr(p, c)
        d[c] = v.isoformat() if v else None
    d["price"] = float(p.price) if p.price is not None else None
    d["area"] = float(p.area) if p.area is not None else None
    ref = lambda u: {"id": u.id, "name": u.name, "username": u.username, "agent_tag": u.agent_tag} if u else None  # noqa: E731
    d["agent"] = ref(agent)
    d["locked"] = bool(p.agent_id) and p.agent_id != user.id and user.role not in STAFF
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
        d["media"] = [{"id": m.id, "kind": m.kind, "room": m.room, "position": m.position, "url": f"/api/media/{m.path}"}
                      for m in media(db, p.id)]
        listing = db.get(Listing, p.listing_id) if p.listing_id else None
        d["listing"] = listing_out(listing, full=can_see_owner(p, user)) if listing else None
        d.update(card(db, p, user))
    else:
        k = ("media_count", p.id)
        if k not in cache:
            cache[k] = db.scalar(select(func.count()).where(PropertyMedia.property_id == p.id))
        d["media_count"] = cache[k]
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


def query(db: Session, user: User, status: str | None = None, agent_id: int | None = None, q: str | None = None,
          mine: bool = False):
    stmt = select(Property)
    if mine or (user.role not in STAFF and agent_id is None and not status):
        stmt = stmt.where(Property.agent_id == user.id)
    elif agent_id:
        stmt = stmt.where(Property.agent_id == agent_id)
    if status:
        stmt = stmt.where(Property.status.in_(status.split(",")))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Property.address.ilike(like), Property.district.ilike(like),
                              Property.cadastral_code.ilike(like), cast(Property.id, Text).ilike(like)))
    return stmt.order_by(Property.updated_at.desc())


def board(db: Session, user: User, per_column: int = 100) -> dict[str, list[dict]]:
    columns = {}
    for st in BOARD:
        stmt = select(Property).where(Property.status == st)
        if user.role not in STAFF:
            stmt = stmt.where(Property.agent_id == user.id)
        columns[st] = list(db.scalars(stmt.order_by(Property.updated_at.desc()).limit(per_column)))
    cache: dict = {}
    prefetch(db, [p for rows in columns.values() for p in rows], cache)
    return {st: [out(db, p, user, cache=cache) for p in rows] for st, rows in columns.items()}


def history(db: Session, pid: int) -> list[dict]:
    rows = db.scalars(select(Event).where(Event.entity == "property", Event.entity_id == str(pid)).order_by(Event.at, Event.id))
    return [{"id": e.id, "action": e.action, "actor_id": e.actor_id, "actor": e.actor_label, "data": e.data,
             "at": e.at.isoformat()} for e in rows]
