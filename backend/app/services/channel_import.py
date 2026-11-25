"""Посты канала → объекты CRM (Stanislav 11-22: «объекты, которые они выкладывают в канал, пусть сами добавляются в
CRM, раз они ей пока не пользуются»). Operators post flats straight to the agency channel (193 posts in two weeks, 4
through the CRM), so hge.ge showed 81 rentals against 250 in the channel. Every half hour the channel is read through the
Telegram account (the reader, else the operator's userbot: rich posts and photos the web preview hides) and each new
flat post becomes a published object on the site: the card fields and generator tags from the text (the old base's
parsers), the agent by the post's hashtag (#Roman2), the photos, a map point. A post of a flat the CRM already has (a
repost: the same address and bedrooms) moves that object's link to the new post instead. Deleted posts: post_sync.py.
The sale channel too (Irakli 11-24: «в квартирах на продажу половины объявлений из канала sale_in_tbilisi нет»): its posts
become sale objects (post_fields.deal = Sale, sale_price), a repost relinks only a sale object, a rent one never."""
import base64
import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings as env
from app.importers import legacy_objects as L
from app.models import Event, Property, PropertyMedia, PropertyPublication, User
from app.services.common import log_event, now

log = logging.getLogger(__name__)
CHANNEL = "rent_tbilisi_ge"
SALE_CHANNEL = "sale_in_tbilisi"
CHANNELS = (CHANNEL, SALE_CHANNEL)  # ponytail: @homesingeorgia (commercial) when asked
BACKFILL_DAYS = 16  # a channel's first pass: the sale one from 11-09 (deployed 11-25)
PAGE = 150                   # messages per request: ~15 albums with their photos inside the reader's 240 s
LIVE = ("published", "taken", "owner_confirmed", "shoot_scheduled", "media_uploaded", "review", "returned")


def _readers() -> list[str]:
    from app.api.content import reader_url
    return [u for u in (reader_url(), (env.USERBOT_URL or "").strip().rstrip("/")) if u]


def ask(path: str, body: dict, timeout: float = 250) -> dict | None:
    """POST to the reader account, else the operator's userbot -> the reply, None when neither answers."""
    for url in _readers():
        try:
            r = httpx.post(f"{url}{path}", json=body, headers={"X-Userbot-Token": env.SECRET_KEY}, timeout=timeout)
            if r.status_code == 200 and r.json().get("ok"):
                return r.json()
        except (httpx.HTTPError, ValueError):
            pass
    return None


def posts(channel: str, min_id: int) -> list[dict] | None:
    """The channel's posts after min_id (none yet: the last BACKFILL_DAYS), oldest first; None: no account answered."""
    out, max_id, since = [], 0, (now() - timedelta(days=BACKFILL_DAYS)).isoformat()
    while True:
        r = ask("/channel", {"user": channel, "min_id": min_id, "max_id": max_id, "limit": PAGE, "photos": 10})
        if r is None:
            return None if not out else sorted(out, key=lambda p: p["id"])
        got = r["posts"]
        out += [p for p in got if min_id or p["date"] >= since]
        if not got or (not min_id and min(p["date"] for p in got) < since) or len(out) > 400:
            break
        max_id = min(p["id"] for p in got)
    return sorted(out, key=lambda p: p["id"])


def cursor(db: Session, channel: str) -> int:
    ev = db.scalar(select(Event).where(Event.entity == "channel", Event.entity_id == channel, Event.action == "channel_import")
                   .order_by(Event.id.desc()).limit(1))
    return int((ev.data or {}).get("max_id") or 0) if ev else 0


def known(db: Session, channel: str, msg: int) -> bool:
    """The CRM posted it (or imported it before)."""
    url = f"https://t.me/{channel}/{msg}"
    if db.scalar(select(Property.id).where(Property.post_url == url).limit(1)):
        return True
    return bool(db.scalar(select(PropertyPublication.id).where(
        (PropertyPublication.url == url) | (PropertyPublication.url.like(f"https://t.me/{channel}/%")
                                            & PropertyPublication.messages["ids"].contains([msg]))).limit(1)))


