"""Stage 8 (client timeline card, refusals) and stage 10 (demand count, owner-first fast track + push)."""
from datetime import datetime, timedelta

from app.models import ClientView, Event, Lead, Property, Reaction, Search
from app.services import clients as client_card
from app.services import demand, leads
from app.services.common import jdump, now, now_text
from tests.conftest import auth, make_client, make_client_listing, make_listing, make_user

PROFILE = {"residents": "2", "budget_max": 900, "bedrooms": [2], "districts": ["Ваке"], "pets": ["cat"], "terms": ["6"]}
FLAT = {"price_usd": 850, "bedrooms": 2, "district": "Ваке", "lat": 41.71, "lng": 44.76, "pets_allowed": None, "periods": None}


def test_fits():
    f = demand.fits
    assert f(PROFILE, FLAT)
    assert f(PROFILE, {**FLAT, "price_usd": 990}) and not f(PROFILE, {**FLAT, "price_usd": 1000})  # budget +10%
    assert not f(PROFILE, {**FLAT, "bedrooms": 1}) and f({"bedrooms": [3]}, {**FLAT, "bedrooms": 5})  # 3 = 3+
    assert f(PROFILE, {**FLAT, "district": "Ваке-Багеби"}) and not f(PROFILE, {**FLAT, "district": "Глдани"})
    assert not f(PROFILE, {**FLAT, "district": None}) and f({**PROFILE, "districts": ["any"]}, {**FLAT, "district": None})
    assert not f(PROFILE, {**FLAT, "pets_allowed": False}) and f({**PROFILE, "pets": ["none"]}, {**FLAT, "pets_allowed": False})
    assert f(PROFILE, {**FLAT, "periods": ["3Month", "12Month"]}) and not f(PROFILE, {**FLAT, "periods": ["12Month"]})
    near = {"pin": {"lat": 41.71, "lng": 44.76}, "radius_km": 1}
    assert f(near, FLAT) and not f(near, {**FLAT, "lat": 41.80}) and not f(near, {**FLAT, "lat": None})
    assert f({}, FLAT)


def _clients(db, n, **kw):
    out = []
    for i in range(n):
        c = make_client(db, name=f"Клиент {i}", external_id=7000 + i + kw.pop("offset", 0), profile_done=1)
        c.profile = jdump(kw.get("profile", PROFILE))
        db.commit()
        out.append(c)
    return out


def _object(db, status="owner_confirmed"):
    agent = make_user(db, "agent", agent_tag="Zaza")
    listing = make_listing(db, lat=41.71, lng=44.76)
    p = Property(listing_id=listing.id, status=status, agent_id=agent.id, district="Ваке", price=850, bedrooms=2)
    db.add(p)
    db.commit()
    return agent, listing, p


def test_fast_track_makes_object_client_eligible(db, client):
    agent, listing, p = _object(db)
    assert not leads.client_listing_eligible(db, listing.id)
    other = make_user(db, "agent")
    assert client.post(f"/api/properties/{p.id}/fast-track", headers=auth(other)).status_code == 403
    r = client.post(f"/api/properties/{p.id}/fast-track", headers=auth(agent))
    assert r.status_code == 200 and r.json()["fast_track_at"]
    assert leads.client_listing_eligible(db, listing.id)
    assert client.post(f"/api/properties/{p.id}/fast-track", headers=auth(agent)).json()["code"] == "already"
    for status in ("archived", "rented"):
        p.status = status
        db.commit()
        assert not leads.client_listing_eligible(db, listing.id)


