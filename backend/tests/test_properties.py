from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app import worker
from app.models import Job, Owner, Property, Report
from app.services import jobs, properties
from app.services.dedup import address_match, norm_phone
from tests.conftest import auth, make_listing, make_user


def test_property_funnel_end_to_end(db, client, sent):
    georgiy = make_user(db, "agent", "Георгий")
    levan = make_user(db, "agent", "Леван")
    nino = make_user(db, "operator", "Нино")
    listing = make_listing(db, "ss2001", agent_reasons='["похоже на агента"]')
    make_listing(db, "mh3001")

    r = client.get("/api/listings/new", headers=auth(georgiy)).json()
    assert r["total"] == 2
    flags = {i["id"]: i["ai_flags"] for i in r["items"]}
    assert flags["ss2001"]["agent_suspect"] and flags["mh3001"]["owner"]
    assert client.get("/api/listings/new?flag=owner", headers=auth(georgiy)).json()["total"] == 1

    # C1: take → locked for others
    r = client.post("/api/properties", json={"listing_id": listing.id}, headers=auth(georgiy))
    assert r.status_code == 200 and r.json()["status"] == "taken" and r.json()["source_type"] == "ss"
    pid = r.json()["id"]
    assert r.json()["listing"]["phones"]  # agent-only listing fields for the taker
    r = client.post("/api/properties", json={"listing_id": listing.id}, headers=auth(levan))
    assert r.status_code == 409 and r.json()["code"] == "taken_by_other"
    assert client.post(f"/api/properties/{pid}/call", json={"reached": True}, headers=auth(levan)).status_code == 403
    assert client.get("/api/listings/new", headers=auth(georgiy)).json()["total"] == 1

    # C2a: no answer → call_failed + retry job in 2 h → worker reminds
    r = client.post(f"/api/properties/{pid}/call", json={"reached": False}, headers=auth(georgiy)).json()
    assert r["status"] == "call_failed" and r["call_attempts"] == 1
    job = jobs.pending(db, "call_retry", pid)[0]
    assert timedelta(hours=1.9) < job.due_at - datetime.now(timezone.utc) < timedelta(hours=2.1)
    db.execute(update(Job).where(Job.id == job.id).values(due_at=datetime.now(timezone.utc)))
    db.commit()
    worker.tick()
    assert sent.of("property_call_retry")
    r = client.post(f"/api/properties/{pid}/call", json={"reached": True}, headers=auth(georgiy)).json()
    assert r["status"] == "taken" and r["call_attempts"] == 2

    # C3: owner → owner base, dedup 'added'; owner phone visible to taker + operator only
    r = client.post(f"/api/properties/{pid}/owner", json={"is_owner": True, "owner_name": "Гиорги",
                                                           "owner_phone": "+995 555 12-34-56"}, headers=auth(georgiy)).json()
    assert r["status"] == "owner_confirmed" and r["dup_check"] == "added" and r["owner"]["phone"]
    assert client.get(f"/api/properties/{pid}", headers=auth(levan)).json()["owner"]["phone"] is None
    assert client.get(f"/api/properties/{pid}", headers=auth(nino)).json()["owner"]["phone"]
    assert db.query(Owner).one().phone_norm == "555123456"
    shoot = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = client.post(f"/api/properties/{pid}/shoot", json={"shoot_at": shoot}, headers=auth(georgiy)).json()
    assert r["status"] == "shoot_scheduled" and r["dup_check"] == "added"
    mk = db.query(Report).filter_by(kind="makefoto").one()
    assert "✅ <b>Добавлено в базу</b>" in mk.data["posts"][0]["text"] and sent.of("report_posted")

    # C4: media + checklist → review
    assert client.post(f"/api/properties/{pid}/submit", headers=auth(georgiy)).json()["code"] == "no_media"
    files = [("files", ("a.jpg", b"\xff\xd8jpeg", "image/jpeg")), ("files", ("b.mp4", b"video", "video/mp4"))]
    r = client.post(f"/api/properties/{pid}/media", files=files, data={"room": "kitchen"}, headers=auth(georgiy)).json()
    assert r["status"] == "media_uploaded" and [m["kind"] for m in r["media"]] == ["photo", "video"]
    media_url = r["media"][0]["url"]
    assert client.get(media_url, headers=auth(georgiy)).content == b"\xff\xd8jpeg"
    client.post(f"/api/properties/{pid}/checklist", json={"checklist": {"kitchen": True}}, headers=auth(georgiy))
    r = client.post(f"/api/properties/{pid}/submit", headers=auth(georgiy)).json()
    assert r["status"] == "review" and sent.of("property_review_request")

    # B7a: return with comment, then approve → published with post url
    assert client.post(f"/api/properties/{pid}/review", json={"approve": True}, headers=auth(georgiy)).status_code == 403
    r = client.post(f"/api/properties/{pid}/review", json={"approve": False, "comment": "нет фото ванной"},
                    headers=auth(nino)).json()
    assert r["status"] == "returned" and r["review_comment"] == "нет фото ванной" and sent.of("property_returned")
    client.post(f"/api/properties/{pid}/media", files=[("files", ("c.jpg", b"x", "image/jpeg"))], data={"room": "bathroom"},
                headers=auth(georgiy))
    client.post(f"/api/properties/{pid}/submit", headers=auth(georgiy))
    r = client.post(f"/api/properties/{pid}/review", json={"approve": True}, headers=auth(nino)).json()
    assert r["status"] == "published" and r["post_url"] == "https://t.me/rent_tbilisi_ge/777"
    assert sent.of("publish_post") and sent.of("property_published")
    hist = [h["action"] for h in client.get(f"/api/properties/{pid}/history", headers=auth(nino)).json()]
    assert hist[0] == "taken" and hist[-1] == "published" and "returned" in hist

    board = client.get("/api/properties/board", headers=auth(georgiy)).json()
    assert [p["id"] for p in board["published"]] == [pid]