def is_sale(p: Property) -> bool:
    return (p.post_fields or {}).get("deal") == "Sale"


SALE_PRICE = re.compile(r"💰\s*\$?\s*(\d{1,3}(?:[., ]\d{3})+|\d{4,9})\s*\$")


def sale_fields(text: str) -> dict:
    """A sale post's card fields: «💰 212.500$» (a dot / comma / space groups thousands; «12$ per Sq.m» is no price),
    «160 000 Sq.m», the 📍 line as the address even without a number (land: «Mirian Mepe Street»), no «SALE IN TBILISI»
    header for a district."""
    t = re.sub(r"^\W*SALE IN TBILISI\W*", "", text, flags=re.I)
    f = {k: parse(t) for k, parse in L.CARD_PARSERS.items()}
    f["address"] = _place(t) or f["address"]
    price = int(re.sub(r"\D", "", m[1])) if (m := SALE_PRICE.search(t)) else 0
    f["price"] = price if price >= 5000 else None
    if m := re.search(r"(\d{1,3}(?: \d{3})+)\s*sq", t, re.I):
        f["area"] = float(m[1].replace(" ", ""))
    return f


def same_flat(db: Session, address: str | None, bedrooms: int | None, sale: bool = False) -> Property | None:
    """A CRM object of this flat: the same street + house, the same bedrooms, the same deal (a channel repost of an
    object; a flat for rent and the same flat for sale are two objects)."""
    key = L.addr_key(address)
    if not key:
        return None
    street = key.split("|")[0].split()[0]
    gone_post = (Property.status == "archived") & (Property.source_type == "channel") & (Property.archive_reason == "not_actual")
    rows = db.scalars(select(Property).where(Property.status.in_(LIVE) | gone_post, Property.bedrooms == bedrooms,
                                             Property.address.ilike(f"%{street}%")).order_by(Property.id.desc()))
    return next((p for p in rows if L.addr_key(p.address) == key and is_sale(p) == sale), None)


def _agent(db: Session, text: str) -> int | None:
    tags = [t.lower() for t in L.tags_of(text)]
    users = db.scalars(select(User).where(func.lower(User.agent_tag).in_(tags))).all() if tags else []
    return users[0].id if users else None


def _queries(address: str):
    """The address as posted, then «50/3» → «50» without «St», then the street alone (an approximate point)."""
    a = re.sub(r"\s+", " ", address).strip()
    yield a, not re.search(r"\d", a)  # «Dirsi»: a place, not a house
    b = re.sub(r"\b(\d+)[a-zA-Z]?(?:[/-]\d+[a-zA-Z]?)+\b", r"\1", a)
    b = re.sub(r"\b(St\.?|Street|Ave|Avenue)\b", "", b, flags=re.I).strip(" ,")
    if b != a:
        yield b, False
    if street := re.sub(r"\b\d+\w*\b", "", b).strip(" ,"):
        yield street, True


def _place(text: str | None) -> str | None:
    """The 📍 line of a post even without a house number («Dirsi», «Vazha-Pshavela IV Block», «Kvemo Lisi»)."""
    for line in L.plain(text or "").splitlines():
        if "📍" in line and "ON MAP" not in line.upper():
            a = re.sub(r"#\w+|💰.*", "", line.split("📍", 1)[1]).strip(" |,")
            if len(a) > 3:
                return a
    return None


CITY = re.compile(r"\b(Rustavi|Batumi|Kutaisi|Mtskheta|Saguramo|Tabahmela|Kobuleti|Gudauri|Bakuriani)\b", re.I)


