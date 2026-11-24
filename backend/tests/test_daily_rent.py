"""«Посуточная аренда» inside the CRM (ApartFlow moved in): roles, the money (shares, compensations, Booking fee, the
rate), payouts, reports, the calendar / iCal feeds, and the one-off import from ApartFlow's SQLite with its checks."""
import json
import sqlite3
from datetime import date, timedelta

import pytest

from app.models import DrBlock, Event
from app.services import daily_rent as R
from app.services import daily_rent_ical as C
from tests.conftest import auth, make_user

RATE = 2.5


@pytest.fixture(autouse=True)
def fixed_rate(monkeypatch):
    monkeypatch.setattr(R, "usd_gel", lambda: RATE)


@pytest.fixture
def boss(db):
    return make_user(db, "admin", name="Ираклий")


def api(client, user, method, path, body=None):
    r = client.request(method, "/api/daily-rent" + path, headers=auth(user), json=body)
    assert r.status_code < 400, (path, r.status_code, r.text)
    return r.json()


def apartment(client, user, **kw):
    return api(client, user, "POST", "/apartments", {"name": "Vake Nest", "owner_name": "Natia", "owner_account": "GE00TB01", **kw})


# ---------------------------------------------------------------- roles

@pytest.mark.parametrize("role", ["superadmin", "admin", "daily_rent_manager"])
def test_daily_rent_roles_have_the_section(client, db, role):
    u = make_user(db, role)
    a = apartment(client, u)
    assert api(client, u, "GET", "/data")["apartments"][0]["id"] == a["id"]


@pytest.mark.parametrize("role", ["operator", "agent", "recruit"])
def test_other_roles_have_no_daily_rent(client, db, role):
    assert client.get("/api/daily-rent/data", headers=auth(make_user(db, role))).status_code == 403


@pytest.mark.parametrize("path", ["/api/leads", "/api/deals", "/api/users", "/api/clients", "/api/invites", "/api/analytics/dashboard",
                                  "/api/media/properties/1/x.jpg", "/api/media/daily/../properties/1/x.jpg"])
def test_manager_sees_nothing_else(client, db, path):
    r = client.get(path, headers=auth(make_user(db, "daily_rent_manager")))
    assert r.status_code == 403, (path, r.status_code)


def test_manager_sees_apartment_photos(client, db):
    m = make_user(db, "daily_rent_manager")
    a = apartment(client, m)
    r = client.post(f"/api/daily-rent/apartments/{a['id']}/photos", headers=auth(m), files={"files": ("p.jpg", b"\xff\xd8\xff photo", "image/jpeg")})
    assert r.status_code == 200, r.text
    key = r.json()["photos"][0]
    assert key.startswith(f"daily/{a['id']}/")
    assert client.get(f"/api/media/{key}", headers=auth(m)).status_code == 200


# ---------------------------------------------------------------- money: shares and compensations (ApartFlow's calcBk)

def test_shares_with_tax_expenses_and_rate(client, db, boss):
    a = apartment(client, boss)
    b = api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Airbnb", "gross": 200,
                                                "check_in": "2026-10-01", "check_out": "2026-10-04"})
    assert (b["currency"], b["tax_pct"], b["platform_pct"]) == ("USD", 1, 20)  # Airbnb defaults: USD, 1% tax, 20/80
    api(client, boss, "POST", "/expenses", {"booking_id": b["id"], "category": "cleaning", "amount": 50, "currency": "GEL", "payer": "platform"})
    b = api(client, boss, "POST", "/expenses", {"booking_id": b["id"], "category": "supplies", "amount": 10, "currency": "USD", "payer": "owner"})
    c = b["calc"]
    # 200 − 1% tax = 198; expenses 50 ₾ / 2.5 = $20 + $10 = $30; net 168
    assert (c["tax"], c["after_tax"], c["total_expenses"], c["net"]) == (2, 198, 30, 168)
    # platform 20% = 33.60 + its $20 back; owner 80% = 134.40 + his $10 back
    assert (c["platform_base"], c["platform"], c["owner_base"], c["owner"]) == (33.6, 53.6, 134.4, 144.4)
    assert c["platform"] + c["owner"] == c["after_tax"]  # the stay's money is split, nothing lost
    assert b["nights"] == 3
    assert "= $144,40" in b["report"] and "Natia (80%)" in b["report"] and "GE00TB01" in b["report"]


