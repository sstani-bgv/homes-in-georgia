"""The public feed (services/site_feed.py, api/site.py) and the CRM
screens' API for sale objects and cars (api/offers.py)."""
import io
import json

import pytest
from PIL import Image

from tests import hge_import as imp
from app.models import Car, Owner, Property, PropertyMedia, PropertyPublication
from app.services import catalog, site_feed
from tests.conftest import auth, make_user

AGENT = {"telegram": "David_Tibelashvili", "phone": "+995 599 20 67 16"}
RENT = {"id": "rent-551776", "service": "rent", "source": "rent", "deal": "rent", "period": "month", "kind": "apartment",
        "title": "5 Chavchavadze Ave", "headline": "Cozy", "district": "Vake", "metro": "Rustaveli", "lat": 41.7, "lon": 44.7,
        "price": 800.0, "currency": "USD", "price_usd": 800, "deposit": 800.0, "rooms": 2, "area": 60.0, "floor": 3,
        "floors": 9, "building": "new", "condition": "renovated", "heating": "central",
        "amenities": ["conditioner", "washer", "parking", "pool"], "pets": "agreement", "months": [6, 12, 24],
        "tenants": 3, "exclusive": True, "description": "Nice flat", "agent": AGENT, "photos": ["media/rent/1.jpg", "media/rent/2.jpg"],
        "post_url": "https://t.me/rent_tbilisi_ge/551776", "published_at": "2026-11-14T12:15:26+04:00"}
SALE = {"id": "sale-77", "service": "sale", "source": "sale", "deal": "sale", "kind": "apartment", "title": "1 Sale St",
        "district": "Saburtalo", "price": 100000.0, "currency": "USD", "price_usd": 100000, "rooms": 2, "area": 50.0,
        "condition": "white_frame", "description": "Sale", "agent": AGENT, "photos": ["media/sale/77-1.jpg"],
        "post_url": "https://t.me/sale_in_tbilisi/77", "published_at": "2026-11-13T10:00:00+04:00"}
CAR = {"id": "car-rent-9", "service": "car-rent", "source": "car-rent", "deal": "rent", "period": "day", "kind": "car",
       "title": "Jeep Compass", "price": 60.0, "currency": "USD", "price_usd": 60, "year": 2022, "gearbox": "auto",
       "fuel": "petrol", "body": "suv", "seats": 5, "pickup": "8 Arakishvili St", "description": "car", "agent": AGENT,
       "photos": ["media/car-rent/9-1.jpg"], "post_url": "https://t.me/CarsRentInGeorgia/9",
       "published_at": "2026-11-12T10:00:00+04:00"}
ITEMS = [RENT, SALE, CAR]
CAR_SALE = {"id": "car-sale-18", "service": "car-sale", "source": "car-sale", "deal": "sale", "kind": "car", "title": "Corvette",
            "price": 30000.0, "currency": "USD", "price_usd": 30000, "year": 2014, "mileage": 76538.0, "fuel": "petrol",
            "exclusive": True, "agent": AGENT, "photos": ["media/car-sale/18-1.jpg"], "post_url": "https://t.me/CarsSale/18",
            "published_at": "2026-11-11T10:00:00+04:00"}
NEW = {"id": "new-5", "service": "new", "source": "new", "deal": "sale", "kind": "apartment", "title": "3 New Tower",
       "district": "Vake", "metro": "Rustaveli", "price": 90000.0, "currency": "USD", "price_usd": 90000, "area": 45.0,
       "building": "new", "completion": "Q4 2027", "amenities": ["balcony", "pool"], "agent": AGENT,
       "photos": ["media/new/5-1.jpg"], "published_at": "2026-11-10T10:00:00+04:00"}
COMM_SALE = {"id": "commercial-3", "service": "commercial", "source": "commercial", "deal": "sale", "kind": "commercial",
             "title": "Shop", "price": 200000.0, "currency": "USD", "area": 100.0, "agent": AGENT, "photos": ["media/c/3.jpg"]}
COMM_RENT = {"id": "commercial-4", "service": "commercial", "source": "commercial", "deal": "rent", "period": "month",
             "kind": "commercial", "title": "Office", "price": 1000.0, "currency": "USD", "area": 80.0, "agent": AGENT,
             "photos": ["media/c/4.jpg"]}


@pytest.fixture(autouse=True)
def _feed(monkeypatch):
    site_feed.clear_cache()
    monkeypatch.setattr(site_feed, "rates", lambda: {"USD": 1.0, "GEL": 0.4, "EUR": 1.1})
    monkeypatch.setattr(imp, "fetch", lambda path: (".jpg", jpg()))
    yield
    site_feed.clear_cache()


