"""David 11-23: not only exclusives — a live object in work / published searches its copies (myhome + ss.ge) and
duplicates once a day; a non-exclusive gets no exclusive alert, only the card's status and «Похожие»."""
import json

from app import worker
from app.models import Event, Property
from app.services import myhome_copies, properties, similar
from app.services.common import log_event
from tests.conftest import auth, make_listing, make_user
from tests.test_similar import H, _object


def test_sites_are_searched_on_publish_link_and_button_only_without_alerts(db, client, sent, monkeypatch):
    """Stanislav 11-23: the sites only on publishing, a link and «Найти копии»; no daily pass."""
    runs = []

    def fake_run(db_, pid, pause=1.0):
        runs.append(pid)
        return 0
    monkeypatch.setattr(myhome_copies, "run", fake_run)
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika)
    make_listing(db, "mh7101", source="myhome", urls=json.dumps(["https://myhome.ge/7101"]), address="Kazbegi 30",
                 photo_hashes=json.dumps([H[0] ^ 1, H[1]]), text="Сдаю")
    p = db.get(Property, pid)
    assert not p.is_exclusive and not hasattr(properties, "schedule_daily_dup_scans")
    p.status = "published"
    db.commit()
    from app.services import jobs
    for trigger in ("media", "new_ads", "published"):
        jobs.schedule(db, "dup_scan", pid, properties.now(), {"trigger": trigger})
        db.commit()
        worker.tick()
    assert runs == [pid]  # the base only on media / new ads
    assert client.post(f"/api/properties/{pid}/myhome-copies", headers=auth(vika)).json() == {"ok": True}
    assert jobs.pending(db, "dup_scan", pid)[0].payload == {"trigger": "manual"}
    worker.tick()
    assert runs == [pid, pid]
    scan = properties.out(db, db.get(Property, pid), vika, full=True)["duplicate_scan"]
    assert [s["key"] for s in scan["strong"]] == ["l:mh7101"]
    assert not sent.of("exclusive_duplicates")  # no exclusive to defend


def test_publishing_queues_the_search(db):
    from app.services import jobs
    from tests.test_david_feedback import _ready
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    p = _ready(db, ag, "ss1", address="Пекини 1")
    jobs.cancel(db, "dup_scan", p.id)
    p.status = "review"
    db.commit()
    properties.publish(db, p.id, op)
    assert jobs.pending(db, "dup_scan", p.id)[0].payload == {"trigger": "published"}
