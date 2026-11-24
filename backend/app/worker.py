"""Scheduler loop: every 30 s run due `jobs` rows (FOR UPDATE SKIP LOCKED). `python -m app.worker [--once]`."""
import argparse
import logging
import threading
import time
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.config import settings as env
from app.db import SessionLocal
from app.models import Deal, Job, Lead, Property
from app.services import birthdays, catalog, jobs, leads, settings
from app.services.common import TZ, aware, log_event, now
from app.telegram import client as tg
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


def _planned(job: Job) -> datetime:
    """When the job was planned for: a retry moves due_at (backoff), the «did the date move?» checks use this."""
    return getattr(job, "_planned", None) or job.due_at


def _best_effort(fn, *args):
    """A notice in a job that also moves state on (plans the next phase, returns an object): a transient delivery
    failure is recorded (the job ends «undelivered») instead of rolling that state back for a retry."""
    try:
        fn(*args)
    except tg.DeliveryError:
        pass


@handler("channel_dups")
def channel_dups(db: Session, job: Job):
    """Right after a channel post: the same flat already posted? → the operator confirms deleting (services/channel_dups)."""
    from app.services import channel_dups as CD
    CD.check(db, _ref_id(job))


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
        _best_effort(notify.sla_warning, lead, stage)  # a late warning is useless; the overdue phase must be planned
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
    n = properties.take_idle_minutes(db)
    if n <= 0:  # turned off in Управление → Настройки after the take
        return "off"
    if n != int(pl.get("minutes") or 60):  # the setting changed after the take: the new span from the take
        properties.plan_take_idle(db, p)
        return "replanned"
    if pl.get("phase") == "warn":
        _best_effort(notify.property_idle_warning, p, properties.take_idle_warn_before(n))
        jobs.schedule(db, "take_idle", p.id, datetime.fromisoformat(pl["return_at"]), {**pl, "phase": "return"})
        return None
    agent_id = properties.return_idle(db, p, n)
    _best_effort(notify.property_idle_returned, p, agent_id, n)


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
    if abs((_planned(job) - p.next_call_at).total_seconds()) > 60:  # the date moved; its own job is scheduled
        return "stale"
    notify.property_owner_followup(p)


@handler("viewing_reminder")
def viewing_reminder(db: Session, job: Job):
    lead = db.get(Lead, _ref_id(job))
    if not lead or lead.status != "time_chosen" or not lead.viewing_at or lead.viewing_at <= now():
        return "stale"
    expected = lead.viewing_at - timedelta(minutes=env.VIEWING_REMINDER_MIN)
    if abs((_planned(job) - expected).total_seconds()) > 60:  # the viewing moved; its own reminder is scheduled
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


@handler("deal_due")
def deal_due(db: Session, job: Job):
    """24 h after both parties signed (services/contracts.start_deal_due): no deal yet → ⛔ to the agent and the operator."""
    from app.models import Contract
    from app.services import contracts as C
    c = db.get(Contract, _ref_id(job))
    if not c or not c.deal_due_at or not C.owes_deal(db, c):
        return "stale"
    notify.deal_overdue(c, C.deal_owers(db, c))
    log_event(db, "contract", c.id, C.DEAL_OVERDUE, None, "bot")


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


@handler("sign_doc_pdf")
def sign_doc_pdf(db: Session, job: Job):
    """Every signer signed a «Документ на подпись» but printing the PDF failed in the request (services/sign_docs.finalize)."""
    from app.services import sign_docs
    return sign_docs.finalize(db, _ref_id(job))


@handler("sign_doc_drive")
def sign_doc_drive(db: Session, job: Job):
    """A signed «Документ на подпись» → the Drive folder of contracts (services/sign_docs.drive_upload)."""
    from app.services import sign_docs
    return sign_docs.drive_upload(db, _ref_id(job))


