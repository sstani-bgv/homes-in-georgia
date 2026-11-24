"""«Посуточная аренда» on autopilot (services/daily_rent_auto.py): Airbnb reservations become stays by themselves and follow
the platform, the «Сегодня» screen and digest, cleanings for check-outs, owner reports on the 1st."""
from datetime import date, datetime, timedelta

import pytest

from app.models import DrBooking, DrCleaning
from app.services import daily_rent as R
from app.services import daily_rent_auto as A
from app.services import daily_rent_ical as C
from tests.conftest import auth, make_user
from tests.test_daily_rent import apartment, api

T = date(2026, 11, 23)


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(R, "usd_gel", lambda: 2.5)
    monkeypatch.setattr(R, "today", lambda: T)


@pytest.fixture
def boss(db):
    return make_user(db, "admin", name="Ираклий")


def feed(*events):
    body = "".join(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART;VALUE=DATE:{s:%Y%m%d}\r\nDTEND;VALUE=DATE:{e:%Y%m%d}\r\nSUMMARY:{summ}\r\n"
                   + (f"DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/details/{code}\\nPhone Number \r\n"
                      " (Last 4 Digits): 1234\r\n" if code else "") + "END:VEVENT\r\n" for uid, s, e, summ, code in events)
    return "BEGIN:VCALENDAR\r\n" + body + "END:VCALENDAR\r\n"


def d(n):
    return T + timedelta(days=n)


def setup(client, boss, **kw):
    a = apartment(client, boss, **kw)
    ch = api(client, boss, "POST", "/channels", {"apartment_id": a["id"], "channel": "airbnb", "import_url": "https://www.airbnb.com/calendar/ical/1.ics"})
    return a, ch


def live(db):
    return db.query(DrBooking).filter(DrBooking.deleted_at.is_(None)).order_by(DrBooking.check_in).all()


def test_airbnb_reservation_becomes_a_stay_and_follows_the_platform(client, db, boss):
    _, ch = setup(client, boss)
    f = feed(("r1", d(2), d(6), "Reserved", "HMABC12345"), ("na", d(10), d(12), "Airbnb (Not available)", None))
    C.sync_channel(db, ch["id"], fetcher=lambda u: f)
    assert A.auto_stays(db) == {"created": 1, "moved": 0, "removed": 0, "flagged": 0}
    [b] = live(db)
    assert (b.platform, b.check_in, b.check_out, b.currency, float(b.tax_pct), b.gross, b.payment_status) == \
        ("Airbnb", d(2), d(6), "USD", 1.0, None, "pending")
    assert b.extra["code"] == "HMABC12345" and "HMABC12345" in b.comment
    assert A.auto_stays(db)["created"] == 0  # idempotent
    # no longer «не внесено в учёт»: the reservation has its stay
    assert C.analyze(db)["unaccounted"] == []
    # the guest changed the dates on Airbnb → the stay (still without money) follows
    C.sync_channel(db, ch["id"], fetcher=lambda u: feed(("r1", d(3), d(8), "Reserved", "HMABC12345")))
    assert A.auto_stays(db)["moved"] == 1 and (live(db)[0].check_in, live(db)[0].check_out) == (d(3), d(8))
    # an empty calendar (a failed / reset feed) removes nothing (Stanislav's review 11-24)
    C.sync_channel(db, ch["id"], fetcher=lambda u: feed())
    assert A.auto_stays(db)["removed"] == 0 and len(live(db)) == 1
    # cancelled on Airbnb before any money (the calendar still has other reservations) → removed
    C.sync_channel(db, ch["id"], fetcher=lambda u: feed(("r2", d(20), d(22), "Reserved", "HMOTHER123")))
    assert A.auto_stays(db)["removed"] == 1 and [b.check_in for b in live(db)] == [d(20)]


def test_cancelled_reservation_with_money_is_kept_and_flagged(client, db, boss):
    _, ch = setup(client, boss)
    C.sync_channel(db, ch["id"], fetcher=lambda u: feed(("r1", d(2), d(6), "Reserved", "HMX1234567")))
    A.auto_stays(db)
    [b] = live(db)
    api(client, boss, "PATCH", f"/bookings/{b.id}", {"gross": 300})
    C.sync_channel(db, ch["id"], fetcher=lambda u: feed(("r2", d(20), d(22), "Reserved", "HMOTHER123")))
    assert A.auto_stays(db)["flagged"] == 1 and len(live(db)) == 2
    assert any(x["kind"] == "cancelled_with_money" for x in A.today(db)["alerts"])


def test_a_stay_entered_by_hand_is_not_duplicated(client, db, boss):
    a, ch = setup(client, boss)
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Airbnb", "guest_name": "Val", "gross": 500,
                                            "check_in": d(2).isoformat(), "check_out": d(6).isoformat()})
    C.sync_channel(db, ch["id"], fetcher=lambda u: feed(("r1", d(2), d(6), "Reserved", "HMABC12345")))
    assert A.auto_stays(db)["created"] == 0 and len(live(db)) == 1


