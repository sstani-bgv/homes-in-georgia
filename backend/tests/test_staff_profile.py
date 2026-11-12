"""Employee profile (name + surname, birth date, phone, email) and team birthdays (Irakli 11-12)."""
from datetime import date, timedelta

from app import worker
from app.models import Job
from app.services import birthdays
from app.services.common import TZ, now
from tests.conftest import PROFILE, auth, make_user


def test_create_requires_full_profile(client, db):
    h = auth(make_user(db, "admin", name="Иракли Админ"))
    post = lambda **kw: client.post("/api/users", json={**PROFILE, "name": "Нино Агент", **kw}, headers=h)  # noqa: E731
    r = client.post("/api/users", json={"name": "Нино Агент", "phone": PROFILE["phone"]}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "profile_required"
    assert "дата рождения" in r.json()["detail"] and "email" in r.json()["detail"]
    assert post(name="Нино").json()["code"] == "profile_required"  # one word: the surname is missing
    assert post(email="nino@").json()["code"] == "bad_email"
    assert post(email="nino@mail").json()["code"] == "bad_email"
    assert post(phone="599 12").json()["code"] == "bad_phone"
    assert post(phone="звоните вечером").json()["code"] == "bad_phone"
    assert post(birth_date=(date.today() + timedelta(days=1)).isoformat()).json()["code"] == "bad_birth_date"
    r = post(email=" Nino@Homes.GE ")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "nino@homes.ge" and r.json()["profile_missing"] == []


def test_edit_keeps_old_users_working(client, db):
    h = auth(make_user(db, "admin", name="Иракли Админ"))
    old = make_user(db, "agent", name="Заза")  # imported without a profile
    assert client.patch(f"/api/users/{old.id}", json={"is_active": False}, headers=h).status_code == 200
    r = client.patch(f"/api/users/{old.id}", json={"name": "Заза Беридзе"}, headers=h)  # the admin's form: all or nothing
    assert r.json()["code"] == "profile_required"
    assert client.patch(f"/api/users/{old.id}", json={**PROFILE, "name": "Заза Беридзе"}, headers=h).status_code == 200


def test_me_fills_own_profile(client, db):
    me = make_user(db, "agent", name="Заза", login="zaza")
    h = auth(me)
    assert client.get("/api/auth/me", headers=h).json()["profile_missing"] == ["name", "birth_date", "phone", "email"]
    r = client.patch("/api/auth/me", json={"birth_date": "1991-02-28"}, headers=h)  # a field at a time
    assert r.status_code == 200 and r.json()["profile_missing"] == ["name", "phone", "email"]
    assert client.patch("/api/auth/me", json={"email": "bad"}, headers=h).json()["code"] == "bad_email"
    assert client.patch("/api/auth/me", json={"phone": ""}, headers=h).json()["code"] == "profile_required"
    r = client.patch("/api/auth/me", json={"name": "Заза Беридзе", "phone": "599111222", "email": "z@homes.ge"}, headers=h)
    assert r.json()["profile_missing"] == [] and r.json()["email"] == "z@homes.ge"
    assert client.patch("/api/auth/me", json={"role": "superadmin"}, headers=h).status_code == 200  # ignored field
    db.refresh(me)
    assert me.role == "agent"


def test_email_and_age_private(client, db):
    adm = make_user(db, "admin", name="Иракли Админ")
    op = make_user(db, "operator", name="Ана Оператор")
    ag = make_user(db, "agent", name="Заза Беридзе", email="z@homes.ge", birth_date=date(1990, 5, 4))
    rows = lambda who: {u["id"]: u for u in client.get("/api/users", headers=auth(who)).json()["items"]}  # noqa: E731
    assert rows(adm)[ag.id]["email"] == "z@homes.ge" and rows(adm)[ag.id]["birth_date"] == "1990-05-04"
    assert rows(op)[ag.id]["email"] is None and rows(op)[ag.id]["birth_date"] is None
    assert rows(op)[ag.id]["birthday"] == "05-04"
    assert "email" not in rows(make_user(db, "agent", name="Другой Агент"))[ag.id]
    c = client.get("/api/me/cabinet", params={"user_id": ag.id}, headers=auth(op)).json()["user"]
    assert c["email"] is None and c["birth_date"] is None


def test_upcoming_feb29_and_inactive(db):
    make_user(db, "agent", name="Лиза Високосная", birth_date=date(2000, 2, 29))
    make_user(db, "agent", name="Уволен Ушёл", birth_date=date(1990, 2, 28), is_active=False)
    make_user(db, "agent", name="Март Первый", birth_date=date(1985, 3, 1))
    db.commit()
    got = birthdays.upcoming(db, date(2027, 2, 28), 0)  # 2027 isn't leap: Feb 29 → Feb 28
    assert [x["name"] for x in got] == ["Лиза Високосная"] and got[0]["age"] == 27
    assert [x["name"] for x in birthdays.upcoming(db, date(2028, 2, 28), 0)] == []  # 2028 is leap: Feb 29 itself
    assert [x["name"] for x in birthdays.upcoming(db, date(2028, 2, 29), 0)] == ["Лиза Високосная"]
    week = birthdays.upcoming(db, date(2027, 2, 25), 7)
    assert [(x["name"], x["days_left"]) for x in week] == [("Лиза Високосная", 3), ("Март Первый", 4)]
    assert [x["name"] for x in birthdays.upcoming(db, date(2026, 12, 30), 7)] == []  # wraps into next year fine


def _run(db, ref):
    job = Job(kind="birthdays", ref=ref, due_at=now(), payload={})
    db.add(job)
    db.commit()
    return worker.run_job(db, job)


def test_birthday_job(db, sent):
    today = now().astimezone(TZ).date()
    tomorrow = today + timedelta(days=1)
    make_user(db, "agent", name="Именинник Сегодня", birth_date=today.replace(year=1990), tg_id=1)
    make_user(db, "agent", name="Именинник Завтра", birth_date=tomorrow.replace(year=1995), tg_id=2)
    make_user(db, "agent", name="Неактивный Тоже", birth_date=today.replace(year=1991), is_active=False)
    make_user(db, "agent", name="Через Неделю", birth_date=(today + timedelta(days=7)).replace(year=1992))
    db.commit()
    assert _run(db, today.isoformat()) == "ok"
    (people,), = sent.of("birthdays_today")
    assert [x["name"] for x in people] == ["Именинник Сегодня"]
    (people,), = sent.of("birthdays_tomorrow")
    assert [x["name"] for x in people] == ["Именинник Завтра"] and people[0]["age"] == tomorrow.year - 1995
    assert _run(db, (today - timedelta(days=1)).isoformat()) == "stale"  # a missed morning isn't sent late
    assert len(sent.of("birthdays_today")) == 1


def test_ensure_job_once_per_day(db):
    birthdays.ensure_job(db)
    birthdays.ensure_job(db)
    rows = db.query(Job).filter(Job.kind == "birthdays").all()
    assert len(rows) == 1 and rows[0].due_at.astimezone(TZ).hour == birthdays.HOUR


def test_birthdays_widget(client, db):
    today = now().astimezone(TZ).date()
    make_user(db, "agent", name="Заза Беридзе", birth_date=(today + timedelta(days=2)).replace(year=1990))
    adm, ag = make_user(db, "admin", name="Иракли Админ"), make_user(db, "agent", name="Нино Агент")
    a = client.get("/api/birthdays", headers=auth(adm)).json()["items"]
    assert a[0]["name"] == "Заза Беридзе" and a[0]["days_left"] == 2 and a[0]["age"] is not None
    assert client.get("/api/birthdays", headers=auth(ag)).json()["items"][0]["age"] is None


def test_first_last_name(client, db, monkeypatch):
    h = auth(make_user(db, "admin", name="Иракли Админ"))
    r = client.post("/api/users", json={**PROFILE, "first_name": " Мария Анна ", "last_name": "Беридзе"}, headers=h)
    assert r.status_code == 200, r.text
    u = r.json()
    assert (u["first_name"], u["last_name"], u["name"]) == ("Мария Анна", "Беридзе", "Мария Анна Беридзе")
    assert client.post("/api/users", json={**PROFILE, "first_name": "Нино"}, headers=h).json()["code"] == "profile_required"
    r = client.patch(f"/api/users/{u['id']}", json={**PROFILE, "last_name": "Гелашвили"}, headers=h)
    assert r.json()["name"] == "Мария Анна Гелашвили"
    old = make_user(db, "agent", name="Заза Беридзе Младший")  # set as a whole name: split at the first space
    assert (old.first_name, old.last_name) == ("Заза", "Беридзе Младший")
    r = client.patch(f"/api/users/{old.id}", json={**PROFILE, "name": "Гия Канчели"}, headers=h)  # old API callers
    assert (r.json()["first_name"], r.json()["last_name"]) == ("Гия", "Канчели")


def test_birth_date_from_telegram(db, monkeypatch):
    from app.services import users
    from app.telegram import client as tg
    monkeypatch.undo()  # the real users.tg_birth_date, a fake getChat
    replies = {1: {"birthdate": {"day": 4, "month": 5, "year": 1990}}, 2: {"birthdate": {"day": 4, "month": 5}}, 3: None}
    monkeypatch.setattr(tg, "call", lambda method, chat_id: replies[chat_id])
    assert users.tg_birth_date(1) == date(1990, 5, 4)
    assert users.tg_birth_date(2) is None  # no year: no date
    assert users.tg_birth_date(3) is None  # hidden / not reachable
