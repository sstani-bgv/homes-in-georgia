"""Scheduler loop: every 30 s run due `jobs` rows (FOR UPDATE SKIP LOCKED). `python -m app.worker [--once]`."""
import argparse
import logging
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings as env
from app.db import SessionLocal
from app.models import Deal, Job, Lead, Property
from app.services import jobs, leads
from app.services.common import log_event, now
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
    if job.payload.get("phase") == "warning":
        notify.sla_warning(lead, stage)
        jobs.schedule(db, "lead_sla", lead.id, max(lead.sla_due_at, now()), {"stage": stage, "phase": "overdue"},
                      replace=False)
    else:
        notify.sla_overdue(lead, stage)
        log_event(db, "lead", lead.id, "sla_overdue", None, "bot", stage=stage)


@handler("call_retry")
def call_retry(db: Session, job: Job):
    p = db.get(Property, _ref_id(job))
    if not p or p.status != "call_failed":
        return "stale"
    notify.property_call_retry(p)


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


def tick(limit: int = 50) -> dict:
    """One pass: claim due jobs one at a time (each in its own transaction, row-locked, SKIP LOCKED)."""
    stats: dict[str, int] = {}
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.once:
        print(tick(limit=1000))
        return
    log.info("worker started, interval %ss, tz %s", args.interval, env.TZ)
    while True:
        try:
            st = tick()
            if st:
                log.info("jobs: %s", st)
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
