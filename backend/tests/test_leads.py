from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app import worker
from app.models import Deal, Job, Lead, LeadEvent, LeadSlot, Property
from app.services import jobs, leads
from app.services.errors import DomainError
from tests.conftest import auth, make_client, make_client_listing as make_listing, make_user


def _due_now(db, kind, ref):
    db.execute(update(Job).where(Job.kind == kind, Job.ref == str(ref), Job.done_at.is_(None))
               .values(due_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()


def test_lead_journey_end_to_end(db, client, sent):
    nino = make_user(db, "operator", "Нино")
    other_op = make_user(db, "operator", "Мариам")
    georgiy = make_user(db, "agent", "Георгий", phone="+995 599 11 22 33", districts=["Ваке"])
    make_user(db, "agent", "Леван")
    anna = make_client(db)
    listing = make_listing(db)

    # A3: like → lead new, operators notified, SLA operator_take running
    lead = leads.create_from_like(db, anna.id, listing.id)
    assert lead.status == "new" and lead.sla_stage == "operator_take"
    assert "lead_new_group" in sent.names() and "lead_client_ack" in sent.names()
    assert jobs.pending(db, "lead_sla", lead.id)
    assert leads.create_from_like(db, anna.id, listing.id).id == lead.id  # idempotent

    # B1: Нино takes, Мариам gets 409
    r = client.post(f"/api/leads/{lead.id}/take", headers=auth(nino))
    assert r.status_code == 200 and r.json()["status"] == "operator_taken"
    r = client.post(f"/api/leads/{lead.id}/take", headers=auth(other_op))
    assert r.status_code == 409 and r.json()["code"] == "already_taken"

    # B3: recommendation (district match) + assign
    rec = client.get(f"/api/leads/{lead.id}/recommend-agents", headers=auth(nino)).json()
    assert rec[0]["agent"]["id"] == georgiy.id and rec[0]["recommended"]
    r = client.post(f"/api/leads/{lead.id}/assign", json={"agent_id": georgiy.id}, headers=auth(nino))
    assert r.json()["status"] == "agent_assigned" and r.json()["sla"]["stage"] == "agent_slots"
    assert r.json()["sla"]["color"] == "green"
    assert sent.of("lead_assigned_agent")

    # C6: the agent sees the profile, not the contacts; other agents cannot open it
    d = client.get(f"/api/leads/{lead.id}", headers=auth(georgiy)).json()
    assert d["contacts_visible"] is False and d["client"]["phone"] is None and d["client"]["profile"]["budget_max"] == 900
    assert client.get(f"/api/leads/{lead.id}", headers=auth(make_user(db, "agent", "Чужой"))).status_code == 403
    assert client.get(f"/api/leads/{lead.id}", headers=auth(nino)).json()["client"]["phone"]  # operators always

    # C7: slots → client
    t1 = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    r = client.post(f"/api/leads/{lead.id}/slots", json={"starts": [t1.isoformat(), (t1 + timedelta(hours=2)).isoformat()]},
                    headers=auth(georgiy))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "slots_sent" and len(d["slots"]) == 2 and d["sla"]["stage"] is None
    assert sent.of("slots_to_client")

    # A4b: client picks → contacts shared, reminders scheduled
    slot_id = d["slots"][0]["id"]
    with pytest.raises(DomainError):
        leads.choose_slot(db, slot_id, client_id=anna.id + 999)
    lead = leads.choose_slot(db, slot_id, client_id=anna.id)
    assert lead.status == "time_chosen" and lead.contacts_shared_at and lead.viewing_at == t1
    assert sent.of("contacts_to_agent") and sent.of("slot_confirmed_client")[0][1].id == georgiy.id
    assert sent.of("client_time_staff")[-1][0].id == lead.id  # the operator learns the chosen time (Stage 0)
    assert db.query(LeadEvent).filter_by(lead_id=lead.id, kind="time_chosen").one().note
    d = client.get(f"/api/leads/{lead.id}", headers=auth(georgiy)).json()
    assert d["contacts_visible"] and d["client"]["phone"] == "+995 555 000 111"
    assert any(h["action"] == "contacts_shared" for h in d["history"])
    assert jobs.pending(db, "viewing_reminder", lead.id) and jobs.pending(db, "post_viewing_survey", lead.id)

    # worker: survey after the viewing → viewing_done
    _due_now(db, "post_viewing_survey", lead.id)
    assert worker.tick()["ok"] == 1
    db.expire_all()
    assert db.get(Lead, lead.id).status == "viewing_done" and sent.of("post_viewing_survey")

    # A5a / C9a: both say rented → deal_pending
    leads.set_outcome(db, lead.id, "client", "rented")
    r = client.post(f"/api/leads/{lead.id}/outcome", json={"outcome": "rented"}, headers=auth(georgiy))
    assert r.json()["status"] == "deal_pending" and r.json()["client_outcome"] == "rented"

    # C9b: deal form → pending; B6: operator confirms → rented, T14 + contract jobs
    db.get(Property, lead.property_id).is_exclusive = True  # the deal takes «exclusive» from the object
    db.commit()
    rent_from = date.today() + timedelta(days=2)
    r = client.post("/api/deals", headers=auth(georgiy), json={
        "lead_id": lead.id, "price": 850, "commission_pct": 50, "is_exclusive": True, "cadastral": "01.14.12.003.045",
        "owner_name": "Гиорги", "owner_phone": "+995 555 12 34 56", "rent_from": rent_from.isoformat(),
        "rent_to": (rent_from + timedelta(days=365)).isoformat(),
        "t0_h2o_client": 3, "t0_h2o_owner": 3, "t0_h2o_process": 2, "t0_result": "clean"})
    assert r.status_code == 200, r.text
    deal = r.json()
    assert deal["status"] == "pending" and deal["commission_total"] == 425 and deal["agent_amount"] == 212.5
    assert deal["client_name"] == "Анна"
    assert client.post(f"/api/deals/{deal['id']}/confirm", headers=auth(georgiy)).status_code == 403
    r = client.post(f"/api/deals/{deal['id']}/confirm", headers=auth(nino))
    assert r.json()["status"] == "confirmed" and r.json()["agent_deal_num"] == 1
    db.expire_all()
    assert db.get(Lead, lead.id).status == "rented"
    assert sent.of("deal_confirmed")
    assert jobs.pending(db, "t14_client", deal["id"]) and jobs.pending(db, "t14_agent", deal["id"])
    assert len(jobs.pending(db, "contract_expiry")) == 3
    text = client.get(f"/api/deals/{deal['id']}", headers=auth(nino)).json()["rented_text"]
    assert "#Rented | Сделка #1" in text and "T0 | H2O: 3/3/2" in text and "Агент/Наставник (50%): 212.5$" in text
    statuses = [e.status for e in db.query(LeadEvent).filter_by(lead_id=lead.id).order_by(LeadEvent.id)]
    assert statuses[0] == "new" and statuses[-1] == "rented"

    board = client.get("/api/leads/board", headers=auth(georgiy)).json()
    assert [c["id"] for c in board["rented"]] == [lead.id]


def test_sla_warning_then_overdue(db, sent):
    lead = leads.create_from_like(db, make_client(db).id, make_listing(db).id)
    _due_now(db, "lead_sla", lead.id)
    worker.tick()
    assert [stage for _, stage in sent.of("sla_warning")] == ["operator_take"] and not sent.of("sla_overdue")
    overdue = jobs.pending(db, "lead_sla", lead.id)
    assert overdue and overdue[0].payload["phase"] == "overdue"
    _due_now(db, "lead_sla", lead.id)
    worker.tick()
    assert len(sent.of("sla_overdue")) == 1


def test_sla_job_goes_stale_after_stage_change(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    lead = leads.create_from_like(db, make_client(db).id, make_listing(db).id)
    first = jobs.pending(db, "lead_sla", lead.id)[0].id
    leads.assign_agent(db, lead.id, ag.id, op)
    db.expire_all()
    assert db.get(Job, first).last_error == "cancelled"
    assert jobs.pending(db, "lead_sla", lead.id)[0].payload["stage"] == "agent_slots"


def test_refusal_reason_and_thinking(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    c = make_client(db)
    lead = leads.create_from_like(db, c.id, make_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    leads.add_slots(db, lead.id, [datetime.now(timezone.utc) + timedelta(hours=3)], ag)
    slot = db.query(LeadSlot).filter_by(lead_id=lead.id).one()
    leads.choose_slot(db, slot.id, c.id)
    lead = leads.set_outcome(db, lead.id, "client", "thinking")
    assert lead.status == "thinking" and jobs.pending(db, "thinking_followup", lead.id)
    lead = leads.set_outcome(db, lead.id, "client", "refused")
    assert lead.status == "refused" and sent.of("refusal_reason_ask")
    lead = leads.set_outcome(db, lead.id, "client", "refused", reason="price")
    assert lead.refusal_reason == "price"
    with pytest.raises(DomainError):
        leads.set_outcome(db, lead.id, "client", "maybe")


def test_reschedule_and_reassign(db, sent):
    op, ag, ag2 = make_user(db, "operator"), make_user(db, "agent"), make_user(db, "agent")
    c = make_client(db)
    lead = leads.create_from_like(db, c.id, make_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    leads.add_slots(db, lead.id, [datetime(2030, 1, 1, 12, 0)], ag)  # naive = Tbilisi
    slot = db.query(LeadSlot).filter_by(lead_id=lead.id).one()
    assert slot.starts_at == datetime(2030, 1, 1, 8, tzinfo=timezone.utc)  # the instant (the DB session TZ may be local)
    leads.choose_slot(db, slot.id, c.id)
    lead = leads.request_reschedule(db, lead.id, "client")
    assert lead.status == "agent_assigned" and lead.viewing_at is None and lead.sla_stage == "agent_slots"
    lead = leads.reassign(db, lead.id, ag2.id, op)
    assert lead.agent_id == ag2.id and lead.contacts_shared_at is None
    with pytest.raises(DomainError) as e:
        leads.add_slots(db, lead.id, [datetime(2030, 1, 2, 12)], ag)
    assert e.value.status == 403


def test_client_dm_messages_preserve_history_and_reject_foreign_ones(db, sent):
    """The operator talks to the client in his own Telegram; the userbot logs both sides (message_from_client)."""
    cl = make_client(db)
    other = make_client(db, external_id=5550002)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    with pytest.raises(DomainError) as ex:
        leads.message_from_client(db, lead.id, other.id, "Чужой ответ")
    assert ex.value.code == "forbidden"
    leads.message_from_client(db, lead.id, cl.id, "Завтра в 13:00")
    ev = db.query(LeadEvent).filter_by(lead_id=lead.id, kind="message_from_client").one()
    assert ev.note == "Завтра в 13:00"
    assert not hasattr(leads, "message_client")                   # no «Написать клиенту» from the CRM any more


def test_client_own_time_reaches_the_operator(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    lead = leads.create_from_like(db, make_client(db).id, make_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    leads.client_own_time(db, lead.id, "суббота после 15")
    assert sent.of("client_time_staff")[-1][1] == "суббота после 15"
    assert sent.of("lead_assigned_agent")


def test_like_auto_assigns_the_objects_agent(db, client, sent):
    """A lead on an object with a known agent goes to him at once (object's agent, else the owner's); the operator
    still sees it and can reassign. No agent → the usual operator flow."""
    from app.models import Owner, Property
    nino = make_user(db, "operator", "Нино")
    georgiy, levan = make_user(db, "agent", "Георгий", is_active=True), make_user(db, "agent", "Леван", is_active=True)
    anna = make_client(db)
    fast = datetime.now(timezone.utc)
    own = make_listing(db, "ss-own", source="ss")
    db.add(Property(listing_id=own.id, status="taken", agent_id=georgiy.id, fast_track_at=fast))
    bound = make_listing(db, "ss-bound", source="ss")
    owner = Owner(name="Нана", phone="599000001", phone_norm="599000001", agent_id=levan.id)
    db.add(owner)
    db.flush()
    db.add(Property(listing_id=bound.id, status="taken", owner_id=owner.id, fast_track_at=fast))
    db.commit()

    lead = leads.create_from_like(db, anna.id, own.id)
    assert lead.status == "agent_assigned" and lead.agent_id == georgiy.id and lead.operator_id is None
    assert lead.sla_stage == "agent_slots"
    assert "lead_new_group" in sent.names() and sent.of("lead_assigned_agent")
    ev = [e for e in leads.history(db, lead.id) if e["action"] == "agent_assigned"]
    assert ev and ev[0]["actor"] == "bot" and ev[0]["data"]["agent_id"] == georgiy.id
    # the operator sees it and can reassign
    assert client.get(f"/api/leads/{lead.id}", headers=auth(nino)).status_code == 200
    r = client.post(f"/api/leads/{lead.id}/assign", json={"agent_id": levan.id}, headers=auth(nino))
    assert r.status_code == 200 and r.json()["agent"]["id"] == levan.id

    # no object agent → the owner's agent
    assert leads.create_from_like(db, anna.id, bound.id).agent_id == levan.id

    # agency listing without an object → operator assigns as before
    plain = leads.create_from_like(db, anna.id, make_listing(db).id)
    assert plain.status == "new" and plain.agent_id is None