@handler("tg_unpost")
def tg_unpost(db: Session, job: Job):
    """Rented («Сдано» / «Сдал сам») or archived for any reason (pause, «Снять с публикации», …): our Telegram posts are deleted
    (properties.unpost); a rented one then gets the channel's «#FlatRented» announcement (properties.rented_post)."""
    from app.services import properties
    p = db.get(Property, _ref_id(job))
    rented = p is not None and (p.status == "rented" or (p.status == "archived" and p.archive_reason in properties.RENTED_REASONS))
    if p is None or not (rented or p.status == "archived"):
        return "noop"  # back on the market before the worker came to it
    r = properties.unpost(db, p, None)
    res = "ok" if not r["failed"] else f"not deleted: {', '.join(r['failed'])}"[:500]
    if rented and (sent := properties.rented_post(db, p)) not in ("ok", "never posted"):
        res = f"{res}; rented post: {sent}"
    return res


@handler("tg_originals")
def tg_originals(db: Session, job: Job):
    """After a publication: the photos without the watermark into the originals chat (properties.send_originals)."""
    from app.services import properties
    p = db.get(Property, _ref_id(job))
    return properties.send_originals(db, p, (job.payload or {}).get("text")) if p is not None else "noop"


@handler("tg_repost")
def tg_repost(db: Session, job: Job):
    """A published object's post shows changed fields («Правка», «% Скидка») or it is back from a pause: posted again
    (properties.publish: the new post first, then the old ones deleted)."""
    from app.services import properties
    from app.services.errors import DomainError
    p = db.get(Property, _ref_id(job))
    if p is None or p.status != "published":
        return "noop"
    try:
        properties.publish(db, p.id, None, tg=True, site=not p.site_hidden)
    except DomainError as e:  # too few photos, an exclusive still advertised elsewhere: the same next time
        if e.code == "publish_failed":
            raise  # Telegram refused: try again later
        db.rollback()
        return f"not reposted: {e}"[:500]
    return "ok"


@handler("dup_purge")
def dup_purge(db: Session, job: Job):
    """«Дубль»: the archived duplicate is deleted with its posts and files (properties.purge_duplicate)."""
    from app.services import properties
    p = db.get(Property, _ref_id(job))
    if p is None or p.archive_reason != "duplicate":
        return "noop"
    return properties.purge_duplicate(db, p)


@handler("dup_scan")
def dup_scan(db: Session, job: Job):
    """An object was published, got a link («Сравнить по ссылке»), «Найти копии» was pressed (the sites are searched
    then), became exclusive / got its contract / took a «Дубль» in, new ads look like it: search its duplicates
    (similar.scan_exclusive)."""
    from app.services import myhome_copies, properties, similar
    trigger = (job.payload or {}).get("trigger") or "exclusive"
    # the sites (myhome, ss.ge, korter) only on publishing, a pasted link and «Найти копии» (Stanislav 11-23: no daily
    # pass); the other triggers search what the base has
    if trigger in properties.SITE_TRIGGERS:
        try:
            myhome_copies.run(db, _ref_id(job))
        except Exception:  # noqa: BLE001 — myhome down / changed: the scan of what we have still runs
            db.rollback()
            log.warning("myhome copies of #%s failed", _ref_id(job), exc_info=True)
    r = similar.scan_exclusive(db, _ref_id(job), (job.payload or {}).get("trigger") or "exclusive")
    return "ok" if r is None else f"found {r['n']}, strong {len(r['strong'])}"


@handler("myhome_copies")
def myhome_copies_job(db: Session, job: Job):
    """Old queued «Найти копии» jobs: the same as a dup_scan «manual» now."""
    job.payload = {"trigger": "manual"}
    return dup_scan(db, job)