def jpg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "JPEG")
    return buf.getvalue()


def run(db, items=ITEMS, **kw):
    return imp.run(db, [dict(i) for i in items], **kw)


def by_site(db, sid):
    return db.query(Property).filter(Property.post_fields["site_id"].astext == sid).one()


def sale(db, sid):
    return by_site(db, sid)


def test_feed_only_public_three_sources(db, client):
    run(db, [*ITEMS, NEW, CAR_SALE])
    sale(db, "new-5").status = "archived"
    db.add(Property(status="published", address="No photo"))
    db.add(Property(status="media_uploaded", address="Draft sale", post_fields={"deal": "Sale"}))
    db.add(Car(deal="rent", title="No photo", status="published"))
    db.commit()
    site_feed.clear_cache()
    d = client.get("/api/public/site/listings").json()
    assert d["counts"] == {"rent": 1, "sale": 1, "car-rent": 1, "car-sale": 1} and len(d["items"]) == 4
    assert d["rates_usd"]["GEL"] == 0.4
    assert [i["service"] for i in d["items"]] == ["rent", "sale", "car-rent", "car-sale"]  # published_at desc
    rent, sl, car, cs = d["items"]
    assert rent["legacy_id"] == "rent-551776" and rent["id"].startswith("o") and rent["deal"] == "rent"
    pid = rent["id"][1:]
    assert rent["photos"] == [f"/api/public/site/photos/o{pid}/0", f"/api/public/site/photos/o{pid}/1"]
    assert rent["metro"] == "Rustaveli" and rent["heating"] == "central" and rent["building"] == "new"
    assert rent["condition"] == "renovated" and rent["pets"] == "agreement" and rent["months"] == [6, 12, 24]
    assert rent["amenities"] == ["conditioner", "washer", "parking", "pool"] and rent["agent"] == AGENT
    assert rent["price_usd"] == 800 and rent["kind"] == "apartment" and rent["exclusive"] is True
    assert sl["id"].startswith("o") and sl["legacy_id"] == "sale-77" and sl["deal"] == "sale" and sl["ppsqm_usd"] == 2000
    assert sl["condition"] == "white_frame" and sl["photos"] == [f"/api/public/site/photos/{sl['id']}/0"]
    assert car["id"].startswith("c") and car["title"] == "Jeep Compass" and car["pickup"] == "8 Arakishvili St"
    assert car["seats"] == 5 and car["kind"] == "car" and car["period"] == "day" and car["deal"] == "rent"
    assert cs["deal"] == "sale" and cs["mileage"] == 76538 and cs["exclusive"] is True and "period" not in cs
    assert client.get("/api/public/site/listings").headers["cache-control"] == "public, max-age=60"


def test_sale_hidden_from_bot_catalog(db):
    run(db)
    p = by_site(db, "rent-551776")
    assert catalog.visible(p)  # a rent object with its post stays in the catalog
    p.post_fields = {**p.post_fields, "service": "daily"}
    assert not catalog.visible(p)
    assert catalog.visible(Property(status="published", post_url="u", post_message_id="1", post_fields={"service": "rent"}))
    assert catalog.visible(Property(status="published", post_url="u", post_message_id="1", post_fields={}))


def test_no_private_data(db, client):
    agent = make_user(db, "agent", username="zaza", phone="+995 555 11 22 33")
    owner = Owner(name="SECRETOWNER", phone="+995 599 77 77 77", phone_norm="599777777")
    db.add(owner)
    db.flush()
    run(db, [RENT])
    p = by_site(db, "rent-551776")
    p.owner_id, p.agent_id = owner.id, agent.id
    p.tenant_name, p.tenant_phone, p.cadastral_code = "SECRETTENANT", "+995 500 00 00 01", "01.14.12.003.045"
    p.source_url, p.review_comment, p.archive_comment = "https://secret.example/src", "SECRETREVIEW", "SECRETARCH"
    p.checklist, p.ai_flags = {"x": "SECRETCHECK"}, {"y": "SECRETFLAG"}
    db.add(PropertyMedia(property_id=p.id, kind="owner_id_doc", path="private/properties/1/id.jpg", position=0))
    db.add(PropertyMedia(property_id=p.id, kind="photo", path="private/x.jpg", position=0))
    db.commit()
    site_feed.clear_cache()
    pid = p.id
    blob = client.get("/api/public/site/listings").text + client.get(f"/api/public/site/listings/o{pid}").text
    for bad in ("SECRET", "599777777", "599 77 77", "500 00 00", "01.14.12", "tenant_", "owner", "cadastral", "checklist",
                "ai_flags", "review_comment", "archive", "source_url", "private/", "properties/", "offers/"):
        assert bad.lower() not in blob.lower(), bad
    # every lead goes to the agency's contact, never the object's agent (Irakli 11-22)
    assert json.loads(client.get("/api/public/site/listings").text)["items"][0]["agent"] == AGENT
    assert len(client.get(f"/api/public/site/listings/o{pid}").json()["photos"]) == 2  # the private one is not counted
    assert client.get(f"/api/public/site/photos/o{pid}/2").status_code == 404


