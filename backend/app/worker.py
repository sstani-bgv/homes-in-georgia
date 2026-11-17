"""Scheduler loop: every 30 s run due `jobs` rows (FOR UPDATE SKIP LOCKED). `python -m app.worker [--once]`."""
import argparse
import logging
import time
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings as env
from app.db import SessionLocal
from app.models import Deal, Job, Lead, Property
from app.services import birthdays, catalog, jobs, leads, settings
from app.services.common import TZ, aware, log_event, now
from app.telegram import notify

log = logging.getLogger("app.worker")
MAX_ATTEMPTS = 5
HANDLERS = {}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def _ref_id(job: Job) -> int:
    return int(str(job.ref).split(":")[0])


@handler("lead_sla")
def lead_sla(db: Session, job: Job):
    """~60% of the window → reminder; at the deadline → escalation to the operators group (+ superadmins)."""
    lead = db.get(Lead, _ref_id(job))
    stage = job.payload.get("stage")
    if not lead or lead.sla_stage != stage or not lead.sla_due_at:
        return "stale"
    if settings.get(db, "sla_enabled") == "0":  # turned off in Управление → Настройки after this job was planned
        return "sla off"
    if job.payload.get("phase") == "warning":
        notify.sla_warning(lead, stage)
        jobs.schedule(db, "lead_sla", lead.id, max(lead.sla_due_at, now()), {"stage": stage, "phase": "overdue"},
                      replace=False)
    else:
        notify.sla_overdue(lead, stage)
        log_event(db, "lead", lead.id, "sla_overdue", None, "bot", stage=stage)


@handler("take_idle")
def take_idle(db: Session, job: Job):
    """An object taken from «Новые объявления» with no action by its agent: a warning, then back to the feed."""
    from app.services import properties
    p = db.get(Property, _ref_id(job), with_for_update=True)
    pl = job.payload or {}
    if (not p or p.agent_id != pl.get("agent_id") or not p.taken_at
            or abs((aware(p.taken_at) - datetime.fromisoformat(pl["taken_at"])).total_seconds()) > 1):
        return "stale"  # re-taken / reassigned / released: its own job (if any) is planned
    if not properties.idle_since_take(db, p):
        return "acted"
    if properties.take_idle_minutes(db) <= 0:  # turned off in Управление → Настройки after the take
        return "off"
    if pl.get("phase") == "warn":
        notify.property_idle_warning(p, properties.TAKE_IDLE_WARN_BEFORE)
        jobs.schedule(db, "take_idle", p.id, datetime.fromisoformat(pl["return_at"]), {**pl, "phase": "return"})
        return None
    agent_id = properties.return_idle(db, p, int(pl.get("minutes") or 60))
    notify.property_idle_returned(p, agent_id, int(pl.get("minutes") or 60))


@handler("call_retry")
def call_retry(db: Session, job: Job):
    p = db.get(Property, _ref_id(job))
    if not p or p.status != "call_failed":
        return "stale"
    notify.property_call_retry(p)


@handler("owner_followup")
def owner_followup(db: Session, job: Job):
    p = db.get(Property, _ref_id(job))
    if not p or p.status not in ("taken", "call_failed", "owner_confirmed") or not p.next_call_at:
        return "stale"
    if abs((job.due_at - p.next_call_at).total_seconds()) > 60:  # the date moved; its own job is scheduled
        return "stale"
    notify.property_owner_followup(p)


@handler("viewing_reminder")
def viewing_reminder(db: Session, job: Job):
    lead = db.get(Lead, _ref_id(job))
    if not lead or lead.status != "time_chosen" or not lead.viewing_at or lead.viewing_at <= now():
        return "stale"
    expected = lead.viewing_at - timedelta(minutes=env.VIEWING_REMINDER_MIN)
    if abs((job.due_at - expected).total_seconds()) > 60:  # the viewing moved; its own reminder is scheduled
        return "stale"
    notify.viewing_reminder(lead)


@handler("post_viewing_survey")
def post_viewing_survey(db: Session, job: Job):
    if leads.mark_viewing_done(db, _ref_id(job)) is None:
        return "stale"


@handler("thinking_followup")
def thinking_followup(db: Session, job: Job):
    lead = db.get(Lead, _ref_id(job))
    if not lead or lead.status != "thinking":
        return "stale"
    notify.post_viewing_survey(lead)


