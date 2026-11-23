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

from app import districts, progress, storage
from app.config import settings as env
from app.models import (ARCHIVE_REASONS, PUBLICATION_CHANNELS, ChannelPost, ClientView, Contract, Deal, Event, Lead, Listing,
                        ListingMatch, Owner,
                        Property, PropertyListing, PropertyMedia, PropertyPublication, Source, User, at_least, is_field)
from app.services import jobs
from app.services import users as users_svc
from app.services import settings as settings_svc
from app.services.common import aware, clean_cadastral, get_or_404, jload, log_event, notify_later, now, now_text
from app.services.dedup import (address_match, e164, extract_phones, norm_address, norm_phone, owner_phones, phone_match,
                                 phone_rows, set_owner_phones)
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
LOCKED_AFTER_VERIFY = ("owner_name", "owner_phone", "owner_phones", "address", "cadastral_code", "source_url", "is_exclusive", "price",
                       "currency", "deposit", "sale_price")
POST_LOCKED = ("deposit", "sale_price")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
MAX_UPLOAD = 300 * 1024 * 1024
# Stanislav 11-16: up to 40 photos + 1 video per object; the site shows them all, the Telegram album takes the first
# ALBUM_PHOTOS photos in card order + the video (9 + 1 = Telegram's 10-item album, notify._album). A rent / sale object
# needs at least MIN_PHOTOS photos to go to review or be published.
MAX_MEDIA = {"photo": 40, "video": 1}
ALBUM_PHOTOS = MIN_PHOTOS = 9
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
# «Без электронной подписи» (Irakli 11-18): the scan / photo of a paper contract the agent attaches to the request
PAPER_CONTRACT = "paper_contract"
DOC_KINDS = {**OWNER_DOCS, PAPER_CONTRACT: ("скан / фото бумажного договора", (".jpg", ".jpeg", ".png", ".pdf", ".heic", ".heif"))}
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
    if at_least(actor, "operator") or p.agent_id == actor.id:
        return
    raise forbidden("object is taken by another agent")


def _require_media_editable(p: Property, actor: User):
    _require_owner(p, actor)
    if not at_least(actor, "operator") and p.status not in AGENT_MEDIA_STATUSES:
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


def _beds(col, bedrooms: str):
    """"0,1,2,4": 0 = studio, 4 = 4 and more (the feed and the base share the «Студия 1 2 3 4+» row)"""
    picked = {int(b) for b in bedrooms.split(",") if b.strip().isdigit()}
    return or_(col.in_(picked - {4}), *([col >= 4] if 4 in picked else []))


def competitor_sources():
    """listings.source of the sources marked «Конкурент» (Управление → Источники, options.competitor): tg:<key>,
    fb:<key>, ig:<key>. Their ads are not our inventory: only «Объекты → База → Конкуренты» lists them."""
    prefix = case((Source.kind.like("tg_%"), "tg:"), (Source.kind == "fb_group", "fb:"), else_="ig:")
    return select(prefix + Source.key).where(cast(Source.options, JSONB)["competitor"].astext == "true")


def _untaken():
    taken = select(Property.listing_id).where(Property.listing_id.isnot(None), Property.status != "archived")
    return (Listing.status.in_(("published", "pending_review")), Listing.source.not_in(("agency", "object")), Listing.id.not_in(taken),
            Listing.source.not_in(competitor_sources()))


