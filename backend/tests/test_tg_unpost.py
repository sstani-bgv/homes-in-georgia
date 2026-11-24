"""Stanislav 11-16: a repost deletes the object's old Telegram post; «Сдано» deletes all its posts (worker job tg_unpost)."""
from app import worker
from app.models import Event, Job, Property, PropertyPublication
from app.services import inbound, properties
from app.telegram import notify
from tests.conftest import make_user
from tests.test_publish_card import _submitted


def _posts(monkeypatch, refused=()):
    """publish_post -> a new post each time (3 messages); delete_post records calls, refuses chats in `refused`."""
    n, deleted = [100], []

    def publish_post(prop):
        n[0] += 10
        return n[0], f"https://t.me/rent_tbilisi_ge/{n[0]}", "@rent_tbilisi_ge", [n[0] - 1, n[0], n[0] + 1]

    def delete_post(chat, ids, album=False):
        deleted.append((chat, list(ids), album))
        return chat not in refused
    monkeypatch.setattr(notify, "publish_post", publish_post)
    monkeypatch.setattr(notify, "delete_post", delete_post)
    monkeypatch.setattr(notify, "rented_post", lambda pid, text, chat=None: deleted.append(("rented", pid, text)) or (900, "https://t.me/rent_tbilisi_ge/900"))
    return deleted


def _actions(db, pid):
    return [e.action for e in db.query(Event).filter(Event.entity == "property", Event.entity_id == str(pid)).order_by(Event.id)]


def test_repost_deletes_old_post_and_keeps_new(db, client, monkeypatch):
    deleted = _posts(monkeypatch)
    _, nino, pid, _ = _submitted(db, client)
    properties.publish(db, pid, nino)
    assert deleted == []  # first publication: nothing to delete
    properties.publish(db, pid, nino)
    assert deleted == [("@rent_tbilisi_ge", [109, 110, 111], False)]
    pubs = {x.message_id: x for x in db.query(PropertyPublication).filter_by(property_id=pid)}
    assert pubs["110"].status == "removed" and pubs["120"].status == "published"
    assert pubs["120"].messages == {"chat": "@rent_tbilisi_ge", "ids": [119, 120, 121]}
    assert db.get(Property, pid).post_url == "https://t.me/rent_tbilisi_ge/120"
    assert "tg_reposted" in _actions(db, pid)


def test_rented_deletes_every_post_in_worker_and_failures_show(db, client, monkeypatch):
    deleted = _posts(monkeypatch, refused=("-100777",))
    _, nino, pid, _ = _submitted(db, client)
    properties.publish(db, pid, nino)
    db.add(PropertyPublication(property_id=pid, channel="tg", url="https://t.me/c/777/5", status="published"))  # legacy, no ids
    properties.mark_rented(db, pid, None)
    db.commit()
    assert deleted == []  # the status change does not wait on Telegram
    assert db.query(Job).filter_by(kind="tg_unpost", ref=str(pid)).count() == 1
    worker.tick()
    assert sorted(x for x in deleted if x[0] != "rented") == [("-100777", [5], True), ("@rent_tbilisi_ge", [109, 110, 111], False)]
    db.expire_all()
    pubs = {x.url: x for x in db.query(PropertyPublication).filter_by(property_id=pid)}
    assert pubs["https://t.me/rent_tbilisi_ge/110"].status == "removed"
    assert pubs["https://t.me/c/777/5"].status == "published" and pubs["https://t.me/c/777/5"].delete_error
    p = db.get(Property, pid)
    assert p.status == "rented" and p.post_url is None
    ev = db.query(Event).filter_by(entity="property", entity_id=str(pid), action="tg_removed").one()
    assert ev.data["deleted"] == 1 and ev.data["failed"] == ["https://t.me/c/777/5"]


def test_rent_cancelled_before_worker_keeps_posts(db, client, monkeypatch):
    deleted = _posts(monkeypatch)
    _, nino, pid, _ = _submitted(db, client)
    properties.publish(db, pid, nino)
    properties.mark_rented(db, pid, None)
    properties.unmark_rented(db, pid, None)
    db.commit()
    worker.tick()
    assert deleted == []


def test_archive_rented_by_owner_unposts(db, client, monkeypatch):
    deleted = _posts(monkeypatch)
    _, nino, pid, _ = _submitted(db, client)
    properties.publish(db, pid, nino)
    properties.archive(db, pid, nino, "rented_by_owner", rent_from="2026-11-01", rent_to="2027-11-01")
    worker.tick()
    assert deleted and deleted[0][1] == [109, 110, 111]


def test_channel_rented_tag_rents_our_object(db, client, monkeypatch):
    deleted = _posts(monkeypatch)
    _, nino, pid, _ = _submitted(db, client)
    properties.publish(db, pid, nino)
    make_user(db, "agent", "Тамара")
    from app.models import ChannelPost
    db.add(ChannelPost(message_id="110", link="https://t.me/rent_tbilisi_ge/110", property_id=pid, status="active"))
    db.commit()
    inbound.mark_rented_refs(db, "#Rented https://t.me/rent_tbilisi_ge/110")
    db.commit()
    assert db.get(Property, pid).status == "rented"
    worker.tick()
    assert deleted and deleted[0][1] == [109, 110, 111]
