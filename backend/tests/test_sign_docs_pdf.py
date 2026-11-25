"""«Документ на подпись» from a ready PDF (Irakli 11-18): the signer reads the uploaded file, the signed PDF is that file
byte-for-byte plus the signing record page after its last page."""
import base64
import io

import pytest
from pypdf import PdfReader, PdfWriter

from app.services import contracts as C, nda as N, sign_docs as D
from app.telegram import client as tgc
from tests.conftest import auth, make_user
from tests.test_nda import SIG


def _pdf(pages: int, text: str = "") -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(595, 842)
    w.add_metadata({"/Title": text or "dummy lease"})
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def _url(raw: bytes) -> str:
    return "data:application/pdf;base64," + base64.b64encode(raw).decode()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "render_pdf", lambda page: seen.append(page) or _pdf(1, "record"))
    monkeypatch.setattr(N, "_bg", lambda fn, *a: None)
    monkeypatch.setattr(tgc, "send", lambda chat, text, kb=None, **kw: {"message_id": 1})
    return seen


def _doc(**kw):
    return {"lang": "KA-RU", "title": {"ka": "იჯარის ხელშეკრულება", "ru": "Договор аренды"},
            "party": {"full_name": "Test Tenant", "personal_id": "01000000000", "phone": "+995 555 000 000"}, **kw}


