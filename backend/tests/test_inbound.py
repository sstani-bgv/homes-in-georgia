"""Inbound Telegram (services/inbound.py): parsers on synthetic messages rebuilt from the Apps Script regexes/templates,
routing by chat/topic, idempotency, API /channel-posts and listings analytics."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.models import ChannelPost, Deal, News, Owner, Report
from app.services import deals, inbound, settings
from tests.conftest import auth, make_user

WG, NEWS_CHAT, CHANNEL = -100111, -100222, -100333
TS = int(datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc).timestamp())

CHANNEL_POST = """🏠 Cozy 2 bedroom apartment in Vake, new building with a view
📍 Chavchavadze avenue 10, 5/12 floor, 75 m²
💰 1200$ + deposit, pets allowed
#Vake #2Bed #NewBuilding #Exclusive
📞 +995 555 12 34 56 #Nika
[Check all listings](https://t.me/rent_tbilisi_ge)"""

TOPIC_POST = """Studio in Saburtalo near metro, renovated, all appliances
Price: $650 per month
#Saburtalo #Studio #OldBuilding
#Rent #Tamar"""

TEMPLATE = """Make Foto
Address: Vake, Chavchavadze 10
Agent: #Nika
Owner: Nino +995 599 44 55 66
Date: 27.11 14:00
Link: https://ss.ge/ka/udzravi-qoneba/123"""

DEAL_EN = """#Rented #Exclusive
Link: https://t.me/rent_tbilisi_ge/501
Price: 900$
Commission: 450$
Platform Commission: 225$
#Nika Commission: 225$
Cadastral number: 01.10.14.003.012
Client: Ivan Petrov +995 555 11 22 33
Owner: Nino Beridze +995 599 44 55 66
Date of Rent: 01.12.2026 - 01.12.2027
Agent 50/50(3/10)"""

DEAL_RU = """💰 #Rented | Сделка #4
#Эксклюзив | Аренда | Agent 70% | 👤 #Tamar
Link: https://ss.ge/777
Cadastral: 01.11.22.333.444

Цена: 1000$
Комиссия: 500$ (50%)

📊 Распределение:
• Платформа (30%): 150$
• Агент/Наставник (70%): 350$

Client: Anna +995 555 00 01 02
Owner: Giorgi +995 577 10 20 30
Rent: 15.11.2026 – 15.05.2027"""


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {"WORK_GROUP_CHAT_ID": str(WG), "NEWS_CHAT_ID": str(NEWS_CHAT), "TOPIC_NEWS": "107", "TOPIC_LISTINGS": "3",
                 "TOPIC_DEAL": "9", "TOPIC_DEAL_EXTRA": "738,2674", "TOPIC_PHOTO_QUEUE": "458,103",
                 "AGENCY_CHANNEL": "rent_tbilisi_ge", "AGENCY_CHANNEL_ID": "", "DEALS_CHANNEL_ID": ""}.items():
        monkeypatch.setenv(k, v)


def msg(text, chat=WG, topic=None, mid=1, chat_type="supergroup", username=None, frm=None, forward=None, edited=False):
    return {"chat_id": chat, "chat_type": chat_type, "chat_username": username, "topic_id": topic, "message_id": mid,
            "date": TS, "text": text, "caption": None, "edited": edited, "from": frm, "forward": forward}


def chan(text, mid=500, edited=False):
    return msg(text, chat=CHANNEL, mid=mid, chat_type="channel", username="rent_tbilisi_ge", edited=edited)


# ---------------------------------------------------------------- parsers

def test_parse_listing_takes_the_agent_after_david():
    """Our posts print «+995 599 20 67 16 #David · Agent #Zaza»: the agent is the last tag of the phone line."""
    post = CHANNEL_POST.split("+995")[0] + "+995 599 20 67 16 #David · Agent #Zaza\n" + "x" * 100
    assert inbound.parse_listing(post, "channel")["agent_tag"] == "Zaza"


def test_parse_listing_channel_and_topic():
    p = inbound.parse_listing(CHANNEL_POST, "channel")
    assert p["agent_tag"] == "Nika" and p["district"] == "Vake" and p["price"] == 1200
    assert (p["rooms"], p["building"], p["is_exclusive"], p["rented"]) == ("2Bed", "New", True, False)
    assert inbound.parse_listing("short text +995", "channel") is None
    assert inbound.parse_listing(CHANNEL_POST.replace("+995", "tel"), "channel") is None  # channel needs a +995 line
    t = inbound.parse_listing(TOPIC_POST, "topic")
    assert t["agent_tag"] == "" and t["agent_candidates"][0] == "Tamar"  # Rent/Saburtalo/Studio are not agents
    assert (t["district"], t["price"], t["rooms"], t["building"]) == ("Saburtalo", 650, "Studio", "Old")


def test_parse_template():
    t = inbound.parse_template(TEMPLATE)
    assert t == {"addr": "Vake, Chavchavadze 10", "agent": "Nika", "owner": "Nino", "phone": "+995599445566",
                 "date": "27.11 14:00", "link": "https://ss.ge/ka/udzravi-qoneba/123"}
    ref = datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert inbound.parse_shoot_date("27.11 14:00", ref) == (date(2026, 11, 27), "14:00")
    assert inbound.parse_shoot_date("2026-12-01 9.30", ref) == (date(2026, 12, 1), "09:30")
    assert inbound.parse_shoot_date("завтра", ref) == (None, None)


def test_parse_deal_english_template():
    d = inbound.parse_deal_message(DEAL_EN)
    assert (d["agent"], d["price"], d["commission"], d["platform"], d["agent_comm"]) == ("Nika", 900, 450, 225, 225)
    assert d["link"] == "https://t.me/rent_tbilisi_ge/501" and d["cadastral"] == "01.10.14.003.012"
    assert (d["client_name"], d["client_phone"]) == ("Ivan Petrov", "+995 555 11 22 33")
    assert (d["owner_name"], d["owner_phone"]) == ("Nino Beridze", "+995 599 44 55 66")
    assert (d["rent_from"], d["rent_to"]) == (date(2026, 12, 1), date(2027, 12, 1))
    assert (d["level"], d["deal_num"], d["deal_plan"], d["is_exclusive"], d["is_sold"]) == (50, 3, 10, True, False)
    assert inbound.parse_deal_message("just a message") is None


def test_parse_deal_platform_russian_text():
    d = inbound.parse_deal_message(DEAL_RU)
    assert (d["agent"], d["price"], d["commission"], d["platform"]) == ("Tamar", 1000, 500, 150)
    assert (d["level"], d["deal_num"], d["is_exclusive"], d["cadastral"]) == (70, 4, True, "01.11.22.333.444")
    assert (d["rent_from"], d["rent_to"]) == (date(2026, 11, 15), date(2027, 5, 15))


# ---------------------------------------------------------------- routing + idempotency

def test_channel_post_saved_once_and_marked_rented(db):
    nika = make_user(db, "agent", "Nika")
    r = inbound.process(db, chan(CHANNEL_POST, mid=500))
    assert [h["kind"] for h in r["handled"]] == ["listing"]
    inbound.process(db, chan(CHANNEL_POST, mid=500))  # the same update again
    rows = db.scalars(select(ChannelPost)).all()
    assert len(rows) == 1
    p = rows[0]
    assert (p.message_id, p.agent_id, p.district, float(p.price), p.rooms, p.status) == ("500", nika.id, "Vake", 1200, "2Bed", "active")
    assert p.link == "https://t.me/rent_tbilisi_ge/500" and p.is_exclusive
    # a work-group message "#Сдано" linking the post marks it rented
    inbound.process(db, msg("#Сдано https://t.me/rent_tbilisi_ge/500", topic=3, mid=77))
    db.expire_all()
    assert db.get(ChannelPost, p.id).status == "rented"


def test_edited_channel_post_with_rented_tag(db):
    make_user(db, "agent", "Nika")
    inbound.process(db, chan(CHANNEL_POST, mid=600))
    inbound.process(db, chan("#Rented\n" + CHANNEL_POST, mid=600, edited=True))
    assert db.scalar(select(ChannelPost.status).where(ChannelPost.message_id == "600")) == "rented"


def test_topic_listing_with_alias_and_unknown_chat_ignored(db):
    tamara = make_user(db, "agent", "Tamara Kapanadze")
    settings.set(db, "agent_tags", '{"#Tamar": "Tamara Kapanadze"}')
    inbound.process(db, msg(TOPIC_POST, topic=3, mid=41))
    p = db.scalar(select(ChannelPost))
    assert (p.message_id, p.agent_name, p.agent_id) == ("wg:41", "Tamara Kapanadze", tamara.id)
    assert p.link == "https://t.me/c/111/41"
    inbound.process(db, msg(TOPIC_POST, chat=-100999, topic=3, mid=42))  # another chat: ignored
    inbound.process(db, msg(TOPIC_POST, topic=5, mid=43))  # another topic: ignored
    assert db.query(ChannelPost).count() == 1


def test_news_topic(db):
    inbound.process(db, msg("Собрание в пятницу в 11:00", chat=NEWS_CHAT, topic=107, mid=10))
    inbound.process(db, msg("Собрание в пятницу в 11:00", chat=NEWS_CHAT, topic=107, mid=10))
    inbound.process(db, msg("не новость", chat=NEWS_CHAT, topic=108, mid=11))
    rows = db.scalars(select(News)).all()
    assert [(n.text, n.message_id) for n in rows] == [("Собрание в пятницу в 11:00", "10")]
    inbound.process(db, msg("Собрание в субботу", chat=NEWS_CHAT, topic=107, mid=10, edited=True))
    db.expire_all()
    assert db.scalar(select(News.text)) == "Собрание в субботу"


def test_photo_queue_owner_and_makefoto_report(db):
    nika = make_user(db, "agent", "Nika")
    r = inbound.process(db, msg(TEMPLATE, topic=458, mid=5))
    assert r["replies"] == [{"chat_id": WG, "thread_id": 458, "reply_to": 5, "text": "✅ Добавлено в базу"}]
    again = inbound.process(db, msg(TEMPLATE, topic=458, mid=5))
    assert again["replies"] == [] and again["handled"][0]["duplicate"]
    owner = db.scalar(select(Owner))
    assert (owner.name, owner.phone_norm, owner.address) == ("Nino", "599445566", "Vake, Chavchavadze 10")
    rep = db.scalar(select(Report))
    assert rep.kind == "makefoto" and rep.author_id == nika.id and rep.date == date(2026, 11, 27)
    assert rep.data["date"] == "27.11.2026 14:00" and rep.data["status"] == "added" and rep.data["tg_ref"] == f"{WG}:5"
    # the same owner phone in another topic message → "already in the base"
    r2 = inbound.process(db, msg(TEMPLATE.replace("Chavchavadze 10", "Paliashvili 3"), topic=103, mid=6))
    assert r2["replies"][0]["text"] == "⚠️ Этот номер уже есть в базе!"
    assert db.query(Owner).count() == 1 and db.query(Report).count() == 2


def test_deal_topic_pending_deal_idempotent_and_confirmable(db, sent):
    nika = make_user(db, "agent", "Nika", level=50)
    op = make_user(db, "operator", "Op")
    r = inbound.process(db, msg(DEAL_EN, topic=9, mid=900))
    assert r["handled"][0]["kind"] == "deal"
    inbound.process(db, msg(DEAL_EN, topic=9, mid=900))
    d = db.scalar(select(Deal))
    assert db.query(Deal).count() == 1
    assert (d.status, d.source, d.tg_ref, d.agent_id) == ("pending", "telegram", f"{WG}:900", nika.id)
    assert (float(d.price), float(d.commission_total), float(d.platform_amount), float(d.agent_amount)) == (900, 450, 225, 225)
    assert (d.rent_from, d.rent_to, d.is_exclusive, d.cadastral) == (date(2026, 12, 1), date(2027, 12, 1), True, "01.10.14.003.012")
    assert d.raw["tg_text"].startswith("#Rented")
    deals.confirm(db, d.id, op)
    db.refresh(nika)
    assert nika.exclusive_deals == 1


def test_deals_channel_extra_topic_and_forward(db):
    tamar = make_user(db, "agent", "Tamar", tg_id=7001)
    stranger = {"id": 9999, "username": "x"}
    # the channel's own #Rented post → deal (not a listing)
    inbound.process(db, chan(DEAL_RU, mid=800))
    assert db.query(Deal).count() == 1 and db.query(ChannelPost).count() == 0
    d = db.scalar(select(Deal))
    assert (d.agent_id, float(d.commission_total), float(d.platform_amount), d.agent_level) == (tamar.id, 500, 150, 70)
    # a forward of that same post by an agent → duplicate, with a reply
    fwd = {"chat_id": CHANNEL, "chat_username": "rent_tbilisi_ge", "message_id": 800, "date": TS}
    r = inbound.process(db, msg(DEAL_RU, chat=7001, chat_type="private", mid=3, frm={"id": 7001}, forward=fwd))
    assert r["replies"][0]["text"].startswith("⚠️ Уже есть")
    # a forward by someone who is not staff → ignored
    r = inbound.process(db, msg(DEAL_EN, chat=9999, chat_type="private", mid=4, frm=stranger,
                                forward={"chat_id": WG, "message_id": 5000, "date": TS}))
    assert r["handled"] == [] and db.query(Deal).count() == 1
    # extra deal topic (TOPIC_DEAL_EXTRA)
    inbound.process(db, msg(DEAL_EN, topic=738, mid=901))
    assert db.query(Deal).count() == 2


def test_tg_user_link_and_status(db):
    u = make_user(db, "agent", "Lika", username="lika_agent")
    r = inbound.process(db, msg("/status", topic=1, mid=2, frm={"id": 4242, "username": "Lika_Agent"}))
    db.refresh(u)
    assert u.tg_id == 4242
    assert "Настройки тем" in r["replies"][0]["text"] and "Make Foto: 458,103" in r["replies"][0]["text"]
    r = inbound.process(db, msg("/setup фото", topic=730, mid=3))
    assert "TOPIC_PHOTO=730" in r["replies"][0]["text"]


def test_from_telegram_forward_origin():
    m = inbound.from_telegram({"message_id": 3, "date": TS, "chat": {"id": 7, "type": "private"}, "from": {"id": 7},
                               "text": "#Rented", "forward_origin": {"type": "channel", "chat": {"id": CHANNEL, "username": "c"},
                                                                     "message_id": 800, "date": TS}})
    assert m["forward"] == {"chat_id": CHANNEL, "chat_username": "c", "message_id": 800, "date": TS}
    assert inbound.deal_ref(m) == f"{CHANNEL}:800"


# ---------------------------------------------------------------- API

def test_channel_posts_api_and_listings_analytics(db, client):
    make_user(db, "agent", "Nika")
    agent = make_user(db, "agent", "Viewer")
    inbound.process(db, chan(CHANNEL_POST, mid=500))
    inbound.process(db, chan(CHANNEL_POST.replace("#Vake", "#Vera").replace("1200$", "800$"), mid=501))
    inbound.process(db, chan("#Rented https://t.me/rent_tbilisi_ge/501\n" + CHANNEL_POST, mid=502))  # deal-ish: no listing
    h = auth(agent)
    all_ = client.get("/api/channel-posts", headers=h).json()
    assert all_["total"] == 2 and {p["message_id"] for p in all_["items"]} == {"500", "501"}
    assert client.get("/api/channel-posts?hashtag=Vera", headers=h).json()["total"] == 1
    assert client.get("/api/channel-posts?district=vake&status=active", headers=h).json()["items"][0]["price"] == 1200
    assert client.get("/api/channel-posts?status=rented", headers=h).json()["items"][0]["message_id"] == "501"
    assert client.get("/api/channel-posts?q=Chavchavadze&source=channel", headers=h).json()["total"] == 2
    assert client.get("/api/channel-posts?source=topic", headers=h).json()["total"] == 0
    stats = client.get("/api/analytics/listings", headers=h).json()
    assert stats["listings"]["total"] == 2 and stats["active"]["total"] == 1
    assert {d["district"]: d["avg_price"] for d in stats["listings"]["by_district"]} == {"Vake": 1200, "Vera": 800}
    assert db.query(Deal).count() == 0  # "#Rented <link>" without numbers is not a deal
    assert "current" in stats["weekly"] and "last_week" in stats["weekly"]
    assert client.get("/api/channel-posts").status_code == 401
    cab = client.get("/api/me/cabinet", headers=h).json()
    assert cab["channel_listings"] == {"active": 0, "last_7_days": 0} and cab["reports"] == []
