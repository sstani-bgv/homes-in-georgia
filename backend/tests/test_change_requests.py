"""Stage 6: key fields lock after the operator's verification, «Запрос оператору» (create / approve / reject), archive
with a mandatory reason, the backfill of reasons for objects archived before stage 6."""


from sqlalchemy import select
from app.models import ChangeRequest, Event, Owner, Property
from app.services import properties
from tests.conftest import auth, fill_required, make_listing, make_user


def _verified(db, client, sent, lid="ss6001"):
    """An object of agent Георгий with an owner, submitted and published by operator Нино (= verified)."""
    georgiy, nino = make_user(db, "agent", "Георгий"), make_user(db, "operator", "Нино")
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, lid).id, "force": True}, headers=auth(georgiy)).json()["id"]
    client.post(f"/api/properties/{pid}/owner", json={"is_owner": True, "owner_name": "Нана", "owner_phone": "+995 599 11 22 33"},
                headers=auth(georgiy))
    p = db.get(Property, pid)
    p.status = "shoot_scheduled"
    db.commit()
    client.post(f"/api/properties/{pid}/media", files=[("files", ("a.jpg", b"\xff\xd8", "image/jpeg"))], headers=auth(georgiy))
    fill_required(db, pid)
    client.post(f"/api/properties/{pid}/submit", headers=auth(georgiy))
    return georgiy, nino, pid


def test_agent_edits_freely_before_verification(db, client, sent):
    georgiy = make_user(db, "agent", "Георгий")
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6000").id, "force": True}, headers=auth(georgiy)).json()["id"]
    r = client.patch(f"/api/properties/{pid}", json={"price": 900, "owner_phone": "599000111", "owner_name": "Нана",
                                                    "address": "Ваке, 5"}, headers=auth(georgiy)).json()
    assert r["change_request"] is None and r["locked_fields"] == [] and r["price"] == 900 and r["owner"]["phone"] == "+995599000111"
    assert r["address"] == "Ваке, 5" and r["verified_at"] is None


def test_after_verification_the_own_agent_edits_at_once_and_the_operator_hears(db, client, sent):
    """Stanislav 11-23: the object's own agent has the operator's buttons on it — no «Запрос оператору»."""
    georgiy, nino, pid = _verified(db, client, sent)
    pub = client.post(f"/api/properties/{pid}/publish", headers=auth(nino)).json()
    assert pub["verified_at"] and client.get(f"/api/properties/{pid}", headers=auth(georgiy)).json()["locked_fields"] == []
    body = {"owner_phone": "+995 577 00 00 00", "price": 900, "description": "Свежий ремонт",
            "post_fields": {"amenities": ["WiFi"], "deposit": 500}}
    r = client.patch(f"/api/properties/{pid}", json=body, headers=auth(georgiy)).json()
    assert r["owner"]["phone"] == "+995577000000"  # not in the post: at once
    heads = [a for a in sent.of("prop_event") if "Агент изменил" in a[1]]
    assert len(heads) == 1 and "owner_phone" in heads[0][5]
    # David 11-24: what the live post shows goes to the operator («Редактирование = новый пост»)
    assert r["change_request"] and {c["field"] for c in r["change_request"]["changes"]} >= {"price", "description", "deposit", "pf:amenities"}


def _request(db, georgiy, pid, changes, reason=None):
    """A pending «Запрос оператору» as made before 11-23 (the queue still decides old ones and transfer requests)."""
    from app.services import change_requests
    cr = change_requests.create(db, "property", pid, "edit", changes, georgiy, reason)
    db.commit()
    return cr.id


def test_an_old_request_is_approved_by_the_operator(db, client, sent):
    georgiy, nino, pid = _verified(db, client, sent)
    client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    cid = _request(db, georgiy, pid, {"owner_phone": {"old": "+995599112233", "new": "+995577000000"}})
    assert [x["id"] for x in client.get("/api/change-requests", headers=auth(nino)).json()["items"]] == [cid]
    assert client.get("/api/change-requests", headers=auth(georgiy)).status_code == 403
    assert client.post(f"/api/change-requests/{cid}/decide", json={"approve": True}, headers=auth(georgiy)).status_code == 403
    d = client.post(f"/api/change-requests/{cid}/decide", json={"approve": True}, headers=auth(nino)).json()
    assert d["status"] == "approved" and d["decided_by"]["id"] == nino.id
    db.expire_all()
    assert db.get(Owner, db.get(Property, pid).owner_id).phone == "+995577000000"
    assert client.post(f"/api/change-requests/{cid}/decide", json={"approve": True}, headers=auth(nino)).json()["code"] == "bad_status"


