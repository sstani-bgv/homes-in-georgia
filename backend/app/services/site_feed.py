"""Public read-only feed for the agency site hge.ge (served by app/api/site.py, no login).

Two sources, one item shape (the site's own listings.json item: empty values are left out, like the old builder did):
properties, rent and sale alike (id "o<id>"; a sale object copied from the old sale_objects table still answers to its
"s<id>", post_fields.offer_ident), and cars (cars, "c<id>"). STRICT whitelist:
an item is built key by key from the public fields; nothing is copied wholesale (no owner / tenant / lease / cadastral /
checklist / ai_flags / archive / review fields, no private media). Public = published AND at least one photo."""
import re
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import Car, Listing, Property, PropertyMedia, User
from app.services import properties as P

NBG_URL = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/?currencies=USD&currencies=EUR"
FALLBACK_RATES = {"USD": 1.0, "GEL": round(1 / 2.7, 6), "EUR": 1.08}
RATES_TTL, FEED_TTL = 12 * 3600, 60
DEFAULT_AGENT = {"telegram": "David_Tibelashvili", "phone": "+995 599 20 67 16"}
PHOTO_URL = "/api/public/site/photos/{id}/{n}"
VIDEO_URL = "/api/public/site/video/{id}"
EPOCH = datetime.min.replace(tzinfo=timezone.utc)

# CRM post_fields tag <-> the site's word (rent and sale objects keep the generator's tags; cars store the words)
KIND = {"apartment": "Apartment", "house": "House", "commercial": "Commercial", "land": "Land"}
BUILDING = {"new": "NewBuilding", "old": "OldBuilding", "historical": "HistoricalBuilding", "reconstruction": "Reconstruction"}
HEATING = {"central": "CentralHeating", "gas": "GasHeating", "electric": "ElectricalHeating", "ac": "ACHeating",
           "fireplace": "Fireplace"}
PETS = {"allowed": "Allowed", "no": "NotAllowed", "agreement": "ByAgreement"}
FRAME = {"white_frame": "WhiteFrame", "green_frame": "GreenFrame", "black_frame": "BlackFrame"}
DESIGN = {"renovated": "New", "mixed": "Mixed", "old": "Old"}
AMENITY = {"smart_tv": "SmartTV", "balcony": "Balcony", "terrace": "Terrace", "courtyard": "Courtyard", "wifi": "WiFi", "tv": "TV",
           "dishwasher": "Dishwasher", "stove": "Stove", "microwave": "Microwave", "washer": "WashingMachine",
           "vacuum": "VacuumCleaner", "fireplace": "Fireplace", "elevator": "Elevator", "oven": "Oven",
           "parking": "ParkingPlace", "conditioner": "Conditioner", "bath": "Bath", "shower": "Shower"}
METRO = {"Liberty Square": "LibertySquare", "Rustaveli": "Rustaveli", "Marjanishvili": "Marjanishvili",
         "Station Square": "StationSquare", "Tsereteli": "Tsereteli", "Gotsiridze": "Gotsiridze",
         "Nadzaladevi": "Nadzaladevi", "Didube": "Didube", "Grmagele": "Grmagele", "Guramishvili": "Guramishvili",
         "Sarajishvili": "Sarajishvili", "Akhmeteli Theatre": "AhmeteliTheatre", "Technical University": "TCUniversity",
         "Vazha-Pshavela": "VazhaPshavela", "Delisi": "Delisi", "State University": "STUniversity",
         "Medical University": "MCUniversity", "Avlabari": "AvlabariMetro", "Isani": "IsaniMetro",
         "300 Aragveli": "300Aragveli", "Samgori": "SamgoriMetro", "Varketili": "Varketili"}


def inv(d: dict) -> dict:
    return {v: k for k, v in d.items()}


def _clear(v) -> bool:
    return v in (None, "", [], False)  # the old builder's rule (0 is dropped too)


# ---------------------------------------------------------------- rates (NBG, 12 h)

