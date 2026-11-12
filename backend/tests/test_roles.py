"""Hierarchical RBAC (Irakli 11-12, docs/ROLES-IRAKLI-2026-11-12.md): recruit 1 < agent 2 < operator 3 < admin 4 <
superadmin 5, every role inherits the lower ones; staff «+ как агент» = users.works_as_agent (was role superagent)."""
import pytest

from app.models import Lead, Property, User, at_least, is_field, role_level
from app.services import leads, users
from tests.conftest import PROFILE, auth, make_client, make_client_listing, make_listing, make_user


def test_superadmin_working_as_agent(db, client):
    david = make_user(db, "superadmin", "Давид", level=90, districts=["Ваке"], agent_tag="David", works_as_agent=True)
    nino = make_user(db, "operator", "Нино")
    zaza = make_user(db, "agent", "Zaza")
    admin = make_user(db, "admin", "Иракли")
    h = auth(david)

    # admin side: settings, users, sources, admin-only writes
    assert client.get("/api/settings", headers=h).status_code == 200
    assert client.put("/api/sources/schedule", json={}, headers=h).status_code != 403
    r = client.post("/api/users", json={**PROFILE, "name": "Новый Админ", "role": "admin"}, headers=h)
    assert r.status_code == 200, r.text
    # only a superadmin grants admin roles; a plain admin can't
    assert client.post("/api/users", json={"name": "X", "role": "superadmin"}, headers=auth(admin)).status_code == 403
    assert client.patch(f"/api/users/{david.id}", json={"name": "Давид Б"}, headers=auth(admin)).status_code == 403

    # agent side: in the agent pickers / leaderboard / field users
    assert david.id in [u["id"] for u in client.get("/api/users", params={"role": "field"}, headers=h).json()["items"]]
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

    # «Главная»: the agency numbers plus his own; operators/agents get only one block
    dash = client.get("/api/analytics/dashboard", headers=h).json()
    assert dash["stats"]["scope"] == "agency" and dash["stats_mine"]["scope"] == "mine"
    assert dash["stats_mine"]["objects"]["work"] == 2  # both objects are his after the transfer
    assert client.get("/api/analytics/dashboard", headers=auth(zaza)).json()["stats"]["objects"]["work"] == 0
    assert "stats_mine" not in client.get("/api/analytics/dashboard", headers=auth(nino)).json()


def test_superadmin_agent_flag_survives_bootstrap(db, monkeypatch):
    """The env bootstrap on restart leaves an existing superadmin (and his «+ как агент» flag) alone."""
    from app.config import settings
    u = make_user(db, "superadmin", "Давид", tg_id=4242, works_as_agent=True)
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_LOGIN", "")
    monkeypatch.setattr(type(settings), "superadmin_tg_ids", property(lambda s: [4242]))
    assert users.ensure_bootstrap(db) == []
    db.expire_all()
    assert (db.get(User, u.id).role, db.get(User, u.id).works_as_agent) == ("superadmin", True)


def test_levels_inherit():
    order = ["recruit", "agent", "operator", "admin", "superadmin"]
    assert [role_level(r) for r in order] == [1, 2, 3, 4, 5]
    for i, r in enumerate(order):
        assert [at_least(User(role=r), x) for x in order] == [j <= i for j in range(5)]
    assert is_field(User(role="recruit")) and is_field(User(role="agent"))
    assert not is_field(User(role="admin")) and is_field(User(role="admin", works_as_agent=True))


def test_staff_not_in_agent_pickers_unless_flagged(db, client):
    irakli = make_user(db, "admin", "Иракли")
    nino = make_user(db, "operator", "Нино")
    ids = lambda: [u["id"] for u in client.get("/api/users", params={"role": "field"}, headers=auth(nino)).json()["items"]]  # noqa: E731
    assert irakli.id not in ids()
    irakli.works_as_agent = True
    db.commit()
    assert irakli.id in ids() and irakli.id in [u.id for u in users.field_users(db)]


@pytest.fixture(autouse=True)
def fake_chrome(monkeypatch):
    from app.services import contracts
    monkeypatch.setattr(contracts, "render_pdf", lambda page: b"%PDF-1.4 test")


CONTRACT = {"kind": "rent", "lang": "RU", "html": "<p>Договор</p>"}
ROLES = ["recruit", "agent", "operator", "admin", "superadmin"]


