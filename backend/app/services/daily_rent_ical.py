"""«Посуточная аренда»: who occupies which nights, double bookings, manual closures and the Airbnb / Booking iCal feeds.

ApartFlow's booking.js + ical.js. Busy = our stays + active blocks. Intervals are half-open [start, end): a check-out and
the next check-in on the same day do not overlap.
- Import: each channel's import_url (the platform's calendar) is fetched (worker, every ICAL_SYNC_MINUTES) and its
  events become blocks of that channel; events that vanished are cancelled.
- Export: GET /api/public/daily-rent/ical/<export_token>.ics — every busy night of the apartment except the platform's
  own (its blocks, stays sold on it, echoes of what it already returned to us), as «Not available».
- New double bookings go to the Daily Rent people in Telegram (notify.daily_rent_conflicts)."""
import hashlib
import json
import re
import secrets
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DrApartment, DrBlock, DrBooking, DrChannel, Setting, User
from app.services import daily_rent as R
from app.services.common import get_or_404, log_event, now
from app.services.errors import DomainError

CHANNELS = ("airbnb", "booking", "other")
LABEL = {"airbnb": "Airbnb", "booking": "Booking", "other": "Другая площадка", "manual": "Закрыто вручную"}
SYNC_ACTOR = "sync:ical"
DEFAULT_HOSTS = [re.compile(p, re.I) for p in (r"(^|\.)airbnb\.[a-z]{2,3}(\.[a-z]{2})?$", r"(^|\.)booking\.com$", r"(^|\.)vrbo\.com$",
                                                 r"(^|\.)homeaway\.com$", r"^calendar\.google\.com$", r"^p\d+-caldav\.icloud\.com$")]
SEEN_KEY = "daily_rent_seen_conflicts"


def platform_key(p) -> str:
    s = str(p or "").lower()
    return "airbnb" if s.startswith("airbnb") else "booking" if s.startswith("booking") else "other"


def overlaps(a, b) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _first_word(s) -> str:
    return (str(s or "").strip().split() or [""])[0].lower()


def occupancy(db: Session, apartment_id: int | None = None) -> list[dict]:
    stays = select(DrBooking).where(DrBooking.deleted_at.is_(None), DrBooking.check_in.is_not(None), DrBooking.check_out > DrBooking.check_in)
    blocks = select(DrBlock).where(DrBlock.deleted_at.is_(None), DrBlock.status == "active", DrBlock.end > DrBlock.start)
    if apartment_id is not None:
        stays, blocks = stays.where(DrBooking.apartment_id == apartment_id), blocks.where(DrBlock.apartment_id == apartment_id)
    out = [{"kind": "stay", "id": b.id, "apartment_id": b.apartment_id, "start": b.check_in.isoformat(), "end": b.check_out.isoformat(),
            "origin": platform_key(b.platform), "guest": b.guest_name or "", "platform": b.platform} for b in db.scalars(stays)]
    out += [{"kind": "block", "id": b.id, "apartment_id": b.apartment_id, "start": b.start.isoformat(), "end": b.end.isoformat(),
             "origin": b.source, "channel_id": b.channel_id, "reserved": b.reserved, "summary": b.summary or b.reason or ""}
            for b in db.scalars(blocks)]
    return out


def mark_echoes(items: list[dict]) -> list[dict]:
    """A platform's block with exactly the dates of our own interval from elsewhere: the platform returned what we gave it."""
    for b in items:
        if b["kind"] != "block" or b["origin"] not in CHANNELS:
            continue
        b["echo"] = any(x is not b and x["apartment_id"] == b["apartment_id"] and x["origin"] != b["origin"]
                        and x["start"] == b["start"] and x["end"] == b["end"]
                        and not (x["kind"] == "block" and x["origin"] in CHANNELS and x.get("echo")) for x in items)
    return items


