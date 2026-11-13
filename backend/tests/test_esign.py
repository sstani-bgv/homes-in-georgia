"""Electronic signing of a contract (services/esign.py): links for the owner and the tenant, the public page, the
signature, the signed PDF once both have signed, links voided by an edit, a signed text frozen, cancel."""
import base64
import itertools
import struct
import zlib

import pytest

from app.models import Contract, ContractSigner, Job, Lead, User
from app.services import contracts as C, esign as E
from app.services.common import now_text
from tests.conftest import auth, make_client, make_listing, make_user

PDF = b"%PDF-1.4 generated"
FOOTER = "@bottom-center{content:\"“გამქირავებელი“ _______________                “დამქირავებელი“ _______________\";font:8.5pt Arial}"
PAGE = ("<!doctype html><html lang='ka'><head><meta charset='utf-8'><style>@media print{@page{size:A4;" + FOOTER + "}}</style>"
        "</head><body><article class='contract-doc'><div class='cd-body'><p class='cd-p'>ხელშეკრულება</p>"
        "<p class='cd-p'>2.8. ანგარიში:  _____________________________</p>"
        "<p class='cd-p'><b>“გამქირავებელი“/\"Owner\"________________________________________</b></p>"
        "<p class='cd-p'><b>“დამქირავებელი“/\"Tenant\" ______________________________________</b></p>"
        "<p class='cd-p'>Payment</p></div></article></body></html>")


def _png(w=40, h=20) -> str:
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)  # noqa: E731
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


SIG = _png()


@pytest.fixture(autouse=True)
def fake_chrome(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "render_pdf", lambda page: seen.append(page) or PDF)
    return seen


@pytest.fixture(autouse=True)
def tg_calls(monkeypatch):
    from app.telegram import client as tg
    calls = []
    monkeypatch.setattr(tg, "call", lambda method, files=None, **kw: calls.append((method, kw)) or {"ok": True})
    return calls


_n = itertools.count()


def _lead(db, agent):
    # a contract is tied to a lead or deal (contracts._fields: no_deal); one fresh lead per contract
    c = make_client(db, external_id=910000 + next(_n))
    lead = Lead(client_id=c.id, listing_id=make_listing(db, f"es{c.id}").id, agent_id=agent.id, status="agent_assigned",
                created_at=now_text(), updated_at=now_text())
    db.add(lead)
    db.commit()
    return lead


