"""Stored contracts: lifecycle draft → signed → deal | closed_no_deal, close with a reason, share link expiry,
Drive upload queued / skipped, the HTML cleaning before Chrome prints it."""
import json
from datetime import timedelta

import pytest

from app.models import Contract, Job, Lead
from app.services import contracts as C, deals
from app.services.common import now, now_text
from app.services.errors import DomainError
from tests.conftest import auth, make_client, make_listing, make_user

PDF = b"%PDF-1.4 generated"
SCAN = b"%PDF-1.4 signed scan"
PAGE = "<!doctype html><html><head><meta charset='utf-8'><style>.x{color:red}</style></head><body><p>ხელშეკრულება</p></body></html>"


@pytest.fixture(autouse=True)
def fake_chrome(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "render_pdf", lambda page: seen.append(page) or PDF)
    return seen


def _lead(db, agent):
    c = make_client(db, external_id=900000 + agent.id)  # one lead per agent in a test
    lead = Lead(client_id=c.id, listing_id=make_listing(db, f"cx{agent.id}").id, agent_id=agent.id, status="agent_assigned",
                created_at=now_text(), updated_at=now_text())
    db.add(lead)
    db.commit()
    return lead


def _new(client, user, **kw):
    body = {"kind": "rent", "lang": "GEO", "html": PAGE, "cadastral": "01.14.12.003.045", "client_name": "Анна", **kw}
    r = client.post("/api/contracts", json=body, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_create_list_pdf_and_lead_summary(db, client):
    ag, other = make_user(db, "agent"), make_user(db, "agent")
    lead = _lead(db, ag)
    c = _new(client, ag, lead_id=lead.id)
    assert c["status"] == "draft" and c["status_label"] == "Сгенерирован" and c["lead_id"] == lead.id
    r = client.get(f"/api/contracts/{c['id']}/pdf", headers=auth(ag))
    assert r.content == PDF and r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")
    assert client.get(f"/api/contracts/{c['id']}/pdf?download=1", headers=auth(ag)).headers["content-disposition"].startswith("attachment")
    # visibility: the creator / the lead's agent / staff
    assert client.get(f"/api/contracts/{c['id']}", headers=auth(other)).status_code == 404
    assert client.get("/api/contracts", headers=auth(other)).json()["total"] == 0
    assert client.get("/api/contracts", headers=auth(make_user(db, "operator"))).json()["total"] == 1
    # the lead payloads carry the contract status
    assert client.get(f"/api/leads/{lead.id}", headers=auth(ag)).json()["contract"]["status"] == "draft"
    assert client.get(f"/api/leads/{lead.id}/contract", headers=auth(ag)).json()["contract"]["id"] == c["id"]
    # draft is editable, a signed one is not
    assert client.put(f"/api/contracts/{c['id']}", json={"kind": "rent", "lang": "RU", "html": PAGE, "lead_id": lead.id}, headers=auth(ag)).json()["lang"] == "RU"
    client.post(f"/api/contracts/{c['id']}/sign", headers=auth(ag))
    assert client.put(f"/api/contracts/{c['id']}", json={"kind": "rent", "lang": "RU", "html": PAGE, "lead_id": lead.id}, headers=auth(ag)).status_code == 409


def test_status_transitions_sign_deal(db, client):
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    lead = _lead(db, ag)
    c = _new(client, ag, lead_id=lead.id)
    r = client.post(f"/api/contracts/{c['id']}/sign", files={"file": ("s.pdf", b"not a pdf", "application/pdf")}, headers=auth(ag))
    assert r.status_code == 400
    r = client.post(f"/api/contracts/{c['id']}/sign", files={"file": ("s.pdf", SCAN, "application/pdf")}, headers=auth(ag)).json()
    assert r["status"] == "signed" and r["has_signed_pdf"] and r["signed_by"]["id"] == ag.id
    assert client.get(f"/api/contracts/{c['id']}/pdf?signed=1", headers=auth(ag)).content == SCAN
    assert client.post(f"/api/contracts/{c['id']}/sign", headers=auth(ag)).status_code == 409  # signed again, no file
    # the operator confirms the deal → the lead's contract becomes «deal»
    lead.status = "viewing_done"
    db.commit()
    deal = deals.create(db, {"lead_id": lead.id, "price": 900, "rent_from": "2026-12-01", "rent_to": "2027-12-01"}, ag)
    deals.confirm(db, deal.id, op)
    got = client.get(f"/api/contracts/{c['id']}", headers=auth(ag)).json()
    assert got["status"] == "deal" and got["deal_id"] == deal.id
    assert client.post(f"/api/contracts/{c['id']}/close", json={"reason": "deal_failed"}, headers=auth(ag)).status_code == 409


def test_close_without_deal_needs_reason(db, client):
    ag, op = make_user(db, "agent", name="Гиорги"), make_user(db, "operator")
    c = _new(client, ag, lead_id=_lead(db, ag).id)
    assert client.post(f"/api/contracts/{c['id']}/close", json={"reason": ""}, headers=auth(ag)).status_code == 400
    assert client.post(f"/api/contracts/{c['id']}/close", json={"reason": "other"}, headers=auth(ag)).status_code == 400
    r = client.post(f"/api/contracts/{c['id']}/close", json={"reason": "other", "comment": "уехал"}, headers=auth(ag)).json()
    assert (r["status"], r["status_label"], r["close_reason_label"], r["close_comment"]) == \
        ("closed_no_deal", "Сделка не состоялась", "Другое", "уехал")
    assert r["closed_by"]["name"] == "Гиорги" and r["closed_at"]
    # gone from the active lists, kept in the history and filterable for staff
    for u in (ag, op):
        assert client.get("/api/contracts", headers=auth(u)).json()["total"] == 0
    hist = client.get("/api/contracts?status=closed_no_deal", headers=auth(op)).json()["items"]
    assert [x["id"] for x in hist] == [c["id"]]
    assert client.get("/api/contracts?status=all", headers=auth(ag)).json()["total"] == 1
    assert client.post(f"/api/contracts/{c['id']}/sign", headers=auth(ag)).status_code == 409


def test_share_link_expires(db, client):
    ag = make_user(db, "agent")
    c = _new(client, ag, lead_id=_lead(db, ag).id)
    s = client.post(f"/api/contracts/{c['id']}/share", headers=auth(ag)).json()["share"]
    assert s["path"].startswith("/api/public/contracts/") and len(s["path"].split("/")[4]) >= 30
    r = client.get(s["path"])  # no login
    assert r.status_code == 200 and r.content == PDF and r.headers["x-robots-tag"] == "noindex"
    assert client.post(f"/api/contracts/{c['id']}/share", headers=auth(ag)).json()["share"]["path"] == s["path"]  # reused
    assert client.get("/api/public/contracts/not-a-real-token-at-all-xx/pdf").status_code == 404
    row = db.get(Contract, c["id"])
    row.share_expires_at = now() - timedelta(minutes=1)
    db.commit()
    assert client.get(s["path"]).status_code == 404
    assert client.post(f"/api/contracts/{c['id']}/share", headers=auth(ag)).json()["share"]["path"] != s["path"]  # renewed


def test_drive_upload_queued_and_skipped(db, client, monkeypatch, tmp_path, caplog):
    from app import worker
    from app.config import settings
    ag = make_user(db, "agent")
    c = _new(client, ag, lead_id=_lead(db, ag).id)
    client.post(f"/api/contracts/{c['id']}/sign", headers=auth(ag))
    job = db.query(Job).filter_by(kind="contract_drive", ref=str(c["id"])).one()
    assert job.done_at is None
    # no credentials: skipped with a log line
    monkeypatch.setattr(settings, "GOOGLE_SA_JSON", "")
    monkeypatch.setattr(C.log, "disabled", False)  # alembic's fileConfig disables loggers that existed before it
    with caplog.at_level("INFO", logger="app.services.contracts"):
        assert worker.run_job(db, job) == "skipped"
    assert "drive: not configured" in caplog.text
    # configured: uploads the PDF (the scan when there is one) under the cadastral-code name
    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"client_email": "x@y", "private_key": "k", "token_uri": "https://t"}))
    monkeypatch.setattr(settings, "GOOGLE_SA_JSON", str(sa))
    put = []
    monkeypatch.setattr(C, "drive_put", lambda path, name, data: put.append((name, data)) or "drive-id-1")
    db.expire_all()
    job2 = Job(kind="contract_drive", ref=str(c["id"]), due_at=now(), payload={})
    db.add(job2)
    db.commit()
    assert worker.run_job(db, job2) == "ok"
    assert put[0][0].startswith("01.14.12.003.045 ") and put[0][0].endswith("Анна.pdf") and put[0][1] == PDF
    db.expire_all()
    assert db.get(Contract, c["id"]).drive_file_id == "drive-id-1"
    assert client.get(f"/api/contracts/{c['id']}", headers=auth(ag)).json()["in_drive"] is True
    # an upload error is retried by the worker
    monkeypatch.setattr(C, "drive_put", lambda *a: (_ for _ in ()).throw(RuntimeError("drive upload 500")))
    row = db.get(Contract, c["id"])
    row.drive_file_id = None
    job3 = Job(kind="contract_drive", ref=str(c["id"]), due_at=now(), payload={})
    db.add(job3)
    db.commit()
    assert worker.run_job(db, job3) == "error"
    db.expire_all()
    job3 = db.get(Job, job3.id)
    assert job3.done_at is None and job3.attempts == 1 and "drive upload 500" in job3.last_error


