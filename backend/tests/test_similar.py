"""Stanislav 11-17: «Похожие объекты» everywhere — one flat is never worked twice. The same photos with a slightly
different area / floor, the owner's phone, the house: the agent sees every candidate and says «Дубль» / «Не дубль»."""
import json

from app.models import Property, PropertyListing, SimilarDecision
from app.services import similar
from app.services.dedup import street_house
from tests.conftest import auth, make_listing, make_user

H = [0x0F0F0F0F0F0F0F0F, 0x123456789ABCDEF0, 0x0FEDCBA987654321, 0x5555AAAA5555AAAA]


def _object(db, client, agent, lid="ss5001", **kw):
    make_listing(db, lid, photo_hashes=json.dumps(H[:3]), **kw)
    r = client.post("/api/properties", json={"listing_id": lid}, headers=auth(agent))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_same_photos_other_area_and_floor_is_shown_and_warned_on_take(db, client):
    vika, ana = make_user(db, "agent", "Вика"), make_user(db, "agent", "Аня")
    pid = _object(db, client, vika)
    # the same flat on myhome: photos re-uploaded (1 bit off), the area and the floor typed differently
    make_listing(db, "mh7001", source="myhome", urls=json.dumps(["https://myhome.ge/7001"]), area=74.0, floor=5,
                 photo_hashes=json.dumps([H[0] ^ 1, H[1], H[2] ^ 2]), address="Tsatskhvebi 4", text="Сдаю, +995 599 00 00 00")
    items = similar.find(db, "l:mh7001", ana)
    assert items[0]["property_id"] == pid and items[0]["level"] == "same" and "общих фото: 3" in items[0]["reasons"]

    r = client.post("/api/properties", json={"listing_id": "mh7001"}, headers=auth(ana))
    assert r.status_code == 409 and r.json()["code"] == "possible_duplicate"
    assert [x["property_id"] for x in r.json()["data"]["items"]] == [pid]
    assert db.query(Property).count() == 1  # nothing made yet

    # «Это другой объект — беру»: taken, and that object is not offered for this ad again
    r = client.post("/api/properties", json={"listing_id": "mh7001", "force": True}, headers=auth(ana))
    assert r.status_code == 200 and r.json()["agent_id"] == ana.id
    assert db.query(SimilarDecision).filter_by(verdict="different").count() == 1
    assert all(x["property_id"] != pid for x in similar.find(db, "l:mh7001", ana))


def test_dubl_links_the_ad_to_the_object(db, client):
    vika, ana = make_user(db, "agent", "Вика"), make_user(db, "agent", "Аня")
    pid = _object(db, client, vika)
    make_listing(db, "mh7002", source="myhome", urls=json.dumps(["https://myhome.ge/7002"]), photo_hashes=json.dumps(H[:2]))
    r = client.post("/api/similar/decide", json={"a": "l:mh7002", "b": f"p:{pid}", "same": True}, headers=auth(ana))
    assert r.json() == {"ok": True, "verdict": "same", "linked_to": pid, "revived": None, "owner_taken": None}
    assert db.query(PropertyListing).filter_by(listing_id="mh7002").one().property_id == pid
    # the object's card lists it no more as a stray ad: it is the object's own now, first, «привязано» (Stanislav 11-24)
    items = client.get(f"/api/properties/{pid}/similar", headers=auth(vika)).json()["items"]
    head = [x["listing_id"] for x in items if x.get("linked")]
    assert "mh7002" in head and [x["listing_id"] for x in items[:len(head)]] == head
    assert sum(x.get("listing_id") == "mh7002" for x in items) == 1


def test_object_card_shows_ads_of_every_site_and_competitors_by_phone_and_house(db, client):
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika, address="ул. Палиашвили 10")
    # no shared photos: the same house, bedrooms, floor ±1, area ±10% → «похож», for the agent to look at
    make_listing(db, "ss5002", urls=json.dumps(["https://ss.ge/5002"]), text="квартира", address="Paliashvili st 10", floor=5, area=74.0)
    # the same house, 3 floors up and 90 m²: another flat of the house (eval 11-17), not shown
    make_listing(db, "ss5004", urls=json.dumps(["https://ss.ge/5004"]), text="квартира", address="Paliashvili st 10", floor=7, area=90.0)
    # another district and house, other params: not shown
    make_listing(db, "ss5003", urls=json.dumps(["https://ss.ge/5003"]), text="квартира", address="Kazbegi 30", district="Сабуртало",
                 bedrooms=4, area=150.0, floor=12, price_usd=3000)
    got = {x["listing_id"]: x for x in client.get(f"/api/properties/{pid}/similar", headers=auth(vika)).json()["items"] if x["kind"] == "ad"}
    assert got["ss5002"]["level"] == "maybe" and "тот же дом" in got["ss5002"]["reasons"]
    assert "ss5003" not in got and "ss5004" not in got


def test_not_dubl_hides_the_pair(db, client):
    vika, ana = make_user(db, "agent", "Вика"), make_user(db, "agent", "Аня")
    pid = _object(db, client, vika)
    p = db.get(Property, pid)
    make_listing(db, "mh7003", source="myhome", urls=json.dumps(["https://myhome.ge/7003"]), photo_hashes=json.dumps(H[:2]))
    item = next(x for x in similar.find(db, "l:mh7003", ana) if x["kind"] == "object")
    assert item["can_open"] is False  # the card stays closed; its photos show (api media_file)
    assert item["agent"] == "Вика"
    client.post("/api/similar/decide", json={"a": f"p:{p.id}", "b": "l:mh7003", "same": False}, headers=auth(ana))
    assert not any(x["kind"] == "object" for x in similar.find(db, "l:mh7003", ana))


