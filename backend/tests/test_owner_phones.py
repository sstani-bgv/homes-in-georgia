"""An owner holds any number of phones (David + Irakli 11-18, migration 0032): the ad's number and the one the person
gave on the call («сестра»), a foreign one; search, duplicates, matching and visibility use every one of them."""
from app.models import Owner, Property
from app.services import matching, properties
from app.services.dedup import e164
from app.telegram.notify import phones_text  # bound before conftest records every notify.* call
from tests.conftest import auth, make_listing, make_user

AD = "+995 599 11 22 33"
SISTER = "577 44 55 66"
FOREIGN = "+7 916 123 45 67"


def _take(client, agent, lid, address="ул. Палиашвили 10"):
    pid = client.post("/api/properties", json={"listing_id": lid, "force": True}, headers=auth(agent)).json()["id"]
    client.patch(f"/api/properties/{pid}", json={"address": address}, headers=auth(agent))
    return pid


def test_e164():
    assert e164("599 11 22 33") == e164("0599112233") == e164("995599112233") == e164("+995 0599 11 22 33") == "+995599112233"
    assert e164("+7 916 123-45-67") == e164("0079161234567") == "+79161234567"  # a foreign code is kept, no +995
    assert e164("+995 +44 20 7946 0958") == "+442079460958"  # a «+» typed after the prefilled «+995 » wins
    assert e164("79161234567") == "+79161234567"
    assert e164("звонить после 6") == "звонить после 6" and e164("123") == "123" and e164("") is None


def test_call_step_saves_every_number_and_hides_them_like_the_main(db, client, sent):
    david, other, nino = make_user(db, "agent", "Давид"), make_user(db, "agent", "Ника"), make_user(db, "operator", "Нино")
    make_listing(db, "ss501")
    pid = _take(client, david, "ss501")
    r = client.post(f"/api/properties/{pid}/owner", headers=auth(david), json={
        "is_owner": True, "owner_name": "Тамара",
        "owner_phones": [{"phone": AD}, {"phone": SISTER, "note": "сестра"}, {"phone": FOREIGN, "note": "WhatsApp"},
                         {"phone": "+995 "}, {"phone": "599112233"}]}).json()  # an empty row and a repeat are dropped
    assert r["status"] == "owner_confirmed"
    assert r["owner"]["phone"] == "+995599112233"  # the main = the first, kept in owners.phone for old readers
    assert [(x["phone"], x["note"]) for x in r["owner"]["phones"]] == [
        ("+995599112233", None), ("+995577445566", "сестра"), ("+79161234567", "WhatsApp")]
    # the same rule as the main phone: staff and the object's agent; another agent sees none of them
    assert client.get(f"/api/properties/{pid}", headers=auth(nino)).json()["owner"]["phones"][2]["phone"] == "+79161234567"
    assert len(client.get("/api/owners", params={"q": "Тамара"}, headers=auth(david)).json()["items"][0]["phones"]) == 3
    seen = client.get("/api/owners", params={"q": "Тамара"}, headers=auth(other)).json()["items"][0]
    assert seen["phone"] is None and seen["phones"] is None
    assert phones_text(r["owner"]["phones"]) == "+995599112233, +995577445566 (сестра), +79161234567 (WhatsApp)"


def test_add_and_remove_numbers_from_the_card(db, client, sent):
    david = make_user(db, "agent", "Давид")
    make_listing(db, "ss502")
    pid = _take(client, david, "ss502")
    client.post(f"/api/properties/{pid}/owner", json={"is_owner": True, "owner_name": "Тамара", "owner_phone": AD},
                headers=auth(david))
    r = client.patch(f"/api/properties/{pid}", headers=auth(david), json={
        "owner_name": "Тамара", "owner_phones": [{"phone": AD}, {"phone": SISTER, "note": "сестра"}]}).json()
    assert [x["phone"] for x in r["owner"]["phones"]] == ["+995599112233", "+995577445566"]
    # the main number alone (the «Править» form): his other numbers stay
    r = client.patch(f"/api/properties/{pid}", json={"owner_phone": "599 00 00 01"}, headers=auth(david)).json()
    assert [x["phone"] for x in r["owner"]["phones"]] == ["+995599000001", "+995577445566"]
    # the sister's number removed, a foreign one made the main
    r = client.patch(f"/api/properties/{pid}", json={"owner_phones": [{"phone": FOREIGN}, {"phone": "599 00 00 01"}]},
                     headers=auth(david)).json()
    assert r["owner"]["phone"] == "+79161234567" and [x["phone"] for x in r["owner"]["phones"]] == ["+79161234567", "+995599000001"]
    assert db.query(Owner).count() == 1
    notes = [e["data"].get("note") for e in client.get(f"/api/properties/{pid}/history", headers=auth(david)).json()
             if e["action"] == "owner_contact"]
    assert any("номера:" in (n or "") for n in notes)