def test_push_recipients_cap_no_resend_logged(db, client, sent):
    agent, listing, p = _object(db)
    fit = _clients(db, 12)
    far = _clients(db, 1, offset=100, profile={**PROFILE, "districts": ["Глдани"]})
    stale = _clients(db, 1, offset=200)[0]
    stale.updated_at = (now() - timedelta(days=demand.ACTIVE_DAYS + 1)).isoformat(timespec="seconds")
    done = _clients(db, 1, offset=300)[0]
    db.add(Lead(client_id=done.id, listing_id=make_listing(db, "ss2").id, status="rented", created_at=now_text(), updated_at=now_text()))
    seen = fit[0]
    db.add(Reaction(client_id=seen.id, listing_id=listing.id, reaction="dislike", created_at=now_text()))
    db.commit()
    staff, before = make_user(db, "operator"), client.get(f"/api/properties/{p.id}/demand", headers=auth(agent)).json()
    assert before["count"] == 12 and before["can_fast_track"] and "name" not in before["clients"][0]  # agents: anonymized
    assert client.get(f"/api/properties/{p.id}/demand", headers=auth(staff)).json()["clients"][0]["name"].startswith("Клиент")
    r = client.post(f"/api/properties/{p.id}/fast-track", headers=auth(agent)).json()
    assert r["sent"] == demand.PUSH_CAP
    got = {v.client_id for v in db.query(ClientView).filter_by(listing_id=listing.id, source="push")}
    assert len(got) == 10 and seen.id not in got and not got & {far[0].id, stale.id, done.id}
    assert [a[1] for a in sent.of("object_to_client")] == [listing.id] * 10
    more = demand.push(db, db.get(Property, p.id), listing, agent)  # a later push reaches only the 1 left, never again the 10
    db.commit()
    assert len(more) == 1 and more[0].id not in got | {seen.id}
    assert demand.push(db, db.get(Property, p.id), listing, agent) == []
    after = client.get(f"/api/properties/{p.id}/demand", headers=auth(staff)).json()
    assert after["pushed"] == 11 and not after["can_fast_track"]
    # the client card shows the pushed object
    tl = client.get(f"/api/clients/{fit[1].id}", headers=auth(staff)).json()["timeline"]
    assert tl[0]["type"] == "shown" and tl[0]["action"] == "push" and tl[0]["links"]["property_id"] == p.id


def test_new_listings_demand_count(db, client):
    _clients(db, 3)
    make_listing(db)
    make_listing(db, "ss9", district="Глдани")
    items = {x["id"]: x for x in client.get("/api/listings/new", headers=auth(make_user(db))).json()["items"]}
    assert (items["ss1001"]["demand"], items["ss9"]["demand"]) == (3, 0)


def test_timeline_merge_order_and_refusals(db, client):
    staff, agent = make_user(db, "operator", name="Нино"), make_user(db, "agent", agent_tag="Zaza")
    c = make_client(db, profile_done=1)
    c.created_at = "2026-11-01T10:00:00+00:00"
    listing = make_client_listing(db)
    db.add(Event(entity="client", entity_id=str(c.id), action="profile_saved", actor_label="client",
                 data={"profile": PROFILE}, at=now() - timedelta(hours=3)))
    s = Search(client_id=c.id, results=1, at=(now() - timedelta(hours=2)).isoformat(timespec="seconds"))
    db.add(s)
    db.flush()
    db.add(ClientView(client_id=c.id, listing_id=listing.id, search_id=s.id, shown_at=now() - timedelta(hours=2)))
    db.commit()
    lead = leads.create_from_like(db, c.id, listing.id)
    leads.operator_take(db, lead.id, staff)
    leads.assign_agent(db, lead.id, agent.id, staff)
    leads.cancel(db, lead.id, staff, "price")
    d = client.get(f"/api/clients/{c.id}", headers=auth(staff)).json()
    tl = d["timeline"]
    ats = [datetime.fromisoformat(x["at"]) for x in tl]
    assert ats == sorted(ats, reverse=True)
    acts = [x["action"] for x in tl]
    assert acts == ["cancelled", "agent_assigned", "operator_taken", "created", "like", "shown", "search", "profile_saved",
                    "first_contact"]
    assert [x["type"] for x in tl][3:] == ["lead", "reaction", "shown", "search", "profile", "profile"]
    assert "#Zaza" in next(x["text"] for x in tl if x["action"] == "agent_assigned")
    assert next(x for x in tl if x["action"] == "created")["links"]["lead_id"] == lead.id
    (ref,) = d["refusals"]
    assert (ref["by"], ref["by_name"], ref["reason"], ref["stage"]) == ("operator", "Нино", "Цена", "agent_assigned")
    assert d["status"].startswith("Сейчас: отказ: Цена")


def test_status_line_waiting_for_viewing(db):
    agent = make_user(db, "agent", agent_tag="Zaza")
    c = make_client(db)
    t = now_text()
    lead = Lead(client_id=c.id, listing_id=make_listing(db).id, status="time_chosen", agent_id=agent.id,
                viewing_at=now().replace(month=11, day=12, hour=14, minute=0, second=0, microsecond=0), created_at=t, updated_at=t)
    db.add(lead)
    db.commit()
    line = client_card.card(db, c)["status"]
    assert line.endswith("заявка #1: Ваке · 2 сп. · $850")
    assert line.startswith("Сейчас: ждёт просмотра 12.11 в ") and "с агентом #Zaza" in line
