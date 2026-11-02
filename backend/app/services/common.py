"""Small helpers shared by services: clocks, history events, JSON-as-TEXT columns."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Event, User
from app.services.errors import not_found

TZ = ZoneInfo(settings.TZ)


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_text() -> str:
    """Legacy TEXT timestamp format used by the bot tables (bot/db.py now())."""
    return now().isoformat(timespec="seconds")


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=TZ).astimezone(timezone.utc)
    return dt


def jload(s, default=None):
    if s is None or s == "":
        return default
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except ValueError:
        return default


def jdump(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def log_event(db: Session, entity: str, entity_id, action: str, actor: User | None = None, label: str | None = None,
              **data) -> Event:
    ev = Event(entity=entity, entity_id=str(entity_id), action=action, actor_id=actor.id if actor else None,
               actor_label=label or (actor.name if actor else "system"), data=json.loads(jdump(data)), at=now())
    db.add(ev)
    return ev


def get_or_404(db: Session, model, ident, what: str | None = None, lock: bool = False):
    if lock:
        obj = db.get(model, ident, with_for_update=True, populate_existing=True)
    else:
        obj = db.get(model, ident)
    if obj is None:
        raise not_found(what or model.__name__.lower())
    return obj
