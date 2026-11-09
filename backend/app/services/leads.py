"""Funnel 2: client like → operator → agent → slots → viewing → outcome → deal (docs/ARCHITECTURE.md §4.3, §5)."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import (FIELD, STAFF, Client, Event, Lead, LeadEvent, LeadSlot, Listing, Owner, Property, Reaction, User)
from app.services import jobs, settings
from app.services.common import aware, get_or_404, jload, log_event, now, now_text
from app.services.dedup import extract_phones
from app.services.errors import DomainError, forbidden
from app.telegram import notify, texts

log = logging.getLogger(__name__)

ACTIVE = ("new", "operator_taken", "agent_assigned", "slots_sent", "time_chosen", "viewing_done", "thinking",
          "deal_pending")
DEAL_STAGES = ("time_chosen", "viewing_done", "thinking", "deal_pending")  # a deal may be filled from these
BOARD_COLUMNS = ACTIVE + ("rented", "refused")
OUTCOMES = {"client": ("rented", "refused", "thinking"), "agent": ("rented", "refused", "thinking", "reschedule")}
REFUSAL_REASONS = ("price", "location", "condition", "other")


def _notify(name: str, *args):
    """notify.* must never raise; guard anyway so a Telegram bug never breaks a committed state change."""
    try:
        return getattr(notify, name)(*args)
    except Exception:  # noqa: BLE001
        log.exception("notify.%s failed", name)


def _event(db: Session, lead: Lead, action: str, actor: User | None = None, label: str | None = None,
           note: str | None = None, **data):
    db.add(LeadEvent(lead_id=lead.id, status=lead.status, agent_id=lead.agent_id, at=now_text(), kind=action, note=note,
                     actor_id=actor.id if actor else None))
    log_event(db, "lead", lead.id, action, actor, label, status=lead.status, note=note, **data)


def _touch(lead: Lead, status: str | None = None):
    if status:
        lead.status = status
    lead.updated_at = now_text()


def _set_sla(db: Session, lead: Lead, stage: str | None):
    jobs.cancel(db, "lead_sla", lead.id)
    if not stage:
        lead.sla_stage = lead.sla_due_at = None
        return
    minutes = settings.sla_minutes(db, stage)
    lead.sla_stage, lead.sla_due_at = stage, now() + timedelta(minutes=minutes)
    jobs.schedule(db, "lead_sla", lead.id, now() + timedelta(minutes=minutes * 0.6),
                  {"stage": stage, "phase": "warning"})


def sla_color(db: Session, lead: Lead) -> str | None:
    if not lead.sla_due_at:
        return None
    left = (lead.sla_due_at - now()).total_seconds()
    window = settings.sla_minutes(db, lead.sla_stage or "operator_take") * 60
    return "red" if left < 0 else "yellow" if left <= 0.4 * window else "green"


def _lead(db: Session, lead_id: int, lock: bool = False) -> Lead:
    return get_or_404(db, Lead, int(lead_id), "lead", lock=lock)


def _require_agent_or_staff(lead: Lead, actor: User | None):
    if actor is not None and (actor.role in STAFF or lead.agent_id == actor.id):
        return
    raise forbidden("not your lead")


def _was_cancelled(db: Session, lead: Lead) -> bool:
    """Closed by an operator (cancel) rather than refused after a viewing: the latest lifecycle event is "cancelled"."""
    if lead.refusal_reason == "cancelled":
        return True
    last = db.scalar(select(LeadEvent.kind).where(LeadEvent.lead_id == lead.id,
                                                  LeadEvent.kind.in_(("created", "reopened", "cancelled")))
                     .order_by(LeadEvent.id.desc()).limit(1))
    return last == "cancelled"


def _require_status(lead: Lead, *allowed: str):
    if lead.status not in allowed:
        raise DomainError("bad_status", f"lead is {lead.status}, expected one of {allowed}")


# ---------------------------------------------------------------- client side (bot)

def client_listing_eligible(db: Session, listing_id: str) -> bool:
    """Clients can request only listings of published objects (the agency channel is imported as objects) or fast-tracked ones
    (stage 10: the owner agreed to show them before the publication). Same rule as bot/core.py CLIENT_ELIGIBLE."""
    published = and_(Property.status == "published", func.trim(func.coalesce(Property.post_url, "")) != "",
                     func.trim(func.coalesce(Property.post_message_id, "")) != "")
    fast = and_(Property.fast_track_at.isnot(None), Property.status.not_in(("archived", "rented")))
    # a published object with its own post is ours: the source ad may be gone (the owner took it down after signing)
    ours = select(Property.id).where(Property.listing_id == Listing.id, published).exists()
    fast_live = and_(Listing.status == "published",
                     select(Property.id).where(Property.listing_id == Listing.id, fast).exists())
    return db.scalar(select(Listing.id).where(Listing.id == listing_id, Listing.status != "rejected",
                                              or_(ours, fast_live))) is not None


def create_from_like(db: Session, client_id: int, listing_id: str, request: str | None = None) -> Lead:
    """A3: ❤️ on a card. Idempotent per (client, listing)."""
    if not client_listing_eligible(db, listing_id):
        raise DomainError("listing_unavailable", "Объект ещё не опубликован агентством или уже недоступен")
    existing = db.scalar(select(Lead).where(Lead.client_id == client_id, Lead.listing_id == listing_id))
    if existing:
        if existing.status == "refused":
            return _reopen(db, existing.id, request)
        return existing
    get_or_404(db, Client, client_id, "client")
    get_or_404(db, Listing, listing_id, "listing")
    t = now_text()
    db.execute(insert(Reaction).values(client_id=client_id, listing_id=listing_id, reaction="like", created_at=t)
               .on_conflict_do_update(index_elements=["client_id", "listing_id"],
                                      set_={"reaction": "like", "created_at": t}))
    prop = db.scalar(select(Property).where(Property.listing_id == listing_id))
    stmt = (insert(Lead).values(client_id=client_id, listing_id=listing_id, property_id=prop.id if prop else None,
                                status="new", request=request, created_at=t, updated_at=t)
            .on_conflict_do_nothing(index_elements=["client_id", "listing_id"]).returning(Lead.id))
    new_id = db.scalar(stmt)
    if new_id is None:  # lost a race with a concurrent like
        db.commit()
        return db.scalar(select(Lead).where(Lead.client_id == client_id, Lead.listing_id == listing_id))
    lead = db.get(Lead, new_id)
    _set_sla(db, lead, "operator_take")
    _event(db, lead, "created", label="client")
    db.commit()
    _notify("lead_new_group", lead)
    _notify("lead_client_ack", lead)
    return _auto_assign(db, lead)


def _auto_assign(db: Session, lead: Lead) -> Lead:
    """A new lead on an object with a known agent goes straight to him: the object's agent, else the owner's
    (owners.agent_id). No agent, or he can't take it (inactive) → the operator assigns as before."""
    prop = db.get(Property, lead.property_id) if lead.property_id else None
    owner = db.get(Owner, prop.owner_id) if prop and prop.owner_id else None
    for aid in dict.fromkeys(a for a in (prop and prop.agent_id, owner and owner.agent_id) if a):
        try:
            return assign_agent(db, lead.id, aid, None)
        except DomainError:
            db.rollback()
    return lead