@handler("t14_client")
def t14_client(db: Session, job: Job):
    deal = db.get(Deal, _ref_id(job))
    if not deal or deal.status != "confirmed" or deal.t14_status == "done":
        return "stale"
    notify.t14_client(deal)


@handler("t14_agent")
def t14_agent(db: Session, job: Job):
    deal = db.get(Deal, _ref_id(job))
    if not deal or deal.status != "confirmed" or deal.t14_status == "done":
        return "stale"
    notify.t14_agent(deal)
    deal.t14_status = "sent" if deal.t14_status == "pending" else deal.t14_status


@handler("contract_expiry")
def contract_expiry(db: Session, job: Job):
    deal = db.get(Deal, _ref_id(job))
    if not deal or deal.status != "confirmed":
        return "stale"
    notify.contract_expiry(deal, int(job.payload.get("days") or 0))


@handler("property_lease_expiry")
def property_lease_expiry(db: Session, job: Job):
    p = db.get(Property, _ref_id(job))
    days = int(job.payload.get("days") or 0)
    if not p or not p.rent_to or job.ref != f"{p.id}:{days}":
        return "stale"
    notify.property_lease_expiry(p, days)


@handler("contract_drive")
def contract_drive(db: Session, job: Job):
    """A signed contract's PDF → the Drive folder (services/contracts.drive_upload); errors retry with backoff."""
    from app.services import contracts
    return contracts.drive_upload(db, _ref_id(job))


@handler("birthdays")
def birthdays_job(db: Session, job: Job):
    """09:00 Tbilisi: today's birthdays → the team, tomorrow's → a reminder to admins (services/birthdays.py)."""
    day = date.fromisoformat(job.ref)
    if day != now().astimezone(TZ).date():
        return "stale"  # the worker was down that morning: no belated greetings
    if today := birthdays.upcoming(db, day, 0):
        notify.birthdays_today(today)
    if tomorrow := [x for x in birthdays.upcoming(db, day, 1) if x["days_left"] == 1]:
        notify.birthdays_tomorrow(tomorrow)


@handler("contract_esign_pdf")
def contract_esign_pdf(db: Session, job: Job):
    """Both parties signed but printing the signed PDF failed in the request: print it again (services/esign.finalize)."""
    from app.services import esign
    return esign.finalize(db, _ref_id(job))


@handler("nda_pdf")
def nda_pdf(db: Session, job: Job):
    """An agent signed his NDA but printing the PDF failed in the request: print it again (services/nda.finalize)."""
    from app.services import nda
    return nda.finalize(db, _ref_id(job))


@handler("nda_drive")
def nda_drive(db: Session, job: Job):
    """A signed NDA → the Drive folder of contracts (services/nda.drive_upload); errors retry with backoff."""
    from app.services import nda
    return nda.drive_upload(db, _ref_id(job))


@handler("tg_unpost")
def tg_unpost(db: Session, job: Job):
    """Rented («Сдано» / «Сдал сам»): our Telegram posts of the object are deleted (properties.unpost)."""
    from app.services import properties
    p = db.get(Property, _ref_id(job))
    if p is None or not (p.status == "rented" or p.archive_reason in properties.RENTED_REASONS):
        return "noop"  # back on the market before the worker came to it
    r = properties.unpost(db, p, None)
    return "ok" if not r["failed"] else f"not deleted: {', '.join(r['failed'])}"[:500]


@handler("dup_purge")
def dup_purge(db: Session, job: Job):
    """«Дубль»: the archived duplicate is deleted with its posts and files (properties.purge_duplicate)."""
    from app.services import properties
    p = db.get(Property, _ref_id(job))
    if p is None or p.archive_reason != "duplicate":
        return "noop"
    return properties.purge_duplicate(db, p)


@handler("tg_video")
def tg_video(db: Session, job: Job):
    """A video just uploaded (ref = its storage key) -> its Telegram-ready copy, so publishing does not run ffmpeg."""
    import tempfile
    from app.telegram import notify
    with tempfile.TemporaryDirectory() as tmp:
        notify._tg_video(job.ref, tmp)


@handler("review_batch")
def review_batch(db: Session, job: Job):
    return "noop"  # the bot's own review loop handles catalog moderation cards