def new_listings(db: Session, q: str | None = None, source: str | list[str] | None = None, flag: str | None = None,
                 limit: int = 50, offset: int = 0, district: str | list[str] | None = None, metro: str | None = None,
                 price_min: int | None = None, price_max: int | None = None, bedrooms: str | None = None,
                 known: bool = False, competitor: bool = False, prop_type: str | None = None) -> dict:
    """Listings not yet taken into work, newest first. competitor=True: instead every ad of the competitor sources
    (any status, the ones linked to our objects too: they show «Уже в базе»). `source` is a type (ss, facebook…) or an exact source (fb:group);
    `source` and `district` take one value or a list (any of them).
    Ads the matcher already linked to an object («уже известные») are hidden unless known=True; known_total counts them."""
    stmt = select(Listing).where(Listing.source.in_(competitor_sources())) if competitor else select(Listing).where(*_untaken())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Listing.address.ilike(like), Listing.district.ilike(like), Listing.text.ilike(like),
                              Listing.id.ilike(like)))
    sources = [x for x in ([source] if isinstance(source, str) else source or []) if x]
    if sources:
        stmt = stmt.where(or_(*(Listing.id.ilike(f"{SOURCE_PREFIX[x]}%") if x in SOURCE_PREFIX else Listing.source == x
                                for x in sources)))
    districts = [x for x in ([district] if isinstance(district, str) else district or []) if x]
    if districts:
        stmt = stmt.where(Listing.district.in_(districts))
    if metro:
        stmt = stmt.where(_metro() == metro)
    if price_min is not None:
        stmt = stmt.where(Listing.price_usd >= price_min)
    if price_max is not None:
        stmt = stmt.where(Listing.price_usd <= price_max)
    if bedrooms:
        stmt = stmt.where(_beds(Listing.bedrooms, bedrooms))
    if prop_type:  # «Тип» (Irakli 11-17): details.property of the parsers (bot/facts.py), none = an apartment
        kind = cast(Listing.details, JSONB)["property"].astext
        stmt = stmt.where(or_(kind == prop_type, kind.is_(None)) if prop_type == "apartment" else kind == prop_type)
    if flag == "owner":
        stmt = stmt.where(Listing.agent_suspect == 0, Listing.agent_reasons == "[]")
    elif flag == "agent_suspect":
        stmt = stmt.where(or_(Listing.agent_suspect == 1, Listing.agent_reasons != "[]"))
    elif flag in ("in_base", "in_archive"):
        # the card's red label (baseAtAddress): «В базе по этому адресу» = a shown hint object is not archived,
        # «В архиве» = all are. Same rules as the card: similar_for over the few hinted ads (~100 on prod, 11-13)
        hinted = select(ListingMatch.listing_id).where(ListingMatch.status == BASE_HINT,
                                                       ListingMatch.listing_id.in_(select(stmt.subquery().c.id)))
        sim = similar_for(db, list(db.scalars(hinted.distinct())))
        live = {lid for lid, objs in sim.items() if any(o["status"] != "archived" for o in objs)}
        stmt = stmt.where(Listing.id.in_(live if flag == "in_base" else set(sim) - live),
                          ~Listing.dup_sources.contains('"agency"'))  # the card shows «уже в канале» instead
    # an object's own origin ad is not "known": released objects (owner refused) come back to the feed through it
    linked = select(PropertyListing.listing_id).join(Property, Property.id == PropertyListing.property_id).where(
        Property.listing_id.is_distinct_from(PropertyListing.listing_id))
    known_total = db.scalar(select(func.count()).select_from(stmt.where(Listing.id.in_(linked)).subquery()))
    if competitor and flag in ("ours", "not_ours"):  # «Конкуренты»: the ads of a flat we already have (the «Уже в базе» chip)
        stmt = stmt.where(Listing.id.in_(linked) if flag == "ours" else Listing.id.not_in(linked))
    if not known and not competitor:
        stmt = stmt.where(Listing.id.not_in(linked))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Listing.first_seen.desc(), Listing.id).limit(limit).offset(offset)))
    ids = [r.id for r in rows]
    sim, refused, objs = similar_for(db, ids), refusals_for(db, ids), known_objects(db, ids)
    from app.services import demand
    want = demand.listing_counts(db, rows)  # stage 10: «Под этот объект подходят N активных клиентов»
    comp = set(db.scalars(competitor_sources()))  # ingest dedupe adds a competitor's copy of the flat to dup_sources
    # the source link before «Беру в работу» (David 11-17): the ad is nobody's yet; phones and text stay hidden
    return {"items": [{**listing_out(r), "url": (jload(r.urls, []) or [None])[0], "similar": sim.get(r.id, []), "refused": refused.get(r.id), "demand": want.get(r.id, 0),
                       "at_competitor": [x for x in dict.fromkeys(jload(r.dup_sources, []) or []) if x in comp],
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
    for m, p in db.execute(select(ListingMatch, Property).join(Property, Property.id == ListingMatch.property_id)
                           .where(ListingMatch.listing_id.in_(listing_ids), ListingMatch.status == "pending")
                           .order_by(ListingMatch.score.desc(), ListingMatch.id)):
        out[m.listing_id]["matches"].append({"property_id": m.property_id, "address": p.address, "score": m.score,
                                             "reasons": m.reasons})
    return out


def same_flat_possible(l, p) -> bool:
    """A «похож» card needs more than a similar text (11-13: «похожее описание 0.93» for Ortachala 38 m², 1st floor vs
    Vera 20 m², 5th floor): floor / bedrooms / area agree (could_be_same) and, when both addresses name a house, it is
    that house or its corpus."""
    if not could_be_same(l, p):
        return False
    from app.services.dedup import relation, street_house
    a, b = street_house(l.address), street_house(p.address)
    if a[1] is None or b[1] is None:
        return True
    rel = relation(a, b)
    return bool(rel) and (rel == "тот же дом" or rel.startswith("соседний"))


BASE_HINT = "address"
MAX_HINTS = 3  # «похожие у нас» per ad: a big complex has dozens of flats with these params (eval 11-17)  # listing_matches.status of a «В архиве / в базе по этому адресу» row: a hint, nobody decides it


def could_be_same(l, p) -> bool:
    """The ad can be this object of our base: what both know roughly agrees — bedrooms, floor ±1, area within 10%
    (11-13: «тут 52 м², этаж 2 — зачем ты показываешь 60 м² и 13-й этаж?»; 11-17: the same flat was 100 m² / 6th floor
    in the CRM and 110 m² / 5th in the ad — «покажи похожие, чтобы определить, та или не та»). Unknown on either side
    doesn't rule it out."""
    def known(a, b):
        return a is not None and b is not None
    if known(l.floor, p.floor) and abs(int(l.floor) - int(p.floor)) > 1:
        return False
    if known(l.bedrooms, p.bedrooms) and int(l.bedrooms) != int(p.bedrooms):
        return False
    if l.area and p.area and abs(float(l.area) - float(p.area)) > max(1.0, 0.10 * max(float(l.area), float(p.area))):
        return False
    return True


def similar_in_base(db: Session, rows: list[Listing], radius: float = 60,
                    twin_radius: float = 1500) -> dict[str, list[dict]]:
    """Objects of our base (archived legacy ones too) near the ad, for the agent to compare by eye (David 2026-11-09 /
    11-12: «по Bakradze 20 в архиве уже 3 объекта — сравните»; 11-17: «покажи похожие — та или не та»). An object whose
    bedrooms, floor ±1 and area ±10% fit (could_be_same) counts when
    - it is the same building or a neighbour corpus (20 / 20A), across Latin / Cyrillic / Georgian spellings
      (dedup.relation), or its pin is within `radius` of the ad's (the object's own pin or its source ad's);
    - it is a house ≤ NEAR_HOUSES apart on the same street, or on the street of an ad that names no house («Vake mckheta
      st. -»), only with the same floor and area (exact()): there the loose fit was another flat 30 times of 33.
    - its pin is within `twin_radius` (pins can be off by a kilometre, 11-13) and floor, area and price (±15%) are the
      same: last, as a looser twin was another flat 80 times of 80 (eval 11-17, «1430 м — не та квартира»: a sale).
    -> listing id → [{property_id, reasons, rank}], the MAX_HINTS best: same house first, the closest params first.
    Run when ads are parsed (store_similar), not per render.
    ponytail: the whole base in memory, a scan per ad (~10k objects × a sync batch); bucket by street / bbox if slow."""
    from app.services.dedup import _same_street, relation, street_house, street_words
    if not rows:
        return {}
    with_photos = set(db.scalars(select(PropertyMedia.property_id).where(PropertyMedia.kind == "photo").distinct()))
    objs = [(p, street_house(p.address or (a.address if a else None)),
             (p.lat, p.lng) if p.lat is not None and p.lng is not None else
             (a.lat, a.lng) if a is not None and a.lat is not None and a.lng is not None else None)
            for p, a in db.execute(select(Property, Listing).outerjoin(Listing, Listing.id == Property.listing_id)).all()
            if p.id in with_photos or (a is not None and (a.photos or "[]") != "[]")]
    # ↑ only objects with photos (own or their ad's): the card compares by eye, similar_for hides the rest
    linked = set(db.execute(select(PropertyListing.listing_id, PropertyListing.property_id)
                            .where(PropertyListing.listing_id.in_([r.id for r in rows]))).all())  # already «known_object»

    def exact(l: Listing, p: Property) -> bool:
        return ((l.floor is None or p.floor is None or l.floor == p.floor)
                and (not l.area or not p.area or abs(float(l.area) - float(p.area)) <= max(1.0, 0.03 * max(float(l.area), float(p.area)))))

    def gap(l: Listing, p: Property) -> float:
        """How far the params are apart (0 = all equal): the closest flats first."""
        def rel(a, b, w):
            return 0.5 if not a or not b else w * abs(float(a) - float(b)) / max(float(a), float(b))
        return ((abs(l.floor - p.floor) if l.floor is not None and p.floor is not None else 0.5)
                + rel(l.area, p.area, 10) + rel(l.price_usd, p.price, 3))

    out = {}
    for l in rows:
        mine, pin = street_house(l.address), (l.lat, l.lng) if l.lat is not None and l.lng is not None else None
        bare = street_words(l.address) if mine[1] is None else frozenset()
        found = []
        for p, addr, at in objs:
            if p.listing_id == l.id or (l.id, p.id) in linked or not could_be_same(l, p):
                continue
            rel = relation(mine, addr)
            if not rel and bare and addr[1] is not None and _same_street(bare, addr[0]):
                rel = "та же улица"
            if rel and not (rel == "тот же дом" or rel.startswith("соседний")) and not exact(l, p):
                rel = None
            d = 111_000 * math.hypot(pin[0] - at[0], (pin[1] - at[1]) * 0.75) if pin and at else None  # metres, equirectangular (0.75 = cos 41.7°)
            near = d is not None and d <= radius
            far_twin = (not near and d is not None and d <= twin_radius and l.bedrooms is not None and exact(l, p)
                        and bool(l.price_usd and p.price) and abs(float(l.price_usd) - float(p.price)) <= 0.15 * float(p.price))
            if not rel and not near and not far_twin:
                continue
            why = ([rel] if rel else []) + ([f"в {round(d)} м"] if near else []) + (
                [f"в {round(d)} м · те же спальни, этаж, площадь"] if far_twin and not rel else [])
            if rel in ("та же улица",) or (rel or "").startswith("рядом"):
                why.append("тот же этаж и площадь")
            rank = (0 if rel == "тот же дом" else 1 if rel and rel.startswith("соседний") else 2 if near else 3 if rel else 4,
                    round(gap(l, p), 2), d if d is not None else 1e9, p.id)
            found.append((rank, p.id, why))
        if found:
            out[l.id] = [{"property_id": pid, "reasons": why, "rank": i}
                         for i, (_, pid, why) in enumerate(sorted(found)[:MAX_HINTS])]
    return out


def store_similar(db: Session, rows: list[Listing]) -> int:
    """similar_in_base for freshly parsed ads → listing_matches rows (status BASE_HINT). A pair that already is a
    «похож» card keeps it. No commit. -> ads with at least one object at their address."""
    from sqlalchemy.dialects.postgresql import insert
    found = similar_in_base(db, rows)
    for lid, objs in found.items():
        for o in objs:
            db.execute(insert(ListingMatch).values(listing_id=lid, property_id=o["property_id"], score=-o["rank"],
                                                   reasons=o["reasons"], status=BASE_HINT).on_conflict_do_nothing())
    return len(found)


def similar_for(db: Session, listing_ids: list[str]) -> dict[str, list[dict]]:
    """The stored «по этому адресу в базе» objects of these ads, with today's status / price / cover photo."""
    if not listing_ids:
        return {}
    rows = db.execute(select(ListingMatch.listing_id, ListingMatch.reasons, Property)
                      .join(Property, Property.id == ListingMatch.property_id)
                      .where(ListingMatch.listing_id.in_(listing_ids), ListingMatch.status == BASE_HINT)
                      .order_by(ListingMatch.score.desc(), ListingMatch.id)).all()
    photos = _photo_lists(db, list({p.id: p for _, _, p in rows}.values()))
    ads = {x.id: x for x in db.scalars(select(Listing).where(Listing.id.in_(listing_ids)))}
    out: dict[str, list[dict]] = {}
    for lid, why, p in rows:
        # nothing to compare by eye without photos; another floor / area / bedrooms is another flat (11-13).
        # Filtered here, so hints stored before the rule follow it too
        if not photos.get(p.id) or (lid in ads and not could_be_same(ads[lid], p)):
            continue
        out.setdefault(lid, []).append({
            "id": p.id, "url": f"/properties/{p.id}", "address": p.address, "status": p.status,
            "archive_reason": p.archive_reason, "photo": (photos.get(p.id) or [None])[0], "photos": photos.get(p.id, []),
            "reasons": why or [],
            "price_usd": float(p.price) if p.price is not None else None, "bedrooms": p.bedrooms,
            "area": float(p.area) if p.area is not None else None, "floor": p.floor})
    return out


def dismiss_listing(db: Session, listing_id: str, actor: User) -> None:
    """"Неактуально": the ad is deleted at the source. Kept as a human decision so the next ingest won't revive it."""
    x = get_or_404(db, Listing, listing_id, "listing", lock=True)
    x.decision, x.status, x.status_reason = "rejected", "rejected", "неактуально"
    x.decided_by, x.decided_at, x.updated_at = actor.id, now_text(), now_text()
    log_event(db, "listing", x.id, "dismissed", actor)
    db.commit()


def _real_owner(db: Session, phone: str | None, other_than: int | None = None) -> bool:
    """The number belongs to an owner of our real objects (not only agent-marked ones): one call or one ad doesn't brand
    him an agent."""
    known = find_owner(db, phone)
    return known is not None and db.scalar(select(Property.id).where(
        Property.owner_id == known.id, Property.status != "agent_marked",
        *([Property.id != other_than] if other_than else [])).limit(1)) is not None


def mark_listing_agent(db: Session, listing_id: str, actor: User, name: str | None, phone: str | None) -> Listing:
    """«Это агент» straight from the feed, without taking the ad into work (David 11-17: «посмотреть ссылку, и если
    агент — нажать, что это агент, и не брать работу»): the ad leaves the feed, and the number (typed, else the ad's own)
    joins the agent base, so the author's next ads are flagged on ingest (bot/db.py agent_phones)."""
    x = get_or_404(db, Listing, listing_id, "listing", lock=True)
    if db.scalar(select(Property.id).where(Property.listing_id == listing_id, Property.status != "archived")):
        raise DomainError("taken_by_other", "the ad is already in work")
    phone = (phone or "").strip() or next(iter(extract_phones(x.text or "")), None)
    if phone and not _real_owner(db, phone):
        upsert_owner(db, (name or "").strip() or None, phone, x.address, next(iter(jload(x.urls, []) or []), None),
                     is_agent=True, actor=actor)
    x.decision, x.status, x.status_reason = "rejected", "rejected", "агент"
    x.decided_by, x.decided_at, x.updated_at = actor.id, now_text(), now_text()
    log_event(db, "listing", x.id, "marked_agent", actor, phone=phone)
    db.commit()
    return x


def new_listing_facets(db: Session, competitor: bool = False) -> dict:
    """Filter options with counts over the same untaken listings (or the competitor ads): exact sources, districts, metro."""
    scope = (Listing.source.in_(competitor_sources()),) if competitor else _untaken()

    def facet(col):
        rows = db.execute(select(col, func.count()).where(*scope, col.isnot(None), col != "")
                          .group_by(col).order_by(func.count().desc()))
        return [{"value": v, "count": n} for v, n in rows]
    # Threads is one search over every author (th:<author> per ad): one option, filtered by its type
    source = case((Listing.source.like("th:%"), "threads"), else_=Listing.source)
    sources = facet(source)
    # Facebook groups are keyed by a group id/slug: show the name the admin gave it in «Источники»
    names = dict(db.execute(select("fb:" + Source.key, Source.name).where(Source.kind == "fb_group", Source.name.isnot(None))).all())
    for x in sources:
        if names.get(x["value"]):
            x["label"] = names[x["value"]]
    return {"sources": sources, "districts": facet(Listing.district), "metros": facet(_metro())}


# ---------------------------------------------------------------- take (C1 → C2)

def take(db: Session, listing_id: str | None, agent: User, manual_fields: dict | None = None, commit: bool = True) -> Property:
    """"Беру в работу": the object is locked to this agent; 409 taken_by_other for anyone else."""
    if listing_id:
        listing = get_or_404(db, Listing, listing_id, "listing", lock=True)
        if listing.source in set(db.scalars(competitor_sources())):
            raise DomainError("competitor_listing", "a competitor's ad is not taken into work")
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
            from_feed = existing.status == "archived"  # its ad was back in «Новые объявления»
            existing.agent_id, existing.taken_at = agent.id, now()
            existing.archive_reason = existing.archive_comment = existing.archived_at = None
            _bind_owner(db, existing, db.get(Owner, existing.owner_id) if existing.owner_id else None, agent)
            _status(db, existing, "taken", agent, "taken")
            if from_feed:
                plan_take_idle(db, existing)
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
    if p.is_exclusive:  # a manual object typed in as an exclusive
        schedule_dup_scan(db, p, "exclusive")
    if listing_id:
        plan_take_idle(db, p)
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
            v = {**{x: y for x, y in (p.post_fields or {}).items() if x in SITE_KEYS}, **clean_post_fields(v)}
        elif k == "cadastral_code":  # only a changed code is checked: old objects keep their legacy codes
            v = p.cadastral_code if str(v or "").strip() == (p.cadastral_code or "").strip() else clean_cadastral(v)
        elif k in ("rent_from", "rent_to"):
            from app.services.deals import parse_date
            v = parse_date(v) if v not in (None, "") else None
        elif k in LEASE:
            v = (str(v).strip() or None) if v is not None else None
        setattr(p, k, v)


def owner_lease_days(db: Session) -> list[int]:
    """«Собственник сдал»: days before the lease end the agent is reminded (Управление → Настройки)."""
    return [int(x) for x in str(settings_svc.get(db, "owner_lease_remind_days") or "").split(",") if x.strip().isdigit()]


def schedule_lease_reminders(db: Session, p: Property) -> None:
    """The agent hears CONTRACT_REMIND_DAYS (30,7,1) before the end of a hand-typed lease, at 10:00 Tbilisi;
    an owner's own lease («Собственник сдал») — owner_lease_days, then the object shows in «Освобождаются»."""
    from app.services.deals import _at_10
    jobs.cancel(db, "property_lease_expiry", ref_prefix=f"{p.id}:")
    days_list = owner_lease_days(db) if p.archive_reason == "rented_by_owner" else env.contract_remind_days
    for days in days_list if p.rent_to else ():
        due = _at_10(p.rent_to - timedelta(days=days))
        if due > now():
            jobs.schedule(db, "property_lease_expiry", f"{p.id}:{days}", due, {"days": days})


def is_locked_for(p: Property, user: User) -> bool:
    """The agent's key fields and archiving go through «Запрос оператору» (LOCKED_AFTER_VERIFY)."""
    return not at_least(user, "operator") and p.verified_at is not None


def _current(db: Session, p: Property, k: str):
    if k in ("owner_name", "owner_phone", "owner_phones"):
        o = db.get(Owner, p.owner_id) if p.owner_id else None
        return (o.name if k == "owner_name" else o.phone if k == "owner_phone" else owner_phones(o)) if o else None
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
    if k == "owner_phone":  # «599…» typed again is the same «+995599…»
        return e164(v)
    if k == "owner_phones":
        return [{"phone": r["phone"], "note": r["note"]} for r in phone_rows(v)] or None
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
            if old != new and not (k in ("owner_name", "owner_phone", "owner_phones") and new is None):  # empty = "don't touch" there
                locked[k] = {"old": old, "new": new}
    return data, locked


def update(db: Session, pid: int, data: dict, actor: User, reason: str | None = None) -> Property:
    """Edit from the card. After verification an agent's change of LOCKED_AFTER_VERIFY fields is not applied but sent as
    a «Запрос оператору» (p.change_request is set then); the rest is applied as usual."""
    p = _prop(db, pid)
    _require_owner(p, actor)
    if oid := data.pop("owner_id", None):  # picked from the owners list: that contact, its phone may be hidden to agents
        o = pick_owner(db, oid, actor)
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
    """No commit (an approved change request applies through here too). A published object whose post shows a changed
    field is reposted (David 11-23: «Правка» / «% Скидка» → the channel follows)."""
    was_exclusive, was_rent_to, was_post = p.is_exclusive, p.rent_to, _post_key(p)
    _apply(p, data)
    if p.status == "published" and _post_key(p) != was_post and has_post(db, p):
        schedule_repost(db, p)
    if p.rent_to != was_rent_to:
        schedule_lease_reminders(db, p)
    _set_owner_contact(db, p, data.get("owner_name"), data.get("owner_phone"), actor, bool(data.get("owner_separate")),
                       data.get("owner_phones"))
    if p.is_exclusive and not was_exclusive:
        for a in external_ads(db, [p.id]).get(p.id, []):
            log_event(db, "property", p.id, "exclusive_conflict", actor, **a)
        schedule_dup_scan(db, p, "exclusive")
    p.updated_at = now()
    fields = sorted(k for k in data if k in EDITABLE or k in ("owner_name", "owner_phone", "owner_phones"))
    if fields:
        log_event(db, "property", p.id, "updated", actor, fields=fields)


POST_KEYS = ("address", "district", "lat", "lng", "price", "currency", "bedrooms", "rooms", "area", "floor", "total_floors",
             "description", "is_exclusive", "post_fields")  # what the channel post shows (post_text / post_rich)


def _post_key(p: Property) -> tuple:
    return tuple(str(getattr(p, k)) if k != "post_fields" else repr(sorted((p.post_fields or {}).items())) for k in POST_KEYS)


def has_post(db: Session, p: Property) -> bool:
    """The object has a live post in the channel (ours or a legacy post_url)."""
    return bool(p.post_url) or bool(db.scalar(select(PropertyPublication.id).where(
        PropertyPublication.property_id == p.id, PropertyPublication.channel == "tg",
        PropertyPublication.status == "published").limit(1)))


def schedule_repost(db: Session, p: Property) -> None:
    """The worker posts the object again (job tg_repost -> publish: the new post out, then the old ones deleted, so the
    channel never holds two). One pending job per object: a second edit just replaces it. No commit."""
    jobs.schedule(db, "tg_repost", p.id, now())


def _mask_phone(phone: str | None) -> str:
    d = re.sub(r"\D", "", phone or "")
    return f"…{d[-4:]}" if d else "—"


def _set_owner_contact(db: Session, p: Property, name: str | None, phone: str | None, actor: User,
                       separate: bool = False, phones: list | None = None) -> None:
    """Fix the owner's name/phone from the object card (a typo like "Sofa" for "Nadia"). The contact lives on the Owner,
    so his other objects show the fix too. A phone that already belongs to another owner in the base re-links the object
    to that owner instead of creating a twin, unless separate (the agent chose «оставить отдельным»). Logged on the
    object's timeline as old → new, the phones masked. phones (the owner's whole list, the main first) replaces his
    numbers; only phone (the main) keeps his other numbers. Empty = don't touch."""
    rows = phone_rows(phones) or None
    name, phone = (name or "").strip() or None, rows[0]["phone"] if rows else (phone or "").strip() or None
    if not name and not phone:
        return
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    before = (o.name, o.phone, {r["norm"] for r in owner_phones(o)}) if o else (None, None, set())
    if o is None:
        o, _ = upsert_owner(db, name, phone, p.address, p.source_url, actor=actor, phones=rows)
        p.owner_id = o.id
        _bind_owner(db, p, o, actor)
    elif phone and norm_phone(phone) != o.phone_norm:
        other = None if separate else find_owner(db, phone)
        if other and other.id != o.id:
            p.owner_id, o = other.id, other
            _bind_owner(db, p, o, actor)
            add_owner_phones(o, rows or [])
        else:
            set_owner_phones(o, rows or phone_rows([phone, *owner_phones(o)[1:]]))
    elif rows:
        set_owner_phones(o, rows)
    if name:
        o.name = name
    changes = []
    if before[0] != o.name:
        changes.append(f"{before[0] or '—'} → {o.name or '—'}")
    if norm_phone(before[1]) != o.phone_norm:
        changes.append(f"{_mask_phone(before[1])} → {_mask_phone(o.phone)}")
    after = owner_phones(o)
    if {r["norm"] for r in after} - {o.phone_norm} != before[2] - {norm_phone(before[1]) or None}:
        changes.append("номера: " + ", ".join(_mask_phone(r["phone"]) for r in after))
    if changes:
        log_event(db, "property", p.id, "owner_contact", actor, owner_id=o.id, note="; ".join(changes))


def owner_refused(db: Session, pid: int, agent: User, comment: str | None = None) -> Property:
    """Owner reached but won't work with the agency: the object goes back to the feed so another agent can try; the feed
    card says who called and when (refusals_for). Re-taking reuses this property, so history and owner stay."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed", "owner_confirmed", "shoot_scheduled")
    _archive_now(db, p, agent, "owner_refused", comment, action="owner_refused")
    _release_owner(db, p, agent)
    db.commit()
    return p


def _release_owner(db: Session, p: Property, actor: User | None) -> None:
    """The object's owner is freed from its agent unless he has other live objects of that agent (he never became ours)."""
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    if o is not None and o.agent_id and o.agent_id == p.agent_id and not db.scalar(select(Property.id).where(
            Property.owner_id == o.id, Property.id != p.id, Property.agent_id == o.agent_id,
            Property.status != "archived").limit(1)):
        o.agent_id = None
        log_event(db, "owner", o.id, "agent_released", actor, property_id=p.id)


# ------------------------------------------------- «Взят», not in work → back to the feed (Stanislav 11-16, David 11-22)
NOT_IN_WORK = ("taken", "call_failed")  # not reached / reached but not confirmed: a call alone keeps nothing (David 11-22)


def take_idle_warn_before(minutes: int) -> int:
    """Minutes before the return the agent is warned: 15 for the hour, 2 h for the day."""
    return 120 if minutes >= 720 else 15


def take_idle_minutes(db: Session) -> int:
    try:
        return max(0, int(settings_svc.get(db, "take_idle_min") or 0))
    except ValueError:
        return 0


def plan_take_idle(db: Session, p: Property) -> None:
    """An object taken from «Новые объявления»: a warning ~15 min before, then the return (worker job take_idle).
    Only this take path plans it: hand assignments, own exclusives and manual objects never go back. No commit."""
    n = take_idle_minutes(db)
    if n <= 0:
        return
    ret = aware(p.taken_at) + timedelta(minutes=n)
    w = take_idle_warn_before(n)
    warn = n > w and ret - timedelta(minutes=w) > now()
    jobs.schedule(db, "take_idle", p.id, ret - timedelta(minutes=w) if warn else ret,
                  {"agent_id": p.agent_id, "taken_at": aware(p.taken_at).isoformat(), "return_at": ret.isoformat(),
                   "minutes": n, "phase": "warn" if warn else "return"})


def idle_since_take(db: Session, p: Property) -> bool:
    """Still not in work since the take: «Взят» / «Не дозвонился», whatever was clicked (David 11-22: a day without
    getting the owner on board → the object goes to someone else). In work = owner confirmed and on, or the owner reached
    and thinking (a call-back date set)."""
    if p.status not in NOT_IN_WORK or not p.agent_id or not p.taken_at:
        return False
    return not db.scalar(select(Event.id).where(
        Event.entity == "property", Event.entity_id == str(p.id), Event.actor_id == p.agent_id,
        Event.at > aware(p.taken_at), Event.action == "owner_thinking").limit(1))


def idle_return_at(db: Session, p: Property) -> str | None:
    """When the object goes back if nothing is done (the card's timer), else None."""
    if take_idle_minutes(db) <= 0 or not idle_since_take(db, p):
        return None
    job = next((j for j in jobs.pending(db, "take_idle", p.id)), None)
    return (job.payload or {}).get("return_at") if job else None


def return_idle(db: Session, p: Property, minutes: int) -> int:
    """Back to the feed: archived as «Возвращён: нет действий» (its ad shows in «Новые объявления» again, a re-take
    reuses this object), agent cleared, his owner binding released. Returns the agent id. No commit."""
    agent = db.get(User, p.agent_id)
    span = f"{minutes // 60} ч" if minutes % 60 == 0 else f"{minutes} мин"
    _release_owner(db, p, None)
    _archive_now(db, p, None, "idle_return", f"{span} не взят в работу (агент {users_svc.tag_of(agent)})",
                 action="idle_returned")
    agent_id, p.agent_id = p.agent_id, None
    return agent_id


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


def check_owner_lease(rent_from, rent_to) -> tuple:
    """«Собственник сдал» needs the lease dates (Stanislav 11-16): without them the flat is lost when it frees up."""
    from app.services.deals import parse_date
    try:
        f, t = parse_date(rent_from), parse_date(rent_to)
    except ValueError:
        f = t = None
    if not f or not t:
        raise DomainError("lease_dates_required", "Укажите, с какого по какое число собственник сдал")
    if t <= f:
        raise DomainError("bad_lease_dates", "«Сдано по» должно быть позже «Сдано с»")
    return f, t


def _lease_comment(comment: str | None, price) -> str | None:
    n = _num(price)
    return " · ".join(x for x in (f"сдано за ${n:g}" if n else None, (comment or "").strip() or None) if x) or None


def set_owner_lease(db: Session, p: Property, rent_from, rent_to, actor: User | None) -> None:
    """The owner's own lease on a «Собственник сдал» object: the dates, the agent's reminders, the calendar. No commit."""
    p.rent_from, p.rent_to = check_owner_lease(rent_from, rent_to)
    schedule_lease_reminders(db, p)
    log_event(db, "property", p.id, "owner_lease", actor, rent_from=p.rent_from.isoformat(), rent_to=p.rent_to.isoformat())


def owner_rented_self(db: Session, pid: int, agent: User, comment: str | None = None, rent_from=None, rent_to=None,
                      price=None) -> Property:
    """The owner rented the flat out himself: the object is closed (with the lease dates) and its listing never comes
    back to the feed; when the lease ends it shows in «Освобождаются»."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed", "owner_confirmed", "shoot_scheduled")
    check_owner_lease(rent_from, rent_to)
    _archive_now(db, p, agent, "rented_by_owner", _lease_comment(comment, price), action="owner_rented_self")
    set_owner_lease(db, p, rent_from, rent_to, agent)
    listing = db.get(Listing, p.listing_id) if p.listing_id else None
    if listing:
        listing.decision, listing.status, listing.status_reason = "rejected", "rejected", "сдал сам"
        listing.decided_by, listing.decided_at, listing.updated_at = agent.id, now_text(), now_text()
    db.commit()
    return p


DONT_CALL_REASONS = ("owner_refused", "rented_by_owner")  # the owner was reached and said no: «Не хочет работать», «Сдал сам»


def refusals_for(db: Session, listing_ids: list[str]) -> dict[str, dict]:
    """listing id → the latest «don't call again» note (DONT_CALL_REASONS) of an archived object the ad belongs to: its
    origin ad and every ad of that flat on other sites (property_listings), so nobody calls the owner twice (David 11-18)."""
    if not listing_ids:
        return {}
    pairs = db.execute(select(Property.listing_id, Property.id).where(Property.listing_id.in_(listing_ids)).union(
        select(PropertyListing.listing_id, PropertyListing.property_id).where(PropertyListing.listing_id.in_(listing_ids)))).all()
    props = {p.id: p for p in db.scalars(select(Property).where(Property.id.in_({pid for _, pid in pairs}),
                                                                 Property.status == "archived",
                                                                 Property.archive_reason.in_(DONT_CALL_REASONS)))}
    who = {}  # the agent who archived it (the latest archiving event); legacy objects have none
    for e in db.scalars(select(Event).where(Event.entity == "property", Event.entity_id.in_([str(i) for i in props]),
                                            Event.action.in_(("owner_refused", "owner_rented_self", "archived")))
                        .order_by(Event.at)):
        who[int(e.entity_id)] = e.actor_label
    out = {}
    for lid, pid in pairs:
        p = props.get(pid)
        if p is None or p.archived_at is None or (lid in out and out[lid]["at"] >= p.archived_at.isoformat()):
            continue
        out[lid] = {"by": who.get(pid), "at": p.archived_at.isoformat(), "comment": p.archive_comment,
                    "reason": p.archive_reason, "property_id": pid}
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
        # the duplicate's exclusive contract and owner docs move to the original (_merge_duplicate); refused only when
        # both have a contract, so neither signed one is lost
        if contract(db, p.id) and contract(db, dup):
            raise DomainError("dup_has_contract", f"У объекта №{dup} тоже есть эксклюзивный договор — дубль не удаляется, "
                                                  "чтобы не потерять ни один из договоров")
    return {"reason": reason, "comment": comment, "duplicate_of": dup}


ARCHIVE_HEAD = {"paused": "⏸ <b>Приостановлен</b>", "unpublished": "🚫 <b>Снят с публикации</b>"}


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
    if reason not in ("owner_refused", "idle_return"):  # David 11-23: архив / пауза / снятие → агенту и оператору
        head = ARCHIVE_HEAD.get(reason) or f"🗄 <b>В архив: {ARCHIVE_REASONS[reason]}</b>"
        notify_later(db, "prop_event", p, head, actor.id if actor else None, None, True, p.archive_comment)
    if reason != "duplicate":  # any archive takes the post down (Stanislav 11-23); a duplicate: dup_purge
        _unpost_later(db, p)
    if reason == "duplicate" and duplicate_of:
        _merge_duplicate(db, p, duplicate_of, actor)


def _merge_duplicate(db: Session, p: Property, orig: int, actor: User | None) -> None:
    """«Дубль» deletes the object (Stanislav 11-16): its leads / showings, deals, contracts, client views, matched
    channel posts and outside ads move to the object that stays; the worker (job dup_purge) then deletes its Telegram
    posts, files and the row itself."""
    moved = {}
    o = db.get(Property, orig)
    kinds = (CONTRACT, *DOC_KINDS)
    have = set(db.scalars(select(PropertyMedia.kind).where(PropertyMedia.property_id == orig, PropertyMedia.kind.in_(kinds))))
    docs = [m for m in db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == p.id,
                                                               PropertyMedia.kind.in_(kinds))) if m.kind not in have]
    for m in docs:  # the files stay where they are; the rows no longer belong to the duplicate, so dup_purge keeps them
        m.property_id = orig
    if docs:
        moved["owner_docs"] = [m.kind for m in docs]
    if p.is_exclusive and not o.is_exclusive:
        o.is_exclusive = True
        moved["is_exclusive"] = True
    if o.is_exclusive:
        schedule_dup_scan(db, o, "merged")
    for k in ("agent_id", "owner_id"):  # the object that stays keeps the duplicate's agent / owner when it has none
        if getattr(p, k) and not getattr(o, k):
            setattr(o, k, getattr(p, k))
            moved[k] = getattr(p, k)
    if p.sign_draft and not o.sign_draft and CONTRACT not in have:
        o.sign_draft = p.sign_draft
    if CONTRACT not in have and not contract_done(db, o) and (w := no_esign(db, p.id)) and w.status == "approved":
        w.entity_id = orig  # the operator's «без электронной подписи» goes with the duplicate's contract step
        moved["no_esign"] = w.id
    for model in (Lead, Deal, Contract, ClientView, ChannelPost, PropertyListing):
        n = db.execute(update_(model).where(model.property_id == p.id).values(property_id=orig)).rowcount
        if n:
            moved[model.__tablename__] = n
    db.execute(update_(Property).where(Property.duplicate_of == p.id).values(duplicate_of=orig))
    log_event(db, "property", p.id, "deleted_as_duplicate", actor, original=orig, moved=moved)
    log_event(db, "property", orig, "duplicate_merged", actor, duplicate=p.id, moved=moved)
    jobs.schedule(db, "dup_purge", p.id, now())
    from app.services import site_feed
    site_feed.clear_cache()


def purge_duplicate(db: Session, p: Property) -> str:
    """Worker: the duplicate's Telegram posts, then its files (photos, videos + previews, owner docs), then the row.
    A post Telegram refused keeps the object in the archive (the card shows it) so the post isn't lost track of."""
    r = unpost(db, p, None)
    if r["failed"]:
        db.commit()
        return f"not deleted: {', '.join(r['failed'])}"[:500]
    for path in db.scalars(select(PropertyMedia.path).where(PropertyMedia.property_id == p.id)):
        try:
            storage.delete(path)
        except Exception:
            log.warning("could not delete %s", path)
    db.delete(p)
    db.commit()
    return "ok"


def archive(db: Session, pid: int, actor: User, reason: str | None = None, comment: str | None = None,
            duplicate_of: int | None = None, rent_from=None, rent_to=None, price=None, lease_required: bool = True) -> Property:
    """«В архив» with a mandatory reason; «Дубль» deletes the object (_merge_duplicate). Staff (and the agent before verification) archive
    right away; a verified object's agent sends a «Запрос оператору» (p.change_request)."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, actor)
    if p.status == "archived":
        raise DomainError("bad_status", "Объект уже в архиве")
    # lease_required=False: the bot's «Уже сдана» on a lead — no dates there, the card asks for them later
    lease = check_owner_lease(rent_from, rent_to) if reason == "rented_by_owner" and (lease_required or rent_to) else None
    if lease:
        comment = _lease_comment(comment, price)
    a = check_archive(db, p, reason, comment, duplicate_of)
    p.change_request = None
    if is_locked_for(p, actor):
        from app.services import change_requests
        payload = {"archive_reason": {"old": p.archive_reason, "new": a["reason"]}}
        if a["comment"]:
            payload["archive_comment"] = {"old": None, "new": a["comment"]}
        if a["duplicate_of"]:
            payload["duplicate_of"] = {"old": p.duplicate_of, "new": a["duplicate_of"]}
        if lease:
            payload["rent_from"] = {"old": None, "new": lease[0].isoformat()}
            payload["rent_to"] = {"old": None, "new": lease[1].isoformat()}
        p.change_request = change_requests.create(db, "property", p.id, "archive", payload, actor, a["comment"])
    else:
        _archive_now(db, p, actor, a["reason"], a["comment"], a["duplicate_of"])
        if lease:
            set_owner_lease(db, p, *lease, actor)
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


def owner_has_phone(pn: str):
    """SQL: the owner has this normalized number, main or any other (owners.phones, GIN index)."""
    return or_(Owner.phone_norm == pn, Owner.phones.contains([{"norm": pn}]))


def owner_phone_like(pn: str):
    """SQL: a digit fragment of any of the owner's numbers (staff search)."""
    return or_(Owner.phone_norm.like(f"%{pn}%"), cast(Owner.phones, Text).like(f"%{pn}%"))


def find_owner(db: Session, phone=None, address: str | None = None) -> Owner | None:
    """The owner with this number (main or another one); phone may be a list: the first number that is known wins."""
    for pn in (norm_phone(x) for x in (phone if isinstance(phone, list) else [phone])):
        if len(pn) > 4 and (o := db.scalar(select(Owner).where(owner_has_phone(pn)).order_by(Owner.id))):
            return o
    return None


def add_owner_phones(o: Owner, rows: list[dict]) -> None:
    """Numbers he doesn't have yet go to the end of his list (the main stays)."""
    have = owner_phones(o)
    new = [r for r in rows if r["norm"] not in {h["norm"] for h in have}]
    if new:
        set_owner_phones(o, have + new)


def upsert_owner(db: Session, name: str | None, phone: str | None, address: str | None = None, link: str | None = None,
                 is_agent: bool = False, actor: User | None = None, phones: list | None = None) -> tuple[Owner, bool]:
    """Owner_Base row by normalized phone (apps-script addToOwnerBase): any of the numbers finds him, and the ones he
    doesn't have are added. phones = the whole list (the main first), else just phone. Returns (owner, created)."""
    rows = phone_rows(phones) or phone_rows([phone])
    o = find_owner(db, [r["phone"] for r in rows])
    if o:
        add_owner_phones(o, rows)
        if is_agent:
            o.is_agent = True
        if name and (not o.name or o.name == "—"):
            o.name = name
        if address and not o.address:
            o.address, o.address_norm = address, norm_address(address)
        return o, False
    o = Owner(name=name, address=address, address_norm=norm_address(address), link=link, is_agent=is_agent,
              created_by=actor.id if actor else None)
    set_owner_phones(o, rows)
    db.add(o)
    db.flush()
    log_event(db, "owner", o.id, "created", actor, is_agent=is_agent)
    return o, True


def owner_or_agent(db: Session, pid: int, agent: User, is_owner: bool, owner_name: str | None = None,
                   owner_phone: str | None = None, owner_phones: list | None = None) -> Property:
    """C2b / C3: an agent answered → object stays in the base marked "agent"; an owner → owner_confirmed.
    The owner is saved to the base right away; dup_check records whether the phone was already known."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "taken", "call_failed", "owner_confirmed", "agent_marked")
    rows = phone_rows(owner_phones) or phone_rows([owner_phone])
    owner_phone = rows[0]["phone"] if rows else None
    if not owner_phone and not owner_name:
        raise DomainError("owner_required", "owner name or phone is required")
    mark_agent = not is_owner
    if mark_agent and _real_owner(db, owner_phone, p.id):  # a known owner with other real objects stays an owner
        mark_agent = False
    owner, created = upsert_owner(db, owner_name, owner_phone, p.address, p.source_url, is_agent=mark_agent, actor=agent,
                                  phones=rows)
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
    elif not at_least(actor, "operator"):
        raise DomainError("owner_taken", f"Собственник закреплён за {users_svc.tag_of(db.get(User, o.agent_id))}", 409)
    else:
        log_event(db, "property", p.id, "owner_agent_override", actor, owner_id=o.id, owner_agent_id=o.agent_id)


def transfer_owner(db: Session, owner_id: int, to_agent_id: int, by_user: User, reason: str | None = None,
                   commit: bool = True) -> Owner:
    """«Передать собственника агенту…»: staff only (stage 6's agent «Запрос оператору» is approved through here, e.g. the
    agent left). The owner and his live objects of the previous agent move to the new one; every object of the owner
    and the owner himself get a history event."""
    if not at_least(by_user, "operator"):
        raise forbidden("only operators transfer owners")
    o = get_or_404(db, Owner, int(owner_id), "owner", lock=True)
    to = get_or_404(db, User, int(to_agent_id), "user")
    if not is_field(to) or not to.is_active:
        raise DomainError("bad_agent", "Передать можно только активному агенту")
    if o.agent_id == to.id:
        raise DomainError("bad_status", "Собственник уже закреплён за этим агентом")
    old = db.get(User, o.agent_id) if o.agent_id else None
    reason = (reason or "").strip() or None
    o.agent_id = to.id
    data = {"from_agent_id": old.id if old else None, "to_agent_id": to.id, "reason": reason,
            "comment": f"{old.name if old else '—'} → {to.name}" + (f" · {reason}" if reason else "")}
    log_event(db, "owner", o.id, "owner_transferred", by_user, **data)
    props = []
    for p in db.scalars(select(Property).where(Property.owner_id == o.id).with_for_update()):
        # an object dropped into the archive goes along (David 11-22: #8462 handed to Arthur stayed David's, unseen)
        moved = p.agent_id == (old.id if old else None) and (
            p.status not in ("archived", "rented") or (p.status == "archived" and p.archive_reason in BACK_TO_WORK))
        if moved:
            p.agent_id, p.updated_at = to.id, now()
        log_event(db, "property", p.id, "owner_transferred", by_user, owner_id=o.id, moved=moved, **data)
        props.append({"id": p.id, "address": p.address, "status": p.status, "archive_reason": p.archive_reason})
    # David 11-22: the new agent learns it from the bot, with the objects (an archived one: «Снова в работу» on its card)
    notify_later(db, "owner_transferred", to.id, o.name, by_user.name, props, reason)
    if old is not None and old.id != by_user.id:
        notify_later(db, "owner_transferred_away", old.id, o.name, to.name, by_user.name, props)
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
        res = check_duplicate(db, [r["phone"] for r in owner_phones(owner)], p.address, exclude_property=p.id)
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
    reports.save(db, "makefoto", {"address": p.address, "phone": ", ".join(r["phone"] for r in owner_phones(owner)), "owner": owner.name if owner else "",
                                  "date": p.shoot_at.astimezone(_tz()).strftime("%d.%m.%Y %H:%M") if p.shoot_at else "",
                                  "agent": agent.name, "link": p.source_url or "", "status": p.dup_check,
                                  "property_id": p.id}, agent)
    return p, p.dup_check


def _tz():
    from app.services.common import TZ
    return TZ


# ---------------------------------------------------------------- media, checklist, review (C4, B7)
ROOM_ALIAS = {"Ванная": "Санузел"}  # renamed room tags (David 11-23): old tags read as the new name, no data rewrite

def media_root() -> Path:
    return Path(env.MEDIA_DIR)


def _media_check(db: Session, p: Property, actor: User, kinds: list[str]):
    _require_media_editable(p, actor)
    _require_status(p, *AGENT_MEDIA_STATUSES, "review", "published")
    have = dict(db.execute(select(PropertyMedia.kind, func.count()).where(PropertyMedia.property_id == p.id)
                           .group_by(PropertyMedia.kind)).all())
    for kind in kinds:
        have[kind] = have.get(kind, 0) + 1
        if have[kind] > MAX_MEDIA[kind]:
            raise DomainError("media_limit", f"У объекта максимум {MAX_MEDIA['photo']} фото и {MAX_MEDIA['video']} видео. "
                              "Удалите лишнее, чтобы добавить новое", 422)


def upload_media(db: Session, pid: int, files: list[tuple[str, bytes]], room: str | None, actor: User) -> list[PropertyMedia]:
    """The slow part (storage put, preview, dHash: ~2 s per iPhone HEIC, a 100+ MB video) runs before the row lock, so
    the 3 parallel uploads of the CRM don't queue behind each other; the checks run again under the lock."""
    kinds = []
    for name, content in files:
        ext = os.path.splitext(name or "")[1].lower() or ".jpg"
        if ext not in PHOTO_EXT | VIDEO_EXT:
            raise DomainError("bad_file", f"Можно загружать только фото и видео: {name}")
        if len(content) > MAX_UPLOAD:
            raise DomainError("too_large", f"Файл слишком большой: {name}")
        kinds.append("video" if ext in VIDEO_EXT else "photo")
    _media_check(db, _prop(db, pid), actor, kinds)
    db.rollback()  # no transaction stays open during the uploads below
    from app.services.matching import photo_hash  # the matcher compares new ads with these photos
    stored = []  # (key, kind, dhash)
    try:
        for (name, content), kind in zip(files, kinds):
            key = f"properties/{int(pid)}/{uuid.uuid4().hex}{os.path.splitext(name or '')[1].lower() or '.jpg'}"
            storage.put(key, content)
            stored.append((key, kind, None))
            storage.put_preview(key, content)
            stored[-1] = (key, kind, photo_hash(content) if kind == "photo" else None)
        p = _prop(db, pid, lock=True)
        _media_check(db, p, actor, kinds)
        pos = db.scalar(select(func.coalesce(func.max(PropertyMedia.position), -1)).where(PropertyMedia.property_id == p.id))
        out = []
        for key, kind, dhash in stored:
            pos += 1
            m = PropertyMedia(property_id=p.id, kind=kind, path=key, room=room if kind == "photo" else None,  # rooms: photos only
                              position=pos, uploaded_by=actor.id, dhash=dhash)
            db.add(m)
            out.append(m)
            if kind == "video":  # the worker prepares the Telegram copy now, not the publish request (notify._tg_video)
                jobs.schedule(db, "tg_video", key, now())
        if p.status in ("owner_confirmed", "shoot_scheduled", "returned"):
            _status(db, p, "media_uploaded", actor, files=len(files))
        else:
            log_event(db, "property", p.id, "media_added", actor, files=len(files))
        if (p.is_exclusive or p.status in DUP_LIVE) and "photo" in kinds:
            # the photos come after the exclusive's first search (a manual object: made, then the photos): searched
            # again with them, or the ad they were taken from is never found (Irakli 11-19, myhome 26699303)
            schedule_dup_scan(db, p, "media")
        db.commit()
    except Exception:
        db.rollback()
        for key, _, _ in stored:  # a parallel upload took the last slot, the card left the agent: no orphan files
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001
                log.warning("could not delete %s", key)
        raise
    return out


UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
PART_TTL = timedelta(days=1)


def upload_part(db: Session, pid: int, upload_id: str, name: str, offset: int, total: int, chunk: bytes,
                room: str | None, actor: User) -> int | list[PropertyMedia]:
    """A big file (an iPhone video, 100-300 MB) in parts (Irakli 11-18: one 2-3 minute request over a phone died midway,
    the video never got in). Parts land at their offset in MEDIA_DIR/uploads, so a retried part just overwrites itself;
    -> the bytes received so far, or (the last part) upload_media's rows."""
    ext = os.path.splitext(name or "")[1].lower()
    if not UPLOAD_ID.fullmatch(upload_id or "") or offset < 0 or total <= 0:
        raise DomainError("bad_upload", "Загрузка сломалась, выберите файл ещё раз")
    if ext not in PHOTO_EXT | VIDEO_EXT:
        raise DomainError("bad_file", f"Можно загружать только фото и видео: {name}")
    if total > MAX_UPLOAD:
        raise DomainError("too_large", f"Файл слишком большой: {name} ({total // 2**20} МБ, можно до {MAX_UPLOAD // 2**20} МБ)")
    if offset == 0:  # the limits before 300 MB travel, not after
        _media_check(db, _prop(db, pid), actor, ["video" if ext in VIDEO_EXT else "photo"])
    else:
        _require_media_editable(_prop(db, pid), actor)
    db.rollback()
    d = Path(env.MEDIA_DIR) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    old = datetime.now().timestamp() - PART_TTL.total_seconds()
    for f in d.iterdir():  # abandoned uploads
        if f.stat().st_mtime < old:
            f.unlink(missing_ok=True)
    path = d / f"{int(pid)}-{actor.id}-{upload_id}{ext}"
    have = path.stat().st_size if path.exists() else 0
    if offset > have:
        raise DomainError("upload_gap", "Часть файла потерялась, продолжаем", 409, data={"have": have})
    if offset + len(chunk) > total:
        raise DomainError("bad_upload", "Загрузка сломалась, выберите файл ещё раз")
    with open(path, "r+b" if path.exists() else "wb") as fh:
        fh.seek(offset)
        fh.write(chunk)
        fh.truncate()
    if offset + len(chunk) < total:
        return offset + len(chunk)
    data = path.read_bytes()
    path.unlink()
    return upload_media(db, pid, [(name, data)], room, actor)


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
    if kind not in DOC_KINDS:
        raise DomainError("not_found", "unknown document kind")
    label, exts = DOC_KINDS[kind]
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
    if kind not in OWNER_DOCS:
        raise DomainError("not_found", "unknown document kind")
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
    schedule_dup_scan(db, p, "contract")
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
    d = p.sign_draft or {}
    return {"id": p.id, "address": p.address or "", "cadastral_code": p.cadastral_code or "",
            "owner_name": owner and owner.name or "", "owner_phone": owner and owner.phone or "",
            "signed_at": c.created_at if c else None,
            "draft": None if c else {k: v for k, v in d.items() if not k.endswith("_file")}}


# ---- the sign page's draft (properties.sign_draft): every field autosaves, the files are kept the moment he gives them;
# reopening the link continues where he stopped. Locked row: the autosave and a file upload don't overwrite each other.
DRAFT_TEXT = ("full_name", "personal_id", "phone", "cadastral_code", "lang", "extract_url")
SIGN_DRAFT_PREFIX = "private/sign-drafts/"


def _set_draft(db: Session, p: Property, **kv) -> dict:
    d = {**(p.sign_draft or {}), **kv, "saved_at": now_text()}
    p.sign_draft = d
    db.commit()
    return {k: v for k, v in d.items() if not k.endswith("_file")}


def save_sign_draft(db: Session, token: str, data: dict) -> dict:
    """What he typed: only the known text fields (trimmed) and the picked NAPR application."""
    p = _signable(db, token, lock=True)
    if contract(db, p.id):
        raise DomainError("already_signed", "Договор уже подписан")
    kv = {k: str(data[k] or "").strip()[:2000 if k == "extract_url" else 200] for k in DRAFT_TEXT if k in data}
    if "registry_app" in data:
        kv["registry_app"] = _registry_app(json.dumps(data["registry_app"]) if isinstance(data["registry_app"], dict) else None)
    return _set_draft(db, p, **kv)


def draft_extract(db: Session, token: str, pdf: bytes, name: str, url: str = "") -> dict:
    """The extract he gave (by its link or as the PDF): kept in private storage, the code and fields read from it."""
    from app.services import registry
    p = _signable(db, token, lock=True)
    if contract(db, p.id):
        raise DomainError("already_signed", "Договор уже подписан")
    if len(pdf) > MAX_OWNER_DOC or not pdf.startswith(b"%PDF"):
        raise DomainError("bad_file", "Выписка — PDF до 15 МБ")
    fields = registry.parse_extract(registry.text_of_pdf(pdf) or "")
    kv = {"extract_file": registry.store_extract(pdf), "extract_name": (name or "extract.pdf")[:200], "extract_url": url[:2000]}
    if code := fields.get("cadastral_code"):
        kv["cadastral_code"] = code
    _set_draft(db, p, **kv)
    return {"cadastral_code": fields.get("cadastral_code"), "fields": fields, "url": url, "name": kv["extract_name"]}


def draft_id_doc(db: Session, token: str, name: str, content: bytes) -> dict:
    """The passport / ID photo: checked like the signing upload, kept privately until he signs."""
    p = _signable(db, token, lock=True)
    if contract(db, p.id):
        raise DomainError("already_signed", "Договор уже подписан")
    ext = _check_owner_doc(OWNER_ID_DOC, name, content)
    old = (p.sign_draft or {}).get("id_doc_file")
    key = f"{SIGN_DRAFT_PREFIX}{p.id}/{uuid.uuid4().hex}{ext}"
    storage.put(key, content)
    out = _set_draft(db, p, id_doc_file=key, id_doc_name=(name or f"id{ext}")[:200])
    if old:
        try:
            storage.delete(old)
        except Exception:
            log.warning("could not delete %s", old)
    return out


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
    docs = {k: v for k, v in (docs or {}).items() if v and v[1]}
    draft = p.sign_draft or {}  # what he gave earlier on the page (the draft) stands in for a file not re-sent
    for kind, fk, nk in ((OWNER_ID_DOC, "id_doc_file", "id_doc_name"), (REGISTRY_EXTRACT, "extract_file", "extract_name")):
        if kind not in docs and draft.get(fk) and (content := storage.read(draft[fk])):
            docs[kind] = (draft.get(nk) or os.path.basename(draft[fk]), content)
    if not data.get("registry_app") and draft.get("registry_app"):
        data = {**data, "registry_app": json.dumps(draft["registry_app"])}
    for kind, (label, _) in OWNER_DOCS.items():
        if not (docs.get(kind) and docs[kind][1]):
            raise DomainError("missing_fields", f"Загрузите: {label}")
        _check_owner_doc(kind, *docs[kind])  # everything is checked before anything is stored
    app = _registry_app(data.get("registry_app"))
    from app.services import registry  # the code from the extract beats a typed one; an empty card takes it
    if code := registry.extract_code(docs[REGISTRY_EXTRACT][1]):
        f["cadastral_code"] = code
        if not (p.cadastral_code or "").strip() and not p.verified_at:
            p.cadastral_code = code
    m = _store_contract(db, p, pdf, None)
    for kind, (name, content) in docs.items():
        if kind in OWNER_DOCS:
            _store_owner_doc(db, p, kind, name, content, None)
    log_event(db, "property", p.id, "exclusive_contract_signed", None, label=f["full_name"], **f, ip=ip, registry_app=app,
              user_agent=(user_agent or "")[:300], pdf_sha256=hashlib.sha256(pdf).hexdigest())
    p.sign_draft = {}
    schedule_dup_scan(db, p, "contract")
    notify_later(db, "prop_event", p, "✍️ <b>Собственник подписал эксклюзивный договор</b>", None, None, True, f["full_name"])
    db.commit()
    if draft.get("id_doc_file"):  # copied into the object's owner docs above; the extract key is shared (content hash)
        try:
            storage.delete(draft["id_doc_file"])
        except Exception:
            log.warning("could not delete %s", draft["id_doc_file"])
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


def no_esign(db: Session, pid: int):
    """The latest «Без электронной подписи» request (change_requests kind no_esign) of the object, any status."""
    from app.models import ChangeRequest
    return db.scalars(select(ChangeRequest).where(ChangeRequest.entity == "property", ChangeRequest.entity_id == pid,
                                                  ChangeRequest.kind == "no_esign").order_by(ChangeRequest.id.desc())).first()


def contract_done(db: Session, p: Property) -> bool:
    """The exclusive contract step: a signed / uploaded PDF, or the operator approved going without the e-signature."""
    if contract(db, p.id):
        return True
    w = no_esign(db, p.id)
    return w is not None and w.status == "approved"


# David 11-23: not only exclusives — an object taken into work or published looks for its copies and duplicates too
DUP_LIVE = (*AGENT_MEDIA_STATUSES, "review", "published")
DUP_DAILY_DAYS = 30   # the daily search: objects published / taken in the last 30 days (agents copy new ads)
DUP_DAILY_BATCH = 20  # per hourly pass: ≤ 480 a day, the myhome requests and the ss.ge Apify runs spread out


def schedule_daily_dup_scans(db: Session) -> int:
    """The worker's hourly pass: live objects (DUP_LIVE, exclusives too) of the last DUP_DAILY_DAYS whose copies were
    not searched for a day get a dup_scan (myhome + ss.ge copies, then the duplicates). Commits -> how many.
    ponytail: a 30-day window caps the Apify spend (~0.003 $ a search); widen it or add a weekly tier for older ones."""
    since = now() - timedelta(days=DUP_DAILY_DAYS)
    fresh = select(Event.entity_id).where(Event.entity == "property", Event.action == "myhome_copies",
                                          Event.at > now() - timedelta(hours=24))
    from app.models import Job
    queued = select(Job.ref).where(Job.kind == "dup_scan", Job.done_at.is_(None))
    rows = list(db.scalars(select(Property).where(
        Property.status.in_(DUP_LIVE), func.coalesce(Property.published_at, Property.taken_at, Property.created_at) > since,
        cast(Property.id, Text).not_in(fresh), cast(Property.id, Text).not_in(queued))
        .order_by(Property.id.desc()).limit(DUP_DAILY_BATCH)))
    for p in rows:
        jobs.schedule(db, "dup_scan", p.id, now(), {"trigger": "daily"}, replace=False)
    db.commit()
    return len(rows)


def schedule_dup_scan(db: Session, p: Property, trigger: str) -> None:
    """«При эксклюзиве — сразу поиск дублей везде» (Irakli 11-18): the worker runs similar.scan_exclusive within ~30 s
    (a whole-base search takes a second or two, not for a save request). No commit."""
    if not jobs.pending(db, "dup_scan", p.id):  # one is waiting already: it runs the same search
        jobs.schedule(db, "dup_scan", p.id, now(), {"trigger": trigger}, replace=False)


def dup_scan(db: Session, pid: int) -> dict | None:
    """The last duplicate search of the object (its history event) for the card: {n, strong, at, trigger}; pending
    while the job has not run yet."""
    ev = db.scalars(select(Event).where(Event.entity == "property", Event.entity_id == str(pid),
                                        Event.action == "duplicate_scan").order_by(Event.id.desc())).first()
    pending = bool(jobs.pending(db, "dup_scan", pid))
    if ev is None:
        return {"pending": True} if pending else None
    d = ev.data or {}
    return {"n": d.get("n", 0), "strong": d.get("strong") or [], "trigger": d.get("trigger"), "at": ev.at.isoformat(),
            "sig": d.get("sig"), "pending": pending, **({"copies": d["copies"]} if "copies" in d else {})}


def set_media_room(db: Session, pid: int, mid: int, room: str | None, actor: User) -> PropertyMedia:
    """Tag one uploaded file with its room after a batch upload (the agent taps the photo)."""
    p = _prop(db, pid)
    _require_media_editable(p, actor)
    m = db.get(PropertyMedia, mid)
    if not m or m.property_id != p.id:
        raise DomainError("not_found", "media not found")
    m.room = ((room or "").strip()[:60] or None) if m.kind == "photo" else None  # a video has no room; an agent's own tag is free text
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
            if o and _phones_match(owner_phones(owner), owner_phones(o)) and address_match(p.address, other.address):
                return other
    return None


def require_min_photos(db: Session, p: Property) -> None:
    n = sum(m.kind == "photo" for m in media(db, p.id))
    if n < MIN_PHOTOS:
        raise DomainError("few_photos", f"Нужно минимум {MIN_PHOTOS} фото, сейчас {n}")


def submit_for_review(db: Session, pid: int, agent: User, other_flat: bool = False) -> Property:
    p = _prop(db, pid, lock=True)
    _require_owner(p, agent)
    _require_status(p, "media_uploaded", "returned", "shoot_scheduled")
    if not media(db, p.id):
        raise DomainError("no_media", "upload photos or videos first")
    require_min_photos(db, p)
    if p.is_exclusive and not contract_done(db, p):
        raise DomainError("no_exclusive_contract", "Эксклюзив: загрузите подписанный собственником эксклюзивный договор (PDF)")
    if docs := missing_owner_docs(db, p):
        raise DomainError("no_owner_docs", "Эксклюзив: загрузите " + ", ".join(docs))
    if missing := missing_fields(p):
        raise DomainError("missing_fields", "Заполните перед отправкой: " + ", ".join(missing))
    if other := _same_object(db, p):
        if not other_flat:  # the owner's other flat in the same house (Irakli 11-22): the agent says so, the operator sees it
            raise DomainError("object_exists", f"Этот объект уже есть в базе: #{other.id} {other.address or ''}".strip(), 409,
                              data={"property_id": other.id, "address": other.address})
        log_event(db, "property", p.id, "other_flat_confirmed", agent, other=other.id,
                  comment=f"агент: это другая квартира, не #{other.id}")
    _status(db, p, "review", agent)
    db.commit()
    _notify("property_review_request", p)
    _notify("prop_event", p, "🕵️ <b>Отправлен на проверку</b>", agent.id, None, False)  # staff sent it for the agent
    return p


def review(db: Session, pid: int, operator: User, approve: bool, comment: str | None = None) -> Property:
    """B7a: approve → publish; return → back to the agent with a comment."""
    if not at_least(operator, "operator"):
        raise forbidden("only operators review objects")
    p = _prop(db, pid, lock=True)
    _require_status(p, "review")
    if approve:
        return publish(db, p.id, operator, comment=comment)
    p.review_comment, p.reviewed_by, p.reviewed_at = comment, operator.id, now()
    _status(db, p, "returned", operator, comment=comment)
    db.commit()
    _notify("property_returned", p)
    _notify("prop_event", p, "↩️ <b>Возвращён на доработку</b>", operator.id, [], True, comment)
    return p




def district_tag(value: str | None) -> str:
    """CRM district -> the channel's hashtag (app/districts.py); an unknown old value keeps its own words as the tag."""
    return "#" + (districts.tag(value) or (value or "Tbilisi").strip().replace(" ", "").replace("-", ""))


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
    "bathrooms": ("1Bathroom", "2Bathroom", "3Bathroom", "4Bathroom"),  # 1: Irakli 11-15
    "pets": ("Allowed", "NotAllowed", "ByAgreement"),
}
FIXED_AMENITIES = ("Elevator", "Oven", "ParkingPlace", "Conditioner")  # a rent post shows ✖️ for the missing ones
POST_MANY = {
    "amenities": ("Balcony", "Terrace", "Courtyard", "WiFi", "TV", "SmartTV", "Bath", "Shower", "Dishwasher", "Stove",
                  "Microwave", "WashingMachine", "VacuumCleaner", "Fireplace") + FIXED_AMENITIES,
    "periods": ("1Day", "1Month", "2Month", "3Month", "4Month", "5Month", "6Month", "9Month", "12Month"),
}
# kept on the card and the site, never a post hashtag (Irakli 11-17: the old rent list has no #WashingMachine either)
NO_POST_TAG = ("WashingMachine",)
# the old generator's sale block offers only these amenities, in this order (s-amenity); others stay out of a sale post
SALE_AMENITIES = ("Balcony", "Terrace", "Courtyard", "ParkingPlace", "Conditioner", "Dishwasher", "Oven", "Stove", "Bath",
                  "Shower", "Elevator", "Fireplace")
# facts of rent objects imported from the site (oneoff_import_hge.py, read by services/site_feed.py): not in the agent's
# generator, so a save of the generator's fields keeps them and never rejects them
SITE_KEYS = ("service", "site_id", "period", "approx", "months", "site_extra", "offer_ident", "completion")
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
        elif k in SITE_KEYS:
            ok = True
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
          "bathrooms": f"{min(baths, 4)}Bathroom" if baths else None,
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
    district = district_tag(p.district)
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
        has = [a for a in am if a not in FIXED_AMENITIES + NO_POST_TAG] + [a for a in FIXED_AMENITIES if a in am]
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
    the buttons «On the map» (our /map page with the pin) and «Contact» (the bot: a lead). No map block,
    no footer (the contacts sit in Tags); an old-base description is cut to its own words (prose()). media: the slideshow items as rich-message links (tg://photo?id=m0, tg://video?id=m1)."""
    f, h = p.post_fields or {}, lambda x: html_.escape(str(x), quote=True)
    sale = f.get("deal") == "Sale"
    district = district_tag(p.district).lstrip("#")
    beds = "Studio" if p.bedrooms == 0 else (f"{p.bedrooms} bedroom{'s' if p.bedrooms != 1 else ''}" if p.bedrooms else None)
    commercial = f.get("prop_type") not in (None, "Apartment", "House", "Duplex", "Townhouse")
    price = f.get("sale_price") or (int(p.price) if p.price is not None else None)
    head = " · ".join(x for x in (price and f"${price:,}".replace(",", " "),
                                  _human(f["prop_type"]).lower() if commercial else beds, (districts.BY_TAG[district][2] if district in districts.BY_TAG else _human(district))) if x)
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
    # open a web_app button, so it's a link), Google Maps without a point
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
    # David 11-23: only «On the map» and «Contact» (no «I like it»; the bot keeps answering plike: of the old posts)
    where = hge_map_url(p.id) if not p.site_hidden else pin or url  # the site's map on this object, else our pin map
    if where:
        out.append("<tg-button-row>" + btn("📍 On the map", where) + "</tg-button-row>")
    # «Contact» (back by Stanislav 11-11, own full-width row): /start p<id> -> journey.on_post_start, a lead as before
    out.append("<tg-button-row>" + btn("💬 Contact", f"https://t.me/{env.BOT_USERNAME.lstrip('@')}?start=p{p.id}")
               + "</tg-button-row>")
    return "".join(out)


def hge_map_url(pid: int | None = None) -> str:
    """Our site's map (SITE_URL) on one object: ?q=o<id> is its listing ID on the site (site_feed: f"o{p.id}"), the
    site's search finds exactly it and zooms in; without an id, the whole map (Stanislav 11-15)."""
    return f"{env.SITE_URL.rstrip('/')}/map" + (f"?q=o{pid}" if pid else "")


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


RENTED_REASONS = ("rented_by_us", "rented_by_owner")  # «Сдано» (and «Сдал сам»): the posts leave Telegram
TG_LINK = re.compile(r"t\.me/(c/)?(\w+)/(?:\d+/)?(\d+)")  # t.me/<channel>/<id>, t.me/c/<chat>[/<topic>]/<id>


def _post_ref(pub: PropertyPublication):
    """(chat, message ids, album) of our post: every id stored at publishing (notify.publish_post), else a legacy post's
    one message from its link (album=True: the userbot finds the rest of the album). None: nothing to go by."""
    m = pub.messages or {}
    if m.get("ids"):
        return m["chat"], m["ids"], False
    if u := TG_LINK.search(pub.url or ""):
        return ("-100" + u[2] if u[1] else "@" + u[2]), [int(u[3])], True
    return None


def _legacy_pub(db: Session, p: Property) -> None:
    """A post known only as properties.post_url (old base, before property_publications) gets its row, so unpost sees it."""
    if (p.post_url or "").strip() and not db.scalar(select(PropertyPublication.id).where(
            PropertyPublication.property_id == p.id, PropertyPublication.url == p.post_url)):
        db.add(PropertyPublication(property_id=p.id, channel="tg", url=p.post_url, message_id=p.post_message_id,
                                   published_at=p.published_at))
        db.flush()


def unpost(db: Session, p: Property, actor: User | None, keep: int | None = None, action: str = "tg_removed") -> dict:
    """Deletes our Telegram posts of the object — every published tg publication except `keep` (the fresh repost):
    deleted -> removed; one Telegram refused, or a legacy row without a message id, keeps «published» with
    delete_error (the object card shows it). Logs `action` when there was anything to delete. Never raises, no commit."""
    _legacy_pub(db, p)
    pubs = db.scalars(select(PropertyPublication).where(
        PropertyPublication.property_id == p.id, PropertyPublication.channel == "tg",
        PropertyPublication.status == "published", PropertyPublication.id != (keep or 0))).all()
    deleted, failed = [], []
    for pub in pubs:
        ref = _post_ref(pub)
        ok = bool(ref) and _notify("delete_post", *ref)
        if ok:
            pub.status, pub.removed_at, pub.delete_error = "removed", now(), None
            deleted.append(pub.url)
        else:
            pub.delete_error = "Telegram refused" if ref else "no message id"
            failed.append(pub.url or f"#{pub.id}")
            log.warning("property %s: post %s not deleted (%s)", p.id, pub.url, pub.delete_error)
    if keep is None and p.post_url in deleted:
        p.post_url = p.post_message_id = None
    if pubs:
        log_event(db, "property", p.id, action, actor, deleted=len(deleted), failed=failed)
    db.flush()
    return {"deleted": len(deleted), "failed": failed}


def _unpost_later(db: Session, p: Property) -> None:
    """Rented / paused / unpublished: the worker deletes the posts (job tg_unpost) — the status change never waits on
    Telegram. A repost still pending is dropped."""
    jobs.cancel(db, "tg_repost", p.id)
    jobs.schedule(db, "tg_unpost", p.id, now())
    from app.services import site_feed
    site_feed.clear_cache()


RESIDENTIAL = (None, "Apartment", "House", "Duplex", "Townhouse")


def channel_kind(p: Property) -> str:
    """Which channel the post goes to (notify.post_chat, David 11-23): rent of a flat / house, sale, commercial."""
    if is_sale(p):
        return "sale"
    return "rent" if (p.post_fields or {}).get("prop_type") in RESIDENTIAL else "commercial"


def rented_text(p: Property) -> str:
    """The channel's own «found a tenant» announcement (as the agency posts it by hand: ❗️#FlatRented, the district)."""
    tag = district_tag(p.district)
    name = districts.BY_TAG[tag[1:]][2] if tag[1:] in districts.BY_TAG else _human(tag[1:])
    if (p.post_fields or {}).get("prop_type") not in RESIDENTIAL:
        return f"❗️#Rented\nCommercial space in 📍{name} has found a business 🤝👏🎉\n{tag}"
    return f"❗️#FlatRented\nNice apartment in 📍{name} found a tenant 🤝👏🎉\n{tag}"


def rented_post(db: Session, p: Property) -> str:
    """Rented (David 11-23): one photo + rented_text() in the channel, once per rent of a flat that was in the channel.
    It stays there (unpost deletes only the object's tg publications). No commit."""
    pub = db.scalar(select(PropertyPublication).where(PropertyPublication.property_id == p.id, PropertyPublication.channel == "tg")
                    .order_by(PropertyPublication.id.desc()).limit(1))
    if pub is None:
        return "never posted"
    last = db.scalar(select(func.max(Event.at)).where(Event.entity == "property", Event.entity_id == str(p.id),
                                                      Event.action == "tg_rented_posted"))
    if last and (not p.published_at or aware(last) >= aware(p.published_at)):
        return "already"
    ref = _notify("rented_post", p.id, rented_text(p), (pub.messages or {}).get("chat"))  # the channel of its post
    if not ref:
        return "not sent"
    log_event(db, "property", p.id, "tg_rented_posted", None, post_url=ref[1])
    db.flush()
    return "ok"


def originals_text(db: Session, p: Property, text: str | None = None) -> str:
    """The originals chat's text: the post as in the channel (`text`: the caption it went out with, default post_text),
    then links to the post and the CRM card."""
    crm = notify.links.property_app(p.id)
    return "\n\n".join(x for x in (
        notify.post_html(text or post_text(db, p), p.id),
        p.post_url and f'🔗 <a href="{html_.escape(p.post_url)}">Пост в канале</a>',
        crm and f'🗂 <a href="{html_.escape(crm)}">Карточка в CRM #{p.id}</a>') if x)


def send_originals(db: Session, p: Property, text: str | None = None) -> str:
    """Job tg_originals: the photos and videos without the watermark + the post text into settings originals_chat_id
    (Irakli 11-23) — at the first publication and when the media changed since the last send; a repost with the same
    media sends nothing. text: the caption the post went out with. No commit."""
    chat = (settings_svc.get(db, "originals_chat_id") or "").strip()
    if not chat:
        return "no chat"
    keys = sorted(db.scalars(select(PropertyMedia.path).where(PropertyMedia.property_id == p.id,
                                                       PropertyMedia.kind.in_(("photo", "video")))))
    if not keys:
        return "no photos"
    sig = hashlib.sha1("\n".join(keys).encode()).hexdigest()
    last = db.scalar(select(Event.data).where(Event.entity == "property", Event.entity_id == str(p.id),
                                              Event.action == "tg_originals_sent").order_by(Event.id.desc()).limit(1))
    if last and last.get("sig") == sig and last.get("chat") == chat:
        return "same photos"
    n = _notify("originals_post", p.id, chat, originals_text(db, p, text))
    if not n:
        return "not sent"
    log_event(db, "property", p.id, "tg_originals_sent", None, sig=sig, chat=chat, files=n)
    db.flush()
    return "ok"


def hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"#\w+", text or "")))


