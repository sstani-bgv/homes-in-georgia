"""Audit fixes: an agent can't raise his own payout, a hidden owner phone doesn't leak through owner_id, a contract
doesn't open a foreign object's card, private files aren't reached through path tricks."""
import pytest

from app.models import Contract, Lead, Owner, Property
from app.services import deals
from app.services.errors import DomainError
from tests.conftest import auth, make_client, make_user


# ---------------------------------------------------------------- 1. money

def test_agent_cannot_inflate_payout_with_recruit_amount(db, client):
    ag = make_user(db, "agent")
    body = {"price": 1000, "address": "y", "recruit_amount": -400}
    r = client.post("/api/deals", json=body, headers=auth(ag))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["commission_total"] == 500 and d["recruit_amount"] == 0 and d["agent_amount"] == 250
    # /deals/calc mirrors the form: the same numbers, a negative or foreign recruit share has no effect
    c = client.post("/api/deals/calc", json={**body, "recruit_pct": -50, "platform_amount": 0, "agent_level": 90},
                    headers=auth(ag)).json()
    assert c["agent_amount"] == 250 and c["recruit_amount"] == 0 and c["platform_amount"] == 250
    # a recruit's share needs a recruit and stays within 0..100
    rec = make_user(db, "agent")
    c = client.post("/api/deals/calc", json={"price": 1000, "recruit_id": rec.id, "recruit_pct": 10}, headers=auth(ag)).json()
    assert c["recruit_amount"] == 50 and c["agent_amount"] == 200
    for pct in (-10, 150):
        r = client.post("/api/deals", json={"price": 1000, "address": "y", "recruit_id": rec.id, "recruit_pct": pct}, headers=auth(ag))
        assert r.status_code == 422, (pct, r.text)


def test_calc_invariants():
    for kw in ({"price": -1000}, {"price": 1000, "commission_pct": -5}, {"price": 1000, "commission_total": -1},
               {"price": 1000, "platform_amount": -100}, {"price": 1000, "recruit_amount": -1},
               {"price": 1000, "recruit_pct": -1}, {"price": 1000, "recruit_pct": 101},
               {"price": 1000, "platform_amount": 400, "recruit_amount": 200},  # 600 out of a 500 commission
               {"price": 1000, "agent_level": 150},
               {"price": 1000, "split": [{"who": "agent", "pct": 150}, {"who": "platform", "pct": -50}]}):
        with pytest.raises(DomainError) as e:
            deals.calc(**kw)
        assert e.value.status == 422, kw
    # what staff legitimately do still works: commercial 100%, a manual commission, everything to the agent
    assert deals.calc(price=1000, commission_pct=100)["commission_total"] == 1000
    r = deals.calc(price=1000, commission_total=700, platform_amount=0, agent_level=50)
    assert r["agent_amount"] == 700
    r = deals.calc(price=1000, commission_total=500, platform_amount=250, recruit_amount=250)
    assert r["agent_amount"] == 0


def test_split_without_platform_row_adds_up():
    r = deals.calc(price=2, commission_pct=50, split=[{"who": "agent", "pct": 50}, {"who": "partner", "pct": 50}])
    assert r["commission_total"] == 1 and sum(x["amount"] for x in r["distribution"]) == 1
    r = deals.calc(price=1000, commission_total=100, split=[{"who": "agent", "pct": 33.33}, {"who": "partner", "pct": 33.33},
                                                             {"who": "other", "pct": 33.34}])
    assert sum(x["amount"] for x in r["distribution"]) == 100
    assert r["agent_amount"] + r["recruit_amount"] + r["platform_amount"] <= 100
    r = deals.calc(price=3, commission_pct=50, split=[{"who": "agent", "pct": 50}, {"who": "platform", "pct": 50}])
    assert sum(x["amount"] for x in r["distribution"]) == 2  # the platform row still takes the remainder


# ---------------------------------------------------------------- 2. owner picker

def _owners(db):
    me, other = make_user(db, "agent"), make_user(db, "agent")
    free = Owner(name="Свободный", phone="+995 577 00 00 01", phone_norm="577000001")
    bound = Owner(name="Чужой", phone="+995 577 00 00 02", phone_norm="577000002", agent_id=other.id)
    on_foreign = Owner(name="На чужом объекте", phone="+995 577 00 00 03", phone_norm="577000003")
    mine_bound = Owner(name="Мой", phone="+995 577 00 00 04", phone_norm="577000004", agent_id=me.id)
    on_mine = Owner(name="На моём объекте", phone="+995 577 00 00 05", phone_norm="577000005", agent_id=other.id)
    db.add_all([free, bound, on_foreign, mine_bound, on_mine])
    db.flush()
    db.add_all([Property(address="Чужой 1", agent_id=other.id, owner_id=on_foreign.id, status="published"),
                Property(address="Мой 1", agent_id=me.id, owner_id=on_mine.id, status="taken")])
    db.commit()
    return me, other, free, bound, on_foreign, mine_bound, on_mine


def test_deal_owner_pick_follows_the_owners_list_rule(db, client):
    me, other, free, bound, on_foreign, mine_bound, on_mine = _owners(db)
    op = make_user(db, "operator")
    for o in (bound, on_foreign):
        r = client.post("/api/deals", json={"price": 1000, "address": "y", "owner_id": o.id}, headers=auth(me))
        assert r.status_code == 403, (o.name, r.text)
        assert o.phone not in r.text
    for o in (free, mine_bound, on_mine):
        r = client.post("/api/deals", json={"price": 1000, "address": "y", "owner_id": o.id}, headers=auth(me))
        assert r.status_code == 200 and r.json()["owner_phone"] == o.phone, (o.name, r.text)
    r = client.post("/api/deals", json={"price": 1000, "address": "y", "owner_id": bound.id}, headers=auth(op))
    assert r.status_code == 200 and r.json()["owner_phone"] == bound.phone


