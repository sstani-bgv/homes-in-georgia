"""Cars from the car channels into the CRM («Авто») and so onto the site hge.ge.

The agency posts cars by hand in two public channels (CarsSaleInGeorgia, CarsRentInGeorgia); the site shows only the
CRM's cars (site_feed). Every CAR_IMPORT_MINUTES the worker reads both channels' web preview t.me/s/<channel> (public
channels, no session, no bot rights needed) and keeps the CRM in step:

  new post                    -> a new car (source=telegram, status published when it has a photo), photos copied
  edited post                 -> the car re-read from the new text (an edit made in the CRM is overwritten: the post
                                 is the truth for an imported car)
  #Sold / #Rented / продано   -> the car goes to the archive (off the site)
  post deleted from channel   -> archived too (only after a complete walk of the channel)
  a rent car reposted again   -> the same car (same model, year, price and period), moved to the newest post
                                 (address, text, link); the rent channel reposts its fleet every day
  a post the CRM published    -> left alone (services/offers.publish set its tg_channel / tg_message_id)

Posts are read in the format of services/car_post.py (the generator writes it, this reads it back). A post without a
car header and a price («CARS ON MAP», the hashtag guide) is skipped.
Usage: python -m app.services.car_import [--dry-run] [--only CarsSaleInGeorgia]"""
import argparse
import html
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.models import Car
from app.services import car_post as CP
from app.services import site_feed
from app.services.common import log_event

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
PAUSE = 1.0  # seconds between t.me pages
MAX_PAGES = 60  # 20 posts a page: far more than either channel holds
MAX_PHOTOS = 9
CLOSED = re.compile(r"#(?:Sold|Rented|Сдано|Продано)\b|\bSOLD\b|\bRENTED\b|\bпродан[аоы]?\b|\bсдан[аоы]?\b", re.I)
CURRENCY = {"$": "USD", "₾": "GEL", "€": "EUR"}
FUEL_IN = {v.lower(): k for k, v in CP.FUEL.items()} | {"gasoline": "petrol", "benzin": "petrol", "lpg": "gas",
                                                         "cng": "gas", "pluginhybrid": "plugin_hybrid"}
BODY_IN = {v.lower(): k for k, v in CP.BODY.items()} | {"cabrio": "convertible", "van": "minivan", "jeep": "suv",
                                                         "universal": "wagon", "businessclass": "business",
                                                         "bussunesclass": "business"}
CLASS_IN = {c.lower(): c for c in CP.CAR_CLASS} | {"businessclass": "BusinessClass", "bussunesclass": "BusinessClass"}
BRAND_IN = {"mersedes": "Mercedes-Benz", "mercedes": "Mercedes-Benz", "landrover": "Land Rover", "alfaromeo": "Alfa Romeo",
            "rollsroyce": "Rolls-Royce", "astonmartin": "Aston Martin", "vw": "Volkswagen"}
PETS_IN = {v.lower(): k for k, v in CP.PETS.items()} | {"notallowed": "no", "no": "no", "petsallowed": "allowed",
                                                         "nopets": "no"}
EXTRA_IN = {tag.lower(): key for key, (_, tag) in CP.EXTRAS.items()} | {"babyseat": "child_seat", "childseat": "child_seat",
                                                                         "tirechains": "chains", "wintertyres": "winter_tires"}
DRIVES = {d.lower(): d for d in CP.DRIVE}
TRANSMISSIONS = {t.lower(): t for t in CP.TRANSMISSION}


# ---------------------------------------------------------------- reading the channel (t.me/s web preview)