def analyze(db: Session) -> dict:
    """conflicts: the same nights sold twice; unaccounted: a platform's booking with no stay entered for it yet."""
    items = mark_echoes(occupancy(db))
    by: dict[int, list[dict]] = {}
    for x in items:
        by.setdefault(x["apartment_id"], []).append(x)
    since = (R.today() - timedelta(days=120)).isoformat()
    conflicts, unaccounted = [], []
    for apt, lst in by.items():
        for b in lst:
            if b["kind"] == "block" and b["origin"] in CHANNELS:
                b["covered"] = any(s["kind"] == "stay" and s["origin"] == b["origin"] and overlaps(s, b) for s in lst)
        skip = lambda x: x.get("echo") or x.get("covered") or (x["kind"] == "block" and x["origin"] in CHANNELS and not x["reserved"])  # noqa: E731
        for i, a in enumerate(lst):
            for b in lst[i + 1:]:
                if not overlaps(a, b) or skip(a) or skip(b):
                    continue
                if a["kind"] == "stay" and b["kind"] == "stay":
                    if _first_word(a["guest"]) and _first_word(a["guest"]) == _first_word(b["guest"]):
                        continue  # one stay paid in parts
                elif a["kind"] != b["kind"]:
                    blk, stay = (a, b) if a["kind"] == "block" else (b, a)
                    if blk["origin"] in CHANNELS and blk["origin"] == stay["origin"]:
                        continue
                elif a["origin"] == b["origin"]:
                    continue
                conflicts.append({"key": "|".join(sorted([f"{a['kind']}:{a['id']}", f"{b['kind']}:{b['id']}"])), "apartment_id": apt,
                                  "start": max(a["start"], b["start"]), "end": min(a["end"], b["end"]), "a": a, "b": b})
        for b in lst:
            if b["kind"] == "block" and b["origin"] in CHANNELS and not b.get("echo") and b["reserved"] and b["end"] >= since \
                    and not b.get("covered"):
                unaccounted.append(b)
    return {"conflicts": conflicts, "unaccounted": unaccounted}


def calendar(db: Session, start: date, end: date) -> dict:
    """The «Шахматка»: every interval touching [start, end), plus conflicts / unaccounted."""
    items = [x for x in mark_echoes(occupancy(db)) if x["start"] < end.isoformat() and x["end"] > start.isoformat()]
    return {"items": items, **analyze(db)}


# ---------------------------------------------------------------- manual blocks

def save_block(db: Session, data: dict, actor: User, block_id: int | None = None) -> DrBlock:
    apt = db.get(DrApartment, data.get("apartment_id") or 0)
    if not apt or apt.deleted_at:
        raise DomainError("not_found", "Квартира не найдена", 404)
    s, e = data.get("start"), data.get("end")
    if not s or not e or e <= s:
        raise DomainError("bad_dates", "Даты: «с» раньше «по»", 422)
    db.execute(select(DrApartment.id).where(DrApartment.id == apt.id).with_for_update())  # one writer per apartment
    want = {"start": s.isoformat(), "end": e.isoformat()}
    clash = [x for x in occupancy(db, apt.id) if not (x["kind"] == "block" and x["id"] == block_id) and overlaps(x, want)]
    if clash and not data.get("force"):
        raise DomainError("busy", "Даты уже заняты: " + ", ".join(f"{x['start']}—{x['end']} {x.get('guest') or LABEL.get(x['origin'], x['origin'])}"
                                                                  for x in clash), 409)
    fields = {"apartment_id": apt.id, "start": s, "end": e, "reason": (data.get("reason") or "")[:200]}
    if block_id:
        b = get_or_404(db, DrBlock, block_id, "block", lock=True)
        if b.source != "manual":
            raise DomainError("not_manual", "Изменить можно только ручную блокировку", 400)
        before = R.out(b)
        for k, v in fields.items():
            setattr(b, k, v)
        log_event(db, "dr_block", b.id, "update", actor, changes=R._changes(before, R.out(b)))
    else:
        b = DrBlock(**fields, source="manual", status="active")
        db.add(b)
        db.flush()
        log_event(db, "dr_block", b.id, "create", actor, after=R.out(b))
    db.commit()
    return b


def cancel_block(db: Session, block_id: int, actor: User) -> DrBlock:
    b = get_or_404(db, DrBlock, block_id, "block", lock=True)
    if b.source != "manual":
        raise DomainError("not_manual", "Снять можно только ручную блокировку", 400)
    b.status, b.cancelled_at = "cancelled", now()
    log_event(db, "dr_block", b.id, "cancel", actor)
    db.commit()
    return b


# ---------------------------------------------------------------- channels (platform calendars)

def check_url(url: str, extra_hosts=()) -> str:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise DomainError("bad_url", "Нужна ссылка https:// на календарь площадки", 400)
    host = u.hostname.lower()
    if not any(p.search(host) for p in DEFAULT_HOSTS) and host not in {h.lower() for h in extra_hosts}:
        raise DomainError("bad_url", "Ссылка должна вести на календарь Airbnb, Booking или другой известной площадки", 400)
    return url


