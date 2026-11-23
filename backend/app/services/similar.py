"""«Похожие объекты» everywhere (Stanislav 11-17): one flat must never be worked twice — one agent takes an ss.ge ad
while Vika has already posted the same flat in the channel, the area / floor differ a little, the photos are the same.

For an ad or an object, every candidate across the whole base: our objects (any status, the archive too) and the live
outside ads (ss / myhome / livo / korter / Facebook / Telegram, competitors' too) not linked to an object yet (a
linked ad is its object). Each candidate gets a score in % from groups of independent evidence (score(), CONFIG; David 11-19): ≥ 85 %
«дубль / почти точно», 65–85 % «вероятный, нужна проверка» — only with something that names the flat (the building
or a pin, a phone / contact / full name, a photo, an identifier); params, price and district alone are never shown.
The agent decides «Дубль» / «Не дубль» / «Нужна проверка» (decide()); a pair called different is not shown again.

ponytail: every object and live ad profile cached per process (_all_objects / _ads: rebuilt only for what changed) and
only the candidates sharing an index key, a photo or a decision scored: warm ~0.25 s on 20k ads + 13k objects locally.
Each API worker keeps its own copy (~a few hundred MB at most); a shared cache if the base grows 10×.
"""
import hashlib
import json
import logging
import math
import re
import time
from datetime import timedelta

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.models import Listing, Owner, Property, PropertyListing, PropertyMedia, SimilarDecision, SimilarJournal, User
from app.services import jobs
from app.services import properties as P
from app.services.common import jload, log_event, now
from app.services.dedup import (_same_street, extract_phones, norm_phone, owner_phones, phone_match, relation, street_house,
                                 street_words)
from app.services.errors import DomainError

log = logging.getLogger(__name__)

PHOTO_BITS = 4       # dHash distance of «the same photo» (matching.PHOTO_BITS)
U64 = (1 << 64) - 1
AREA_TOL = 0.12      # «площадь почти та же»: 52 vs 57 m² still shows (11-17: the same flat, the area differs a little)
PRICE_TOL = 0.12
NEAR_M = 80          # pins this close: the same building, give or take the geocoder
TWIN_KM = 1.5        # pins this far apart are still «рядом» (weight only): ad pins can be a district off
# an exact point under this many ads is a geocoder's centroid (a district, a street, a metro: 387 ads on one Saburtalo
# point 11-21), not a building: «рядом на карте, 0 м» there said nothing (#15494 / #15491 at 80 %, other streets)
CROWD = 10
_crowded: dict = {"t": 0.0, "pts": frozenset()}
LIVE = ("published", "pending_review", "copy", "rejected")  # copy: a myhome copy found for an object (services/myhome_copies.py)
# rejected (11-23): competitors' tg posts (2,184 on prod, all «rejected» by the bot) and agents' FB ads are most often
# the agents' copies of our objects — candidates of an object's «Похожие» only (find), never of an ad's; never
# shown to clients (the bot's search reads «published»). ponytail: dHash only, no photo vectors (embeddings.LIVE_AD)
AD_ONLY_LIVE = ("published", "pending_review", "copy")  # an ad's own «Похожие»
CADASTRAL = re.compile(r"(?<![\d.])\d{2}\.\d{2}\.\d{2}\.\d{3}\.\d{3}(?:\.\d{2}\.\d{3})?(?![\d])")
# «похожие фото (другой ракурс)»: meanbest of the two photo sets (each photo's best cosine to the other set, averaged
# over both), SigLIP vectors in photo_vectors (app/embeddings.py). Measured 11-20 on 4,053 myhome Tbilisi rentals, 112
# hand-labelled pairs without a shared dHash photo: AUC 0.989; ≥ 0.875 no false pair (every edited copy, ~40% of the
# other shoots); 0.85–0.875 recall 0.92 on other shoots with ~5% look-alikes (flats of one new-build complex).
# ponytail: both thresholds rest on 13 different-shoot pairs of myhome photos: re-check them on our own photos
PHOTO_SIM_ALONE = 0.875   # a medium signal by itself
PHOTO_SIM_SURE = 0.95     # …even against another known address
PHOTO_SIM_WEIGHT = 0.85   # only weight on a candidate already shown by the params / house / pin
PHOTO_REASON = "похожие фото (другой ракурс)"
PHOTO_K, PHOTO_HIT = 100, 1 - PHOTO_SIM_WEIGHT  # nearest photos per subject photo; their cosine distance that counts
GEL_USD, EUR_USD = 0.37, 1.08  # rough: only for «цена близкая» (±12%)
SOURCE_NAME = {"ss": "ss.ge", "myhome": "myhome.ge", "livo": "livo.ge", "korter": "korter.ge", "facebook": "Facebook",
               "instagram": "Instagram", "threads": "Threads", "telegram": "Telegram", "agency": "канал агентства"}


def key(kind: str, ident) -> str:
    return f"{kind}:{ident}"


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _name_tokens(s) -> frozenset:
    """«Нино Беридзе» → {нино, беридзе}; short words and «собственник» / «owner» say nothing."""
    words = re.findall(r"[a-zа-яёა-ჰ]{3,}", str(s or "").lower())
    return frozenset(w for w in words if w not in {"собственник", "хозяин", "owner", "agent", "агент", "без", "посредников"})


def _bare_street(addr) -> frozenset:
    """The street of an address without a house number («Vake mckheta st. -»), else empty."""
    return street_words(addr) if street_house(addr)[1] is None else frozenset()


def _hashes(raw) -> list[int]:
    try:
        return [int(h) for h in (jload(raw, []) if isinstance(raw, str) or raw is None else raw) or []]
    except (TypeError, ValueError):
        return []


def _usd(price, currency) -> float | None:
    if not price:
        return None
    return float(price) * {"GEL": GEL_USD, "EUR": EUR_USD}.get(currency or "USD", 1.0)


FLAT = re.compile(r"(?:\bкв(?:артира)?|\bapt|\bapartment|\bflat|ბინა)\.?\s*(?:№|n|#|no\.?)?\s*(\d{1,4})(?![\d.,]*\s*(?:м|m|кв|sq))", re.I)
HANDLE = re.compile(r"(?:t\.me/|telegram\.me/|facebook\.com/|fb\.com/|instagram\.com/|(?<![\w.])@)([a-z0-9_.]{4,40})", re.I)
TYPES = {"apartment": "apartment", "house": "house", "land": "land", "commercial": "commercial"}  # Property.post_fields.prop_type,
# Listing.details.property (bot/facts.py): anything else (Retail, Cafe, Office…) is commercial; none known: unknown


def _flat(*texts) -> str | None:
    """«ул. Палиашвили 10, кв. 12» / «apt 12» / «ბინა 12» → "12" (the flat number; an area «кв. м» never counts)."""
    for t in texts:
        m = FLAT.search(str(t or ""))
        if m:
            return m.group(1).lstrip("0") or "0"
    return None


def _handles(*texts) -> frozenset:
    """Telegram / Facebook / Instagram contacts written in an ad: t.me/nino_flats, @nino_flats, facebook.com/nino.b."""
    return frozenset(h.lower().rstrip(".") for t in texts for h in HANDLE.findall(str(t or ""))
                     if not h.lower().startswith(("homes", "share", "profile.php", "groups", "joinchat")))


def _words(t) -> frozenset:
    """The words of a description (HTML, emoji, digits and short words out) for «похожий текст»."""
    return frozenset(re.findall(r"[a-zа-яёა-ჰ]{4,}", re.sub(r"<[^>]+>", " ", str(t or "")).lower()))


def _type(v) -> str | None:
    v = str(v or "").strip().lower()
    return TYPES.get(v, "commercial") if v else None


def _date(v):
    from datetime import datetime, timezone
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def building_key(addr: str | None) -> str | None:
    """One key per building whatever the script and spelling: «ул. Палиашвили, 10» / «10 Paliashvili St» /
    «ფალიაშვილის ქუჩა 10» → "paliashvil|10" (dedup.street_house skeletons; the corpus letter kept: 20A ≠ 20)."""
    street, n, letter = street_house(addr)
    return f"{'+'.join(sorted(street))}|{n}{letter}" if street and n is not None else None


def crowded(db: Session) -> frozenset:
    """The centroid points (CROWD), counted again once an hour. ponytail: one GROUP BY over all ads, fine at 100k rows."""
    if time.time() - _crowded["t"] > 3600:
        rows = db.execute(text("SELECT lat, lng FROM listings WHERE lat IS NOT NULL GROUP BY 1, 2 HAVING count(*) >= :n"),
                          {"n": CROWD}).all()
        _crowded.update(t=time.time(), pts=frozenset((float(a), float(b)) for a, b in rows))
    return _crowded["pts"]