def _text(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_page(page: str) -> list[dict]:
    """Posts of one t.me/s page, oldest first: {id, ids (every message of an album), date, text, photos: [url]}."""
    posts = []
    for chunk in page.split('<div class="tgme_widget_message_wrap')[1:]:
        m = re.search(r'data-post="[^"/]+/(\d+)"', chunk)
        if not m:
            continue
        t = re.search(r'class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', chunk, re.S)
        d = re.search(r'<time datetime="([^"]+)"', chunk)
        photos, ids = [], {int(m[1])}
        for tag in re.findall(r'<a class="tgme_widget_message_photo_wrap[^>]*>', chunk):
            url = re.search(r"background-image:url\('([^']+)'\)", tag)
            pid = re.search(r'href="https://t\.me/[^/]+/(\d+)', tag)
            if pid:
                ids.add(int(pid[1]))
            if url:
                photos.append((int(pid[1]) if pid else 0, html.unescape(url[1])))
        posts.append({"id": int(m[1]), "ids": sorted(ids), "date": d[1] if d else None,
                      "text": _text(t[1]) if t else "", "photos": [u for _, u in sorted(photos)]})
    return posts


def walk(c: httpx.Client, channel: str) -> tuple[list[dict], bool]:
    """Every post of the channel, newest first -> (posts, complete). complete = the walk reached the first post
    (only then a car whose post is missing counts as deleted)."""
    out, before = {}, None
    for _ in range(MAX_PAGES):
        r = c.get(f"https://t.me/s/{channel}" + (f"?before={before}" if before else ""))
        r.raise_for_status()
        posts = parse_page(r.text)
        if not posts:
            return sorted(out.values(), key=lambda p: -p["id"]), before is not None
        for p in posts:
            out[p["id"]] = p
        oldest = min(p["id"] for p in posts)
        if before is not None and oldest >= before or oldest <= 1:
            return sorted(out.values(), key=lambda p: -p["id"]), True
        before = oldest
        time.sleep(PAUSE)
    return sorted(out.values(), key=lambda p: -p["id"]), False


# ---------------------------------------------------------------- reading a post (the car_post.py format back)

def _n(s) -> float | None:
    s = re.sub(r"[\s,]", "", str(s or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _tags(s: str) -> list[str]:
    return re.findall(r"#(\w+)", s or "")


def _after(lines: list[str], head: str) -> str:
    """The line after the section heading (✨ OPTIONS, 🛡 SAFETY)."""
    for i, line in enumerate(lines):
        if head in line.upper() and i + 1 < len(lines):
            return lines[i + 1]
    return ""


def _canon(tag: str, vocab) -> str:
    low = {v.lower(): v for v in vocab}
    return low.get(tag.lower(), tag)


def parse_sale(text: str) -> dict | None:
    """A CarsSaleInGeorgia post -> the car's columns + post_fields, or None when it is not a car offer."""
    lines = [x.strip() for x in text.splitlines()]
    head = next((x for x in lines if x.startswith("🚘")), "")
    m = re.match(r"🚘\s*(.+?)\s*\|\s*(\d{4})\s*(.*)$", head)
    if not m:
        return None
    title, year, rest = m[1].strip(), int(m[2]), m[3]
    pm = re.search(r"([$₾€])\s*(\d[\d,. ]*)|(\d[\d,. ]*)\s*([$₾€])", rest)
    if not pm:
        return None
    price = _n(pm[2] or pm[3])
    f: dict = {}
    car = {"deal": "sale", "title": title, "year": year, "price": price, "currency": CURRENCY[pm[1] or pm[4]],
           "is_exclusive": "#exclusive" in text.lower()}
    if mm := re.search(r"⛽️?\s*([^\n⚙]*)", text):  # «Petrol · I4 2.0L Turbo · 208 HP»
        parts = [p.strip() for p in mm[1].split("·") if p.strip()]
        if parts and parts[0].lower().replace(" ", "") in FUEL_IN:
            car["fuel"] = FUEL_IN[parts.pop(0).lower().replace(" ", "")]
        hp = next((p for p in parts if re.fullmatch(r"\d+\s*HP", p, re.I)), None)
        if hp:
            f["hp"] = int(re.match(r"\d+", hp)[0])
            parts.remove(hp)
        if parts:
            f["engine"] = " · ".join(parts)
    if mm := re.search(r"Transmission:\s*([^\n🛣]*)", text):
        for t in _tags(mm[1]):
            if t.lower() in TRANSMISSIONS:
                f["transmission"] = TRANSMISSIONS[t.lower()]
                car["gearbox"] = CP.TRANSMISSION[f["transmission"]]
            elif t.lower() in DRIVES:
                f["drive"] = DRIVES[t.lower()]
    if mm := re.search(r"🛣️?\s*(\d[\d,. ]*)\s*km\s*(?:·\s*([^\n🎨]+))?", text, re.I):
        car["mileage"] = int(_n(mm[1]) or 0)
        if mm[2] and (b := mm[2].strip().lower().replace(" ", "")) in BODY_IN:
            car["body"] = BODY_IN[b]
    if mm := re.search(r"🎨\s*#?(\w+)", text):
        f["color"] = _canon(mm[1], CP.COLORS)
    if opts := _tags(_after(lines, "✨ OPTIONS")):
        f["options"] = [_canon(o, CP.SALE_OPTIONS) for o in opts]
    if safety := _tags(_after(lines, "SAFETY")):
        f["safety"] = [_canon(o, CP.SAFETY) for o in safety]
    if mm := re.search(r"CONDITION:\s*#?(\w+)", text):
        f["condition"] = _canon(mm[1], CP.CONDITION)
    if mm := re.search(r"VIN:\s*([A-Z0-9]{8,20})", text, re.I):
        f["vin"] = mm[1].upper()
    f["after_sale"] = "AFTER-SALE" in text.upper()
    # the free text: from the CONDITION line to the first section that follows
    desc, fixes, marks, zone = [], [], [], None
    start = next((i for i, x in enumerate(lines) if "CONDITION:" in x), None)
    for line in lines[start + 1:] if start is not None else []:
        if re.match(r"(🛡|🔎|🛠|📲|👤|#)", line):
            break
        if line.lower().startswith("cosmetic imperfections"):
            zone = "cosmetic"
        elif line.startswith("✅"):
            fixes.append(line.lstrip("✅").strip())
        elif line.startswith("•") or (zone == "cosmetic" and line.startswith("-")):
            marks.append(line.lstrip("•-").strip())
        elif line and zone != "cosmetic":
            desc.append(line)
        elif not line and desc and desc[-1] != "":
            desc.append("")
    car["description"] = "\n".join(desc).strip() or None
    if fixes:
        f["maintenance"] = fixes
    if marks:
        f["cosmetic"] = marks
    car["post_fields"] = f
    return car


def parse_rent(text: str) -> dict | None:
    """A CarsRentInGeorgia post -> the car's columns + post_fields, or None when it is not a car offer."""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    head = next((x for x in lines if "🚙" in x or "🚗" in x), "")
    m = re.match(r"#(\w+)\s*([^🚙🚗]*?)\s*[🚙🚗]\s*((?:#\w+\s*)*)(?:📍\s*(.*))?$", head)
    pm = re.search(r"💰\s*(\d[\d,. ]*)\s*([$₾€])\s*/\s*(day|month|день|месяц)", text, re.I)
    if not m or not pm:
        return None
    brand = BRAND_IN.get(m[1].lower(), m[1])
    title = f"{brand} {m[2].strip()}".strip()
    f: dict = {}
    car = {"deal": "rent", "title": title, "price": _n(pm[1]), "currency": CURRENCY[pm[2]],
           "period": "month" if pm[3].lower() in ("month", "месяц") else "day", "pickup": (m[4] or "").strip() or None,
           "is_exclusive": "#exclusive" in text.lower()}
    for k in (t.lower() for t in _tags(m[3])):  # «#SUV», «#Businessclass», «#SUV #Minivan»
        if k in CLASS_IN and "car_class" not in f:
            f["car_class"] = CLASS_IN[k]
            car.setdefault("body", "business" if f["car_class"] == "BusinessClass" else None)
        elif k in BODY_IN and not car.get("body"):
            car["body"] = BODY_IN[k]
    tags = [t.lower() for t in _tags(text)]
    if mm := re.search(r"#((?:19|20)\d\d)\b", text):
        car["year"] = int(mm[1])
    if "automatic" in tags:
        car["gearbox"] = "auto"
    elif "manual" in tags:
        car["gearbox"] = "manual"
    if fuel := next((FUEL_IN[t] for t in tags if t in FUEL_IN), None):
        car["fuel"] = fuel
    if mm := re.search(r"Fuel Use:\s*(\d+(?:[.,]\d+)?)", text, re.I):
        f["fuel_use"] = _n(mm[1].replace(",", "."))
    opt_line = next((x for x in lines if "🧊" in x or "🛞" in x or "Fuel Use" in x), "")
    known = {o.lower(): o for group in CP.RENT_OPTIONS.values() for o in group}
    f["options"] = [known[t.lower()] for t in _tags(opt_line) if t.lower() in known]
    if drive := next((DRIVES[t.lower()] for t in _tags(opt_line) if t.lower() in DRIVES), None):
        f["drive"] = drive
    f["smoking"] = bool(re.search(r"Smoking\s*✅", text))
    if mm := re.search(r"Pets:\s*#?(\w+)", text):
        f["pets"] = PETS_IN.get(mm[1].lower())
    if mm := re.search(r"Capacity:\s*(\d+)", text):
        car["seats"] = int(mm[1])
    if mm := re.search(r"Trunk:\s*(\d+)", text):
        f["trunk"] = int(mm[1])
    for key, name in (("insurance_basic", "Basic Insurance"), ("insurance_full", "Full Insurance")):
        if mm := re.search(name + r":\s*(\d[\d.,]*)", text, re.I):
            f[key] = _n(mm[1])
    if mm := re.search(r"Deposit:\s*(\d[\d,. ]*)", text, re.I):
        car["deposit"] = _n(mm[1])
    extras = []
    for line in lines:
        if em := re.search(r"#(\w+):\s*(?:(\d[\d.,]*)\s*[$₾€]|Free)", line, re.I):
            if (key := EXTRA_IN.get(em[1].lower())) and key not in {e["key"] for e in extras}:
                extras.append({"key": key, "price": _n(em[2]) if em[2] else 0})
    if extras:
        f["extras"] = extras
    f["unlimited_mileage"] = "unlimitedmileage" in text.lower().replace(" ", "")
    if mm := re.search(r"Pick-up\s*&\s*drop-off:\s*(.+)", text, re.I):
        f["delivery"] = mm[1].strip()
    else:
        f["delivery"] = "-"
    car["post_fields"] = {k: v for k, v in f.items() if v not in (None, [], "")}
    return car


def parse(text: str, deal: str) -> dict | None:
    return parse_sale(text) if deal == "sale" else parse_rent(text)


# ---------------------------------------------------------------- the CRM side

CHANNEL_DEAL = {"CarsSaleInGeorgia": "sale", "CarsRentInGeorgia": "rent"}
COLUMNS = ("title", "year", "price", "currency", "period", "deposit", "gearbox", "fuel", "body", "seats", "mileage",
           "pickup", "is_exclusive", "description")


def _key(car) -> tuple:
    """The rent channel reposts the same car: the same model, year, price and period is one car."""
    g = car.get if isinstance(car, dict) else lambda k: getattr(car, k)
    price = g("price")
    return ((g("title") or "").lower().strip(), g("year"), float(price) if price is not None else None, g("period"))


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _apply(o: Car, data: dict) -> None:
    for k in COLUMNS:
        setattr(o, k, data.get(k))
    o.post_fields = data.get("post_fields") or {}


def _geocode(o: Car) -> None:
    """The pick-up address -> the point on the site's map (OSM, cached; nothing when it is not found)."""
    if not o.pickup or o.lat is not None:
        return
    try:
        from app.services import geo
        hits = geo.search(f"{o.pickup}, Tbilisi").get("items") or []
    except Exception:  # noqa: BLE001
        hits = []
    if hits:
        o.lat, o.lng = hits[0]["lat"], hits[0]["lng"]


def _photos(c: httpx.Client, o: Car, urls: list[str]) -> list[str]:
    keys = []
    for url in urls[:MAX_PHOTOS]:
        try:
            r = c.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("car %s photo %s: %s", o.id, url, e)
            continue
        key = f"offers/{site_feed.ident_of(o)}/{uuid.uuid4().hex}.jpg"
        storage.put(key, r.content)
        storage.put_preview(key, r.content)
        keys.append(key)
    return keys


def sync_channel(db: Session, c: httpx.Client, channel: str, posts: list[dict], complete: bool,
                 dry_run: bool = False) -> dict:
    """One channel's posts (newest first) -> the CRM's cars. -> counts {created, updated, moved, archived, skipped}."""
    deal = CHANNEL_DEAL[channel]
    stats = dict.fromkeys(("created", "updated", "moved", "archived", "skipped"), 0)
    cars = db.scalars(select(Car).where(Car.deal == deal)).all()
    by_msg = {o.tg_message_id: o for o in cars if o.tg_channel == channel and o.tg_message_id}
    for o in cars:  # a car the CRM published before tg_message_id existed: known by its post link
        if m := re.search(rf"t\.me/{channel}/(\d+)", o.post_url or ""):
            by_msg.setdefault(int(m[1]), o)
    by_key = {}
    for o in sorted(cars, key=lambda x: -(x.tg_message_id or 0)):
        if o.source == "telegram":
            by_key.setdefault(_key(o), o)
    seen: set[int] = set()  # cars whose post is still in the channel
    for p in posts:  # newest first: the newest repost of a rent car wins
        own = next((by_msg[i] for i in p["ids"] if i in by_msg), None)
        data = parse(p["text"], deal)
        closed = bool(CLOSED.search(p["text"]))
        if own is not None and own.source != "telegram":  # the CRM's own post: the CRM is the truth
            seen.add(own.id)
            if closed and own.status != "archived" and not dry_run:
                own.status = "archived"
                stats["archived"] += 1
            continue
        if data is None:
            stats["skipped"] += 1
            continue
        o = own or (by_key.get(_key(data)) if deal == "rent" else None)
        if o is not None and o.id in seen:  # an older repost of a car already taken from a newer post
            continue
        if o is None:
            if closed:
                stats["skipped"] += 1
                continue
            stats["created"] += 1
            if dry_run:
                continue
            o = Car(deal=deal, source="telegram", tg_channel=channel, tg_message_id=p["id"], status="draft",
                    title=data["title"], post_url=f"https://t.me/{channel}/{p['id']}", source_text=p["text"],
                    published_at=_dt(p["date"]) or datetime.now(timezone.utc))
            _apply(o, data)
            db.add(o)
            db.flush()
            o.photos = _photos(c, o, p["photos"])
            o.status = "published" if o.photos else "draft"
            _geocode(o)
            log_event(db, "car_offer", o.id, "imported", None, channel=channel, message_id=p["id"])
        else:
            moved = o.tg_message_id != p["id"]
            changed = moved or o.source_text != p["text"] or (closed and o.status != "archived")
            if not changed:
                seen.add(o.id)
                continue
            stats["moved" if moved else "updated"] += 1
            if dry_run:
                seen.add(o.id)
                continue
            old_pickup = o.pickup
            _apply(o, data)
            if o.pickup != old_pickup:
                o.lat = o.lng = None
                _geocode(o)
            o.tg_channel, o.tg_message_id, o.source_text = channel, p["id"], p["text"]
            o.post_url = f"https://t.me/{channel}/{p['id']}"
            if moved:
                o.published_at = _dt(p["date"]) or o.published_at
            got = False
            if not o.photos:
                o.photos = _photos(c, o, p["photos"])
                got = bool(o.photos)
            # sold / rented -> archive; a fresh repost puts the car back on the site; otherwise the CRM's status stays
            # (an operator who took an imported car off the site is not overruled by the next run)
            if closed and o.status != "archived":
                stats["archived"] += 1
                o.status = "archived"
            elif not closed and o.photos and (moved or got and o.status == "draft"):
                o.status = "published"
            log_event(db, "car_offer", o.id, "reimported", None, channel=channel, message_id=p["id"])
        seen.add(o.id)
        by_msg[p["id"]] = o
        by_key.setdefault(_key(o), o)
    if complete:  # a channel car whose post is gone: off the site
        for o in cars:
            if o.source == "telegram" and o.tg_channel == channel and o.id not in seen and o.status == "published":
                stats["archived"] += 1
                if not dry_run:
                    o.status = "archived"
                    log_event(db, "car_offer", o.id, "archived", None, reason="post deleted from the channel")
    if dry_run:
        db.rollback()
    else:
        db.commit()
        site_feed.clear_cache()
    return stats


def run(db: Session, dry_run: bool = False, only: str | None = None, client: httpx.Client | None = None) -> dict:
    """Both car channels -> the CRM. A channel that fails to load keeps its cars as they are."""
    out = {}
    c = client or httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
    try:
        for channel in CHANNEL_DEAL:
            if only and channel.lower() != only.lower():
                continue
            try:
                posts, complete = walk(c, channel)
            except httpx.HTTPError as e:
                log.warning("car import %s: %s", channel, e)
                out[channel] = {"error": str(e)}
                continue
            out[channel] = sync_channel(db, c, channel, posts, complete, dry_run)
    finally:
        if client is None:
            c.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()
    from app.db import SessionLocal
    with SessionLocal() as db:
        print(run(db, args.dry_run, args.only))


if __name__ == "__main__":
    main()