_rates = {"at": 0.0, "v": dict(FALLBACK_RATES)}


def parse_nbg(payload) -> dict:
    day = payload[0] if isinstance(payload, list) else payload
    cur = day.get("currencies") if isinstance(day, dict) and "currencies" in day else payload
    gel_per = {c["code"]: float(c["rate"]) / float(c.get("quantity") or 1) for c in cur}
    usd = gel_per["USD"]
    return {"USD": 1.0, "GEL": round(1 / usd, 6), "EUR": round(gel_per["EUR"] / usd, 6)}


def rates() -> dict:
    if time.time() - _rates["at"] > RATES_TTL:
        _rates["at"] = time.time()  # also after a failure: no hammering the bank, the old / fallback rates stay
        try:
            # without &date= the bank answers [] on some days (14.11.2026, a Saturday); with today's date it never does
            day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))  # Tbilisi date
            r = httpx.get(f"{NBG_URL}&date={day}", timeout=8, headers={"User-Agent": "HomesInGeorgiaSite/1.0"})
            r.raise_for_status()
            _rates["v"] = parse_nbg(r.json())
        except Exception:  # network or format problem
            pass
    return _rates["v"]


# ---------------------------------------------------------------- items

def _agent(u: User | None) -> dict:
    """The site's «Написать» / «Позвонить»: always the agency's contact (Irakli 11-22: every lead to +995 599 20 67 16),
    never the object's agent."""
    return dict(DEFAULT_AGENT)


def _usd(price, currency, fx) -> int | None:
    return round(price * fx.get(currency or "USD", 1)) if price else None


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _final(d: dict) -> dict:
    return {k: v for k, v in d.items() if not _clear(v)}


def tag_words(f: dict) -> dict:
    """post_fields (the generator's tags, rent and sale objects alike) -> the site's words; post_fields.site_extra keeps
    the words of imported objects that have no tag."""
    extra = f.get("site_extra") or {}
    cond = next((k for k, v in FRAME.items() if v == f.get("condition")), None) or \
        ("renovated" if f.get("condition") == "FullyRenovated" else None) or \
        inv(DESIGN).get(f.get("design"), "old" if f.get("design") else None) or extra.get("condition")
    t = f.get("prop_type")
    return {
        "kind": inv(KIND).get(t, "apartment" if not t else "house" if t in ("Duplex", "Townhouse") else "commercial"),
        "type": t.lower() if t and t not in KIND.values() else None,  # cafe / retail / duplex …: the exact type
        "headline": f.get("headline"), "metro": inv(METRO).get(f.get("metro")) or extra.get("metro"), "approx": f.get("approx"),
        "building": inv(BUILDING).get(f.get("building")) or extra.get("building"), "condition": cond,
        "heating": inv(HEATING).get(f.get("heating")) or extra.get("heating"),
        "bathrooms": int(f["bathrooms"][0]) if f.get("bathrooms") else None,
        "amenities": [inv(AMENITY).get(a, a.lower()) for a in f.get("amenities") or []] + (extra.get("amenities") or []),
    }


def pins(db: Session, props: list) -> dict:
    """{object id: (lat, lng)} of the origin ad for objects without their own point (David 11-24: «On the map» showed an
    empty map for #15512 — the post's Google link had the ad's pin, the site had none)."""
    want = {p.listing_id: p.id for p in props if p.lat is None and p.listing_id}
    if not want:
        return {}
    rows = db.execute(select(Listing.id, Listing.lat, Listing.lng).where(Listing.id.in_(want), Listing.lat.is_not(None),
                                                                       Listing.lng.is_not(None)))
    return {want[lid]: (float(lat), float(lng)) for lid, lat, lng in rows}


