"""CRM side of cars (the «Авто» section). Public side: services/site_feed.py. Sale objects are properties now
(post_fields.deal = Sale, migration 0021).

An agent adds and edits them by hand; photos / video are keys in the row (offers/c<id>/<uuid>.<ext>). «Опубликовать»
goes to the checked targets, as for rent objects: the site (status published) and / or Telegram (the post)."""
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.config import settings as env
from app.api.deps import paginate
from app.models import Car, User, can_cars
from app.services import properties as P
from app.services import car_post, jobs, site_feed
from app.services.common import get_or_404, log_event, now
from app.services.errors import DomainError, forbidden
from app.telegram import notify
from app.telegram.client import conf

log = logging.getLogger(__name__)

MODELS = {"car": Car}
MAX_PHOTOS, MAX_VIDEOS = P.MAX_MEDIA["photo"], P.MAX_MEDIA["video"]
# what the agent's form may write; status / photos / video / site_id / published_at only through the calls below
FIELDS = {
    "car": ("deal", "title", "period", "deposit", "year", "gearbox", "fuel", "body", "seats", "mileage", "pickup", "lat", "lng",
            "price", "currency", "description", "is_exclusive", "post_url"),
}


def clean_post_fields(old: dict | None, new) -> dict:
    """The generator's fields (car_post.ALL_FIELDS) merged over the stored ones; None / "" clears a field."""
    out = dict(old or {})
    for k, v in (new or {}).items() if isinstance(new, dict) else ():
        if k not in car_post.ALL_FIELDS:
            continue
        if v in (None, "", []):
            out.pop(k, None)
        else:
            out[k] = v
    return out


def _gearbox_of(data: dict) -> None:
    """A sale's exact transmission (DCT, CVT...) sets the column the site filters on (auto | manual)."""
    t = (data.get("post_fields") or {}).get("transmission") if isinstance(data.get("post_fields"), dict) else None
    if t in car_post.TRANSMISSION and "gearbox" not in data:
        data["gearbox"] = car_post.TRANSMISSION[t]


def kind_of(model) -> str:
    return "car"


def out(o) -> dict:
    d = {c.name: getattr(o, c.name) for c in o.__table__.columns}
    return {**d, "item_id": site_feed.ident_of(o), "public": o.status == "published" and bool(o.photos)}


def _op(actor: User | None) -> bool:
    """Operator rights over the cars: admins and the «Оператор авто» (Stanislav 11-16; models.can_cars)."""
    return can_cars(actor)


def listing(db: Session, model, status: str | None, q: str | None, limit: int, offset: int) -> dict:
    stmt = select(model).order_by(model.id.desc())
    if status:
        stmt = stmt.where(model.status == status)
    if q:
        stmt = stmt.where(model.title.ilike(f"%{q.strip()}%"))
    return paginate(db, stmt, out, limit, offset)


def _changed(db: Session, o, actor: User | None, action: str, **data):
    log_event(db, kind_of(type(o)) + "_offer", o.id, action, actor, **data)
    db.commit()
    site_feed.clear_cache()  # the site shows the edit at once, not after the 60 s


def create(db: Session, model, data: dict, actor: User) -> object:
    allowed = FIELDS[kind_of(model)]
    _gearbox_of(data)
    o = model(**{k: v for k, v in data.items() if k in allowed}, agent_id=actor.id, status="draft",
              post_fields=clean_post_fields({}, data.get("post_fields")))
    if (aid := data.get("agent_id")) and _op(actor):
        o.agent_id = aid
    db.add(o)
    db.flush()
    _changed(db, o, actor, "created")
    return o


def update(db: Session, model, oid: int, data: dict, actor: User) -> object:
    o = get_or_404(db, model, oid, "object", lock=True)
    _gearbox_of(data)
    for k, v in data.items():
        if k in FIELDS[kind_of(model)] or (k == "agent_id" and _op(actor)):
            setattr(o, k, v)
    if "post_fields" in data:
        o.post_fields = clean_post_fields(o.post_fields, data["post_fields"])
    _changed(db, o, actor, "updated", fields=sorted(data))
    return o


def set_status(db: Session, model, oid: int, status: str, actor: User) -> object:
    o = get_or_404(db, model, oid, "object", lock=True)
    if status == "published" and not o.photos:
        raise DomainError("no_media", "Добавьте хотя бы одно фото перед публикацией")
    if status == "published" and not o.published_at:
        o.published_at = datetime.now(timezone.utc)
    o.status = status
    _changed(db, o, actor, status)
    return o


def upload(db: Session, model, oid: int, files: list[tuple[str, bytes]], actor: User) -> tuple[object, list[str]]:
    o = get_or_404(db, model, oid, "object", lock=True)
    photos, n_video = list(o.photos), 1 if o.video else 0
    for name, content in files:  # check everything first: a rejected batch stores nothing
        ext = os.path.splitext(name or "")[1].lower() or ".jpg"
        if ext not in P.PHOTO_EXT | P.VIDEO_EXT:
            raise DomainError("bad_file", f"Можно загружать только фото и видео: {name}")
        if len(content) > P.MAX_UPLOAD:
            raise DomainError("too_large", f"Файл слишком большой: {name}")
        if ext in P.VIDEO_EXT:
            n_video += 1
        else:
            photos.append(name)
        if n_video > MAX_VIDEOS or len(photos) > MAX_PHOTOS:
            raise DomainError("media_limit", f"Максимум {MAX_PHOTOS} фото и {MAX_VIDEOS} видео. Удалите лишнее, чтобы добавить новое", 422)
    keys, photos = [], list(o.photos)
    for name, content in files:
        ext = os.path.splitext(name or "")[1].lower() or ".jpg"
        key = f"offers/{site_feed.ident_of(o)}/{uuid.uuid4().hex}{ext}"
        storage.put(key, content)
        storage.put_preview(key, content)
        if ext in P.VIDEO_EXT:
            o.video = key
            jobs.schedule(db, "tg_video", key, now())  # the Telegram copy (notify._tg_video), not in the publish request
        else:
            photos.append(key)
        keys.append(key)
    o.photos = photos
    _changed(db, o, actor, "media_added", files=len(files))
    return o, keys


