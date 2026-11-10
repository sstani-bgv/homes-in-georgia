"""Per-source parser frequency and the monthly price of the paid (Apify) ones."""
import json

from app.models import Setting
from app.services import settings
from tests.conftest import auth, make_user


def test_schedule_price_and_save(db, client):
    admin = make_user(db, "superadmin")
    # two Facebook runs: $0.002 a start + posts; 12 h of new posts cost $0.084 -> $0.007/h
    runs = {"facebook": [[1, 0.044, 60, 6], [2, 0.044, 60, 6]], "threads": [[1, 0.12, 9, 8]]}
    db.add(Setting(key="source_runs", value=json.dumps(runs)))
    db.commit()
    r = client.get("/api/sources/schedule", headers=auth(admin)).json()
    fb, th = next(x for x in r if x["key"] == "facebook"), next(x for x in r if x["key"] == "threads")
    assert fb["every_min"] == 360 and fb["paid"]["per_run"] == 0.002 and fb["paid"]["per_hour"] == 0.007
    assert fb["paid"]["spent_7d"] == 0.088 and th["paid"]["per_run"] == 0.12 and th["paid"]["per_hour"] == 0
    assert next(x for x in r if x["key"] == "ss")["paid"] is None

    r = client.put("/api/sources/schedule", headers=auth(admin), json={"facebook": 120})
    assert r.status_code == 200 and next(x for x in r.json() if x["key"] == "facebook")["every_min"] == 120
    assert client.put("/api/sources/schedule", headers=auth(admin), json={"facebook": 5}).status_code == 400
    assert client.put("/api/sources/schedule", headers=auth(admin), json={"nope": 60}).status_code == 400
    # the generic settings form neither shows nor overwrites them
    assert "source_runs" not in settings.all(db) and "source_every_min" not in settings.all(db)
    client.put("/api/settings", headers=auth(admin), json={"source_every_min": "{}"})
    assert json.loads(settings.get(db, "source_every_min"))["facebook"] == 120