def _contract(db, client, user, **kw):
    body = {"lead_id": _lead(db, user).id, "kind": "rent", "lang": "EN", "html": PAGE, "cadastral": "01.14.12.003.045", "client_name": "Anna",
            "owner_name": "Giorgi", "data": {"owner_full_name": "Giorgi Beridze", "owner_personal_number": "01001012345",
                                             "client_full_name": "Anna Smith"}, **kw}
    r = client.post("/api/contracts", json=body, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _start(client, user, cid):
    r = client.post(f"/api/contracts/{cid}/esign", headers=auth(user))
    assert r.status_code == 200, r.text
    es = r.json()["esign"]
    return es, {s["role"]: s["path"].split("/")[-1] for s in es["signers"]}


def _sign(client, token, **kw):
    v = client.get(f"/api/public/esign/{token}").json()
    body = {"full_name": "Name", "personal_id": "01001000001", "phone": "+995 555 00 00 00", "consent": True,
            "signature": SIG, "doc_sha256": v["doc_sha256"], "lang": "ka", **kw}
    return client.post(f"/api/public/esign/{token}", json=body, headers={"user-agent": "UA-test", "x-forwarded-for": "5.6.7.8"})


def test_both_parties_sign_then_signed_pdf(db, client, fake_chrome, tg_calls):
    ag = make_user(db, "agent", tg_id=4242)
    c = _contract(db, client, ag)
    assert c["esign"] is None
    es, tok = _start(client, ag, c["id"])
    assert [s["role"] for s in es["signers"]] == ["owner", "tenant"] and not es["complete"]
    assert all(len(t) >= 30 for t in tok.values())
    # starting again keeps the same links
    assert _start(client, ag, c["id"])[1] == tok

    v = client.get(f"/api/public/esign/{tok['owner']}").json()  # no login
    assert v["role"] == "owner" and v["prefill"] == {"full_name": "Giorgi Beridze", "personal_id": "01001012345"}
    assert v["html"] == db.get(Contract, c["id"]).html and not v["complete"]
    assert client.get(f"/api/public/esign/{tok['tenant']}").json()["prefill"]["full_name"] == "Anna Smith"

    # checks: consent, fields, a real PNG, the text the party read
    assert _sign(client, tok["owner"], consent=False).status_code == 400
    assert _sign(client, tok["owner"], phone="").status_code == 400
    assert _sign(client, tok["owner"], signature="data:image/png;base64,AAAA").status_code == 400
    assert _sign(client, tok["owner"], signature=_png(6, 360)).status_code == 400  # an empty pad cropped to a sliver
    assert _sign(client, tok["owner"], doc_sha256="0" * 64).status_code == 409

    r = _sign(client, tok["owner"], full_name="Giorgi  Beridze")
    assert r.status_code == 200, r.text
    assert r.json()["signed_at"] and r.json()["html"] is None and not r.json()["complete"]
    assert _sign(client, tok["owner"]).status_code == 409  # once
    assert tg_calls[-1][0] == "sendMessage" and tg_calls[-1][1]["chat_id"] == 4242
    got = client.get(f"/api/contracts/{c['id']}", headers=auth(ag)).json()
    assert got["status"] == "draft" and got["esign"]["signers"][0]["full_name"] == "Giorgi Beridze"
    assert client.get(f"/api/public/esign/{tok['owner']}/pdf").status_code == 404  # not yet

    r = _sign(client, tok["tenant"], full_name="Anna Smith").json()
    assert r["complete"] is True
    page = fake_chrome[-1]
    # signatures in the parties' block (the last two long lines), not in the bank-account line; the footer; the record
    assert page.count(f'<img class="esig" src="{SIG}"') == 4  # 2 in the block + 2 on the record page
    assert "ანგარიში:  _____" in page
    assert "url(data:image/svg+xml;base64," in page and "Electronic signature record" in page
    assert "Giorgi Beridze" in page and "5.6.7.8" in page and "UA-test" in page and E.doc_hash(db.get(Contract, c["id"])) in page

    got = client.get(f"/api/contracts/{c['id']}", headers=auth(ag)).json()
    assert got["status"] == "signed" and got["has_signed_pdf"] and got["esign"]["complete"]
    assert all(s["path"] is None for s in got["esign"]["signers"])
    assert db.query(Job).filter_by(kind="contract_drive", ref=str(c["id"])).count() == 1
    assert tg_calls[-1][0] == "sendDocument" and tg_calls[-1][1]["chat_id"] == 4242
    # each party downloads its copy by its link
    pdf = client.get(f"/api/public/esign/{tok['tenant']}/pdf")
    assert pdf.status_code == 200 and pdf.content == PDF
    assert client.get(f"/api/public/esign/{tok['tenant']}").json()["complete"] is True


def test_edit_voids_links_and_signed_text_is_frozen(db, client):
    ag = make_user(db, "agent")
    c = _contract(db, client, ag)
    _, tok = _start(client, ag, c["id"])
    # an edit before anyone signed: the old links die
    edited = PAGE.replace("Payment", "Payment / გადახდა")
    assert client.put(f"/api/contracts/{c['id']}", json={"kind": "rent", "lang": "EN", "html": edited, "lead_id": c["lead_id"]}, headers=auth(ag)).status_code == 200
    assert client.get(f"/api/public/esign/{tok['owner']}").status_code == 404
    assert db.query(ContractSigner).count() == 0
    # new links; after one signature the text can't change until the signing is cancelled
    _, tok = _start(client, ag, c["id"])
    assert _sign(client, tok["owner"]).status_code == 200
    r = client.put(f"/api/contracts/{c['id']}", json={"kind": "rent", "lang": "EN", "html": PAGE, "lead_id": c["lead_id"]}, headers=auth(ag))
    assert r.status_code == 409
    r = client.post(f"/api/contracts/{c['id']}/esign/cancel", headers=auth(ag))
    assert r.status_code == 200 and r.json()["esign"] is None
    assert client.get(f"/api/public/esign/{tok['tenant']}").status_code == 404
    assert client.put(f"/api/contracts/{c['id']}", json={"kind": "rent", "lang": "EN", "html": PAGE, "lead_id": c["lead_id"]}, headers=auth(ag)).status_code == 200


def test_access_and_states(db, client):
    ag, other = make_user(db, "agent"), make_user(db, "agent")
    c = _contract(db, client, ag)
    assert client.post(f"/api/contracts/{c['id']}/esign", headers=auth(other)).status_code == 404
    assert client.get("/api/public/esign/not-a-real-token-at-all-xxxxx").status_code == 404
    sale = _contract(db, client, ag, kind="sale")
    assert client.post(f"/api/contracts/{sale['id']}/esign", headers=auth(ag)).status_code == 409
    _, tok = _start(client, ag, c["id"])
    # closed: the links stop working
    client.post(f"/api/contracts/{c['id']}/close", json={"reason": "deal_failed"}, headers=auth(ag))
    assert client.get(f"/api/public/esign/{tok['owner']}").status_code == 404
    # marked signed on paper: the page opens but signing is refused
    c2 = _contract(db, client, ag)
    _, tok2 = _start(client, ag, c2["id"])
    r = client.post(f"/api/contracts/{c2['id']}/sign", headers=auth(ag))
    assert r.status_code == 200, r.text
    assert client.get(f"/api/public/esign/{tok2['owner']}").status_code == 200
    assert _sign(client, tok2["owner"]).status_code == 409
    assert client.post(f"/api/contracts/{c2['id']}/esign", headers=auth(ag)).status_code == 409


def test_pdf_failure_is_retried_by_the_worker(db, client, monkeypatch):
    from app import worker
    from app.services.errors import DomainError
    ag = make_user(db, "agent")
    c = _contract(db, client, ag)
    _, tok = _start(client, ag, c["id"])
    _sign(client, tok["owner"])

    def boom(page):
        raise DomainError("pdf_failed", "x", 502)
    monkeypatch.setattr(C, "render_pdf", boom)
    r = _sign(client, tok["tenant"])
    assert r.status_code == 200 and r.json()["signed_at"] and not r.json()["complete"]
    db.expire_all()
    assert db.get(Contract, c["id"]).status == "draft"
    job = db.query(Job).filter_by(kind="contract_esign_pdf", ref=str(c["id"])).one()
    monkeypatch.setattr(C, "render_pdf", lambda page: PDF)
    assert worker.run_job(db, job) == "ok"
    db.expire_all()
    assert db.get(Contract, c["id"]).status == "signed"


def test_signed_html_without_marks_still_gets_the_record(db):
    ag = make_user(db, "agent")
    c = Contract(kind="rent", lang="RU", html="<html><head></head><body><p>text</p></body></html>", created_by=ag.id)
    db.add(c)
    db.flush()
    rows = [ContractSigner(contract_id=c.id, role=r, token=f"t-{r}-xxxxxxxxxxxxxxxxxxxx", doc_sha256="h", full_name="<b>X</b>",
                           personal_id="1", phone="2", signature=SIG, signed_at=C.now()) for r in E.ROLES]
    out = E.signed_html(c, rows)
    assert "Протокол электронного подписания" in out and "&lt;b&gt;X&lt;/b&gt;" in out and "<b>X</b>" not in out
    db.rollback()
    assert db.query(User).count() == 1


def test_extract_with_the_signing_link(db, client, monkeypatch):
    # Irakli 11-13: the registry extract goes with the e-sign link — only by a signer token of that contract
    from app import storage
    key = "private/registry-extracts/" + "a" * 64 + ".pdf"
    monkeypatch.setattr(storage, "read", lambda k: b"%PDF extract" if k == key else None)
    ag = make_user(db, "agent")
    c = _contract(db, client, ag, data={"extract_file": key})
    _, tok = _start(client, ag, c["id"])
    other = _contract(db, client, ag)
    _, tok2 = _start(client, ag, other["id"])
    assert client.get(f"/api/public/esign/{tok['tenant']}").json()["has_extract"] is True
    r = client.get(f"/api/public/esign/{tok['tenant']}/extract")
    assert r.status_code == 200 and r.content == b"%PDF extract"
    assert client.get(f"/api/public/esign/{'x' * 32}/extract").status_code == 404  # a wrong token
    assert client.get(f"/api/public/esign/{tok2['owner']}/extract").status_code == 404  # no extract on that contract
    assert client.get(f"/api/public/esign/{tok2['owner']}").json()["has_extract"] is False