def _km(a, b) -> float | None:
    if None in (a[0], a[1], b[0], b[1]):
        return None
    if (float(a[0]), float(a[1])) in _crowded["pts"] or (float(b[0]), float(b[1])) in _crowded["pts"]:
        return None  # a centroid: where the ad is, nobody knows
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- profiles

def _ad_source(l: Listing, competitors: set[str]) -> str:
    if l.source in competitors:
        return "конкурент · " + l.source.split(":", 1)[-1]
    return SOURCE_NAME.get(P.source_type_of(l), l.source)


def _listing_profile(l: Listing, competitors: set[str]) -> dict:
    t = l.text or ""
    urls = jload(l.urls, []) or []
    n_photos = len(jload(l.photos, []) or [])
    d = jload(l.details, {}) or {}
    d = d if isinstance(d, dict) else {}
    # 0: a photo the bot could not read (its dHash says nothing; two zeros are no shared photo)
    hp = [(h, f"/api/listing-photos/{l.id}/{i}") for i, h in enumerate(_hashes(l.photo_hashes)) if h]
    return {
        "key": key("l", l.id), "kind": "ad", "listing_id": l.id, "property_id": None,
        "urls": set(urls), "phones": extract_phones(t), "names": frozenset(), "author": (l.source, l.owner_id) if l.owner_id else None,
        "cads": {_digits(c) for c in CADASTRAL.findall(t)}, "hashes": [h for h, _ in hp], "hash_urls": [u for _, u in hp],
        "house": street_house(l.address), "street": _bare_street(l.address), "at": (l.lat, l.lng), "district": l.district,
        "floor": l.floor, "area": float(l.area) if l.area else None, "bedrooms": l.bedrooms,
        "price_usd": float(l.price_usd) if l.price_usd else None,
        "flat": _flat(l.address, t), "handles": _handles(t), "words": _words(t), "type": _type(d.get("property")),
        "metro": d.get("metro_ru"), "date": _date(l.first_seen),
        "out": {"kind": "ad", "listing_id": l.id, "property_id": None, "where": _ad_source(l, competitors),
                "competitor": l.source in competitors, "status": l.status, "address": l.address, "district": l.district,
                "bedrooms": l.bedrooms, "area": l.area, "floor": l.floor, "price_usd": l.price_usd,
                "photos": [f"/api/listing-photos/{l.id}/{i}" for i in range(n_photos)],  # all: an agent judges «тот же?» by them (Stanislav 11-18)
                "url": urls[0] if urls else None, "seen": l.last_seen},
    }


def _objects(db: Session, pids: list[int] | None = None) -> list[dict]:
    """Profiles of our objects: the owner's phone and name, every link and photo hash of the object and its ads."""
    q = select(Property) if pids is None else select(Property).where(Property.id.in_(pids))
    props = list(db.scalars(q))
    if not props:
        return []
    ids = [p.id for p in props]
    owners = {o.id: o for o in db.scalars(select(Owner).where(Owner.id.in_({p.owner_id for p in props if p.owner_id})))}
    agents = dict(db.execute(select(User.id, User.name).where(User.id.in_({p.agent_id for p in props if p.agent_id}))).all()) \
        if any(p.agent_id for p in props) else {}
    ads: dict[int, list] = {}
    for pid, url, l in db.execute(select(PropertyListing.property_id, PropertyListing.url, Listing)
                                  .join(Listing, Listing.id == PropertyListing.listing_id)
                                  .where(PropertyListing.property_id.in_(ids))):
        ads.setdefault(pid, []).append((url, l))
    photos: dict[int, list[str]] = {}
    own: dict[int, list[tuple]] = {}  # (property_media.dhash (matching.photo_hash), signed in the DB; its url)
    for pid, path, h in db.execute(select(PropertyMedia.property_id, PropertyMedia.path, PropertyMedia.dhash)
                                   .where(PropertyMedia.property_id.in_(ids), PropertyMedia.kind == "photo")
                                   .order_by(PropertyMedia.position, PropertyMedia.id)):
        photos.setdefault(pid, []).append(path)
        if h:  # 0: unreadable (matching.backfill_media_hashes)
            own.setdefault(pid, []).append((h & U64, f"/api/media/{path}"))
    out = []
    for p in props:
        o = owners.get(p.owner_id)
        mine = ads.get(p.id, [])
        origin = next((l for _, l in mine if l.id == p.listing_id), None)
        urls = {p.source_url} | {u for u, _ in mine} | {u for _, l in mine for u in jload(l.urls, []) or []}
        hp = own.get(p.id, []) + [(h, f"/api/listing-photos/{l.id}/{i}") for _, l in mine
                                  for i, h in enumerate(_hashes(l.photo_hashes)) if h]
        phones = [r["phone"] for r in owner_phones(o)]
        phones += [x for _, l in mine for x in extract_phones(l.text or "")]
        at = (p.lat, p.lng) if p.lat is not None else (origin.lat, origin.lng) if origin is not None else (None, None)
        # every photo (lazy previews, Lightbox on tap): with 6 the card showed the house and the terrace, not the flat (11-18)
        pics = [f"/api/media/{x}" for x in photos.get(p.id, [])] or \
            ([f"/api/listing-photos/{origin.id}/{i}" for i in range(len(jload(origin.photos, []) or []))] if origin else [])
        out.append({
            "key": key("p", p.id), "kind": "object", "listing_id": None, "property_id": p.id, "ad_ids": {l.id for _, l in mine},
            "urls": urls - {None, ""}, "phones": list(dict.fromkeys(phones)), "names": _name_tokens(o.name) if o else frozenset(),
            "author": None, "cads": {_digits(p.cadastral_code)} - {""}, "hashes": [h for h, _ in hp],
            "hash_urls": [u for _, u in hp],
            "house": street_house(p.address), "street": _bare_street(p.address), "at": at, "district": p.district, "floor": p.floor,
            "area": float(p.area) if p.area is not None else None, "bedrooms": p.bedrooms, "price_usd": _usd(p.price, p.currency),
            "agent_id": p.agent_id, "flat": _flat(p.address), "words": _words(p.description), "date": _date(p.created_at),
            "handles": _handles(o.link if o else None, *(l.text for _, l in mine)),
            "type": _type((p.post_fields or {}).get("prop_type")) or next((_type((jload(l.details, {}) or {}).get("property"))
                                                                          for _, l in mine if l.details), None),
            "metro": next(((jload(l.details, {}) or {}).get("metro_ru") for _, l in mine if l.details), None),
            "out": {"kind": "object", "listing_id": None, "property_id": p.id, "where": "наша база", "competitor": False,
                    "status": p.status, "address": p.address, "district": p.district, "bedrooms": p.bedrooms,
                    "area": float(p.area) if p.area is not None else None, "floor": p.floor,
                    "price_usd": round(_usd(p.price, p.currency)) if p.price else None,
                    "photos": pics, "url": p.post_url, "agent": agents.get(p.agent_id), "is_exclusive": p.is_exclusive,
                    "seen": p.updated_at.isoformat() if p.updated_at else None},
        })
    return out


# Building ~10k ad and ~10k object profiles and indexing them is most of a call (prod 11-20: 16–55 s a card): kept per
# process until something they are made of changes — the live ads (count / last update), the competitor list; for the
# objects the properties, their photos, links, owners, agents and any ad's text (cheap aggregates, the stamp).
_cache: dict = {"stamp": None, "ads": [], "idx": {}, "keys": {}}
_ocache: dict = {"stamp": None, "objs": [], "idx": {}, "keys": {}, "by_pid": {}}
CELL = 0.001  # pin grid, degrees: ≥ 83 m at Tbilisi's latitude, so pins ≤ NEAR_M apart are in neighbouring cells


def _phone_key(x: str) -> str:
    n = norm_phone(x)
    d = _digits(n)
    return d[-9:] if len(d) >= 9 else n  # phone_match: the last 9 digits, or the whole short number


def _cell(at) -> tuple | None:
    return (math.floor(at[0] / CELL), math.floor(at[1] / CELL)) if at and None not in (at[0], at[1]) else None


def _index_keys(p: dict, probe: bool = False) -> set:
    """What two profiles must share for score() to call the pair «identifying» (a link, a cadastral code, a phone, a
    contact, a word of the owner's name, the house number, a pin cell); probe: the subject's side (neighbour cells)."""
    ks = {("u", u) for u in p.get("urls") or ()} | {("c", c) for c in p.get("cads") or ()}
    ks |= {("h", h) for h in p.get("handles") or ()} | {("p", _phone_key(x)) for x in p.get("phones") or ()}
    if len(p.get("names") or ()) >= 2:
        ks |= {("n", t) for t in p["names"]}
    if p["house"][1] is not None:
        ks.add(("hn", p["house"][1]))
    c = _cell(p.get("at"))
    if c:
        ks |= {("g", c[0] + i, c[1] + j) for i in (-1, 0, 1) for j in (-1, 0, 1)} if probe else {("g", *c)}
    return ks


