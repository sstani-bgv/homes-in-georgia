"""Viewing calendar file (A4b "📅 Добавить в календарь"): /api/public/ics/<token>."""
import datetime as _dt
from datetime import timedelta

import jwt

from app.config import settings
from app.models import Lead, Listing, User
from app.services.errors import DomainError


def ics_token(lead_id: int) -> str:
    return jwt.encode({"lead": int(lead_id), "purpose": "ics"}, settings.SECRET_KEY, algorithm="HS256")


def ics_url(lead_id: int) -> str:
    return f"{settings.PUBLIC_URL.rstrip('/')}/api/public/ics/{ics_token(lead_id)}"


def lead_from_token(token: str) -> int:
    try:
        p = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise DomainError("not_found", "calendar link is invalid")
    if p.get("purpose") != "ics":
        raise DomainError("not_found", "calendar link is invalid")
    return int(p["lead"])


def _esc(s) -> str:
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build(db, lead: Lead) -> str:
    if not lead.viewing_at:
        raise DomainError("not_found", "no viewing time yet")
    listing = db.get(Listing, lead.listing_id) if lead.listing_id else None
    agent = db.get(User, lead.agent_id) if lead.agent_id else None
    fmt = "%Y%m%dT%H%M%SZ"
    start = lead.viewing_at.astimezone(_dt.timezone.utc)
    end = start + timedelta(hours=1)
    where = ", ".join(x for x in ((listing.district if listing else None), (listing.address if listing else None)) if x)
    desc = f"Агент: {agent.name} {agent.phone or ''}".strip() if agent else ""
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Homes in Georgia//Viewing//RU", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
        f"UID:lead-{lead.id}@homesingeorgia", f"DTSTAMP:{_dt.datetime.now(_dt.timezone.utc).strftime(fmt)}",
        f"DTSTART:{start.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}", f"SUMMARY:{_esc('Просмотр квартиры · Homes in Georgia')}",
        f"LOCATION:{_esc(where)}", f"DESCRIPTION:{_esc(desc)}",
        "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY", "DESCRIPTION:Просмотр через час", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""])
