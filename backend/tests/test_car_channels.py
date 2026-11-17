"""«Авто»: the car post generator (services/car_post.py) and the import from the car channels (services/car_import.py),
on real posts of CarsSaleInGeorgia / CarsRentInGeorgia."""
from types import SimpleNamespace

import httpx

from app.models import Car
from app.services import car_import as I
from app.services import car_post as CP
from tests.conftest import auth, make_user

# real posts (17.11.2026), as the channels' web preview gives the text
SALE = """🚘 Ford Escape Titanium | 2013💰$5,000❗️#Exclusive
0% Commission

⛽️ Petrol · I4 2.0L EcoBoost Turbocharged · 240 HP ⚙️ Transmission: #Automatic · #4WD 
🛣 255,000km · SUV 🎨 #Black

✨ OPTIONS
#AC · #ClimateControl · #MultifunctionSteering · #EcoMode · #MultimediaSystem · #Display · #Bluetooth

🔧 CONDITION: #Good

The engine runs well, performance is confident, the transmission shifts smoothly without jerks, under load uphill it also shifts without any complaints, the suspension works without extraneous noises and handles uneven surfaces, with the current tire profile it is very comfortable on cobblestones and off-road, no serious accidents in its history

Cosmetic imperfections:
• It has some cosmetic imperfections on the body and wheels, but considering its age, both the interior and exterior look remarkably fresh

🛡 SAFETY
#ABS

🔎 VIN: 1FMCU9J92DUB76691

🛠 AFTER-SALE SERVICES
We can assist with maintenance, repairs, detailing, insurance, and delivery after purchase

📲 +995 599 20 67 16
👤 @David_Tibelashvili

#Ford #SUV #2010s 
#Petrol #I4 #From200000 #PriceUnder5000 #Price5000to10000
#CarsSaleInGeorgia #CarsRentInGeorgia"""
RENT = """#Jeep Compass 🚙 #SUV📍 8 Dimitri Arakishvili Street

🛣️ #DailyRent #2022 | #Automatic ⛽ #Petrol 

🪫 Fuel Use: 8 L / 100 km 🧊 #AC #ClimateControl #HeatedSeats 📲 #Bluetooth #Multimedia #USB 🛞 #4WD #CruiseControl #EcoMode 🚫 Smoking ❌

🐶 Pets: #Allowed  
👨‍👩‍👦 Capacity: 5 seats | Trunk: 350 L  

💰 60$/ day  
Basic Insurance: 10$
Full Insurance: 15$

✔️Add Services
👶#ChildSeatAvailable: 3$/day
💺#BoosterSeat: 3$/day
🔗#SnowChains: 3$/day
❄️#WinterTires: 3$/day
👥#SecondDriver: 3$/day

❗️0% Commission
❗️UnlimitedMileage

📲 +995 599 20 67 16 |@David_Tibelashvili  
📍 FREE Pick-up & drop-off: Tbilisi / Airport / City Center 

📍CARS ON MAP📍"""


def car(**kw):
    return SimpleNamespace(**{**{c.name: None for c in Car.__table__.columns}, "currency": "USD", "is_exclusive": False,
                              "post_fields": {}, **kw})


def norm(s):
    return [x.rstrip() for x in s.replace("\ufe0f", "").splitlines()]


def test_generator_rebuilds_the_channel_posts_exactly():
    assert norm(CP.build(car(**I.parse(SALE, "sale")))) == norm(SALE)
    assert norm(CP.build(car(**I.parse(RENT, "rent")))) == norm(RENT)


def test_sale_post_is_read_into_fields():
    d = I.parse(SALE, "sale")
    assert (d["title"], d["year"], d["price"], d["mileage"], d["body"], d["fuel"], d["gearbox"]) == (
        "Ford Escape Titanium", 2013, 5000, 255000, "suv", "petrol", "auto")
    f = d["post_fields"]
    assert f["engine"] == "I4 2.0L EcoBoost Turbocharged" and f["hp"] == 240 and f["drive"] == "4WD" and f["color"] == "Black"
    assert f["vin"] == "1FMCU9J92DUB76691" and f["condition"] == "Good" and f["safety"] == ["ABS"] and "Bluetooth" in f["options"]
    assert d["description"].startswith("The engine runs well") and f["cosmetic"][0].startswith("It has some cosmetic")
    assert d["is_exclusive"] is True


