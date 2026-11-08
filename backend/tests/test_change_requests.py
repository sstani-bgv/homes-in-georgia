"""Stage 6: key fields lock after the operator's verification, «Запрос оператору» (create / approve / reject), archive
with a mandatory reason, the backfill of reasons for objects archived before stage 6."""
import importlib.util
from pathlib import Path

from sqlalchemy import text

from app.models import ChangeRequest, Event, Owner, Property
from app.services import properties
from tests.conftest import auth, fill_required, make_listing, make_user


def _verified(db, client, sent, lid="ss6001"):
    """An object of agent Георгий with an owner, submitted and published by operator Нино (= verified)."""
    georgiy, nino = make_user(db, "agent", "Георгий"), make_user(db, "operator", "Нино")
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, lid).id}, headers=auth(georgiy)).json()["id"]
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
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6000").id}, headers=auth(georgiy)).json()["id"]
    r = client.patch(f"/api/properties/{pid}", json={"price": 900, "owner_phone": "599000111", "owner_name": "Нана",
                                                    "address": "Ваке, 5"}, headers=auth(georgiy)).json()
    assert r["change_request"] is None and r["locked_fields"] == [] and r["price"] == 900 and r["owner"]["phone"] == "599000111"
    assert r["address"] == "Ваке, 5" and r["verified_at"] is None


def test_after_verification_locked_fields_become_a_request_others_apply(db, client, sent):
    georgiy, nino, pid = _verified(db, client, sent)
    pub = client.post(f"/api/properties/{pid}/publish", headers=auth(nino)).json()
    assert pub["verified_at"] and "owner_phone" in client.get(f"/api/properties/{pid}", headers=auth(georgiy)).json()["locked_fields"]
    assert client.get(f"/api/properties/{pid}", headers=auth(nino)).json()["locked_fields"] == []  # staff edit directly

    body = {"owner_phone": "+995 577 00 00 00", "price": 850, "description": "Свежий ремонт",
            "post_fields": {"amenities": ["WiFi"], "deposit": 500}, "reason": "собственник сменил номер"}
    r = client.patch(f"/api/properties/{pid}", json=body, headers=auth(georgiy)).json()
    # price unchanged (850) → not in the request; description / amenities applied; phone and deposit wait for the operator
    assert r["description"] == "Свежий ремонт" and r["post_fields"] == {"amenities": ["WiFi"]}
    assert r["owner"]["phone"] == "+995 599 11 22 33"
    cr = r["change_request"]
    assert cr["kind"] == "edit" and cr["status"] == "pending" and cr["reason"] == "собственник сменил номер"
    assert {c["field"]: (c["old"], c["new"]) for c in cr["changes"]} == {
        "owner_phone": ("+995 599 11 22 33", "+995 577 00 00 00"), "deposit": (None, 500)}
    assert [x["id"] for x in client.get(f"/api/properties/{pid}", headers=auth(georgiy)).json()["change_requests"]] == [cr["id"]]
    assert sent.of("change_request_new")

    # the queue «Запросы» is staff only: agents can't load it (their own requests are on the object card) nor decide
    assert [x["id"] for x in client.get("/api/change-requests", headers=auth(nino)).json()["items"]] == [cr["id"]]
    assert client.get("/api/change-requests", headers=auth(georgiy)).status_code == 403
    assert client.post(f"/api/change-requests/{cr['id']}/decide", json={"approve": True}, headers=auth(georgiy)).status_code == 403

    d = client.post(f"/api/change-requests/{cr['id']}/decide", json={"approve": True}, headers=auth(nino)).json()
    assert d["status"] == "approved" and d["decided_by"]["id"] == nino.id
    db.expire_all()
    p = db.get(Property, pid)
    assert db.get(Owner, p.owner_id).phone == "+995 577 00 00 00" and p.post_fields == {"amenities": ["WiFi"], "deposit": 500}
    acts = [e.action for e in db.query(Event).filter_by(entity="property", entity_id=str(pid)).order_by(Event.id)]
    assert acts[-3:] == ["change_requested", "updated", "change_request_approved"]
    assert sent.of("change_request_decided")
    assert client.post(f"/api/change-requests/{cr['id']}/decide", json={"approve": True}, headers=auth(nino)).json()["code"] == "bad_status"


