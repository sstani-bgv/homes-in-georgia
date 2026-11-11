"""Exclusive object: «Отправить оператору» needs the owner's signed contract PDF; the PDF never goes into the post."""
import json

import pytest

from app.services import properties
from app.services.errors import DomainError
from tests.conftest import make_user
from tests.test_david_feedback import _code, _ready

PDF = b"%PDF-1.4 signed"
JPG = b"\xff\xd8\xff\xe0passport"


def test_exclusive_submit_needs_contract_pdf(db):
    ag = make_user(db, "agent")
    plain = _ready(db, ag, "ss1", address="Пекини 1")
    assert _code(db, plain.id, ag)[0] == "ok"  # not exclusive: unaffected

    p = _ready(db, ag, "ss2", address="Пекини 2")
    p.is_exclusive = True
    db.commit()
    assert "эксклюзивный договор (PDF)" in properties.out(db, p, ag, full=True)["missing"]
    assert _code(db, p.id, ag)[0] == "no_exclusive_contract"

    with pytest.raises(DomainError) as e:
        properties.upload_contract(db, p.id, "c.pdf", b"\xff\xd8jpeg", ag)
    assert e.value.code == "bad_file"
    db.rollback()
    properties.upload_contract(db, p.id, "old.pdf", PDF, ag)
    c = properties.upload_contract(db, p.id, "c.pdf", PDF, ag)  # replaces the first one

    d = properties.out(db, p, ag, full=True)
    assert d["exclusive_contract"]["id"] == c.id and "эксклюзивный договор (PDF)" not in d["missing"]
    # the PDF is not a post file: not in the card's media, the album, nor pickable for publishing
    assert [m["kind"] for m in d["media"]] == ["photo"]
    assert c.id not in {m.id for m in properties.media(db, p.id)}
    assert _code(db, p.id, ag)[0] == "no_owner_docs"  # the passport and the registry extract too
    properties.upload_owner_doc(db, p.id, "owner_id_doc", "id.jpg", JPG, ag)
    properties.upload_owner_doc(db, p.id, "registry_extract", "ex.pdf", PDF, ag)
    assert _code(db, p.id, ag)[0] == "ok"

    op = make_user(db, "operator")
    p.is_exclusive = False  # skip the «exclusive still on ss» publish block (this test listing is an ss ad)
    db.commit()
    with pytest.raises(DomainError) as e:
        properties.publish(db, p.id, op, media_ids=[c.id])
    assert e.value.code == "not_found"


def test_contract_only_upload_does_not_count_as_media(db):
    ag = make_user(db, "agent")
    p = _ready(db, ag, "ss3")
    p.is_exclusive = True
    for m in properties.media(db, p.id):
        properties.delete_media(db, p.id, m.id, ag)
    properties.upload_contract(db, p.id, "c.pdf", PDF, ag)
    assert _code(db, p.id, ag)[0] == "no_media"


def test_owner_signs_by_public_link(db, client):
    ag = make_user(db, "agent")
    p = _ready(db, ag, "ss4", address="Пекини 4")
    p.is_exclusive = True
    db.commit()
    token = properties.out(db, p, ag, full=True)["exclusive_sign_token"]
    assert client.get("/api/public/exclusive/garbage").status_code == 404

    v = client.get(f"/api/public/exclusive/{token}").json()  # no login: the token is the access
    assert (v["address"], v["owner_name"], v["signed_at"]) == ("Пекини 4", "Гиорги", None)
    form = {"full_name": "Гиорги Беридзе", "personal_id": "01001012345", "phone": "+995 555 12 34 56", "consent": "true"}
    docs = {"id_doc": ("passport.jpg", JPG), "extract": ("amonatseri.pdf", PDF)}
    r = client.post(f"/api/public/exclusive/{token}", data={**form, "consent": "false"}, files={"file": ("c.pdf", PDF), **docs})
    assert r.json()["code"] == "consent_required" and properties.contract(db, p.id) is None
    r = client.post(f"/api/public/exclusive/{token}", data=form, files={"file": ("c.pdf", PDF), "extract": docs["extract"]})
    assert r.json()["code"] == "missing_fields" and properties.contract(db, p.id) is None  # no passport
    r = client.post(f"/api/public/exclusive/{token}", data=form,
                    files={"file": ("c.pdf", PDF), "id_doc": ("passport.exe", b"MZ"), "extract": docs["extract"]})
    assert r.json()["code"] == "bad_file" and properties.contract(db, p.id) is None
    app = {"app_id": "35575905", "reg_number": "882026909236", "date": "2026-10-07T11:34:56+00:00", "type": "ამონაწერი", "x": 1}
    r = client.post(f"/api/public/exclusive/{token}", data={**form, "registry_app": json.dumps(app)},
                    files={"file": ("c.pdf", PDF), **docs})
    assert r.status_code == 200 and r.json()["signed_at"]
    db.expire_all()
    assert properties.contract(db, p.id) and _code(db, p.id, ag)[0] == "ok"
    from app.models import Event
    ev = db.query(Event).filter_by(entity_id=str(p.id), action="exclusive_contract_signed").one()
    assert ev.data["personal_id"] == "01001012345" and len(ev.data["pdf_sha256"]) == 64
    assert ev.data["registry_app"]["reg_number"] == "882026909236" and "x" not in ev.data["registry_app"]
    d = properties.out(db, p, ag, full=True)
    assert d["registry_app"]["url"].endswith("#/view/35575905")
    r = client.post(f"/api/public/exclusive/{token}", data=form, files={"file": ("c.pdf", PDF), **docs})
    assert r.json()["code"] == "already_signed"

    p.is_exclusive = False  # no longer exclusive: the link stops working
    db.commit()
    assert client.get(f"/api/public/exclusive/{token}").status_code == 404


