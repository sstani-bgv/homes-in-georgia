"""Посты канала → объекты (services/channel_import.py), удалённый пост → архив (services/post_sync.py). No Telegram:
channel_import.ask is faked, no map lookups."""
import base64
import io

from PIL import Image

from app.models import Event, Property, PropertyMedia
from app.services import channel_import as CI
from app.services import post_sync
from tests.conftest import make_user

TEXT = """#Vera 🚇 #Rustaveli
📍16-18-20 Simon Janashia St

🏢 #2Bed #Apartment for #Rent
✨ #NewBuilding | #Mixed
🏠 115 sq.m | 6/8 Floor | #CentralHeating | #Bath
👬Tenants: 1-4
🐕Pets: #ByAgreement
💰 1300$ + Deposit 1300$
📲 @David_Tibelashvili | +995 599 20 67 16 #Roman2"""


def _jpg(c):
    import random
    rnd = random.Random(str(c))  # a textured picture: a flat colour has no dHash to compare
    img = Image.new("L", (64, 48))
    img.putdata([rnd.randrange(256) for _ in range(64 * 48)])
    b = io.BytesIO()
    img.resize((256, 192)).convert("RGB").save(b, "JPEG")
    return base64.b64encode(b.getvalue()).decode()


def _post(msg, text=TEXT):
    return {"id": msg, "date": "2026-11-22T08:00:00+00:00", "text": text, "links": [],
            "photos": [{"id": msg * 10 + n, "b64": _jpg((n * 60, 90, 120))} for n in range(3)]}


def _fake(monkeypatch, channel_posts, there=None):
    def ask(path, body, timeout=250):
        if path == "/channel":
            return {"ok": True, "posts": [p for p in channel_posts if p["id"] > body["min_id"]
                                          and (not body["max_id"] or p["id"] < body["max_id"])]}
        return None if there is None else {"ok": True, "ids": [i for i in body["ids"] if i in there]}
    monkeypatch.setattr(CI, "ask", ask)
    monkeypatch.setattr(CI, "_point", lambda p: None)


def test_a_channel_post_becomes_a_published_object_once(db, monkeypatch):
    roman = make_user(db, "agent", "Роман")
    roman.agent_tag = "Roman2"
    db.commit()
    _fake(monkeypatch, [_post(500), _post(501, "❗️#FlatRented\nNice apartment in 📍Vera found a tenant")])
    assert CI.run(db) == {"created": 1, "not_object": 1, "located": 0}
    p = db.query(Property).one()
    assert (p.status, p.site_hidden, p.post_url) == ("published", False, "https://t.me/rent_tbilisi_ge/500")
    assert (p.address, p.bedrooms, float(p.area), p.floor, p.total_floors, float(p.price)) == \
        ("16-18-20 Simon Janashia St", 2, 115.0, 6, 8, 1300.0)
    assert p.agent_id == roman.id and p.post_fields["pets"] == "ByAgreement" and p.post_fields["deposit"] == 1300
    assert db.query(PropertyMedia).filter_by(property_id=p.id, kind="photo").count() == 3
    assert not p.is_exclusive
    assert CI.run(db) == {"located": 0}  # the cursor moved past 501: nothing new


def test_a_repost_moves_the_link_and_a_deleted_post_archives(db, monkeypatch):
    _fake(monkeypatch, [_post(600)])
    CI.run(db)
    pid = db.query(Property).one().id
    _fake(monkeypatch, [_post(600), _post(610, TEXT.replace("1300$ +", "1400$ +"))])  # the same flat again, dearer
    assert CI.run(db) == {"relinked": 1, "located": 0}
    assert db.query(Property).count() == 1 and db.get(Property, pid).post_url.endswith("/610")
    assert float(db.get(Property, pid).price) == 1400

    _fake(monkeypatch, [], there=set())  # the account says 610 is gone
    assert post_sync.run(db, pause=0) == 1
    db.expire_all()
    p = db.get(Property, pid)
    assert (p.status, p.archive_reason) == ("archived", "not_actual")
    assert db.query(Event).filter_by(entity_id=str(pid), action="archived").one().actor_label == "сверка с каналом"

    _fake(monkeypatch, [_post(620, "❗️#Exclusive\n" + TEXT)])  # posted once more, now ours: back from the archive
    assert CI.run(db) == {"relinked": 1, "located": 0}
    db.expire_all()
    assert db.get(Property, pid).status == "published" and db.get(Property, pid).is_exclusive
    from app.models import Job
    assert db.query(Job).filter_by(kind="dup_scan").count() == 1


