"""David 11-23: a contract signed by both parties → the deal is due in 24 h. Overdue: ⛔ to the agent + operator,
the agent's CRM is locked to that deal (423 deal_overdue) until a deal is entered, the contract is closed or an
operator lifts the lock. Staff are never locked."""
from datetime import timedelta

import pytest

from app import worker
from app.models import Contract, Job, Lead
from app.services import contracts as C
from app.services.common import now, now_text
from tests.conftest import auth, make_client, make_listing, make_user

PAGE = "<!doctype html><html><body><p>x</p></body></html>"


@pytest.fixture(autouse=True)
def fake_chrome(monkeypatch):
    monkeypatch.setattr(C, "render_pdf", lambda page: b"%PDF-1.4")


def _signed(db, client, ag):
    cl = make_client(db, external_id=910000 + ag.id)
    lead = Lead(client_id=cl.id, listing_id=make_listing(db, f"dd{ag.id}").id, agent_id=ag.id, status="viewing_done",
                created_at=now_text(), updated_at=now_text())
    db.add(lead)
    db.commit()
    c = client.post("/api/contracts", json={"kind": "rent", "lang": "RU", "html": PAGE, "lead_id": lead.id, "address": "Paliashvili 10"},
                    headers=auth(ag)).json()
    assert client.post(f"/api/contracts/{c['id']}/sign", headers=auth(ag)).json()["status"] == "signed"
    return c["id"], lead


def _expire(db, cid):
    db.expire_all()
    c = db.get(Contract, cid)
    c.deal_due_at = now() - timedelta(minutes=1)
    db.query(Job).filter_by(kind="deal_due", ref=str(cid), done_at=None).update({"due_at": c.deal_due_at})
    db.commit()


def test_deadline_notice_lock_and_deal_lifts(db, client, sent):
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    cid, lead = _signed(db, client, ag)
    c = db.get(Contract, cid)
    assert timedelta(hours=23, minutes=59) < c.deal_due_at - now() <= timedelta(hours=24)
    job = db.query(Job).filter_by(kind="deal_due", ref=str(cid), done_at=None).one()
    assert job.due_at == c.deal_due_at
    # before the deadline: free
    assert client.get("/api/auth/me", headers=auth(ag)).json()["deal_overdue"] is None
    assert client.post("/api/contracts/draft", json={"kind": "rent"}, headers=auth(ag)).status_code == 200
    _expire(db, cid)
    worker.tick()
    [(c_arg, agents)] = sent.of("deal_overdue")
    assert c_arg.id == cid and agents == [ag.id]
    # locked: other actions 423, reads / the deal / this contract / profile pass
    me = client.get("/api/auth/me", headers=auth(ag)).json()["deal_overdue"]
    assert me["id"] == cid and me["lead_id"] == lead.id and me["address"] == "Paliashvili 10"
    r = client.post("/api/contracts/draft", json={"kind": "rent"}, headers=auth(ag))
    assert r.status_code == 423 and r.json()["code"] == "deal_overdue" and f"#{cid}" in r.json()["detail"]
    assert r.json()["data"]["contract"]["id"] == cid
    assert client.get("/api/contracts", headers=auth(ag)).status_code == 200
    assert client.patch("/api/auth/me", json={"phone": "+995555000000"}, headers=auth(ag)).status_code == 200
    assert client.post(f"/api/contracts/{cid}/deal-lead", headers=auth(ag)).status_code == 200
    assert client.post("/api/contracts/draft", json={"kind": "rent"}, headers=auth(op)).status_code == 200  # staff: never
    # the deal (pending) lifts it
    r = client.post("/api/deals", json={"lead_id": lead.id, "price": 900, "rent_from": "2026-12-01", "rent_to": "2027-12-01"},
                    headers=auth(ag))
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me", headers=auth(ag)).json()["deal_overdue"] is None
    assert client.post("/api/contracts/draft", json={"kind": "rent"}, headers=auth(ag)).status_code == 200


def test_deal_in_time_cancels_the_job(db, client, sent):
    ag = make_user(db, "agent")
    cid, lead = _signed(db, client, ag)
    assert client.post("/api/deals", json={"lead_id": lead.id, "price": 900, "rent_from": "2026-12-01", "rent_to": "2027-12-01"},
                       headers=auth(ag)).status_code == 200
    assert db.query(Job).filter_by(kind="deal_due", ref=str(cid), done_at=None).count() == 0
    _expire(db, cid)  # even past the date: a deal is there
    assert client.get("/api/auth/me", headers=auth(ag)).json()["deal_overdue"] is None


def test_operator_lift_and_close_unlock(db, client, sent):
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    cid, _ = _signed(db, client, ag)
    _expire(db, cid)
    assert client.post(f"/api/contracts/{cid}/deal-due/lift", headers=auth(ag)).status_code == 403
    assert client.post(f"/api/contracts/{cid}/deal-due/lift", headers=auth(op)).json()["deal_due_at"] is None
    assert client.get("/api/auth/me", headers=auth(ag)).json()["deal_overdue"] is None
    worker.tick()
    assert sent.of("deal_overdue") == []  # the job went with the lock
    # a second contract, closed without a deal (allowed while locked: it is this contract) → unlocked
    ag2 = make_user(db, "agent")
    cid2, _ = _signed(db, client, ag2)
    _expire(db, cid2)
    assert client.post("/api/contracts/draft", json={"kind": "rent"}, headers=auth(ag2)).status_code == 423
    assert client.post(f"/api/contracts/{cid2}/close", json={"reason": "deal_failed"}, headers=auth(ag2)).status_code == 200
    assert client.get("/api/auth/me", headers=auth(ag2)).json()["deal_overdue"] is None


def test_esign_by_both_parties_starts_the_deadline(db, client):
    from app.models import ContractSigner
    from app.services import esign
    ag = make_user(db, "agent")
    c = client.post("/api/contracts", json={"kind": "rent", "lang": "RU", "html": PAGE}, headers=auth(ag)).json()
    for role in ("owner", "tenant"):
        db.add(ContractSigner(contract_id=c["id"], role=role, token=role * 10, doc_sha256="x", full_name=role, signed_at=now()))
    db.commit()
    assert esign.finalize(db, c["id"]) == "ok"
    db.expire_all()
    assert db.get(Contract, c["id"]).deal_due_at and db.query(Job).filter_by(kind="deal_due", ref=str(c["id"]), done_at=None).count() == 1