def test_compare_twin_and_nothing():
    base = {"urls": set(), "phones": [], "names": frozenset(), "author": None, "cads": set(), "hashes": [],
            "house": (frozenset(), None, ""), "at": (41.7, 44.77), "district": "Ваке", "floor": 4, "area": 70.0,
            "bedrooms": 2, "price_usd": 850.0}
    twin = {**base, "at": (41.705, 44.775), "area": 72.0, "price_usd": 900.0}
    # a twin by params, price and district alone names no flat (Stanislav 11-20, object 15488): not shown
    assert similar.compare(base, twin)[0] is None
    other = {**twin, "bedrooms": 3}
    assert similar.compare(base, other)[0] is None
    phone = {**other, "phones": ["+995 555 12 34 56"], "floor": 4}
    # the owner's phone and the floor, the bedrooms differ: one owner's other flat, never «дубль» (David 11-19 §13.5)
    assert similar.compare({**base, "phones": ["555123456"]}, phone)[0] != "same"
    # the phone, one building and the params fit: «дубль» (§8)
    house = street_house("Paliashvili 10")
    assert similar.compare({**base, "phones": ["555123456"], "house": house}, {**twin, **{"phones": phone["phones"], "house": house}})[0] == "same"



def test_owner_name_alone_is_no_duplicate():
    """Irakli 11-19: dozens of other flats listed «то же имя собственника» — the name only weighs a real candidate."""
    a = {"urls": set(), "phones": [], "names": frozenset({"нино", "беридзе"}), "author": None, "cads": set(), "hashes": [],
         "house": street_house("5 Eredvi Street, Dighomi"), "street": frozenset(), "at": (41.75, 44.75), "district": "Дигоми",
         "floor": None, "area": None, "bedrooms": None, "price_usd": 500.0}
    other = {**a, "house": street_house("21 Zurab Sakandelidze"), "at": (41.71, 44.76), "district": "Ваке",
             "area": 120.0, "price_usd": 1000.0}
    assert similar.compare(a, other)[0] is None  # the same full name, nothing else in common
    assert similar.compare({**a, "names": frozenset({"нино"})}, {**other, "names": frozenset({"нино"})})[0] is None
    level, _, why = similar.compare(a, {**a})  # the same name and the same house: shown, the name adds weight
    assert level == "maybe" and "тот же дом" in why and "то же имя собственника" in why
    assert "то же имя собственника" not in similar.compare({**a, "names": frozenset({"нино"})}, {**a})[2]  # first name only


def test_same_author_alone_is_no_duplicate():
    """Stanislav 11-19: an agent's account posts many flats — the same author only weighs a real candidate."""
    a = {"urls": set(), "phones": [], "names": frozenset(), "author": ("ss", "u42"), "cads": set(), "hashes": [],
         "house": street_house("5 Eredvi Street, Dighomi"), "street": frozenset(), "at": (41.75, 44.75), "district": "Дигоми",
         "floor": None, "area": None, "bedrooms": None, "price_usd": 500.0}
    other = {**a, "house": street_house("21 Zurab Sakandelidze"), "at": (41.71, 44.76), "district": "Ваке",
             "area": 120.0, "price_usd": 1000.0}
    assert similar.compare(a, other)[0] is None
    level, _, why = similar.compare(a, {**a})
    assert level == "maybe" and "тот же дом" in why and "тот же автор объявления" in why

def test_object_photos_are_hashed_and_found(db, client, monkeypatch):
    """An object shot by our agent (no ad photos): its own photos are hashed by the worker and an ad with them is found."""
    import io
    from PIL import Image
    from app import storage
    from app.models import PropertyMedia
    files = {}
    monkeypatch.setattr(storage, "read", lambda k: files.get(k))
    im = Image.new("L", (90, 80))
    im.putdata([(x * 7 + y * 3) % 256 for y in range(80) for x in range(90)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    files["properties/9/a.png"] = buf.getvalue()
    vika = make_user(db, "agent", "Вика")
    p = Property(status="published", address="Kazbegi 30", agent_id=vika.id)
    db.add(p)
    db.flush()
    db.add(PropertyMedia(property_id=p.id, kind="photo", path="properties/9/a.png"))
    db.commit()
    from app.services import matching
    assert matching.backfill_media_hashes(db, max_batches=1, out=lambda *_: None) == 1
    h = [db.query(PropertyMedia.dhash).scalar() & matching.U64]
    make_listing(db, "ss8001", photo_hashes=json.dumps([h[0] ^ 1, 77]), address="other 1", district="Сабуртало")
    make_listing(db, "ss8002", photo_hashes=json.dumps([h[0], 99]), address="other 2", district="Сабуртало")
    ana = make_user(db, "agent", "Аня")
    assert [x["property_id"] for x in similar.find(db, "l:ss8001", ana) if x["kind"] == "object"] == [p.id]  # one photo: «похож»


def test_rejected_ads_are_candidates_of_an_object_not_of_an_ad(db, client):
    """11-23: competitors' tg posts / agents' FB ads are «rejected» by the bot, and most often they are the agents'
    copies of our object: an object's «Похожие» (its duplicate search) lists them, an ad's own does not."""
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika)
    make_listing(db, "tg:rival:77", source="tg:rival", status="rejected", status_reason="источник-конкурент",
                 urls=json.dumps(["https://t.me/rival/77"]), address="Kazbegi 30", photo_hashes=json.dumps(H[:3]))
    make_listing(db, "ss8001", urls=json.dumps(["https://ss.ge/8001"]), address="Kazbegi 31", photo_hashes=json.dumps(H[:3]))
    assert "l:tg:rival:77" in {c["key"] for c in similar.find(db, f"p:{pid}", vika)}
    assert "l:tg:rival:77" not in {c["key"] for c in similar.find(db, "l:ss8001", vika)}
