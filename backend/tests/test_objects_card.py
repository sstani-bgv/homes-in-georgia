"""Stage 2: the object is the main entity: listings + publications per object, the publish paths write
property_publications, and the full card payload respects the owner-contact rule."""
from sqlalchemy import select, text

from app.models import ChannelPost, Deal, Property, PropertyListing, PropertyPublication
from app.services.properties import record_publication
from tests.conftest import auth, make_listing, make_user




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
    # the same post reported again updates its row, a new post adds one and links the matching channel post
    p = db.get(Property, pid)
    record_publication(db, p, "tg", "https://t.me/rent_tbilisi_ge/777", "777")
    db.add(ChannelPost(message_id="wg:45", link="https://t.me/c/123/45"))
    db.flush()
    record_publication(db, p, "tg", "https://t.me/c/123/45", "45")
    db.commit()
    r = client.get(f"/api/properties/{pid}", headers=auth(nino)).json()
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


def test_base_search_words_order_and_counts(db, client):
    from datetime import datetime, timezone
    zaza = make_user(db, "agent", "Zaza")
    nino = make_user(db, "operator", "Нино")
    t = lambda d: datetime(2026, 1, d, tzinfo=timezone.utc)
    a = Property(status="published", district="Vake", price=600, agent_id=zaza.id, published_at=t(1),
                 description="#Vake #2Bed for #Rent", address="5 Abashidze St")
    b = Property(status="rented", district="Saburtalo", price=600, agent_id=zaza.id, published_at=t(5),
                 description="#Saburtalo #1Bed", address="1 Kazbegi Ave")
    c = Property(status="archived", district="Vake", price=900, published_at=t(9), description="#Vake #2Bed")
    db.add_all([a, b, c])
    db.commit()
    ids = lambda q, **kw: [x["id"] for x in client.get("/api/properties", params={"base": 1, "status": "published,rented",
                                                                                 "q": q, **kw}, headers=auth(nino)).json()["items"]]
    assert ids("") == [b.id, a.id]                       # newest publication first; the archive is not the base
    assert ids("Vake 2Bed") == [a.id] and ids("#vake #2bed") == [a.id]
    assert ids("600 zaza") == [b.id, a.id] and ids("Saburtalo 600") == [b.id]
    assert ids("", limit=1, offset=1) == [a.id]
    counts = client.get("/api/properties/counts", headers=auth(nino)).json()
    assert counts == {"published": 1, "rented": 1, "archived": 1}


def test_published_photos_fit_telegram(tmp_path):
    """A 48 MP iPhone original (8064x6048, 11 MB) is refused by Telegram (PHOTO_INVALID_DIMENSIONS): the post gets a
    watermarked JPEG of at most 2560 px."""
    import importlib.util
    from PIL import Image
    from app.telegram import notify
    spec = importlib.util.spec_from_file_location("notify_real", notify.__file__)  # conftest stubs notify.*
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    src = tmp_path / "big.jpg"
    Image.new("RGB", (8064, 6048), "white").save(src)
    with Image.open(real.watermarked(str(src), str(tmp_path))) as out:
        assert (out.format, out.size) == ("JPEG", (2560, 1920))
