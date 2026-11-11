"""Exclusive object: «Отправить оператору» needs the owner's signed contract PDF; the PDF never goes into the post."""
import pytest

from app.config import settings
from app.services import properties
from app.services.errors import DomainError
from tests.conftest import make_user
from tests.test_david_feedback import _code, _ready

PDF = b"%PDF-1.4 signed"


def test_exclusive_submit_needs_contract_pdf(db, monkeypatch):
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

    monkeypatch.setattr(settings, "EXCLUSIVE_SIGN_URL", "https://sign.test/x?obj={id}&owner={owner}&addr={address}")
    d = properties.out(db, p, ag, full=True)
    assert d["exclusive_contract"]["id"] == c.id and "эксклюзивный договор (PDF)" not in d["missing"]
    assert d["exclusive_sign_url"] == f"https://sign.test/x?obj={p.id}&owner=%D0%93%D0%B8%D0%BE%D1%80%D0%B3%D0%B8&addr=%D0%9F%D0%B5%D0%BA%D0%B8%D0%BD%D0%B8%202"
    # the PDF is not a post file: not in the card's media, the album, nor pickable for publishing
    assert [m["kind"] for m in d["media"]] == ["photo"]
    assert c.id not in {m.id for m in properties.media(db, p.id)}
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