def test_booking_fee_goes_to_the_platform_and_only_on_booking(client, db, boss):
    a = apartment(client, boss)
    b = api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Booking", "gross": 500, "booking_fee": 75})
    c = b["calc"]
    assert (b["currency"], c["tax"], c["net"], c["platform"], c["owner"]) == ("GEL", 0, 425, 160, 340)  # 85 + fee 75 | 340
    b = api(client, boss, "PATCH", f"/bookings/{b['id']}", {"platform": "Airbnb"})
    assert b["booking_fee"] is None and b["calc"]["net"] == 500


def test_apartment_split_is_copied_to_new_stays(client, db, boss):
    a = apartment(client, boss, platform_pct=30)
    assert a["owner_pct"] == 70
    b = api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Другое", "gross": 100})
    assert (b["platform_pct"], b["calc"]["platform"], b["calc"]["owner"]) == (30, 30, 70)
    api(client, boss, "PATCH", f"/apartments/{a['id']}", {"platform_pct": 20})
    assert api(client, boss, "GET", f"/bookings/{b['id']}")["calc"]["owner"] == 70  # an existing stay keeps its split


def test_calc_rounds_like_apartflow():
    b = type("B", (), {"currency": "GEL", "gross": 267.76, "tax_pct": 0, "booking_fee": None, "platform": "Booking", "platform_pct": 20})
    e = type("E", (), {"id": 1, "category": "cleaning", "amount": 49.54, "currency": "GEL", "payer": "platform", "comment": ""})
    big = type("E", (), {"id": 2, "category": "other", "amount": 60000, "currency": "GEL", "payer": "owner", "comment": ""})
    c = R.calc(b, [e, big], 2.5934)
    assert (c["net"], c["platform"], c["owner"]) == (218.22, 93.18, 174.58)  # the absurd 60 000 is skipped
    assert c["owner_usd"] == round(174.58 / 2.5934, 2)


# ---------------------------------------------------------------- payouts

def test_payouts_ready_waiting_pay_all_and_undo(client, db, boss):
    a = apartment(client, boss)
    mk = lambda **kw: api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Booking", "gross": 100, **kw})  # noqa: E731
    b1, b2, b3 = mk(check_in="2026-10-01"), mk(check_in="2026-10-05", payment_status="pending"), mk(check_in="2026-10-09")
    [p] = api(client, boss, "GET", "/summary")["payouts"]
    assert (p["ready"], p["waiting"], p["ready_count"]) == ({"GEL": 160}, {"GEL": 80}, 2)
    assert api(client, boss, "POST", "/payouts", {"apartment_id": a["id"]})["changed"] == 2  # «Выплатить всё»: ready ones only
    rows = {b["id"]: b for b in api(client, boss, "GET", f"/bookings?apartment_id={a['id']}")["items"]}
    assert rows[b1["id"]]["paid_out"] and rows[b3["id"]]["paid_out"] and not rows[b2["id"]]["paid_out"]
    assert rows[b1["id"]]["paid_out_at"] == R.today().isoformat()
    api(client, boss, "POST", "/payouts", {"booking_ids": [b1["id"]], "paid": False})
    assert api(client, boss, "GET", f"/bookings/{b1['id']}")["paid_out_at"] is None
    s = api(client, boss, "GET", "/summary?month_from=2026-10&month_to=2026-10")
    assert s["total"]["to_pay"] == {"GEL": 160} and s["total"]["stays"] == 3 and s["total"]["nights"] == 0


