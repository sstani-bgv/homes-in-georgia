"""Small helpers shared by services: clocks, history events, JSON-as-TEXT columns."""
import json
import re
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


# Georgian cadastral code (Public Registry): dot-separated digit groups — zone.sector.quarter.parcel[.building.entrance
# .flat…], 4–9 groups — the last one may end with a letter (01.10.13.015.029.01.150ა) and rarely a /suffix. Shapes
# taken from the 2.2k codes in the old Sheets/CRM: 01.14.12.003.045, 01.10.06.011.071.01.01.085, 01.10.08.005.156.01.02.010a
CADASTRAL_RE = re.compile(r"^\d{1,3}(\.\d{1,6}){3,8}[^\W\d_]?(/\d{1,4}[^\W\d_]?)?$")
CADASTRAL_HINT = "Кадастровый код: цифры через точку, например 01.14.12.003.045 или 01.10.13.015.029.01.150ა"


def cadastral_norm(v) -> str:
    """A leading N/№/: dropped, spaces/commas/backslashes -> dots, no doubled/edge dots, a letter suffix glued to its group ('088 а' -> '088а')."""
    s = re.sub(r"^(N|№|:)\s*", "", str(v or "").strip(), flags=re.I)
    s = re.sub(r"[\s,\\]+", ".", s)
    s = re.sub(r"\.{2,}", ".", s).strip(".")
    return re.sub(r"\.(?=[^\W\d_]$)", "", s)


def clean_cadastral(v) -> str | None:
    """Normalised code, None for empty; DomainError (422) for anything that isn't a cadastral code."""
    from app.services.errors import DomainError
    s = cadastral_norm(v)
    if not s:
        return None
    if not CADASTRAL_RE.match(s):
        raise DomainError("bad_cadastral", CADASTRAL_HINT, 422)
    return s