def _point(p: Property) -> bool:
    """A map point for the object (the site's map shows only objects with one): the Google Maps link of its post
    (most posts have one, with the exact pin), else its address or 📍 place by OSM."""
    from app.services import geo
    for url in re.findall(r"https?://[^\s)\]]+", p.description or ""):
        if "goo" in url and (at := geo.point_of(url)):
            p.lat, p.lng = at
            return True
    place = p.address or _place(p.description)
    head = " ".join(re.findall(r"#(\w+)", (p.description or "").strip().split("\n", 1)[0]))  # «#Saguramo 🏔️ #Tbilisi»
    city = "" if place and CITY.search(f"{place} {head}") else ", Tbilisi"
    for q, approx in _queries(place) if place else ():
        try:
            hit = (geo.search(f"{q}{city}").get("items") or [None])[0]
        except Exception:  # noqa: BLE001 — no map point is no reason to lose the object
            return False
        if hit:
            p.lat, p.lng = hit["lat"], hit["lng"]
            p.district = p.district or hit.get("district")
            if approx:
                p.post_fields = {**(p.post_fields or {}), "approx": True}
            return True
    return False


def locate(db: Session, limit: int = 60) -> int:
    """Objects on the site without a map point (the old base's, posts with an odd address): a point by the address,
    `limit` a pass (Nominatim: 1 request a second); the ones not found are tried once a day."""
    from app.services import site_feed
    tried = {e for e in db.scalars(select(Event.entity_id).where(Event.action == "no_map_point",
                                                                 Event.at > now() - timedelta(days=1)))}
    rows = [p for p in db.scalars(select(Property).where(*site_feed.public_filter(), Property.lat.is_(None)).order_by(Property.id.desc()))
            if str(p.id) not in tried][:limit]
    n = 0
    for p in rows:
        if _point(p):
            n += 1
        else:
            log_event(db, "property", p.id, "no_map_point", None, label="карта", address=p.address)
        db.commit()
    return n


def _photos(db: Session, p: Property, photos: list[dict]) -> None:
    from app import storage
    from app.services.matching import photo_hash
    for n, ph in enumerate(photos):
        data = base64.b64decode(ph["b64"])
        key = f"properties/{p.id}/tg{ph['id']}.jpg"
        storage.put(key, data)
        storage.put_preview(key, data)
        db.add(PropertyMedia(property_id=p.id, kind="photo", path=key, position=n, dhash=photo_hash(data)))


def _same_by_photos(db: Session, p: Property) -> Property | None:
    """An object on the site (or one post_sync archived) that «Похожие» calls the same flat (shared photos, the house)."""
    from app.services import similar
    for i in similar.find(db, f"p:{p.id}", None, limit=10, include_ads=False):
        o = db.get(Property, i["property_id"]) if i.get("level") == "same" and i.get("property_id") else None
        if o and o.id != p.id and is_sale(o) == is_sale(p) and (o.status == "published" or (o.status == "archived" and o.source_type == "channel"
                                                               and o.archive_reason == "not_actual")):
            return o
    return None


EXCLUSIVE = re.compile(r"#exclusive\b", re.I)


def _exclusive(db: Session, p: Property, text: str) -> None:
    """«#Exclusive» in the post: the object is ours exclusively — marked, and the duplicate search runs at once (its
    copies on myhome, ss.ge…, Irakli 11-18) like for an exclusive typed into the CRM."""
    from app.services import properties as P
    if EXCLUSIVE.search(text) and not p.is_exclusive:
        p.is_exclusive = True
        P.schedule_dup_scan(db, p, "exclusive")


def _relink(db: Session, p: Property, url: str, msg: int, price, text: str) -> None:
    from app.services import properties as P
    old = p.post_url
    p.post_url, p.post_message_id, p.published_at = url, str(msg), now()
    if price and p.status == "published":  # a repost with a new price (600$ → 800$)
        p.price = price
        if is_sale(p):
            p.post_fields = {**p.post_fields, "sale_price": price}
    if p.status == "archived":  # same_flat: only a channel object archived by post_sync
        p.status, p.archive_reason, p.archive_comment, p.archived_at = "published", None, None, None
    if p.status == "published":
        p.site_hidden = False
    P.record_publication(db, p, "tg", url, str(msg))
    log_event(db, "property", p.id, "channel_repost", None, label="пост в канале", post_url=url, old_post_url=old)
    _exclusive(db, p, text)


