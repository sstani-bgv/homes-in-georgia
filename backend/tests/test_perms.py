"""«Права» (Stanislav 11-23): the role × section matrix. Defaults = the old require_level / at_least behaviour; the
superadmin's flags change it without code (Irakli's «operator who only posts photos»), every change is an event."""
from app import storage
from app.models import PropertyMedia
from app.services import perms
from tests.conftest import auth, make_user
from tests.test_similar import _object


def test_defaults_are_the_old_ladder(db, client):
    me = client.get("/api/auth/me", headers=auth(make_user(db, "recruit", "Рекрут"))).json()
    assert me["perms"] == {"contracts": "rcu", "originals": ""}
    assert perms.for_user(db, make_user(db, "operator", "Оля")) == {"contracts": "rcu", "originals": "r"}
    assert perms.for_user(db, make_user(db, "superadmin", "Стас")) == {"contracts": "rcu", "originals": "r"}
    assert perms.for_user(db, make_user(db, "car_operator", "Авто")) == {"contracts": "", "originals": ""}
    m = client.get("/api/admin/permissions", headers=auth(make_user(db, "superadmin", "Стас2"))).json()
    assert m["matrix"] == m["defaults"] and m["roles"] == ["admin", "operator", "agent", "recruit"] and m["custom"] == []


def test_superadmin_flags_change_access(db, client):
    boss, admin = make_user(db, "superadmin", "Стас"), make_user(db, "admin", "Давид")
    vika, rec = make_user(db, "agent", "Вика"), make_user(db, "recruit", "Рекрут")
    pid = _object(db, client, vika)
    storage.put(f"properties/{pid}/a.jpg", b"x")
    db.add(PropertyMedia(property_id=pid, kind="photo", path=f"properties/{pid}/a.jpg", position=0, dhash=0))
    db.commit()
    assert client.get(f"/api/properties/{pid}/originals", headers=auth(vika)).status_code == 403
    assert client.put("/api/admin/permissions", json={"agent": {"originals": "r"}}, headers=auth(admin)).status_code == 403
    assert client.put("/api/admin/permissions", json={"superadmin": {"originals": ""}}, headers=auth(boss)).status_code == 400

    r = client.put("/api/admin/permissions", json={"agent": {"originals": "r"}, "recruit": {"contracts": "r"}}, headers=auth(boss))
    assert r.status_code == 200 and r.json()["matrix"]["agent"]["originals"] == "r"
    assert client.get(f"/api/properties/{pid}/originals", headers=auth(vika)).status_code == 200
    assert client.get("/api/contracts", headers=auth(rec)).status_code == 200  # read kept
    assert client.post("/api/contracts/draft", json={}, headers=auth(rec)).status_code == 403  # create taken away
    assert client.get("/api/auth/me", headers=auth(rec)).json()["perms"]["contracts"] == "r"

    hist = client.get("/api/admin/permissions/history", headers=auth(boss)).json()
    assert {(h["role"], s, c["from"], c["to"]) for h in hist for s, c in h["changes"].items()} == {
        ("agent", "originals", "", "r"), ("recruit", "contracts", "rcu", "r")}
    # back to the default: no stored row left, nothing logged for an unchanged cell
    client.put("/api/admin/permissions", json={"agent": {"originals": ""}, "recruit": {"contracts": "rcu"}, "admin": {"contracts": "rcu"}},
               headers=auth(boss))
    assert perms._stored(db) == {} and len(client.get("/api/admin/permissions/history", headers=auth(boss)).json()) == 4


def test_custom_role_on_the_basis_of_operator(db, client):
    """Irakli's «оператор, который только выкладывает фото»: a role made on operator, contracts taken off."""
    boss, admin, op = make_user(db, "superadmin", "Стас"), make_user(db, "admin", "Давид"), make_user(db, "operator", "Фото")
    assert client.post("/api/admin/permissions/roles", json={"label": "Фото", "base": "operator"}, headers=auth(admin)).status_code == 403
    assert client.post("/api/admin/permissions/roles", json={"label": "X", "base": "superadmin"}, headers=auth(boss)).status_code == 400
    m = client.post("/api/admin/permissions/roles", json={"label": "Оператор фото", "base": "operator"}, headers=auth(boss)).json()
    (cr,) = m["custom"]
    key = cr["key"]
    assert m["matrix"][key] == m["defaults"][key] == {"contracts": "rcu", "originals": "r"}
    client.put("/api/admin/permissions", json={key: {"contracts": ""}}, headers=auth(boss))

    # only the superadmin assigns it, and only to a user of its base role
    assert client.patch(f"/api/users/{op.id}", json={"custom_role_id": cr["id"]}, headers=auth(admin)).status_code == 403
    agent = make_user(db, "agent", "Вика")
    assert client.patch(f"/api/users/{agent.id}", json={"custom_role_id": cr["id"]}, headers=auth(boss)).status_code == 400
    assert client.patch(f"/api/users/{op.id}", json={"custom_role_id": cr["id"]}, headers=auth(boss)).status_code == 200
    assert client.get("/api/auth/me", headers=auth(op)).json()["perms"] == {"contracts": "", "originals": "r"}
    assert client.get("/api/contracts", headers=auth(op)).status_code == 403
    assert client.get("/api/admin/permissions", headers=auth(boss)).json()["custom"][0]["users"] is True

    # another role than the base (promotion etc.): the user's own role's flags again
    client.patch(f"/api/users/{op.id}", json={"role": "agent"}, headers=auth(boss))
    assert client.get("/api/auth/me", headers=auth(op)).json()["perms"]["contracts"] == "rcu"
    client.patch(f"/api/users/{op.id}", json={"role": "operator"}, headers=auth(boss))
    # removing the role: its users are back on the base role
    client.delete(f"/api/admin/permissions/roles/{cr['id']}", headers=auth(boss))
    assert client.get("/api/auth/me", headers=auth(op)).json()["perms"]["contracts"] == "rcu"
    assert perms._stored(db) == {}
    assert [h["action"] for h in client.get("/api/admin/permissions/history", headers=auth(boss)).json()] == [
        "role_deleted", "updated", "role_created"]
    hist = client.get("/api/admin/permissions/history", headers=auth(boss)).json()
    assert {(h["by"], h["label"]) for h in hist if h["action"] != "updated"} == {("Стас", "Оператор фото")}