def test_booking_feed_creates_nothing(client, db, boss):
    a = apartment(client, boss)
    bk = api(client, boss, "POST", "/channels", {"apartment_id": a["id"], "channel": "booking", "import_url": "https://ical.booking.com/v1/export?t=1"})
    C.sync_channel(db, bk["id"], fetcher=lambda u: feed(("b1", d(2), d(6), "CLOSED - Not available", None)))
    assert A.auto_stays(db)["created"] == 0


def test_old_reservations_are_history(client, db, boss):
    _, ch = setup(client, boss)
    C.sync_channel(db, ch["id"], fetcher=lambda u: feed(("old", d(-100), d(-90), "Reserved", "HMOLD12345")))
    assert A.auto_stays(db)["created"] == 0


def test_today_digest_and_cleanings(client, db, boss):
    a = apartment(client, boss, name="Vake Nest")
    mk = lambda g, ci, co, gross=100, staff=None: api(client, boss, "POST", "/bookings", {  # noqa: E731
        "apartment_id": a["id"], "platform": "Booking", "guest_name": g, "gross": gross, "assigned_staff": staff,
        "check_in": ci.isoformat(), "check_out": co.isoformat()})
    mk("Anna", d(-3), d(0), staff="Nazgul")
    mk("Boris", d(0), d(4), gross=None)
    mk("Clara", d(5), d(7))
    v = A.today(db)
    assert [x["guest"] for x in v["departures"]] == ["Anna"]
    assert [x["guest"] for x in v["arrivals"]] == ["Boris"] and [x["guest"] for x in v["living"]] == ["Boris"]
    assert [x["guest"] for x in v["upcoming"]] == ["Clara"]
    assert [x["guest"] for x in v["no_money"]] == ["Boris"]
    assert v["load"][0]["busy"] == 6  # Boris 4 + Clara 2 of the next 30 nights
    assert any(x["kind"] == "gap" for x in v["alerts"])  # free from d(7) to d(14)
    assert A.auto_cleanings(db) == 1 and A.auto_cleanings(db) == 0
    [c] = db.query(DrCleaning).all()
    assert (c.date, c.cleaner, float(c.hourly_rate)) == (T, "Nazgul", 16.7) and "Anna" in c.notes and d(0).strftime("%d.%m") in c.notes
    text = A.digest_text(A.today(db))
    assert "Заезжают сегодня" in text and "Boris" in text and "Без суммы" in text and "Vake Nest — Nazgul" in text
    assert client.get("/api/daily-rent/today", headers=auth(make_user(db, "daily_rent_manager"))).status_code == 200


def test_hidden_apartment_with_bookings_is_flagged(client, db, boss):
    a = apartment(client, boss, name="River Crown")
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Booking", "guest_name": "W", "gross": 1,
                                            "check_in": d(20).isoformat(), "check_out": d(30).isoformat()})
    api(client, boss, "PATCH", f"/apartments/{a['id']}", {"active": False})
    assert [x["kind"] for x in A.today(db)["alerts"]] == ["hidden_booked"]


def test_morning_tick_sends_once_and_reports_start_next_month(client, db, boss, sent, monkeypatch):
    from app import worker
    a = apartment(client, boss)
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Airbnb", "gross": 100, "check_in": "2026-10-05", "check_out": "2026-10-08"})
    api(client, boss, "POST", "/bookings", {"apartment_id": a["id"], "platform": "Airbnb", "gross": 200, "check_in": "2026-11-05", "check_out": "2026-11-08"})
    monkeypatch.setattr(A, "now", lambda: datetime(2026, 11, 23, 8, tzinfo=R.TZ))
    worker.daily_rent_morning_tick()
    assert sent.of("daily_rent_digest") == []  # before 09:00
    monkeypatch.setattr(A, "now", lambda: datetime(2026, 11, 23, 10, tzinfo=R.TZ))
    worker.daily_rent_morning_tick()
    worker.daily_rent_morning_tick()
    assert len(sent.of("daily_rent_digest")) == 1
    assert sent.of("daily_rent_owner_reports") == []  # the deploy day only remembers October
    monkeypatch.setattr(R, "today", lambda: date(2026, 12, 1))
    worker.daily_rent_morning_tick()
    [(reports, month)] = sent.of("daily_rent_owner_reports")
    assert month == "2026-11" and [r["apartment"] for r in reports] == ["Vake Nest"] and "05.11.26" in reports[0]["text"]
    worker.daily_rent_morning_tick()
    assert len(sent.of("daily_rent_owner_reports")) == 1
    assert A.owner_reports(db, "2026-10")[0]["text"].startswith("🏠 Vake Nest")