def publish(db: Session, pid: int, actor: User, text: str | None = None, media_ids: list[int] | None = None,
            channel: str = "tg", comment: str | None = None, tg: bool = True, site: bool = True) -> Property:
    """B7: the operator publishes from the object card to the checked targets (Stanislav 11-15: one «Опубликовать», two
    checkboxes): «В Telegram» — the caption as edited (default post_text), the picked media in the picked order (default
    all), to a channel of PUBLICATION_CHANNELS; «На сайт» — hge.ge shows it (site_hidden off). The first publication
    without «На сайт» keeps it off the site; a later one without it leaves the site as it is. Publishing an object in
    review approves it. Without BOT_TOKEN in dev the post is a dry run (recorded without a link); a Telegram failure
    leaves it unpublished."""
    p = _prop(db, pid, lock=True)
    if actor is not None and not at_least(actor, "operator"):  # None: the worker's repost (job tg_repost)
        raise forbidden("only operators publish")
    if not (tg or site):
        raise DomainError("no_target", "Отметьте, куда публиковать: сайт или Telegram")
    if channel not in PUBLICATION_CHANNELS:
        raise DomainError("bad_channel", f"unknown channel {channel}")
    _require_status(p, "review", "published")
    _require_no_exclusive_conflict(db, p)
    require_min_photos(db, p)  # no override: a published object with fewer can't be republished until topped up
    if media_ids is not None:
        own = {m.id for m in media(db, p.id)}
        if not set(media_ids) <= own:
            raise DomainError("not_found", "media not found")
    first = p.status != "published"
    approved = p.status == "review"
    ref, dry_run = None, False
    if tg:
        auto = post_text(db, p)
        p.post_text = (text or "").strip() or auto  # transient attributes read by notify.publish_post
        # the rich post is built from the fields: a caption the operator rewrote by hand goes out as the plain album
        p.post_rich = (lambda ids: post_rich(db, p, ids)) if p.post_text == auto and not is_sale(p) else None
        p.post_media_ids = media_ids
        p.post_channel = channel_kind(p)
        p.progress_key = f"publish:{p.id}"  # the card polls GET /properties/{id}/publish/progress
        _legacy_pub(db, p)  # before post_url turns into the new post's link
        ref = _notify("publish_post", p)
        dry_run = ref is None and not env.BOT_TOKEN and env.APP_ENV == "dev"
        if ref is None and not dry_run:
            raise DomainError("publish_failed", "Telegram did not accept the post", 502)
        if ref:
            p.post_message_id, p.post_url = str(ref[0]), ref[1]
    if site:
        progress.step(f"publish:{p.id}", "site")
        p.site_hidden = False
    elif first:
        p.site_hidden = True
    p.published_at = now()
    p.verified_at = p.verified_at or p.published_at
    if p.status == "review" and actor is not None:
        p.review_comment, p.reviewed_by, p.reviewed_at = comment, actor.id, now()
        log_event(db, "property", p.id, "approved", actor, comment=comment)
    if tg:
        pub = record_publication(db, p, channel, p.post_url if ref else None, p.post_message_id if ref else None)
        if ref and len(ref) > 3:
            pub.messages = {"chat": str(ref[2]), "ids": ref[3], **({"warning": ref[4]} if len(ref) > 4 and ref[4] else {})}
        if ref:  # a repost: the old post leaves the channel (Stanislav 11-16)
            unpost(db, p, actor, keep=pub.id, action="tg_reposted")
        if actor is not None:  # a repost by hand: the pending automatic one is not needed
            jobs.cancel(db, "tg_repost", p.id)
        if ref:  # the same flat already in the channel? the operator decides (services/channel_dups.py, David 11-20)
            jobs.schedule(db, "channel_dups", p.id, now())
        if ref:  # the originals chat, once per photo set (send_originals)
            jobs.schedule(db, "tg_originals", p.id, now(), {"text": p.post_text})
    _status(db, p, "published", actor, post_url=p.post_url if ref else None, channel=channel if tg else None,
            site=not p.site_hidden, **({"dry_run": True} if dry_run else {}))
    schedule_dup_scan(db, p, "published")  # David 11-23: on publishing any object looks for its copies at once
    db.commit()
    from app.services import catalog, site_feed
    site_feed.clear_cache()
    if catalog.sync(db, p):  # clients find it in the bot at once
        db.commit()
    if tg:
        _notify("property_published", p)
    if first and actor is not None:  # David 11-23: одобрено / опубликовано → и оператору (the agent: property_published)
        head = ("✅ <b>Одобрен и опубликован</b>" if approved else "📢 <b>Опубликован</b>") + \
            (" (канал + сайт)" if tg and site else " (канал)" if tg else " (сайт)")
        _notify("prop_event", p, head, actor.id, [] if tg else None, True, comment)
    return p