def item(p: Property, n_photos: int, agent: User | None, fx: dict, pin: tuple | None = None) -> dict:
    """A property: rent (rent, daily, commercial for rent) or sale (post_fields.deal = Sale: resale, new, commercial)."""
    f = p.post_fields or {}
    sale = f.get("deal") == "Sale"
    service = f.get("service") or ("sale" if sale else "rent")
    price = float(f.get("sale_price") or p.price or 0) or None if sale else float(p.price) if p.price is not None else None
    usd, area = _usd(price, p.currency, fx), float(p.area) if p.area is not None else None
    months = f.get("months") or [int(x[:-5]) for x in f.get("periods") or [] if x.endswith("Month")]
    iid = f"o{p.id}"
    return _final({
        "id": iid, "legacy_id": f.get("site_id"), "service": service, "source": service, "deal": "sale" if sale else "rent",
        "period": f.get("period"), **tag_words(f),
        "title": p.address, "district": p.district, "lat": p.lat if p.lat is not None else pin and pin[0],
        "lon": p.lng if p.lng is not None else pin and pin[1],
        "price": price, "currency": p.currency, "price_usd": usd, "deposit": None if sale else f.get("deposit"),
        "ppsqm_usd": round(usd / area) if sale and usd and area else None, "completion": f.get("completion"),
        "rooms": p.bedrooms, "area": area, "floor": p.floor, "floors": p.total_floors,
        "pets": inv(PETS).get(f.get("pets")), "months": months, "tenants": f.get("tenants"),
        "exclusive": p.is_exclusive, "description": p.description,
        "agent": _agent(agent), "photos": [PHOTO_URL.format(id=iid, n=n) for n in range(n_photos)],
        "post_url": p.post_url, "published_at": _iso(p.published_at), "updated_at": _iso(p.updated_at),
    })


def car_item(c: Car, agent: User | None, fx: dict) -> dict:
    iid, price = f"c{c.id}", float(c.price) if c.price is not None else None
    service = f"car-{c.deal}"
    return _final({
        "id": iid, "legacy_id": c.site_id, "service": service, "source": service, "deal": c.deal, "period": c.period,
        "kind": "car", "title": c.title, "lat": c.lat, "lon": c.lng, "price": price, "currency": c.currency,
        "price_usd": _usd(price, c.currency, fx), "deposit": float(c.deposit) if c.deposit is not None else None,
        "year": c.year, "gearbox": c.gearbox, "fuel": c.fuel, "body": c.body, "seats": c.seats, "mileage": c.mileage,
        "pickup": c.pickup, "exclusive": c.is_exclusive, "description": c.description, "agent": _agent(agent),
        "photos": [PHOTO_URL.format(id=iid, n=n) for n in range(len(c.photos))],
        "post_url": c.post_url, "published_at": _iso(c.published_at), "updated_at": _iso(c.updated_at),
    })


def photo_keys(db: Session, pid: int) -> list[str]:
    """Storage keys of a rent object's public photos, in order (the n of /photos/o<pid>/<n>)."""
    return [k for k in db.scalars(select(PropertyMedia.path).where(PropertyMedia.property_id == pid, PropertyMedia.kind == "photo")
                                  .order_by(PropertyMedia.position, PropertyMedia.id)) if not k.startswith(P.PRIVATE_PREFIX)]


def public_filter():
    has_photo = exists().where(PropertyMedia.property_id == Property.id, PropertyMedia.kind == "photo",
                               ~PropertyMedia.path.startswith(P.PRIVATE_PREFIX))
    return (Property.status == "published", ~Property.site_hidden, has_photo)


def site_pid(db: Session, listing_id) -> int | None:
    """The object the site shows for a listing (the bot card's «📍 На карте» opens it on the site's map), else None.
    A catalog row of an object is «obj<id>»; an outside ad is linked by properties.listing_id."""
    lid = str(listing_id or "")
    own = int(lid[3:]) if lid.startswith("obj") and lid[3:].isdigit() else None
    return db.scalar(select(Property.id).where(or_(Property.listing_id == lid, Property.id == own), *public_filter())
                     .order_by(Property.id).limit(1))


def _offer_filter(m):
    return (m.status == "published", func.jsonb_array_length(m.photos) > 0)