def test_reject_needs_comment_and_changes_nothing(db, client, sent):
    georgiy, nino, pid = _verified(db, client, sent)
    client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    cr = client.patch(f"/api/properties/{pid}", json={"address": "Другой адрес"}, headers=auth(georgiy)).json()["change_request"]
    assert client.post(f"/api/change-requests/{cr['id']}/decide", json={"approve": False}, headers=auth(nino)).json()["code"] == "comment_required"
    d = client.post(f"/api/change-requests/{cr['id']}/decide", json={"approve": False, "comment": "адрес верный"}, headers=auth(nino)).json()
    assert d["status"] == "rejected" and d["decision_comment"] == "адрес верный"
    db.expire_all()
    assert db.get(Property, pid).address == "ул. Палиашвили 10"
    assert db.query(Event).filter_by(entity_id=str(pid), action="change_request_rejected").count() == 1


def test_archive_reason_validation_and_agent_request_after_verification(db, client, sent):
    georgiy, nino, pid = _verified(db, client, sent)
    other_pid = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6002").id}, headers=auth(georgiy)).json()["id"]
    arch = lambda uid, b: client.post(f"/api/properties/{uid}/archive", json=b, headers=auth(nino)).json()  # noqa: E731
    assert arch(other_pid, {})["code"] == "bad_reason"
    assert arch(other_pid, {"reason": "other"})["code"] == "comment_required"
    assert arch(other_pid, {"reason": "duplicate"})["code"] == "bad_duplicate"
    assert arch(other_pid, {"reason": "duplicate", "duplicate_of": other_pid})["code"] == "bad_duplicate"
    r = arch(other_pid, {"reason": "duplicate", "duplicate_of": pid})
    assert (r["status"], r["archive_reason"], r["duplicate_of"]) == ("archived", "duplicate", pid) and r["archived_at"]

    client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    r = client.post(f"/api/properties/{pid}/archive", json={"reason": "not_actual"}, headers=auth(georgiy)).json()
    assert r["status"] == "published" and r["change_request"]["kind"] == "archive"
    client.post(f"/api/change-requests/{r['change_request']['id']}/decide", json={"approve": True}, headers=auth(nino))
    db.expire_all()
    p = db.get(Property, pid)
    assert (p.status, p.archive_reason) == ("archived", "not_actual")


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

    a = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6003").id}, headers=auth(georgiy)).json()["id"]
    b = client.post("/api/properties", json={"listing_id": make_listing(db, "ss6004").id}, headers=auth(georgiy)).json()["id"]
    assert client.post(f"/api/properties/{a}/refused", json={}, headers=auth(georgiy)).json()["archive_reason"] == "owner_refused"
    assert client.post(f"/api/properties/{b}/rented-self", json={}, headers=auth(georgiy)).json()["archive_reason"] == "rented_by_owner"
    # re-taking a refused object clears its archive reason
    assert client.post("/api/properties", json={"listing_id": "ss6003"}, headers=auth(levan)).json()["archive_reason"] is None


def test_backfill_reasons_from_events(db):
    spec = importlib.util.spec_from_file_location(
        "m0010", Path(__file__).parents[1] / "alembic/versions/0010_change_requests_archive_reasons.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    ag = make_user(db, "agent", "Георгий")
    refused, rented_self, plain, rented, pub = (Property(status=s, agent_id=ag.id) for s in
                                                ("archived", "archived", "archived", "rented", "published"))
    db.add_all([refused, rented_self, plain, rented, pub])
    db.flush()
    pub.published_at = refused.updated_at
    for p, action in ((refused, "owner_refused"), (rented_self, "owner_rented_self")):
        db.add(Event(entity="property", entity_id=str(p.id), action=action, data={"status": "archived", "comment": "нет"}))
    db.add(Event(entity="property", entity_id=str(plain.id), action="archived", data={"status": "archived"}))
    db.commit()
    for sql in mig.BACKFILL:
        db.execute(text(sql))
    db.commit()
    db.expire_all()
    assert [(p.archive_reason, p.archive_comment) for p in (refused, rented_self, plain, rented)] == [
        ("owner_refused", "нет"), ("rented_by_owner", "нет"), ("other", None), ("rented_by_us", None)]
    assert refused.archived_at and pub.verified_at and rented.verified_at and refused.verified_at is None
    assert not db.query(ChangeRequest).count()
