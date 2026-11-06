"""Stage 2: the object is the main entity: listings + publications per object (migration 0007 backfill), the publish
paths write property_publications, and the full card payload respects the owner-contact rule."""
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.db import engine
from app.models import ChannelPost, Deal, Property, PropertyListing, PropertyPublication
from tests.conftest import BACKEND, TEST_URL, auth, make_listing, make_user


def _alembic(fn, rev):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL.replace("%", "%%"))
    fn(cfg, rev)


def test_migration_backfills_links_publications_and_channel_posts(db):
    make_listing(db, "ss7001")
    db.close()
    _alembic(command.downgrade, "0006")
    try:
        with engine.begin() as c:
            c.execute(text("""INSERT INTO properties (id, listing_id, source_type, source_url, status, post_url,
                                  post_message_id, published_at)
                              VALUES (1, 'ss7001', 'ss', 'https://ss.ge/ss7001', 'published', 'https://t.me/c/555/45', '45', now()),
                                     (2, NULL, 'direct', NULL, 'taken', NULL, NULL, NULL)"""))
            c.execute(text("""INSERT INTO channel_posts (message_id, link, status) VALUES
                              ('wg:45', 'https://t.me/c/555/45', 'active'), ('46', 'https://t.me/rent_tbilisi_ge/46', 'active')"""))
    finally:
        _alembic(command.upgrade, "head")
    links = db.scalars(select(PropertyListing)).all()
    assert [(x.property_id, x.listing_id, x.source, x.url) for x in links] == [(1, "ss7001", "ss", "https://ss.ge/ss7001")]
    assert links[0].first_seen_at is not None
    pubs = db.scalars(select(PropertyPublication)).all()
    assert [(x.property_id, x.channel, x.status, x.message_id) for x in pubs] == [(1, "tg", "published", "45")]
    posts = {x.message_id: x.property_id for x in db.scalars(select(ChannelPost))}
    assert posts == {"wg:45": 1, "46": None}  # unmatched channel posts stay as they are


def test_take_links_listing_and_publish_writes_publication(db, client):
    georgiy = make_user(db, "agent", "Георгий")
    nino = make_user(db, "operator", "Нино")
    listing = make_listing(db, "ss7002")
    pid = client.post("/api/properties", json={"listing_id": listing.id}, headers=auth(georgiy)).json()["id"]
    assert db.scalar(select(PropertyListing.property_id).where(PropertyListing.listing_id == "ss7002")) == pid

    db.execute(text("UPDATE properties SET status = 'review' WHERE id = :i"), {"i": pid})
    db.commit()
    r = client.post(f"/api/properties/{pid}/publish", headers=auth(nino)).json()
    assert r["publications"] == [{**r["publications"][0], "channel": "tg", "status": "published",
                                  "url": "https://t.me/rent_tbilisi_ge/777", "message_id": "777"}]
    # the post generator reports a post: same message id updates the row, a new post adds one
    body = {"post_url": "https://t.me/rent_tbilisi_ge/777", "message_id": "777"}
    client.post(f"/api/properties/{pid}/published", json=body, headers=auth(nino))
    db.add(ChannelPost(message_id="wg:45", link="https://t.me/c/123/45"))
    db.commit()
    r = client.post(f"/api/properties/{pid}/published", json={"post_url": "https://t.me/c/123/45", "message_id": "45"},
                    headers=auth(nino)).json()
    assert sorted(x["message_id"] for x in r["publications"]) == ["45", "777"]
    db.expire_all()
    assert db.scalar(select(ChannelPost.property_id).where(ChannelPost.message_id == "wg:45")) == pid


def test_card_payload_and_contact_rules(db, client):
    georgiy = make_user(db, "agent", "Георгий")
    levan = make_user(db, "agent", "Леван")
    listing = make_listing(db, "ss7003")
    pid = client.post("/api/properties", json={"listing_id": listing.id}, headers=auth(georgiy)).json()["id"]
    other = make_listing(db, "mh7003", source="myhome", urls='["https://myhome.ge/7003"]', price_usd=900)
    from app.services.properties import link_listing
    link_listing(db, db.get(Property, pid), other)
    db.add(Deal(property_id=pid, agent_id=georgiy.id, price=850, status="confirmed", client_name="Анна", client_phone="+995 5"))
    db.commit()

    mine = client.get(f"/api/properties/{pid}", headers=auth(georgiy)).json()
    ads = {a["listing_id"]: a for a in mine["listings"]}
    assert ads["ss7003"]["origin"] and not ads["mh7003"]["origin"]
    assert ads["mh7003"]["url"] == "https://myhome.ge/7003" and ads["mh7003"]["price_usd"] == 900
    assert mine["deals"][0]["price"] == 850 and mine["deals"][0]["agent"] == "Георгий"
    assert "client_phone" not in mine["deals"][0]
    assert mine["publications"] == []

    foreign = client.get(f"/api/properties/{pid}", headers=auth(levan)).json()
    assert all(a["url"] is None for a in foreign["listings"]) and foreign["deals"] == []
    assert len(foreign["listings"]) == 2
