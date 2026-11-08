"""David 2026-11-07: twin beats a closer flat in the channel; agent submit needs full params and no existing object."""
from app.models import Property
from app.services import properties
from app.services.errors import DomainError
from tests.conftest import make_client_listing, make_listing, make_user


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
    return db.get(Property, p.id)


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