@pytest.mark.parametrize("role", ROLES)
def test_matrix_objects_contracts_deals(db, client, role):
    """Own vs others' objects; contracts and deals from agent up; others' from operator up."""
    me = make_user(db, role, "Я")
    other = make_user(db, "agent", "Чужой")
    h, lvl = auth(me), role_level(role)
    mine = client.post("/api/properties", json={"listing_id": make_listing(db, "ss1").id}, headers=h)
    assert mine.status_code == 200, mine.text  # every role creates objects
    mine = mine.json()["id"]
    theirs = client.post("/api/properties", json={"listing_id": make_listing(db, "ss2").id}, headers=auth(other)).json()["id"]
    assert client.patch(f"/api/properties/{mine}", json={"price": 900}, headers=h).status_code == 200
    listed = [p["id"] for p in client.get("/api/properties", headers=h).json()["items"]]
    assert (theirs in listed) == (lvl >= 3)
    assert (client.patch(f"/api/properties/{theirs}", json={"price": 777}, headers=h).status_code == 200) == (lvl >= 3)

    # contracts: none for a recruit; an agent sees his own only; operator+ sees everyone's
    from tests.test_contracts import _lead  # noqa: PLC0415 (a contract belongs to a deal: the agent's lead)
    r = client.post("/api/contracts", json={**CONTRACT, "lead_id": _lead(db, me).id}, headers=h)
    assert (r.status_code == 200) == (lvl >= 2), r.text
    assert client.get("/api/contracts", headers=h).status_code == (200 if lvl >= 2 else 403)
    theirs_c = client.post("/api/contracts", json={**CONTRACT, "lead_id": _lead(db, other).id}, headers=auth(other)).json()["id"]
    if lvl >= 2:
        assert (client.get(f"/api/contracts/{theirs_c}", headers=h).status_code == 200) == (lvl >= 3)
        own_c = r.json()["id"]
        r = client.post(f"/api/contracts/{own_c}/close", json={"reason": "client_changed_mind"}, headers=h)
        assert r.status_code == 200, r.text  # cancels his own contract with a reason

    # deals: none for a recruit; an agent cancels his own pending deal (with a reason), not others'
    deal = {"price": 850, "rent_from": "2030-01-01", "address": "ул. Тест 1", "property_id": mine}
    r = client.post("/api/deals", json=deal, headers=h)
    assert (r.status_code == 200) == (lvl >= 2), r.text
    theirs_d = client.post("/api/deals", json={**deal, "property_id": theirs}, headers=auth(other)).json()["id"]
    can_cancel_theirs = client.post(f"/api/deals/{theirs_d}/cancel", json={"reason": "x"}, headers=h).status_code == 200
    assert can_cancel_theirs == (lvl >= 3)
    if lvl == 2:
        own_d = r.json()["id"]
        assert client.post(f"/api/deals/{own_d}/cancel", json={}, headers=h).status_code == 400  # reason required
        assert client.post(f"/api/deals/{own_d}/cancel", json={"reason": "клиент передумал"}, headers=h).status_code == 200
        assert client.post(f"/api/deals/{theirs_d}/confirm", headers=h).status_code == 403
    if lvl >= 3:
        assert client.get("/api/analytics/funnel", headers=h).status_code == 200
    else:
        assert client.get("/api/analytics/funnel", headers=h).status_code == 403


@pytest.mark.parametrize("role", ROLES)
def test_matrix_users_and_settings(db, client, role):
    me = make_user(db, role, "Я")
    h, lvl = auth(me), role_level(role)
    agent = make_user(db, "agent", "Агент")
    adm = make_user(db, "admin", "Другой админ")
    sup = make_user(db, "superadmin", "Давид")
    st = lambda r: r.status_code  # noqa: E731
    # operators / agents / recruits: managed from admin up
    assert (st(client.post("/api/users", json={**PROFILE, "name": "Новый Оператор", "role": "operator"}, headers=h)) == 200) == (lvl >= 4)
    assert (st(client.patch(f"/api/users/{agent.id}", json={"is_active": False}, headers=h)) == 200) == (lvl >= 4)
    # admins / superadmins: only a superadmin
    assert (st(client.post("/api/users", json={**PROFILE, "name": "Анна Админ", "role": "admin"}, headers=h)) == 200) == (lvl >= 5)
    assert (st(client.patch(f"/api/users/{adm.id}", json={**PROFILE, "name": "Иракли 2"}, headers=h)) == 200) == (lvl >= 5)
    assert (st(client.patch(f"/api/users/{agent.id}", json={"role": "admin"}, headers=h)) == 200) == (lvl >= 5)
    assert (st(client.patch(f"/api/users/{sup.id}", json={"is_active": False}, headers=h)) == 200) == (lvl >= 5 and role == "superadmin")
    # settings: read from operator, write from admin; Telegram routing (integration) only a superadmin
    assert (st(client.get("/api/settings", headers=h)) == 200) == (lvl >= 3)
    assert (st(client.put("/api/settings", json={"review_batch": "6"}, headers=h)) == 200) == (lvl >= 4)
    assert (st(client.put("/api/settings", json={"group_chat_id": "-100777"}, headers=h)) == 200) == (lvl >= 5)
    assert (st(client.post("/api/userbot/logout", headers=h)) != 403) == (lvl >= 5)


def test_last_superadmin_is_protected(db, client):
    david = make_user(db, "superadmin", "Давид")
    h = auth(david)
    r = client.patch(f"/api/users/{david.id}", json={"role": "admin"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "last_superadmin"
    assert client.patch(f"/api/users/{david.id}", json={"is_active": False}, headers=h).status_code == 409
    second = make_user(db, "superadmin", "Второй")
    assert client.patch(f"/api/users/{second.id}", json={"is_active": False}, headers=h).status_code == 200
    assert client.patch(f"/api/users/{david.id}", json={"is_active": False}, headers=h).status_code == 409
    db.expire_all()
    second.is_active = True
    db.commit()
    assert client.patch(f"/api/users/{david.id}", json={"role": "admin"}, headers=h).status_code == 200