def test_period_report_and_kpis(client, db, boss):
    a = apartment(client, boss)
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Airbnb", "gross": 100, "tax_pct": 0,
                                            "check_in": "2026-10-10", "check_out": "2026-10-12", "guest_name": "Di"})
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Airbnb", "gross": 50, "check_in": "2026-09-30"})
    x = api(client, boss, "GET", f"/apartments/{a['id']}?month_from=2026-10&month_to=2026-10&label=Октябрь 2026")
    assert len(x["bookings"]) == 2 and x["econ"]["stays"] == 1 and x["econ"]["nights"] == 2 and x["econ"]["occupancy"] == 6
    assert "📅 Период: Октябрь 2026" in x["period_report"] and "Di, Airbnb" in x["period_report"] and "владельцу $80" in x["period_report"]


def test_reports_in_three_languages_and_the_owner_language(client, db, boss):
    a = apartment(client, boss)
    b = api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Airbnb", "gross": 1234.5, "tax_pct": 0,
                                                "check_in": "2026-10-10", "check_out": "2026-10-12", "guest_name": "Di"})
    api(client, boss, "POST", "/expenses", {"booking_id": b["id"], "category": "cleaning", "amount": 25, "currency": "USD"})
    b = api(client, boss, "GET", f"/bookings/{b['id']}")
    r = b["reports"]
    assert b["report_lang"] == "ru" and b["report"] == r["ru"] and "Чистая прибыль" in r["ru"]
    assert "📅 Period: 10 October — 12 October (2 nights)" in r["en"] and "Net profit: $1,209.50" in r["en"] and "Cleaning: $25" in r["en"]
    assert "📅 პერიოდი: 10 ოქტომბერი — 12 ოქტომბერი (2 ღამე)" in r["ka"] and "წმინდა მოგება: $1 209,50" in r["ka"]
    assert "დასუფთავება" in r["ka"] and "👤 სტუმარი: Di" in r["ka"]  # what people typed stays as typed
    x = api(client, boss, "GET", f"/apartments/{a['id']}?month_from=2026-10&month_to=2026-10")
    assert "📅 Period: October 2026" in x["period_reports"]["en"] and "📅 პერიოდი: ოქტომბერი 2026" in x["period_reports"]["ka"]
    api(client, boss, "PATCH", f"/apartments/{a['id']}", {"report_lang": "ka"})  # the owner speaks Georgian
    assert api(client, boss, "GET", f"/bookings/{b['id']}")["report"] == r["ka"]
    from app.services import daily_rent_auto as A
    [rep] = [o for o in A.owner_reports(db, "2026-10") if o["apartment_id"] == a["id"]]
    assert rep["lang"] == "ka" and "პერიოდი: ოქტომბერი 2026" in rep["text"]
    assert client.patch(f"/api/daily-rent/apartments/{a['id']}", headers=auth(boss), json={"report_lang": "de"}).status_code == 422


def test_delete_stay_takes_its_expenses_and_everything_is_audited(client, db, boss):
    a = apartment(client, boss)
    b = api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "gross": 100})
    api(client, boss, "POST", "/expenses", {"booking_id": b["id"], "amount": 5})
    assert api(client, boss, "DELETE", f"/bookings/{b['id']}")["expenses_deleted"] == 1
    assert client.delete(f"/api/daily-rent/apartments/{a['id']}", headers=auth(boss)).status_code == 200  # no live stays left
    j = api(client, boss, "GET", "/journal")
    assert {(e["entity"], e["action"]) for e in j["items"]} >= {("dr_booking", "create"), ("dr_expense", "delete"), ("dr_booking", "delete")}
    assert all(e["actor"] == "Ираклий" for e in j["items"])


def test_apartment_with_stays_is_not_deleted(client, db, boss):
    a = apartment(client, boss)
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "gross": 1})
    assert client.delete(f"/api/daily-rent/apartments/{a['id']}", headers=auth(boss)).status_code == 409


# ---------------------------------------------------------------- calendar and iCal