@handler("dup_recheck")
def dup_recheck(db: Session, job: Job):
    """§12 (David 11-19): 24 h after an exclusive without the e-signature showed duplicates — search again; clean with
    the contract in → approved, else the operator decides (similar.no_esign_gate; a decided request: a no-op)."""
    from app.services import similar
    r = similar.scan_exclusive(db, _ref_id(job), "dup_recheck")
    return (r or {}).get("gate") or "stale"


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
    # The retry bookkeeping of an earlier failed attempt (below) lives in the payload; the handler sees its own keys only
    pl = dict(job.payload or {})
    replay, planned = pl.pop("_tg_sent", None), pl.pop("_due_at", None) or job.due_at.isoformat()
    if len(pl) != len(job.payload or {}):
        job.payload = pl
    job._planned = datetime.fromisoformat(planned)
    # Claim inside the handler's transaction: if the handler commits midway (e.g. leads.mark_viewing_done) the row
    # lock is released, but done_at is committed with it, so another worker can never pick the job up again.
    job.done_at, job.attempts, job.last_error = now(), (job.attempts or 0) + 1, None
    db.flush()
    d = None
    try:
        # Transient send failures raise tg.DeliveryError (→ except: retry with backoff). Permanent ones (blocked
        # the bot, chat not found) and those a service swallowed after its commit only end the job «undelivered»:
        # the same send would fail again, or the handler would find its work done and skip.
        with tg.delivery(replay) as d:
            res = fn(db, job) or "ok"
        job = db.get(Job, job_id)
        if job.done_at is None:
            # the handler rolled the claim back with its own work (leads.mark_viewing_done on a lead with nothing to
            # do): claim it again, or the same job comes first in every pass (11-20..11-22: the whole queue stood)
            job.done_at, job.attempts = now(), (job.attempts or 0) + 1
        if d.failed:
            log.warning("job %s %s:%s: %s", job_id, kind, ref, "; ".join(map(str, d.failed)))
            job.last_error = ((f"{res}; " if res != "ok" else "") + "not delivered: " + "; ".join(map(str, d.failed)))[:500]
            res = "undelivered"
        else:
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
            sent = {**(replay or {}), **(d.sent if d else {})}  # what already went out is not sent again
            job.payload = {**{k: v for k, v in (job.payload or {}).items() if k not in ("_tg_sent", "_due_at")},
                           "_due_at": planned, **({"_tg_sent": sent} if sent else {})}
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
            from app.services import daily_rent_auto
            if (st := daily_rent_auto.auto_stays(db)) and any(st.values()):
                log.info("daily rent auto stays: %s", st)
            if fresh := daily_rent_ical.new_conflicts(db):
                notify.daily_rent_conflicts(fresh, {a.id: a.name for a in db.query(DrApartment)})
    except Exception:  # noqa: BLE001
        log.exception("daily rent ical sync failed")


def daily_rent_morning_tick():
    """«Посуточная аренда», once a day from 09:00 Tbilisi: cleanings for today's check-outs, then the «Сегодня» digest to the
    managers; on the 1st (or the first day after) the past month's owner reports (services/daily_rent_auto.py)."""
    from app.services import daily_rent_auto as A
    try:
        with SessionLocal() as db:
            if A.digest_due(db):
                if n := A.auto_cleanings(db):
                    log.info("daily rent: %s cleanings for today's check-outs", n)
                notify.daily_rent_digest(A.digest_text(A.today(db)))
                A.mark_digest(db)
            if month := A.reports_due(db):
                if reps := A.owner_reports(db, month):
                    notify.daily_rent_owner_reports(reps, month)
                A.mark_reports(db, month)
    except Exception:  # noqa: BLE001
        log.exception("daily rent morning failed")


_child = {"proc": None, "after": 0.0, "npz": None}


def photo_vectors_tick() -> None:
    """Per-photo vectors (app/embeddings.py photos) in a child process, one at a time: started when an object photo or
    an old enough ad has none, or the bot's vectors.npz changed (copies). A failed child waits half an hour. Off:
    PHOTO_VECTORS=0 in .env."""
    import importlib.util
    import os
    import subprocess
    import sys
    from app import embeddings
    if os.environ.get("PHOTO_VECTORS", "1") == "0":
        return
    p = _child["proc"]
    if p is not None:
        if p.poll() is None:
            return
        _child["proc"] = None
        if p.returncode:
            log.warning("photo vectors: child exited %s, next try in 30 min", p.returncode)
            _child["after"] = time.time() + 1800
    if time.time() < _child["after"]:
        return
    f = embeddings.npz_path()
    mtime = os.path.getmtime(f) if os.path.exists(f) else None
    with SessionLocal() as db:
        due = (importlib.util.find_spec("torch") is not None and embeddings.photos_due(db)) or \
            (mtime != _child["npz"] and mtime is not None and bool(embeddings.ads_pending(db, limit=1)))
    _child["npz"] = mtime
    if due:
        _child["proc"] = subprocess.Popen([sys.executable, "-m", "app.embeddings", "photos"],
                                          cwd=str(REPO_ROOT / "backend"))


