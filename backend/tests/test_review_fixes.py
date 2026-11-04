"""Regression tests for the code-review fixes (lead lifecycle, deal idempotency, dedup, masking, worker, importer)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app import worker
from app.importers import sheets_csv
from app.models import Deal, Job, Lead, LeadSlot, Owner, Property
from app.services import deals, jobs, leads, properties
from app.services.dedup import address_match, extract_phones, phone_match
from app.services.errors import DomainError
from tests.conftest import auth, make_client, make_client_listing, make_listing, make_user


def _soon(hours=3):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _to_time_chosen(db, op, ag, client=None, listing=None):
    c = client or make_client(db)
    lead = leads.create_from_like(db, c.id, (listing or make_client_listing(db)).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    leads.add_slots(db, lead.id, [_soon()], ag)
    slot = db.query(LeadSlot).filter_by(lead_id=lead.id).one()
    return leads.choose_slot(db, slot.id, c.id)


# ---------------------------------------------------------------- dedup

def test_dedup_no_false_positives():
    assert not address_match("Tbilisi, Vake, Chavchavadze 5", "Tbilisi, Vake, Paliashvili 7")
    assert not address_match("Chavchavadze 1", "Chavchavadze 10")
    assert not phone_match("555123", "555123456")
    assert address_match("Chavchavadze 10, Vake, Tbilisi", "chavchavadze ave. 10")
    assert address_match("ул. Палиашвили 10", "Палиашвили, 10, Ваке")
    assert phone_match("+995 599 11 22 33", "0599112233")


# ---------------------------------------------------------------- deals

def test_attach_deal_only_after_viewing_and_clears_sla(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    lead = leads.create_from_like(db, make_client(db).id, make_client_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    deals.create(db, {"lead_id": lead.id, "price": 500}, op)  # staff may file early; lead stage is not skipped
    db.refresh(lead)
    assert lead.status == "agent_assigned" and lead.deal_id

    lead2 = _to_time_chosen(db, op, ag, client=make_client(db, external_id=2), listing=make_client_listing(db, "ss2"))
    leads.set_outcome(db, lead2.id, "client", "thinking")
    deals.create(db, {"lead_id": lead2.id, "price": 500}, ag)
    db.refresh(lead2)
    assert lead2.status == "deal_pending" and lead2.sla_stage is None and not jobs.pending(db, "lead_sla", lead2.id)


def test_deal_create_is_idempotent_per_lead_and_cancel_reverts(db, client, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent", exclusive_deals=10)
    listing = make_client_listing(db)
    prop = properties.take(db, listing.id, ag)
    prop.status = "published"
    prop.post_url, prop.post_message_id = "https://t.me/rent_tbilisi_ge/1", "1"
    db.commit()
    lead = _to_time_chosen(db, op, ag, listing=listing)
    body = {"lead_id": lead.id, "price": 1000, "is_exclusive": True, "rent_from": "2030-01-10", "rent_to": "2031-01-10"}
    first = client.post("/api/deals", json=body, headers=auth(ag)).json()
    second = client.post("/api/deals", json=body, headers=auth(ag)).json()
    assert first["id"] == second["id"] and db.query(Deal).count() == 1

    deals.confirm(db, first["id"], op)
    db.expire_all()
    assert db.get(Lead, lead.id).status == "rented" and db.get(Property, prop.id).status == "rented"
    assert ag.exclusive_deals == 11 and ag.level == 70
    deals.cancel(db, first["id"], op, "сорвалась")
    db.expire_all()
    lead = db.get(Lead, lead.id)
    assert lead.status == "deal_pending" and lead.deal_id is None
    assert db.get(Property, prop.id).status == "published"
    assert ag.exclusive_deals == 10 and ag.level == 50
    assert not jobs.pending(db, "t14_client", first["id"]) and not jobs.pending(db, "contract_expiry")
    # after the cancel a new deal can be filed for the same lead
    assert client.post("/api/deals", json=body, headers=auth(ag)).json()["id"] != first["id"]


def test_deals_find_minimal_for_agents(db, client):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    deals.create(db, {"agent_id": op.id, "price": 900, "cadastral": "01.02.03", "owner_name": "Гиорги",
                      "client_name": "Анна", "rent_from": "2030-01-01"}, op)
    row = client.get("/api/deals/find", params={"cadastral": "01.02"}, headers=auth(ag)).json()[0]
    assert set(row) == {"id", "cadastral", "address", "status", "rent_from", "rent_to"}
    assert "price" in client.get("/api/deals/find", params={"cadastral": "01.02"}, headers=auth(op)).json()[0]


# ---------------------------------------------------------------- lead lifecycle

def test_closed_leads_stay_closed(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    lead = _to_time_chosen(db, op, ag)
    leads.set_outcome(db, lead.id, "agent", "refused", "price", actor=ag)
    with pytest.raises(DomainError) as e:
        leads.set_outcome(db, lead.id, "client", "rented")
    assert e.value.code == "bad_status"
    leads.set_outcome(db, lead.id, "agent", "rented", actor=ag)  # the agent can still revive a refusal
    assert db.get(Lead, lead.id).status == "deal_pending"

    lead2 = _to_time_chosen(db, op, ag, client=make_client(db, external_id=2), listing=make_client_listing(db, "ss2"))
    leads.cancel(db, lead2.id, op, "клиент пропал")
    for call in (lambda: leads.cancel(db, lead2.id, op), lambda: leads.set_outcome(db, lead2.id, "agent", "rented", actor=ag),
                 lambda: leads.client_late(db, lead2.id), lambda: leads.client_own_time(db, lead2.id, "завтра")):
        with pytest.raises(DomainError) as e:
            call()
        assert e.value.code == "bad_status"
        db.rollback()


def test_like_reopens_refused_lead(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    c, listing = make_client(db), make_client_listing(db)
    lead = _to_time_chosen(db, op, ag, client=c, listing=listing)
    leads.cancel(db, lead.id, op)
    sent.clear()
    again = leads.create_from_like(db, c.id, listing.id)
    assert again.id == lead.id and again.status == "new" and again.sla_stage == "operator_take"
    assert again.operator_id is None and again.agent_id is None and again.viewing_at is None
    assert not db.query(LeadSlot).filter_by(lead_id=lead.id).count()
    assert "lead_new_group" in sent.names()
    leads.assign_agent(db, lead.id, ag.id, op)


def test_assign_respects_leading_operator(db, sent):
    op1, op2, admin, ag = (make_user(db, "operator"), make_user(db, "operator"), make_user(db, "admin"),
                           make_user(db, "agent"))
    lead = leads.create_from_like(db, make_client(db).id, make_client_listing(db).id)
    leads.operator_take(db, lead.id, op1)
    with pytest.raises(DomainError) as e:
        leads.assign_agent(db, lead.id, ag.id, op2)
    assert e.value.code == "already_taken"
    db.rollback()
    assert leads.assign_agent(db, lead.id, ag.id, admin).agent_id == ag.id


def test_choose_deleted_slot_fails(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    c = make_client(db)
    lead = leads.create_from_like(db, c.id, make_client_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    leads.add_slots(db, lead.id, [_soon()], ag)
    old = db.query(LeadSlot).filter_by(lead_id=lead.id).one().id
    leads.add_slots(db, lead.id, [_soon(5)], ag)  # replaces the unchosen slots
    with pytest.raises(DomainError):
        leads.choose_slot(db, old, c.id)


def test_slots_api_rejects_past(db, client):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    lead = leads.create_from_like(db, make_client(db).id, make_client_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = client.post(f"/api/leads/{lead.id}/slots", json={"starts": [past]}, headers=auth(ag))
    assert r.status_code == 400 and r.json()["code"] == "past_slot"


def test_viewing_reminder_stale_after_move(db, sent):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    lead = _to_time_chosen(db, op, ag)
    db.execute(update(Lead).where(Lead.id == lead.id).values(viewing_at=_soon(30)))  # moved without rescheduling jobs
    db.execute(update(Job).where(Job.kind == "viewing_reminder").values(due_at=datetime.now(timezone.utc)))
    db.commit()
    worker.tick()
    assert not sent.of("viewing_reminder")
    assert db.scalar(select(Job).where(Job.kind == "viewing_reminder")).last_error == "stale"


# ---------------------------------------------------------------- objects / listings

def test_listing_detail_masked_for_other_agents(db, client):
    ag, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    listing = make_listing(db)
    properties.take(db, listing.id, ag)
    assert client.get(f"/api/listings/{listing.id}", headers=auth(ag)).json()["phones"]
    assert client.get(f"/api/listings/{listing.id}", headers=auth(op)).json()["text"]
    d = client.get(f"/api/listings/{listing.id}", headers=auth(other)).json()
    assert "text" not in d and "phones" not in d and "urls" not in d


def test_listing_contacts_follow_active_lead_assignment(db, client):
    op, ag, other = make_user(db, "operator"), make_user(db, "agent"), make_user(db, "agent")
    listing = make_client_listing(db, text="Телефон 557 686 983. Писать @contact_for_viewing")
    lead = leads.create_from_like(db, make_client(db).id, listing.id)
    leads.assign_agent(db, lead.id, ag.id, op)
    url = f"/api/listings/{listing.id}"
    d = client.get(url, headers=auth(ag)).json()
    assert d["property"] is None and d["contacts_visible"] and d["phones"] and d["urls"]
    assert d["phones"] == ["557 686 983"] and d["telegram_contacts"] == ["contact_for_viewing"]
    hidden = client.get(url, headers=auth(other)).json()
    assert hidden["contacts_visible"] is False and "telegram_contacts" not in hidden
    leads.assign_agent(db, lead.id, other.id, op)
    assert "phones" not in client.get(url, headers=auth(ag)).json()
    assert client.get(url, headers=auth(other)).json()["phones"]
    lead.status = "refused"
    db.commit()
    assert "phones" not in client.get(url, headers=auth(other)).json()


def test_listing_detail_includes_only_linked_owner_for_authorized_agent(db, client):
    op, ag, other = make_user(db, "operator"), make_user(db, "agent"), make_user(db, "agent")
    listing = make_listing(db, text="Объявление без телефона")
    owner = Owner(name="Собственник", phone="+995 599 11 22 33")
    unrelated = Owner(name="Другой собственник", phone="+995 599 99 99 99", address=listing.address)
    db.add_all([owner, unrelated])
    db.flush()
    prop = Property(listing_id=listing.id, owner_id=owner.id, agent_id=op.id, status="published",
                    post_url="https://t.me/rent_tbilisi_ge/1", post_message_id="1")
    db.add(prop)
    db.commit()
    lead = leads.create_from_like(db, make_client(db).id, listing.id)
    leads.assign_agent(db, lead.id, ag.id, op)
    url = f"/api/listings/{listing.id}"
    d = client.get(url, headers=auth(ag)).json()
    assert d["owner"]["phone"] == owner.phone and d["phones"] == []
    assert "owner" not in client.get(url, headers=auth(other)).json()
    prop.owner_id = None
    db.commit()
    assert "owner" not in client.get(url, headers=auth(ag)).json()  # no guessing by address


def test_retake_archived_and_agent_mark_keeps_owner(db, sent):
    ag = make_user(db, "agent")
    p = properties.take(db, make_listing(db).id, ag)
    properties.archive(db, p.id, ag)
    assert properties.take(db, p.listing_id, ag).status == "taken"

    properties.owner_or_agent(db, p.id, ag, True, "Нана", "599 11 22 33")
    p2 = properties.take(db, make_listing(db, "ss2").id, ag)
    p2 = properties.owner_or_agent(db, p2.id, ag, False, "Нана", "599112233")
    assert p2.status == "agent_marked"
    assert not db.scalar(select(Owner).where(Owner.phone_norm == "599112233")).is_agent


# ---------------------------------------------------------------- settings / users

def test_settings_and_user_validation(db, client):
    admin = make_user(db, "admin")
    assert client.put("/api/settings", json={"sla_operator_take_min": 0}, headers=auth(admin)).status_code == 400
    assert client.put("/api/settings", json={"sla_agent_slots_min": "abc"}, headers=auth(admin)).status_code == 400
    r = client.put("/api/settings", json={"sla_agent_slots_min": 45}, headers=auth(admin))
    assert r.status_code == 200 and r.json()["sla_agent_slots_min"] == "45"
    a = make_user(db, "agent", tg_id=111, login="a1")
    b = make_user(db, "agent", tg_id=222)
    assert client.patch(f"/api/users/{b.id}", json={"tg_id": 111}, headers=auth(admin)).status_code == 409
    assert client.patch(f"/api/users/{b.id}", json={"login": "A1"}, headers=auth(admin)).status_code == 409
    assert client.patch(f"/api/users/{a.id}", json={"tg_id": 111, "name": "Same"}, headers=auth(admin)).status_code == 200


# ---------------------------------------------------------------- importer

def test_importer_keeps_rerent_in_another_month_and_schedules_reminders(tmp_path, db):
    (tmp_path / "Agents.csv").write_text("id,name,telegram,is_active,level,,photo_url,user_id\n1,Гия,@gia,TRUE,50,,,1\n",
                                         encoding="utf-8-sig")
    rows = ["2026-03-01,1,Гия,50,3,https://l,1000,500,250,250,01.01,К,+995,О,+995,01.03.2026,Эксклюзив,miniapp,,rent,3/2026",
            "2026-03-02,1,Гия,50,3,https://l,1000,500,250,250,01.01,К,+995,О,+995,01.03.2026,Эксклюзив,bot,,rent,3/2026",
            "2030-05-01,1,Гия,50,3,https://l,1000,500,250,250,01.01,К,+995,О,+995,01.05.2030,Эксклюзив,miniapp,,rent,5/2030"]
    (tmp_path / "Reports_Deals.csv").write_text("column_01\n" + "\n".join(rows) + "\n", encoding="utf-8-sig")
    stats = sheets_csv.run(tmp_path, db)
    assert stats["Reports_Deals"]["imported"] == 2 and stats["Reports_Deals"]["merged"] == 1
    assert {d.month for d in db.scalars(select(Deal))} == {"3/2026", "5/2030"}


def test_extract_georgian_mobile_phone_groups():
    for number in ("557 686 983", "+995 557 686 983", "555 12 34 56", "555-12-34-56", "5.8.5.8.9.0.3.2.7"):
        assert extract_phones(f"Позвонить {number} или написать") == [number]
    assert extract_phones("ID 15556869831, 80 м2, этаж 5/9, $1300") == []
    assert extract_phones("557 686 983 / 557 686 983") == ["557 686 983"]