def _reopen(db: Session, lead_id: int, request: str | None = None) -> Lead:
    """A refused/cancelled lead liked again: back to `new` with a clean slate, like a fresh like."""
    lead = _lead(db, lead_id, lock=True)
    if lead.status != "refused":
        db.commit()
        return lead
    db.query(LeadSlot).filter(LeadSlot.lead_id == lead.id).delete()
    for k in ("viewing_reminder", "post_viewing_survey", "thinking_followup"):
        jobs.cancel(db, k, lead.id)
    lead.operator_id = lead.agent_id = None
    lead.client_outcome = lead.agent_outcome = lead.refusal_reason = lead.client_note = None
    lead.viewing_at = lead.contacts_shared_at = None
    if request:
        lead.request = request
    t = now_text()
    db.execute(insert(Reaction).values(client_id=lead.client_id, listing_id=lead.listing_id, reaction="like", created_at=t)
               .on_conflict_do_update(index_elements=["client_id", "listing_id"],
                                      set_={"reaction": "like", "created_at": t}))
    _touch(lead, "new")
    _set_sla(db, lead, "operator_take")
    _event(db, lead, "reopened", label="client")
    db.commit()
    _notify("lead_new_group", lead)
    _notify("lead_client_ack", lead)
    return _auto_assign(db, lead)


