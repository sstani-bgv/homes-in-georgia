"""«Права» (Stanislav 11-23): standard roles keep their standard rights (= the old require_level / at_least behaviour);
the superadmin changes them for one person in his card (Irakli's «operator who only posts photos»), every change is an
event in that person's rights history."""
from app import storage
from app.models import PropertyMedia
from app.services import perms
from tests.conftest import auth, make_user
from tests.test_similar import _object


def test_defaults_are_the_old_ladder(db, client):
    me = client.get("/api/auth/me", headers=auth(make_user(db, "recruit", "Рекрут"))).json()
    assert me["perms"] == {"contracts": "rcu", "originals": ""} and me["perms_own"] is False
    assert perms.for_user(make_user(db, "operator", "Оля")) == {"contracts": "rcu", "originals": "r"}
    assert perms.for_user(make_user(db, "superadmin", "Стас")) == {"contracts": "rcu", "originals": "r"}
    assert perms.for_user(make_user(db, "car_operator", "Авто")) == {"contracts": "", "originals": ""}
    info = client.get("/api/admin/permissions", headers=auth(make_user(db, "superadmin", "Стас2"))).json()
    assert info["defaults"]["agent"] == {"contracts": "rcu", "originals": ""}
    assert client.get("/api/admin/permissions", headers=auth(make_user(db, "admin", "Давид"))).status_code == 403


def test_rights_of_one_person(db, client):
    """Irakli's photo operator: an operator with «Договоры» off; another agent gets the originals."""
    boss, admin = make_user(db, "superadmin", "Стас"), make_user(db, "admin", "Давид")
    op, vika, other = make_user(db, "operator", "Фото"), make_user(db, "agent", "Вика"), make_user(db, "operator", "Оля")
    pid = _object(db, client, vika)
    storage.put(f"properties/{pid}/a.jpg", b"x")
    db.add(PropertyMedia(property_id=pid, kind="photo", path=f"properties/{pid}/a.jpg", position=0, dhash=0))
    db.commit()
    assert client.get(f"/api/properties/{pid}/originals", headers=auth(vika)).status_code == 403

    # only the superadmin changes rights
    assert client.patch(f"/api/users/{op.id}", json={"perms": {"contracts": ""}}, headers=auth(admin)).status_code == 403
    assert client.patch(f"/api/users/{op.id}", json={"perms": {"nope": "r"}}, headers=auth(boss)).status_code == 400
    assert client.patch(f"/api/users/{boss.id}", json={"perms": {"contracts": ""}}, headers=auth(boss)).status_code == 200
    assert perms.for_user(boss)["contracts"] == "rcu"  # the superadmin keeps everything
    r = client.patch(f"/api/users/{op.id}", json={"perms": {"contracts": "", "originals": "r"}}, headers=auth(boss))
    assert r.status_code == 200 and r.json()["perms"] == {"contracts": "", "originals": "r"} and r.json()["perms_own"]
    db.refresh(op)
    assert op.perms == {"contracts": ""}  # only what differs from the role
    assert client.get("/api/contracts", headers=auth(op)).status_code == 403
    assert client.get("/api/contracts", headers=auth(other)).status_code == 200  # the role itself unchanged
    assert client.post("/api/contracts/draft", json={}, headers=auth(op)).status_code == 403

    client.patch(f"/api/users/{vika.id}", json={"perms": {"originals": "r", "contracts": "r"}}, headers=auth(boss))
    assert client.get(f"/api/properties/{pid}/originals", headers=auth(vika)).status_code == 200
    assert client.get("/api/contracts", headers=auth(vika)).status_code == 200  # read kept
    assert client.post("/api/contracts/draft", json={}, headers=auth(vika)).status_code == 403  # create taken away

    # an admin's edit that doesn't touch the rights keeps them; another role drops them
    assert client.patch(f"/api/users/{op.id}", json={"username": "foto"}, headers=auth(admin)).status_code == 200
    db.refresh(op)
    assert op.perms == {"contracts": ""}
    client.patch(f"/api/users/{op.id}", json={"role": "agent"}, headers=auth(boss))
    db.refresh(op)
    assert op.perms is None and perms.for_user(op)["contracts"] == "rcu"

    hist = client.get(f"/api/users/{op.id}/perms-history", headers=auth(boss)).json()
    assert [(h["by"], h["role"], h["changes"]) for h in hist] == [
        ("Стас", "agent", {"originals": {"from": "r", "to": ""}, "contracts": {"from": "", "to": "rcu"}}),
        ("Стас", "operator", {"contracts": {"from": "rcu", "to": ""}})]
    assert client.get(f"/api/users/{op.id}/perms-history", headers=auth(admin)).status_code == 403
