"""David 2026-11-12: a new ad «Old Tbilisi, Vera, Bakradze 20» must point at the archive objects of that address."""
from app.models import Property
from app.services import matching
from app.services.dedup import house_relation
from tests.conftest import auth, make_listing, make_user

AD = "Old Tbilisi, Vera, Bakradze 20"


def test_house_relation_across_spellings():
    assert house_relation(AD, "20 Dimitri Bakradze St") == "тот же дом"
    assert house_relation(AD, "ул. Бакрадзе, 20") == "тот же дом"
    assert house_relation(AD, "ბაქრაძის ქ. 20") == "тот же дом"  # Georgian genitive
    assert house_relation(AD, "20A Dimitri Bakradze St") == "соседний корпус 20A"
    assert house_relation(AD, "Бакрадзе 20а") == "соседний корпус 20A"
    assert house_relation(AD, "16 Dimitri Bakradze St") == "рядом по улице, дом 16"
    assert house_relation(AD, "30 Dimitri Bakradze St") is None              # too far down the street
    assert house_relation(AD, "20 Dimitri Uznadze St") is None               # shared first name is not the street
    assert house_relation("Vera, Barnovi 20", "Vake, Bakradze 20") is None   # districts never make the street
    assert house_relation("Chavchavadze 1", "Chavchavadze 10") is None
    assert house_relation("ул. Костава 5", "კოსტავას ქ. 5") == "тот же дом"
    assert house_relation("1st floor", "Bakradze 1") is None
    # «Block» is not a street (David 11-12: Kalandadze 15 matched Vazha-Pshavela VI Block 15)
    assert house_relation("Krtsanisi, Bezhan Kalandadze St, 15 (Block 6)", "15 Vazha-Pshavela VI Block") is None
    assert house_relation("Krtsanisi, Bezhan Kalandadze St, 15 (Block 6)", "Бежан Каландадзе 15") == "тот же дом"
    assert house_relation("Vazha-Pshavela VII Block, 16", "16 Vazha-Pshavela I Block") == "тот же дом"


def test_new_ad_lists_archive_objects_at_its_address(db, client):
    agent = make_user(db, "agent", "Давид")
    legacy = {a: Property(address=a, status="archived", archive_reason="not_actual", bedrooms=1, price=650)
              for a in ("20 Dimitri Bakradze St", "20A Dimitri Bakradze St", "16 Dimitri Bakradze St",
                        "20 Dimitri Uznadze St")}  # legacy archive: no source ad, no pin
    db.add_all(legacy.values())
    db.commit()
    make_listing(db, "ss900", source="ss.ge", address=AD, bedrooms=1, price_usd=700, lat=None, lng=None)
    matching.process(db, ["ss900"])  # at parse time, not on every feed render

    item = next(i for i in client.get("/api/listings/new", headers=auth(agent)).json()["items"] if i["id"] == "ss900")
    got = [(s["id"], s["reasons"], s["status"], s["url"]) for s in item["similar"]]
    ids = {a: p.id for a, p in legacy.items()}
    assert got == [(ids["20 Dimitri Bakradze St"], ["тот же дом"], "archived", f"/properties/{ids['20 Dimitri Bakradze St']}"),
                   (ids["20A Dimitri Bakradze St"], ["соседний корпус 20A"], "archived", f"/properties/{ids['20A Dimitri Bakradze St']}"),
                   (ids["16 Dimitri Bakradze St"], ["рядом по улице, дом 16"], "archived", f"/properties/{ids['16 Dimitri Bakradze St']}")]
    assert [s["id"] for s in client.get("/api/listings/ss900", headers=auth(agent)).json()["similar"]] == [x[0] for x in got]