def choose_slot(db: Session, slot_id: int, client_id: int | None = None) -> Lead:
    """A4b: client picked a slot → contacts go to the agent (C8/B5), reminders scheduled."""
    slot = get_or_404(db, LeadSlot, int(slot_id), "slot")
    lead = _lead(db, slot.lead_id, lock=True)
    # re-read the slot under the lead lock: a concurrent reschedule/add_slots may have deleted or replaced it
    slot = db.get(LeadSlot, int(slot_id), populate_existing=True)
    if slot is None or slot.lead_id != lead.id:
        raise DomainError("bad_slot", "slot is no longer available", 409)
    if client_id is not None and lead.client_id != client_id:
        raise forbidden("not your lead")
    if lead.status == "time_chosen" and slot.chosen:
        return lead
    _require_status(lead, "slots_sent")
    for s in db.scalars(select(LeadSlot).where(LeadSlot.lead_id == lead.id)):
        s.chosen = s.id == slot.id
    lead.viewing_at = slot.starts_at
    lead.contacts_shared_at = now()
    _touch(lead, "time_chosen")
    _set_sla(db, lead, None)
    _schedule_viewing_jobs(db, lead)
    _event(db, lead, "time_chosen", label="client", note=texts.when_long(slot.starts_at), viewing_at=slot.starts_at.isoformat())
    _event(db, lead, "contacts_shared", label="bot", note="Бот передал контакты агенту")
    db.commit()
    agent = db.get(User, lead.agent_id) if lead.agent_id else None
    _notify("slot_confirmed_client", lead, agent)
    _notify("contacts_to_agent", lead)
    _notify("client_time_staff", lead)
    return lead


def _schedule_viewing_jobs(db: Session, lead: Lead):
    v = lead.viewing_at
    remind = v - timedelta(minutes=env.VIEWING_REMINDER_MIN)
    if remind > now():
        jobs.schedule(db, "viewing_reminder", lead.id, remind)
    else:
        jobs.cancel(db, "viewing_reminder", lead.id)
    jobs.schedule(db, "post_viewing_survey", lead.id, v + timedelta(minutes=env.POST_VIEWING_SURVEY_MIN))


def client_own_time(db: Session, lead_id: int, text: str) -> Lead:
    """A4a "Другое время": the client's free-text time goes to the agent and operator (history)."""
    lead = _lead(db, lead_id, lock=True)
    _require_status(lead, "agent_assigned", "slots_sent")
    lead.client_note = (text or "").strip()[:1000]
    _touch(lead)
    _event(db, lead, "client_own_time", label="client", note=lead.client_note)
    _set_sla(db, lead, "agent_slots")
    db.commit()
    _notify("client_own_time_agent", lead, lead.client_note)
    _notify("client_time_staff", lead, lead.client_note)
    return lead


def client_action(db: Session, data: str, client_id: int) -> Lead:
    """A client's button on a card from the operator's account (the bot gets the press) or its number typed in the
    operator's DM (the userbot): ls:pick:<slot> | lv:ok|late|resched:<lead> | lo:c:<lead>:<outcome> | lr:<lead>:<reason>.
    Only the lead's own client."""
    p = data.split(":")
    if p[0] == "ls" and p[1] == "pick":
        return choose_slot(db, int(p[2]), client_id)
    lead = _lead(db, int(p[2] if p[0] in ("lv", "lo") else p[1]))
    if lead.client_id != client_id:
        raise forbidden("not your lead")
    if p[0] == "lv" and p[1] == "ok":
        _event(db, lead, "client_confirmed", label="client", note="Клиент: «Буду»")
        db.commit()
        _notify("viewing_update", lead, "ok")
        return lead
    if p[0] == "lv" and p[1] == "late":
        return client_late(db, lead.id)
    if p[0] == "lv" and p[1] == "resched":
        return request_reschedule(db, lead.id, "client")
    if p[0] == "lo" and p[1] == "c":
        return set_outcome(db, lead.id, "client", p[3])
    if p[0] == "lr":
        return set_outcome(db, lead.id, "client", "refused", p[2] if p[2] in REFUSAL_REASONS else "other")
    raise DomainError("bad_action", f"unknown client action {data}")


