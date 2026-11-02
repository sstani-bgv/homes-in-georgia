"""Funnel 1: new listing → take → call → owner/agent → shoot → media → review → publish (§4.2, §5)."""
import logging
import os
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import STAFF, Deal, Event, Listing, Owner, Property, PropertyMedia, User
from app.services import jobs
from app.services.common import aware, get_or_404, jload, log_event, now
from app.services.dedup import address_match, extract_phones, norm_address, norm_phone, phone_match
from app.services.errors import DomainError, forbidden
from app.telegram import notify

log = logging.getLogger(__name__)

BOARD = ("taken", "call_failed", "owner_confirmed", "agent_marked", "shoot_scheduled", "media_uploaded", "review",
         "returned", "published", "rented")
EDITABLE = ("address", "district", "cadastral_code", "price", "currency", "bedrooms", "rooms", "area", "floor",
            "total_floors", "description", "is_exclusive", "source_url", "source_type", "checklist", "shoot_at")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
MAX_UPLOAD = 300 * 1024 * 1024
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
                 details=jload(listing.details, {}), dup_sources=jload(listing.dup_sources, []))
    return d


def new_listings(db: Session, q: str | None = None, source: str | None = None, flag: str | None = None,
                 limit: int = 50, offset: int = 0) -> dict:
    """Listings not yet taken into work, owner-like first by default, newest first."""
    taken = select(Property.listing_id).where(Property.listing_id.isnot(None), Property.status != "archived")
    stmt = select(Listing).where(Listing.status.in_(("published", "pending_review")), Listing.source != "agency",
                                 Listing.id.not_in(taken))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Listing.address.ilike(like), Listing.district.ilike(like), Listing.text.ilike(like),
                              Listing.id.ilike(like)))
    if source:
        pref = {"ss": "ss", "myhome": "mh", "facebook": "fb", "telegram": "tg"}.get(source)
        stmt = stmt.where(Listing.id.ilike(f"{pref}%") if pref else Listing.source.ilike(f"{source}%"))
    if flag == "owner":
        stmt = stmt.where(Listing.agent_suspect == 0, Listing.agent_reasons == "[]")
    elif flag == "agent_suspect":
        stmt = stmt.where(or_(Listing.agent_suspect == 1, Listing.agent_reasons != "[]"))
    elif flag == "duplicate":
        stmt = stmt.where(Listing.dup_sources.contains('"agency"'))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Listing.first_seen.desc(), Listing.id).limit(limit).offset(offset))
    return {"items": [listing_out(r) for r in rows], "total": total}


# ---------------------------------------------------------------- take (C1 → C2)

def take(db: Session, listing_id: str | None, agent: User, manual_fields: dict | None = None) -> Property:
    """"Беру в работу": the object is locked to this agent; 409 taken_by_other for anyone else."""
    if listing_id:
        listing = get_or_404(db, Listing, listing_id, "listing", lock=True)
        existing = db.scalar(select(Property).where(Property.listing_id == listing_id).with_for_update())
        if existing:
            if existing.agent_id == agent.id and existing.status != "archived":
                return existing
            if existing.agent_id and existing.status != "archived":
                raise DomainError("taken_by_other", "object already taken by another agent")
            existing.agent_id, existing.taken_at = agent.id, now()
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
    log_event(db, "property", p.id, "taken", agent, listing_id=listing_id)
    db.commit()
    return p


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
        setattr(p, k, v)


def update(db: Session, pid: int, data: dict, actor: User) -> Property:
    p = _prop(db, pid)
    _require_owner(p, actor)
    _apply(p, data)
    p.updated_at = now()
    log_event(db, "property", p.id, "updated", actor, fields=sorted(k for k in data if k in EDITABLE))
    db.commit()
    return p


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


