"""Irakli 11-18: (1) an object turning exclusive searches its duplicates everywhere at once (the base, every live ad,
competitors), the card shows «Найдены возможные дубли: N», strong ones go to the agent and the operators; new ads
re-run it. (2) «Без электронной подписи»: the contract step through the operator's confirmation."""
import json

from app import worker
from app.models import Event, Job
from app.services import jobs, matching, properties, similar
from tests.conftest import auth, make_listing, make_user
from tests.test_david_feedback import _code, _ready
from tests.test_exclusive_contract import JPG, PDF
from tests.test_similar import H, _object


def _scans(db, pid):
    return db.query(Event).filter_by(entity="property", entity_id=str(pid), action="duplicate_scan").order_by(Event.id).all()


def test_exclusive_runs_the_duplicate_search_and_new_ads_rerun_it(db, client, sent):
    vika, op = make_user(db, "agent", "Вика"), make_user(db, "operator")
    pid = _object(db, client, vika)
    # a competitor's ad with two of its photos (re-uploaded, a bit off), another site, another address
    make_listing(db, "mh7001", source="myhome", urls=json.dumps(["https://myhome.ge/7001"]), address="Kazbegi 30",
                 photo_hashes=json.dumps([H[0] ^ 1, H[1]]), text="Сдаю")
    assert properties.dup_scan(db, pid) is None
    r = client.patch(f"/api/properties/{pid}", json={"is_exclusive": True}, headers=auth(vika))
    assert r.status_code == 200, r.text
    assert jobs.pending(db, "dup_scan", pid) and r.json()["duplicate_scan"] == {"pending": True}  # «Ищем дубли…»

    worker.tick()
    scan = properties.out(db, db.get(properties.Property, pid), vika, full=True)["duplicate_scan"]
    assert scan["n"] >= 1 and not scan["pending"] and scan["trigger"] == "exclusive"
    assert [s["key"] for s in scan["strong"]] == ["l:mh7001"] and "общих фото: 2" in scan["strong"][0]["reasons"]
    (prop, n, strong), = sent.of("exclusive_duplicates")
    assert prop.id == pid and n == scan["n"] and strong[0]["listing_id"] == "mh7001"

    # nothing new: a re-scan after new ads leaves no event and no message
    assert similar.scan_exclusive(db, pid, "new_ads") is None and len(_scans(db, pid)) == 1

    # a new ad with one shared photo (not enough for the matcher to link it): the exclusive is scanned again
    make_listing(db, "ss7003", urls=json.dumps(["https://ss.ge/7003"]), address="Kazbegi 31", photo_hashes=json.dumps([H[2]]),
                 text="Сдаю квартиру")
    matching.process(db, ["ss7003"])
    assert db.query(Job).filter_by(kind="dup_scan", ref=str(pid), done_at=None).one().payload["trigger"] == "new_ads"
    worker.tick()
    assert len(_scans(db, pid)) == 2
    assert {s["key"] for s in properties.dup_scan(db, pid)["strong"]} == {"l:mh7001", "l:ss7003"}
    assert [x[2][0]["key"] for x in sent.of("exclusive_duplicates")] == ["l:mh7001", "l:ss7003"]  # only the new one

    # only exclusives are re-scanned on new ads
    assert similar.exclusives_hit(db, ["ss7003"]) == {pid}
    properties.update(db, pid, {"is_exclusive": False}, op)
    assert similar.exclusives_hit(db, ["ss7003"]) == set()
    properties.update(db, pid, {"is_exclusive": True}, op)
    # «Не дубль» on a strong candidate: no longer counted
    client.post("/api/similar/decide", json={"a": f"p:{pid}", "b": "l:mh7001", "same": False}, headers=auth(op))
    assert "l:mh7001" not in {s["key"] for s in similar.scan_exclusive(db, pid)["strong"]}