def client_comment(db: Session, lead_id: int, client_id: int, text: str) -> Lead:
    """The client's own words after a refusal («Другое»), written to the operator's DM: history only."""
    lead = _lead(db, lead_id)
    if lead.client_id != client_id:
        raise forbidden("not your lead")
    _event(db, lead, "client_comment", label="client", note=text.strip()[:1000], via="dm")
    db.commit()
    return lead


def client_late(db: Session, lead_id: int) -> Lead:
    lead = _lead(db, lead_id)
    _require_status(lead, "time_chosen")
    _event(db, lead, "client_late", label="client", note="Клиент опаздывает")
    _touch(lead)
    db.commit()
    _notify("viewing_update", lead, "late")
    return lead


def request_reschedule(db: Session, lead_id: int, by: str = "client", actor: User | None = None) -> Lead:
    """"Перенести" by client or agent → back to entering free slots (C7)."""
    lead = _lead(db, lead_id, lock=True)
    if by != "client":
        _require_agent_or_staff(lead, actor)
    _require_status(lead, "slots_sent", "time_chosen", "viewing_done", "thinking")
    db.query(LeadSlot).filter(LeadSlot.lead_id == lead.id).delete()
    lead.viewing_at = None
    _touch(lead, "agent_assigned")
    jobs.cancel(db, "viewing_reminder", lead.id)
    jobs.cancel(db, "post_viewing_survey", lead.id)
    _set_sla(db, lead, "agent_slots")
    _event(db, lead, "reschedule", actor, label=by)
    db.commit()
    _notify("lead_assigned_agent", lead)
    return lead


# ---------------------------------------------------------------- operator side

def operator_take(db: Session, lead_id: int, operator: User) -> Lead:
    """B1: first operator to press "Взять" leads it."""
    lead = _lead(db, lead_id, lock=True)
    if lead.operator_id and lead.operator_id != operator.id:
        raise DomainError("already_taken", "lead already taken by another operator")
    if lead.operator_id == operator.id:
        return lead
    if operator.role not in STAFF:
        raise forbidden("only operators take leads")
    lead.operator_id = operator.id
    if lead.status == "new":
        _touch(lead, "operator_taken")
    _event(db, lead, "operator_taken", operator)
    db.commit()
    _notify("lead_taken_group", lead, operator)
    return lead


def assign_agent(db: Session, lead_id: int, agent_id: int, actor: User | None) -> Lead:
    """B3 → C6: agent gets the object + client profile without contacts; SLA agent_slots.
    actor None: the system auto-assign of a new lead to the object's agent (no operator is set, he can reassign)."""
    lead = _lead(db, lead_id, lock=True)
    _require_status(lead, "new", "operator_taken", "agent_assigned")
    if actor is not None and actor.role not in STAFF:
        raise forbidden("only operators assign agents")
    if actor is not None and lead.operator_id and lead.operator_id != actor.id and actor.role not in ("admin", "superadmin"):
        raise DomainError("already_taken", "lead is led by another operator")
    agent = get_or_404(db, User, int(agent_id), "agent")
    if agent.role not in FIELD + ("admin", "superadmin") or not agent.is_active:
        raise DomainError("bad_agent", "user is not an active agent")
    took = actor is not None and lead.operator_id is None
    if took:
        lead.operator_id = actor.id
    prev = lead.agent_id
    lead.agent_id = agent.id
    _touch(lead, "agent_assigned")
    _set_sla(db, lead, "agent_slots")
    _event(db, lead, "agent_assigned", actor, label=None if actor else "bot",
           note=None if actor else "автоматически · агент объекта", agent_id=agent.id, previous_agent_id=prev)
    db.commit()
    if took:
        _notify("lead_taken_group", lead, actor)
    _notify("lead_assigned_agent", lead)
    return lead


