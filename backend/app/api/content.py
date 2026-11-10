"""Ticker, news, settings, sources, addresses, catalog moderation, media files, public ICS."""
import json
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import storage
from app.api.deps import ADMIN_ROLES, STAFF_ROLES, current_user, paginate, require_roles
from app.config import REPO_ROOT, settings as env
from app.db import get_db
from app.models import Address, ChannelPost, Lead, Listing, News, Source, TickerMessage, User
from app.services import ics, settings
from app.services.common import get_or_404, jload, log_event, now_text
from app.services.errors import DomainError
from app.services.properties import listing_out, media_root

router = APIRouter(tags=["content"])
public = APIRouter(tags=["public"])


# ---------------------------------------------------------------- ticker / news / addresses

class TickerIn(BaseModel):
    text: str


def ticker_out(t: TickerMessage) -> dict:
    return {"id": t.id, "text": t.text, "status": t.status, "created_at": t.created_at.isoformat()}


@router.get("/ticker")
def ticker_list(all: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):  # noqa: A002
    stmt = select(TickerMessage)
    if not all:
        stmt = stmt.where(TickerMessage.status == "active")
    return [ticker_out(t) for t in db.scalars(stmt.order_by(TickerMessage.created_at.desc()))]


@router.post("/ticker")
def ticker_add(body: TickerIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    t = TickerMessage(text=body.text.strip(), status="active")
    db.add(t)
    db.commit()
    return ticker_out(t)


@router.delete("/ticker/{tid}")
def ticker_delete(tid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    t = get_or_404(db, TickerMessage, tid, "ticker message")
    t.status = "deleted"
    db.commit()
    return ticker_out(t)


@router.get("/news")
def news_list(limit: int = Query(30, ge=0, le=500), db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    rows = db.scalars(select(News).order_by(News.date.desc().nullslast(), News.id.desc()).limit(limit))
    return [{"id": n.id, "date": n.date.isoformat() if n.date else None, "text": n.text, "message_id": n.message_id}
            for n in rows]


def channel_post_out(p: ChannelPost) -> dict:
    return {"id": p.id, "message_id": p.message_id, "source": "topic" if "t.me/c/" in (p.link or "") else "channel",
            "date": p.date.isoformat() if p.date else None, "agent_name": p.agent_name, "agent_id": p.agent_id,
            "district": p.district, "price": float(p.price) if p.price is not None else None, "rooms": p.rooms,
            "building": p.building, "is_exclusive": p.is_exclusive, "link": p.link, "status": p.status, "raw": p.raw}


@router.get("/channel-posts")
def channel_posts(hashtag: str | None = None, district: str | None = None, status: str | None = None, q: str | None = None,
                  agent_id: int | None = None, source: str | None = None, rooms: str | None = None,
                  exclusive: bool | None = None, price_min: float | None = None, price_max: float | None = None,
                  limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Agency listings parsed from the channel / work-group Listings topic (services/inbound.py). Filters: hashtag
    (#Vake or Vake, matched in the post text), district, status (active|rented|archived, comma list), q (text, agent,
    district), agent_id, source (channel|topic), rooms (2Bed|Studio), exclusive, price range."""
    stmt = select(ChannelPost)
    if hashtag:
        for tag in [t for t in hashtag.replace(",", " ").split() if t.strip("#")]:
            stmt = stmt.where(ChannelPost.raw.ilike(f"%#{tag.lstrip('#')}%"))
    if district:
        stmt = stmt.where(ChannelPost.district.ilike(district.strip()))
    if status:
        stmt = stmt.where(ChannelPost.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ChannelPost.raw.ilike(like), ChannelPost.agent_name.ilike(like), ChannelPost.district.ilike(like)))
    if agent_id:
        stmt = stmt.where(ChannelPost.agent_id == agent_id)
    if source == "topic":
        stmt = stmt.where(ChannelPost.link.like("%t.me/c/%"))
    elif source == "channel":
        stmt = stmt.where(or_(ChannelPost.link.is_(None), ChannelPost.link.not_like("%t.me/c/%")))
    if rooms:
        stmt = stmt.where(ChannelPost.rooms == rooms)
    if exclusive is not None:
        stmt = stmt.where(ChannelPost.is_exclusive.is_(exclusive))
    if price_min is not None:
        stmt = stmt.where(ChannelPost.price >= price_min)
    if price_max is not None:
        stmt = stmt.where(ChannelPost.price <= price_max)
    stmt = stmt.order_by(ChannelPost.date.desc().nullslast(), ChannelPost.id.desc())
    return paginate(db, stmt, channel_post_out, limit, offset)


@router.get("/addresses")
def addresses_list(active: int = 1, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    stmt = select(Address)
    if active:
        stmt = stmt.where(Address.is_active.is_(True))
    return [{"id": a.id, "district": a.district, "address": a.address, "ad_link": a.ad_link, "is_active": a.is_active}
            for a in db.scalars(stmt.order_by(Address.district, Address.address))]


# ---------------------------------------------------------------- settings / sources

@router.get("/settings")
def settings_get(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    return settings.all(db)


@router.put("/settings")
def settings_put(body: dict, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    settings.validate(body)
    for k, v in body.items():
        if k in settings.HIDDEN:  # written by the parser / its own page, never by this form
            continue
        settings.set(db, k, json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v, commit=False)
    log_event(db, "settings", "-", "updated", user, keys=sorted(body))
    db.commit()
    return settings.all(db)


class SourceIn(BaseModel):
    kind: str | None = None
    key: str | None = None
    name: str | None = None
    url: str | None = None
    lang: str | None = None
    options: dict | None = None
    enabled: bool | None = None
    position: int | None = None


def source_out(s: Source) -> dict:
    return {"id": s.id, "kind": s.kind, "key": s.key, "name": s.name, "url": s.url, "lang": s.lang,
            "options": jload(s.options, {}), "enabled": bool(s.enabled), "position": s.position}


@router.get("/sources")
def sources_list(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    return [source_out(s) for s in db.scalars(select(Source).order_by(Source.position, Source.id))]


def _apply_source(s: Source, data: dict):
    for k, v in data.items():
        if k == "options":
            v = json.dumps(v or {}, ensure_ascii=False)
        elif k == "enabled":
            v = int(bool(v))
        setattr(s, k, v)


@router.post("/sources")
def sources_create(body: SourceIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    data = body.model_dump(exclude_none=True)
    if data.get("kind") not in ("tg_agency", "tg_competitor", "fb_group", "ig_account") or not data.get("key"):
        raise DomainError("bad_source", "kind (tg_agency|tg_competitor|fb_group|ig_account) and key are required")
    if db.scalar(select(Source).where(Source.key == data["key"])):
        raise DomainError("conflict", "source key exists")
    s = Source(options="{}")
    _apply_source(s, data)
    db.add(s)
    db.commit()
    return source_out(s)


@router.patch("/sources/{sid}")
def sources_patch(sid: int, body: SourceIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    s = get_or_404(db, Source, sid, "source")
    _apply_source(s, body.model_dump(exclude_unset=True))
    db.commit()
    return source_out(s)


# How often the parser runs each source (bot/refresh.sh reads source_every_min through db.py export-sources) and what the
# paid ones cost: per run (Apify start / per query) and per hour of new posts, measured from the runs the parser
# recorded (source_runs, 7 days). The page multiplies them by the chosen frequency: $/month.
SCHEDULE = [("ss", "ss.ge", None), ("myhome", "myhome.ge", None), ("livo", "livo.ge", None), ("korter", "korter.ge", None),
            ("telegram", "Telegram-каналы", None), ("facebook", "Facebook-группы (Apify)", 0.002),
            ("threads", "Threads (Apify)", None)]
SCHEDULE_DEFAULTS = {"ss": 360, "myhome": 360, "livo": 360, "korter": 360, "telegram": 360, "facebook": 360, "threads": 480}
THREADS_PER_QUERY = 0.03


def _schedule(db: Session) -> list[dict]:
    every = {**SCHEDULE_DEFAULTS, **jload(settings.get(db, "source_every_min"), {})}
    runs = jload(settings.get(db, "source_runs"), {})
    queries = len((jload(settings.get(db, "threads_defaults"), {}) or {}).get("queries") or []) or 4
    out = []
    for key, label, start in SCHEDULE:
        paid = None
        if key in ("facebook", "threads"):
            rr = runs.get(key) or []
            spent = sum(r[1] for r in rr)
            if key == "threads":  # a flat price per query and run, new posts are free
                per_run = spent / len(rr) if rr else THREADS_PER_QUERY * queries
                paid = {"per_run": round(per_run, 4), "per_hour": 0}
            else:  # $0.002 a start + $0.0007 a new post: posts come at their own pace, whatever the frequency
                hours = sum(r[3] for r in rr if r[3])
                var = sum(max(0, r[1] - start) for r in rr if r[3])
                paid = {"per_run": start, "per_hour": round(var / hours, 5) if hours else None}
            paid |= {"spent_7d": round(spent, 3), "runs_7d": len(rr), "posts_7d": sum(r[2] for r in rr)}
        out.append({"key": key, "label": label, "every_min": int(every[key]), "paid": paid})
    return out


@router.get("/sources/schedule")
def schedule_get(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    return _schedule(db)


@router.put("/sources/schedule")
def schedule_put(body: dict[str, int], db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    if any(k not in SCHEDULE_DEFAULTS or not isinstance(v, int) or not 15 <= v <= 7 * 1440 for k, v in body.items()):
        raise DomainError("bad_setting", "every_min: a known source and 15..10080 minutes")
    every = {**jload(settings.get(db, "source_every_min"), {}), **body}
    settings.set(db, "source_every_min", json.dumps(every), commit=False)
    log_event(db, "settings", "-", "updated", user, keys=["source_every_min"])
    db.commit()
    return _schedule(db)


# ---------------------------------------------------------------- operator account (userbot QR login)

def _userbot(method: str, path: str, body: dict | None = None) -> dict:
    """The userbot process (USERBOT_URL) owns the operator's Telegram session; the CRM only asks it. The QR url is
    rendered here as SVG, the 2FA password passes through and is never stored."""
    import httpx
    import segno
    url = (env.USERBOT_URL or "").strip().rstrip("/")
    if not url:
        return {"configured": False}
    try:
        r = httpx.request(method, f"{url}{path}", json=body, headers={"X-Userbot-Token": env.SECRET_KEY}, timeout=35)
        st = r.json()
    except Exception:  # noqa: BLE001 — the service is down or restarting
        return {"configured": True, "reachable": False}
    if r.status_code != 200:
        return {"configured": True, "reachable": False}
    login = st.get("login") or {}
    if login.get("url"):
        login["qr_svg"] = segno.make(login["url"], error="m").svg_inline(scale=5, border=2)
    return {"configured": True, "reachable": True, **st}


@router.get("/userbot")
def userbot_status(user: User = Depends(require_roles(*ADMIN_ROLES))):
    return _userbot("GET", "/status")


class UserbotPassword(BaseModel):
    password: str


@router.post("/userbot/login")
def userbot_login(user: User = Depends(require_roles(*ADMIN_ROLES))):
    return _userbot("POST", "/login", {})


@router.post("/userbot/password")
def userbot_password(body: UserbotPassword, user: User = Depends(require_roles(*ADMIN_ROLES))):
    return _userbot("POST", "/password", {"password": body.password})


@router.post("/userbot/logout")
def userbot_logout(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    out = _userbot("POST", "/logout", {})
    log_event(db, "settings", "-", "userbot_logout", user)
    db.commit()
    return out


# ---------------------------------------------------------------- moderation (bot "Проверка")

class ModerationIn(BaseModel):
    approve: bool


@router.get("/moderation")
def moderation_list(limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"),
                    user: User = Depends(require_roles(*STAFF_ROLES))):
    stmt = select(Listing).where(Listing.status == "pending_review").order_by(Listing.first_seen.desc())
    return paginate(db, stmt, lambda x: listing_out(x, full=True), limit, offset)


@router.post("/moderation/{listing_id}")
def moderation_decide(listing_id: str, body: ModerationIn, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(require_roles(*STAFF_ROLES))):
    x = get_or_404(db, Listing, listing_id, "listing")
    x.decision = "approved" if body.approve else "rejected"
    x.status = "published" if body.approve else "rejected"
    x.status_reason = None if body.approve else "отклонено агентом"
    x.decided_by, x.decided_at = user.id, now_text()
    x.updated_at = now_text()
    log_event(db, "listing", x.id, x.decision, user)
    db.commit()
    return listing_out(x, full=True)


# ---------------------------------------------------------------- files

def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise DomainError("not_found", "file not found")
    return p


@router.get("/media/{path:path}")
def media_file(path: str, preview: bool = Query(False, alias="p"), user: User = Depends(current_user)):
    """p=1: the ~640px preview (storage.preview_key), the original while there is none."""
    if storage.is_s3():
        if path.startswith("/") or ".." in path.split("/"):
            raise DomainError("not_found", "file not found")
        pk = storage.preview_key(path)
        return RedirectResponse(storage.url(pk if preview and storage.s3_has(pk) else path), status_code=307)
    src = _safe(media_root(), path)
    if not src.is_file():
        raise DomainError("not_found", "file not found")
    p = (storage.local_preview(src, path) if preview and not path.startswith("previews/") else None) or src
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    safe = mt.startswith(("image/", "video/")) and mt != "image/svg+xml"
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
    if not safe:
        mt, headers["Content-Disposition"] = "application/octet-stream", "attachment"
    return FileResponse(p, media_type=mt, headers=headers)


PHOTO_ROOTS = [REPO_ROOT / "bot", REPO_ROOT, Path(env.MEDIA_DIR)]


@public.get("/config")
def public_config():
    return {"bot_username": env.BOT_USERNAME, "public_url": env.PUBLIC_URL}


@public.get("/listing-photos/{listing_id}/{n}")
def listing_photo(listing_id: str, n: int, preview: bool = Query(False, alias="p"), db: Session = Depends(get_db, scope="function")):
    """Photos of catalog listings (paths written by the bot's ingest, relative to bot/). Public like the channel.
    p=1: the ~640px preview, made on first request (remote http photos have none)."""
    x = get_or_404(db, Listing, listing_id, "listing")
    photos = jload(x.photos, []) or []
    if not 0 <= n < len(photos):
        raise DomainError("not_found", "photo not found")
    ref = str(photos[n])
    if ref.startswith(("http://", "https://")):
        return RedirectResponse(ref)
    for root in PHOTO_ROOTS:
        try:
            p = Path(ref) if os.path.isabs(ref) else _safe(root, ref)
        except DomainError:
            continue
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"):
            if preview:  # key as in `storage previews`: the path under the repo (bot/raw/img/<file>)
                rel = p.relative_to(REPO_ROOT.resolve()).as_posix() if p.is_relative_to(REPO_ROOT.resolve()) else f"abs/{p.name}"
                p = storage.local_preview(p, rel) or p
            return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "image/jpeg",
                                headers={"Cache-Control": "public, max-age=86400"})
    raise DomainError("not_found", "photo file missing")


@public.get("/public/ics/{token}")
def public_ics(token: str, db: Session = Depends(get_db, scope="function")):
    lead = get_or_404(db, Lead, ics.lead_from_token(token), "lead")
    return Response(ics.build(db, lead), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="viewing-{lead.id}.ics"'})