def test_queries_go_from_the_exact_address_to_the_street():
    assert list(CI._queries("50/3 Mikheili Tsinamdzghvrishvili St")) == [
        ("50/3 Mikheili Tsinamdzghvrishvili St", False), ("50 Mikheili Tsinamdzghvrishvili", False),
        ("Mikheili Tsinamdzghvrishvili", True)]


def test_the_map_point_comes_from_the_posts_google_link_else_the_place(monkeypatch):
    from app.services import geo
    p = Property(description="📍[22 Budapesht Street](https://www.google.com/maps?q=41.7288458%2C44.7615617&entry=gps)")
    assert CI._point(p) and (p.lat, p.lng) == (41.7288458, 44.7615617)
    asked = []
    monkeypatch.setattr(geo, "search", lambda q: asked.append(q) or {"items": [{"lat": 41.5, "lng": 45.0}]})
    p = Property(description="#Isani 🚇 #IsaniMetro\n📍Dirsi\n🏢 #2Bed")
    assert CI._point(p) and asked == ["Dirsi, Tbilisi"] and p.post_fields == {"approx": True}
    asked.clear()
    assert CI._point(Property(address="Rustavi, 28 Leonidze St")) and asked == ["Rustavi, 28 Leonidze St"]


def test_the_same_photos_under_another_address_spelling_relink(db, monkeypatch):
    _fake(monkeypatch, [_post(700)])
    CI.run(db)
    first = _post(700)
    again = {**_post(710, TEXT.replace("16-18-20 Simon Janashia St", "16 Simon Janashia Street")), "photos": first["photos"]}
    _fake(monkeypatch, [again])
    assert CI.run(db)["relinked"] == 1
    assert db.query(Property).count() == 1 and db.query(Property).one().post_url.endswith("/710")


SALE = """SALE IN TBILISI

#Lisi 🚇 #Delisi 📍VIII Lane Tstatshvebi, 11

🏢 #1bed #Apartment for #Sale
✨ #NewBuilding | #BlackFrame
🏠 125 Sq.m | 3/4 Floor |

✅ #Elevator
✅ #ParkingPlace 12,000$
✅ #Balcony

💰 212.500$  |
 0% commission
#Price210000to240000

📲 @David_Tibelashvili |
+995 599 20 67 16 #Shafi

📍APARTMENTS ON MAP📍"""


def test_a_sale_channel_post_becomes_a_sale_object_and_never_relinks_a_rent_one(db, monkeypatch):
    shafi = make_user(db, "agent", "Шафи")
    shafi.agent_tag = "Shafi"
    db.commit()
    rent = SALE.replace("for #Sale", "for #Rent").replace("212.500$", "900$")
    _fake(monkeypatch, [_post(700, rent)])
    CI.run(db)  # the same flat for rent in the rent channel
    _fake(monkeypatch, [_post(77499, SALE)])
    assert CI.run(db, CI.SALE_CHANNEL) == {"created": 1, "located": 0}
    p = db.query(Property).filter(Property.post_url == "https://t.me/sale_in_tbilisi/77499").one()
    assert (p.post_fields["deal"], p.post_fields["sale_price"], float(p.price), p.currency) == ("Sale", 212500, 212500, "USD")
    assert (p.address, p.district, p.bedrooms, float(p.area), p.floor, p.agent_id) == \
        ("VIII Lane Tstatshvebi, 11", "Лиси", 1, 125.0, 3, shafi.id)
    from app.services import site_feed
    it = site_feed.item(p, 3, None, {})
    assert (it["deal"], it["kind"], it["price"], it["ppsqm_usd"]) == ("sale", "apartment", 212500, 1700)
    _fake(monkeypatch, [_post(77520, SALE.replace("212.500$", "199.000$"))])  # a repost, cheaper
    assert CI.run(db, CI.SALE_CHANNEL) == {"relinked": 1, "located": 0}
    db.expire_all()
    assert p.post_url.endswith("/77520") and p.post_fields["sale_price"] == 199000
    assert db.query(Property).count() == 2


def test_sale_fields_read_land_and_odd_prices():
    land = "#DidiDighomi 🚇 #Sarajishvili\n📍Mirian Mepe Street\n\n🏢 #Land for #Sale\n🏠 475 Sq.m |\n💰87.000$\n#Mari"
    f = CI.sale_fields(land)
    assert (f["address"], f["district"], f["price"], f["area"], f["bedrooms"]) == ("Mirian Mepe Street", "DidiDighomi", 87000, 475.0, None)
    f = CI.sale_fields("#Shekvetili\n📍Village Vakijvari\n#Land for #Sale\n📐 160 000 Sq.m\n💰 12$ per Sq.m")
    assert (f["price"], f["area"]) == (None, 160000.0)