def _key_index(profiles: list[dict]) -> dict:
    idx: dict = {}
    for n, p in enumerate(profiles):
        for k in _index_keys(p):
            idx.setdefault(k, set()).add(n)
    return idx


def _ads(db: Session, competitors: set[str]) -> tuple[list[dict], dict, dict]:
    """Live outside ads (profiles, their photo index, their key index); the caller drops the ones linked since. On a
    change only the ads updated since are parsed again (the bot touches some every few minutes)."""
    live = (Listing.status.in_(LIVE), Listing.source.not_in(("object", "agency")))
    comp = frozenset(competitors)
    stamp = (*db.execute(select(func.count(), func.max(Listing.updated_at)).where(*live)).one(), comp)
    if stamp != _cache["stamp"]:
        old = _cache.get("by_id", {}) if _cache.get("comp") == comp else {}
        # the fields a profile is made of decide, not the time: every bot pass touches updated_at / last_seen of
        # every ad it sees, and the time has a second's resolution
        rows = sorted(db.execute(select(Listing.id, func.md5(func.concat_ws(
            "|", Listing.source, Listing.owner_id, Listing.status, Listing.urls, Listing.text,
            Listing.photos, Listing.photo_hashes, Listing.address, Listing.district, Listing.lat, Listing.lng,
            Listing.floor, Listing.area, Listing.bedrooms, Listing.price_usd, Listing.details, Listing.first_seen)))
            .where(*live)).all())
        fresh = [i for i, u in rows if old.get(i, (None,))[0] != u]
        got = {}
        for n in range(0, len(fresh), 2000):
            prints = dict(rows)
            got.update({l.id: (prints[l.id], _listing_profile(l, competitors))
                        for l in db.scalars(select(Listing).where(Listing.id.in_(fresh[n:n + 2000])))})
        by_id = {i: got.get(i) or old[i] for i, _ in rows if i in got or i in old}
        if got or by_id.keys() != old.keys():  # a bot pass that only touched the times changes nothing
            ads = [v[1] for v in by_id.values()]
            _cache.update(by_id=by_id, ads=ads, idx=_photo_index(ads), keys=_key_index(ads))
        _cache.update(stamp=stamp, comp=comp)
    return _cache["ads"], _cache["idx"], _cache["keys"]


# what an object's profile is made of, per object: compared when the cheap stamp changes, so only those are rebuilt
OBJ_PRINT = text(
    "SELECT p.id, md5(concat_ws('|', p.updated_at, o.name, o.phone, o.phones::text, o.link, u.name,"
    " (SELECT string_agg(m.id || ':' || coalesce(m.dhash, 0) || ':' || m.position || ':' || m.path, ',' ORDER BY m.id)"
    "  FROM property_media m WHERE m.property_id = p.id AND m.kind = 'photo'),"
    " (SELECT string_agg(pl.listing_id || ':' || coalesce(pl.url, '') || ':' || md5(concat_ws('|', l.text, l.urls, l.photos,"
    "  l.photo_hashes, l.details, l.lat, l.lng)), ',' ORDER BY pl.listing_id)"
    "  FROM property_listings pl JOIN listings l ON l.id = pl.listing_id WHERE pl.property_id = p.id)))"
    " FROM properties p LEFT JOIN owners o ON o.id = p.owner_id LEFT JOIN users u ON u.id = p.agent_id")


def _all_objects(db: Session) -> dict:
    """Every object's profile (an agent's number behind 3+ objects dropped, one on two marked «multi»), their photo and
    key indexes; cached until the stamp changes, then only the objects whose OBJ_PRINT changed are rebuilt."""
    stamp = tuple(db.execute(text(
        "SELECT (SELECT count(*) || '/' || coalesce(max(updated_at)::text, '') FROM properties),"
        " (SELECT count(*) || '/' || count(dhash) || '/' || coalesce(max(id), 0) || '/' || coalesce(sum(position::bigint * id), 0)"
        "  FROM property_media),"
        " (SELECT count(*) || '/' || coalesce(max(id), 0) || '/' || coalesce(sum(length(coalesce(url, ''))), 0) FROM property_listings),"
        " (SELECT md5(coalesce(string_agg(id || ':' || coalesce(name, '') || coalesce(phone, '') || coalesce(phones::text, '')"
        "  || coalesce(link, ''), ',' ORDER BY id), '')) FROM owners),"
        " (SELECT md5(coalesce(string_agg(id || ':' || coalesce(name, ''), ',' ORDER BY id), '')) FROM users),"
        " (SELECT coalesce(max(l.updated_at), '') FROM listings l JOIN property_listings pl ON pl.listing_id = l.id)")).one())
    if stamp != _ocache["stamp"]:
        prints = dict(db.execute(OBJ_PRINT).all())
        old = _ocache.get("prints", {})
        fresh = [i for i, h in prints.items() if old.get(i) != h or i not in _ocache["by_pid"]]
        got = {}
        for n in range(0, len(fresh), 2000):
            got.update({o["property_id"]: o for o in _objects(db, fresh[n:n + 2000])})
        by_pid = {i: got.get(i) or _ocache["by_pid"][i] for i in sorted(prints) if i in got or i in _ocache["by_pid"]}
        if not got and by_pid.keys() == _ocache["by_pid"].keys():
            _ocache["stamp"] = stamp  # the times moved, no object did
            return _ocache
        objs = list(by_pid.values())
        # an agent's number (or a landlord's with many flats): behind 3+ objects it names no flat (eval 11-17)
        seen: dict[str, set] = {}
        for o in objs:
            o.setdefault("phones_all", o["phones"])
            for x in o["phones_all"]:
                seen.setdefault(_digits(x)[-9:], set()).add(o["key"])
        many = {k for k, v in seen.items() if len(v) >= 3}
        two = {k for k, v in seen.items() if len(v) == 2}  # an owner with two flats: his phone alone names neither (§13.5)
        for o in objs:
            o["phones"] = [x for x in o["phones_all"] if _digits(x)[-9:] not in many]
            o["multi"] = {_digits(x)[-9:] for x in o["phones"]} & two
        _ocache.update(stamp=stamp, prints=prints, objs=objs, idx=_photo_index(objs), keys=_key_index(objs), by_pid=by_pid)
    return _ocache


# ---------------------------------------------------------------- photos

def _photo_index(cands: list[dict]) -> dict:
    """Pigeonhole: two hashes ≤ 4 bits apart share at least one of 5 equal 13-bit chunks."""
    idx: dict[tuple, set] = {}
    for n, c in enumerate(cands):
        for h in c["hashes"]:
            for i in range(5):
                idx.setdefault((i, (h >> (i * 13)) & 0x1FFF), set()).add(n)
    return idx


def _shared(a: list[int], b: list[int]) -> int:
    return sum(1 for x in a if any(bin(x ^ y).count("1") <= PHOTO_BITS for y in b))


def _shared_idx(a: list[int], b: list[int]) -> list[int]:
    """Which of b's photos are one of a's (the candidate's matched photos, for the strip)."""
    return [j for j, y in enumerate(b) if any(bin(x ^ y).count("1") <= PHOTO_BITS for x in a)]


# ---------------------------------------------------------------- the verdict

def _fits(s: dict, c: dict) -> bool:
    """What both know roughly agrees: bedrooms, floor ±1, area ±10% (properties.could_be_same on profiles)."""
    def known(k):
        return s[k] is not None and c[k] is not None
    return not ((known("bedrooms") and int(s["bedrooms"]) != int(c["bedrooms"]))
                or (known("floor") and abs(int(s["floor"]) - int(c["floor"])) > 1)
                or (s["area"] and c["area"] and abs(s["area"] - c["area"]) > max(1.0, 0.10 * max(s["area"], c["area"]))))


def _street(s: dict, c: dict) -> bool:
    """One address names no house, the other one is on its street (11-17: «Vake mckheta st. -» / 45 Mtskheta)."""
    return (bool(s.get("street")) and c["house"][1] is not None and _same_street(s["street"], c["house"][0])) or \
        (bool(c.get("street")) and s["house"][1] is not None and _same_street(c["street"], s["house"][0]))


# ---------------------------------------------------------------- the score (David 11-19, spec §4–8)

