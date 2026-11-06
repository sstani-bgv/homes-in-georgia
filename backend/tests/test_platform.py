import hashlib
import hmac
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest
from sqlalchemy import func, select, update

from app import worker
from app.importers import sheets_csv
from app.models import Deal, Evaluation, Job, Owner, Report, TickerMessage, User
from app.services import deals, evaluations, jobs, reports, users
from app.services.errors import DomainError
from tests.conftest import REPO, auth, make_user

BOT_TOKEN = "123456:TEST-TOKEN"


# ---------------------------------------------------------------- commission (apps-script saveRentalWithT0)

def js_round(x):
    """JS Math.round: half up (Python round() is banker's)."""
    return math.floor(x + 0.5)


def apps_script(price, pct=50, level=50, client_src=False, recruit_amt=0):
    comm = js_round(price * pct / 100)
    plat = comm * (100 - level) / 100
    return comm, plat, comm - plat - recruit_amt - (js_round(comm * 0.1) if client_src else 0)


@pytest.mark.parametrize("price,pct,level,src,rec", [(1000, 50, 50, False, 0), (850, 50, 70, False, 0),
                                                     (1500, 100, 90, True, 0), (777, 50, 50, True, 30),
                                                     (2400, 40, 70, False, 100)])
def test_commission_matches_apps_script(price, pct, level, src, rec):
    r = deals.calc(price=price, commission_pct=pct, agent_level=level, client_src="Сандро" if src else None,
                   recruit_amount=rec)
    comm, plat, agent = apps_script(price, pct, level, src, rec)
    assert (r["commission_total"], r["platform_amount"], r["agent_amount"]) == (comm, plat, agent)
    assert r["platform_pct"] == 100 - level


def test_commission_rounds_half_up_like_js():
    assert deals.calc(price=1005, commission_pct=50)["commission_total"] == 503
    assert deals.calc(price=50, commission_pct=50, client_src="Сандро")["client_src_amount"] == 3  # 10% of 25


def test_commission_explicit_values_and_rerented():
    r = deals.calc(price=1000, commission_total=600, platform_amount=100, agent_level=50, recruit_pct=10)
    assert (r["commission_total"], r["platform_amount"], r["recruit_amount"], r["agent_amount"]) == (600, 100, 60, 440)
    assert deals.calc(price=900, is_rerented=True)["commission_total"] == 0


def test_levels():
    assert [users.level_for(n) for n in (0, 10, 11, 100, 101)] == [50, 50, 70, 70, 90]
    assert users.next_level_at(9) == {"level": 70, "at": 11, "deals_left": 2} and users.next_level_at(500) is None


def test_exclusive_confirm_levels_up(db, sent):
    op = make_user(db, "operator")
    ag = make_user(db, "agent", exclusive_deals=10)
    d = deals.create(db, {"agent_id": ag.id, "price": 1000, "is_exclusive": True, "rent_from": "2026-01-10",
                          "rent_to": "2027-01-10"}, op)
    assert d.agent_level == 50 and d.platform_amount == 250 and d.month == "1/2026" and d.t14_date == date(2026, 1, 24)
    deals.confirm(db, d.id, op)
    db.refresh(ag)
    assert ag.exclusive_deals == 11 and ag.level == 70 and d.agent_deal_num == 11
    d2 = deals.create(db, {"agent_id": ag.id, "price": 1000}, op)
    assert d2.agent_level == 70 and float(d2.agent_amount) == 350
    deals.cancel(db, d.id, op, "ошибка")
    db.refresh(ag)
    assert ag.exclusive_deals == 10 and not jobs.pending(db, "t14_client", d.id)


def test_low_t14_alerts_operators(db, sent):
    ag = make_user(db, "agent")
    d = deals.create(db, {"agent_id": ag.id, "price": 500}, ag)
    evaluations.create(db, {"deal_id": d.id, "kind": "t14", "h2o_client": 3, "h2o_owner": 3, "h2o_process": 3}, ag)
    assert not sent.of("evaluation_alert")
    evaluations.create(db, {"deal_id": d.id, "kind": "t14", "author": "client", "h2o_client": 1, "h2o_owner": 3,
                            "h2o_process": 3}, None)
    assert len(sent.of("evaluation_alert")) == 1 and db.get(Deal, d.id).t14_status == "done"
    with pytest.raises(DomainError):
        evaluations.create(db, {"deal_id": d.id, "h2o_client": 5}, ag)


# ---------------------------------------------------------------- auth