def test_detail_legacy_ids_and_photos(db, client):
    run(db, [*ITEMS, NEW])
    p, s, c = by_site(db, "rent-551776"), sale(db, "new-5"), db.query(Car).one()
    d = client.get("/api/public/site/listings/rent-551776")
    assert d.status_code == 200 and d.json()["id"] == f"o{p.id}" and d.json()["video"] is None
    assert client.get("/api/public/site/listings/sale-77").json()["id"].startswith("o")
    assert client.get("/api/public/site/listings/car-rent-9").json()["id"] == f"c{c.id}"
    assert client.get(f"/api/public/site/listings/o{s.id}").json()["completion"] == "Q4 2027"
    assert client.get(f"/api/public/site/listings/o{p.id}").json()["description"] == "Nice flat"
    assert client.get("/api/public/site/listings/nope").status_code == 404
    for ident, n in ((f"o{p.id}", 2), (f"o{s.id}", 1), (f"c{c.id}", 1)):
        r = client.get(f"/api/public/site/photos/{ident}/0")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=86400"
        assert client.get(f"/api/public/site/photos/{ident}/0?p=1").headers["content-type"] == "image/webp"
        assert client.get(f"/api/public/site/photos/{ident}/{n}").status_code == 404
    assert client.get(f"/api/public/site/photos/{p.id}/0").status_code == 404  # the bare number is no longer an id
    p.status = "rented"
    db.commit()
    assert client.get("/api/public/site/listings/rent-551776").status_code == 404
    assert client.get(f"/api/public/site/photos/o{p.id}/0").status_code == 404


# ---------------------------------------------------------------- CRM API

def up(client, h, kind, oid, n=1, name="a.jpg"):
    return client.post(f"/api/offers/{kind}/{oid}/media", headers=h, files=[("files", (name, jpg(), "image/jpeg"))] * n)


def test_crm_car_and_validation(db, client):
    agent = make_user(db, "car_operator")
    h = auth(agent)
    assert client.post("/api/offers/car", headers=h, json={"title": "X"}).status_code == 422  # no deal
    assert client.post("/api/offers/car", headers=h, json={"deal": "lease", "title": "X"}).status_code == 422
    r = client.post("/api/offers/car", headers=h, json={"deal": "rent", "title": "Kia Rio", "price": 40, "period": "day",
                                                         "year": 2020, "gearbox": "auto", "pickup": "Airport"})
    cid = r.json()["id"]
    assert up(client, h, "car", cid, 41).status_code == 422  # max 40 photos
    assert client.post(f"/api/offers/car/{cid}/media", headers=h, files=[("files", ("a.exe", b"x", "x/y"))]).status_code == 400
    assert up(client, h, "car", cid).status_code == 200
    r = client.post(f"/api/offers/car/{cid}/media", headers=h, files=[("files", ("v.mp4", b"vid", "video/mp4"))])
    assert r.status_code == 200 and r.json()["video"].endswith(".mp4")
    assert client.post(f"/api/offers/car/{cid}/publish", headers=h, json={"tg": False}).status_code == 200  # the site only
    it = client.get(f"/api/public/site/listings/c{cid}").json()
    assert it["service"] == "car-rent" and it["video"] == f"/api/public/site/video/c{cid}" and it["pickup"] == "Airport"
    assert client.get(f"/api/public/site/video/c{cid}").status_code == 200
    assert client.get("/api/public/site/video/c999").status_code == 404


def test_crm_access(db, client):
    assert client.get("/api/offers/car").status_code == 401
    assert client.get("/api/offers/sale", headers=auth(make_user(db, "admin"))).status_code == 422  # sales are properties now
    for role, code in (("recruit", 403), ("daily_rent_manager", 403), ("agent", 403), ("operator", 403), ("car_operator", 200), ("admin", 200)):
        assert client.get("/api/offers/car", headers=auth(make_user(db, role))).status_code == code, role
    a = make_user(db, "car_operator")
    b = make_user(db, "admin")
    o = client.post("/api/offers/car", headers=auth(a), json={"deal": "rent", "title": "A", "agent_id": b.id}).json()
    assert o["agent_id"] == b.id  # the car operator can assign it (admin rights over the cars)