def schedule_shoot(db: Session, pid: int, agent: User, shoot_at) -> tuple[Property, str]:
    """C3: shoot date set; owner dedup by phone/address → 'added' | 'exists' (apps-script saveMakeFoto)."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "owner_confirmed", "shoot_scheduled")
    p.shoot_at = aware(shoot_at)
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
    _require_owner(p, actor)
    _require_status(p, "owner_confirmed", "shoot_scheduled", "media_uploaded", "returned", "review", "published")
    folder = media_root() / "properties" / str(p.id)
    folder.mkdir(parents=True, exist_ok=True)
    pos = db.scalar(select(func.coalesce(func.max(PropertyMedia.position), -1)).where(PropertyMedia.property_id == p.id))
    out = []
    for name, content in files:
        ext = os.path.splitext(name or "")[1].lower() or ".jpg"
        if ext not in PHOTO_EXT | VIDEO_EXT:
            raise DomainError("bad_file", f"only photos and videos are allowed: {name}")
        if len(content) > MAX_UPLOAD:
            raise DomainError("too_large", f"file is too large: {name}")
        fname = f"{uuid.uuid4().hex}{ext}"
        (folder / fname).write_bytes(content)
        pos += 1
        m = PropertyMedia(property_id=p.id, kind="video" if ext in VIDEO_EXT else "photo",
                          path=f"properties/{p.id}/{fname}", room=room, position=pos, uploaded_by=actor.id)
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
    _require_owner(p, actor)
    m = db.get(PropertyMedia, mid)
    if not m or m.property_id != p.id:
        raise DomainError("not_found", "media not found")
    try:
        (media_root() / m.path).unlink(missing_ok=True)
    except OSError:
        log.warning("could not delete %s", m.path)
    db.delete(m)
    log_event(db, "property", p.id, "media_deleted", actor, media_id=mid)
    db.commit()


def media(db: Session, pid: int) -> list[PropertyMedia]:
    return list(db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == pid).order_by(PropertyMedia.position)))


def set_checklist(db: Session, pid: int, checklist: dict, actor: User) -> Property:
    p = _prop(db, pid)
    _require_owner(p, actor)
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
    p.review_comment, p.reviewed_by, p.reviewed_at = comment, operator.id, now()
    if approve:
        log_event(db, "property", p.id, "approved", operator, comment=comment)
        db.commit()
        return publish(db, p.id, operator)
    _status(db, p, "returned", operator, comment=comment)
    db.commit()
    _notify("property_returned", p)
    return p


# CRM district -> the channel's hashtag (static/tools/post-generator.html, buildRentPost)
DISTRICT_TAG = {"Ваке": "#Vake", "Ваке-Багеби": "#Vake", "Сабуртало": "#Saburtalo", "Мтацминда": "#Mtatsminda", "Вере": "#Vera",
                "Ортачала": "#Ortachala", "Дидубе": "#Didube", "Чугурети": "#Chugureti", "Исани": "#Isani", "Самгори": "#Samgori",
                "Глдани": "#Gldani", "Надзаладеви": "#Nadzaladevi", "Дигоми": "#Digomi", "Сололаки": "#Sololaki",
                "Авлабари": "#Avlabari", "Нахаловка": "#Naxalovka", "Ваке-Сабуртало": "#Vake"}
PRICE_TAGS = ((300, "#Price0to300"), (500, "#Price300to500"), (700, "#Price500to700"), (900, "#Price700to900"),
              (1200, "#Price900to1200"))


def post_text(db: Session, p: Property) -> str:
    """Channel caption in the agency's own format: a port of buildRentPost() from the agency post generator.
    Fields the object doesn't have (metro, amenities, pets, terms) are left out, as the generator does when empty."""
    agent = db.get(User, p.agent_id) if p.agent_id else None
    district = DISTRICT_TAG.get((p.district or "").strip(), f"#{(p.district or 'Tbilisi').replace(' ', '')}")
    lines = [district]
    if p.address:
        lines.append(f"📍{p.address}")
    lines.append("")
    if p.is_exclusive:
        lines += ["❗️#Exclusive", ""]
    beds = "#Studio " if p.bedrooms == 0 else (f"#{p.bedrooms}Bed " if p.bedrooms else "")
    lines.append(f"🏢 {beds}#Apartment for #Rent")
    spec = []
    if p.area:
        spec.append(f"{float(p.area):g} sq.m")
    if p.floor:
        spec.append(f"{p.floor}/{p.total_floors} Floor" if p.total_floors else f"{p.floor} Floor")
    if spec:
        lines.append("🏠 " + " | ".join(spec))
    lines.append("")
    if p.price is not None:
        price = int(p.price)
        lines.append(f"💰 {price}$ + Deposit {price}$")
    lines.append("0% Commission")
    if p.price is not None:
        lines.append(next((t for top, t in PRICE_TAGS if int(p.price) <= top), "#Price1200plus"))
    agent_tag = f"#{agent.name.split()[0]}" if agent and agent.name else "#???"
    lines += ["", f"📲 @David_Tibelashvili | \n+995 599 20 67 16 {agent_tag}", "", "🌟 Check all listings | Reviews", "",
              "📍APARTMENTS ON MAP📍", "", "📱 Instagram 🗳 FB 🎥 YouTube"]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def publish(db: Session, pid: int, actor: User) -> Property:
    p = _prop(db, pid, lock=True)
    if actor.role not in STAFF:
        raise forbidden("only operators publish")
    _require_status(p, "review", "published")
    p.post_text = post_text(db, p)  # transient attribute read by notify.publish_post
    ref = _notify("publish_post", p)
    if ref:
        p.post_message_id, p.post_url = str(ref[0]), ref[1]
    p.published_at = now()
    _status(db, p, "published", actor, post_url=p.post_url)
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
    for model, ids in ((User, {p.agent_id for p in rows}), (Owner, {p.owner_id for p in rows})):
        ids = {i for i in ids if i is not None and (model.__name__, i) not in cache}
        if ids:
            for obj in db.scalars(select(model).where(model.id.in_(ids))):
                cache[(model.__name__, obj.id)] = obj
            for i in ids:
                cache.setdefault((model.__name__, i), None)
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
                                    "post_message_id", "owner_id")}
    for c in ("taken_at", "next_call_at", "shoot_at", "reviewed_at", "published_at", "created_at", "updated_at"):
        v = getattr(p, c)
        d[c] = v.isoformat() if v else None
    d["price"] = float(p.price) if p.price is not None else None
    d["area"] = float(p.area) if p.area is not None else None
    d["agent"] = {"id": agent.id, "name": agent.name, "username": agent.username} if agent else None
    d["locked"] = bool(p.agent_id) and p.agent_id != user.id and user.role not in STAFF
    owner = get(Owner, p.owner_id)
    if owner:
        visible = can_see_owner(p, user)
        d["owner"] = {"id": owner.id, "name": owner.name, "is_agent": owner.is_agent,
                      "phone": owner.phone if visible else None}
    else:
        d["owner"] = None
    if full:
        d["media"] = [{"id": m.id, "kind": m.kind, "room": m.room, "position": m.position, "url": f"/api/media/{m.path}"}
                      for m in media(db, p.id)]
        listing = db.get(Listing, p.listing_id) if p.listing_id else None
        d["listing"] = listing_out(listing, full=can_see_owner(p, user)) if listing else None
        d["post_text"] = post_text(db, p)
    else:
        k = ("media_count", p.id)
        if k not in cache:
            cache[k] = db.scalar(select(func.count()).where(PropertyMedia.property_id == p.id))
        d["media_count"] = cache[k]
    return d


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