def init_data(user: dict, token=BOT_TOKEN, auth_date=None) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAE", "user": json.dumps(user)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_webapp_auth(db, client):
    make_user(db, "agent", "Георгий", tg_id=111222333)
    r = client.post("/api/auth/telegram-webapp", json={"init_data": init_data({"id": 111222333, "username": "geo"})})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["username"] == "geo" and me["role"] == "agent"
    assert client.post("/api/auth/telegram-webapp",
                       json={"init_data": init_data({"id": 111222333}, token="999:WRONG")}).status_code == 401
    tampered = init_data({"id": 111222333}).replace("111222333", "111222334")
    assert client.post("/api/auth/telegram-webapp", json={"init_data": tampered}).status_code == 401
    old = init_data({"id": 111222333}, auth_date=int(time.time()) - 2 * 86400)
    assert client.post("/api/auth/telegram-webapp", json={"init_data": old}).status_code == 401
    r = client.post("/api/auth/telegram-webapp", json={"init_data": init_data({"id": 5})})
    assert r.status_code == 403 and r.json()["code"] == "not_registered"


def test_login_widget_and_password_login(db, client):
    make_user(db, "operator", "Нино", tg_id=444, login="nino")
    data = {"id": 444, "first_name": "Нино", "auth_date": int(time.time())}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(hashlib.sha256(BOT_TOKEN.encode()).digest(), dcs.encode(), hashlib.sha256).hexdigest()
    assert client.post("/api/auth/telegram-login", json=data).status_code == 200
    assert client.post("/api/auth/telegram-login", json={**data, "hash": "0" * 64}).status_code == 401
    r = client.post("/api/auth/login", json={"login": "NINO", "password": "pw"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "operator"
    assert client.post("/api/auth/login", json={"login": "nino", "password": "bad"}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_role_checks(db, client):
    ag = make_user(db, "agent")
    assert client.get("/api/settings", headers=auth(ag)).status_code == 403
    assert client.post("/api/users", json={"name": "X"}, headers=auth(ag)).status_code == 403
    admin = make_user(db, "admin")
    r = client.post("/api/users", json={"name": "Новый", "role": "recruit", "mentor_id": ag.id}, headers=auth(admin))
    assert r.status_code == 200
    r = client.post(f"/api/users/{r.json()['id']}/promote", json={"level": 50}, headers=auth(admin))
    assert r.json()["role"] == "agent"
    assert client.patch(f"/api/users/{ag.id}", json={"role": "superadmin"}, headers=auth(admin)).status_code == 403


def test_bootstrap_superadmin(db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_LOGIN", "root")
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", "s3cret")
    monkeypatch.setattr(settings, "SUPERADMIN_TG_IDS", "100, 200")
    created = users.ensure_bootstrap(db)
    assert len(created) == 2 and users.check_password("s3cret", users.by_login(db, "root").password_hash)
    assert users.by_tg(db, 200).role == "superadmin"
    assert users.ensure_bootstrap(db) == []


# ---------------------------------------------------------------- importer

def test_importer_runs_on_legacy_sheets_idempotently(db):
    folder = REPO / "data" / "legacy-sheets"
    first = sheets_csv.run(folder, db)
    assert first["Agents"].get("imported") == 1 and first["Reports_Deals"].get("imported") == 1
    assert first["Platform_Desk_Users"].get("imported") == 1
    counts = lambda: (db.scalar(select(func.count()).select_from(User)), db.scalar(select(func.count()).select_from(Deal)),
                      db.scalar(select(func.count()).select_from(Report)), db.scalar(select(func.count()).select_from(Owner)))  # noqa: E731
    before = counts()
    second = sheets_csv.run(folder, db)
    assert counts() == before
    assert not any(st.get("imported") for st in second.values())
    desk = db.scalar(select(User).where(User.login == "DEMO_01"))
    assert desk.password_hash and desk.password_hash != "DEMO_NOT_A_VALID_PASSWORD"
    assert db.scalar(select(func.count()).select_from(Evaluation)) == 2
    assert db.scalar(select(TickerMessage)).legacy_id == "DEMO_01"


def test_importer_tolerates_junk(tmp_path, db):
    (tmp_path / "Agents.csv").write_text("id,name,telegram,is_active,level,,photo_url,user_id\n1,Гия,@gia,TRUE,70,,,123456789\n"
                                         ",,,,,,,\nx\n", encoding="utf-8-sig")
    (tmp_path / "Reports_Deals.csv").write_text("column_01\n" + "2026-03-01,1,Гия,70,3,https://l,1000,500,350,150,"
                                                 "01.01,К,+995,О,+995,01.03.2026,Эксклюзив,miniapp,,rent,3/2026\n"
                                                 "garbage,row\n", encoding="utf-8-sig")
    (tmp_path / "Owner_Base.csv").write_text("Name of Owner,Telephone,Address,Link\nА,,,\n", encoding="utf-8-sig")
    stats = sheets_csv.run(tmp_path, db)
    assert stats["Agents"]["imported"] == 1 and stats["Owner_Base"]["skipped"] == 1
    d = db.scalar(select(Deal).where(Deal.price == 1000))
    gia = db.scalar(select(User).where(User.name == "Гия"))
    assert d.agent_id == gia.id and d.is_exclusive and d.month == "3/2026" and gia.level == 70 and gia.tg_id == 123456789


# ---------------------------------------------------------------- worker

def test_worker_dispatch_retries_and_stale(db, sent, monkeypatch):
    ag, op = make_user(db, "agent"), make_user(db, "operator")
    d = deals.create(db, {"agent_id": ag.id, "price": 1000, "rent_from": date.today().isoformat(),
                          "rent_to": (date.today() + timedelta(days=20)).isoformat()}, op)
    deals.confirm(db, d.id, op)
    db.execute(update(Job).values(due_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    stats = worker.tick()
    assert sent.of("t14_client") and sent.of("t14_agent") and [a[1] for a in sent.of("contract_expiry")] == [7, 1]
    assert stats.get("ok") == 4
    db.expire_all()
    assert db.get(Deal, d.id).t14_status == "sent"

    jobs.schedule(db, "unknown_kind", 1, datetime.now(timezone.utc))
    jobs.schedule(db, "contract_expiry", f"{d.id}:30", datetime.now(timezone.utc), {"days": 30})
    db.commit()

    def boom(*_):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(worker.notify, "contract_expiry", boom)
    stats = worker.tick()
    assert stats == {"unknown": 1, "error": 1}
    job = db.scalar(select(Job).where(Job.ref == f"{d.id}:30"))
    db.refresh(job)
    assert job.done_at is None and job.attempts == 1 and "telegram down" in job.last_error and job.due_at > datetime.now(timezone.utc)
    assert worker.tick() == {}


def test_reports_counters_and_texts(db, sent):
    mentor = make_user(db, "agent", "Георгий")
    rec = make_user(db, "recruit", "Сандро", mentor_id=mentor.id)
    for _ in range(2):
        r = reports.save(db, "photo", {"district_address": "Ваке", "ad_link_advertising": "https://x"}, rec)
    assert r.data["counter"] == 2 and r.data["mentor"] == "Георгий" and "Учёт: <b>2/15</b>" in r.posts[0]["text"]
    assert r.text.startswith("📷 #Фотография | Отчёт")
    g = reports.save(db, "general", {"photo": 2, "showing": 1, "stickers": 0}, mentor)
    assert [p["topic"] for p in g.posts] == ["photo", "showing"]
    c = {x["user"]["name"]: x for x in reports.counters(db)}
    assert c["Сандро"]["photo"] == {"done": 2, "plan": 15}
    assert len(sent.of("report_posted")) == 3


def test_ics_and_dashboard(db, client, sent):
    from app.services import ics, leads
    from tests.conftest import make_client, make_listing
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    c = make_client(db)
    lead = leads.create_from_like(db, c.id, make_listing(db, source="agency").id)
    leads.assign_agent(db, lead.id, ag.id, op)
    leads.add_slots(db, lead.id, [datetime(2030, 5, 1, 12, 0)], ag)
    from app.models import LeadSlot
    leads.choose_slot(db, db.scalar(select(LeadSlot.id)), c.id)
    r = client.get(f"/api/public/ics/{ics.ics_token(lead.id)}")
    assert r.status_code == 200 and "DTSTART:20300501T080000Z" in r.text
    assert client.get("/api/public/ics/bad").status_code == 404
    dash = client.get("/api/analytics/dashboard", headers=auth(op)).json()
    assert dash["open_leads"] == 1 and dash["funnel"]["leads"]["time_chosen"] == 1
    assert dash["response_times"]["agent_slots_avg_min"] is not None
    cab = client.get("/api/me/cabinet", headers=auth(ag)).json()
    assert cab["upcoming_viewings"][0]["id"] == lead.id and cab["level"]["level"] == 50
