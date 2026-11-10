from app.importers.legacy_objects import Registry, address_of, area_of, bedrooms_of, floor_of, phones_of, price_of
from app.importers import legacy_objects as L

POST = """#Saburtalo 🚇 #STUniversity
📍[3 Ana Politkovskaia St](https://maps.app.goo.gl/x)

🏢 #2Bed #Apartment for #Rent
🏠 63 Sq.m | 8/12 Floor
💰 900$ + Deposit 900$
📲 @David_Tibelashvili | +995 555 00 00 00 #ArturB"""


def test_post_template_fields():
    assert address_of(POST) == "3 Ana Politkovskaia St"
    assert (bedrooms_of(POST), area_of(POST), floor_of(POST), price_of(POST)) == (2, 63.0, 8, 900)
    assert bedrooms_of("For #Rent in #Tbilisi #Studio") == 0


def test_link_ids_are_not_phones():
    assert phones_of("Ссылка: https://t.me/c/2252468572/3/244821 | +995 555 12 34 56") == {"555123456"}


def test_weak_merge_never_joins_different_owners():
    r = Registry()
    r.phones = {"a": {"555111111"}, "b": {"555222222"}, "c": {"555111111"}}
    assert not r.union("a", "b", "same_text", weak=True)
    assert r.union("a", "c", "same_text", weak=True)
    assert r.union("a", "b", "owner_card")  # exact evidence (same post / owner card) still merges


def test_media_follows_its_post_into_the_merged_object(tmp_path):
    from app.importers.legacy_objects import media_by_object
    data = {"objects": [{"key": "ch:1", "units": ["ch:1", "team-chat:5"]}]}
    done = tmp_path / "done.jsonl"
    rows = [{"where": "team-chat/7", "object": "team-chat:5", "key": "team-chat/7.mov", "sha256": "v"},
            {"where": "team-chat/6", "object": "team-chat:5", "key": "team-chat/6.jpg", "sha256": "a"},
            {"where": "channel/9", "object": "ch:1", "key": "channel/9.jpg", "sha256": "b"},
            {"where": "channel/8", "object": "ch:1", "key": "channel/8.jpg", "sha256": "a"},  # same bytes: once
            {"where": "channel/3", "object": "ch:1", "error": "missing"}]
    done.write_text("".join(__import__("json").dumps(r) + "\n" for r in rows))
    keys = [d["key"] for d in media_by_object(data, done)["ch:1"]]
    assert keys == ["channel/9.jpg", "channel/8.jpg", "team-chat/7.mov"]  # newest photos first, videos last


def test_post_hashtags_fill_the_generator_fields():
    post = """#Chugureti 🚇 #LibertySquare 📍 3 Tsinamdzghvrishvili St
🏢 #Studio #Apartment for #Rent
✨ #OldBuilding | #New #White
🏠30Sq.m |3/9 Floor | #ElectricalHeating | #ShowerCabin
✅#Stove ✅#WiFi ✖️#Dishwasher
👬Tenants: 1-2|
🐕Pets: #Byagreemnt
🕐 #6month #12Months
💰 400$ + Deposit 400$"""
    assert L.total_floors_of(post) == 9 and L.total_floors_of("2 floor") is None
    assert L.post_fields_of(post) == {
        "metro": "LibertySquare", "prop_type": "Apartment", "deal": "Rent", "building": "OldBuilding", "design": "New",
        "heating": "ElectricalHeating", "amenities": ["WiFi", "Shower", "Stove"], "periods": ["6Month", "12Month"],
        "pets": "ByAgreement", "tenants": 2, "deposit": 400}


def test_prefill_takes_empty_fields_from_older_posts(db):
    from app.models import Property
    p = Property(source_type="legacy", source_external_id="ch:5", status="archived", price=700,
                 description="#Vake 🏢 #2Bed for #Rent | 80 Sq.m | 4 Floor", post_fields={"heating": "CentralHeating"},
                 ai_flags={"legacy": {"key": "ch:5"}})
    db.add(p)
    db.commit()
    older = "#Vake 📍 5 Abashidze St\n🏢 #2Bed #Apartment\n✨ #NewBuilding | 4/12 Floor | #GasHeating\n💰 650$"
    n = L.prefill(db, {"ch:5": [p.description, older]})
    db.refresh(p)
    assert (p.address, p.total_floors, p.floor, p.price) == ("5 Abashidze St", 12, 4, 700)  # the newer price stays
    assert p.post_fields["building"] == "NewBuilding" and p.post_fields["heating"] == "CentralHeating"
    assert n["total_floors"] == 1 and n["objects"] == 1


def test_channel_announcements_are_not_objects():
    assert not L.is_object_text("❗️#Rented Commercial space in 📍Vera has found a business 🤝💰🎉")
    assert not L.is_object_text("❗️#FlatRented Nice House in 📍Saburtalo found a tenant 🤝👏🎉")
    assert L.is_object_text("#Vake 📍 5 Abashidze St\n🏢 #2Bed #Apartment for #Rent | 80 Sq.m")