def test_clean_html_drops_file_and_network_reads():
    dirty = ('<html><head><meta http-equiv="refresh" content="0;url=file:///etc/passwd"><link rel=stylesheet href="http://x/a.css">'
             '<style>@import "http://x"; .a{background:url(file:///app/.env)} .b{background:u\\72l(x)}</style></head>'
             '<body><iframe src="file:///proc/self/environ">x</iframe><script>alert(1)</script>'
             '<img src="file:///app/data/media/private/p.jpg" onerror="x()"><img src="/brand/ge-flag.svg">'
             '<p style="background:url(http://evil)" onclick="x()">ტექსტი &amp; <b>bold</b><input></p><p>after</p></body></html>')
    out = C.clean_html(dirty)
    for bad in ("file:", "http:", "iframe", "script", "onerror", "onclick", "@import", "<link", "refresh", "environ", "alert"):
        assert bad not in out, bad
    assert "ტექსტი &amp; <b>bold</b>" in out and "<p>after</p>" in out
    assert 'src="data:image/svg+xml;base64,' in out


def test_bad_inputs(db):
    ag = make_user(db, "agent")
    with pytest.raises(DomainError):
        C.create(db, {"kind": "x", "lang": "GEO", "html": PAGE}, ag)
    assert C.create(db, {"kind": "rent", "lang": "GEO", "html": PAGE}, ag).lead_id is None  # standalone is fine (11-13)
    other_lead = _lead(db, make_user(db, "agent"))
    with pytest.raises(DomainError) as e:
        C.create(db, {"kind": "rent", "lang": "GEO", "html": PAGE, "lead_id": other_lead.id}, ag)
    assert e.value.code == "not_found"