def test_agent_marked_and_duplicate_exists(db, sent):
    ag1, ag2 = make_user(db, "agent"), make_user(db, "agent")
    p1 = properties.take(db, make_listing(db, "ss1").id, ag1)
    properties.call_result(db, p1.id, ag1, True)
    properties.owner_or_agent(db, p1.id, ag1, True, "Нана", "599 11 22 33")

    # same owner phone, written differently, for another object → 'exists'
    p2 = properties.take(db, make_listing(db, "ss2", address="Другая 5").id, ag2)
    p2 = properties.owner_or_agent(db, p2.id, ag2, True, "Нана", "+995 0599-11-22-33")
    assert p2.dup_check == "exists" and p2.duplicate_of == p1.id
    _, dup = properties.schedule_shoot(db, p2.id, ag2, datetime.now(timezone.utc) + timedelta(days=1))
    assert dup == "exists"
    rep = db.query(Report).filter_by(kind="makefoto").one()
    assert "Уже есть в базе" in rep.data["posts"][0]["text"]

    # C2b: an agent answered → agent_marked, phone flagged as agent
    p3 = properties.take(db, make_listing(db, "ss3").id, ag1)
    p3 = properties.owner_or_agent(db, p3.id, ag1, False, "Агент Вася", "577 000 111")
    assert p3.status == "agent_marked" and p3.ai_flags["agent_suspect"]
    assert db.query(Owner).filter_by(phone_norm=norm_phone("577 000 111")).one().is_agent

    res = properties.check_duplicate(db, phone="599112233", address="ул. Палиашвили 10")
    assert res["level"] == "danger"
    assert properties.check_duplicate(db, phone="111")["level"] == "none"


def test_manual_property_and_check_duplicate_api(db, client):
    ag = make_user(db, "agent")
    r = client.post("/api/properties", json={"address": "Pekini Ave 12", "district": "Сабуртало", "price": "700",
                                              "cadastral_code": "01.10.15.001.002"}, headers=auth(ag)).json()
    assert r["status"] == "taken" and r["source_type"] == "direct" and r["price"] == 700
    r = client.post("/api/properties/check-duplicate", json={"cadastral": "01.10.15.001.002"}, headers=auth(ag)).json()
    assert r["level"] == "danger" and r["results"][0]["property_id"]
    assert address_match("12 Pekini Avenue, Tbilisi", "Pekini avenue 12") and not address_match("Pekini 1", "Vazha 1")


def test_archive_cancels_retry(db, sent):
    ag = make_user(db, "agent")
    p = properties.take(db, make_listing(db, "ss9").id, ag)
    properties.call_result(db, p.id, ag, False)
    properties.archive(db, p.id, ag)
    assert not jobs.pending(db, "call_retry", p.id)
    assert db.get(Property, p.id).status == "archived"
