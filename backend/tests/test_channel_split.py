"""Stanislav 11-23: «Снять только из канала» deletes the posts — the status and the site stay; «Снять только с сайта» —
the post and the status stay. Both come back on their own: «Выложить в канал» (publish tg) / «Вернуть на сайт»."""
from app import worker
from app.models import Job, Property
from tests.conftest import auth
from tests.test_channel_follows import _live, _published


def test_channel_only_off_and_back(db, client, monkeypatch):
    calls, georgiy, nino, pid = _published(db, client, monkeypatch)
    assert client.post(f"/api/properties/{pid}/unpost", headers=auth(georgiy)).status_code == 403  # operators only
    r = client.post(f"/api/properties/{pid}/unpost", headers=auth(nino))
    assert r.status_code == 200 and (r.json()["status"], r.json()["site_hidden"], r.json()["post_url"]) == ("published", False, None)
    assert _live(db, pid) == [] and calls == [("@rent_tbilisi_ge", [109, 110, 111], False)]
    p = db.get(Property, pid)
    client.patch(f"/api/properties/{pid}", json={"floor": (p.floor or 0) + 1}, headers=auth(nino))  # no post: no repost
    assert not db.query(Job).filter_by(kind="tg_repost", ref=str(pid), done_at=None).count()
    r = client.post(f"/api/properties/{pid}/publish", json={"channel": "tg", "tg": True, "site": True}, headers=auth(nino))
    assert r.status_code == 200 and len(_live(db, pid)) == 1


def test_site_only_off_keeps_the_post(db, client, monkeypatch):
    calls, _, nino, pid = _published(db, client, monkeypatch)
    r = client.post(f"/api/properties/{pid}/site", json={"on": False}, headers=auth(nino))
    assert r.status_code == 200 and (r.json()["status"], r.json()["site_hidden"]) == ("published", True)
    worker.tick()
    assert len(_live(db, pid)) == 1 and not calls
    assert client.post(f"/api/properties/{pid}/site", json={"on": True}, headers=auth(nino)).json()["site_hidden"] is False
