"""Leads after the viewing (Stanislav 11-11): «Договор» → «Договор подписан» → «Сделка» → «Сдано»; stage chip counts."""
from datetime import datetime, timezone

from app.models import Contract, Lead
from app.services import deals, leads
from tests.conftest import auth, make_client, make_client_listing as make_listing, make_user

CX = {"client_full_name": "Анна Иванова", "owner_full_name": "Гиорги Беридзе", "cadastral_code": "01.14.12.003.045",
      "street_house": "Абашидзе 10", "price_usd": "850", "deposit_usd": "850", "start_rent": "2026-12-01",
      "end_rent": "2027-12-01", "rent_duration_months": "12", "pay_day": "1", "client_birth_day": "1990-05-04",
      "owner_birth_day": "", "with_pets": False}


def signed_contract(db, lead, data=CX, kind="rent", status="signed"):
    """The contracts session's row for a lead, signed (the wizard's CxData kept in `data`)."""
    c = Contract(lead_id=lead.id, kind=kind, lang="RU", html="<p>договор</p>", status=status, data=data,
                 address=data.get("street_house"), cadastral=data.get("cadastral_code"),
                 signed_at=datetime.now(timezone.utc) if status != "draft" else None)
    db.add(c)
    db.commit()
    return c


def _lead_at(db, status, agent):
    lead = leads.create_from_like(db, make_client(db, external_id=db.query(Lead).count() + 9000).id,
                                  make_listing(db, lid=f"tg{2000 + db.query(Lead).count()}").id)
    lead.status, lead.agent_id = status, agent.id
    db.commit()
    return lead


def test_contract_stage_transitions(db, client):
    agent, other = make_user(db, "agent", "Георгий"), make_user(db, "agent", "Чужой")
    lead = _lead_at(db, "slots_sent", agent)
    r = client.post(f"/api/leads/{lead.id}/start-contract", headers=auth(agent))
    assert r.status_code == 409  # before the viewing time is chosen: no contract yet
    lead.status = "viewing_done"
    db.commit()
    assert client.post(f"/api/leads/{lead.id}/start-contract", headers=auth(other)).status_code == 403
    r = client.post(f"/api/leads/{lead.id}/start-contract", headers=auth(agent))
    assert r.status_code == 200 and r.json()["status"] == "contract" and r.json()["contract"] is None

    # contract → deal: blocked until signed, for the agent and for staff alike
    for who in (agent, make_user(db, "operator")):
        r = client.post("/api/deals", headers=auth(who), json={"lead_id": lead.id, "price": 850, "agent_id": agent.id})
        assert r.status_code == 409 and r.json()["code"] == "contract_not_signed"
    draft = signed_contract(db, lead, status="draft")
    assert client.post("/api/deals", headers=auth(agent), json={"lead_id": lead.id, "price": 850}).status_code == 409
    draft.status = "signed"
    db.commit()
    r = client.post("/api/deals", headers=auth(agent), json={"lead_id": lead.id, "price": 850, "rent_from": "2026-12-01"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Lead, lead.id).status == "deal_pending"

    # an agent can't skip the contract: a deal straight from the viewing is refused
    skip = _lead_at(db, "viewing_done", agent)
    r = client.post("/api/deals", headers=auth(agent), json={"lead_id": skip.id, "price": 850})
    assert r.status_code == 409 and r.json()["code"] == "bad_status"
    # «Снимает» after the viewing → «Договор», not straight to the deal
    assert leads.set_outcome(db, skip.id, "agent", "rented", actor=agent).status == "contract"


def test_prefill_mapping_contract_to_deal():
    f = deals.prefill_from_contract("rent", CX)
    assert f == {"deal_type": "rent", "sub_type": "long", "price": 850.0, "address": "Абашидзе 10",
                 "cadastral": "01.14.12.003.045", "client_name": "Анна Иванова", "owner_name": "Гиорги Беридзе",
                 "client_birth": "1990-05-04", "rent_from": "2026-12-01", "rent_to": "2027-12-01",
                 "terms": {"deposit_usd": "850", "pay_day": "1", "rent_duration_months": "12"}}
    s = deals.prefill_from_contract("sale", {"sale_price": "120 000", "start_rent": "2026-12-01", "transfer_date": "2027-01-10"})
    assert s == {"deal_type": "sale", "price": 120000.0, "terms": {"transfer_date": "2027-01-10"}}
    assert deals.prefill_from_contract("comm", {"price_usd": "abc"})["sub_type"] == "commercial"


def test_deal_prefill_endpoint(db, client):
    agent = make_user(db, "agent", "Георгий")
    lead = _lead_at(db, "contract", agent)
    assert client.get(f"/api/leads/{lead.id}/deal-prefill", headers=auth(agent)).json() == {"contract": None, "fields": {}}
    c = signed_contract(db, lead)
    d = client.get(f"/api/leads/{lead.id}/deal-prefill", headers=auth(agent)).json()
    assert d["contract"]["id"] == c.id and d["fields"]["price"] == 850 and d["fields"]["rent_to"] == "2027-12-01"
    assert client.get(f"/api/leads/{lead.id}/deal-prefill", headers=auth(make_user(db, "agent"))).status_code == 403


def test_stage_counts_scoped(db, client):
    a, b = make_user(db, "agent", "А"), make_user(db, "agent", "Б")
    for st, ag in (("contract", a), ("contract", a), ("deal_pending", a), ("contract", b)):
        _lead_at(db, st, ag)
    leads.create_from_like(db, make_client(db, external_id=7777).id, make_listing(db, lid="tg3999").id)  # new, no agent
    assert client.get("/api/leads/counts", headers=auth(a)).json() == {"contract": 2, "deal_pending": 1}
    staff = client.get("/api/leads/counts", headers=auth(make_user(db, "operator"))).json()
    assert staff["contract"] == 3 and staff["deal_pending"] == 1 and staff["new"] == 1


def test_wizard_data_saved_and_prefills_the_deal(db, client, monkeypatch):
    """The wizard sends its CxData with the contract (contracts.data); after «Договор подписан» the deal page gets it."""
    from app.services import contracts
    from tests.test_contracts import PDF, _new
    monkeypatch.setattr(contracts, "render_pdf", lambda page: PDF)
    agent = make_user(db, "agent", "Георгий")
    lead = _lead_at(db, "viewing_done", agent)
    client.post(f"/api/leads/{lead.id}/start-contract", headers=auth(agent))
    c = _new(client, agent, lead_id=lead.id, data={**CX, "nested": {"x": 1}})
    assert client.get(f"/api/leads/{lead.id}/deal-prefill", headers=auth(agent)).json()["contract"] is None  # draft
    assert client.post(f"/api/contracts/{c['id']}/sign", headers=auth(agent)).status_code == 200
    d = client.get(f"/api/leads/{lead.id}/deal-prefill", headers=auth(agent)).json()
    assert d["contract"]["status"] == "signed" and d["fields"]["price"] == 850 and d["fields"]["terms"]["pay_day"] == "1"
    assert "nested" not in db.get(Contract, c["id"]).data