def find(db: Session, ident: str) -> Property | Car | None:
    """A public object by "o<id>" / "c<id>", a copied sale object's "s<id>", or its old site id ("rent-551776");
    None when it is not public."""
    pub = lambda model: public_filter() if model is Property else _offer_filter(model)  # noqa: E731
    if m := re.fullmatch(r"([oc])(\d+)", ident):
        model = Property if m[1] == "o" else Car
        return db.scalars(select(model).where(*pub(model), model.id == int(m[2]))).first()
    if re.fullmatch(r"s\d+", ident):
        return db.scalars(select(Property).where(*pub(Property), Property.post_fields["offer_ident"].astext == ident)).first()
    for model in (Property, Car):
        by_site = Property.post_fields["site_id"].astext == ident if model is Property else model.site_id == ident
        if o := db.scalars(select(model).where(*pub(model), by_site)).first():
            return o
    return None


def keys(db: Session, o) -> list[str]:
    return photo_keys(db, o.id) if isinstance(o, Property) else list(o.photos)


def video_key(db: Session, o) -> str | None:
    if not isinstance(o, Property):
        return o.video
    return db.scalar(select(PropertyMedia.path).where(PropertyMedia.property_id == o.id, PropertyMedia.kind == "video",
                                                      ~PropertyMedia.path.startswith(P.PRIVATE_PREFIX))
                     .order_by(PropertyMedia.position, PropertyMedia.id).limit(1))


def ident_of(o) -> str:
    return f"{'o' if isinstance(o, Property) else 'c'}{o.id}"


def feed(db: Session) -> dict:
    props = db.scalars(select(Property).where(*public_filter())).all()
    cars = db.scalars(select(Car).where(*_offer_filter(Car))).all()
    counts: dict[int, int] = {}
    for pid, in db.execute(select(PropertyMedia.property_id).where(
            PropertyMedia.kind == "photo", ~PropertyMedia.path.startswith(P.PRIVATE_PREFIX),
            PropertyMedia.property_id.in_([p.id for p in props]))):
        counts[pid] = counts.get(pid, 0) + 1
    aids = {o.agent_id for o in (*props, *cars) if o.agent_id}
    agents = {u.id: u for u in db.scalars(select(User).where(User.id.in_(aids)))}
    fx, at = rates(), pins(db, props)
    rows = ([(p.published_at, p.id, item(p, counts.get(p.id, 0), agents.get(p.agent_id), fx, at.get(p.id))) for p in props]
            + [(c.published_at, c.id, car_item(c, agents.get(c.agent_id), fx)) for c in cars])
    rows.sort(key=lambda r: (r[0] or EPOCH, r[1]), reverse=True)  # newest first, undated last
    items = [r[2] for r in rows]
    by: dict[str, int] = {}
    for i in items:
        by[i["service"]] = by.get(i["service"], 0) + 1
    return {"updated_at": max((i["updated_at"] for i in items if "updated_at" in i), default=None),
            "rates_usd": fx, "counts": by, "items": items}


_cache = {"at": 0.0, "v": None}


def cached_feed(db: Session) -> dict:
    # ponytail: per process, 60 s; add a shared cache only if the API ever runs many workers
    if _cache["v"] is None or time.time() - _cache["at"] > FEED_TTL:
        _cache["v"], _cache["at"] = feed(db), time.time()
    return _cache["v"]


def clear_cache() -> None:
    _cache["v"] = None


def detail(db: Session, ident: str) -> dict | None:
    o = find(db, ident)
    if o is None:
        return None
    agent, fx = db.get(User, o.agent_id) if o.agent_id else None, rates()
    d = item(o, len(photo_keys(db, o.id)), agent, fx, pins(db, [o]).get(o.id)) if isinstance(o, Property) else car_item(o, agent, fx)
    d["video"] = VIDEO_URL.format(id=ident_of(o)) if video_key(db, o) else None
    return d
