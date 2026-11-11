"""Stage 6.5: the agent's hashtag in the profile, the owner belongs to an agent (block / staff override / transfer),
the owner's agent first in the lead hint; the full post fields of the old generator in post_text."""
from app.models import Event, Owner, Property
from app.services import inbound, leads, properties, settings, users
from app.telegram.notify import post_html  # the real one: the `sent` fixture stubs notify.*
from tests.conftest import auth, make_client, make_client_listing, make_listing, make_user


def test_agent_tag_suggestion_and_uniqueness(db, client):
    admin = make_user(db, "admin", "Админ")
    zaza = make_user(db, "agent", "Zaza Beridze")
    artur = make_user(db, "agent", "Артур Бахтадзе")
    make_user(db, "agent", "Artur Gelashvili")
    assert users.suggest_agent_tag(db, "Zaza Beridze", zaza.id) == "Zaza"
    assert users.suggest_agent_tag(db, "Артур Бахтадзе", artur.id) == "ArturB"  # another Artur → surname letter
    r = client.get("/api/users/agent-tag-suggestion", params={"name": "Георгий", "uid": 0}, headers=auth(admin)).json()
    assert r == {"tag": "Georgiy"}

    assert client.patch(f"/api/users/{zaza.id}", json={"agent_tag": "#Zaza"}, headers=auth(admin)).json()["agent_tag"] == "Zaza"
    r = client.patch(f"/api/users/{artur.id}", json={"agent_tag": "zaza"}, headers=auth(admin))  # case-insensitive
    assert r.status_code == 409 and "уже у агента Zaza Beridze" in r.json()["detail"]
    assert client.patch(f"/api/users/{artur.id}", json={"agent_tag": "Ар тур"}, headers=auth(admin)).json()["code"] == "bad_tag"
    assert users.suggest_agent_tag(db, "Zaza Kapanadze") == "ZazaK"  # #Zaza is taken
    assert client.patch(f"/api/users/{zaza.id}", json={"agent_tag": ""}, headers=auth(admin)).json()["agent_tag"] is None
    assert client.patch(f"/api/users/{zaza.id}", json={"agent_tag": "Zaza"}, headers=auth(artur)).status_code == 403


def test_resolve_agent_by_profile_tag_then_setting(db):
    zaza = make_user(db, "agent", "Zaza Beridze", agent_tag="Zaza")
    tamara = make_user(db, "agent", "Tamara Kapanadze")
    settings.set(db, "agent_tags", '{"#Tamar": "Tamara Kapanadze", "#Zaza": "Someone Else"}')
    assert inbound.resolve_agent(db, "#zaza") == ("Zaza Beridze", zaza)  # the profile wins over the setting
    assert inbound.resolve_agent(db, "#Tamar") == ("Tamara Kapanadze", tamara)


def _owner_confirmed(db, client, agent, lid, phone="+995 599 20 30 40"):
    pid = client.post("/api/properties", json={"listing_id": make_listing(db, lid).id}, headers=auth(agent)).json()["id"]
    return client.post(f"/api/properties/{pid}/owner", json={"is_owner": True, "owner_name": "Нана", "owner_phone": phone},
                       headers=auth(agent))


def test_owner_bound_to_agent_blocks_others_staff_override_and_transfer(db, client, sent):
    zaza = make_user(db, "agent", "Zaza Beridze", agent_tag="Zaza")
    levan = make_user(db, "agent", "Леван")
    nino = make_user(db, "operator", "Нино")
    first = _owner_confirmed(db, client, zaza, "ss1").json()
    owner = db.get(Owner, first["owner"]["id"])
    assert owner.agent_id == zaza.id and first["owner"]["agent"]["agent_tag"] == "Zaza"

    # another agent's call reaches the same owner → blocked, nothing re-assigned
    r = _owner_confirmed(db, client, levan, "ss2", phone="599 203 040")
    assert r.status_code == 409 and r.json() == {"detail": "Собственник закреплён за #Zaza", "code": "owner_taken"}
    db.expire_all()
    p2 = db.query(Property).filter_by(listing_id="ss2").one()
    assert p2.owner_id is None and db.get(Owner, owner.id).agent_id == zaza.id
    # staff may link him to Levan's object anyway (logged); the owner stays Zaza's, the card shows it
    r = client.post(f"/api/properties/{p2.id}/owner", json={"is_owner": True, "owner_phone": "599203040"}, headers=auth(nino)).json()
    assert r["owner"]["id"] == owner.id and r["owner"]["agent"]["id"] == zaza.id and r["agent"]["id"] == levan.id
    assert db.query(Event).filter_by(entity="property", entity_id=str(p2.id), action="owner_agent_override").count() == 1

    # transfer: staff only, explicit, with history on the owner and his objects; Zaza's live object moves
    body = {"agent_id": levan.id, "reason": "агент ушёл"}
    assert client.post(f"/api/owners/{owner.id}/transfer", json=body, headers=auth(levan)).status_code == 403
    assert client.post(f"/api/owners/{owner.id}/transfer", json={"agent_id": nino.id}, headers=auth(nino)).json()["code"] == "bad_agent"
    assert client.post(f"/api/owners/{owner.id}/transfer", json=body, headers=auth(nino)).json()["agent_id"] == levan.id
    db.expire_all()
    assert db.get(Property, first["id"]).agent_id == levan.id
    ev = [e for e in client.get(f"/api/properties/{first['id']}/history", headers=auth(nino)).json() if e["action"] == "owner_transferred"]
    assert ev[0]["data"]["comment"] == "Zaza Beridze → Леван · агент ушёл" and ev[0]["data"]["moved"]
    assert db.query(Event).filter_by(entity="owner", entity_id=str(owner.id), action="owner_transferred").count() == 1
    # Zaza is now the one blocked
    assert _owner_confirmed(db, client, zaza, "ss3", phone="599203040").json()["code"] == "owner_taken"