def test_rent_post_is_read_into_fields():
    d = I.parse(RENT, "rent")
    assert (d["title"], d["year"], d["price"], d["period"], d["pickup"], d["seats"]) == (
        "Jeep Compass", 2022, 60, "day", "8 Dimitri Arakishvili Street", 5)
    f = d["post_fields"]
    assert f["drive"] == "4WD" and f["trunk"] == 350 and f["insurance_full"] == 15 and f["pets"] == "allowed"
    assert [e["key"] for e in f["extras"]] == ["child_seat", "booster", "chains", "winter_tires", "second_driver"]
    assert I.parse("📍CARS ON MAP📍", "rent") is None and I.parse("🚘 CARS RENT IN GEORGIA 🇬🇪", "sale") is None
    # typos of the channel are read and written right
    d = I.parse(RENT.replace("#Jeep Compass 🚙 #SUV", "#Mersedes C-Class 300🚙 #Businessclass"), "rent")
    assert d["title"] == "Mercedes-Benz C-Class 300" and d["post_fields"]["car_class"] == "BusinessClass"
    assert CP.build(car(**d)).startswith("#Mercedes C-Class 300 🚙 #BusinessClass📍 8 Dimitri")


def test_sale_hashtags():
    post = lambda **kw: CP.build(car(deal="sale", title="Mercedes-Benz CLA 250", **kw)).splitlines()  # noqa: E731
    assert post(year=2013, price=30000, mileage=76538, body="coupe", fuel="petrol",
                post_fields={"engine": "V8 6.2L"})[-3:] == ["#Mercedes #Coupe #2010s ", "#Petrol #V8 #From50000to100000 #Price30000to50000",
                                                         "#CarsSaleInGeorgia #CarsRentInGeorgia"]
    assert "#PriceUnder5000 #Price5000to10000" in post(price=5000)[-2]  # as the channel tags $5,000
    assert post(price=150000, mileage=10)[-2] == "#Under50000 #Price100000plus"
    assert post(price=8300)[0] == "🚘 Mercedes-Benz CLA 250💰$8,300" and "🛠 AFTER-SALE SERVICES" in post()
    assert "🛠 AFTER-SALE SERVICES" not in post(post_fields={"after_sale": False})


def test_rent_post_terms():
    text = CP.build(car(deal="rent", title="Kia Rio", price=40, period="month", deposit=200, is_exclusive=True,
                        post_fields={"smoking": True, "unlimited_mileage": False, "delivery": "-",
                                     "extras": [{"key": "sim", "price": 0}]}))
    assert "💰 40$/ month" in text and "Deposit: 200$" in text and "🚬 Smoking ✅" in text and "📶#SIMCardIncluded: Free" in text
    assert "❗️#Exclusive" in text and "UnlimitedMileage" not in text and "Pick-up" not in text and "#MonthlyRent" in text


# ---------------------------------------------------------------- the import

def page(*posts):
    """A t.me/s page: (id, text, n photos, extra message ids of the album)."""
    out = ""
    for pid, text, n in posts:
        ph = "".join(f'<a class="tgme_widget_message_photo_wrap x" href="https://t.me/ch/{pid + i}" '
                     f"style=\"width:1px;background-image:url('https://cdn.test/{pid}-{i}.jpg')\"></a>" for i in range(n))
        body = text.replace("\n", "<br/>")
        out += (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="ch/{pid}">'
                f'{ph}<div class="tgme_widget_message_text js-message_text" dir="auto">{body}</div>'
                f'<time datetime="2026-11-17T1{pid % 10}:00:00+00:00" class="time"></time></div></div>')
    return out