def reassign(db: Session, lead_id: int, agent_id: int, actor: User) -> Lead:
    """Change the agent at any active stage: slots and viewing are reset, the new agent enters time again."""
    lead = _lead(db, lead_id, lock=True)
    _require_status(lead, *ACTIVE)
    db.query(LeadSlot).filter(LeadSlot.lead_id == lead.id).delete()
    lead.viewing_at = lead.contacts_shared_at = None
    lead.status = "agent_assigned"
    jobs.cancel(db, "viewing_reminder", lead.id)
    jobs.cancel(db, "post_viewing_survey", lead.id)
    db.flush()
    return assign_agent(db, lead.id, agent_id, actor)


def recommend_agents(db: Session, lead_id: int) -> list[dict]:
    """B3 hint: the agent the object's owner belongs to first (stage 6.5), then the object's agent, district coverage,
    lighter load. The operator still assigns."""
    lead = _lead(db, lead_id)
    listing = db.get(Listing, lead.listing_id) if lead.listing_id else None
    prop = db.get(Property, lead.property_id) if lead.property_id else None
    district = (prop.district if prop else None) or (listing.district if listing else None) or ""
    owner = db.get(Owner, prop.owner_id) if prop and prop.owner_id else None
    load = dict(db.execute(select(Lead.agent_id, func.count()).where(Lead.status.in_(ACTIVE), Lead.agent_id.isnot(None))
                           .group_by(Lead.agent_id)).all())
    out = []
    for a in db.scalars(select(User).where(User.role.in_(FIELD), User.is_active.is_(True))):
        score, reasons = 0, []
        if owner is not None and owner.agent_id == a.id:
            score += 200
            reasons.append("его собственник")
        if prop and prop.agent_id == a.id:
            score += 100
            reasons.append("его объект")
        districts = [str(d).lower() for d in (a.districts or [])]
        if district and any(d and (d in district.lower() or district.lower() in d) for d in districts):
            score += 30
            reasons.append(f"район {district}")
        n = load.get(a.id, 0)
        score -= 5 * n
        reasons.append(f"заявок в работе: {n}")
        out.append({"agent": {"id": a.id, "name": a.name, "username": a.username, "districts": a.districts},
                    "score": score, "reasons": reasons, "active_leads": n})
    out.sort(key=lambda r: -r["score"])
    if out:
        out[0]["recommended"] = True
    return out


def cancel(db: Session, lead_id: int, actor: User, reason: str | None = None) -> Lead:
    lead = _lead(db, lead_id, lock=True)
    if actor.role not in STAFF:
        raise forbidden("only operators cancel leads")
    _require_status(lead, *ACTIVE)
    lead.refusal_reason = reason or lead.refusal_reason or "cancelled"
    _touch(lead, "refused")
    _set_sla(db, lead, None)
    for k in ("viewing_reminder", "post_viewing_survey", "thinking_followup"):
        jobs.cancel(db, k, lead.id)
    _event(db, lead, "cancelled", actor, note=reason)
    db.commit()
    return lead


ARCHIVABLE = ("rented", "refused", "thinking")


def set_archived(db: Session, lead_id: int, actor: User, archived: bool) -> Lead:
    """Closed leads leave the board but stay searchable (filter «Архив»); restoring puts them back."""
    lead = _lead(db, lead_id, lock=True)
    if actor.role not in STAFF and lead.agent_id != actor.id:
        raise forbidden("not your lead")
    if archived:
        _require_status(lead, *ARCHIVABLE)
    if bool(lead.archived_at) != archived:
        lead.archived_at = now() if archived else None
        _touch(lead)
        _event(db, lead, "archived" if archived else "unarchived", actor)
        db.commit()
    return lead


def log_operator_dm(db: Session, lead_id: int, text: str, actor: User | None = None) -> Lead:
    """The operator wrote to the client from their own Telegram (seen by the userbot): history only."""
    lead = _lead(db, lead_id)
    _event(db, lead, "message_to_client", actor, label=None if actor else "operator", note=text, via="dm")
    _touch(lead)
    db.commit()
    return lead


def message_from_client(db: Session, lead_id: int, client_id: int, text: str) -> Lead:
    """The client wrote to the operator's own account (seen by the userbot): history only, the operator already has it."""
    lead = _lead(db, lead_id, lock=True)
    if lead.client_id != client_id:
        raise forbidden("not your lead")
    text = text.strip()
    if not text:
        raise DomainError("empty_message", "message is empty")
    _event(db, lead, "message_from_client", label="client", note=text, via="dm")
    _touch(lead)
    db.commit()
    return lead


