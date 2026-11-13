"""«Посуточная аренда» (Daily Rent / ApartFlow inside the CRM, Irakli 11-13): the Daily Rent manager role sees only
Daily Rent; superadmin and admin always have it; the entry token is what ApartFlow expects."""
import jwt
import pytest

from app.config import settings
from tests.conftest import auth, make_user

SECRET = "daily-rent-test-secret-long-enough-for-hs256"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_RENT_URL", "https://apartflow.example/")
    monkeypatch.setattr(settings, "DAILY_RENT_SSO_SECRET", SECRET)


def session(client, user):
    return client.post("/api/daily-rent/session", headers=auth(user))


def test_manager_gets_entry_token(client, db, configured):
    m = make_user(db, "daily_rent_manager", name="Анастасия")
    r = session(client, m)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("https://apartflow.example/#sso=")
    claims = jwt.decode(url.split("#sso=", 1)[1], SECRET, algorithms=["HS256"], audience="apartflow", issuer="hig-crm")
    assert claims["sub"] == str(m.id) and claims["role"] == "daily_rent_manager" and claims["name"] == "Анастасия"
    assert claims["exp"] - claims["iat"] == 12 * 3600


@pytest.mark.parametrize("role", ["superadmin", "admin"])
def test_admins_always_have_daily_rent(client, db, configured, role):
    assert session(client, make_user(db, role)).status_code == 200


@pytest.mark.parametrize("role", ["operator", "agent", "recruit"])
def test_other_roles_have_no_daily_rent(client, db, configured, role):
    assert session(client, make_user(db, role)).status_code == 403


def test_not_configured(client, db):
    r = session(client, make_user(db, "superadmin"))
    assert r.status_code == 503


@pytest.mark.parametrize("path", ["/api/leads", "/api/deals", "/api/users", "/api/clients", "/api/invites", "/api/ticker",
                                  "/api/analytics/dashboard"])
def test_manager_sees_nothing_else(client, db, path):
    m = make_user(db, "daily_rent_manager")
    r = client.get(path, headers=auth(m))
    assert r.status_code == 403, (path, r.status_code)


def test_manager_profile_still_works(client, db):
    m = make_user(db, "daily_rent_manager", name="Анастасия")
    r = client.get("/api/auth/me", headers=auth(m))
    assert r.status_code == 200 and r.json()["role"] == "daily_rent_manager"


def test_admin_grants_role_and_invites(client, db):
    adm = make_user(db, "admin")
    h = auth(adm)
    r = client.post("/api/invites", json={"role": "daily_rent_manager", "note": "менеджер посуточной аренды"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "daily_rent_manager"
    r = client.post("/api/users", headers=h, json={"first_name": "Анастасия", "last_name": "К", "role": "daily_rent_manager",
                                                   "phone": "+995555000000", "email": "a@example.com", "birth_date": "1995-01-01"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "daily_rent_manager"
