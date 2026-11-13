"""«Отправить клиенту в Telegram» (11-13): the contract PDF + the registry extract from the operator's account. The
userbot is mocked: httpx.post for the API side, a fake Telethon client for the userbot side. Nothing goes to Telegram."""
import asyncio
import base64

import httpx
import pytest

from app.models import Client
from app.services import registry as R
from app.telegram import userbot
from tests.conftest import auth, make_user
from tests.test_contracts import PDF, _lead, _new, fake_chrome  # noqa: F401 — autouse: no Chrome


@pytest.fixture
def posts(monkeypatch):
    got = []
    monkeypatch.setenv("USERBOT_URL", "http://userbot:8091/")
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None: got.append((url, json, headers))
                        or type("R", (), {"status_code": 200, "json": lambda self: {"ok": True, "message_id": 7}})())
    return got


def test_send_to_the_leads_client_with_the_extract(db, client, posts):
    ag, other = make_user(db, "agent"), make_user(db, "agent")
    lead = _lead(db, ag)
    key = R.store_extract(b"%PDF-1.4 extract")
    c = _new(client, ag, lead_id=lead.id, address="Ваке, Чавчавадзе 10", data={"extract_file": key})
    assert c["client_tg"] == {"label": "Анна (@anna_demo)"} and c["sent_to_client"] is None
    assert client.post(f"/api/contracts/{c['id']}/send-client", json={}, headers=auth(other)).status_code == 404  # not his
    assert not posts
    r = client.post(f"/api/contracts/{c['id']}/send-client", json={}, headers=auth(ag))
    assert r.status_code == 200, r.text
    url, body, headers = posts[0]
    assert url == "http://userbot:8091/send" and headers["X-Userbot-Token"] and body["client_id"] == lead.client_id and not body["peer"]
    assert "Договор аренды — Ваке, Чавчавадзе 10 во вложении, вместе с выпиской" in body["text"]
    assert [base64.b64decode(f["b64"]) for f in body["files"]] == [PDF, b"%PDF-1.4 extract"]
    assert body["files"][1]["name"].startswith("Выписка из реестра")
    sent = r.json()["sent_to_client"]
    assert sent["to"] == "Анна (@anna_demo)" and sent["version"] == 1 and sent["extract"] is True and sent["at"] and sent["by"]
    # re-send (the UI confirms first) after a regeneration: the next version
    client.put(f"/api/contracts/{c['id']}", json={"kind": "rent", "lang": "RU", "html": "<p>v2</p>", "lead_id": lead.id}, headers=auth(ag))
    assert client.post(f"/api/contracts/{c['id']}/send-client", json={}, headers=auth(ag)).json()["sent_to_client"]["version"] == 2
    staff = make_user(db, "operator")
    assert client.post(f"/api/contracts/{c['id']}/send-client", json={}, headers=auth(staff)).status_code == 200


def test_no_lead_needs_a_recipient_and_errors(db, client, posts, monkeypatch):
    ag = make_user(db, "agent")
    c = _new(client, ag)  # no lead, no extract
    assert c["client_tg"] is None
    r = client.post(f"/api/contracts/{c['id']}/send-client", json={}, headers=auth(ag))
    assert r.status_code == 409 and "укажите @username или телефон" in r.text
    assert client.post(f"/api/contracts/{c['id']}/send-client", json={"to": "hello there"}, headers=auth(ag)).status_code == 400
    r = client.post(f"/api/contracts/{c['id']}/send-client", json={"to": "https://t.me/Nino_K"}, headers=auth(ag))
    assert r.status_code == 200 and posts[-1][1]["peer"] == "@Nino_K" and len(posts[-1][1]["files"]) == 1
    assert "выпиской" not in posts[-1][1]["text"]
    client.post(f"/api/contracts/{c['id']}/send-client", json={"to": "555 12 34 56"}, headers=auth(ag))
    assert posts[-1][1]["peer"] == "+995555123456"
    # the userbot not configured / failed: the viewing-slots texts
    monkeypatch.setenv("USERBOT_URL", "")
    r = client.post(f"/api/contracts/{c['id']}/send-client", json={"to": "@Nino_K"}, headers=auth(ag))
    assert r.status_code == 409 and "Userbot оператора не подключён" in r.text
    monkeypatch.setenv("USERBOT_URL", "http://userbot:8091")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: type("R", (), {"status_code": 503, "json": lambda self: {"ok": False}})())
    r = client.post(f"/api/contracts/{c['id']}/send-client", json={"to": "@Nino_K"}, headers=auth(ag))
    assert r.status_code == 502 and "не смог отправить" in r.text
    # a stale PDF is not sent
    client.post("/api/contracts/draft", json={"id": c["id"], "kind": "rent", "data": {"x": "changed"}}, headers=auth(ag))
    assert client.get(f"/api/contracts/{c['id']}", headers=auth(ag)).json()["pdf_stale"]
    assert client.post(f"/api/contracts/{c['id']}/send-client", json={"to": "@Nino_K"}, headers=auth(ag)).status_code == 409


def test_a_non_telegram_client_by_phone(db, client, posts):
    ag = make_user(db, "agent")
    lead = _lead(db, ag)
    cl = db.get(Client, lead.client_id)
    cl.channel, cl.phone = "legacy", "+995 599 11 22 33"
    db.commit()
    c = _new(client, ag, lead_id=lead.id)
    assert c["client_tg"]["label"] == "Анна (+995599112233)"
    client.post(f"/api/contracts/{c['id']}/send-client", json={}, headers=auth(ag))
    assert posts[-1][1]["peer"] == "+995599112233" and not posts[-1][1]["client_id"]


class FakeTelethon:
    def __init__(self):
        self.files, self.imported = [], []

    async def get_input_entity(self, peer):
        if isinstance(peer, str) and peer.startswith("+"):
            raise ValueError("not a contact")
        return peer if not isinstance(peer, str) else 4242

    async def __call__(self, req):
        if type(req).__name__ == "ImportContactsRequest":
            self.imported.append(req.contacts[0].phone)
            return type("Res", (), {"users": [777]})()
        return []  # GetRequirementsToContact: nothing required

    async def send_file(self, entity, f, caption=None, force_document=False):
        self.files.append((entity, f.name, f.read(), caption, force_document))
        return type("Msg", (), {"id": 50 + len(self.files)})()


def test_userbot_sends_documents_with_the_caption_on_the_first(db):
    fake = FakeTelethon()
    files = [{"name": "a.pdf", "b64": base64.b64encode(b"A").decode()}, {"name": "b.pdf", "b64": base64.b64encode(b"B").decode()}]
    mid = asyncio.run(userbot.send(fake, None, "Здравствуйте!", peer="@nino_k", files=files))
    assert mid == 51 and fake.files == [(4242, "a.pdf", b"A", "Здравствуйте!", True), (4242, "b.pdf", b"B", None, True)]
    asyncio.run(userbot.send(fake, None, "Hi", peer="+995599112233", files=files[:1]))   # a phone: imported as a contact
    assert fake.imported == ["+995599112233"] and fake.files[-1][0] == 777
    with pytest.raises(ValueError):
        asyncio.run(userbot.send(fake, None, "text only", peer="@nino_k"))