# ---------------------------------------------------------------- agent side

def reject_past_slots(starts: list[datetime]) -> None:
    """Agent input (API): viewing windows must be in the future. seed_demo builds past viewings directly."""
    if any(s is not None and aware(s) <= now() for s in starts):
        raise DomainError("past_slot", "slot time is in the past")


def add_slots(db: Session, lead_id: int, starts: list[datetime], actor: User | None) -> Lead:
    """C7: agent enters free windows → the operator's userbot sends them to the client as the bot's inline card
    «slots:<token>» (A4a, notify.slots_to_client); agent SLA stops. Not delivered (no userbot) → the lead is back at
    agent_assigned without these slots, and the agent gets the reason (503)."""
    lead = _lead(db, lead_id, lock=True)
    _require_agent_or_staff(lead, actor)
    _require_status(lead, "agent_assigned", "slots_sent")
    starts = sorted({aware(s) for s in starts if s})
    if not starts:
        raise DomainError("no_slots", "at least one slot is required")
    db.query(LeadSlot).filter(LeadSlot.lead_id == lead.id, LeadSlot.chosen.is_(False)).delete()
    slots = [LeadSlot(lead_id=lead.id, starts_at=s, created_by=actor.id if actor else None) for s in starts]
    db.add_all(slots)
    _touch(lead, "slots_sent")
    _set_sla(db, lead, None)
    _event(db, lead, "slots_sent", actor, slots=[s.isoformat() for s in starts])
    db.commit()  # before the send: the bot reads the slots while the userbot asks it for the card
    if err := _notify("slots_to_client", lead):
        lead = _lead(db, lead_id, lock=True)
        db.query(LeadSlot).filter(LeadSlot.lead_id == lead.id, LeadSlot.chosen.is_(False)).delete()
        _touch(lead, "agent_assigned")
        _set_sla(db, lead, "agent_slots")
        _event(db, lead, "slots_not_delivered", label="bot", note=err)
        db.commit()
        raise DomainError("userbot_off", err, 503)
    return lead


def set_outcome(db: Session, lead_id: int, by: str, outcome: str, reason: str | None = None,
                actor: User | None = None) -> Lead:
    """A5a / C9a. Agent "rented" → deal_pending (deal form C9b); refused → refused (+reason ask);
    thinking → remind tomorrow; reschedule → slots again."""
    if by not in OUTCOMES or outcome not in OUTCOMES[by]:
        raise DomainError("bad_outcome", f"outcome for {by} must be one of {OUTCOMES.get(by)}")
    lead = _lead(db, lead_id, lock=True)
    if by == "agent":
        _require_agent_or_staff(lead, actor)
    if outcome == "reschedule":
        db.rollback()
        return request_reschedule(db, lead_id, "agent", actor)
    _require_status(lead, "time_chosen", "viewing_done", "thinking", "deal_pending", "refused")
    if lead.status == "refused" and (_was_cancelled(db, lead) or (by == "client" and outcome == "rented")):
        raise DomainError("bad_status", "lead is closed")
    if by == "client":
        lead.client_outcome = outcome
    else:
        lead.agent_outcome = outcome
    if reason:
        lead.refusal_reason = reason
    ask_reason = False
    if outcome == "refused":
        if lead.status != "deal_pending" or by == "agent":
            _touch(lead, "refused")
        ask_reason = by == "client" and not reason
        jobs.cancel(db, "thinking_followup", lead.id)
    elif outcome == "thinking":
        if lead.status not in ("deal_pending", "refused"):
            _touch(lead, "thinking")
        jobs.schedule(db, "thinking_followup", lead.id, now() + timedelta(days=1), {"by": by})
    elif outcome == "rented":
        if by == "agent" or lead.status == "refused":
            _touch(lead, "deal_pending")
        jobs.cancel(db, "thinking_followup", lead.id)
    if lead.status != "time_chosen":
        jobs.cancel(db, "viewing_reminder", lead.id)
    _touch(lead)
    _event(db, lead, f"outcome_{by}", actor, label=None if actor else by, outcome=outcome, reason=reason)
    db.commit()
    if ask_reason:
        _notify("refusal_reason_ask", lead)
    return lead