def set_site(db: Session, pid: int, actor: User, on: bool) -> Property:
    """«Опубликовать на сайте» / «Снять с сайта»: a published object is on hge.ge unless hidden (site_feed.public_filter);
    the Telegram post (publish) is the other button. Operators only."""
    p = _prop(db, pid, lock=True)
    if not at_least(actor, "operator"):
        raise forbidden("only operators publish")
    if on and p.status != "published":
        raise DomainError("bad_status", "На сайт попадает опубликованный объект — сначала опубликуйте его в Telegram")
    p.site_hidden = not on
    log_event(db, "property", p.id, "site_on" if on else "site_off", actor)
    db.commit()
    from app.services import site_feed
    site_feed.clear_cache()
    return p


def unpost_channel(db: Session, pid: int, actor: User) -> Property:
    """«Снять только из канала» (Stanislav 11-23): deletes the object's Telegram posts now; the status, the site and the
    bot stay. «Выложить в канал» is publish(tg=True). Operators only."""
    p = _prop(db, pid, lock=True)
    if not at_least(actor, "operator"):
        raise forbidden("only operators publish")
    jobs.cancel(db, "tg_repost", p.id)
    res = unpost(db, p, actor, action="tg_removed")
    if res["failed"]:
        db.commit()
        raise DomainError("unpost_failed", "Telegram не удалил пост — попробуйте ещё раз", 502)
    db.commit()
    return p


