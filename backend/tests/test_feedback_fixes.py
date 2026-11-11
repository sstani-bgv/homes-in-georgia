"""Product-owner feedback: the agent's commission is fixed; «База объектов» lists only published objects."""
from app.models import Deal, Property
from app.services.common import now
from tests.conftest import auth, make_user


def test_agent_cannot_set_commission(db, client):
    agent = make_user(db, "agent", "Леван")
    nino = make_user(db, "operator", "Нино")
    body = {"price": 1000, "deal_type": "rent", "sub_type": "long", "rent_from": "2026-11-01",
            "commission_pct": 90, "commission_total": 900, "address": "ул. Палиашвили 10", "new_object": True}
    # the preview and the saved deal both ignore the agent's numbers: 50% of a month's rent
    assert client.post("/api/deals/calc", json=body, headers=auth(agent)).json()["commission_total"] == 500
    r = client.post("/api/deals", json=body, headers=auth(agent))
    assert r.status_code == 200, r.text
    d = db.get(Deal, r.json()["id"])
    assert (float(d.commission_pct), float(d.commission_total)) == (50, 500)
    sale = client.post("/api/deals/calc", json={**body, "deal_type": "sale", "price": 100000}, headers=auth(agent)).json()
    assert sale["commission_total"] == 3000
    # staff still set another commission
    r = client.post("/api/deals", json={**body, "agent_id": agent.id, "address": "ул. Абашидзе 5"}, headers=auth(nino))
    assert r.status_code == 200, r.text
    assert float(db.get(Deal, r.json()["id"]).commission_total) == 900


def test_base_lists_only_published_objects(db, client):
    agent = make_user(db, "agent", "Зураб")  # his own objects: an agent sees no one else's (test_board_hide)
    rows = {k: Property(status=st, agent_id=agent.id, published_at=pub) for k, (st, pub) in {
        "published": ("published", now()), "rented_after_post": ("rented", now()), "rented_never_posted": ("rented", None),
        "taken": ("taken", None), "review": ("review", None), "archived": ("archived", now())}.items()}
    db.add_all(rows.values())
    db.commit()
    got = {p["id"] for p in client.get("/api/properties", params={"base": 1}, headers=auth(agent)).json()["items"]}
    assert got == {rows["published"].id, rows["rented_after_post"].id}
    one = client.get("/api/properties", params={"base": 1, "status": "rented"}, headers=auth(agent)).json()["items"]
    assert [p["id"] for p in one] == [rows["rented_after_post"].id]


def test_contract_sent_to_telegram(db, client, monkeypatch):
    from app.telegram import client as tg
    calls = []
    monkeypatch.setattr(tg, "call", lambda method, files=None, **kw: calls.append((method, files, kw)) or {"message_id": 1})
    body = {"filename": "Contract rent/RU 01.14.doc", "html": "<p>Договор</p>"}
    assert client.post("/api/contracts/send", json=body, headers=auth(make_user(db, "agent"))).status_code == 409  # no Telegram
    r = client.post("/api/contracts/send", json=body, headers=auth(make_user(db, "agent", tg_id=4242)))
    assert r.status_code == 200, r.text
    (method, files, kw), = calls
    name, data, mime = files["document"]
    assert (method, kw["chat_id"], name, mime) == ("sendDocument", 4242, "Contract_rent_RU_01.14.doc", "application/msword")
    assert data.decode("utf-8") == "﻿<p>Договор</p>"