def extra_hosts() -> list[str]:
    from app.config import settings
    return [h.strip() for h in (getattr(settings, "DAILY_RENT_ICAL_HOSTS", "") or "").split(",") if h.strip()]


def save_channel(db: Session, data: dict, actor: User, channel_id: int | None = None) -> DrChannel:
    url = (data.get("import_url") or "").strip()
    if url:
        check_url(url, extra_hosts())
    if channel_id:
        ch = get_or_404(db, DrChannel, channel_id, "channel", lock=True)
        before = R.out(ch)
        ch.import_url = url or None
        log_event(db, "dr_channel", ch.id, "update", actor, changes=R._changes(before, R.out(ch)))
    else:
        if data.get("channel") not in CHANNELS:
            raise DomainError("bad_channel", "Площадка: airbnb, booking или other", 400)
        apt = db.get(DrApartment, data.get("apartment_id") or 0)
        if not apt or apt.deleted_at:
            raise DomainError("not_found", "Квартира не найдена", 404)
        ch = DrChannel(apartment_id=apt.id, channel=data["channel"], import_url=url or None, export_token=secrets.token_hex(24))
        db.add(ch)
        db.flush()
        log_event(db, "dr_channel", ch.id, "create", actor, after={k: v for k, v in R.out(ch).items() if k != "export_token"})
    db.commit()
    return ch


def delete_channel(db: Session, channel_id: int, actor: User) -> dict:
    ch = get_or_404(db, DrChannel, channel_id, "channel", lock=True)
    for b in db.scalars(select(DrBlock).where(DrBlock.channel_id == ch.id, DrBlock.status == "active")):
        b.status, b.cancelled_at = "cancelled", now()
    ch.deleted_at = now()
    log_event(db, "dr_channel", ch.id, "delete", actor)
    db.commit()
    return {"deleted": channel_id}


def channels(db: Session, apartment_id: int | None = None) -> list[DrChannel]:
    stmt = select(DrChannel).where(DrChannel.deleted_at.is_(None)).order_by(DrChannel.id)
    if apartment_id:
        stmt = stmt.where(DrChannel.apartment_id == apartment_id)
    return list(db.scalars(stmt))


# ---------------------------------------------------------------- iCal (RFC 5545, just enough for busy-date calendars)

def _to_day(v: str) -> str:
    m = re.search(r"(\d{4})(\d{2})(\d{2})", v or "")
    return f"{m[1]}-{m[2]}-{m[3]}" if m else ""


def _unescape(s: str) -> str:
    return re.sub(r"\\([,;\\])", r"\1", re.sub(r"\\n", " ", s or "", flags=re.I)).strip()


def parse(text: str) -> list[dict]:
    text = re.sub(r"\n[ \t]", "", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    events, cur = [], None
    for line in text.split("\n"):
        if line == "BEGIN:VEVENT":
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur and cur.get("start"):
                if not cur.get("end") or cur["end"] <= cur["start"]:
                    cur["end"] = (date.fromisoformat(cur["start"]) + timedelta(days=1)).isoformat()
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.split(";")[0].upper()
        if name == "UID":
            cur["uid"] = value.strip()
        elif name == "DTSTART":
            cur["start"] = _to_day(value)
        elif name == "DTEND":
            cur["end"] = _to_day(value)
        elif name == "SUMMARY":
            cur["summary"] = _unescape(value)
        elif name == "DESCRIPTION":
            cur["description"] = _unescape(value)[:500]
    for e in events:
        e.setdefault("uid", f"{e['start']}_{e['end']}_{e.get('summary', '')}")
    return events


def reservation_code(description: str | None) -> str | None:
    """Airbnb puts «Reservation URL: https://www.airbnb.com/hosting/reservations/details/HMABC12345» in DESCRIPTION."""
    m = re.search(r"/reservations/details/([A-Z0-9]{6,14})", description or "")
    return m[1] if m else None


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, buf = [], ""
    for ch in line:
        if len((buf + ch).encode()) > (74 if out else 75):
            out.append(buf)
            buf = ""
        buf += ch
    out.append(buf)
    return "\r\n ".join(out)


def build(name: str, events: list[dict]) -> str:
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Homes in Georgia//Daily Rent//RU", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + _esc(name)]
    for e in events:
        lines += ["BEGIN:VEVENT", "UID:" + e["uid"], "DTSTAMP:" + stamp, "DTSTART;VALUE=DATE:" + e["start"].replace("-", ""),
                  "DTEND;VALUE=DATE:" + e["end"].replace("-", ""), "SUMMARY:" + _esc(e.get("summary") or "Not available"),
                  "TRANSP:OPAQUE", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def export_feed(db: Session, token: str) -> str | None:
    if not re.fullmatch(r"[a-f0-9]{48}", token or ""):
        return None
    ch = db.scalar(select(DrChannel).where(DrChannel.export_token == token, DrChannel.deleted_at.is_(None)))
    if not ch:
        return None
    apt = db.get(DrApartment, ch.apartment_id)
    since = (R.today() - timedelta(days=60)).isoformat()
    items = [x for x in mark_echoes(occupancy(db, ch.apartment_id)) if x["end"] >= since
             and not (x["origin"] == ch.channel or (x["kind"] == "block" and (x.get("channel_id") == ch.id or x.get("echo"))))]
    return build(f"{apt.name if apt else 'Daily Rent'} · {LABEL.get(ch.channel, ch.channel)}",
                 [{"uid": f"{x['kind']}-{x['id']}@dailyrent.hge", "start": x["start"], "end": x["end"]} for x in items])


def fetch(url: str) -> str:
    check_url(url, extra_hosts())
    r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "DailyRent-iCal/1.0"})
    check_url(str(r.url), extra_hosts())  # a redirect may lead only to a platform too
    r.raise_for_status()
    if len(r.text) > 2_000_000:
        raise ValueError("календарь слишком большой")
    if "BEGIN:VCALENDAR" not in r.text:
        raise ValueError("по ссылке не календарь iCal")
    return r.text


