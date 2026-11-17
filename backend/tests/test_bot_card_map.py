"""Stanislav 11-17: «📍 На карте» under a bot card opens our site's map — on the object when the site shows it."""
from app.models import Property
from app.services import properties, site_feed
from tests.test_publish_card import _submitted
from tests.test_tg_unpost import _posts


def test_site_pid_only_for_objects_the_site_shows(db, client, monkeypatch):
    _posts(monkeypatch)
    georgiy, nino, pid, _ = _submitted(db, client)
    p = db.get(Property, pid)
    assert site_feed.site_pid(db, f"obj{pid}") is None  # in review: not on the site yet
    properties.publish(db, pid, nino)
    assert site_feed.site_pid(db, f"obj{pid}") == pid
    assert site_feed.site_pid(db, p.listing_id) == pid  # the outside ad it came from
    p.site_hidden = True  # «Снять с сайта»
    db.commit()
    assert site_feed.site_pid(db, f"obj{pid}") is None