def client_for(pages: dict):
    """httpx client: t.me/s/<channel> -> the page given, photos -> bytes."""
    def handler(req):
        if req.url.host == "t.me":
            return httpx.Response(200, text=pages.get(req.url.path.rsplit("/", 1)[-1] + ("?" + req.url.query.decode() if req.url.query else ""), ""))
        return httpx.Response(200, content=b"\xff\xd8jpg")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_import_creates_moves_and_archives(db, monkeypatch):
    monkeypatch.setattr(I, "PAUSE", 0)
    monkeypatch.setattr(I, "_geocode", lambda o: None)
    sold = SALE.replace("❗️#Exclusive", "❗️#Exclusive #Sold").replace("Ford Escape Titanium", "Honda Civic")
    pages = {"CarsSaleInGeorgia": page((1, SALE, 3), (5, sold, 2), (9, "📍CARS ON MAP📍", 0)),
             "CarsRentInGeorgia": page((20, RENT, 2), (40, RENT.replace("8 Dimitri Arakishvili Street", "Airport"), 2))}
    with client_for(pages) as c:
        r = I.run(db, client=c)
    assert r["CarsSaleInGeorgia"]["created"] == 1 and r["CarsRentInGeorgia"]["created"] == 1  # a sold car is not imported
    sale = db.query(Car).filter_by(deal="sale").one()
    assert sale.status == "published" and len(sale.photos) == 3 and sale.source == "telegram"
    assert sale.post_url == "https://t.me/CarsSaleInGeorgia/1" and sale.post_fields["vin"] == "1FMCU9J92DUB76691"
    rent = db.query(Car).filter_by(deal="rent").one()  # the repost is the same car, on the newest post
    assert rent.tg_message_id == 40 and rent.pickup == "Airport"
    # a second run changes nothing; then the sale is sold and the rent post is deleted
    with client_for(pages) as c:
        assert I.run(db, client=c)["CarsSaleInGeorgia"] == dict.fromkeys(("created", "updated", "moved", "archived", "skipped"), 0) | {"skipped": 2}
    pages = {"CarsSaleInGeorgia": page((1, SALE + "\n#Sold", 3)), "CarsRentInGeorgia": page((20, "📍CARS ON MAP📍", 0))}
    with client_for(pages) as c:
        I.run(db, client=c)
    db.expire_all()
    assert db.get(Car, sale.id).status == "archived" and db.get(Car, rent.id).status == "archived"


def test_crm_car_published_to_its_channel_is_not_imported_again(db, client, monkeypatch, sent):
    from app.telegram import notify
    monkeypatch.setattr(I, "PAUSE", 0)
    op = make_user(db, "car_operator")
    h = auth(op)
    o = client.post("/api/offers/car", headers=h, json={"deal": "sale", "title": "Ford Escape Titanium", "year": 2013,
                                                       "price": 5000, "post_fields": {"vin": "x1", "transmission": "DCT", "bogus": 1}}).json()
    assert o["post_fields"] == {"vin": "x1", "transmission": "DCT"} and o["gearbox"] == "auto"
    client.post(f"/api/offers/car/{o['id']}/media", headers=h, files=[("files", ("a.jpg", b"\xff\xd8", "image/jpeg"))])
    monkeypatch.setattr(notify, "publish_post", lambda p: sent.append(("publish_post", (p,))) or (7, "https://t.me/CarsSaleInGeorgia/7"))
    r = client.post(f"/api/offers/car/{o["id"]}/publish", headers=h, json={"tg": True, "site": True})
    assert r.status_code == 200, r.text
    r = r.json()
    (post,), = sent.of("publish_post")
    assert post.chat == "@CarsSaleInGeorgia" and post.post_text.startswith("🚘 Ford Escape Titanium | 2013💰$5,000")
    assert r["tg_channel"] == "CarsSaleInGeorgia" and r["tg_message_id"] == 7
    with client_for({"CarsSaleInGeorgia": page((7, SALE, 1)), "CarsRentInGeorgia": ""}) as c:
        assert I.run(db, client=c)["CarsSaleInGeorgia"]["created"] == 0
    assert db.query(Car).count() == 1
    # the form's live preview: the post of what is typed, nothing saved
    t = client.post("/api/offers/car/preview", headers=h, json={"deal": "rent", "title": "Kia Rio", "price": 40}).json()["text"]
    assert t.startswith("#Kia Rio 🚙") and "💰 40$/ day" in t and db.query(Car).count() == 1