def sync_channel(db: Session, channel_id: int, fetcher=fetch) -> dict:
    """The platform's events → this channel's blocks; vanished ones are cancelled. Errors are stored on the channel."""
    ch = get_or_404(db, DrChannel, channel_id, "channel")
    if ch.deleted_at:
        raise DomainError("not_found", "Календарь не найден", 404)
    if not ch.import_url:
        return {"events": 0, "skipped": True}
    try:
        events = parse(fetcher(ch.import_url))
    except Exception as e:  # noqa: BLE001 — the network / the platform
        ch.last_sync_at, ch.last_status, ch.last_error = now(), "error", str(e)[:200]
        db.commit()
        return {"error": str(e)}
    seen = set()
    existing = {b.external_uid: b for b in db.scalars(select(DrBlock).where(DrBlock.channel_id == ch.id))}
    for ev in events:
        if not ev.get("start") or not ev.get("end"):
            continue
        uid = hashlib.sha1(ev["uid"].encode()).hexdigest()[:16]
        seen.add(uid)
        reserved = not re.search(r"not available", ev.get("summary") or "", re.I) if ch.channel == "airbnb" else True
        vals = {"start": date.fromisoformat(ev["start"]), "end": date.fromisoformat(ev["end"]), "summary": ev.get("summary") or "",
                "reserved": reserved, "status": "active", "cancelled_at": None}
        if code := reservation_code(ev.get("description")):
            vals["reason"] = code  # Airbnb's confirmation code (HM…): the auto stay shows it (services/daily_rent_auto.py)
        b = existing.get(uid)
        if b is None:
            b = DrBlock(apartment_id=ch.apartment_id, channel_id=ch.id, source=ch.channel, external_uid=uid, **vals)
            db.add(b)
        else:
            for k, v in vals.items():
                setattr(b, k, v)
    for uid, b in existing.items():
        if uid not in seen and b.status != "cancelled":
            b.status, b.cancelled_at = "cancelled", now()
    ch.last_sync_at, ch.last_status, ch.last_error, ch.events = now(), "ok", None, len(seen)
    db.commit()
    return {"events": len(seen)}


def sync_all(db: Session, fetcher=fetch) -> list[dict]:
    return [{"id": ch.id, **sync_channel(db, ch.id, fetcher)} for ch in channels(db) if ch.import_url]


def new_conflicts(db: Session) -> list[dict]:
    """Conflicts not seen on the previous run (the first run only remembers them, as ApartFlow does)."""
    cs = analyze(db)["conflicts"]
    keys = [c["key"] for c in cs]
    row = db.get(Setting, SEEN_KEY)
    seen = None if row is None else set(json.loads(row.value or "[]"))
    if row is None:
        db.add(Setting(key=SEEN_KEY, value=json.dumps(keys)))
    else:
        row.value = json.dumps(keys)
    db.commit()
    return [] if seen is None else [c for c in cs if c["key"] not in seen]
