"""David 2026-11-07: twin beats a closer flat in the channel; agent submit needs full params and no existing object."""
from app.models import Property
from app.services import properties
from app.services.errors import DomainError
from tests.conftest import auth, fill_required, make_client_listing, make_listing, make_user


def _obj_listing(db, rows):
    return [db.get(Property, x["id"]).listing_id for x in rows]


def test_base_twin_ranks_over_nearer_flat_and_whole_house_is_shown(db):
    ad = make_listing(db, "lv1", source="livo.ge", bedrooms=2, area=120, floor=11, lat=41.70614, lng=44.77521)
    make_client_listing(db, "tg1", bedrooms=2, area=110, floor=5, lat=41.70639, lng=44.77573)      # 51 m, 5th floor
    make_client_listing(db, "tg2", bedrooms=2, area=115, floor=11, lat=41.70929, lng=44.76366)     # 1 km, mis-geocoded
    make_client_listing(db, "tg3", bedrooms=2, area=115, floor=11, lat=41.75, lng=44.80)           # 5 km: too far
    make_client_listing(db, "tg4", bedrooms=5, area=300, floor=2, lat=41.70614, lng=44.77521)      # same house, other flat
    assert _obj_listing(db, properties.similar_in_base(db, [ad])["lv1"]) == ["tg2", "tg4", "tg1"]
    db.get(Property, properties.similar_in_base(db, [ad])["lv1"][1]["id"]).status = "archived"
    db.commit()
    assert _obj_listing(db, properties.similar_in_base(db, [ad])["lv1"]) == ["tg2", "tg4", "tg1"]  # archived count too


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


def test_object_ad_is_not_similar_to_its_own_object(db):
    post = make_client_listing(db, "tg7", bedrooms=2, area=100, floor=21, lat=41.75, lng=44.77)
    assert properties.similar_in_base(db, [post]) == {}


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


def test_published_object_discount_pause_resume(db):
    staff, ag = make_user(db, "operator"), make_user(db, "agent")
    p = _ready(db, ag, "ss9")
    p.status, p.published_at, p.price = "published", p.updated_at, 900
    db.commit()
    properties.update(db, p.id, {"price": 800, "post_fields": {**(p.post_fields or {}), "discount": True}}, staff)
    assert "❗️#Discount" in properties.post_text(db, p) and p.price == 800
    properties.archive(db, p.id, staff, "paused")
    assert (p.status, p.archive_reason) == ("archived", "paused")
    properties.resume(db, p.id, ag)
    assert (p.status, p.archive_reason) == ("published", None)


def test_team_shows_only_active_agents(db):
    from app.services import analytics
    make_user(db, "agent", "Работает")
    gone = make_user(db, "agent", "Ушёл")
    gone.is_active = False
    db.commit()
    assert [a["name"] for a in analytics.agents(db)] == ["Работает"]


def test_rich_post_has_slideshow_params_tags_map_and_buttons(db):
    ag = make_user(db, "agent")
    make_listing(db, "ss77", lat=41.70, lng=44.76)
    p = properties.take(db, "ss77", ag)
    p.bedrooms, p.area, p.floor, p.total_floors, p.price, p.address = 3, 140, 4, 9, 1500, "ул. Чавчавадзе 33"
    p.post_fields = {"heating": "ACHeating", "periods": ["1Month", "12Month"], "amenities": ["WiFi", "SmartTV"], "discount": True}
    db.commit()
    h = properties.post_rich(db, p, ["tg://photo?id=m0", "tg://video?id=m1"])
    assert '<tg-slideshow><img src="tg://photo?id=m0"/><video src="tg://video?id=m1"/></tg-slideshow>' in h
    assert "<h2>$1 500 · 3 bedrooms · Vake</h2><p>Discount · 140 m² · 4/9 floor · ID: " in h  # the @tbilisi_rent title lines
    assert "<td>AC heating</td>" in h and "<td>1 month, 12 months</td>" in h and "WiFi · Smart TV" in h
    assert "<details><summary>Parameters and amenities</summary>" in h  # collapsed, as in Irakli's video
    assert "<summary>Tags</summary>" in h and "#Discount" in h and "+995 599 20 67 16" in h  # contacts inside Tags
    assert "<tg-map" not in h  # «On the map» opens the map: no second map in the post
    assert h.count("<tg-button ") == 2 and "RentInTbilisi_bot" not in h and "Contact" not in h
    # «On the map»: Irakli's hge.ge map filtered to this address; «I like it»: a plain bot button (callback), no link arrow
    assert '<tg-button type="url" url="https://hge.ge/map/?q=%D1%83%D0%BB+%D0%A7%D0%B0%D0%B2%D1%87%D0%B0%D0%B2%D0%B0%D0%B4%D0%B7%D0%B5+33">' in h
    p.address = None
    assert '/map?pin=41.700000%2C44.760000&amp;' in properties.post_rich(db, p, [])  # no address: our pin map
    assert h.endswith(f'<tg-button type="callback_data" style="primary" data="plike:{p.id}">❤️ I like it</tg-button></tg-button-row>')