def test_autosave_draft_generate_regenerate_keeps_versions(db, client, fake_chrome):
    """The wizard autosaves a partly filled form into the deal's draft, reopens it, generates it, and after an edit in
    the same form regenerates it: the old PDF stays as a version, «PDF устарел» blocks signing until then."""
    ag = make_user(db, "agent")
    lead = _lead(db, ag)
    h = auth(ag)
    d = client.post("/api/contracts/draft", json={"lead_id": lead.id, "kind": "rent", "data": {"owner_full_name": "Нино", "_step": 3}},
                    headers=h).json()
    assert (d["status"], d["status_label"], d["has_pdf"], d["lang"], d["owner_name"]) == ("draft", "Черновик", False, None, "Нино")
    # the same deal's form again (no id: a reopened tab) continues the same draft, it isn't a second one
    d2 = client.post("/api/contracts/draft", json={"lead_id": lead.id, "kind": "rent", "lang": "RU",
                                                   "data": {"owner_full_name": "Нино", "client_full_name": "Анна", "_step": 3}}, headers=h).json()
    assert d2["id"] == d["id"] and d2["data"]["client_full_name"] == "Анна" and d2["data"]["_step"] == 3
    rows = client.get(f"/api/contracts?lead_id={lead.id}", headers=h).json()["items"]
    assert [(x["id"], x["status_label"]) for x in rows] == [(d["id"], "Черновик")]
    # not generated: no PDF, no signing, no share; the lead summary shows the draft
    assert client.post(f"/api/contracts/{d['id']}/sign", headers=h).status_code == 409
    assert client.post(f"/api/contracts/{d['id']}/share", headers=h).status_code == 409
    assert client.get(f"/api/leads/{lead.id}", headers=h).json()["contract"]["status_label"] == "Черновик"
    # generate
    body = {"kind": "rent", "lang": "RU", "html": PAGE, "lead_id": lead.id, "cadastral": "01.14.12.003.045",
            "data": {"owner_full_name": "Нино", "price_usd": "800"}}
    g = client.put(f"/api/contracts/{d['id']}", json=body, headers=h).json()
    assert (g["status_label"], g["has_pdf"], g["pdf_stale"], g["versions"]) == ("Сгенерирован", True, False, [])
    # a reopened generated contract: unchanged data saves nothing, an edit marks the PDF stale
    same = client.post("/api/contracts/draft", json={"id": d["id"], "kind": "rent", "lang": "RU", "data": body["data"]}, headers=h).json()
    assert same["pdf_stale"] is False
    step_only = client.post("/api/contracts/draft", json={"id": d["id"], "kind": "rent", "lang": "RU", "data": {**body["data"], "_step": 4}}, headers=h).json()
    assert step_only["pdf_stale"] is False and step_only["data"]["_step"] == 4  # the wizard's step isn't an edit
    edit = {**body["data"], "price_usd": "850"}
    assert client.post("/api/contracts/draft", json={"id": d["id"], "kind": "rent", "lang": "RU", "data": edit}, headers=h).json()["pdf_stale"]
    assert client.post(f"/api/contracts/{d['id']}/sign", headers=h).json()["code"] == "pdf_stale"
    # regenerate: the new PDF, the old one kept as version 1
    fake_chrome.clear()
    r = client.put(f"/api/contracts/{d['id']}", json={**body, "data": edit, "html": PAGE.replace("ხელშეკრულება", "v2")}, headers=h).json()
    assert r["pdf_stale"] is False and len(r["versions"]) == 1 and r["versions"][0]["by"]["id"] == ag.id
    assert client.get(f"/api/contracts/{d['id']}/pdf?version=0", headers=h).content == PDF
    assert client.get(f"/api/contracts/{d['id']}/pdf?version=5", headers=h).status_code == 404
    assert "v2" in fake_chrome[0]
    assert client.post(f"/api/contracts/{d['id']}/sign", headers=h).json()["status"] == "signed"
    # signed: the form can't change it any more; a new autosave for the deal starts a fresh draft, the signed one
    # stays the lead's contract (the deal guard reads it)
    assert client.post("/api/contracts/draft", json={"id": d["id"], "data": edit}, headers=h).status_code == 409
    n = client.post("/api/contracts/draft", json={"lead_id": lead.id, "data": {"x": "1"}}, headers=h).json()
    assert n["id"] != d["id"]
    assert client.get(f"/api/leads/{lead.id}", headers=h).json()["contract"]["id"] == d["id"]
    # the agent's cabinet list: staff filter by agent
    op = make_user(db, "operator")
    assert client.get(f"/api/contracts?agent_id={ag.id}&status=all", headers=auth(op)).json()["total"] == 2
    assert client.get(f"/api/contracts?agent_id={op.id}&status=all", headers=auth(op)).json()["total"] == 0


