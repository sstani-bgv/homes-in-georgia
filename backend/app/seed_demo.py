"""Demo data matching docs/user-journey.txt (operator Нино, agent Георгий, client Анна ...). `python -m app.seed_demo`

Runs through the real services (so history, jobs and SLA timers look real). Skips if the demo users exist.
Logins: david / nino / georgiy (password `demo`)."""
import sys
from datetime import date, datetime, timedelta, timezone

from unittest.mock import patch

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Client, Contract, Job, Lead, LeadSlot, Listing, Property, TickerMessage, User
from app.services import deals, evaluations, leads, properties, reports, users
from app.services.common import jdump, now, now_text
from app.telegram import notify

PHOTO = "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800"
LISTINGS = [
    ("ss5001", "ss", 850, 2, 72, 5, "ул. Палиашвили 24", "Ваке", "Светлая квартира с балконом, собственник", 0, "[]"),
    ("ss5002", "ss", 650, 1, 48, 9, "пр. Пекини 12", "Сабуртало", "Новый ремонт, рядом метро", 0, "[]"),
    ("mh5003", "myhome", 1200, 3, 110, 7, "ул. Абашидзе 40", "Ваке", "Большая квартира для семьи", 0, "[]"),
    ("mh5004", "myhome", 700, 2, 65, 3, "ул. Церетели 101", "Дидубе", "Тихий двор, можно с животными", 1,
     '["один телефон в 6 объявлениях"]'),
    ("fb5005", "fb:tbilisi_rent", 550, 1, 40, 2, "ул. Марджанишвили 5", "Чугурети", "Студия в центре", 0, "[]"),
    ("tg_rent_5006", "tg:competitor", 950, 2, 80, 12, "ул. Казбеги 15", "Сабуртало", "Вид на горы", 1, "[]"),
    ("ss5007", "ss", 1500, 3, 140, 4, "ул. Чавчавадзе 33", "Ваке", "Премиум, эксклюзив", 0, "[]"),
    ("ss5008", "ss", 600, 1, 45, 6, "ул. Гамсахурдиа 8", "Сабуртало", "Уютная квартира", 0, "[]"),
    ("ss5009", "ss", 780, 2, 68, 8, "ул. Костава 70", "Мтацминда", "Рядом с парком", 0, "[]"),
    ("ss5010", "ss", 900, 2, 75, 10, "ул. Барнова 50", "Ваке", "Тёплый пол, кондиционеры", 0, "[]"),
    ("ss5011", "ss", 720, 2, 60, 2, "ул. Тамарашвили 3", "Ваке", "Рядом с университетом", 0, "[]"),
    ("ss5012", "ss", 1100, 3, 95, 15, "ул. Нуцубидзе 77", "Сабуртало", "Панорамные окна", 0, "[]"),
    ("ss5013", "ss", 640, 1, 50, 5, "ул. Джавахишвили 9", "Мтацминда", "Старый Тбилиси", 0, '["дубль: agency"]'),
    ("ss5014", "ss", 990, 2, 82, 11, "ул. Кипшидзе 2", "Ваке", "Новый дом", 0, "[]"),
]


def _user(db, **f):
    pw = f.pop("password", None)
    u = User(**f)
    if pw:
        u.password_hash = users.hash_password(pw)
    db.add(u)
    db.flush()
    return u


def _client(db, ext, name, username, phone, lang, profile):
    t = now_text()
    c = Client(channel="tg", external_id=str(ext), chat_id=str(ext), username=username, name=name, phone=phone, lang=lang,
               state="{}", profile=jdump(profile), profile_done=1, created_at=t, updated_at=t)
    db.add(c)
    db.flush()
    return c