# Every weight and threshold of the duplicate score, in one place. Tune here, then `python -m app.dup_eval` prints
# precision / recall / false pairs of the labelled pairs (services/dup_pairs.json; --db: the operators' decisions).
CONFIG = {
    # groups of independent evidence (§6–7); a group both sides know nothing about is left out and the rest renormalised
    "weights": {"photo": 0.30, "contact": 0.25, "location": 0.20, "property": 0.15, "text_price": 0.05, "time": 0.05},
    # …over at least this share of the weights: a pair that only shares a house and a price is no sure pair
    "min_coverage": 0.45,
    "same": 0.85,          # «дубль / почти точно»
    "maybe": 0.65,         # «вероятный, нужна проверка»; below: not shown
    "strong_group": 0.7,   # a group this sure is one independent proof; «дубль» needs two (or a near-unique identifier)
    "photo_dhash": {1: 0.6, 2: 0.9, 3: 1.0},                 # shared photos (dHash ≤ PHOTO_BITS) → the photo group
    "photo_sim": [(PHOTO_SIM_ALONE, 0.7), (0.95, 0.9), (1.0, 1.0)],  # SigLIP meanbest → the photo group (below: none)
    "area_exact": (1.0, 0.03),   # m², share: 45 / 45.0 / 45.05, 70 / 72 are one area
    "area_fit": 0.10,            # _fits: the area may be typed this far off
    "area_zero": 0.25,           # the area / price sub-score falls to 0 at this difference
    "days": (14, 120),           # published this close: the time group is 1, at the second 0
    "pin_m": [(NEAR_M, 0.9), (300, 0.5), (TWIN_KM * 1000, 0.3)],
    "far_km": 3.0,               # pins this far apart contradict (a geocoder slip stays under it)
    # a street named without a house number (one ad of the pair) is weak: a street holds many buildings
    "location": {"house": 1.0, "corpus": 0.6, "street": 0.4, "bare_street": 0.3, "metro": 0.3, "district": 0.3},
    "text_jaccard": 0.6,         # word overlap of two descriptions that counts as the same text
    "bonus": {"phone_house_area": 0.10, "photos_address": 0.10, "angles_house_layout": 0.10,
              "owner_area_floor_dates": 0.05, "same_flat": 0.15, "full_name": 0.10, "author": 0.10,
              "photos_a_little": 0.05},
    "penalty": {"cadastre": 0.40, "flat": 0.30, "pins_far": 0.20, "area_20": 0.15, "area_30": 0.30, "type": 0.30,
                "no_fit": 0.15, "address": 0.20},
    # contradictions that forbid «дубль» whatever the rest says (§7, §13.12)
    "blocking": ("cadastre", "flat", "area_30", "type"),
    # hard rules (§8): the score is at least this when the rule holds and nothing blocking contradicts it
    "floors": {"identifier": 0.97, "phone_house": 0.88, "photos_place": 0.88, "photos_any": 0.80, "photo_sim": 0.70,
               "photo_one": 0.66, "phone": 0.66, "street_layout": 0.66},
}
GROUPS = ("identifier", "photo", "contact", "location", "property", "text_price", "time")
LEVEL_TEXT = {"same": "дубль / почти точно", "maybe": "вероятный, нужна проверка"}


def _interp(x: float, pts) -> float:
    if x < pts[0][0]:
        return 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _close(a: float, b: float, cfg=CONFIG) -> float:
    """1 for the same number (area 45 / 45.05, a price to 3%), falling to 0 at area_zero apart."""
    d, m = abs(a - b), max(a, b)
    if d <= max(cfg["area_exact"][0], cfg["area_exact"][1] * m):
        return 1.0
    return max(0.0, 1 - (d / m) / cfg["area_zero"])