def test_refused_owner_is_released_for_the_next_agent(db, client):
    zaza, levan = make_user(db, "agent", "Zaza"), make_user(db, "agent", "Леван")
    pid = _owner_confirmed(db, client, zaza, "ss1").json()["id"]
    client.post(f"/api/properties/{pid}/refused", json={"comment": "нет"}, headers=auth(zaza))
    again = client.post("/api/properties", json={"listing_id": "ss1"}, headers=auth(levan)).json()
    assert again["id"] == pid and again["owner"]["agent"]["id"] == levan.id


def test_recommend_puts_owners_agent_first(db, client, sent):
    zaza = make_user(db, "agent", "Zaza")
    levan = make_user(db, "agent", "Леван", districts=["Ваке"])
    nino = make_user(db, "operator", "Нино")
    owner = Owner(name="Нана", phone="599000001", phone_norm="599000001", agent_id=zaza.id)
    db.add(owner)
    db.flush()
    p = Property(status="published", agent_id=levan.id, owner_id=owner.id, district="Ваке")
    db.add(p)
    db.commit()
    lead = leads.create_from_like(db, make_client(db).id, make_client_listing(db, "tg1").id)
    lead.property_id = p.id
    db.commit()
    rec = client.get(f"/api/leads/{lead.id}/recommend-agents", headers=auth(nino)).json()
    assert rec[0]["agent"]["id"] == zaza.id and rec[0]["reasons"][0] == "его собственник" and rec[0]["recommended"]
    assert rec[1]["agent"]["id"] == levan.id and "его объект" in rec[1]["reasons"]


FULL = {"metro": "Delisi", "building": "OldBuilding", "design": "Mixed", "heating": "CentralHeating",
        "amenities": ["#Stove", "Balcony", "Conditioner", "WiFi"], "pets": "ByAgreement", "periods": ["12Month", "6Month"],
        "tenants": 3, "deposit": 500, "headline": "No one lived"}


def test_post_fields_validated_and_full_rent_post(db, client):
    zaza = make_user(db, "agent", "Zaza Beridze", agent_tag="Zaza")
    nino = make_user(db, "operator", "Нино")
    listing = make_listing(db, "ss9", address="3 Ferdinand Tavadze St", district="Сабуртало", lat=41.72, lng=44.75, price_usd=500,
                           bedrooms=1, area=60.0, floor=10)
    pid = client.post("/api/properties", json={"listing_id": listing.id}, headers=auth(zaza)).json()["id"]
    assert client.patch(f"/api/properties/{pid}", json={"post_fields": {"pets": "Dogs"}}, headers=auth(zaza)).json()["code"] == "bad_post_fields"
    r = client.patch(f"/api/properties/{pid}", json={"total_floors": 12, "is_exclusive": True, "post_fields": FULL},
                     headers=auth(zaza)).json()
    assert r["post_fields"]["amenities"] == ["Balcony", "WiFi", "Stove", "Conditioner"]  # canonical order, no '#'
    assert r["post_fields"]["periods"] == ["6Month", "12Month"]
    text = client.get(f"/api/properties/{pid}/post", headers=auth(nino)).json()["text"]
    lines = text.split("\n")
    assert lines[:2] == ["#Saburtalo 🚇 #Delisi", "📍[3 Ferdinand Tavadze St](https://maps.google.com/?q=41.72,44.75)"]
    for line in ("⭐️ No one lived ⭐️", "❗️#Exclusive", "🏢 #1Bed #Apartment for #Rent", "✨ #OldBuilding | #Mixed",
                 "🏠 60 sq.m | 10/12 Floor | #CentralHeating", "✅ #Balcony  ✅ #WiFi  ✅ #Stove", "✅ #Conditioner",
                 "✖️ #Elevator  ✖️ #Oven  ✖️ #ParkingPlace", "👬Tenants: 1-3", "🐕Pets: #ByAgreement",
                 "🕐 #6Month #12Month", "💰 500$ + Deposit 500$", "0% Commission", "#Price300to500", "+995 599 20 67 16 #Zaza"):
        assert line in lines, line
    assert lines.index("👬Tenants: 1-3") < lines.index("💰 500$ + Deposit 500$")  # the generator's order
    html = post_html(text)
    assert '<a href="https://maps.google.com/?q=41.72,44.75">📍3 Ferdinand Tavadze St</a>' in html
    assert '📍<b><a href="https://hge.ge/map/">APARTMENTS ON MAP</a></b>📍' in html  # Irakli 11-11


def test_sale_post(db):
    zaza = make_user(db, "agent", "Zaza", agent_tag="Zaza")
    p = Property(status="review", agent_id=zaza.id, district="Ваке", address="Chavchavadze 10", bedrooms=3, area=120, floor=5,
                 total_floors=9, price=900, post_fields=properties.clean_post_fields(
                     {"deal": "Sale", "sale_price": 255000, "building": "NewBuilding", "condition": "WhiteFrame",
                      "amenities": ["Balcony", "Elevator"], "deposit": 0, "pets": "Allowed"}))
    db.add(p)
    db.commit()
    lines = properties.post_text(db, p).split("\n")
    for line in ("🏢 #3Bed #Apartment for #Sale", "✨ #NewBuilding | #WhiteFrame", "🏠 120 Sq.m | 5/9 Floor",
                 "✅ #Balcony ✅ #Elevator", "💰255.000$", "#Price240000to270000", "SALE IN TBILISI🇬🇪"):
        assert line in lines, line
    assert not any(x.startswith(("🐕", "👬", "✖️")) for x in lines)  # rent-only lines