# ---------------------------------------------------------------- the two publications: Telegram and the site

def _sale(db, agent, status="review", **pf):
    p = Property(source_type="direct", status=status, agent_id=agent.id, address="5 Rustaveli Ave", district="Vake",
                 price=255000, area=85, floor=1, total_floors=5, bedrooms=2, lat=41.7, lng=44.78,
                 post_fields={"deal": "Sale", "service": "commercial", **pf})
    db.add(p)
    db.flush()
    db.add(PropertyMedia(property_id=p.id, kind="photo", path=f"properties/{p.id}/1.jpg", position=0))
    db.commit()
    return p


def test_sale_object_is_a_property_with_its_own_lists_and_two_targets(db, client, sent):
    agent, op = make_user(db, "agent", name="Zaza"), make_user(db, "operator")
    pf = {"prop_type": "Cafe", "building": "OldBuilding", "design": "New", "condition": "WhiteFrame", "heating": "ACHeating",
          "bathrooms": "2Bathroom", "amenities": ["Terrace", "Fireplace"], "metro": "Rustaveli", "headline": "Corner cafe",
          "sale_price": 255000}
    s = _sale(db, agent, **pf)
    rent = Property(source_type="direct", status="review", agent_id=agent.id, address="Rent St", price=900)
    db.add(rent)
    db.commit()
    # «Объекты» → Аренда / Продажа: every list, the board and the counts apart
    ids = lambda deal, **q: [x["id"] for x in client.get("/api/properties", headers=auth(op), params={"deal": deal, **q}).json()["items"]]  # noqa: E731
    assert ids("sale") == [s.id] and ids("rent") == [rent.id]
    board = client.get("/api/properties/board", headers=auth(op), params={"deal": "sale"}).json()
    assert [x["id"] for x in board["review"]] == [s.id]
    assert client.get("/api/properties/counts", headers=auth(op), params={"deal": "rent"}).json() == {"review": 1}
    text = client.get(f"/api/properties/{s.id}/post", headers=auth(op)).json()["text"]
    assert "🌟 Corner cafe 🌟" in text and "🏢 #Cafe for #Sale" in text and "💰255.000$" in text
    assert "#OldBuilding | #NewApartment | #WhiteFrame" in text and "85 Sq.m | 1/5 Floor | #ACHeating | #2Bathroom" in text
    assert "#Price240000to270000" in text and text.endswith("SALE IN TBILISI🇬🇪")
    # one «Опубликовать», two checkboxes: none = refused; the site only = no post, on hge.ge
    assert client.post(f"/api/properties/{s.id}/publish", headers=auth(op), json={"tg": False, "site": False}).status_code == 400
    r = client.post(f"/api/properties/{s.id}/publish", headers=auth(op), json={"tg": False, "site": True}).json()
    assert r["status"] == "published" and r["site_hidden"] is False and not r.get("post_url") and not sent.of("publish_post")
    it = client.get(f"/api/public/site/listings/o{s.id}").json()
    assert it["deal"] == "sale" and it["service"] == "commercial" and it["kind"] == "commercial" and it["type"] == "cafe"
    assert it["bathrooms"] == 2 and it["heating"] == "ac" and it["building"] == "old" and it["condition"] == "white_frame"
    assert it["amenities"] == ["terrace", "fireplace"] and it["price"] == 255000 and it["ppsqm_usd"] == 3000
    assert "deposit" not in it and catalog.visible(db.get(Property, s.id)) is False  # the bot is a rental bot
    # later: Telegram only, the site stays as it is
    r = client.post(f"/api/properties/{s.id}/publish", headers=auth(op), json={"tg": True, "site": False}).json()
    assert r["post_url"] == "https://t.me/rent_tbilisi_ge/777" and r["site_hidden"] is False
    (post,), = sent.of("publish_post")
    assert post.post_text == text and post.post_rich is None  # the rich post is the rent one
    # the first publication without «На сайт» keeps the object off the site
    t = _sale(db, agent, prop_type="Apartment")
    r = client.post(f"/api/properties/{t.id}/publish", headers=auth(op), json={"tg": True, "site": False}).json()
    assert r["site_hidden"] is True and client.get(f"/api/public/site/listings/o{t.id}").status_code == 404