def test_photos_uploaded_after_the_exclusive_search_run_it_again(db, client, sent):
    """Irakli 11-19: a manual exclusive, then the photos taken from a myhome ad — the first search ran without them."""
    import io
    from PIL import Image
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika)
    im = Image.new("L", (90, 80))
    im.putdata([(x * 7 + y * 3) % 256 for y in range(80) for x in range(90)])
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    h = matching.photo_hash(buf.getvalue()) & matching.U64
    make_listing(db, "mh26699303", source="myhome", urls=json.dumps(["https://myhome.ge/26699303"]), address="Eredvi st",
                 photo_hashes=json.dumps([h ^ 1, 12345]), text="Сдаю")
    assert client.patch(f"/api/properties/{pid}", json={"is_exclusive": True}, headers=auth(vika)).status_code == 200
    worker.tick()
    assert properties.dup_scan(db, pid)["strong"] == []  # the ad may be listed by its params, not by a photo yet
    db.get(properties.Property, pid).status = "shoot_scheduled"
    db.commit()
    r = client.post(f"/api/properties/{pid}/media", files=[("files", ("a.jpg", buf.getvalue(), "image/jpeg"))], headers=auth(vika))
    assert r.status_code == 200, r.text
    assert db.query(Job).filter_by(kind="dup_scan", ref=str(pid), done_at=None).one().payload["trigger"] == "media"
    worker.tick()
    assert [s["key"] for s in properties.dup_scan(db, pid)["strong"]] == ["l:mh26699303"]
    assert sent.of("exclusive_duplicates")[-1][2][0]["listing_id"] == "mh26699303"

def test_contract_signed_or_merged_in_schedules_the_search(db, client):
    ag = make_user(db, "agent")
    p = _ready(db, ag, "ss1", address="Пекини 1")
    p.is_exclusive = True
    # David 11-23: the photos of an object in work queue its search (any object); cleared to see the contract's own
    assert jobs.pending(db, "dup_scan", p.id)[0].payload == {"trigger": "media"}
    jobs.cancel(db, "dup_scan", p.id)
    db.commit()
    properties.upload_contract(db, p.id, "c.pdf", PDF, ag)
    assert jobs.pending(db, "dup_scan", p.id)[0].payload == {"trigger": "contract"}

    q = _ready(db, ag, "ss2", address="Пекини 2")
    dup = _ready(db, ag, "ss3", address="Пекини 3")
    dup.is_exclusive = True
    jobs.cancel(db, "dup_scan", q.id)
    db.commit()
    op = make_user(db, "operator")
    properties.archive(db, dup.id, op, "duplicate", None, q.id)
    assert jobs.pending(db, "dup_scan", q.id)[0].payload == {"trigger": "merged"}