def mark_viewing_done(db: Session, lead_id: int) -> Lead | None:
    """Worker: viewing time + POST_VIEWING_SURVEY_MIN passed → survey to client and agent."""
    lead = _lead(db, lead_id, lock=True)
    if lead.status != "time_chosen":
        db.rollback()
        return None
    _touch(lead, "viewing_done")
    _event(db, lead, "viewing_done", label="bot")
    db.commit()
    _notify("post_viewing_survey", lead)
    return lead


def attach_deal(db: Session, lead: Lead, deal_id: int, actor: User | None):
    lead.deal_id = deal_id
    if lead.status in DEAL_STAGES:
        _touch(lead, "deal_pending")
        _set_sla(db, lead, None)
    _event(db, lead, "deal_created", actor, deal_id=deal_id)


def detach_deal(db: Session, lead: Lead, deal, actor: User | None, was_confirmed: bool):
    """Deal cancelled: a rented lead goes back to deal_pending (a new deal form can be filled)."""
    if lead.deal_id == deal.id:
        lead.deal_id = None
    if was_confirmed and lead.status == "rented":
        _touch(lead, "deal_pending")
    _event(db, lead, "deal_cancelled", actor, deal_id=deal.id)


def mark_rented(db: Session, lead: Lead, actor: User | None):
    _touch(lead, "rented")
    _set_sla(db, lead, None)
    _event(db, lead, "rented", actor, deal_id=lead.deal_id)


# ---------------------------------------------------------------- reading

def history(db: Session, lead_id: int) -> list[dict]:
    rows = db.scalars(select(Event).where(Event.entity == "lead", Event.entity_id == str(lead_id)).order_by(Event.at, Event.id))
    return [{"id": e.id, "action": e.action, "actor_id": e.actor_id, "actor": e.actor_label, "data": e.data,
             "at": e.at.isoformat()} for e in rows]


def contacts_visible(lead: Lead, user: User) -> bool:
    if user.role in STAFF:
        return True
    return lead.agent_id == user.id and lead.contacts_shared_at is not None


def _listing_card(listing: Listing | None) -> dict | None:
    if not listing:
        return None
    photos = jload(listing.photos, []) or []
    return {"id": listing.id, "district": listing.district, "address": listing.address, "price_usd": listing.price_usd,
            "bedrooms": listing.bedrooms, "area": listing.area, "floor": listing.floor, "status": listing.status,
            "photo": f"/api/listing-photos/{listing.id}/0" if photos else None, "photos": len(photos),
            "description": listing.description_ru}


def _person(u: User | None) -> dict | None:
    return {"id": u.id, "name": u.name, "username": u.username, "phone": u.phone} if u else None


def prefetch(db: Session, rows: list[Lead], cache: dict) -> None:
    """Batch-load clients, listings and users of these leads into card()'s cache (no N+1)."""
    want = {Client: {r.client_id for r in rows}, Listing: {r.listing_id for r in rows},
            User: {i for r in rows for i in (r.operator_id, r.agent_id)}}
    for model, ids in want.items():
        ids = {i for i in ids if i is not None and (model.__name__, i) not in cache}
        if ids:
            for obj in db.scalars(select(model).where(model.id.in_(ids))):
                cache[(model.__name__, obj.id)] = obj
            for i in ids:
                cache.setdefault((model.__name__, i), None)


def cards(db: Session, rows, user: User, cache: dict | None = None) -> list[dict]:
    cache = cache if cache is not None else {}
    rows = list(rows)
    prefetch(db, rows, cache)
    return [card(db, lead, user, cache) for lead in rows]


def page(db: Session, stmt, user: User, limit: int = 50, offset: int = 0) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset)))
    return {"items": cards(db, rows, user), "total": total}