def run_job(db: Session, job: Job) -> str:
    fn = HANDLERS.get(job.kind)
    if fn is None:
        job.done_at, job.last_error = now(), f"unknown kind {job.kind}"
        db.commit()
        return "unknown"
    job_id, kind, ref = job.id, job.kind, job.ref
    # Claim inside the handler's transaction: if the handler commits midway (e.g. leads.mark_viewing_done) the row
    # lock is released, but done_at is committed with it, so another worker can never pick the job up again.
    job.done_at, job.attempts, job.last_error = now(), (job.attempts or 0) + 1, None
    db.flush()
    try:
        res = fn(db, job) or "ok"
        job = db.get(Job, job_id)
        job.last_error = None if res == "ok" else res
        db.commit()
        return res
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("job %s %s:%s failed", job_id, kind, ref)
        job = db.get(Job, job_id, with_for_update=True, populate_existing=True)
        if job.done_at is None:  # claim rolled back with the handler → count the attempt again
            job.attempts = (job.attempts or 0) + 1
        job.last_error = f"{type(e).__name__}: {e}"[:500]
        if job.attempts >= MAX_ATTEMPTS:
            job.done_at = job.done_at or now()
        else:
            job.done_at = None
            job.due_at = now() + timedelta(minutes=2 ** job.attempts)
        db.commit()
        return "error"


def daily_rent_ical_tick():
    """«Посуточная аренда»: fetch every Airbnb / Booking calendar, then tell the Daily Rent people about new double
    bookings (ApartFlow's syncTick + notifyConflicts)."""
    from app.models import DrApartment
    from app.services import daily_rent_ical
    try:
        with SessionLocal() as db:
            if r := daily_rent_ical.sync_all(db):
                log.info("daily rent ical: %s", r)
            if fresh := daily_rent_ical.new_conflicts(db):
                notify.daily_rent_conflicts(fresh, {a.id: a.name for a in db.query(DrApartment)})
    except Exception:  # noqa: BLE001
        log.exception("daily rent ical sync failed")


def car_import_tick():
    """«Авто»: the car channels' new / edited / sold posts -> the CRM's cars -> the site (services/car_import.py)."""
    from app.services import car_import
    try:
        with SessionLocal() as db:
            r = car_import.run(db)
            if any(v.get(k) for v in r.values() for k in ("created", "updated", "moved", "archived", "error")):
                log.info("car import: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("car import failed")


def tick(limit: int = 50) -> dict:
    """One pass: claim due jobs one at a time (each in its own transaction, row-locked, SKIP LOCKED)."""
    stats: dict[str, int] = {}
    with SessionLocal() as db:
        birthdays.ensure_job(db)
    for _ in range(limit):
        with SessionLocal() as db:
            due = jobs.due(db, limit=1)
            if not due:
                break
            res = run_job(db, due[0])
            stats[res] = stats.get(res, 0) + 1
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one pass and exit")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()
    from app import logs
    logs.setup("worker")
    if args.once:
        print(tick(limit=1000))
        return
    log.info("worker started, interval %ss, tz %s", args.interval, env.TZ)
    from app.telegram import login_bot
    login_bot.start_thread()  # staff invites: /start inv_<token> in the CRM sign-in bot
    last_ical = time.time() - env.DAILY_RENT_ICAL_MINUTES * 60 + 20  # first fetch 20 s after the start, as ApartFlow
    last_cars = time.time() - env.CAR_IMPORT_MINUTES * 60 + 60  # first read a minute after the start
    last_avatars = time.time() - 23 * 3600  # first pass an hour after the start, then daily
    while True:
        from app import keys
        keys.refresh()  # Управление → Ключи
        try:
            st = tick()
            if st:
                log.info("jobs: %s", st)
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        if env.DAILY_RENT_ICAL_MINUTES > 0 and time.time() - last_ical >= env.DAILY_RENT_ICAL_MINUTES * 60:
            last_ical = time.time()
            daily_rent_ical_tick()
        if env.CAR_IMPORT_MINUTES > 0 and time.time() - last_cars >= env.CAR_IMPORT_MINUTES * 60:
            last_cars = time.time()
            car_import_tick()
        if time.time() - last_avatars >= 24 * 3600:  # team members still without a stored photo (telegram/avatars.py)
            last_avatars = time.time()
            try:
                from app.telegram import avatars
                avatars.run(out=log.info)
            except Exception:  # noqa: BLE001
                log.exception("avatar sync failed")
        try:  # the bot's catalog follows the objects (services/catalog.py)
            with SessionLocal() as db:
                if n := catalog.sync_all(db):
                    log.info("catalog: %s rows changed", n)
        except Exception:  # noqa: BLE001
            log.exception("catalog sync failed")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
