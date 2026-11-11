"""Stage 7: a deal picks its object from the base (search, prefill, another agent's object blocked); «Объекта нет в
базе» creates an object the operator must verify before the deal is confirmed; the matcher sends known objects back."""
from app.models import Deal, Owner, Property
from tests.conftest import auth, make_listing, make_user

PHONE = "+995 599 20 30 40"


def _object(db, client, agent, lid="ss1", cadastral="01.14.05.003.017"):
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, lid).id}, headers=auth(agent)).json()["id"]
    client.post(f"/api/properties/{pid}/owner", json={"is_owner": True, "owner_name": "Нана", "owner_phone": PHONE},
                headers=auth(agent))
    client.patch(f"/api/properties/{pid}", json={"cadastral_code": cadastral, "post_fields": {"metro": "Rustaveli"}},
                 headers=auth(agent))
    return pid


def _search(client, user, **params):
    return client.get("/api/properties/deal-search", params=params, headers=auth(user)).json()["items"]


def test_picker_search_scoping_and_prefill(db, client):
    zaza = make_user(db, "agent", "Zaza Beridze", agent_tag="Zaza")
    levan = make_user(db, "agent", "Леван")
    nino = make_user(db, "operator", "Нино")
    pid = _object(db, client, zaza)

    # the owner's agent finds it by address, #id, cadastral and the owner's phone in any format, with all the data
    for q in ("Палиашвили", f"#{pid}", "01.14.05", "599203040"):
        rows = _search(client, zaza, q=q)
        assert [r["id"] for r in rows] == [pid], q
    f = rows[0]["fields"]
    assert rows[0]["blocked"] is None and f["owner_phone"] == PHONE and f["owner_name"] == "Нана"
    assert f["address"] == "ул. Палиашвили 10" and f["cadastral"] == "01.14.05.003.017" and f["metro"] == "Rustaveli"
    assert f["price"] == 850 and f["ad_link"] == "https://ss.ge/ss1" and f["is_exclusive"] is False
    # another agent sees the object only as «закреплён», no owner, no data
    r = _search(client, levan, q="599 20 30 40")[0]
    assert r["blocked"] == "Объект закреплён за #Zaza" and r["fields"] is None
    assert _search(client, nino, pid=pid)[0]["fields"]["owner_phone"] == PHONE  # staff see everything
    assert _search(client, zaza, q="x") == []

    # the deal takes the object's data whatever the form says, and links the object
    r = client.post("/api/deals", headers=auth(zaza), json={
        "property_id": pid, "price": 1, "address": "липовый адрес", "owner_phone": "+995 500 00 00 00",
        "rent_from": "2030-01-01"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["property_id"] == pid and d["address"] == "ул. Палиашвили 10" and d["owner_phone"] == PHONE
    assert d["price"] == 850 and d["cadastral"] == "01.14.05.003.017" and d["metro"] == "Rustaveli"
    card = client.get(f"/api/deals/{d['id']}", headers=auth(zaza)).json()
    assert card["property"] == {"id": pid, "address": "ул. Палиашвили 10", "status": "owner_confirmed", "needs_check": False}
    assert client.get(f"/api/properties/{pid}", headers=auth(zaza)).json()["deals"][0]["id"] == d["id"]

    # another agent can't file a deal on it; staff can
    r = client.post("/api/deals", headers=auth(levan), json={"property_id": pid, "price": 850, "rent_from": "2030-01-01"})
    assert r.status_code == 409 and r.json() == {"detail": "Объект закреплён за #Zaza", "code": "object_taken"}
    r = client.post("/api/deals", headers=auth(nino), json={"property_id": pid, "agent_id": levan.id, "price": 850})
    assert r.status_code == 200 and r.json()["owner_phone"] == PHONE


def test_new_object_from_deal_needs_verification(db, client):
    levan = make_user(db, "agent", "Леван", agent_tag="Levan")
    nino = make_user(db, "operator", "Нино")
    body = {"new_object": True, "price": 700, "address": "ул. Чавчавадзе 5", "cadastral": "01.10.13.001.002",
            "ad_link": "https://myhome.ge/123", "metro": "Rustaveli", "owner_name": "Гоча", "owner_phone": "555 11 22 33",
            "rent_from": "2030-02-01"}
    r = client.post("/api/deals", headers=auth(levan), json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    p = db.get(Property, d["property_id"])
    assert p.source_type == "deal" and p.verified_at is None and p.agent_id == levan.id and p.address == "ул. Чавчавадзе 5"
    assert p.cadastral_code == "01.10.13.001.002" and p.source_url == "https://myhome.ge/123" and p.post_fields == {"metro": "Rustaveli"}
    assert db.get(Owner, p.owner_id).agent_id == levan.id  # stage 6.5: the owner is Levan's
    assert d["status"] == "pending" and d["owner_phone"] == "555 11 22 33"
    assert client.get(f"/api/deals/{d['id']}", headers=auth(nino)).json()["property"]["needs_check"] is True
    assert client.get(f"/api/properties/{p.id}", headers=auth(nino)).json()["needs_check"] is True

    # confirm is blocked until the operator verifies the object
    r = client.post(f"/api/deals/{d['id']}/confirm", headers=auth(nino))
    assert r.status_code == 409 and r.json()["code"] == "object_unverified"
    assert client.post(f"/api/properties/{p.id}/verify", headers=auth(levan)).status_code == 403
    v = client.post(f"/api/properties/{p.id}/verify", headers=auth(nino)).json()
    assert v["verified_at"] and v["needs_check"] is False
    r = client.post(f"/api/deals/{d['id']}/confirm", headers=auth(nino))
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    db.expire_all()
    assert db.get(Property, p.id).status == "rented"


def test_new_object_known_to_matcher_must_be_picked(db, client):
    zaza = make_user(db, "agent", "Zaza Beridze", agent_tag="Zaza")
    pid = _object(db, client, zaza)
    n = db.query(Property).count()
    for extra in ({"cadastral": "01 14 05 003 017"}, {"ad_link": "https://ss.ge/ss1"},
                  {"owner_phone": "599203040", "address": "Палиашвили 10"}):
        body = {"new_object": True, "price": 850, "address": "где-то", "rent_from": "2030-01-01", **extra}
        r = client.post("/api/deals", headers=auth(zaza), json=body)
        assert r.status_code == 409 and r.json()["code"] == "object_exists", extra
        assert f"#{pid}" in r.json()["detail"]
    assert db.query(Property).count() == n and db.query(Deal).count() == 0
    # the same owner's phone at another address is a new object; another agent is stopped by the owner binding (6.5)
    levan = make_user(db, "agent", "Леван")
    r = client.post("/api/deals", headers=auth(levan), json={"new_object": True, "price": 500, "address": "ул. Другая 1",
                                                            "owner_phone": PHONE, "rent_from": "2030-01-01"})
    assert r.status_code == 409 and r.json()["code"] == "owner_taken"
    assert db.query(Property).count() == n and db.query(Deal).count() == 0
