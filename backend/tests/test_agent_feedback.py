"""Product-owner feedback (agent role): level, funnel stages, contract prefill from a lead."""
from datetime import date, datetime, timezone

from app.models import Deal, Lead, Owner, Property, User
from app.services import leads
from tests.conftest import auth, make_client, make_client_listing, make_user


def _lead(db, agent, status, **kw):
    lead = leads.create_from_like(db, make_client(db).id, make_client_listing(db).id)
    lead.status, lead.agent_id = status, agent.id
    for k, v in kw.items():
        setattr(lead, k, v)
    db.commit()
    return lead


def test_agent_cannot_change_own_level(db, client):
    admin = make_user(db, "admin")
    ag = make_user(db, "agent", level=50)
    r = client.patch(f"/api/users/{ag.id}", json={"level": 90}, headers=auth(ag))
    assert r.status_code == 403
    # the deal form: an agent's agent_level is ignored, the effective level is used
    r = client.post("/api/deals", json={"agent_id": ag.id, "price": 1000, "agent_level": 90, "rent_from": "2026-01-10"},
                    headers=auth(ag))
    assert r.status_code == 200, r.text
    assert db.get(Deal, r.json()["id"]).agent_level == 50
    assert client.patch(f"/api/users/{ag.id}", json={"level": 70}, headers=auth(admin)).status_code == 200
    db.expire_all()
    assert db.get(User, ag.id).level == 70


def test_agent_board_has_no_operator_stages_and_cannot_move_back(db, client):
    ag = make_user(db, "agent")
    lead = _lead(db, ag, "agent_assigned")
    board = client.get("/api/leads/board", headers=auth(ag)).json()
    assert "new" not in board and "operator_taken" not in board and [c["id"] for c in board["agent_assigned"]] == [lead.id]
    assert "new" in client.get("/api/leads/board", headers=auth(make_user(db, "operator"))).json()
    assert client.post(f"/api/leads/{lead.id}/take", headers=auth(ag)).status_code == 403
    assert client.post(f"/api/leads/{lead.id}/cancel", json={"reason": "x"}, headers=auth(ag)).status_code == 403
    assert client.post(f"/api/leads/{lead.id}/assign", json={"agent_id": ag.id}, headers=auth(ag)).status_code == 403
    # outcomes only after the viewing time is chosen
    assert client.post(f"/api/leads/{lead.id}/outcome", json={"outcome": "rented"}, headers=auth(ag)).status_code == 409


def test_contract_prefill_from_lead(db, client):
    ag = make_user(db, "agent")
    owner = Owner(name="Гиорги", phone="+995 555 12 34 56")
    db.add(owner)
    db.flush()
    p = Property(status="published", agent_id=ag.id, owner_id=owner.id, address="Палиашвили 10", district="Ваке",
                 cadastral_code="01.14.12.003.045", price=850, area=70.5, floor=4)
    db.add(p)
    db.commit()
    lead = _lead(db, ag, "viewing_done", property_id=p.id, contacts_shared_at=datetime.now(timezone.utc))
    r = client.get(f"/api/leads/{lead.id}/contract", headers=auth(ag))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["type"] == "rent" and out["property_id"] == p.id and out["district"] == "Ваке"
    assert out["data"] == {"client_full_name": "Анна", "owner_full_name": "Гиорги", "cadastral_code": "01.14.12.003.045",
                           "street_house": "Палиашвили 10", "floor": "4", "apartment_area": "70.5", "price_usd": "850",
                           "deposit_usd": "850"}
    assert out["client"]["phone"] == "+995 555 000 111" and out["owner"]["phone"] == "+995 555 12 34 56"

    # the deal's dates and price win once it is filled
    deal = Deal(lead_id=lead.id, agent_id=ag.id, price=900, rent_from=date(2026, 3, 5), rent_to=date(2027, 3, 5))
    db.add(deal)
    db.flush()
    db.get(Lead, lead.id).deal_id = deal.id
    db.commit()
    data = client.get(f"/api/leads/{lead.id}/contract", headers=auth(ag)).json()["data"]
    assert data["price_usd"] == "900" and data["start_rent"] == "2026-03-05" and data["end_rent"] == "2027-03-05"
    assert data["pay_day"] == "5"

    # another agent: not his lead
    assert client.get(f"/api/leads/{lead.id}/contract", headers=auth(make_user(db, "agent"))).status_code == 403