def test_rich_post_keeps_only_the_description_prose_of_an_old_post():
    """An old-base object's description is the whole agency post: the rich post keeps only its own words."""
    old = ("#DidiDigomi 🚇 #Sarajishvili \n📍[11 Ioane Petretse St](https://www.google.com/maps/place/x) \n \n"
           "Bright flat with a view of the park.\nNew furniture.\n \n🏢 #2Bed #Apartment for #Rent \n✅\xa0#WiFi  ✅\xa0#TV\n"
           "✖️\xa0ParkingPlace \n👬Tenants: 1-4 \n💰 800$ + Deposit 800$ \n0% Commission \n#Price700to900 \n"
           "📲 @David_Tibelashvili |  \n+995 599 20 67 16 #Eduard \n📍[**APARTMENTS ON MAP**](https://hge.ge/map/)📍")
    assert properties.prose(old) == "Bright flat with a view of the park.\nNew furniture."
    assert properties.prose("Сдаю уютную квартиру в Ваке.\nРядом парк.") == "Сдаю уютную квартиру в Ваке.\nРядом парк."
    m = properties.MAPS_POINT.search("📍[x](https://www.google.com/maps/place/x/@41.7918386,44.7574398,16z/data=!3d41.7918386!4d44.7574398)")
    assert (float(m[1] or m[3]), float(m[2] or m[4])) == (41.7918386, 44.7574398)  # «On the map» pin for an old-base object


def test_published_object_takes_leads_when_its_source_ad_is_gone(db):
    from app.services import leads
    x = make_client_listing(db, "tg55")
    assert leads.client_listing_eligible(db, "tg55")
    x.status = "gone"  # the owner took the ad down after signing with us: the channel post is still ours
    db.commit()
    assert leads.client_listing_eligible(db, "tg55")


def test_hand_typed_lease_on_an_old_base_object(db, client):
    """David 2026-11-10: an object from the old base has no deal: the agent types the tenant and the lease dates on the
    card; the lease end is in the calendar feed (rented=1) and the agent is reminded 30/7/1 days before."""
    from datetime import date, timedelta

    from app.models import Job, Property
    agent = make_user(db, "agent")
    p = Property(agent_id=agent.id, status="published", address="Ваке, Чавчавадзе 53", source_type="legacy")
    db.add(p)
    db.commit()
    end = (date.today() + timedelta(days=90)).isoformat()
    r = client.patch(f"/api/properties/{p.id}", headers=auth(agent), json={
        "rent_from": date.today().isoformat(), "rent_to": end, "tenant_name": "Анна", "tenant_phone": "+995 555 12 34 56"})
    assert r.status_code == 200, r.text
    assert (r.json()["rent_to"], r.json()["tenant_name"]) == (end, "Анна")
    jobs = db.query(Job).filter(Job.kind == "property_lease_expiry", Job.done_at.is_(None)).all()
    assert sorted(j.payload["days"] for j in jobs) == [1, 7, 30]
    rows = client.get("/api/properties?rented=1", headers=auth(agent)).json()["items"]
    assert [x["id"] for x in rows] == [p.id]
    client.patch(f"/api/properties/{p.id}", headers=auth(agent), json={"rent_to": ""})  # cleared: no reminders
    assert not db.query(Job).filter(Job.kind == "property_lease_expiry", Job.done_at.is_(None)).count()


def test_post_media_fit_the_bot_api_upload_limit(tmp_path, monkeypatch):
    """2026-11-11: a 157 MB phone video made the whole post fail (Request Entity Too Large): a video over what the
    photos leave of 48 MB is squeezed by ffmpeg, or left out when that fails."""
    from app.telegram import notify
    photo, big = tmp_path / "a.jpg", tmp_path / "b.mov"
    photo.write_bytes(b"x" * 1000)
    with open(big, "wb") as fh:
        fh.truncate(60 * 2**20)
    small = tmp_path / "b_small.mp4"
    small.write_bytes(b"v" * 1000)
    monkeypatch.setattr(notify, "watermarked", lambda p, tmp: p)  # also a stub by the autouse fixture
    asked = []
    monkeypatch.setattr(notify, "_squeeze_video", lambda p, tmp, n: asked.append(n) or str(small))
    assert notify._bot_media([("photo", str(photo)), ("video", str(big))], str(tmp_path)) == [("photo", str(photo)), ("video", str(small))]
    assert asked == [notify.BOT_UPLOAD_MAX - 1000]
    monkeypatch.setattr(notify, "_squeeze_video", lambda p, tmp, n: None)
    assert notify._bot_media([("photo", str(photo)), ("video", str(big))], str(tmp_path)) == [("photo", str(photo))]
    assert notify._bot_media([("video", str(small))], str(tmp_path)) == [("video", str(small))]  # small: untouched


def test_team_avatars_from_telegram(db, monkeypatch, tmp_path):
    """Irakli 2026-11-11: the team's Telegram profile photos fill users.photo_url; a set photo (a Telegram sign-in) is
    kept, a hidden one leaves the initials."""
    import io

    from PIL import Image

    from app import storage
    from app.telegram import avatars
    a, b, c = make_user(db, "agent", tg_id=101), make_user(db, "agent", tg_id=102), make_user(db, "agent", tg_id=103)
    c.photo_url = "https://t.me/i/userpic/320/c.jpg"
    db.commit()
    buf = io.BytesIO()
    Image.new("RGB", (640, 640), "red").save(buf, "JPEG")
    calls = {"getUserProfilePhotos": lambda p: {"photos": [[{"file_id": "s"}, {"file_id": "big"}]]} if p["user_id"] == 101 else {"photos": []},
             "getFile": lambda p: {"file_path": "photos/x.jpg"}}
    monkeypatch.setattr(avatars.tg, "call", lambda m, **p: calls[m](p))
    monkeypatch.setattr(avatars.httpx, "get", lambda url, timeout: type("R", (), {"status_code": 200, "content": buf.getvalue()})())
    saved = {}
    monkeypatch.setattr(storage, "put", lambda k, d: saved.update({k: d}))
    assert avatars.run(out=lambda *_: None) == (1, 1)
    db.expire_all()
    assert a.photo_url == f"/api/media/avatars/u{a.id}.jpg" and b.photo_url is None and c.photo_url.startswith("https://t.me/")
    with Image.open(io.BytesIO(saved[f"avatars/u{a.id}.jpg"])) as im:
        assert im.size == (256, 256)
