from app.importers.legacy_objects import Registry, address_of, area_of, bedrooms_of, floor_of, phones_of, price_of

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
