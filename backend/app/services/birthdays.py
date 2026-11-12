"""Team birthdays (Irakli 11-12): the «Дни рождения» widget and the daily worker job. Active users only; a Feb 29
birthday is celebrated on Feb 28 in non-leap years."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, User
from app.services import jobs
from app.services.common import TZ, now

HOUR = 9  # the morning job, Tbilisi time


def occurrence(bd: date, year: int) -> date:
    try:
        return bd.replace(year=year)
    except ValueError:  # Feb 29 in a non-leap year
        return date(year, 2, 28)


def next_birthday(bd: date, today: date) -> date:
    d = occurrence(bd, today.year)
    return d if d >= today else occurrence(bd, today.year + 1)


def upcoming(db: Session, today: date, days: int = 7) -> list[dict]:
    """Active users whose birthday falls within [today, today + days], soonest first."""
    out = []
    for u in db.scalars(select(User).where(User.is_active.is_(True), User.birth_date.is_not(None))):
        d = next_birthday(u.birth_date, today)
        if (d - today).days <= days:
            out.append({"id": u.id, "name": u.name, "photo_url": u.photo_url, "tg_id": u.tg_id, "username": u.username,
                        "date": d, "days_left": (d - today).days, "age": d.year - u.birth_date.year})
    return sorted(out, key=lambda x: (x["days_left"], x["name"]))


def ensure_job(db: Session) -> None:
    """Keep one «birthdays» job per day at HOUR:00 Tbilisi (ref = the date); a day already handled isn't redone."""
    local = now().astimezone(TZ)
    day = local.date() if local.hour < HOUR else local.date() + timedelta(days=1)
    if not db.scalar(select(Job.id).where(Job.kind == "birthdays", Job.ref == day.isoformat())):
        jobs.schedule(db, "birthdays", day.isoformat(), datetime.combine(day, time(HOUR), TZ), replace=False)
        db.commit()