def card(db: Session, lead: Lead, user: User, cache: dict | None = None) -> dict:
    cache = cache if cache is not None else {}

    def get(model, ident):
        if ident is None:
            return None
        k = (model.__name__, ident)
        if k not in cache:
            cache[k] = db.get(model, ident)
        return cache[k]

    client = get(Client, lead.client_id)
    show = contacts_visible(lead, user)
    c = None
    if client:
        c = {"id": client.id, "lang": client.lang, "profile": jload(client.profile, {}),
             "name": client.name if show else None, "username": client.username if show else None,
             "phone": client.phone if show else None, "channel": client.channel}
    return {
        "id": lead.id, "status": lead.status, "created_at": lead.created_ts.isoformat() if lead.created_ts else lead.created_at,
        "updated_at": lead.updated_at, "listing": _listing_card(get(Listing, lead.listing_id)),
        "property_id": lead.property_id, "client": c, "contacts_visible": show,
        "operator": _person(get(User, lead.operator_id)), "agent": _person(get(User, lead.agent_id)),
        "sla": {"stage": lead.sla_stage, "due_at": lead.sla_due_at.isoformat() if lead.sla_due_at else None,
                "color": sla_color(db, lead)},
        "viewing_at": lead.viewing_at.isoformat() if lead.viewing_at else None,
        "client_outcome": lead.client_outcome, "agent_outcome": lead.agent_outcome,
        "refusal_reason": lead.refusal_reason, "client_note": lead.client_note, "deal_id": lead.deal_id,
        "archived_at": lead.archived_at.isoformat() if lead.archived_at else None,
        "request": lead.request,
    }


def owner_contact(db: Session, lead: Lead, user: User, prop: Property | None) -> dict | None:
    """«Позвонить собственнику» in the lead: staff and the lead's agent while the lead is active (as listing_get)."""
    if not (user.role in STAFF or (lead.agent_id == user.id and lead.status in ACTIVE)):
        return None
    owner = db.get(Owner, prop.owner_id) if prop and prop.owner_id else None
    if owner and owner.phone:
        return {"name": owner.name, "phone": owner.phone, "is_agent": owner.is_agent}
    listing = db.get(Listing, lead.listing_id) if lead.listing_id else None
    phones = extract_phones(listing.text or "") if listing else []
    return {"name": None, "phone": phones[0], "is_agent": False} if phones else None


def detail(db: Session, lead: Lead, user: User) -> dict:
    d = card(db, lead, user)
    prop = db.get(Property, lead.property_id) if lead.property_id else (
        db.scalar(select(Property).where(Property.listing_id == lead.listing_id)) if lead.listing_id else None)
    d["property_id"] = prop.id if prop else None
    d["owner"] = owner_contact(db, lead, user, prop)
    d["slots"] = [{"id": s.id, "starts_at": s.starts_at.isoformat(), "chosen": s.chosen}
                  for s in db.scalars(select(LeadSlot).where(LeadSlot.lead_id == lead.id).order_by(LeadSlot.starts_at))]
    d["history"] = history(db, lead.id)
    return d


def can_view(lead: Lead, user: User) -> bool:
    return user.role in STAFF or lead.agent_id == user.id


def query(db: Session, user: User, status: str | None = None, agent_id: int | None = None,
          operator_id: int | None = None, q: str | None = None, archived: bool = False):
    stmt = select(Lead).where(Lead.archived_at.isnot(None) if archived else Lead.archived_at.is_(None))
    if user.role not in STAFF:
        stmt = stmt.where(Lead.agent_id == user.id)
    elif agent_id:
        stmt = stmt.where(Lead.agent_id == agent_id)
    if status:
        stmt = stmt.where(Lead.status.in_(status.split(",")))
    if operator_id:
        stmt = stmt.where(Lead.operator_id == operator_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.join(Client, Client.id == Lead.client_id).outerjoin(Listing, Listing.id == Lead.listing_id).where(
            (Client.name.ilike(like)) | (Client.username.ilike(like)) | (Listing.district.ilike(like))
            | (Listing.address.ilike(like)) | (cast(Lead.id, Text).ilike(like)))
    return stmt.order_by(Lead.created_ts.desc())


def board(db: Session, user: User, per_column: int = 100) -> dict[str, list[dict]]:
    columns = {st: list(db.scalars(query(db, user, status=st).limit(per_column))) for st in BOARD_COLUMNS}
    cache: dict = {}
    prefetch(db, [lead for rows in columns.values() for lead in rows], cache)
    return {st: [card(db, lead, user, cache) for lead in rows] for st, rows in columns.items()}
