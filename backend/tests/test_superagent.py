"""Role «Суперагент» (superagent): every superadmin permission AND a working agent (pickers, leads, objects, leaderboard)."""
from app.models import Lead, Property, User
from app.services import leads, users
from tests.conftest import auth, make_client, make_client_listing, make_listing, make_user


def test_superagent_is_superadmin_and_agent(db, client):
    david = make_user(db, "superagent", "Давид", level=90, districts=["Ваке"], agent_tag="David")
    nino = make_user(db, "operator", "Нино")
    zaza = make_user(db, "agent", "Zaza")
    admin = make_user(db, "admin", "Иракли")
    h = auth(david)

    # admin side: settings, users, sources, admin-only writes
    assert client.get("/api/settings", headers=h).status_code == 200
    assert client.put("/api/sources/schedule", json={}, headers=h).status_code != 403
    r = client.post("/api/users", json={"name": "Новый админ", "role": "admin"}, headers=h)
    assert r.status_code == 200, r.text
    # only a superadmin/superagent grants super roles; a plain admin can't
    assert client.post("/api/users", json={"name": "X", "role": "superagent"}, headers=auth(admin)).status_code == 403
    assert client.patch(f"/api/users/{david.id}", json={"name": "Давид Б"}, headers=auth(admin)).status_code == 403

    # agent side: in the agent pickers / leaderboard / field users
    assert david.id in [u["id"] for u in client.get("/api/users", params={"role": "agent,recruit,superagent"}, headers=h).json()["items"]]
    assert david.id in [a["id"] for a in client.get("/api/analytics/agents", headers=h).json()]
    assert david.id in [u.id for u in users.field_users(db)]

    # an operator assigns him a lead
    lead = leads.create_from_like(db, make_client(db).id, make_client_listing(db).id)
    client.post(f"/api/leads/{lead.id}/take", headers=auth(nino))
    r = client.post(f"/api/leads/{lead.id}/assign", json={"agent_id": david.id}, headers=auth(nino))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Lead, lead.id).agent_id == david.id

    # he takes an object as his own; it shows in «Мои объекты»; he still sees Zaza's objects (superadmin)
    mine = client.post("/api/properties", json={"listing_id": make_listing(db, "ss1").id}, headers=h).json()
    assert mine["agent"]["id"] == david.id
    theirs = client.post("/api/properties", json={"listing_id": make_listing(db, "ss2").id}, headers=auth(zaza)).json()
    ids = lambda r: [p["id"] for p in r.json()["items"]]  # noqa: E731
    assert ids(client.get("/api/properties", params={"mine": 1}, headers=h)) == [mine["id"]]
    assert {mine["id"], theirs["id"]} <= set(ids(client.get("/api/properties", headers=h)))
    # a plain agent still doesn't see the others' objects in his list
    assert mine["id"] not in ids(client.get("/api/properties", headers=auth(zaza)))
    # «Сменить агента собственника»: Zaza's owner (and his live object) moves to David
    r = client.post(f"/api/properties/{theirs['id']}/owner", json={"is_owner": True, "owner_name": "Нана", "owner_phone": "+995 599 20 30 40"},
                    headers=auth(zaza)).json()
    r = client.post(f"/api/owners/{r['owner']['id']}/transfer", json={"agent_id": david.id}, headers=auth(nino))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Property, theirs["id"]).agent_id == david.id


def test_superagent_counts_for_bootstrap(db, monkeypatch):
    """A superagent owner must not be re-promoted to superadmin by the env bootstrap on restart."""
    from app.config import settings
    u = make_user(db, "superagent", "Давид", tg_id=4242)
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_LOGIN", "")
    monkeypatch.setattr(type(settings), "superadmin_tg_ids", property(lambda s: [4242]))
    assert users.ensure_bootstrap(db) == []
    db.expire_all()
    assert db.get(User, u.id).role == "superagent"