def test_property_owner_pick_follows_the_owners_list_rule(db, client):
    me, other, free, bound, on_foreign, mine_bound, on_mine = _owners(db)
    p = Property(address="Мой 2", agent_id=me.id, status="taken")
    db.add(p)
    db.commit()
    for o in (bound, on_foreign):
        r = client.patch(f"/api/properties/{p.id}", json={"owner_id": o.id}, headers=auth(me))
        assert r.status_code == 403, (o.name, r.text)
        assert o.phone not in r.text
    db.expire_all()
    assert db.get(Property, p.id).owner_id is None
    r = client.patch(f"/api/properties/{p.id}", json={"owner_id": free.id}, headers=auth(me))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Property, p.id).owner_id == free.id and db.get(Owner, free.id).agent_id == me.id


# ---------------------------------------------------------------- 3. contracts

def test_contract_with_own_lead_does_not_open_a_foreign_object(db, client):
    me, other = make_user(db, "agent"), make_user(db, "agent")
    mine = Property(address="Мой 1", agent_id=me.id, status="taken")
    foreign = Property(address="Чужой 2", agent_id=other.id, status="published")
    platform = Property(address="Платформа 3", agent_id=None, status="published")
    db.add_all([mine, foreign, platform])
    db.commit()
    cl = make_client(db)
    t = "2026-11-17"
    lead = Lead(client_id=cl.id, property_id=mine.id, agent_id=me.id, status="new", created_at=t, updated_at=t)
    bare = Lead(client_id=cl.id, agent_id=me.id, status="new", created_at=t, updated_at=t)  # no object yet
    db.add_all([lead, bare])
    db.commit()
    h = auth(me)
    assert client.get(f"/api/properties/{foreign.id}", headers=h).status_code == 404
    d = client.post("/api/contracts/draft", json={"lead_id": lead.id, "property_id": foreign.id, "kind": "rent",
                                                  "data": {"x": "1"}}, headers=h).json()
    assert d["property_id"] == mine.id  # the lead's object, not the one sent along
    for pid in (foreign.id, platform.id):  # a lead without an object: an explicit one must be his to use
        r = client.post("/api/contracts/draft", json={"lead_id": bare.id, "property_id": pid, "data": {"x": "1"}}, headers=h)
        assert r.status_code in (403, 404), r.text
        r = client.post("/api/contracts/draft", json={"property_id": pid, "data": {"x": "1"}}, headers=h)
        assert r.status_code in (403, 404), r.text
    # «Привязать к заявке» of a standalone contract: an object sent along must be his to use (11-26 the endpoint takes
    # one for «Привязать объект»), with a lead that has one the lead's object comes along
    c = client.post("/api/contracts/draft", json={"kind": "rent", "data": {"x": "2"}}, headers=h).json()
    for pid in (foreign.id, platform.id):
        r = client.post(f"/api/contracts/{c['id']}/link", json={"lead_id": bare.id, "property_id": pid}, headers=h)
        assert r.status_code in (403, 404), r.text
        r = client.post(f"/api/contracts/{c['id']}/link", json={"property_id": pid}, headers=h)
        assert r.status_code in (403, 404), r.text
    c = client.post("/api/contracts/draft", json={"kind": "rent", "data": {"x": "3"}}, headers=h).json()
    r = client.post(f"/api/contracts/{c['id']}/link", json={"lead_id": lead.id, "property_id": foreign.id}, headers=h)
    assert r.status_code == 200 and r.json()["property_id"] == mine.id, r.text
    assert not db.query(Contract).filter(Contract.property_id.in_((foreign.id, platform.id))).count()
    assert client.get(f"/api/properties/{foreign.id}", headers=h).status_code == 404
    assert client.get(f"/api/properties/{platform.id}", headers=h).status_code == 404


# ---------------------------------------------------------------- 4. private files

def test_private_media_not_reached_by_path_tricks(db, client):
    from app.services.properties import media_root
    me, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    p = Property(address="Чужой", agent_id=other.id, status="published")
    db.add(p)
    db.commit()
    root = media_root()
    for rel in (f"private/properties/{p.id}/passport.jpg", f"private/contracts/{p.id}/c.pdf", f"private/nda/{p.id}/n.pdf",
                f"previews/private/properties/{p.id}/passport.jpg.webp", "avatars/u1.jpg"):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\xff\xd8\xff")
    for path in (f"x/%2e%2e/private/properties/{p.id}/passport.jpg", f"avatars/../private/properties/{p.id}/passport.jpg",
                 f"./private/properties/{p.id}/passport.jpg", f"private//properties/{p.id}/passport.jpg",
                 f"private/properties/{p.id}/passport.jpg", f"previews/private/properties/{p.id}/passport.jpg.webp",
                 f"private/contracts/{p.id}/c.pdf", f"private/nda/{p.id}/n.pdf"):
        r = client.get(f"/api/media/{path}", headers=auth(me))
        assert r.status_code == 404, (path, r.status_code)
    # the object's agent and staff: the passport; other private/ prefixes: never through this endpoint
    assert client.get(f"/api/media/private/properties/{p.id}/passport.jpg", headers=auth(other)).status_code == 200
    assert client.get(f"/api/media/private/properties/{p.id}/passport.jpg", headers=auth(op)).status_code == 200
    assert client.get(f"/api/media/private/contracts/{p.id}/c.pdf", headers=auth(op)).status_code == 404
    assert client.get(f"/api/media/private/nda/{p.id}/n.pdf", headers=auth(op)).status_code == 404
    assert client.get("/api/media/avatars/u1.jpg", headers=auth(me)).status_code == 200