def score(s: dict, c: dict, shared: int = 0, photo: float = 0.0, cfg: dict = CONFIG) -> dict:
    """The duplicate score of candidate c for subject s (profiles; shared: photos in common by dHash, photo: the
    meanbest of their photo vectors). -> {score 0..1, pct, level same|maybe|None, groups {group: G}, matched, differs,
    reasons (the matched ones, old wording), why, sure}. Missing data is never a mismatch: a group neither side knows is
    left out; only the contradictions of cfg["penalty"] take score away."""
    W, B, PEN = cfg["weights"], cfg["bonus"], cfg["penalty"]
    g: dict[str, float] = {}
    m: dict[str, list[str]] = {k: [] for k in GROUPS}
    differs: list[str] = []
    bonus, pen, floors = [], [], []

    def both(k):
        return s.get(k) is not None and c.get(k) is not None

    # identifiers: the same link / cadastral code is near-unique; different reliable codes forbid «дубль»
    if (s.get("urls") or set()) & (c.get("urls") or set()):
        m["identifier"].append("та же ссылка")
    cs, cc = s.get("cads") or set(), c.get("cads") or set()
    if cs & cc:
        m["identifier"].append("тот же кадастровый код")
    elif cs and cc and not any(x.startswith(y) or y.startswith(x) for x in cs for y in cc):  # a parcel's code ⊂ a flat's
        pen.append("cadastre"), differs.append("разные кадастровые коды")
    if m["identifier"]:
        floors.append("identifier")
        g["identifier"] = 1.0

    # photos: the same files (dHash) or the same rooms (SigLIP vectors; another shoot, a crop, a watermark)
    pd = cfg["photo_dhash"][min(shared, 3)] if shared else 0.0
    ps = _interp(photo, cfg["photo_sim"])
    if pd:
        m["photo"].append(f"общих фото: {shared}" if shared >= 2 else "одно общее фото")
    if ps > pd:
        m["photo"].append(PHOTO_REASON)
    elif not pd and photo >= PHOTO_SIM_WEIGHT:  # 0.85–0.875: look-alikes of one new-build complex too: weight only
        bonus.append("photos_a_little"), m["photo"].append(PHOTO_REASON)
    if pd or ps:
        g["photo"] = max(pd, ps)

    # contacts: the phone, a Telegram / Facebook contact. The owner's full name and the ad's author only add weight
    # (Irakli / Stanislav 11-19: an agent's name and account stand behind many flats)
    pairs = [(x, y) for x in s.get("phones") or [] for y in c.get("phones") or [] if phone_match(x, y)]
    multi = (s.get("multi") or set()) | (c.get("multi") or set())
    phone = bool(pairs)
    if phone:
        shared_phone = any(_digits(x)[-9:] in multi for p_ in pairs for x in p_)
        g["contact"] = 0.6 if shared_phone else 1.0
        m["contact"].append("тот же телефон собственника" + (" (он же у другого объекта)" if shared_phone else ""))
    hs = (s.get("handles") or frozenset()) & (c.get("handles") or frozenset())
    if hs:
        g["contact"] = max(g.get("contact", 0), 0.9)
        m["contact"].append("тот же контакт: " + ", ".join(sorted(hs)[:2]))
    same_name = len((s.get("names") or frozenset()) & (c.get("names") or frozenset())) >= 2
    same_author = bool(s.get("author")) and s.get("author") == c.get("author")

    # location: one building whatever the spelling (dedup.street_house), the pin, the metro, the district
    rel = relation(s["house"], c["house"])
    same_house = rel == "тот же дом"
    km = _km(s.get("at") or (None, None), c.get("at") or (None, None))
    near = km is not None and km * 1000 <= NEAR_M
    L, sub = cfg["location"], []
    addr_differs = False
    if rel:
        sub.append(L["house"] if same_house else L["corpus"] if rel.startswith("соседний") else L["street"])
        m["location"].append(rel)
    elif _street(s, c):
        sub.append(L["bare_street"]), m["location"].append("та же улица")
    elif s["house"][1] is not None and c["house"][1] is not None:
        sub.append(0.0), differs.append("другой адрес")
        addr_differs = True
    if km is not None:
        sub.append(next((y for x, y in cfg["pin_m"] if km * 1000 <= x), 0.0))
        if near and not same_house:
            m["location"].append(f"рядом на карте, {round(km * 1000)} м")
        if km > cfg["far_km"] and not same_house:
            pen.append("pins_far"), differs.append(f"далеко на карте, {km:.1f} км")
    loc_named = bool(sub and max(sub) > 0)
    if both("metro"):
        same = s["metro"] == c["metro"]
        sub.append(L["metro"] if same else 0.0)
        if same and not loc_named:
            m["location"].append(f"то же метро ({s['metro']})")
    if s.get("district") and c.get("district"):
        same = s["district"] == c["district"]
        sub.append(L["district"] if same else 0.0)
        if same and not loc_named:
            m["location"].append("тот же район")
        elif not same:
            differs.append(f"район {s['district']} / {c['district']}")
            addr_differs = True  # another district: another building as surely as another known house
    if sub:
        g["location"] = max(sub)
    same_place = same_house or near
    # two known houses on other streets and no pin nearby: another building. Photos «from another angle» alone don't
    # outweigh that (Stanislav 11-20: unfinished «black frame» flats look alike — 15430 / #10083 at 70 %)
    # The ad's address may be off though: rooms this alike (meanbest ≥ PHOTO_SIM_SURE) still count
    addr_differs = addr_differs and not near and not rel and photo < PHOTO_SIM_SURE
    if addr_differs:
        pen.append("address")

    # the flat itself: its number, the type, bedrooms, floor, area (45 / 45.0 / 45.05 m² are one)
    sub = []
    if both("flat") and (same_place or rel):
        if s["flat"] == c["flat"]:
            bonus.append("same_flat"), m["identifier"].append(f"та же квартира №{s['flat']}")
        else:
            pen.append("flat"), differs.append(f"квартиры №{s['flat']} / №{c['flat']}")
    if both("type"):
        if s["type"] == c["type"]:
            sub.append(1.0)
        else:
            sub.append(0.0), pen.append("type"), differs.append("другой тип объекта")
    if both("bedrooms"):
        if int(s["bedrooms"]) == int(c["bedrooms"]):
            sub.append(1.0), m["property"].append("спальни совпадают")
        else:
            sub.append(0.0), differs.append(f"спальни {s['bedrooms']} / {c['bedrooms']}")
    same_floor = both("floor") and int(s["floor"]) == int(c["floor"])
    if both("floor"):
        df = abs(int(s["floor"]) - int(c["floor"]))
        sub.append(1.0 if df == 0 else 0.5 if df == 1 else 0.0)
        if df == 0:
            m["property"].append("этаж совпадает")
        else:
            differs.append(f"этаж {s['floor']} / {c['floor']}")
    area_ok = True
    if s.get("area") and c.get("area"):
        a, b = s["area"], c["area"]
        x, off = _close(a, b, cfg), abs(a - b) / max(a, b)
        sub.append(x)
        area_ok = off <= cfg["area_fit"]
        if x == 1.0:
            m["property"].append("площадь та же" if abs(a - b) <= 1 else f"площадь близкая ({a:g} / {b:g} м²)")
        elif off <= AREA_TOL:
            m["property"].append(f"площадь близкая ({a:g} / {b:g} м²)")
        else:
            differs.append(f"площадь {a:g} / {b:g} м²")
            if off >= 0.30:
                pen.append("area_30")
            elif off >= 0.20:
                pen.append("area_20")
    if sub:
        g["property"] = sum(sub) / len(sub)
    fits = _fits(s, c)
    if sub and not fits:
        pen.append("no_fit")  # bedrooms, floor ±1, area ±10% disagree: another flat 548 times of 552 (prod eval 11-17)

    # the text and the price (a changed price is no mismatch by itself: §13.10), the dates
    sub = []
    price_close = False
    if s.get("price_usd") and c.get("price_usd"):
        a, b = s["price_usd"], c["price_usd"]
        price_close = abs(a - b) <= PRICE_TOL * max(a, b)
        sub.append(max(0.0, 1 - (abs(a - b) / max(a, b)) / cfg["area_zero"]))
        if price_close:
            m["text_price"].append("цена близкая")
        else:
            differs.append("цена другая")
    ws, wc = s.get("words") or frozenset(), c.get("words") or frozenset()
    if len(ws) >= 15 and len(wc) >= 15:
        j = len(ws & wc) / len(ws | wc)
        sub.append(min(1.0, j / cfg["text_jaccard"]))
        if j >= cfg["text_jaccard"] * 0.8:
            m["text_price"].append("похожий текст объявления")
    if sub:
        g["text_price"] = sum(sub) / len(sub)
    if s.get("date") and c.get("date"):
        days = abs((s["date"] - c["date"]).total_seconds()) / 86400
        d0, d1 = cfg["days"]
        g["time"] = 1.0 if days <= d0 else max(0.0, 1 - (days - d0) / (d1 - d0))

    # bonuses for strong combinations (§7)
    if same_name:
        bonus.append("full_name"), m["contact"].append("то же имя собственника")
    if same_author:
        bonus.append("author"), m["contact"].append("тот же автор объявления")
    if phone and same_place and area_ok:
        bonus.append("phone_house_area")
    if pd >= 0.9 and g.get("location", 0) >= 0.9:
        bonus.append("photos_address")
    if photo >= PHOTO_SIM_WEIGHT and same_place and both("bedrooms") and int(s["bedrooms"]) == int(c["bedrooms"]) \
            and s.get("area") and c.get("area") and area_ok:
        bonus.append("angles_house_layout")
    if (same_name or same_author) and s.get("area") and c.get("area") and _close(s["area"], c["area"], cfg) == 1.0 \
            and same_floor and g.get("time", 0) >= 0.5:
        bonus.append("owner_area_floor_dates")

    # hard rules (§8)
    loc_ok = "pins_far" not in pen and g.get("location", 0) >= cfg["location"]["district"]
    if phone and same_place and fits:
        floors.append("phone_house")
    if shared >= 2 and fits:
        floors.append("photos_place" if loc_ok else "photos_any" if "pins_far" not in pen else None)
    if photo >= PHOTO_SIM_ALONE and fits and not addr_differs:
        floors.append("photo_sim")
    if shared == 1 and fits:
        floors.append("photo_one")
    if phone and fits:
        floors.append("phone")
    # an ad the copies search found for this object (status «copy», services/myhome_copies.py) on the same street (no
    # house number), the same floor, area and bedrooms: an agent's re-shot copy (David 11-22, #15509: 3 myhome copies,
    # other photos) — «вероятный», never more. Not for the feed's ads: params alone stay below (Stanislav 11-20, #15488)
    street_layout = "copy" in (s.get("status"), c.get("status")) and bool(rel or "та же улица" in m["location"]) and same_floor and both("bedrooms") \
        and int(s["bedrooms"]) == int(c["bedrooms"]) and bool(s.get("area") and c.get("area")) \
        and _close(s["area"], c["area"], cfg) == 1.0 and fits and not addr_differs
    if street_layout:
        floors.append("street_layout")

    avail = sum(W[k] for k in g if k in W)
    raw = sum(W[k] * g[k] for k in g if k in W) / max(avail, cfg["min_coverage"]) if avail else 0.0
    sc = raw + sum(B[k] for k in set(bonus)) - sum(PEN[k] for k in set(pen))
    blocked = [k for k in pen if k in cfg["blocking"]]
    fl = cfg["floors"]
    if not blocked:
        sc = max([sc] + [fl[k] for k in floors if k])
    sc = min(1.0, max(0.0, sc))
    # «дубль» (§3, §8): a near-unique identifier, a hard rule, or two independent groups sure on their own — a photo by
    # its files only (SigLIP alone is never «почти точно»), the property never (one house has twin flats)
    strong = [k for k in ("contact", "location") if g.get(k, 0) >= cfg["strong_group"]] + (["photo"] if pd >= cfg["strong_group"] else [])
    same_flat = "same_flat" in bonus and same_place  # one building, one flat number: near-unique too
    sure = not blocked and (bool(m["identifier"] and "identifier" in floors) or same_flat or bool({"phone_house", "photos_place"} & set(floors))
                            or (len(strong) >= 2 and fits))
    note = None
    # «вероятный» needs something that names this flat (Stanislav 11-20: object 15488 listed 8 ads at 66–69 % by the
    # district, bedrooms and price alone): the building or a pin ≤ NEAR_M, a phone / contact / full name, a photo, an
    # identifier. Params, price, district, a street without a number and the dates only weigh such a candidate
    identifying = bool(m["identifier"] or pd or (photo >= PHOTO_SIM_WEIGHT and not addr_differs) or phone or hs or same_name or near
                       or (rel and not rel.startswith("рядом по улице")) or street_layout)
    if not identifying:
        sc = min(sc, cfg["maybe"] - 0.01)
    if sc >= cfg["same"] and not sure:
        sc = cfg["same"] - 0.01
        note = "нужна проверка: " + ("есть противоречие" if blocked else "доказательства только из одной группы")
    level = "same" if sc >= cfg["same"] else "maybe" if sc >= cfg["maybe"] else None
    matched = [r for k in GROUPS for r in m[k]]
    why = (f"{round(sc * 100)}%: " + (", ".join(matched) if matched else "совпадают только параметры")
           + (f"; отличается: {', '.join(differs)}" if differs else "") + (f" ({note})" if note else ""))
    return {"score": round(sc, 3), "pct": round(sc * 100), "level": level, "groups": {k: round(v, 2) for k, v in g.items()},
            "matched": matched, "differs": differs, "reasons": matched, "why": why, "sure": sure,
            "bonus": sorted(set(bonus)), "penalty": sorted(set(pen)), "rules": [k for k in floors if k]}