def unpublish(db: Session, pid: int, actor: User) -> Property:
    """«Снять с публикации» (David 11-23, instead of «Снять с сайта»): off everywhere — the site, the bot's catalog and
    the channel (job tg_unpost) — the object waits in the archive (reason «unpublished») for «Опубликовать снова»
    (resume). Staff and the object's agent, no operator request."""
    p = _prop(db, pid, lock=True)
    _require_owner(p, actor)
    _require_status(p, "published")
    _archive_now(db, p, actor, "unpublished", action="unpublished")
    db.commit()
    from app.services import catalog
    if catalog.sync(db, p):
        db.commit()
    return p


BACK_TO_WORK = ("rented_by_owner", "not_actual", "idle_return")  # «Снова в работу» from the archive
RESUMABLE = ("paused", "unpublished")  # «Снять с паузы» / «Опубликовать снова»: back to published


def _republish(db: Session, p: Property, actor: User | None, action: str) -> None:
    """Back on the market as published (the site as it was) and posted again if it had a channel post. No commit."""
    p.archive_reason, p.archive_comment, p.archived_at = None, None, None
    jobs.cancel(db, "tg_unpost", p.id)
    _status(db, p, "published", actor, action)
    if db.scalar(select(PropertyPublication.id).where(PropertyPublication.property_id == p.id,
                                                      PropertyPublication.channel == "tg").limit(1)) or p.post_url:
        schedule_repost(db, p)