# The sites' copies search (myhome / ss.ge / korter pages and photos) runs minutes: in its own thread, so a post's
# repost, a takedown or a notification never waits behind it (David 11-24: a price change sat 10 min in the queue)
SLOW = ("dup_scan", "myhome_copies", "dup_recheck", "channel_dups")  # all of them: similar.find over the whole base, one thread


def tick(limit: int = 50, kinds: tuple = (), skip: tuple = ()) -> dict:
    """One pass: claim due jobs one at a time (each in its own transaction, row-locked, SKIP LOCKED)."""
    stats: dict[str, int] = {}
    if not kinds:
        with SessionLocal() as db:
            birthdays.ensure_job(db)
    for _ in range(limit):
        with SessionLocal() as db:
            due = jobs.due(db, limit=1, kinds=kinds, skip=() if kinds else skip)
            if not due:
                break
            res = run_job(db, due[0])
            stats[res] = stats.get(res, 0) + 1
    return stats


def slow_lane(interval: int = 10):
    """worker.SLOW jobs, one at a time, beside the main loop."""
    while True:
        try:
            if st := tick(limit=20, kinds=SLOW):
                log.info("slow jobs: %s", st)
        except Exception:  # noqa: BLE001
            log.exception("slow tick failed")
        time.sleep(interval)


def channel_import_pass():
    try:
        from app.services import channel_import
        with SessionLocal() as db:
            channel_import.run(db)
    except Exception:  # noqa: BLE001
        log.exception("channel import failed")


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
    threading.Thread(target=slow_lane, daemon=True).start()
    last_ical = time.time() - env.DAILY_RENT_ICAL_MINUTES * 60 + 20  # first fetch 20 s after the start, as ApartFlow
    last_avatars = time.time() - 23 * 3600  # first pass an hour after the start, then daily
    last_posts = time.time() - 480  # «сверка с каналом» (services/post_sync.py): 2 min after the start, then every 10 min
    # (David 11-24: a post deleted by hand in the channel → the CRM and the bot know within minutes, not an hour)
    importer = None
    last_import = time.time() - 25 * 60  # channel posts → objects (services/channel_import.py): 5 min after the start, every 30 min
    while True:
        from app import keys
        keys.refresh()  # Управление → Ключи
        try:
            st = tick(skip=SLOW)  # the slow ones: slow_lane
            if st:
                log.info("jobs: %s", st)
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        if env.DAILY_RENT_ICAL_MINUTES > 0 and time.time() - last_ical >= env.DAILY_RENT_ICAL_MINUTES * 60:
            last_ical = time.time()
            daily_rent_ical_tick()
        daily_rent_morning_tick()
        if time.time() - last_avatars >= 24 * 3600:  # team members still without a stored photo (telegram/avatars.py)
            last_avatars = time.time()
            try:
                from app.telegram import avatars
                avatars.run(out=log.info)
            except Exception:  # noqa: BLE001
                log.exception("avatar sync failed")
        if time.time() - last_posts >= 600:
            last_posts = time.time()
            try:
                from app.services import post_sync
                with SessionLocal() as db:
                    post_sync.run(db)
            except Exception:  # noqa: BLE001
                log.exception("post sync failed")
        if time.time() - last_import >= 30 * 60 and not (importer and importer.is_alive()):
            last_import = time.time()  # a thread: the first pass downloads two weeks of albums, the jobs go on meanwhile
            importer = threading.Thread(target=channel_import_pass, daemon=True)
            importer.start()
        try:  # dHash of the objects' photos uploaded before migration 0030, a batch per pass (services/matching.py)
            from app.services import matching
            with SessionLocal() as db:
                matching.backfill_media_hashes(db, batch=200, max_batches=1, out=log.debug)
        except Exception:  # noqa: BLE001
            log.exception("photo hashes failed")
        try:  # per-photo vectors for «похожие фото (другой ракурс)» (services/similar.py)
            photo_vectors_tick()
        except Exception:  # noqa: BLE001
            log.exception("photo vectors failed")
        try:  # the bot's catalog follows the objects (services/catalog.py)
            with SessionLocal() as db:
                if n := catalog.sync_all(db):
                    log.info("catalog: %s rows changed", n)
        except Exception:  # noqa: BLE001
            log.exception("catalog sync failed")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
