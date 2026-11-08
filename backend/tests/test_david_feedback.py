"""David 2026-11-07: twin beats a closer flat in the channel; agent submit needs full params and no existing object."""
from app.models import Property
from app.services import properties
from app.services.errors import DomainError
from tests.conftest import fill_required, make_client_listing, make_listing, make_user


def test_channel_twin_ranks_over_nearer_flat(db):
    ad = make_listing(db, "lv1", source="livo.ge", bedrooms=2, area=120, floor=11, lat=41.70614, lng=44.77521)
    make_client_listing(db, "tg1", bedrooms=2, area=110, floor=5, lat=41.70639, lng=44.77573)      # 51 m, 5th floor
    make_client_listing(db, "tg2", bedrooms=2, area=115, floor=11, lat=41.70929, lng=44.76366)     # 1 km, mis-geocoded
    make_client_listing(db, "tg3", bedrooms=2, area=115, floor=11, lat=41.75, lng=44.80)           # 5 km: too far
    assert [x["id"] for x in properties.similar_in_channel(db, [ad])["lv1"]] == ["tg2", "tg1"]


def _ready(db, agent, lid, **kw):
    make_listing(db, lid, **kw)
    p = properties.take(db, lid, agent)
    properties.call_result(db, p.id, agent, True)
    properties.owner_or_agent(db, p.id, agent, True, "Гиорги", "+995 555 12 34 56")
    p.status = "media_uploaded"
    properties.upload_media(db, p.id, [("a.jpg", b"\xff\xd8jpeg")], "kitchen", agent)
    return fill_required(db, p.id)


def _code(db, pid, agent):
    try:
        properties.submit_for_review(db, pid, agent)
    except DomainError as e:
        db.rollback()
        return e.code, e.message
    return "ok", ""


def test_submit_requires_params_and_no_duplicate(db, sent):
    ag = make_user(db, "agent")
    p = _ready(db, ag, "ss1", floor=None, area=None)
    code, msg = _code(db, p.id, ag)
    assert code == "missing_fields" and "площадь" in msg and "этаж" in msg
    p.floor, p.area, p.cadastral_code = 3, 60, "01.14.12.003.001"
    db.commit()
    assert _code(db, p.id, ag)[0] == "ok"

    q = _ready(db, ag, "ss2", address="другой адрес 5")
    q.cadastral_code = "01.14.12.003.001"
    db.commit()
    assert _code(db, q.id, ag) == ("object_exists", f"Этот объект уже есть в базе: #{p.id} {p.address}")



def test_lead_archive_hides_from_board_and_finds_by_search(db, client):
    from app.models import Lead
    from app.services.common import now_text
    from tests.conftest import auth, make_client
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    c = make_client(db, "Тамара")
    lead = Lead(client_id=c.id, listing_id=make_listing(db, "ss9").id, status="time_chosen", agent_id=ag.id,
                created_at=now_text(), updated_at=now_text())
    db.add(lead)
    db.commit()
    r = client.post(f"/api/leads/{lead.id}/archive", json={"archived": True}, headers=auth(ag))
    assert r.status_code == 409  # only closed leads (rented / refused / thinking)
    lead.status = "refused"
    db.commit()
    assert client.post(f"/api/leads/{lead.id}/archive", json={}, headers=auth(ag)).json()["archived_at"]
    assert not client.get("/api/leads/board", headers=auth(op)).json()["refused"]
    assert client.get("/api/leads", headers=auth(op)).json()["total"] == 0
    found = client.get("/api/leads?archived=true&q=Тамар", headers=auth(op)).json()
    assert [x["id"] for x in found["items"]] == [lead.id]
    assert not client.post(f"/api/leads/{lead.id}/archive", json={"archived": False}, headers=auth(op)).json()["archived_at"]
    assert [x["id"] for x in client.get("/api/leads/board", headers=auth(op)).json()["refused"]] == [lead.id]


def test_channel_post_is_not_similar_to_itself(db):
    post = make_client_listing(db, "tg7", bedrooms=2, area=100, floor=21, lat=41.75, lng=44.77)
    make_client_listing(db, "tg8", bedrooms=2, area=100, floor=21, lat=41.75, lng=44.77)
    assert properties.similar_in_channel(db, [post]) == {}