def import_post(db: Session, channel: str, post: dict) -> str:
    """-> what happened: known / not_object / relinked / skipped (a live CRM object of the flat) / created."""
    from app.services import properties as P
    msg, text = int(post["id"]), post.get("text") or ""
    url = f"https://t.me/{channel}/{msg}"
    if known(db, channel, msg):
        return "known"
    if not L.is_object_text(text) or not post.get("photos"):
        return "not_object"
    sale = channel == SALE_CHANNEL
    fields = sale_fields(text) if sale else {f: parse(text) for f, parse in L.CARD_PARSERS.items()}
    pf = L.post_fields_of(text) or {}
    if sale:
        pf = {**pf, "deal": "Sale", **({"sale_price": fields["price"]} if fields["price"] else {})}
    if p := same_flat(db, fields["address"], fields["bedrooms"], sale):
        if p.status not in ("published", "archived"):
            return "skipped"  # the agent is working on it in the CRM: his card, his post
        _relink(db, p, url, msg, fields["price"], text)
        db.commit()
        return "relinked"
    t = now()
    p = Property(source_type="channel", source_url=url, source_external_id=f"tg:{channel}/{msg}", status="published",
                 post_url=url, post_message_id=str(msg), published_at=t, verified_at=t, site_hidden=False,
                 description=text, post_fields=pf or None, agent_id=_agent(db, text),
                 currency="USD", **{k: v for k, v in fields.items() if v not in (None, "")})
    if fields.get("district"):
        from app import districts
        p.district = districts.label(fields["district"]) or fields["district"]
    _point(p)
    db.add(p)
    db.flush()
    _photos(db, p, post["photos"])
    db.flush()
    if o := _same_by_photos(db, p):  # the address written another way: the photos tell (#15540 = #15509, 11-22)
        from app import storage
        keys = [m.path for m in db.scalars(select(PropertyMedia).where(PropertyMedia.property_id == p.id))]
        oid = o.id
        db.rollback()
        for k in keys:
            try:
                storage.delete(k)
            except Exception:  # noqa: BLE001
                log.warning("could not delete %s", k)
        o = db.get(Property, oid)
        _relink(db, o, url, msg, fields["price"], text)
        db.commit()
        return "relinked"
    P.record_publication(db, p, "tg", url, str(msg))
    log_event(db, "property", p.id, "imported_from_channel", None, label="пост в канале", post_url=url)
    _exclusive(db, p, text)
    db.commit()
    return "created"


def run(db: Session, channel: str = CHANNEL) -> dict:
    start = cursor(db, channel)
    got = posts(channel, start)
    if got is None:
        log.warning("channel import %s: no Telegram account answered", channel)
        got = []
    counts: dict = {}
    for post in got:
        try:
            r = import_post(db, channel, post)
        except Exception:  # noqa: BLE001 — one odd post must not stop the rest
            db.rollback()
            log.exception("channel import %s/%s failed", channel, post.get("id"))
            r = "failed"
        counts[r] = counts.get(r, 0) + 1
    if got:
        log_event(db, "channel", channel, "channel_import", None, max_id=max(p["id"] for p in got), **counts)
        db.commit()
        from app.services import catalog, site_feed
        site_feed.clear_cache()
        catalog.sync_all(db)
        db.commit()
    counts["located"] = locate(db)
    if counts["located"]:
        from app.services import site_feed
        site_feed.clear_cache()
    log.info("channel import %s after %s: %s", channel, start, counts)
    return counts