def test_old_sale_ids_keep_resolving(db, client):
    p = _sale(db, make_user(db, "agent"), status="published", offer_ident="s77", site_id="sale-77")
    p.published_at = p.updated_at
    db.commit()
    from app import storage
    storage.put(f"properties/{p.id}/1.jpg", jpg())
    for ident in ("s77", "sale-77", f"o{p.id}"):
        assert client.get(f"/api/public/site/listings/{ident}").json()["id"] == f"o{p.id}", ident
    assert client.get("/api/public/site/photos/s77/0").status_code == 200
    assert client.get("/api/public/site/listings/s78").status_code == 404


def test_car_telegram_post_and_failure(db, client, sent, monkeypatch):
    from app.config import settings
    from app.telegram import notify
    op = make_user(db, "car_operator")
    cid = client.post("/api/offers/car", headers=auth(op), json={"deal": "sale", "title": "Toyota Camry", "year": 2018,
                      "price": 14500, "gearbox": "auto", "fuel": "hybrid", "body": "sedan", "seats": 5, "mileage": 76538,
                      "is_exclusive": True}).json()["id"]
    text = client.get(f"/api/offers/car/{cid}/post", headers=auth(op)).json()["text"]
    assert text.startswith("🚗 #Toyota Camry 2018") and "🏁 #Car for #Sale" in text and "✨ #Sedan | #Automatic | #Hybrid" in text
    assert "5 Seats | 76.538 km" in text and "💰 14.500$" in text and "#Price10000to15000" in text and "#CarSale" in text
    rent = client.post("/api/offers/car", headers=auth(op), json={"deal": "rent", "title": "Kia Rio", "price": 40, "period": "day",
                       "deposit": 200, "pickup": "Airport"}).json()["id"]
    t2 = client.get(f"/api/offers/car/{rent}/post", headers=auth(op)).json()["text"]
    assert "💰 40$/day + Deposit 200$" in t2 and "📍 Airport" in t2 and "#CarRent" in t2
    agent = make_user(db, "agent")
    assert client.post(f"/api/offers/car/{cid}/publish", headers=auth(op), json={}).status_code == 400  # no photo
    up(client, auth(op), "car", cid)
    assert client.post(f"/api/offers/car/{cid}/publish", headers=auth(op), json={"tg": False, "site": False}).status_code == 400
    assert client.post(f"/api/offers/car/{cid}/publish", headers=auth(agent), json={}).status_code == 403  # Telegram: operators
    assert client.post(f"/api/offers/car/{cid}/publish", headers=auth(op), json={"keys": ["offers/x.jpg"]}).status_code == 404
    r = client.post(f"/api/offers/car/{cid}/publish", headers=auth(op), json={}).json()
    assert r["status"] == "published" and r["post_url"] == "https://t.me/rent_tbilisi_ge/777" and r["public"] is True
    (post,), = sent.of("publish_post")
    assert post.post_text == text and [k for k, _ in post.post_media_keys] == ["photo"]
    monkeypatch.setattr(notify, "publish_post", lambda p: None)  # Telegram refused: nothing changes, the site neither
    client.post(f"/api/offers/car/{rent}/media", headers=auth(op), files=[("files", ("a.jpg", jpg(), "image/jpeg"))])
    assert client.post(f"/api/offers/car/{rent}/publish", headers=auth(op), json={"text": "Мой текст"}).status_code == 502
    assert db.get(Car, rent).post_url is None and db.get(Car, rent).status == "draft"
    monkeypatch.setattr(settings, "BOT_TOKEN", "")
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    assert client.post(f"/api/offers/car/{rent}/publish", headers=auth(op), json={}).status_code == 200  # dev dry run


def test_rent_object_site_button(db, client):
    op, agent = make_user(db, "operator"), make_user(db, "agent")
    p = Property(source_type="manual", status="published", address="1 Site St", price=900, agent_id=agent.id)
    db.add(p)
    db.flush()
    db.add(PropertyMedia(property_id=p.id, kind="photo", path="properties/x/1.jpg", position=0))
    db.commit()
    assert client.get(f"/api/public/site/listings/o{p.id}").status_code == 200
    assert client.post(f"/api/properties/{p.id}/site", headers=auth(make_user(db, "agent", "Чужой")), json={"on": False}).status_code == 403
    r = client.post(f"/api/properties/{p.id}/site", headers=auth(op), json={"on": False}).json()
    assert r["site_hidden"] is True and r["status"] == "published"
    assert client.get(f"/api/public/site/listings/o{p.id}").status_code == 404
    assert client.post(f"/api/properties/{p.id}/site", headers=auth(op), json={"on": True}).json()["site_hidden"] is False
    assert client.get(f"/api/public/site/listings/o{p.id}").status_code == 200
    p.status = "review"
    db.commit()
    assert client.post(f"/api/properties/{p.id}/site", headers=auth(op), json={"on": True}).status_code == 409