def test_second_number_finds_the_same_owner(db, client, sent):
    """Duplicate-owner search: the sister's number typed on another object, in the owners list or in the dedup check."""
    david, nika, nino = make_user(db, "agent", "Давид"), make_user(db, "agent", "Ника"), make_user(db, "operator", "Нино")
    make_listing(db, "ss503")
    make_listing(db, "ss504")
    p1 = _take(client, david, "ss503")
    client.post(f"/api/properties/{p1}/owner", headers=auth(david), json={
        "is_owner": True, "owner_name": "Тамара", "owner_phones": [{"phone": AD}, {"phone": SISTER, "note": "сестра"}]})
    owner_id = db.get(Property, p1).owner_id
    # staff: a fragment of the second number; an agent: the full second number only
    assert [o["id"] for o in client.get("/api/owners", params={"q": "4455"}, headers=auth(nino)).json()["items"]] == [owner_id]
    assert [o["id"] for o in client.get("/api/owners", params={"q": SISTER}, headers=auth(nika)).json()["items"]] == [owner_id]
    assert client.get("/api/owners", params={"q": "4455"}, headers=auth(nika)).json()["items"] == []
    # the object search by the owner's second number
    assert p1 in [x["id"] for x in client.get("/api/properties", params={"q": "577445566"}, headers=auth(nino)).json()["items"]]
    # the dedup check: the second number + the address = danger
    res = properties.check_duplicate(db, phone="+995 577 44 55 66", address="ул. Палиашвили 10")
    assert res["level"] == "danger" and res["results"][0]["owner_id"] == owner_id and res["results"][0]["phone_match"]
    # another object, the call reaches the sister: the same owner, not a twin; the new number joins his list
    p2 = _take(client, david, "ss504", "ул. Абашидзе 3")
    r = client.post(f"/api/properties/{p2}/owner", headers=auth(david), json={
        "is_owner": True, "owner_name": "Сестра Тамары", "owner_phones": [{"phone": SISTER}, {"phone": FOREIGN}]}).json()
    assert r["owner"]["id"] == owner_id and r["dup_check"] == "exists" and db.query(Owner).count() == 1
    assert [x["phone"] for x in r["owner"]["phones"]] == ["+995599112233", "+995577445566", "+79161234567"]


def test_matching_by_the_second_number(db, client, sent):
    david = make_user(db, "agent", "Давид")
    make_listing(db, "ss505")
    pid = _take(client, david, "ss505")
    client.post(f"/api/properties/{pid}/owner", headers=auth(david), json={
        "is_owner": True, "owner_name": "Тамара", "owner_phones": [{"phone": AD}, {"phone": SISTER, "note": "сестра"}]})
    hit = matching.same_as(db, "Палиашвили 10", None, "+995 577 44 55 66")
    assert [x["property_id"] for x in hit] == [pid] and "тот же телефон и адрес" in hit[0]["reasons"]
    assert matching.same_as(db, "Палиашвили 10", None, "+995 555 00 00 00") == []


def test_after_verification_the_own_agent_adds_a_number_at_once(db, client, sent):
    from tests.test_change_requests import _verified
    georgiy, nino, pid = _verified(db, client, sent, "ss506")
    client.post(f"/api/properties/{pid}/publish", headers=auth(nino))
    body = {"owner_name": "Нана", "owner_phone": AD, "owner_phones": [{"phone": AD}, {"phone": SISTER, "note": "сестра"}]}
    r = client.patch(f"/api/properties/{pid}", json=body, headers=auth(georgiy)).json()
    assert r["change_request"] is None and [x["phone"] for x in r["owner"]["phones"]] == ["+995599112233", "+995577445566"]
    assert any("Агент изменил" in a[1] and "owner_phones" in a[5] for a in sent.of("prop_event"))


def test_ad_window_and_dedup_check_show_every_number_to_who_may_see_them(db, client, sent):
    david, nika, nino = make_user(db, "agent", "Давид"), make_user(db, "agent", "Ника"), make_user(db, "operator", "Нино")
    make_listing(db, "ss507")
    pid = _take(client, david, "ss507")
    client.post(f"/api/properties/{pid}/owner", headers=auth(david), json={
        "is_owner": True, "owner_name": "Тамара", "owner_phones": [{"phone": AD}, {"phone": FOREIGN, "note": "сестра"}]})
    for who in (david, nino):  # the object's agent and staff: the ad window lists every number with its note
        owner = client.get("/api/listings/ss507", headers=auth(who)).json()["owner"]
        assert [(x["phone"], x["note"]) for x in owner["phones"]] == [("+995599112233", None), ("+79161234567", "сестра")]
    assert client.get("/api/listings/ss507", headers=auth(nika)).json().get("owner") is None
    # «Проверить дубль»: staff see the matched owner's numbers, another agent none
    body = {"phone": "+7 916 123 45 67"}
    assert len(client.post("/api/properties/check-duplicate", json=body, headers=auth(nino)).json()["results"][0]["phones"]) == 2
    hidden = client.post("/api/properties/check-duplicate", json=body, headers=auth(nika)).json()["results"][0]
    assert hidden["phone_match"] and hidden["phone"] is None and hidden["phones"] is None