def test_standalone_contract_visibility_and_link_later(db, client):
    """11-13: a contract without a lead / deal — autosaved, generated, seen by its creator and staff only, linked to a
    lead later from the card (then the lead shows it)."""
    ag, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    h = auth(ag)
    d = client.post("/api/contracts/draft", json={"kind": "rent", "data": {"owner_full_name": "Нино"}}, headers=h).json()
    assert (d["status"], d["lead_id"], d["deal_id"]) == ("draft", None, None)
    # no lead to continue a draft in: a new form is a new contract, the open one continues by id
    assert client.post("/api/contracts/draft", json={"kind": "rent", "data": {"x": "1"}}, headers=h).json()["id"] != d["id"]
    g = client.put(f"/api/contracts/{d['id']}", json={"kind": "rent", "lang": "RU", "html": PAGE}, headers=h).json()
    assert g["has_pdf"] and g["lead_id"] is None
    assert client.get(f"/api/contracts/{d['id']}", headers=auth(other)).status_code == 404
    assert client.get("/api/contracts", headers=auth(other)).json()["total"] == 0
    assert client.get("/api/contracts", headers=h).json()["total"] == 2
    assert client.get(f"/api/contracts/{d['id']}", headers=auth(op)).status_code == 200
    # link: not to someone else's lead; then the lead carries it, a regeneration without lead_id keeps the link
    assert client.post(f"/api/contracts/{d['id']}/link", json={"lead_id": _lead(db, other).id}, headers=h).status_code == 404
    lead = _lead(db, ag)
    assert client.post(f"/api/contracts/{d['id']}/link", json={}, headers=h).status_code == 400
    r = client.post(f"/api/contracts/{d['id']}/link", json={"lead_id": lead.id}, headers=h).json()
    assert r["lead_id"] == lead.id
    assert client.post(f"/api/contracts/{d['id']}/link", json={"lead_id": lead.id}, headers=h).status_code == 409
    assert client.put(f"/api/contracts/{d['id']}", json={"kind": "rent", "lang": "RU", "html": PAGE}, headers=h).json()["lead_id"] == lead.id
    assert client.get(f"/api/leads/{lead.id}/contract", headers=h).json()["contract"]["id"] == d["id"]