def test_no_esign_request_approved_by_operator_counts_as_the_contract(db, client, sent):
    ag, nino, dato = make_user(db, "agent", "Гиорги"), make_user(db, "operator", "Нино"), make_user(db, "operator", "Дато")
    p = _ready(db, ag, "ss1", address="Пекини 1")
    url = f"/api/properties/{p.id}/exclusive-no-esign"
    assert client.post(url, data={"reason": "за границей"}, headers=auth(ag)).json()["code"] == "bad_status"  # not exclusive
    p.is_exclusive = True
    db.commit()
    assert client.post(url, data={"reason": " "}, headers=auth(ag)).json()["code"] == "reason_required"
    other = make_user(db, "agent")
    assert client.post(url, data={"reason": "x"}, headers=auth(other)).status_code == 403

    r = client.post(url, data={"reason": "собственник за границей"}, files={"file": ("paper.jpg", JPG)}, headers=auth(ag))
    assert r.status_code == 200, r.text
    w = r.json()["no_esign"]
    assert (w["status"], w["reason"], w["requested_by"]["name"]) == ("pending", "собственник за границей", "Гиорги")
    assert w["scan_url"] and "эксклюзивный договор (PDF)" in r.json()["missing"]
    assert sent.of("change_request_new")
    assert _code(db, p.id, ag)[0] == "no_exclusive_contract"  # pending is not done
    assert r.json()["owner_docs"] == {}  # the scan is not one of the owner's documents

    q = client.get("/api/change-requests", headers=auth(nino)).json()["items"]
    assert q[0]["kind"] == "no_esign" and q[0]["scan_url"] == w["scan_url"]
    assert client.post(f"/api/change-requests/{w['id']}/decide", json={"approve": True}, headers=auth(ag)).status_code == 403
    d = client.post(f"/api/change-requests/{w['id']}/decide", json={"approve": True}, headers=auth(nino)).json()
    assert d["status"] == "approved"

    card = client.get(f"/api/properties/{p.id}", headers=auth(ag)).json()
    w = card["no_esign"]
    assert (w["status"], w["decided_by"]["name"]) == ("approved", "Нино") and w["decided_at"]
    assert "эксклюзивный договор (PDF)" not in card["missing"] and card["exclusive_contract"] is None
    assert _code(db, p.id, ag)[0] == "no_owner_docs"  # past the contract step; the documents are still asked for
    ev = db.query(Event).filter_by(entity="property", entity_id=str(p.id), action="exclusive_contract_waived").one()
    assert ev.actor_id == nino.id and ev.data["reason"] == "собственник за границей"
    assert jobs.pending(db, "dup_scan", p.id)  # the contract step done → the duplicate search
    assert client.post(url, data={"reason": "ещё раз"}, headers=auth(ag)).json()["code"] == "bad_status"

    # an operator's own request is decided by another operator
    p2 = _ready(db, ag, "ss2", address="Пекини 2")
    p2.is_exclusive = True
    db.commit()
    w2 = client.post(f"/api/properties/{p2.id}/exclusive-no-esign", data={"reason": "подписал на бумаге"},
                     headers=auth(nino)).json()["no_esign"]
    assert client.post(f"/api/change-requests/{w2['id']}/decide", json={"approve": True}, headers=auth(nino)).status_code == 403
    assert client.post(f"/api/change-requests/{w2['id']}/decide", json={"approve": True}, headers=auth(dato)).json()["status"] == "approved"

    # merged into another object as its «Дубль»: the confirmation goes along
    keep = _ready(db, ag, "ss9", address="Пекини 9")
    properties.archive(db, p.id, nino, "duplicate", None, keep.id)
    keep.is_exclusive = True
    db.commit()
    assert properties.contract_done(db, keep) and properties.no_esign(db, keep.id).decided_by == nino.id


def test_no_esign_rejected_returns_the_step_with_the_comment(db, client, sent):
    ag, nino = make_user(db, "agent"), make_user(db, "operator", "Нино")
    p = _ready(db, ag, "ss1")
    p.is_exclusive = True
    db.commit()
    url = f"/api/properties/{p.id}/exclusive-no-esign"
    w = client.post(url, data={"reason": "не пользуется телефоном"}, headers=auth(ag)).json()["no_esign"]
    assert w["scan_url"] is None
    r = client.post(f"/api/change-requests/{w['id']}/decide", json={"approve": False}, headers=auth(nino))
    assert r.json()["code"] == "comment_required"
    client.post(f"/api/change-requests/{w['id']}/decide", json={"approve": False, "comment": "пусть подпишет сын по ссылке"},
                headers=auth(nino))
    card = client.get(f"/api/properties/{p.id}", headers=auth(ag)).json()
    assert (card["no_esign"]["status"], card["no_esign"]["comment"]) == ("rejected", "пусть подпишет сын по ссылке")
    assert "эксклюзивный договор (PDF)" in card["missing"] and _code(db, p.id, ag)[0] == "no_exclusive_contract"
    assert [x[0].status for x in sent.of("change_request_decided")] == ["rejected"]
    # he may ask again; a contract signed meanwhile makes the request moot
    assert client.post(url, data={"reason": "подписал на бумаге"}, headers=auth(ag)).json()["no_esign"]["status"] == "pending"
    properties.upload_contract(db, p.id, "c.pdf", PDF, ag)
    assert client.post(url, data={"reason": "x"}, headers=auth(ag)).json()["code"] == "already_signed"
