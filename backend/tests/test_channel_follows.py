"""David 11-23 (call, A1–A10): the channel follows every button of the object card — a repost never leaves two posts,
«% Скидка» / «Правка» repost by themselves, «Снять с публикации» and «Приостановить» take the post down and the way
back posts it again, a rented flat gets the channel's «#FlatRented» announcement once, and a rented object goes back
to the market from the card."""
from app import worker
from app.models import Job, Property, PropertyPublication
from app.services import properties
from tests.conftest import auth, make_user
from tests.test_tg_unpost import _actions, _posts
from tests.test_publish_card import _submitted


def _live(db, pid):
    db.expire_all()
    return [x.message_id for x in db.query(PropertyPublication).filter_by(property_id=pid, channel="tg", status="published")]


def _published(db, client, monkeypatch):
    calls = _posts(monkeypatch)
    georgiy, nino, pid, _ = _submitted(db, client)
    properties.publish(db, pid, nino)
    return calls, georgiy, nino, pid


def test_update_post_reposts_without_a_second_post(db, client, monkeypatch):
    calls, _, nino, pid = _published(db, client, monkeypatch)
    r = client.post(f"/api/properties/{pid}/publish", json={"channel": "tg", "tg": True, "site": True}, headers=auth(nino))
    assert r.status_code == 200 and _live(db, pid) == ["120"] and calls == [("@rent_tbilisi_ge", [109, 110, 111], False)]


def test_discount_and_edit_of_a_post_field_repost_by_themselves(db, client, monkeypatch):
    calls, _, nino, pid = _published(db, client, monkeypatch)
    p = db.get(Property, pid)
    r = client.patch(f"/api/properties/{pid}", json={"price": float(p.price) - 100, "post_fields": {**(p.post_fields or {}), "discount": True}},
                     headers=auth(nino))
    assert r.status_code == 200
    assert db.query(Job).filter_by(kind="tg_repost", ref=str(pid), done_at=None).count() == 1
    worker.tick()
    assert _live(db, pid) == ["120"] and "tg_reposted" in _actions(db, pid)
    # a field the post doesn't show: no repost
    client.patch(f"/api/properties/{pid}", json={"cadastral_code": "01.10.13.001.001"}, headers=auth(nino))
    assert db.query(Job).filter_by(kind="tg_repost", ref=str(pid), done_at=None).count() == 0


def test_discount_lowers_a_one_month_deposit_too(db, client, monkeypatch):
    """David 11-27: deposit = the old price follows the discount; another deposit stays."""
    _, _, nino, pid = _published(db, client, monkeypatch)
    p = db.get(Property, pid)
    old = int(p.price)
    p.post_fields = {**(p.post_fields or {}), "deposit": old}
    db.commit()
    client.patch(f"/api/properties/{pid}", json={"price": old - 100, "post_fields": {**p.post_fields, "discount": True}},
                 headers=auth(nino))
    db.expire_all()
    p = db.get(Property, pid)
    assert p.post_fields["deposit"] == old - 100 and f"Deposit {old - 100}$" in properties.post_text(db, p)
    client.patch(f"/api/properties/{pid}", json={"price": old - 200, "post_fields": {**p.post_fields, "deposit": 500}},
                 headers=auth(nino))
    db.expire_all()
    assert db.get(Property, pid).post_fields["deposit"] == 500

def test_two_edits_make_one_repost(db, client, monkeypatch):
    calls, _, nino, pid = _published(db, client, monkeypatch)
    client.patch(f"/api/properties/{pid}", json={"floor": 3}, headers=auth(nino))
    client.patch(f"/api/properties/{pid}", json={"floor": 4}, headers=auth(nino))
    worker.tick()
    assert _live(db, pid) == ["120"]


def test_unpublish_by_the_agent_and_publish_again(db, client, monkeypatch):
    calls, georgiy, nino, pid = _published(db, client, monkeypatch)
    r = client.post(f"/api/properties/{pid}/unpublish", headers=auth(georgiy))  # the object's agent may
    assert r.status_code == 200 and (r.json()["status"], r.json()["archive_reason"]) == ("archived", "unpublished")
    worker.tick()
    assert _live(db, pid) == [] and calls == [("@rent_tbilisi_ge", [109, 110, 111], False)]
    r = client.post(f"/api/properties/{pid}/resume", headers=auth(georgiy))  # «Опубликовать снова»
    assert r.json()["status"] == "published"
    worker.tick()
    assert _live(db, pid) == ["120"]


def test_pause_takes_the_post_down_and_resume_posts_again(db, client, monkeypatch):
    calls, _, nino, pid = _published(db, client, monkeypatch)
    client.post(f"/api/properties/{pid}/archive", json={"reason": "paused"}, headers=auth(nino))
    worker.tick()
    assert _live(db, pid) == [] and not [c for c in calls if c[0] == "rented"]
    client.post(f"/api/properties/{pid}/resume", headers=auth(nino))
    worker.tick()
    assert _live(db, pid) == ["120"]


def test_rented_posts_flat_rented_once_and_goes_back_from_the_card(db, client, monkeypatch):
    calls, georgiy, nino, pid = _published(db, client, monkeypatch)
    properties.mark_rented(db, pid, None)
    db.commit()
    worker.tick()
    rented = [c for c in calls if c[0] == "rented"]
    assert len(rented) == 1 and rented[0][2].startswith("❗️#FlatRented\nNice apartment in 📍") and "found a tenant" in rented[0][2]
    assert rented[0][2].endswith(properties.district_tag(db.get(Property, pid).district))
    assert _live(db, pid) == [] and "tg_rented_posted" in _actions(db, pid)
    properties.rented_post(db, db.get(Property, pid))  # the same rent: not again
    assert len([c for c in calls if c[0] == "rented"]) == 1
    # «Опубликовать снова» right from the rented card: someone else's agent can't, the object's own agent can (11-23)
    other = make_user(db, "agent", "Леван")
    assert client.post(f"/api/properties/{pid}/resume", json={"republish": True}, headers=auth(other)).status_code == 403
    assert client.post(f"/api/properties/{pid}/resume", json={"republish": True}, headers=auth(georgiy)).json()["status"] == "published"
    worker.tick()
    assert _live(db, pid) == ["120"]


def test_rented_back_to_work_by_the_agent(db, client, monkeypatch):
    calls, georgiy, nino, pid = _published(db, client, monkeypatch)
    properties.mark_rented(db, pid, None)
    db.commit()
    assert client.post(f"/api/properties/{pid}/resume", headers=auth(georgiy)).json()["status"] == "taken"


def test_rented_text_for_commercial():
    p = Property(district="Vake", post_fields={"prop_type": "Office"})
    assert properties.rented_text(p).startswith("❗️#Rented\nCommercial space in 📍")


def test_rented_text_for_a_sale():
    """e2e 11-23: a sold flat is not «#FlatRented … found a tenant»."""
    p = Property(district="Vake", post_fields={"deal": "Sale", "prop_type": "Apartment"})
    assert properties.rented_text(p).startswith("❗️#Sold\nNice apartment in 📍") and "tenant" not in properties.rented_text(p)


def test_any_archive_reason_takes_the_post_down(db, client, monkeypatch):
    """Stanislav 11-23: no CRM path leaves the post in the channel — «Неактуально», «Другое» too."""
    calls, _, nino, pid = _published(db, client, monkeypatch)
    client.post(f"/api/properties/{pid}/archive", json={"reason": "not_actual"}, headers=auth(nino))
    worker.tick()
    assert _live(db, pid) == [] and not [c for c in calls if c[0] == "rented"]
