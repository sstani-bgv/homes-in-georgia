"""David 11-23: «Где ещё выложен» for any object — the ads its duplicate search finds, «Попросить снять» (the agent's
task), «Проверено: снято» (operator), «Не дубль»; the agent hears of new copies once."""
import json

from app.services import properties, similar
from tests.conftest import auth, make_listing, make_user
from tests.test_similar import H, _object


def test_copies_of_any_object_ask_check_and_not_a_dup(db, client, sent):
    vika, op = make_user(db, "agent", "Вика"), make_user(db, "operator", "Нино")
    pid = _object(db, client, vika)
    db.get(properties.Property, pid).status = "shoot_scheduled"  # in work: searched daily
    db.commit()
    make_listing(db, "mh7001", source="myhome", status="copy", urls=json.dumps(["https://myhome.ge/7001"]),
                 address="Kazbegi 30", photo_hashes=json.dumps([H[0] ^ 1, H[1], H[2]]), text="Сдаю")
    make_listing(db, "ss7002", urls=json.dumps(["https://ss.ge/7002"]), address="Kazbegi 30",
                 photo_hashes=json.dumps([H[0], H[1]]), text="Сдаю, собственник")
    assert not db.get(properties.Property, pid).is_exclusive
    similar.scan_exclusive(db, pid, "daily")
    card = client.get(f"/api/properties/{pid}", headers=auth(vika)).json()
    got = {c["listing_id"]: c for c in card["copies"]}
    # its own source ad (linked) first, then the found copies
    assert card["copies"][0]["listing_id"] == "ss5001" and card["copies"][0]["level"] == "linked"
    assert set(got) == {"ss5001", "mh7001", "ss7002"} and got["mh7001"]["url"] == "https://myhome.ge/7001"
    assert got["ss7002"]["who"] == "собственник" and got["mh7001"]["who"] is None
    assert all(c["level"] in ("confirmed", "probable") and c["pct"] for c in card["copies"][1:])
    assert not any(c["blocking"] for c in got.values())  # not an exclusive: nothing is blocked
    (prop, new), = sent.of("object_copies")
    assert prop.id == pid and {c["listing_id"] for c in new} == {"mh7001", "ss7002"}
    assert not sent.of("exclusive_duplicates")  # not an exclusive

    similar.scan_exclusive(db, pid, "daily")  # nothing new: no second message
    assert len(sent.of("object_copies")) == 1

    r = client.post(f"/api/properties/{pid}/listings/mh7001/takedown", headers=auth(vika))
    assert r.status_code == 200, r.text
    c = next(c for c in r.json()["copies"] if c["listing_id"] == "mh7001")
    assert c["asked"]["by"] == "Вика"
    assert client.post(f"/api/properties/{pid}/listings/mh7001/takedown", headers=auth(make_user(db, "agent"))).status_code == 403
    assert client.post(f"/api/properties/{pid}/listings/mh7001/removed", headers=auth(vika)).status_code == 403
    r = client.post(f"/api/properties/{pid}/listings/mh7001/removed", headers=auth(op))
    assert r.status_code == 200, r.text
    assert [c["listing_id"] for c in r.json()["copies"]] == ["ss5001", "ss7002"]

    client.post("/api/similar/decide", json={"a": f"p:{pid}", "b": "l:ss7002", "same": False}, headers=auth(op))
    assert [c["listing_id"] for c in client.get(f"/api/properties/{pid}", headers=auth(vika)).json()["copies"]] == ["ss5001"]


def test_a_scan_before_the_copies_list_is_the_baseline(db, client, sent):
    vika = make_user(db, "agent")
    pid = _object(db, client, vika)
    db.get(properties.Property, pid).status = "shoot_scheduled"  # in work: searched daily
    db.commit()
    from app.services.common import log_event
    log_event(db, "property", pid, "duplicate_scan", None, n=0, strong=[], trigger="daily", sig="x")
    db.commit()
    make_listing(db, "mh7001", source="myhome", status="copy", urls=json.dumps(["https://myhome.ge/7001"]),
                 address="Kazbegi 30", photo_hashes=json.dumps([H[0], H[1], H[2]]), text="Сдаю")
    similar.scan_exclusive(db, pid, "daily")
    assert not sent.of("object_copies") and properties.dup_scan(db, pid)["copies"]