def test_channel_import_makes_published_objects_with_agent_and_owner(db):
    from app.importers import channel_objects
    from app.models import ChannelPost, Owner, Property
    ag = make_user(db, "agent")
    db.add_all([ChannelPost(message_id="548267", agent_id=ag.id, is_exclusive=True),
                Owner(name="Нана", phone="+995 599 11 22 33", phone_norm="599112233")])
    db.commit()
    make_listing(db, "tg548267", source="agency", text="Сдаю, 599 11 22 33",
                 urls='["https://t.me/rent_tbilisi_ge/548267"]')
    make_listing(db, "ss77")                                                   # an outside ad: stays an ad
    assert channel_objects.run(db) == {"created": 1, "skipped": 0, "with_agent": 1, "with_owner": 1}
    p = db.query(Property).one()
    assert (p.status, p.post_message_id, p.agent_id, p.is_exclusive) == ("published", "548267", ag.id, True)
    assert p.owner_id and p.verified_at and p.post_url == "https://t.me/rent_tbilisi_ge/548267"
    assert channel_objects.run(db)["skipped"] == 1                            # idempotent
    assert db.query(ChannelPost).one().property_id == p.id


def test_deal_keeps_owner_and_cadastral_on_the_object_and_contract_gets_them(db, client):
    from app.models import Property
    from app.services import leads
    from tests.conftest import auth, make_client
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    lead = leads.create_from_like(db, make_client(db).id, make_client_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    lead.status = "time_chosen"
    db.commit()
    r = client.post("/api/deals", headers=auth(ag), json={
        "lead_id": lead.id, "property_id": lead.property_id, "price": 850, "owner_name": "Гиорги",
        "owner_phone": "+995 555 12 34 56", "cadastral": "01.14.12.003.045", "owner_birth": "1980-05-01",
        "rent_from": "2026-12-01", "rent_to": "2027-12-01"})
    assert r.status_code == 200, r.text
    p = db.get(Property, lead.property_id)
    db.refresh(p)
    assert p.owner_id and p.cadastral_code == "01.14.12.003.045"                # kept for the next deal
    c = client.get(f"/api/leads/{lead.id}/contract", headers=auth(ag)).json()
    assert c["data"]["owner_full_name"] == "Гиорги" and c["data"]["cadastral_code"] == "01.14.12.003.045"
    assert c["data"]["owner_birth_day"] == "1980-05-01" and c["data"]["start_rent"] == "2026-12-01"


def test_owner_scopes_media_room_and_picked_owner(db, client, sent):
    """David 2026-11-08: owners list = base (shot and sent) / unlinked (old Owner_Base); rooms tagged per photo after
    a batch upload; a deal picks an owner from the list instead of retyping him."""
    from app.models import Owner
    from tests.conftest import auth
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    old = Owner(name="Старый", phone="+995 577 00 00 01", phone_norm="577000001", notes="import:Owner_Base")
    db.add(old)
    db.commit()
    p = _ready(db, ag, "ss77")  # owner from the call: on an object in work, not in the base yet
    names = lambda scope: {o["name"] for o in client.get("/api/owners", params={"scope": scope}, headers=auth(op)).json()["items"]}
    assert names("unlinked") == {"Старый"} and names("base") == set()
    assert properties.missing_fields(p) == []
    assert _code(db, p.id, ag)[0] == "ok"
    assert names("base") == {"Гиорги"}

    mid = properties.media(db, p.id)[0].id
    r = client.patch(f"/api/properties/{p.id}/media/{mid}", json={"room": "Спальня 1"}, headers=auth(op)).json()
    assert r == {"id": mid, "room": "Спальня 1"}

    q = _ready(db, ag, "ss78", address="ул. Другая 3")
    q.owner_id = None
    db.commit()
    assert "собственник" in properties.missing_fields(q)
    r = client.patch(f"/api/properties/{q.id}", json={"owner_id": old.id}, headers=auth(ag)).json()
    db.expire_all()
    assert db.get(Property, q.id).owner_id == old.id, r


def test_take_prefills_the_generator_from_the_ad(db):
    """David 2026-11-08: what the ad knows is already picked in the agent's generator; the agent's own picks stay."""
    import json
    ag = make_user(db, "agent")
    make_listing(db, "mh77", lat=41.7036, lng=44.7898, details=json.dumps({
        "floors": 12, "property": "apartment", "building": "new", "heating": "central", "pets": "not_allowed",
        "yes": ["elevator", "wifi"], "terms": [12], "tenants": "1-3"}))
    p = properties.take(db, "mh77", ag)
    pf = p.post_fields
    assert p.total_floors == 12 and pf["metro"] == "Rustaveli" and pf["building"] == "NewBuilding"
    assert pf["amenities"] == ["WiFi", "Elevator"] and pf["periods"] == ["12Month"] and pf["tenants"] == 3
    p.post_fields = {**pf, "pets": "Allowed", "metro": None}
    from app.models import Listing
    assert properties.prefill(p, db.get(Listing, "mh77"))
    assert p.post_fields["pets"] == "Allowed" and p.post_fields["metro"] == "Rustaveli"
    assert properties.nearest_metro(41.75, 44.87) is None  # 6 km from any station: no metro rather than a wrong one
