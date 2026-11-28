"""David 11-24: an agent's edit of a live post goes to the operator («Редактирование = новый пост»), approval applies
it and the post follows; «Поднять пост наверх» by hand — 1 a day per 5 published objects (at least 1), operators
without a limit."""
from app import worker
from app.models import ChangeRequest, Job, Property
from app.services import change_requests
from tests.conftest import auth
from tests.test_channel_follows import _live, _published, _repost_due_now


def test_agents_post_edit_goes_to_the_operator(db, client, monkeypatch):
    calls, georgiy, nino, pid = _published(db, client, monkeypatch)
    p = db.get(Property, pid)
    floor, area = p.floor, p.area
    pets = "NotAllowed" if (p.post_fields or {}).get("pets") != "NotAllowed" else "Allowed"
    r = client.patch(f"/api/properties/{pid}", json={"floor": (floor or 0) + 2, "area": float(area) if area is not None else None,
                                                     "post_fields": {**(p.post_fields or {}), "pets": pets}}, headers=auth(georgiy))
    assert r.status_code == 200 and r.json()["change_request"]
    db.expire_all()
    assert db.get(Property, pid).floor == floor  # not applied yet, no repost
    assert not db.query(Job).filter_by(kind="tg_repost", ref=str(pid), done_at=None).count()
    cr = db.query(ChangeRequest).filter_by(entity_id=pid).one()
    assert set(cr.payload) == {"floor", "pf:pets"}  # the unchanged area (45 = 45.0) is not a change
    change_requests.decide(db, cr.id, nino, True)
    db.expire_all()
    p = db.get(Property, pid)
    assert p.floor == (floor or 0) + 2 and p.post_fields["pets"] == pets
    _repost_due_now(db)
    worker.tick()
    assert len(_live(db, pid)) == 1 and _live(db, pid) == ["120"]


def test_repost_limit_one_per_five_objects(db, client, monkeypatch):
    calls, georgiy, nino, pid = _published(db, client, monkeypatch)
    p = db.get(Property, pid)
    for _ in range(14):  # 15 published objects → 3 a day
        db.add(Property(agent_id=georgiy.id, status="published", source_type=p.source_type))
    db.commit()
    pub = lambda u: client.post(f"/api/properties/{pid}/publish", json={"channel": "tg"}, headers=auth(u))  # noqa: E731
    assert client.get(f"/api/properties/{pid}", headers=auth(georgiy)).json()["reposts_left"] == 3
    for _ in range(3):
        assert pub(georgiy).status_code == 200
    r = pub(georgiy)
    assert r.status_code >= 400 and r.json()["code"] == "repost_limit"
    assert client.get(f"/api/properties/{pid}", headers=auth(georgiy)).json()["reposts_left"] == 0
    assert pub(nino).status_code == 200  # the operator: no limit
    assert client.get(f"/api/properties/{pid}", headers=auth(nino)).json()["reposts_left"] is None


def test_repost_limit_per_person(db, client, monkeypatch):
    """David's voice 287351: the limit is set per person (users.plans["reposts"])."""
    calls, georgiy, nino, pid = _published(db, client, monkeypatch)
    georgiy.plans = {**(georgiy.plans or {}), "reposts": 1}
    db.commit()
    r = client.get(f"/api/properties/{pid}", headers=auth(georgiy)).json()
    assert (r["reposts_left"], r["reposts_limit"]) == (1, 1)
    assert client.post(f"/api/properties/{pid}/publish", json={"channel": "tg"}, headers=auth(georgiy)).status_code == 200
    assert client.post(f"/api/properties/{pid}/publish", json={"channel": "tg"}, headers=auth(georgiy)).json()["code"] == "repost_limit"