def compare(s: dict, c: dict, shared: int = 0, photo: float = 0.0) -> tuple[str | None, float, list[str]]:
    """(same | maybe | None, score 0..1, reasons) of candidate c for subject s: score() for the old callers."""
    r = score(s, c, shared, photo)
    return r["level"], r["score"], r["reasons"]


# ---------------------------------------------------------------- photo vectors

def _vectors(db: Session, pids=(), lids=()) -> dict[str, tuple]:
    """{key: (photo vectors (n, 768), their preview urls)}: an object's own photos (its ads' when it has none), an ad's."""
    import numpy as np
    acc: dict[str, tuple[list, list]] = {}
    if pids:
        for pid, path, v in db.execute(text(
                "SELECT m.property_id, m.path, CAST(v.vec AS real[]) FROM photo_vectors v JOIN property_media m ON m.id = v.media_id "
                "WHERE m.property_id = ANY(:p) AND v.vec IS NOT NULL ORDER BY m.position, m.id"), {"p": list(pids)}):
            a = acc.setdefault(key("p", pid), ([], []))
            a[0].append(v), a[1].append(f"/api/media/{path}")
        bare = [p for p in pids if key("p", p) not in acc]
        if bare:
            for pid, lid, n, v in db.execute(text(
                    "SELECT pl.property_id, v.listing_id, v.n, CAST(v.vec AS real[]) FROM photo_vectors v JOIN property_listings pl "
                    "ON pl.listing_id = v.listing_id WHERE pl.property_id = ANY(:p) AND v.vec IS NOT NULL ORDER BY v.listing_id, v.n"),
                    {"p": bare}):
                a = acc.setdefault(key("p", pid), ([], []))
                a[0].append(v), a[1].append(f"/api/listing-photos/{lid}/{n}")
    if lids:
        for lid, n, v in db.execute(text("SELECT listing_id, n, CAST(vec AS real[]) FROM photo_vectors WHERE listing_id = ANY(:l) "
                                         "AND vec IS NOT NULL ORDER BY listing_id, n"), {"l": list(lids)}):
            a = acc.setdefault(key("l", lid), ([], []))
            a[0].append(v), a[1].append(f"/api/listing-photos/{lid}/{n}")
    return {k: (np.asarray(vs, dtype=np.float32), urls) for k, (vs, urls) in acc.items()}


def _photo_sims(db: Session, s: dict, cands: list[dict]) -> dict[int, tuple[float, list[str]]]:
    """{candidate n: (meanbest, the candidate's photos alike, the likest first)} for the candidates worth it. Not the whole
    base: a pair with meanbest ≥ PHOTO_SIM_WEIGHT has a photo pair at least that close, so the nearest photos of each
    subject photo (the HNSW index) name the candidates, and only those are compared photo by photo."""
    sv = _vectors(db, [s["property_id"]] if s["kind"] == "object" else [], [s["listing_id"]] if s["kind"] == "ad" else [])
    if s["key"] not in sv:
        return {}
    A, _ = sv[s["key"]]
    db.execute(text(f"SET LOCAL hnsw.ef_search = {PHOTO_K}"))
    hits: set[str] = set()
    for q in A[:24]:  # ponytail: one index query per photo, ≤ 24; a batched LATERAL if it shows in the request log
        lit = "[" + ",".join(f"{x:.6f}" for x in q) + "]"
        for pid, lid, d in db.execute(text(
                "SELECT COALESCE(m.property_id, pl.property_id), n.listing_id, n.d FROM (SELECT media_id, listing_id, "
                "vec <=> CAST(:q AS vector) AS d FROM photo_vectors ORDER BY vec <=> CAST(:q AS vector) LIMIT :k) n "
                "LEFT JOIN property_media m ON m.id = n.media_id LEFT JOIN property_listings pl ON pl.listing_id = n.listing_id"),
                {"q": lit, "k": PHOTO_K}):
            if d is not None and d <= PHOTO_HIT:
                hits |= ({key("p", pid)} if pid else set()) | ({key("l", lid)} if lid else set())
    near = {n: c for n, c in enumerate(cands) if c["key"] in hits}
    if not near:
        return {}
    cv = _vectors(db, [c["property_id"] for c in near.values() if c["kind"] == "object"],
                  [c["listing_id"] for c in near.values() if c["kind"] == "ad"])
    out = {}
    for n, c in near.items():
        if c["key"] not in cv:
            continue
        B, urls = cv[c["key"]]
        m = A @ B.T
        best = m.max(0)
        alike = [urls[j] for j in best.argsort()[::-1] if best[j] >= PHOTO_SIM_WEIGHT]
        out[n] = (float(m.max(1).mean() + best.mean()) / 2, alike)
    return out


# ---------------------------------------------------------------- finding

def _decisions(db: Session, k: str) -> dict[str, dict]:
    out = {}
    for d in db.scalars(select(SimilarDecision).where(or_(SimilarDecision.a == k, SimilarDecision.b == k))):
        out[d.b if d.a == k else d.a] = {"verdict": d.verdict, "by": d.decided_by, "at": d.decided_at.isoformat()}
    return out


def find(db: Session, subject: str, user: User | None = None, limit: int = 30, include_ads: bool = True) -> list[dict]:
    """Candidates for «l:<listing id>» / «p:<object id>», the likeliest first: [{kind, property_id, listing_id, where,
    level same|maybe, reasons, decided, address, …, photos, url, can_open}]. A pair marked «Не дубль» is left out."""
    kind, _, ident = subject.partition(":")
    competitors = set(db.scalars(P.competitor_sources()))
    crowded(db)
    oc = _all_objects(db)
    objs = oc["objs"]
    if kind == "p":
        s = oc["by_pid"].get(int(ident)) if ident.isdigit() else None
        if s is None:
            raise DomainError("not_found", "object not found")
        skip = {s["key"]} | {key("l", x) for x in s["ad_ids"]}
    elif kind == "l":
        l = db.get(Listing, ident)
        if l is None:
            raise DomainError("not_found", "listing not found")
        s = _listing_profile(l, competitors)
        owner = db.scalar(select(PropertyListing.property_id).where(PropertyListing.listing_id == l.id)) \
            or db.scalar(select(Property.id).where(Property.listing_id == l.id))
        skip = {s["key"]} | ({key("p", owner)} if owner else set())
        if l.source == "object" and l.id.startswith("obj") and l.id[3:].isdigit():
            skip.add(key("p", int(l.id[3:])))
    else:
        raise DomainError("not_found", "bad subject")
    ads, ad_idx, ad_keys = _ads(db, competitors) if include_ads else ([], {}, {})
    linked = {key("l", x) for x in db.scalars(select(PropertyListing.listing_id))} if ads else set()
    # a linked ad is its object's: never listed on its own (the cache may predate the link)
    cands = [o for o in objs if o["key"] not in skip] + [a for a in ads if a["key"] not in skip and a["key"] not in linked
                                                       and (kind == "p" or a["out"]["status"] in AD_ONLY_LIVE)]
    # Only a candidate with an identifying signal (score(): `identifying`) can be shown, so only those are scored:
    # the ones sharing an index key, a photo (dHash pigeonhole, photo vectors) or a decision. The rest stay < 65 %.
    want: set[str] = set()
    for k in _index_keys(s, probe=True):
        want |= {objs[n]["key"] for n in oc["keys"].get(k, ())} | {ads[n]["key"] for n in ad_keys.get(k, ())}
    shared: dict[str, int] = {}
    if s["hashes"]:
        hits_o, hits_a = set(), set()
        for h in s["hashes"]:
            for i in range(5):
                k = (i, (h >> (i * 13)) & 0x1FFF)
                hits_o |= oc["idx"].get(k, set())
                hits_a |= ad_idx.get(k, set())
        for c in [objs[n] for n in hits_o] + [ads[n] for n in hits_a]:
            x = _shared(s["hashes"], c["hashes"])
            if x:
                shared[c["key"]] = x
    sims = {cands[n]["key"]: v for n, v in _photo_sims(db, s, cands).items()}
    decided = _decisions(db, s["key"])
    want |= set(shared) | set(sims) | set(decided)
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_({d["by"] for d in decided.values() if d["by"]}))).all()) if decided else {}
    out = []
    for c in cands:
        if c["key"] not in want:
            continue
        d = decided.get(c["key"])
        if d and d["verdict"] == "different":
            continue
        photo, alike = sims.get(c["key"], (0.0, []))
        n_shared = shared.get(c["key"], 0)
        r = score(s, c, n_shared, photo)
        level, why = r["level"], r["reasons"]
        if d and d["verdict"] == "same":
            level = "same"
            why = [f"отмечено как дубль{' · ' + names[d['by']] if names.get(d['by']) else ''}"] + why
        elif d and d["verdict"] == "review":
            level = level or "maybe"  # «Нужна проверка»: stays listed for the operator whatever the score says now
        if level is None:
            continue
        # the photos that matched lead the strip: the same files (dHash) first, then the alike ones (SigLIP)
        same_files = [c["hash_urls"][j] for j in _shared_idx(s["hashes"], c["hashes"])] if n_shared and c.get("hash_urls") else []
        lead = list(dict.fromkeys(same_files + (alike if PHOTO_REASON in why else [])))
        item = {**c["out"], "key": c["key"], "level": level, "level_text": LEVEL_TEXT[level], "score": r["score"],
                "pct": r["pct"], "reasons": why, "matched": r["matched"], "differs": r["differs"], "why": r["why"],
                "groups": r["groups"], "matched_photos": lead, "decided": d["verdict"] if d else None}
        if lead:
            item["photos"] = lead + [x for x in item["photos"] if x not in lead]
        if PHOTO_REASON in why and alike:  # the reason says the alike ones lead the strip
            item["reasons"] = [f"{x} — похожие первыми в ленте" if x == PHOTO_REASON else x for x in why]
        if c["kind"] == "object":
            # the card may be closed to him, its photos are not (api media_file, 11-17): he compares by them
            item["can_open"] = user is None or P.can_open(db, db.get(Property, c["property_id"]), user)
        out.append(item)
    out.sort(key=lambda x: (x["decided"] != "same", x["level"] != "same", x["kind"] != "object", -x["score"]))
    return out[:limit]