def resume(db: Session, pid: int, actor: User, republish: bool = False) -> Property:
    """«Снять с паузы» / «Опубликовать снова» (paused, unpublished): back to published, the post goes out again.
    «Снова в работу» («Собственник сдал», the lease ends, a rented object): back to «Взят» with its agent (an agent:
    him) to call the owner. republish (staff, David 11-23): a rented one is published again right away instead."""
    p = _prop(db, pid, lock=True)
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    owners_agent = o is not None and o.agent_id == actor.id
    if not owners_agent:
        _require_owner(p, actor)
    rented = p.status == "rented" or (p.status == "archived" and p.archive_reason in RENTED_REASONS)
    if republish and rented:
        if not at_least(actor, "operator"):
            raise forbidden("only operators publish")
        if not p.published_at:
            raise DomainError("bad_status", "Объект ещё не публиковался — сначала верните его в работу")
        _republish(db, p, actor, "republished")
    elif p.status == "rented" or (p.status == "archived" and p.archive_reason in BACK_TO_WORK):
        _back_to_work(db, p, actor, owners_agent)
        _notify("prop_event", p, "🔁 <b>Снова в работу</b>", actor.id)
        return p
    elif p.status != "archived" or p.archive_reason not in RESUMABLE:
        raise DomainError("bad_status", "Объект не на паузе")
    elif p.published_at:
        _republish(db, p, actor, "resumed")
    else:
        p.archive_reason, p.archive_comment, p.archived_at = None, None, None
        _status(db, p, "media_uploaded", actor, "resumed")
    db.commit()
    _notify("prop_event", p, "▶️ <b>Снова опубликован</b>" if p.status == "published" else "🔁 <b>Снова в работу</b>", actor.id)
    from app.services import catalog, site_feed
    site_feed.clear_cache()
    if catalog.sync(db, p):
        db.commit()
    return p