def seed():
    db = SessionLocal()
    if db.scalar(select(User).where(User.login == "georgiy")):
        print("demo data already present (login 'georgiy' exists), nothing to do")
        return
    david = _user(db, name="Давид", role="superadmin", login="david", password="demo", username="david_hig")
    nino = _user(db, name="Нино", role="operator", login="nino", password="demo", username="nino_operator")
    _user(db, name="Мариам", role="operator", login="mariam", password="demo")
    georgiy = _user(db, name="Георгий", role="agent", login="georgiy", password="demo", username="georgiy_agent", level=70,
                    exclusive_deals=13, phone="+995 599 11 22 33", districts=["Ваке", "Сабуртало"])
    levan = _user(db, name="Леван", role="agent", login="levan", password="demo", level=50, exclusive_deals=4,
                  phone="+995 577 44 55 66", districts=["Дидубе", "Чугурети"])
    ana = _user(db, name="Ана", role="agent", level=90, exclusive_deals=104, districts=["Мтацминда"])
    sandro = _user(db, name="Сандро", role="recruit", mentor_id=georgiy.id, plans={"photo": 15, "showing": 15,
                                                                                    "stickers": 15, "rental": 10})
    t = now_text()
    for lid, src, price, bd, area, floor, addr, distr, desc, suspect, reasons in LISTINGS:
        dups = '["agency"]' if "дубль" in reasons else "[]"
        db.add(Listing(id=lid, status="published", source=src, urls=jdump([f"https://example.invalid/{lid}"]),
                       dup_sources=dups, price_usd=price, bedrooms=bd, area=area, floor=floor, address=addr, district=distr,
                       photos=jdump([PHOTO]), description_ru=desc, agent_suspect=suspect,
                       agent_reasons=reasons if "дубль" not in reasons else "[]",
                       text=f"{desc}. Звоните: +995 555 {lid[-4:-2]} {lid[-2:]} 10", first_seen=t, last_seen=t, updated_at=t))
    anna = _client(db, 5550001, "Анна", "anna_demo", "+995 555 000 111", "ru",
                   {"residents": "2", "pets": ["cat"], "terms": ["6", "12"], "budget_max": 900, "bedrooms": [2],
                    "districts": ["Ваке", "Сабуртало"], "move_in": (date.today() + timedelta(days=10)).isoformat()})
    mike = _client(db, 5550002, "Michael", "mike_demo", "+995 555 000 222", "en",
                   {"residents": "1", "pets": ["none"], "terms": ["12+"], "budget_max": 700, "bedrooms": [1],
                    "districts": ["any"]})
    tamar = _client(db, 5550003, "Тамара", "tamar_demo", "+995 555 000 333", "ru",
                    {"residents": "3", "pets": ["dog"], "terms": ["12"], "budget_max": 1300, "bedrooms": [3],
                     "districts": ["Ваке"]})
    extra = [_client(db, 5550010 + i, n, None, f"+995 555 000 4{i}0", "ru", {"residents": "2", "budget_max": 800})
             for i, n in enumerate(["Ника", "Ирина", "Дато", "Елена", "Гела", "Софо"])]
    for u in ("Ваке, Палиашвили 24", "Сабуртало, Пекини 12"):
        db.add(TickerMessage(text=f"Новый эксклюзив: {u}", status="active"))
    db.add(TickerMessage(text="План на ноябрь: 15 съёмок на каждого рекрута", status="active"))
    db.commit()

    # ---------------- funnel 1: objects in different statuses
    future = now() + timedelta(days=1)
    p = properties.take(db, "ss5001", georgiy)                                    # taken
    p = properties.take(db, "ss5002", georgiy)
    properties.call_result(db, p.id, georgiy, False)                              # call_failed
    p = properties.take(db, "mh5004", levan)
    properties.call_result(db, p.id, levan, True)
    properties.owner_or_agent(db, p.id, levan, False, "Агентство Дом", "+995 577 90 90 90")   # agent_marked
    p = properties.take(db, "ss5008", levan)
    properties.call_result(db, p.id, levan, True)
    properties.owner_or_agent(db, p.id, levan, True, "Гиорги Беридзе", "+995 555 12 34 56")  # owner_confirmed
    p = properties.take(db, "ss5007", georgiy)
    properties.call_result(db, p.id, georgiy, True)
    properties.owner_or_agent(db, p.id, georgiy, True, "Нана Кобахидзе", "+995 599 22 33 44")
    properties.schedule_shoot(db, p.id, georgiy, future)                          # shoot_scheduled
    pub = []
    for lid, owner, phone, final in (("mh5003", "Зураб", "+995 593 10 20 30", "review"),
                                      ("ss5009", "Тея", "+995 593 40 50 60", "returned"),
                                      ("ss5010", "Леван Г.", "+995 593 70 80 90", "published"),
                                      ("ss5011", "Мзия", "+995 591 11 11 11", "published"),
                                      ("ss5012", "Котэ", "+995 591 22 22 22", "published")):
        ag = georgiy if lid != "ss5009" else levan
        p = properties.take(db, lid, ag)
        properties.call_result(db, p.id, ag, True)
        properties.owner_or_agent(db, p.id, ag, True, owner, phone)
        properties.schedule_shoot(db, p.id, ag, future)
        properties.upload_media(db, p.id, [("living.jpg", _tiny_jpeg())], "living_room", ag)
        properties.set_checklist(db, p.id, {"living_room": True, "kitchen": True, "bedroom": lid != "ss5009"}, ag)
        p.total_floors = p.total_floors or max(9, p.floor or 0)  # the generator fields submit_for_review requires (stage «полные параметры»)
        p.post_fields = {"prop_type": "Apartment", "building": "NewBuilding", "design": "New", "heating": "CentralHeating",
                         "pets": "ByAgreement", "periods": ["6Month", "12Month"], "amenities": ["Balcony", "WiFi", "Elevator"],
                         "tenants": 3, **(p.post_fields or {})}
        db.commit()
        properties.submit_for_review(db, p.id, ag)
        if final == "returned":
            properties.review(db, p.id, nino, False, "Нет фото спальни")
        elif final == "published":
            p = properties.review(db, p.id, nino, True, "Отлично")
            p.post_url = p.post_url or f"https://t.me/rent_tbilisi_ge/{1000 + p.id}"   # no bot locally: a fake post
            p.post_message_id = p.post_message_id or str(1000 + p.id)
            p.is_exclusive = lid == "ss5010"
            db.commit()
            pub.append(p)
    properties.take(db, None, ana, {"address": "ул. Леселидзе 20", "district": "Мтацминда", "price": 1000, "bedrooms": 2,
                                    "description": "Объект от знакомого собственника"})

    # ---------------- funnel 2: one lead per status
    def lead_to(client, listing_id, status, agent=georgiy, minutes_ago=0):
        lead = leads.create_from_like(db, client.id, listing_id)
        if status == "new":
            _age(db, lead, minutes_ago)
            return lead
        leads.operator_take(db, lead.id, nino)
        if status == "operator_taken":
            return lead
        leads.assign_agent(db, lead.id, agent.id, nino)
        if status == "agent_assigned":
            _age(db, lead, minutes_ago)
            return lead
        past = status in ("viewing_done", "thinking", "contract", "deal_pending", "rented", "refused")
        base = now() - timedelta(hours=3) if past else now() + timedelta(days=1)
        with patch.object(notify, "slots_to_client", lambda lead: None):  # demo clients aren't real: no userbot send
            leads.add_slots(db, lead.id, [base.replace(minute=0, second=0, microsecond=0),
                                          (base + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)], agent)
        if status == "slots_sent":
            return lead
        slot = db.scalars(select(LeadSlot).where(LeadSlot.lead_id == lead.id).order_by(LeadSlot.starts_at)).first()
        leads.choose_slot(db, slot.id, client.id)
        if status == "time_chosen":
            return lead
        leads.mark_viewing_done(db, lead.id)
        if status == "viewing_done":
            return lead
        if status == "thinking":
            return leads.set_outcome(db, lead.id, "client", "thinking")
        if status == "refused":
            return leads.set_outcome(db, lead.id, "client", "refused", "price")
        leads.set_outcome(db, lead.id, "client", "rented")
        lead = leads.set_outcome(db, lead.id, "agent", "rented", actor=agent)  # → «Договор»
        if status == "deal_pending":  # the contract signed: the deal form opens
            db.add(Contract(lead_id=lead.id, property_id=lead.property_id, created_by=agent.id, kind="rent", lang="RU",
                            html="<p>Демо-договор</p>", status="signed", signed_at=now(), signed_by=agent.id,
                            client_name=client.name, data={"client_full_name": client.name or "", "price_usd": "850"}))
            db.commit()
        return lead

    lead_to(anna, "ss5012", "new", minutes_ago=1)
    lead_to(extra[0], "ss5010", "new", minutes_ago=4)
    lead_to(mike, "ss5011", "operator_taken")
    lead_to(extra[1], "ss5011", "agent_assigned", minutes_ago=25)
    lead_to(tamar, "ss5010", "slots_sent")
    lead_to(anna, "ss5010", "time_chosen")
    lead_to(extra[2], "ss5012", "viewing_done", agent=levan)
    lead_to(extra[3], "ss5011", "thinking", agent=levan)
    lead_to(tamar, "ss5012", "contract")
    pending = lead_to(anna, "ss5011", "deal_pending")
    rented = lead_to(extra[4], "ss5011", "deal_pending")
    lead_to(extra[5], "ss5012", "refused", agent=levan)

    # ---------------- deals: one pending (from a lead), confirmed ones over several months, T0/T14
    today = date.today()
    deals.create(db, {"lead_id": pending.id, "price": 850, "commission_pct": 50, "is_exclusive": False,
                      "owner_name": "Гиорги", "owner_phone": "+995 555 12 34 56", "cadastral": "01.14.12.003.045",
                      "rent_from": today + timedelta(days=3), "rent_to": today + timedelta(days=368),
                      "t0_h2o_client": 3, "t0_h2o_owner": 3, "t0_h2o_process": 3, "t0_result": "clean"}, georgiy)
    d = deals.create(db, {"lead_id": rented.id, "price": 720, "commission_pct": 50, "is_exclusive": True,
                          "recruit_id": sandro.id, "recruit_pct": 10, "owner_name": "Мзия", "owner_phone": "+995 591 11 11 11",
                          "cadastral": "01.14.15.001.010", "rent_from": today - timedelta(days=13),
                          "rent_to": today + timedelta(days=25)}, georgiy)
    deals.confirm(db, d.id, nino)
    history = [(georgiy, 1000, True, 40), (georgiy, 1400, True, 75), (levan, 600, False, 20), (ana, 2000, True, 100),
               (georgiy, 800, False, 130), (levan, 750, True, 160), (ana, 1800, True, 200), (georgiy, 950, True, 5)]
    for i, (ag, price, excl, ago) in enumerate(history):
        rf = today - timedelta(days=ago)
        d = deals.create(db, {"agent_id": ag.id, "price": price, "commission_pct": 50, "is_exclusive": excl,
                              "client_name": f"Клиент {i + 1}", "client_phone": f"+995 555 90 00 {i:02d}",
                              "owner_name": f"Собственник {i + 1}", "address": f"Тбилиси, демо-адрес {i + 1}",
                              "cadastral": f"01.10.{i:02d}.000.001", "rent_from": rf, "rent_to": rf + timedelta(days=365),
                              "client_src": "Сандро" if i == 2 else None}, nino)
        deals.confirm(db, d.id, nino)
        if ago > 14:
            evaluations.create(db, {"deal_id": d.id, "kind": "t14", "h2o_client": 3 if i != 2 else 1, "h2o_owner": 3,
                                    "h2o_process": 2, "result": "clean" if i != 2 else "dirty",
                                    "complaints": i == 2, "complaints_text": "Шумные соседи" if i == 2 else None}, ag)

    # ---------------- reports (counters)
    for k in range(6):
        reports.save(db, "photo", {"recruit": "Сандро", "district_address": f"Ваке, демо {k}", "date": today.isoformat()},
                     sandro, post=False)
    for k in range(3):
        reports.save(db, "showing", {"recruit": "Сандро", "district_address": "Сабуртало", "result": "думает",
                                     "date": today.isoformat()}, sandro, post=False)
    reports.save(db, "stickers", {"recruit": "Сандро", "district": "Дидубе", "stickers_count": 40,
                                  "date": today.isoformat()}, sandro, post=False)
    reports.save(db, "daily", {"author": "Георгий", "date": today.isoformat(), "calling": 12, "agreed": 3,
                               "preview": "📅 Daily Report · Георгий: 12 звонков, 3 договорились"}, georgiy, post=False)
    db.close()
    print("demo data created: logins david / nino / georgiy / levan, password 'demo'")


def _age(db, lead: Lead, minutes: int):
    """Shift SLA timers back so the board shows green / yellow / red cards."""
    if not minutes or not lead.sla_due_at:
        return
    lead = db.get(Lead, lead.id)
    lead.sla_due_at -= timedelta(minutes=minutes)
    lead.created_ts -= timedelta(minutes=minutes)
    db.execute(update(Job).where(Job.kind == "lead_sla", Job.ref == str(lead.id), Job.done_at.is_(None))
               .values(due_at=Job.due_at - timedelta(minutes=minutes)))
    db.commit()


def _tiny_jpeg() -> bytes:
    import base64
    return base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP////////////////////////////////////////////////////////////////////"
        "//////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////"
        "////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAA"
        "AAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AKAA/9k=")


if __name__ == "__main__":
    seed()
    sys.exit(0)
