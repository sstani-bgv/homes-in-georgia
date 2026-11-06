"""Stage 5: the agent submits once (media and checklist read-only after that), the operator publishes from the card's
post generator: the object's caption + hashtags, edited text, picked media in order, the TG channel."""
from app.config import settings
from app.models import Property, PropertyPublication
from app.telegram import notify
from tests.conftest import auth, make_listing, make_user

JPG = ("files", ("a.jpg", b"\xff\xd8jpeg", "image/jpeg"))


def _submitted(db, client):
    georgiy = make_user(db, "agent", "Георгий Беридзе")
    nino = make_user(db, "operator", "Нино")
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, "ss5001").id}, headers=auth(georgiy)).json()["id"]
    p = db.get(Property, pid)
    p.status, p.total_floors = "shoot_scheduled", 9
    db.commit()
    media = client.post(f"/api/properties/{pid}/media", files=[JPG, ("files", ("b.mp4", b"video", "video/mp4"))],
                        headers=auth(georgiy)).json()["media"]
    assert client.post(f"/api/properties/{pid}/submit", headers=auth(georgiy)).json()["status"] == "review"
    return georgiy, nino, pid, [m["id"] for m in media]


def test_agent_media_read_only_after_submit_until_returned(db, client, sent):
    georgiy, nino, pid, (photo, _) = _submitted(db, client)
    for r in (client.post(f"/api/properties/{pid}/media", files=[JPG], headers=auth(georgiy)),
              client.delete(f"/api/properties/{pid}/media/{photo}", headers=auth(georgiy)),
              client.post(f"/api/properties/{pid}/checklist", json={"checklist": {"kitchen": True}}, headers=auth(georgiy))):
        assert r.status_code == 409 and r.json()["code"] == "submitted"
    assert client.post(f"/api/properties/{pid}/media", files=[JPG], headers=auth(nino)).status_code == 200  # staff may
    client.post(f"/api/properties/{pid}/review", json={"approve": False, "comment": "нет ванной"}, headers=auth(nino))
    assert client.post(f"/api/properties/{pid}/media", files=[JPG], headers=auth(georgiy)).json()["status"] == "media_uploaded"


def test_post_text_endpoint(db, client, sent):
    georgiy, nino, pid, _ = _submitted(db, client)
    assert client.get(f"/api/properties/{pid}/post", headers=auth(georgiy)).status_code == 403
    r = client.get(f"/api/properties/{pid}/post", headers=auth(nino)).json()
    assert r["text"].startswith("#Vake\n📍ул. Палиашвили 10") and "70 sq.m | 4/9 Floor" in r["text"]
    assert "💰 850$ + Deposit 850$" in r["text"] and "#Георгий" in r["text"]
    assert r["hashtags"] == ["#Vake", "#2Bed", "#Apartment", "#Rent", "#Price700to900", "#Георгий"]
    assert r["channels"] == ["tg"]


def test_publish_from_card_with_edited_text_and_picked_media(db, client, sent):
    georgiy, nino, pid, (photo, video) = _submitted(db, client)
    body = {"text": "Свой текст #Vake", "media_ids": [video, photo], "channel": "tg"}
    assert client.post(f"/api/properties/{pid}/publish", json=body, headers=auth(georgiy)).status_code == 403
    assert client.post(f"/api/properties/{pid}/publish", json={**body, "channel": "ig"}, headers=auth(nino)).status_code == 400
    assert client.post(f"/api/properties/{pid}/publish", json={**body, "media_ids": [999]}, headers=auth(nino)).status_code == 404
    r = client.post(f"/api/properties/{pid}/publish", json=body, headers=auth(nino)).json()
    assert r["status"] == "published" and r["reviewed_by"] == nino.id
    (prop,), = sent.of("publish_post")
    assert prop.post_text == "Свой текст #Vake" and prop.post_media_ids == [video, photo]
    assert [(x["channel"], x["url"]) for x in r["publications"]] == [("tg", "https://t.me/rent_tbilisi_ge/777")]
    hist = [e["action"] for e in client.get(f"/api/properties/{pid}/history", headers=auth(nino)).json()]
    assert hist[-2:] == ["approved", "published"] and sent.of("property_published")


def test_publish_failure_keeps_review_and_dev_without_bot_is_a_dry_run(db, client, sent, monkeypatch):
    _, nino, pid, _ = _submitted(db, client)
    monkeypatch.setattr(notify, "publish_post", lambda p: None)  # Telegram refused
    r = client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    assert r.status_code == 502 and r.json()["code"] == "publish_failed"
    db.expire_all()
    assert db.get(Property, pid).status == "review" and not db.query(PropertyPublication).count()
    monkeypatch.setattr(settings, "BOT_TOKEN", "")
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    r = client.post(f"/api/properties/{pid}/publish", headers=auth(nino)).json()
    assert r["status"] == "published" and [(x["status"], x["url"]) for x in r["publications"]] == [("published", None)]