def objects_to_warn(db: Session, listing_id: str, user: User) -> list[dict]:
    """«Беру в работу»: our objects this ad may be (the agent is asked «это другой объект?» before a twin is made).
    None when the ad already is an object's (taking it takes that object, nothing new is made)."""
    if db.scalar(select(Property.id).where(Property.listing_id == listing_id)) is not None \
            or db.scalar(select(PropertyListing.property_id).where(PropertyListing.listing_id == listing_id)) is not None:
        return []
    return [x for x in find(db, key("l", listing_id), user, limit=10, include_ads=False) if x["kind"] == "object"]


VERDICTS = ("same", "different", "review")


def _profile(db: Session, k: str) -> dict | None:
    kind, _, ident = k.partition(":")
    if kind == "p" and ident.isdigit():
        return next(iter(_objects(db, [int(ident)])), None)
    l = db.get(Listing, ident) if kind == "l" else None
    return _listing_profile(l, set(db.scalars(P.competitor_sources()))) if l is not None else None


def _meanbest(A, B) -> float:
    m = A @ B.T
    return float(m.max(1).mean() + m.max(0).mean()) / 2


def score_pair(db: Session, a: str, b: str) -> dict | None:
    """score() of one pair «l:<ad>» / «p:<object>» from the base as it is now (photos: dHash and their vectors), for the
    decision journal and app/dup_eval.py. None when either is gone."""
    s, c = _profile(db, a), _profile(db, b)
    if s is None or c is None:
        return None
    vs = _vectors(db, [x["property_id"] for x in (s, c) if x["kind"] == "object"],
                  [x["listing_id"] for x in (s, c) if x["kind"] == "ad"])
    photo = _meanbest(vs[s["key"]][0], vs[c["key"]][0]) if s["key"] in vs and c["key"] in vs else 0.0
    return score(s, c, _shared(s["hashes"], c["hashes"]), photo)


def _journal(db: Session, x: str, y: str, verdict: str, prev: str | None, actor: User | None, r: dict | None) -> None:
    db.add(SimilarJournal(a=x, b=y, verdict=verdict, prev=prev, decided_by=actor.id if actor else None,
                          score=r["score"] if r else None, level=r["level"] if r else None,
                          features={k: r[k] for k in ("pct", "groups", "matched", "differs", "bonus", "penalty", "rules")} if r else {}))


def decide(db: Session, a: str, b: str, same: bool | None, actor: User, verdict: str | None = None) -> dict:
    """«Дубль» / «Не дубль» / «Нужна проверка» (verdict review: the pair stays listed, flagged for the operator) on a pair.
    An ad called the same as an object is linked to it (matching.attach); two objects or two ads stay as they are, the
    pair is marked (an operator archives the twin: «В архив → Дубль»). Every decision goes to the journal with the
    pair's score then; the same decision again changes nothing and sends nothing (§13.13)."""
    verdict = verdict or ("same" if same else "different")
    if verdict not in VERDICTS or a == b or not all(re.fullmatch(r"[lp]:[\w.\-:]+", x or "") for x in (a, b)):
        raise DomainError("bad_request", "bad pair")
    x, y = _pair(a, b)
    for k in (x, y):
        kind, _, ident = k.partition(":")
        if (db.get(Property, int(ident)) if kind == "p" and ident.isdigit() else db.get(Listing, ident) if kind == "l" else None) is None:
            raise DomainError("not_found", f"{k} not found")
    d = db.scalar(select(SimilarDecision).where(SimilarDecision.a == x, SimilarDecision.b == y).with_for_update())
    if d is not None and d.verdict == verdict:
        return {"ok": True, "verdict": verdict, "linked_to": None, "repeat": True}
    try:
        r = score_pair(db, x, y)
    except Exception:  # the operator's decision is kept whatever the scorer does
        log.exception("similar: score_pair %s %s", x, y)
        r = None
    prev = d.verdict if d is not None else None
    if d is None:
        d = SimilarDecision(a=x, b=y)
        db.add(d)
    d.verdict, d.decided_by, d.decided_at = verdict, actor.id, now()
    _journal(db, x, y, verdict, prev, actor, r)
    same = verdict == "same"
    linked = None
    if same and {x[0], y[0]} == {"l", "p"}:
        lk, pk = (x, y) if x.startswith("l") else (y, x)
        l, p = db.get(Listing, lk[2:]), db.get(Property, int(pk[2:]))
        if db.scalar(select(PropertyListing.property_id).where(PropertyListing.listing_id == l.id)) is None \
                and db.scalar(select(Property.id).where(Property.listing_id == l.id)) is None:
            from app.services import matching
            alerts = matching.attach(db, p, l, actor, ["отмечено как дубль"], "agent")
            linked = p.id
        else:
            alerts = []
        revived, owner_taken = _back_to_work(db, p, actor)
    else:
        alerts = []
        revived, owner_taken = None, None
    ev = {"same": "similar_same", "different": "similar_different", "review": "similar_review"}[verdict]
    for k, other in ((x, y), (y, x)):
        if k.startswith("p:"):
            log_event(db, "property", int(k[2:]), ev, actor, other=other, pct=r["pct"] if r else None)
    db.commit()
    if alerts:
        from app.services import matching
        matching._send(alerts)
    return {"ok": True, "verdict": d.verdict, "linked_to": linked, "revived": revived, "owner_taken": owner_taken}


def _back_to_work(db: Session, p: Property, actor: User) -> tuple[int | None, str | None]:
    """David 11-22 #8462: an agent calls a fresh ad «Дубль: это он» of an object dropped into the archive (the old base,
    «Неактуально», «Возвращён: нет действий») — the flat is for rent again, so the object comes back to work with him,
    unless its owner belongs to another agent (then he asks them / an operator: «Передать собственника»).
    -> (the object id if it came back, the owner's agent's tag if he is someone else's). No commit."""
    from app.models import Owner
    from app.services import properties as P
    from app.services import users as users_svc
    if p.status != "archived" or p.archive_reason not in P.BACK_TO_WORK or not P.is_field(actor):
        return None, None
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    if o is not None and o.agent_id not in (None, actor.id) and not o.is_agent:
        return None, users_svc.tag_of(db.get(User, o.agent_id))
    p.agent_id, p.taken_at, p.rent_from, p.rent_to = actor.id, now(), None, None
    p.archive_reason, p.archive_comment, p.archived_at = None, None, None
    if o is not None and o.agent_id is None and not o.is_agent:
        o.agent_id = actor.id
    P._status(db, p, "taken", actor, "back_to_work", via="дубль нового объявления")
    return p.id, None


def mark_different(db: Session, subject: str, others: list[str], actor: User) -> None:
    """«Это другой объект — беру»: every object shown to him is recorded as not this one (and journalled). No commit."""
    for o in others:
        x, y = _pair(subject, o)
        d = db.scalar(select(SimilarDecision).where(SimilarDecision.a == x, SimilarDecision.b == y))
        if d is None:
            db.add(SimilarDecision(a=x, b=y, verdict="different", decided_by=actor.id, decided_at=now()))
            _journal(db, x, y, "different", None, actor, None)


