"""Сверка с каналом (services/post_sync.py): an object whose channel post is gone leaves the site; a live post, an odd
answer and a mass «gone» (t.me down) change nothing. No network: a fake httpx transport."""
import httpx

from app.models import Event, Job, Property
from app.services import post_sync
from tests.conftest import make_user
from tests.test_similar import _object

LIVE = '<div class="tgme_widget_message"><div class="tgme_widget_message_text">x</div></div>'
GONE = '<div class="tgme_widget_message_error">Post not found</div>'


def _fake(monkeypatch, pages):
    def handler(req):
        msg = int(req.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, text=pages.get(msg, "")) if msg in pages else httpx.Response(502)
    real = httpx.Client
    monkeypatch.setattr(post_sync.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _pubs(db, client, msgs):
    """Published objects, one per channel post id: copies of tests.test_similar._object's object."""
    first = db.get(Property, _object(db, client, make_user(db, "agent", "Вика")))
    cols = [c.key for c in Property.__table__.columns if c.key not in ("id", "listing_id")]
    out = []
    for n, msg in enumerate(msgs):
        p = first if n == 0 else Property(**{c: getattr(first, c) for c in cols})
        db.add(p)
        p.status, p.site_hidden, p.post_url = "published", False, f"https://t.me/rent_tbilisi_ge/{msg}"
        db.flush()
        out.append(p.id)
    db.commit()
    return out


def test_a_deleted_post_counts_the_object_as_rented(db, client, monkeypatch):
    """Stanislav 11-23: a post deleted in the channel by hand = rented («Сдал сам»): off the site and the bot, no #FlatRented."""
    live, dead, odd = _pubs(db, client, (101, 102, 103))
    _fake(monkeypatch, {101: LIVE, 102: GONE})  # 103: a 502
    assert post_sync.run(db, pause=0) == 1
    db.expire_all()
    assert [db.get(Property, x).status for x in (live, dead, odd)] == ["published", "archived", "published"]
    p = db.get(Property, dead)
    assert (p.archive_reason, p.archive_comment) == ("rented_by_owner", "пост удалён в канале")
    ev = db.query(Event).filter_by(entity="property", entity_id=str(dead), action="archived").one()
    assert ev.actor_label == "сверка с каналом"
    assert not db.query(Job).filter_by(kind="tg_unpost", ref=str(dead)).count()  # the post is gone already: no #FlatRented


def test_mass_gone_hides_nothing(db, client, monkeypatch):
    pids = _pubs(db, client, [200 + n for n in range(12)])
    _fake(monkeypatch, {200 + n: GONE for n in range(12)})
    assert post_sync.run(db, pause=0) == 0
    db.expire_all()
    assert all(db.get(Property, x).status == "published" for x in pids)


def test_both_channels_are_checked(db, client, monkeypatch):
    """David 11-23: sale / commercial / cars are posted to @homesingeorgia — its deleted posts count as well."""
    rent, homes = _pubs(db, client, (301, 302))
    db.get(Property, homes).post_url = "https://t.me/homesingeorgia/302"
    db.commit()
    asked = []
    monkeypatch.setattr(post_sync, "alive", lambda channel, msgs: asked.append(channel) or None)
    _fake(monkeypatch, {301: LIVE, 302: GONE})
    assert post_sync.run(db, pause=0) == 1
    assert sorted(asked) == ["homesingeorgia", "rent_tbilisi_ge"]
    db.expire_all()
    assert [db.get(Property, x).status for x in (rent, homes)] == ["published", "archived"]


def test_a_repost_during_the_check_keeps_the_object(db, client, monkeypatch):
    """Irakli 11-25: the channel import (its own thread) moved the object to a newer post while the check looked at the
    old one, deleted by the repost — the object, live under its new post, stays published."""
    pid, = _pubs(db, client, (401,))

    def alive(channel, msgs):  # the import relinks meanwhile, in another session
        from app.db import SessionLocal
        with SessionLocal() as other:
            other.get(Property, pid).post_url = "https://t.me/rent_tbilisi_ge/402"
            other.commit()
        return set()
    monkeypatch.setattr(post_sync, "alive", alive)
    post_sync.run(db, pause=0)
    db.expire_all()
    p = db.get(Property, pid)
    assert (p.status, p.post_url) == ("published", "https://t.me/rent_tbilisi_ge/402")