def test_reject_needs_comment_and_changes_nothing(db, client, sent):
    georgiy, nino, pid = _verified(db, client, sent)
    client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    cid = _request(db, georgiy, pid, {"address": {"old": "ул. Палиашвили 10", "new": "Другой адрес"}})
    assert client.post(f"/api/change-requests/{cid}/decide", json={"approve": False}, headers=auth(nino)).json()["code"] == "comment_required"
    d = client.post(f"/api/change-requests/{cid}/decide", json={"approve": False, "comment": "адрес верный"}, headers=auth(nino)).json()
    assert d["status"] == "rejected" and d["decision_comment"] == "адрес верный"
    db.expire_all()
    assert db.get(Property, pid).address == "ул. Палиашвили 10"
    assert db.query(Event).filter_by(entity_id=str(pid), action="change_request_rejected").count() == 1


def test_archive_reason_validation_and_the_own_agent_archives_at_once(db, client, sent):
    georgiy, nino, pid = _verified(db, client, sent)
    other_pid = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6002").id, "force": True}, headers=auth(georgiy)).json()["id"]
    arch = lambda uid, b: client.post(f"/api/properties/{uid}/archive", json=b, headers=auth(nino)).json()  # noqa: E731
    assert arch(other_pid, {})["code"] == "bad_reason"
    assert arch(other_pid, {"reason": "other"})["code"] == "comment_required"
    assert arch(other_pid, {"reason": "duplicate"})["code"] == "bad_duplicate"
    assert arch(other_pid, {"reason": "duplicate", "duplicate_of": other_pid})["code"] == "bad_duplicate"
    r = arch(other_pid, {"reason": "duplicate", "duplicate_of": pid})
    assert (r["status"], r["archive_reason"], r["duplicate_of"]) == ("archived", "duplicate", pid) and r["archived_at"]

    client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    r = client.post(f"/api/properties/{pid}/archive", json={"reason": "not_actual"}, headers=auth(georgiy)).json()
    assert (r["status"], r["archive_reason"], r["change_request"]) == ("archived", "not_actual", None)
    levan = make_user(db, "agent", "Леван")  # someone else's object: as before
    assert client.post(f"/api/properties/{pid}/resume", headers=auth(levan)).status_code == 403


def test_existing_flows_set_reasons_and_transfer_request(db, client, sent):
    georgiy, nino, pid = _verified(db, client, sent)
    client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    levan = make_user(db, "agent", "Леван")
    cr = client.post(f"/api/properties/{pid}/transfer-request", json={"agent_id": levan.id, "reason": "ухожу"},
                     headers=auth(georgiy)).json()["change_request"]
    assert cr["changes"] == [{"field": "owner_agent", "old": "Георгий", "new": "Леван"}]
    client.post(f"/api/change-requests/{cr['id']}/decide", json={"approve": True}, headers=auth(nino))
    db.expire_all()
    assert db.get(Owner, db.get(Property, pid).owner_id).agent_id == levan.id and db.get(Property, pid).agent_id == levan.id

    properties.mark_rented(db, pid, nino)
    assert db.get(Property, pid).archive_reason == "rented_by_us"
    properties.unmark_rented(db, pid, nino)
    assert db.get(Property, pid).archive_reason is None

    a = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6003").id, "force": True}, headers=auth(georgiy)).json()["id"]
    b = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6004").id, "force": True}, headers=auth(georgiy)).json()["id"]
    assert client.post(f"/api/properties/{a}/refused", json={}, headers=auth(georgiy)).json()["archive_reason"] == "owner_refused"
    assert client.post(f"/api/properties/{b}/rented-self", json={"rent_from": "2026-11-01", "rent_to": "2027-11-01"}, headers=auth(georgiy)).json()["archive_reason"] == "rented_by_owner"
    # re-taking a refused object clears its archive reason
    assert client.post("/api/properties", json={"listing_id": "ss6003"}, headers=auth(levan)).json()["archive_reason"] is None


def test_staff_give_an_ownerless_object_an_agent(db, client, sent):
    """David 11-26 #15102: a channel import has no owner and no agent («Платформа») — the operator assigns one."""
    nino = make_user(db, "operator", "Нино")
    levan = make_user(db, "agent", "Леван")
    p = Property(address="1 Ateni St", status="published")
    db.add(p)
    db.commit()
    r = client.post(f"/api/properties/{p.id}/transfer-request", json={"agent_id": levan.id}, headers=auth(nino))
    assert r.status_code == 200 and r.json()["change_request"] is None
    db.expire_all()
    assert db.get(Property, p.id).agent_id == levan.id
    assert db.scalar(select(Event).where(Event.entity_id == str(p.id), Event.action == "agent_assigned")) is not None
    assert client.post(f"/api/properties/{p.id}/transfer-request", json={"agent_id": levan.id},
                       headers=auth(levan)).status_code in (400, 409)
