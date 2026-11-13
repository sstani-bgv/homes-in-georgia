"""NDA for new agents (services/nda.py): the company signature once, staff make an NDA, the agent confirms himself with
Telegram (Login Widget HMAC; only the named @username), signs, the signed PDF is printed and sent."""
import base64
import hashlib
import hmac
import struct
import time
import zlib

import pytest

from app.models import AgentNda, Job
from app.services import contracts as C, nda as N
from tests.conftest import auth, make_user

PDF = b"%PDF-1.4 nda"


def _png(w=40, h=20) -> str:
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)  # noqa: E731
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


SIG = _png()


def _widget(**f):
    """A Telegram Login Widget answer signed like Telegram does (key = SHA256(bot token))."""
    f = {"id": "777001", "first_name": "Nino", "username": "nino_agent", "auth_date": str(int(time.time())), **f}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    f["hash"] = hmac.new(hashlib.sha256(b"123456:TEST-TOKEN").digest(), dcs.encode(), hashlib.sha256).hexdigest()
    return f


@pytest.fixture(autouse=True)
def fake_chrome(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "render_pdf", lambda page: seen.append(page) or PDF)
    return seen


@pytest.fixture(autouse=True)
def tg_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(N.httpx, "post", lambda url, **kw: sent.append((url, kw["data"]["chat_id"], kw["files"]["document"][0])))
    return sent


def test_full_flow(db, client, fake_chrome, tg_sent):
    boss, op = make_user(db, "superadmin", tg_id=111), make_user(db, "operator", tg_id=222)
    ag = make_user(db, "agent")
    body = {"lang": "KA-RU", "full_name": "Nino *Test*", "personal_id": "01001055555", "phone": "+995 555 11 22 33",
            "telegram": "@Nino_Agent", "role": "Агент"}
    # no company signature yet; only a superadmin sets it; agents can't make NDAs
    assert client.post("/api/ndas", json=body, headers=auth(op)).status_code == 409
    assert client.put("/api/ndas/company-signature", json={"signature": SIG}, headers=auth(op)).status_code == 403
    assert client.put("/api/ndas/company-signature", json={"signature": SIG}, headers=auth(boss)).status_code == 200
    assert client.post("/api/ndas", json=body, headers=auth(ag)).status_code == 403
    r = client.post("/api/ndas", json=body, headers=auth(op))
    assert r.status_code == 200, r.text
    n = r.json()
    assert n["status"] == "sent" and n["telegram"] == "Nino_Agent" and n["full_name"] == "Nino Test"
    assert "Nino Test" in n["html"] and "01001055555" in n["html"] and "@Nino_Agent" in n["html"]
    assert "Получатель" in n["html"] and "მიმღები" in n["html"] and "рус" in n["html"]  # two columns + the language clause
    token = n["path"].split("/")[-1]

    v = client.get(f"/api/public/nda/{token}").json()  # no login
    assert v["confirmed"] is False and v["html"]
    sign = {"signature": SIG, "consent": True, "doc_sha256": v["doc_sha256"]}
    assert client.post(f"/api/public/nda/{token}", json=sign).status_code == 403  # Telegram first
    # a forged / other account's confirmation is refused
    bad = _widget()
    bad["hash"] = "0" * 64
    assert client.post(f"/api/public/nda/{token}/telegram", json=bad).status_code == 401
    assert client.post(f"/api/public/nda/{token}/telegram", json=_widget(username="someone_else")).status_code == 403
    assert client.post(f"/api/public/nda/{token}/telegram", json={"test": True}).status_code == 401  # not the test server
    r = client.post(f"/api/public/nda/{token}/telegram", json=_widget())
    assert r.status_code == 200 and r.json()["confirmed"] and r.json()["tg_username"] == "nino_agent"
    assert client.post(f"/api/public/nda/{token}", json={**sign, "consent": False}).status_code == 400
    assert client.post(f"/api/public/nda/{token}", json={**sign, "doc_sha256": "x" * 64}).status_code == 409

    r = client.post(f"/api/public/nda/{token}", json=sign, headers={"x-forwarded-for": "9.9.9.9", "user-agent": "UA-nda"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "signed" and r.json()["html"] is None and r.json()["has_pdf"]
    page = fake_chrome[-1]
    assert page.count(f'<img src="{SIG}"') == 6  # company + agent in both language tables, + both on the record page
    assert "9.9.9.9" in page and "UA-nda" in page and "777001" in page and "Протокол электронного подписания" in page
    assert "[эл. подпись]" not in page
    # sent to the staff member who made it and to the agent; Drive queued
    assert {chat for _, chat, _ in tg_sent} == {"222", "777001"}
    assert db.query(Job).filter_by(kind="nda_drive", ref=str(n["id"])).count() == 1
    assert client.get(f"/api/public/nda/{token}/pdf").content == PDF
    assert client.get(f"/api/ndas/{n['id']}/pdf", headers=auth(op)).content == PDF
    assert client.post(f"/api/public/nda/{token}", json=sign).status_code == 409  # once
    lst = client.get("/api/ndas", headers=auth(op)).json()
    assert lst["total"] == 1 and lst["items"][0]["status_label"] == "Подписано"


def test_any_account_when_no_username_cancel_and_languages(db, client):
    boss = make_user(db, "superadmin")
    client.put("/api/ndas/company-signature", json={"signature": SIG}, headers=auth(boss))
    for lang, needle, absent in (("KA", "მიმღები", "Получатель"), ("KA-EN", "Recipient", "Получатель")):
        n = client.post("/api/ndas", json={"lang": lang, "full_name": "A", "personal_id": "1"}, headers=auth(boss)).json()
        assert needle in n["html"] and absent not in n["html"]
    n = client.post("/api/ndas", json={"lang": "KA", "full_name": "A", "personal_id": "1"}, headers=auth(boss)).json()
    token = n["path"].split("/")[-1]
    assert client.post(f"/api/public/nda/{token}/telegram", json=_widget(username="whoever")).status_code == 200
    assert client.post(f"/api/ndas/{n['id']}/cancel", headers=auth(boss)).json()["status"] == "cancelled"
    assert client.get(f"/api/public/nda/{token}").status_code == 404
    assert client.post("/api/ndas", json={"lang": "XX", "full_name": "A", "personal_id": "1"}, headers=auth(boss)).status_code == 400


def test_test_mode_confirmation_and_pdf_retry(db, client, monkeypatch):
    from app import worker
    from app.services.errors import DomainError
    boss = make_user(db, "superadmin")
    client.put("/api/ndas/company-signature", json={"signature": SIG}, headers=auth(boss))
    n = client.post("/api/ndas", json={"lang": "KA-EN", "full_name": "B", "personal_id": "2", "telegram": "b_agent"},
                    headers=auth(boss)).json()
    token = n["path"].split("/")[-1]
    monkeypatch.setattr(N, "_test_mode", lambda: True)
    v = client.post(f"/api/public/nda/{token}/telegram", json={"test": True}).json()
    assert v["confirmed"] and v["tg_username"] == "b_agent"

    def boom(page):
        raise DomainError("pdf_failed", "x", 502)
    monkeypatch.setattr(C, "render_pdf", boom)
    r = client.post(f"/api/public/nda/{token}", json={"signature": SIG, "consent": True, "doc_sha256": v["doc_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "signed" and not r.json()["has_pdf"]
    job = db.query(Job).filter_by(kind="nda_pdf", ref=str(n["id"])).one()
    monkeypatch.setattr(C, "render_pdf", lambda page: PDF)
    assert worker.run_job(db, job) == "ok"
    db.expire_all()
    assert db.get(AgentNda, n["id"]).pdf_path
