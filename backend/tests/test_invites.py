"""Staff invites (Stanislav 11-12): one-time link → the Telegram account that opens it becomes the user."""
import hashlib
import hmac
import time
from datetime import timedelta

from app.config import settings
from app.models import Event, Invite, User
from app.services.common import now
from app.telegram import login_bot
from tests.conftest import auth, make_user


def widget(tg_id, **kw):
    """Login Widget fields signed with the bot token (LOGIN_BOT_TOKEN is empty in tests → BOT_TOKEN)."""
    d = {"id": tg_id, "first_name": "Нино", "last_name": "Беридзе", "username": "nino", "auth_date": int(time.time()), **kw}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hashlib.sha256(settings.BOT_TOKEN.encode()).digest()
    return {**d, "hash": hmac.new(key, dcs.encode(), hashlib.sha256).hexdigest()}


def invite(client, h, **kw):
    r = client.post("/api/invites", json={"role": "agent", **kw}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def token_of(inv):
    return inv["web_link"].rsplit("/", 1)[1]


def test_create_accept_web(client, db, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://admin.hge.ge")
    adm = make_user(db, "admin", name="Иракли Админ")
    h = auth(adm)
    inv = invite(client, h, level=50, agent_tag="Nino", districts=["Ваке"], note="Нино, новый агент")
    tok = token_of(inv)
    assert inv["status"] == "pending" and inv["web_link"] == f"https://admin.hge.ge/invite/{tok}" and len(tok) >= 32
    assert inv["bot_link"] is None  # no separate login bot configured
    info = client.get(f"/api/auth/invite/{tok}").json()
    assert info == {"status": "pending", "role": "agent", "invited_by": "Иракли", "expires_at": inv["expires_at"]}
    r = client.post("/api/auth/invite", json={**widget(777), "token": tok})
    assert r.status_code == 200, r.text
    me = r.json()["user"]
    assert me["tg_id"] == 777 and me["name"] == "Нино" and me["last_name"] == "Беридзе" and me["role"] == "agent" and me["level"] == 50
    assert me["agent_tag"] == "Nino" and me["districts"] == ["Ваке"]
    assert me["profile_missing"] == ["birth_date", "phone", "email"]  # → «Заполните профиль»
    row = client.get("/api/invites", headers=h).json()["items"][0]
    assert row["status"] == "accepted" and row["used_by"]["name"] == "Нино" and row["created_by"] == "Иракли"
    acts = {(e.entity, e.action) for e in db.query(Event).all()}
    assert {("invite", "created"), ("invite", "accepted"), ("user", "created")} <= acts
    # reused
    r = client.post("/api/auth/invite", json={**widget(778), "token": tok})
    assert r.status_code == 409 and r.json()["code"] == "invite_used"
    assert client.get(f"/api/auth/invite/{tok}").json()["status"] == "accepted"


def test_bad_signature_and_unknown(client, db):
    h = auth(make_user(db, "admin"))
    tok = token_of(invite(client, h))
    assert client.post("/api/auth/invite", json={**widget(5), "hash": "0" * 64, "token": tok}).status_code == 401
    assert client.post("/api/auth/invite", json={**widget(5), "token": "nope"}).status_code == 404
    assert db.query(User).filter(User.tg_id == 5).count() == 0


def test_expired_and_revoked(client, db):
    h = auth(make_user(db, "admin"))
    a, b = invite(client, h), invite(client, h)
    db.query(Invite).filter(Invite.id == a["id"]).update({"expires_at": now() - timedelta(minutes=1)})
    db.commit()
    r = client.post("/api/auth/invite", json={**widget(10), "token": token_of(a)})
    assert r.status_code == 410 and r.json()["code"] == "invite_expired"
    assert client.post(f"/api/invites/{a['id']}/revoke", headers=h).status_code == 409  # not pending any more
    assert client.post(f"/api/invites/{b['id']}/revoke", headers=h).json()["status"] == "revoked"
    r = client.post("/api/auth/invite", json={**widget(10), "token": token_of(b)})
    assert r.status_code == 410 and r.json()["code"] == "invite_revoked"
    statuses = {i["id"]: i["status"] for i in client.get("/api/invites", headers=h).json()["items"]}
    assert statuses == {a["id"]: "expired", b["id"]: "revoked"}


def test_role_ceiling(client, db):
    adm, sup = auth(make_user(db, "admin")), auth(make_user(db, "superadmin"))
    for role in ("operator", "agent", "recruit"):
        assert client.post("/api/invites", json={"role": role}, headers=adm).status_code == 200
    for role in ("admin", "superadmin"):
        r = client.post("/api/invites", json={"role": role}, headers=adm)
        assert r.status_code == 403, role
        assert client.post("/api/invites", json={"role": role}, headers=sup).status_code == 200
    assert client.post("/api/invites", json={"role": "boss"}, headers=sup).status_code == 400
    assert client.post("/api/invites", json={"role": "agent"}, headers=auth(make_user(db, "operator"))).status_code == 403
    assert client.get("/api/invites", headers=auth(make_user(db, "operator"))).status_code == 403
    sup_inv = client.get("/api/invites", headers=sup).json()["items"][0]
    assert sup_inv["role"] == "superadmin"
    assert client.post(f"/api/invites/{sup_inv['id']}/revoke", headers=adm).status_code == 403  # can't touch admin invites


def test_already_bound_tg(client, db):
    make_user(db, "agent", name="Заза Беридзе", tg_id=900)
    tok = token_of(invite(client, auth(make_user(db, "admin"))))
    r = client.post("/api/auth/invite", json={**widget(900), "token": tok})
    assert r.status_code == 409 and r.json()["code"] == "tg_already_bound" and "«Заза»" in r.json()["detail"]
    assert client.get(f"/api/auth/invite/{tok}").json()["status"] == "pending"  # still usable by the right person


def test_login_bot_start(client, db, monkeypatch):
    sent = []
    monkeypatch.setattr(login_bot, "reply", lambda chat, text: sent.append((chat, text)))
    monkeypatch.setattr(settings, "LOGIN_BOT_USERNAME", "HiG_login_bot")
    monkeypatch.setattr(settings, "LOGIN_BOT_TOKEN", "999:LOGIN")
    inv = invite(client, auth(make_user(db, "admin")), role="operator", works_as_agent=True)
    tok = token_of(inv)
    assert inv["bot_link"] == f"https://t.me/HiG_login_bot?start=inv_{tok}"
    upd = lambda text, uid=4242, kind="private": {"update_id": 1, "message": {  # noqa: E731
        "text": text, "chat": {"id": uid, "type": kind}, "from": {"id": uid, "first_name": "Ана", "username": "ana"}}}
    assert login_bot.handle(upd(f"/start inv_{tok}", kind="group")) is None
    out = login_bot.handle(upd(f"/start inv_{tok}"))
    assert "Готово, Ана" in out and "оператор" in out and sent[-1][0] == 4242
    u = db.query(User).filter(User.tg_id == 4242).one()
    assert u.role == "operator" and u.works_as_agent and u.username == "ana"
    assert "уже использовано" in login_bot.handle(upd(f"/start inv_{tok}", uid=4243))
    tok2 = token_of(invite(client, auth(make_user(db, "admin"))))
    assert "уже привязан" in login_bot.handle(upd(f"/start inv_{tok2}"))
    assert "бот входа" in login_bot.handle(upd("/start"))
    assert login_bot.handle(upd("привет")) is None