def test_upload_sign_keeps_the_original(db, client, fakes):
    boss, op = make_user(db, "superadmin", tg_id=111), make_user(db, "operator", tg_id=222)
    src = _pdf(6)
    # PDF only, readable
    for bad in (_url(b"not a pdf at all"), "data:text/plain;base64,!!!", _url(b"%PDF-1.4 broken")):
        assert client.post("/api/sign-docs", json=_doc(pdf=bad), headers=auth(op)).status_code == 400
    r = client.post("/api/sign-docs", json=_doc(pdf=_url(src), pdf_name="lease.pdf"), headers=auth(op))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pdf"] == {"name": "lease.pdf", "pages": 6, "size": len(src)} and d["html"] == ""
    assert client.get(f"/api/sign-docs/{d['id']}/pdf", headers=auth(op)).content == src  # the draft = the file itself
    # editing the parties keeps the file (no text needed)
    r = client.put(f"/api/sign-docs/{d['id']}", json=_doc(party={**_doc()["party"], "full_name": "Test Tenant Two"}), headers=auth(op))
    assert r.status_code == 200 and r.json()["pdf"]["pages"] == 6, r.text

    client.put("/api/ndas/company-signature", json={"signature": SIG}, headers=auth(boss))
    d = client.post(f"/api/sign-docs/{d['id']}/send", headers=auth(op)).json()
    tok = d["signers"][0]["path"].split("/")[-1]
    v = client.get(f"/api/public/doc/{tok}").json()
    assert v["pdf"]["pages"] == 6 and not v["html"] and v["doc_sha256"] == D.hashlib.sha256(src).hexdigest()
    assert client.get(f"/api/public/doc/{tok}/pdf").content == src  # the signer reads the uploaded file
    r = client.post(f"/api/public/doc/{tok}", json={"signature": SIG, "consent": True, "doc_sha256": v["doc_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "signed", r.text

    out = client.get(f"/api/public/doc/{tok}/pdf").content
    assert out[:len(src)] == src  # the original bytes, untouched
    pages = PdfReader(io.BytesIO(out)).pages
    assert len(pages) == 7
    orig = PdfReader(io.BytesIO(src)).pages
    assert all(pages[i].get_contents() == orig[i].get_contents() and pages[i].mediabox == orig[i].mediabox for i in range(6))
    rec = fakes[-1]  # the added page: the signing record with the signature and the file's hash
    assert "Протокол электронного подписания" in rec and "lease.pdf" in rec and v["doc_sha256"] in rec and SIG in rec
    assert "Test Tenant Two" in rec


def test_password_and_size_limits(db, client, monkeypatch):
    op = make_user(db, "operator")
    w = PdfWriter()
    w.add_blank_page(595, 842)
    w.encrypt("secret")
    b = io.BytesIO()
    w.write(b)
    r = client.post("/api/sign-docs", json=_doc(pdf=_url(b.getvalue())), headers=auth(op))
    assert r.status_code == 400 and "паролем" in r.text
    monkeypatch.setattr(D, "MAX_PDF", 100)
    assert client.post("/api/sign-docs", json=_doc(pdf=_url(_pdf(1))), headers=auth(op)).status_code == 400


def _parties(**kw):
    return {"lang": "KA-RU", "mode": "parties", "title": {"ka": "ხელშეკრულება", "ru": "Договор аренды"},
            "signers": [{"role": "Собственник", "full_name": "Test Owner", "phone": "+995 555 111 111"},
                        {"role": "Клиент", "full_name": "Test Client", "phone": "+995 555 222 222", "personal_id": "01000000001"}], **kw}


def test_parties_two_outside_signers(db, client, fakes, monkeypatch):
    """«Договор между сторонами» (Irakli 11-18): no company signature (none is set here), a link per signer, the
    signed PDF = the original + a record page with both, sent to the author in Telegram."""
    sent = []
    monkeypatch.setattr(N, "_bg", lambda fn, *a: sent.append(a))
    irakli = make_user(db, "admin", tg_id=333)
    src = _pdf(3)
    r = client.post("/api/sign-docs", json=_parties(pdf=_url(src), pdf_name="lease.pdf"), headers=auth(irakli))
    assert r.status_code == 200, r.text
    d = client.post(f"/api/sign-docs/{r.json()['id']}/send", headers=auth(irakli)).json()
    assert d["parties"] and d["status"] == "sent", d
    assert [s["role"] for s in d["signers"]] == ["Собственник", "Клиент"]
    toks = [s["path"].split("/")[-1] for s in d["signers"]]
    assert len(set(toks)) == 2
    for i, tok in enumerate(toks):
        v = client.get(f"/api/public/doc/{tok}").json()
        assert not v["need_tg"]
        r = client.post(f"/api/public/doc/{tok}", json={"signature": SIG, "consent": True, "doc_sha256": v["doc_sha256"]})
        assert r.status_code == 200 and r.json()["status"] == ("signed" if i else "sent"), r.text
    out = client.get(f"/api/sign-docs/{d['id']}/pdf", headers=auth(irakli)).content
    assert out[:len(src)] == src and len(PdfReader(io.BytesIO(out)).pages) == 4
    rec = fakes[-1]
    assert "Test Owner" in rec and "Test Client" in rec and "Собственник" in rec and "Оператор" not in rec
    assert any(a[1] == 333 and a[4] == out for a in sent)  # the signed PDF → the author's Telegram


def test_parties_signer_needs_a_name_not_a_contact(db, client):
    """Irakli 11-25: the contact is optional; the name is not."""
    op = make_user(db, "operator")
    body = _parties(pdf=_url(_pdf(1)))
    body["signers"][1] = {"role": "Клиент", "full_name": "No Contact"}
    assert client.post("/api/sign-docs", json=body, headers=auth(op)).status_code == 200
    body["signers"][1] = {"role": "Клиент", "telegram": "@client"}
    assert client.post("/api/sign-docs", json=body, headers=auth(op)).status_code == 400  # no name


def test_agent_only_own_parties_docs(db, client):
    """From «Договоры» an agent sends «Договор между сторонами»; he sees only his own, not the company's documents."""
    ag, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    assert client.post("/api/sign-docs", json=_doc(pdf=_url(_pdf(1))), headers=auth(ag)).status_code == 403
    r = client.post("/api/sign-docs", json=_parties(pdf=_url(_pdf(1))), headers=auth(ag))
    assert r.status_code == 200, r.text
    mine = client.post("/api/sign-docs", json=_doc(pdf=_url(_pdf(1))), headers=auth(op)).json()
    assert [x["id"] for x in client.get("/api/sign-docs", headers=auth(ag)).json()["items"]] == [r.json()["id"]]
    assert client.get(f"/api/sign-docs/{mine['id']}", headers=auth(ag)).status_code == 404
    assert client.get(f"/api/sign-docs/{r.json()['id']}", headers=auth(other)).status_code == 404
    assert client.post(f"/api/sign-docs/{r.json()['id']}/send", headers=auth(ag)).json()["status"] == "sent"
    assert client.get("/api/sign-docs", headers=auth(make_user(db, "recruit"))).status_code == 403


def test_parties_signer_contacts_are_optional_and_a_phone_in_telegram_is_a_phone():
    """Irakli 11-25: «убери обязательный пункт номер телефона и тг»; he typed +995… into «Telegram»."""
    from app.services import sign_docs
    f = sign_docs._outside({"role": "Клиент", "full_name": "Миша", "telegram": "+995593531305"})
    assert (f["telegram"], f["phone"]) == (None, "+995593531305")
    assert sign_docs._outside({"full_name": "Нино"})["phone"] is None
    assert sign_docs._outside({"full_name": "Лука", "telegram": "@luka_tb"})["telegram"] == "luka_tb"