def test_owner_docs_are_private(db, client):
    """The passport / extract: private storage, not post media, only staff and the object's agent see them."""
    from tests.conftest import auth
    ag, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    p = _ready(db, ag, "ss5", address="Пекини 5")
    p.is_exclusive = True
    db.commit()
    with pytest.raises(DomainError) as e:
        properties.upload_owner_doc(db, p.id, "owner_id_doc", "id.pdf", JPG, ag)  # a JPEG named .pdf
    assert e.value.code == "bad_file"
    db.rollback()
    m = properties.upload_owner_doc(db, p.id, "owner_id_doc", "id.jpg", JPG, ag)
    assert m.path.startswith("private/") and m.id not in {x.id for x in properties.media(db, p.id)}
    d = properties.out(db, p, ag, full=True)
    assert d["owner_docs"]["owner_id_doc"]["id"] == m.id and "выписка из Публичного реестра (PDF)" in d["missing"]
    assert all(x["kind"] in ("photo", "video") for x in d["media"])
    assert properties.out(db, p, other, full=True)["owner_docs"] == {}
    assert properties.out(db, p, other)["media_count"] == len(properties.media(db, p.id))  # the passport isn't counted

    url = f"/api/media/{m.path}"
    assert client.get(url, headers=auth(other)).status_code == 404
    assert client.get(url, headers=auth(ag)).content == JPG
    assert client.get(url, headers=auth(op)).status_code == 200
    assert client.get(url).status_code == 401
    r = client.post(f"/api/properties/{p.id}/owner-docs/nonsense", files={"file": ("a.pdf", PDF)}, headers=auth(ag))
    assert r.status_code == 404


def test_registry_lookup_is_scoped_to_a_signing_link(db, client, monkeypatch):
    """GET /public/exclusive/<token>/registry: NAPR's application list, only for a live link, validated, cached."""
    import httpx
    from app.services import registry
    calls = []

    class R:
        def raise_for_status(self): pass
        def json(self): return {"page": 1, "lastpage": 1, "total": 1, "applist": [
            {"appID": "35575905", "regNumber": "882026909236", "statusId": "10", "status": "done", "appRegDate": "1791386696",
             "webTransact": "ამონაწერი", "address": "Tbilisi", "applicants": ["X"]}]}

    monkeypatch.setattr(httpx, "post", lambda url, json, **kw: calls.append(json) or R())
    registry._cache.clear()
    ag = make_user(db, "agent")
    p = _ready(db, ag, "ss6", address="Пекини 6")
    p.is_exclusive = True
    db.commit()
    token = properties.sign_token(p.id)
    assert client.get("/api/public/exclusive/garbage/registry?cadastral=01.10.13.015.029").status_code == 404
    assert client.get(f"/api/public/exclusive/{token}/registry?cadastral=drop table").json()["code"] == "bad_cadastral"
    r = client.get(f"/api/public/exclusive/{token}/registry?cadastral=01.10.13.015.029").json()
    assert r["items"][0]["reg_number"] == "882026909236" and r["items"][0]["done"] and r["items"][0]["date"].startswith("2026-10-07")
    assert r["items"][0]["url"].endswith("#/view/35575905") and calls[0]["cadcode"] == "01.10.13.015.029"
    client.get(f"/api/public/exclusive/{token}/registry?cadastral=01.10.13.015.029")
    assert len(calls) == 1  # cached