def reorder(db: Session, model, oid: int, keys: list[str], actor: User) -> object:
    o = get_or_404(db, model, oid, "object", lock=True)
    if sorted(keys or []) != sorted(o.photos):
        raise DomainError("bad_order", "Порядок фото устарел — обновите страницу", 409)
    o.photos = list(keys)
    _changed(db, o, actor, "media_reordered")
    return o


def delete_file(db: Session, model, oid: int, key: str, actor: User) -> object:
    o = get_or_404(db, model, oid, "object", lock=True)
    if key == o.video:
        o.video = None
    elif key in o.photos:
        o.photos = [k for k in o.photos if k != key]
    else:
        raise DomainError("not_found", "media not found")
    try:
        storage.delete(key)
    except Exception:
        pass  # a stray file in the bucket is harmless; the row is the truth
    _changed(db, o, actor, "media_deleted", key=key)
    return o


# ---------------------------------------------------------------- the Telegram post (as rent objects: notify.publish_post)

def car_post_text(db: Session, c: Car) -> str:
    """The car's post in its channel's format (services/car_post.py)."""
    return car_post.build(c)


def preview_text(data: dict) -> str:
    """The post of an unsaved form (POST /offers/car/preview): the same generator over a stand-in car."""
    _gearbox_of(data)
    cols = {c.name: None for c in Car.__table__.columns}
    car = SimpleNamespace(**{**cols, "currency": "USD", "deal": "rent", "is_exclusive": False,
                             **{k: v for k, v in data.items() if k in FIELDS["car"]},
                             "post_fields": clean_post_fields({}, data.get("post_fields"))})
    car.title = car.title or ""
    return car_post.build(car)


def car_channel(o) -> str:
    """Where a car's post goes: CAR_SALE_CHAT_ID / CAR_RENT_CHAT_ID (a numeric id or @username), else the public car
    channel itself (@CarsSaleInGeorgia / @CarsRentInGeorgia). The bot must be an admin of that channel."""
    key = "CAR_SALE_CHAT_ID" if o.deal == "sale" else "CAR_RENT_CHAT_ID"
    return conf(key, "").strip() or "@" + car_post.CHANNELS["sale" if o.deal == "sale" else "rent"]


def post_text(db: Session, o) -> str:
    return car_post_text(db, o)


def publish(db: Session, model, oid: int, actor: User, text: str | None = None, tg: bool = True, site: bool = True,
            keys: list[str] | None = None) -> object:
    """«Опубликовать» to the checked targets: the site (status published) and / or Telegram — the post (as edited,
    default post_text) with the photos and the video through the rent objects' notify.publish_post (watermarked album +
    originals); Telegram for operators only, as for rent objects. Without BOT_TOKEN in dev the post is a dry run (no
    link); a Telegram failure changes nothing (the site is not touched either)."""
    if not (tg or site):
        raise DomainError("no_target", "Отметьте, куда публиковать: сайт или Telegram")
    if tg and not _op(actor):
        raise forbidden("only operators publish to Telegram")
    o = get_or_404(db, model, oid, "object", lock=True)
    if not o.photos:
        raise DomainError("no_media", "Добавьте хотя бы одно фото перед публикацией")
    data: dict = {"site": site, "tg": tg}
    own = o.photos + ([o.video] if o.video else [])
    if keys is not None and not set(keys) <= set(own):
        raise DomainError("not_found", "media not found")
    if tg:
        post = SimpleNamespace(id=None, chat=car_channel(o), address=o.title, district=None, price=o.price, description=o.description,
                               owner_id=None, post_text=(text or "").strip() or post_text(db, o),
                               post_media_keys=[("video" if k == o.video else "photo", k) for k in (own if keys is None else keys)])
        try:
            ref = notify.publish_post(post)
        except Exception:  # noqa: BLE001
            log.exception("notify.publish_post failed")
            ref = None
        dry_run = ref is None and not env.BOT_TOKEN and env.APP_ENV == "dev"
        if ref is None and not dry_run:
            raise DomainError("publish_failed", "Telegram не принял пост", 502)
        if ref:
            o.post_url = ref[1] or o.post_url
            # the importer meets this post in the channel and knows it is this car (services/car_import.py)
            if m := re.search(r"t\.me/(?:c/)?([^/]+)/(\d+)", ref[1] or ""):
                if not db.scalars(select(Car).where(Car.tg_channel == m[1], Car.tg_message_id == int(m[2]), Car.id != o.id)).first():
                    o.tg_channel, o.tg_message_id = m[1], int(m[2])
        data.update(post_url=o.post_url, **({"dry_run": True} if dry_run else {}))
    if site:
        o.status = "published"
        o.published_at = o.published_at or datetime.now(timezone.utc)
    _changed(db, o, actor, "published", **data)
    return o