FEED = """BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:abc@airbnb\r\nDTSTART;VALUE=DATE:20261201\r\nDTEND;VALUE=DATE:20261205\r
SUMMARY:Reserved\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:na@airbnb\r\nDTSTART;VALUE=DATE:20261220\r\nDTEND;VALUE=DATE:20261222\r
SUMMARY:Airbnb (Not available)\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""


def test_ical_import_export_and_conflicts(client, db, boss):
    a = apartment(client, boss)
    air = api(client, boss, "POST", "/channels", {"apartment_id": a["id"], "channel": "airbnb", "import_url": "https://www.airbnb.com/calendar/ical/1.ics"})
    bk = api(client, boss, "POST", "/channels", {"apartment_id": a["id"], "channel": "booking"})
    assert air["export_url"].endswith(f"/api/public/daily-rent/ical/{air['export_token']}.ics")
    assert C.sync_channel(db, air["id"], fetcher=lambda url: FEED) == {"events": 2}
    # a Booking stay on Dec 3-6 overlaps Airbnb's reservation: a double booking
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Booking", "gross": 1, "check_in": "2026-12-03", "check_out": "2026-12-06"})
    cal = api(client, boss, "GET", "/calendar?start=2026-12-01&end=2027-01-01")
    [cf] = cal["conflicts"]
    assert (cf["start"], cf["end"]) == ("2026-12-03", "2026-12-05")
    # Booking's feed: Airbnb's reservation + «Not available»; our Booking stay is not sent back to Booking
    r = client.get(f"/api/public/daily-rent/ical/{bk['export_token']}.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert "DTSTART;VALUE=DATE:20261201" in r.text and "DTSTART;VALUE=DATE:20261220" in r.text and "20261203" not in r.text
    # Airbnb's feed: only our Booking stay
    t = client.get(f"/api/public/daily-rent/ical/{air['export_token']}.ics").text
    assert "DTSTART;VALUE=DATE:20261203" in t and "20261201" not in t
    assert client.get("/api/public/daily-rent/ical/" + "0" * 48 + ".ics").status_code == 404
    # the event vanished from Airbnb → its block is cancelled
    C.sync_channel(db, air["id"], fetcher=lambda url: FEED.split("BEGIN:VEVENT")[0] + "END:VCALENDAR\r\n")
    assert {b.status for b in db.query(DrBlock)} == {"cancelled"}


def test_new_conflicts_notified_once(client, db, boss, sent):
    a = apartment(client, boss)
    mk = lambda g, p: api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": p, "guest_name": g, "gross": 1,  # noqa: E731
                                                              "check_in": "2026-12-01", "check_out": "2026-12-03"})
    mk("Anna", "Airbnb")
    assert C.new_conflicts(db) == []  # the first run only remembers
    mk("Boris", "Booking")
    fresh = C.new_conflicts(db)
    assert len(fresh) == 1 and C.new_conflicts(db) == []
    mk("Anna K", "Booking")  # same first name = one stay paid in parts, not a conflict with Anna... but it is with Boris
    assert len(C.new_conflicts(db)) == 1
    from app import worker
    worker.daily_rent_ical_tick()
    assert sent.of("daily_rent_conflicts") == []  # nothing new since


def test_manual_block_refuses_busy_dates(client, db, boss):
    a = apartment(client, boss)
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "gross": 1, "check_in": "2026-12-01", "check_out": "2026-12-03"})
    r = client.post("/api/daily-rent/blocks", headers=auth(boss), json={"apartment_id": a["id"], "start": "2026-12-02", "end": "2026-12-04"})
    assert r.status_code == 409
    ok = api(client, boss, "POST", "/blocks", {"apartment_id": a["id"], "start": "2026-12-03", "end": "2026-12-04", "reason": "ремонт"})
    assert ok["source"] == "manual"  # check-out day = free for the next one
    assert api(client, boss, "DELETE", f"/blocks/{ok['id']}")["status"] == "cancelled"


def test_channel_url_must_be_a_platform(client, db, boss):
    a = apartment(client, boss)
    r = client.post("/api/daily-rent/channels", headers=auth(boss), json={"apartment_id": a["id"], "channel": "airbnb", "import_url": "https://evil.example/x.ics"})
    assert r.status_code == 400


# ---------------------------------------------------------------- the one-off import from ApartFlow

def _sqlite(path, rows):
    con = sqlite3.connect(path)
    for t in ("apartments", "bookings", "expenses", "cleaning", "staff", "blocks", "channels"):
        con.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY, data TEXT, created_at TEXT, updated_at TEXT, deleted_at TEXT)")
    con.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, entity TEXT, entity_id TEXT, action TEXT, before TEXT, after TEXT)")
    for t, rid, data, deleted in rows:
        con.execute(f"INSERT INTO {t} VALUES (?, ?, '2026-11-11T19:58:09.222Z', '2026-11-11T19:58:09.222Z', ?)", (rid, json.dumps(data), deleted))
    con.execute("INSERT INTO audit_log VALUES (1, '2026-11-11T19:58:09.222Z', 'import:sheets', 'bookings', 'b1', 'create', NULL, '{}')")
    con.commit()
    con.close()


def test_import_from_apartflow(db, tmp_path):
    from app.importers import apartflow
    f = str(tmp_path / "af.db")
    _sqlite(f, [
        ("apartments", "1", {"apartment_name": "Rustaveli Warm Corner", "owner_name": "Sophiko", "owner_percentage": "", "tax": "", "active": False}, None),
        ("bookings", "b1", {"apartment_id": "1", "platform": "Booking", "gross_income": 540, "payment_status": "received",
                            "paid_out": True, "paid_out_at": "2026-05-29", "platform_pct": 20, "booking_fee": 57.12, "check_in": "2026-04-30",
                            "check_out": "2026-05-05", "nights": ""}, None),  # no currency: Booking → GEL
        ("bookings", "b2", {"apartment_id": "1", "platform": "Airbnb", "gross_income": "", "income_currency": "USD", "paid_out": ""}, None),
        ("bookings", "b3", {"apartment_id": "1", "platform": "Airbnb", "gross_income": 10}, "2026-11-11T20:05:52.876Z"),
        ("expenses", "e1", {"booking_id": "b1", "apartment_id": "1", "category": "cleaning", "amount": 49.54, "currency": "GEL", "payer": "platform"}, None),
        ("expenses", "e2", {"booking_id": "b1", "apartment_id": "1", "category": "supplies", "amount": 10, "currency": "USD", "payer": "owner"}, None),
        ("expenses", "e3", {"booking_id": "ghost", "apartment_id": "1", "category": "other", "amount": 5, "currency": "GEL", "payer": "platform"}, None),
        ("staff", "1", {"name": "Mike ", "role": "Cleaning ", "contact": 995511741008, "active": True}, None),
    ])
    res = apartflow.run(db, f, commit=True)
    assert res["errors"] == [] and res["committed"] and res["audit_moved"] == 1
    assert res["gross"] == {"GEL": 540} and res["owner_not_paid"] == {}
    [b1] = [b for b in R.bookings(db, rate=apartflow.CHECK_RATE) if b["legacy_id"] == "b1"]
    # 540 − fee 57.12 − (49.54 + 10 × 2.7) = 406.34 net; owner 80% 325.07 + 27 back
    assert (b1["currency"], b1["calc"]["net"], b1["calc"]["owner"], b1["calc"]["platform"]) == ("GEL", 406.34, 352.07, 187.93)
    assert apartflow.run(db, f, commit=True)["audit_moved"] == 0  # re-runnable
    assert db.query(Event).filter(Event.entity == "dr_booking", Event.actor_label == "import:sheets").count() == 1


def test_import_refuses_on_mismatch(db, tmp_path, monkeypatch):
    from app.importers import apartflow
    f = str(tmp_path / "af.db")
    _sqlite(f, [("apartments", "1", {"apartment_name": "A"}, None),
                ("bookings", "b1", {"apartment_id": "1", "platform": "Airbnb", "gross_income": 100, "income_currency": "USD"}, None)])
    monkeypatch.setattr(apartflow, "js_calc", lambda b, e, r: {"net": 1, "owner": 1, "platform": 1})
    res = apartflow.run(db, f, commit=True)
    assert res["errors"] and not res["committed"]
    assert R.bookings(db) == []  # rolled back
