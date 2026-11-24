"""David 11-24: an agent who typed another agent's owner asks the operator to move him over; the operator's yes moves
the owner and links him to the asking agent's object; an exclusive with the owner's agent blocks it."""
from app.models import Owner, Property
from app.services import change_requests as CR
from app.services import properties as P
from tests.conftest import auth, make_listing, make_user


def _two(db, client):
    zaza, luka, nino = make_user(db, "agent", "Заза"), make_user(db, "agent", "Лука"), make_user(db, "operator", "Нино")
    o, _ = P.upsert_owner(db, "Нана", "+995577939339")
    o.agent_id = zaza.id
    db.commit()
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, "ss9101").id, "force": True}, headers=auth(luka)).json()["id"]
    return zaza, luka, nino, o, pid


def test_agent_asks_for_anothers_owner_and_the_operator_moves_him(db, client, sent):
    zaza, luka, nino, o, pid = _two(db, client)
    r = client.patch(f"/api/properties/{pid}", json={"owner_name": "Нана", "owner_phone": "577939339"}, headers=auth(luka))
    assert r.status_code == 409 and r.json()["code"] == "owner_taken"
    r = client.post(f"/api/properties/{pid}/owner-request", json={"phone": "577 93 93 39"}, headers=auth(luka))
    assert r.status_code == 200 and r.json()["change_request"]["kind"] == "transfer_owner"
    assert any(zaza.id in (a[3] or []) for a in sent.of("prop_event"))  # Zaza hears of it
    CR.decide(db, r.json()["change_request"]["id"], nino, True)
    db.expire_all()
    assert db.get(Owner, o.id).agent_id == luka.id and db.get(Property, pid).owner_id == o.id


def test_no_request_against_an_exclusive(db, client, sent):
    zaza, luka, nino, o, pid = _two(db, client)
    db.add(Property(agent_id=zaza.id, owner_id=o.id, status="published", is_exclusive=True))
    db.commit()
    r = client.post(f"/api/properties/{pid}/owner-request", json={"phone": "577939339"}, headers=auth(luka))
    assert r.status_code >= 400 and r.json()["code"] == "exclusive"