# ---------------------------------------------------------------- an exclusive's duplicates (Irakli 11-18)

# what makes a candidate worth a message to the agent and the operators: a shared photo, the owner's phone, the
# cadastral code, the same link
STRONG_HINTS = ("та же ссылка", "тот же кадастровый код", "общих фото", "одно общее фото", "тот же телефон собственника")


def _strong(reasons: list[str]) -> list[str]:
    return [r for r in reasons if r.startswith(STRONG_HINTS)]


def scan_exclusive(db: Session, pid: int, trigger: str = "exclusive") -> dict | None:
    """«При эксклюзиве — сразу поиск дублей везде»: find() over the whole base (any status) and every live outside ad,
    competitors' channels too. Stored as a «duplicate_scan» event (the card shows «Найдены возможные дубли: N»);
    candidates with a strong signal not seen by the previous scan go to the object's agent and the operators.
    A re-scan after new ads (trigger «new_ads») is logged only when the candidates changed. David 11-23: an object in
    work / published that is not an exclusive is searched too, shown on the card and in «Похожие» only (no alerts:
    there is no exclusive to defend). Commits."""
    p = db.get(Property, pid)
    if p is None or not (p.is_exclusive or p.status in P.DUP_LIVE):
        return None
    items = find(db, key("p", pid), None, limit=500)
    strong = [{"key": x["key"], "where": x["where"], "property_id": x["property_id"], "listing_id": x["listing_id"],
               "url": x["url"], "address": x["address"], "reasons": _strong(x["reasons"])[:3]}
              for x in items if _strong(x["reasons"]) and x["decided"] != "same"]
    prev = P.dup_scan(db, pid) or {}
    prev_keys = {s["key"] for s in prev.get("strong") or []}
    sig = hashlib.sha1(" ".join(sorted(x["key"] for x in items)).encode()).hexdigest()[:16]
    w = P.no_esign(db, pid)
    gate_open = w is not None and w.status == "pending" and not (w.payload or {}).get("dups")
    if trigger == "new_ads" and prev.get("sig") == sig and not gate_open:  # new photos change the reasons: always
        return None
    # «Где ещё выложен» (David 11-23, any object): the outside ads it is confirmed / probably the same as
    copies = [{"key": x["key"], "listing_id": x["listing_id"], "where": x["where"], "url": x["url"], "pct": x.get("pct"),
               "level": dup_level(x)} for x in items if x["kind"] == "ad" and dup_level(x) in P.COPY_LEVELS
              and P.source_type_of(Listing(id=x["listing_id"], source="")) in P.COPY_SITES][:50]
    log_event(db, "property", pid, "duplicate_scan", None, n=len(items), strong=strong[:20], trigger=trigger, sig=sig,
              copies=copies)
    gate = no_esign_gate(db, p, w, items, trigger == "dup_recheck")
    db.commit()
    if gate:
        P._notify("exclusive_dups_gate", p, *gate)
    fresh = [s for s in strong if s["key"] not in prev_keys]
    told = set()
    if fresh and not gate and p.is_exclusive:  # the gate's message lists them already
        P._notify("exclusive_duplicates", p, len(items), fresh)
        told = {s["key"] for s in fresh}
    # new copies → one message to the object's agent; a scan before 11-23 stored none: it is the baseline, not news
    if not prev or "copies" in prev:
        was = {c["key"] for c in prev.get("copies") or []} | told
        if new := [c for c in copies if c["key"] not in was]:
            P._notify("object_copies", p, new)
    return {"n": len(items), "strong": strong, "fresh": fresh, "gate": gate[0] if gate else None}


# ---------------------------------------------------------------- «эксклюзив без электронной подписи» (David 11-19, §12)

DUPS_WAIT = timedelta(hours=24)  # to get the owner's signed exclusive contract and the other ads taken down
BLOCKING = ("confirmed", "probable")


def dup_level(item: dict) -> str:
    """confirmed | probable | doubtful of a find() candidate by its score (item["pct"]): ≥ CONFIG same (85 %) confirmed,
    ≥ CONFIG maybe (65 %) probable, below doubtful (a pair kept listed by «Нужна проверка»); a pair marked «Дубль» is
    confirmed. An item without a pct (an older caller) goes by its level."""
    if item.get("decided") == "same":
        return "confirmed"
    pct = item.get("pct")
    if pct is None:
        return "confirmed" if item.get("level") in ("same", "confirmed") else "probable" if item.get("level") in ("maybe", "probable") else "doubtful"
    return "confirmed" if pct >= CONFIG["same"] * 100 else "probable" if pct >= CONFIG["maybe"] * 100 else "doubtful"


def _dup_item(x: dict) -> dict:
    return {"key": x["key"], "where": x["where"], "url": x["url"], "property_id": x["property_id"],
            "listing_id": x["listing_id"], "address": x["address"], "status": x.get("status"), "level": dup_level(x),
            "pct": x.get("pct"), "reasons": (x.get("reasons") or [])[:5]}


def no_esign_gate(db: Session, p: Property, w, items: list[dict], recheck: bool) -> tuple | None:
    """§12 on a pending «Без электронной подписи» request (state in its payload["dups"]), after a scan of the object:
    - first scan, nothing found → approved by the system («эксклюзив подтверждён: дублей нет»);
    - confirmed / probable duplicates → «ожидается устранение дублей»: 24 h (a dup_recheck job) for the signed contract
      and the other ads taken down — the system never removes an outside ad;
    - only doubtful ones → «ручная проверка»: the operator decides, nothing is blocked;
    - the re-check at the deadline: the contract is in (a scan on the request / the PDF) and nothing found → approved,
      anything else → the operator decides; never approved with duplicates left.
    Later scans (new ads, photos) leave a decided state alone. -> (state, items, deadline) to message, or None. No commit."""
    if w is None or w.status != "pending":
        return None
    pl = dict(w.payload or {})
    st = (pl.get("dups") or {}).get("state")
    if (st != "waiting") if recheck else bool(st):
        return None
    found = [_dup_item(x) for x in items]
    received = bool(pl.get("media_id")) or P.contract(db, p.id) is not None
    deadline = None
    if not found and (received or not recheck):
        state = "clear"
        w.status, w.decided_at, w.decision_comment = "approved", now(), "Эксклюзив подтверждён: дублей нет"
        log_event(db, "property", p.id, "change_request_approved", None, request_id=w.id, kind=w.kind, auto=True,
                  comment=w.decision_comment)
        log_event(db, "property", p.id, "exclusive_contract_waived", None, request_id=w.id, reason=w.reason,
                  requested_by=w.requested_by, media_id=pl.get("media_id"), auto=True)
    elif recheck:
        state = "operator"
    elif any(x["level"] in BLOCKING for x in found):
        state, deadline = "waiting", now() + DUPS_WAIT
        jobs.schedule(db, "dup_recheck", p.id, deadline, {"request_id": w.id})
    else:
        state = "review"
    dl = deadline.isoformat() if deadline else (pl.get("dups") or {}).get("deadline")
    pl["dups"] = {"state": state, "at": now().isoformat(), "deadline": dl, "received": received, "items": found[:30],
                  "n": len(found)}
    w.payload = pl
    log_event(db, "property", p.id, f"exclusive_dups_{state}", None, request_id=w.id, n=len(found), deadline=dl,
              items=found[:20])
    return state, found, dl


def exclusives_hit(db: Session, listing_ids: list[str]) -> set[int]:
    """New ads (bot sync → matching.process) that look like one of our live exclusives → those objects, to re-scan.
    Cheap on purpose: only the exclusives' profiles against the new ads, photos through the index."""
    ex = list(db.scalars(select(Property.id).where(Property.is_exclusive.is_(True), Property.status != "archived")))
    if not ex or not listing_ids:
        return set()
    competitors = set(db.scalars(P.competitor_sources()))
    ads = [_listing_profile(l, competitors) for l in db.scalars(
        select(Listing).where(Listing.id.in_(listing_ids), Listing.source.not_in(("object", "agency"))))]
    if not ads:
        return set()
    idx, hit = _photo_index(ads), set()
    for o in _objects(db, ex):
        near = {n for h in o["hashes"] for i in range(5) for n in idx.get((i, (h >> (i * 13)) & 0x1FFF), ())}
        for n, a in enumerate(ads):
            if a["key"] in {key("l", x) for x in o["ad_ids"]}:
                continue
            if compare(o, a, _shared(o["hashes"], a["hashes"]) if n in near else 0)[0]:
                hit.add(o["property_id"])
                break
    return hit