def _back_to_work(db: Session, p: Property, actor: User, owners_agent: bool) -> None:
    """«Снова в работу» / «Вернуть в работу» (a rented flat is free again): «Взят», the agent calls the owner."""
    # David 11-22 #8462: an old-base / dropped object whose ad came back («Дубль» on a new ad) goes back to work with the
    # owner's agent — the owner moved to him (transfer_owner leaves archived objects with the old agent)
    if owners_agent:
        p.agent_id = actor.id
    p.archive_reason, p.archive_comment, p.archived_at = None, None, None
    if not at_least(actor, "operator") or not p.agent_id:
        p.agent_id = actor.id
    p.taken_at, p.rent_from, p.rent_to = now(), None, None
    schedule_lease_reminders(db, p)  # cancels what's left
    _status(db, p, "taken", actor, "back_to_work")
    db.commit()


def mark_rented(db: Session, pid: int, actor: User | None) -> None:
    p = db.get(Property, pid)
    if p and p.status != "rented":
        p.archive_reason = "rented_by_us"
        _status(db, p, "rented", actor, reason="rented_by_us")
        _unpost_later(db, p)


def unmark_rented(db: Session, pid: int, actor: User | None) -> None:
    """Confirmed deal cancelled → the object is back on the market."""
    p = db.get(Property, pid)
    if p and p.status == "rented":
        p.archive_reason = None
        _status(db, p, "published", actor, "rent_cancelled")
        jobs.cancel(db, "tg_unpost", p.id)  # not deleted yet: the posts stay


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
    if not at_least(actor, "operator"):
        raise forbidden("only operators confirm a removed ad")
    p = _prop(db, pid, lock=True)
    link = db.scalar(select(PropertyListing).where(PropertyListing.property_id == p.id,
                                                   PropertyListing.listing_id == listing_id))
    copy = None if link else next((c for c in copies(db, p) if c["listing_id"] == listing_id), None)
    if link is None and copy is None:  # a copy found by the duplicate search (David 11-23): not linked, its event says so
        raise DomainError("not_found", "the ad is not linked to this object")
    if link:
        link.removed_checked_at, link.removed_checked_by = now(), actor.id
    source, url = (link.source, link.url) if link else (copy["where"], copy["url"])
    log_event(db, "property", p.id, "ad_removed_checked", actor, listing_id=listing_id, source=source, url=url)
    db.commit()
    _notify("prop_event", p, f"✅ <b>Проверено: объявление на {source or 'сайте'} снято</b>", actor.id, None, False, url)
    return p


# ---------------------------------------------------------------- «Где ещё выложен» (David 11-23: any object)

COPY_LEVELS = ("confirmed", "probable")  # similar.dup_level of an ad the duplicate search found: listed as a copy


def _who(l: Listing, competitors: set[str]) -> str | None:
    """Who posted the ad, as far as we know: a competitor's / agency channel or the bot's agent check → агент, an ad
    that passed the bot's owner check → собственник; a copy found on a site (myhome / ss / korter search) is unknown."""
    if l.source in competitors or l.source == "agency" or l.agent_suspect or (l.agent_reasons or "[]") != "[]":
        return "агент"
    return "собственник" if l.status in ("published", "pending_review") else None


def copies(db: Session, p: Property) -> list[dict]:
    """Where else the object is advertised: its linked outside ads (an exclusive's «висит на …», they block its
    publication) + the ads its last duplicate search (similar.scan_exclusive → event data «copies») calls confirmed /
    probable. Without ads gone from the feed, checked as removed by an operator or marked «Не дубль».
    -> [{listing_id, where, url, pct, level linked|confirmed|probable, who, asked {at, by}, blocking}], linked first."""
    from app.services import similar  # local: similar imports this module
    linked = external_ads(db, [p.id]).get(p.id, [])
    ev = db.scalars(select(Event).where(Event.entity == "property", Event.entity_id == str(p.id),
                                        Event.action == "duplicate_scan").order_by(Event.id.desc())).first()
    found = [c for c in ((ev.data or {}).get("copies") or [] if ev else []) if c["listing_id"] not in {a["listing_id"] for a in linked}]
    if not linked and not found:
        return []
    asked, removed = {}, set()
    for e_ in db.scalars(select(Event).where(Event.entity == "property", Event.entity_id == str(p.id),
                                             Event.action.in_(("ad_takedown_asked", "ad_removed_checked"))).order_by(Event.id)):
        lid = (e_.data or {}).get("listing_id")
        if e_.action == "ad_removed_checked":
            removed.add(lid)
        else:
            asked[lid] = {"at": e_.at.isoformat(), "by": e_.actor_id}
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_({a["by"] for a in asked.values() if a["by"]}))).all()) if asked else {}
    different = {k for k, d in similar._decisions(db, similar.key("p", p.id)).items() if d["verdict"] == "different"}
    ls = {l.id: l for l in db.scalars(select(Listing).where(Listing.id.in_([a["listing_id"] for a in linked + found])))}
    competitors = set(db.scalars(competitor_sources()))
    out = []
    for c, level in [(a, "linked") for a in linked] + [(c, c["level"]) for c in found]:
        l = ls.get(c["listing_id"])
        if l is None or (level != "linked" and (l.status == "gone" or l.id in removed or similar.key("l", l.id) in different)):
            continue
        a = asked.get(l.id)
        out.append({"listing_id": l.id, "where": similar._ad_source(l, competitors), "url": c.get("url") or (jload(l.urls, []) or [None])[0],
                    "pct": c.get("pct"), "level": level, "who": _who(l, competitors), "blocking": level == "linked" and p.is_exclusive,
                    "asked": {"at": a["at"], "by": names.get(a["by"])} if a else None})
    return out


def ad_takedown(db: Session, pid: int, listing_id: str, actor: User) -> Property:
    """«Попросить снять»: a task for the object's agent — contact whoever posted the ad (or the owner) to take it down;
    the operator checks it later («Проверено: снято»). A history event + a message to the agent."""
    p = _prop(db, pid)
    _require_owner(p, actor)
    c = next((x for x in copies(db, p) if x["listing_id"] == listing_id), None)
    if c is None:
        raise DomainError("not_found", "no such ad among the object's copies")
    log_event(db, "property", p.id, "ad_takedown_asked", actor, listing_id=listing_id, source=c["where"], url=c["url"])
    db.commit()
    who = "собственником" if c["who"] == "собственник" else "тем, кто выложил, или с собственником"
    _notify("prop_event", p, f"📣 <b>Попросить снять объявление на {html_.escape(c['where'])}</b>\nСвяжитесь с {who}; оператор потом "
            "проверит, что его нет", actor.id if p.agent_id != actor.id else None, None, True, c["url"])
    return p


# ---------------------------------------------------------------- dedup

def _phones_match(a: list[dict], b: list[dict]) -> bool:
    return any(phone_match(x["phone"], y["phone"]) for x in a for y in b)


def check_duplicate(db: Session, phone=None, address: str | None = None, url: str | None = None,
                    cadastral: str | None = None, exclude_property: int | None = None) -> dict:
    """Port of apps-script checkProperty (Owner_Base phone/address) + link and cadastral matches. phone: one number or
    a list; any of them against any of an owner's numbers.
    level: none | warn (one signal) | danger (phone and address, or same link/cadastral)."""
    results = []
    mine = [r for r in phone_rows(phone if isinstance(phone, list) else [phone]) if len(r["norm"]) > 4]
    if mine or (address and len(norm_address(address)) > 2):
        for o in db.scalars(select(Owner)):
            pm = bool(mine) and _phones_match(mine, owner_phones(o))
            am = bool(address) and address_match(address, o.address)
            if pm or am:
                results.append({"source": "owners", "owner_id": o.id, "owner": o.name, "phone": o.phone, "phones": owner_phones(o),
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
    return at_least(user, "operator") or p.agent_id == user.id


def can_pick_owner(db: Session, o: Owner, user: User) -> bool:
    """May this user take the owner from the owners list into a form (deal, object card)? The pick copies his phone,
    which GET /owners hides from an agent, so the same rule: staff; the owner bound to the agent (stage 6.5) or on one
    of his objects; or a free contact — bound to no other agent and on no other agent's object (old Owner_Base,
    «unlinked»: the link appears with the pick)."""
    if at_least(user, "operator") or o.agent_id == user.id:
        return True
    holders = set(db.scalars(select(Property.agent_id).where(Property.owner_id == o.id, Property.agent_id.isnot(None))))
    if user.id in holders:
        return True
    return not holders and (o.agent_id is None or o.is_agent)


def pick_owner(db: Session, oid, user: User) -> Owner:
    o = get_or_404(db, Owner, int(oid), "owner")
    if not can_pick_owner(db, o, user):
        raise DomainError("owner_taken", "Этот собственник закреплён за другим агентом", 403)
    return o


def can_open(db: Session, p: Property, user: User) -> bool:
    """The object card and its reads: staff; the object's agent; an agent whose lead / showing, deal or contract is on it
    (a client's ❤ lead assigned to him by an operator). Everyone else — other agents' and platform objects: closed
    (Stanislav 11-16; free objects are taken from the ad, «Беру в работу», not from the card)."""
    if can_see_owner(p, user):
        return True
    return db.scalar(select(Lead.id).where(Lead.property_id == p.id, Lead.agent_id == user.id).limit(1)) is not None \
        or db.scalar(select(Deal.id).where(Deal.property_id == p.id, or_(Deal.agent_id == user.id, Deal.recruit_id == user.id))
                     .limit(1)) is not None \
        or db.scalar(select(Contract.id).where(Contract.property_id == p.id, Contract.created_by == user.id).limit(1)) is not None


def get_visible(db: Session, pid: int, user: User) -> Property:
    """The object, or 404 «объект не найден» for an agent who can't open it (not 403: don't confirm it exists)."""
    p = _prop(db, pid)
    if not can_open(db, p, user):
        raise DomainError("not_found", "property not found")
    return p


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


def _photo_lists(db: Session, rows: list[Property], limit: int = 20) -> dict[int, list[str]]:
    """Object id → its photos to flip through (David 11-12: compare an ad with archive objects by photo): own ones,
    else the origin ad's."""
    own: dict[int, list[str]] = {}
    for pid, path in db.execute(select(PropertyMedia.property_id, PropertyMedia.path)
                                .where(PropertyMedia.property_id.in_([p.id for p in rows]), PropertyMedia.kind == "photo")
                                .order_by(PropertyMedia.position, PropertyMedia.id)):
        own.setdefault(pid, []).append(f"/api/media/{path}")
    lids = {p.listing_id for p in rows if p.listing_id and p.id not in own}
    n_ad = {lid: len(jload(ph, [])) for lid, ph in db.execute(select(Listing.id, Listing.photos).where(Listing.id.in_(lids)))} if lids else {}
    return {p.id: (own.get(p.id) or [f"/api/listing-photos/{p.listing_id}/{i}" for i in range(n_ad.get(p.listing_id, 0))])[:limit]
            for p in rows}


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
                                    "total_floors", "description", "is_exclusive", "site_hidden", "checklist", "ai_flags",
                                    "duplicate_of", "dup_check", "review_comment", "reviewed_by", "post_url",
                                    "post_message_id", "owner_id", "post_fields", "archive_reason", "archive_comment")}
    if d["checklist"]:
        d["checklist"] = {ROOM_ALIAS.get(k, k): v for k, v in d["checklist"].items()}
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
    d["locked"] = bool(p.agent_id) and p.agent_id != user.id and not at_least(user, "operator")
    d["locked_fields"] = list(LOCKED_AFTER_VERIFY) if is_locked_for(p, user) else []
    d["needs_check"] = needs_check(p)
    owner = get(Owner, p.owner_id)
    if owner:
        visible = can_see_owner(p, user)
        # stage 6.5: the owner's agent is the agent of all his objects (may differ from this object's after a staff override)
        d["owner"] = {"id": owner.id, "name": owner.name, "is_agent": owner.is_agent,
                      "phone": owner.phone if visible else None, "phones": owner_phones(owner) if visible else None,
                      "agent": ref(get(User, owner.agent_id))}
    else:
        d["owner"] = None
    k = ("conflict", p.id)
    if p.is_exclusive and k not in cache:
        cache[k] = external_ads(db, [p.id]).get(p.id, [])
    visible = can_see_owner(p, user)  # ad links lead to the owner's phone
    d["exclusive_conflict"] = [{**a, "url": a["url"] if visible else None} for a in cache.get(k, [])] if p.is_exclusive else []
    if full:
        d["copies"] = [{**c, "url": c["url"] if visible else None} for c in copies(db, p)]
    if full:
        d["media"] = [{"id": m.id, "kind": m.kind, "room": ROOM_ALIAS.get(m.room, m.room), "position": m.position, "url": f"/api/media/{m.path}",
                       "preview": f"/api/media/{m.path}?p=1"}
                      for m in media(db, p.id)]
        d["missing"] = missing_fields(p)  # what still blocks «Отправить оператору»
        d["room_picker"] = settings_svc.get(db, "room_picker") != "0"  # Управление → Настройки; rooms never required
        d["photo_rooms"] = [r.strip() for r in (settings_svc.get(db, "photo_rooms") or "").splitlines() if r.strip()]
        c = contract(db, p.id) if p.is_exclusive else None
        d["exclusive_contract"] = {"id": c.id, "url": f"/api/media/{c.path}", "created_at": c.created_at} if c else None
        w = no_esign(db, p.id) if p.is_exclusive and not c else None
        d["no_esign"] = _no_esign_out(db, w, can_see_owner(p, user)) if w else None
        if p.is_exclusive and not c and not (w and w.status == "approved"):
            d["missing"].append("эксклюзивный договор (PDF)")
        scan = dup_scan(db, p.id)
        if scan and not can_see_owner(p, user):  # ad links lead to the owner's phone
            scan["strong"] = [{**x, "url": None} for x in scan.get("strong") or []]
        d["duplicate_scan"] = scan
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
        d["idle_return_at"] = idle_return_at(db, p)
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


def _no_esign_out(db: Session, w, files: bool) -> dict:
    """The «Без электронной подписи» request on the card: status, reason, who asked / decided, the scan (private)."""
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_({w.requested_by, w.decided_by} - {None}))).all())
    scan = db.get(PropertyMedia, (w.payload or {}).get("media_id")) if (w.payload or {}).get("media_id") and files else None
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": w.id, "status": w.status, "reason": w.reason, "created_at": iso(w.created_at),
            "requested_by": {"id": w.requested_by, "name": names.get(w.requested_by)},
            "decided_by": {"id": w.decided_by, "name": names.get(w.decided_by)} if w.decided_by else None,
            "decided_at": iso(w.decided_at), "comment": w.decision_comment,
            "scan_url": f"/api/media/{scan.path}" if scan else None, "dups": _dups_out((w.payload or {}).get("dups"), files)}


def _dups_out(d: dict | None, files: bool) -> dict | None:
    """§12: the duplicates gate of the request (similar.no_esign_gate); ad links lead to the owner's phone."""
    if not d or files:
        return d
    return {**d, "items": [{**x, "url": None} for x in d.get("items") or []]}


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
                              "published_at": iso(x.published_at), "removed_at": iso(x.removed_at), "delete_error": x.delete_error,
                              "warning": (x.messages or {}).get("warning")} for x in pubs],
            "listings": ads, "deals": deals}


# «База объектов»: published (shot + operator approved), or rented after that; published_at keeps out an object that got
# «rented»/«published» through a deal without ever being published (mark_rented / unmark_rented)
BASE_STATUSES = ("published", "rented")
PLATFORM = "Платформа"  # an object with no agent (agent_id NULL, legacy imports) is the platform's (Stanislav 11-13)


def query(db: Session, user: User, status: str | None = None, agent_id: int | None = None, q: str | None = None,
          mine: bool = False, everyone: bool = False, base: bool = False, rented: bool = False,
          source: list[str] | None = None, district: list[str] | None = None, metro: str | None = None,
          bedrooms: str | None = None, price_min: int | None = None, price_max: int | None = None,
          archive_reason: str | None = None, deal: str | None = None, freeing: bool = False):
    """freeing: «Освобождаются» — the owner's own lease («Собственник сдал») has ended, the flat goes back to work.
    source / district: any of the values; metro: the post's tag (post_fields.metro); the base's filter block (11-13).
    deal: rent | sale — «Объекты» shows rent and sale apart (Stanislav 11-15); None = both."""
    stmt = select(Property)
    if deal:
        stmt = stmt.where(deal_cond(deal))
    if source := [x for x in source or [] if x]:
        stmt = stmt.where(Property.source_type.in_(source))
    if district := [x for x in district or [] if x]:
        stmt = stmt.where(Property.district.in_(district))
    if metro:
        stmt = stmt.where(Property.post_fields["metro"].astext == metro)
    if bedrooms:
        stmt = stmt.where(_beds(Property.bedrooms, bedrooms))
    # ponytail: price in the object's own currency (rent is all USD); convert if GEL objects show up in the base
    if price_min is not None:
        stmt = stmt.where(Property.price >= price_min)
    if price_max is not None:
        stmt = stmt.where(Property.price <= price_max)
    if archive_reason:
        stmt = stmt.where(Property.archive_reason == archive_reason)
    if rented:  # the calendar: objects with a hand-typed lease end
        stmt = stmt.where(Property.rent_to.isnot(None))
    if freeing:
        from app.services.daily_rent import today
        stmt = stmt.where(Property.status == "archived", Property.archive_reason == "rented_by_owner",
                          Property.rent_to <= today())
    if base:
        stmt = stmt.where(Property.status.in_(BASE_STATUSES), Property.published_at.isnot(None))
    # agents see only their own objects in every list, «База объектов» too (Stanislav 11-11); staff see all.
    # everyone: the deal form's picker only (deal_search), where another agent's object comes blocked, without data
    if mine or (not at_least(user, "operator") and not everyone):
        stmt = stmt.where(Property.agent_id == user.id)
    elif agent_id == 0:  # «Платформа»: objects with no agent belong to the platform (Stanislav 11-13)
        stmt = stmt.where(Property.agent_id.is_(None))
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
            cond = or_(cond, Property.owner_id.in_(select(Owner.id).where(owner_phone_like(pn))))
        stmt = stmt.where(cond)
    # the base by publication date, the archive by archiving date; work lists by the last change
    first = (Property.published_at if base else Property.archived_at if status == "archived" else Property.updated_at)
    return stmt.order_by(first.desc().nulls_last(), Property.id.desc())


def facets(db: Session, user: User, **scope) -> dict:
    """Filter options with counts over the tab's objects (same scope as query, no filters): sources, districts, metro."""
    sub = query(db, user, **scope).order_by(None).subquery()
    def facet(col):
        rows = db.execute(select(col, func.count()).where(col.isnot(None), col != "").group_by(col).order_by(func.count().desc()))
        return [{"value": v, "count": n} for v, n in rows]
    return {"sources": facet(sub.c.source_type), "districts": facet(sub.c.district), "metros": facet(sub.c.post_fields["metro"].astext)}


def status_counts(db: Session, deal: str | None = None) -> dict[str, int]:
    stmt = select(Property.status, func.count()).group_by(Property.status)
    return dict(db.execute(stmt.where(deal_cond(deal)) if deal else stmt).all())


def is_sale(p) -> bool:
    return (p.post_fields or {}).get("deal") == "Sale"


def deal_cond(deal: str):
    """A sale object is a property with post_fields.deal = Sale; everything else is rent."""
    sale = Property.post_fields["deal"].astext == "Sale"
    return sale if deal == "sale" else or_(Property.post_fields["deal"].astext.is_(None), ~sale)


# ---------------------------------------------------------------- deals (stage 7)

def needs_check(p: Property) -> bool:
    """«Новый объект из сделки — проверить»: typed in the deal form, not checked by an operator yet."""
    return p.source_type == "deal" and p.verified_at is None


def deal_block(db: Session, p: Property, user: User) -> str | None:
    """Why this agent can't file a deal on the object: it is another agent's (the owner's agent, stage 6.5, else the
    object's). Staff are never blocked."""
    if at_least(user, "operator"):
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
                    "blocked": blocked, "fields": None if blocked else deal_fields(db, p, user, deal_type),
                    "can_open": can_open(db, p, user)})  # the «Открыть» link: only a card he may open
        if pid and not blocked:  # «Оформить сделку» from the object card: its agent and the lead in work on it
            out[-1].update(agent_id=p.agent_id, lead_id=_deal_lead(db, p, user))
    return out


def _deal_lead(db: Session, p: Property, user: User) -> int | None:
    """The newest lead past the viewing on this object (the user's own unless staff), for the deal form's prefill."""
    from app.models import Lead
    from app.services.leads import DEAL_STAGES
    stmt = select(Lead.id).where(Lead.property_id == p.id, Lead.status.in_(DEAL_STAGES), Lead.archived_at.is_(None))
    if not at_least(user, "operator"):
        stmt = stmt.where(Lead.agent_id == user.id)
    return db.scalar(stmt.order_by(Lead.id.desc()).limit(1))


def verify(db: Session, pid: int, actor: User) -> Property:
    """The operator checked the object (stage 7: a new object from a deal; its deal can be confirmed then)."""
    if not at_least(actor, "operator"):
        raise forbidden("only operators verify objects")
    p = _prop(db, pid, lock=True)
    if p.verified_at is None:
        p.verified_at = now()
        log_event(db, "property", p.id, "verified", actor)
        db.commit()
    return p


def board(db: Session, user: User, per_column: int = 100, deal: str | None = None) -> dict[str, list[dict]]:
    columns = {}
    for st in BOARD:
        stmt = select(Property).where(Property.status == st, Property.board_hidden_at.is_(None))
        if deal:
            stmt = stmt.where(deal_cond(deal))
        if not at_least(user, "operator"):
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
    if not at_least(user, "operator"):
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
