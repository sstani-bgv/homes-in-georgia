"""Stage 9: the operator userbot (matching DMs to clients/leads, logging, numbered choices, the slot card as the bot's
inline result) with a fake Telethon client; one bot for everyone; ❤️ as the operator DM link; Georgian in clients.lang."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import pytest

from app.models import Client, Lead, LeadEvent, LeadSlot
from app.services import leads
from app.services.errors import DomainError
from app.telegram import notify, userbot
from tests.conftest import make_client, make_client_listing as make_listing, make_user


class FakeTelethon:
    """get_input_entity / send_message / inline_query as Telethon's TelegramClient: users not in the entity cache raise
    ValueError; inline_query -> results whose [0].click(entity) sends «via @bot» (inline_error: the query fails)."""
    def __init__(self, cached=(), inline_error=None):
        self.cached, self.sent, self.queries, self.clicked, self.inline_error = set(cached), [], [], [], inline_error

    async def inline_query(self, bot, query):
        self.queries.append((bot, query))
        if self.inline_error:
            raise self.inline_error
        fake = self

        class Result:
            async def click(self, entity):
                fake.clicked.append(entity)
                return type("Msg", (), {"id": 100 + len(fake.clicked)})()
        return [Result()]

    async def get_input_entity(self, peer):
        if peer not in self.cached:
            raise ValueError("Could not find the input entity")
        return peer

    async def send_message(self, entity, text, parse_mode=None):
        self.sent.append((entity, text))
        return type("Msg", (), {"id": len(self.sent)})()


def events(db, lead_id, kind):
    db.expire_all()
    return [e.note for e in db.query(LeadEvent).filter_by(lead_id=lead_id, kind=kind).order_by(LeadEvent.id)]


def state(db, cid):
    db.expire_all()
    return json.loads(db.get(Client, cid).state)


def test_incoming_dm_is_matched_and_logged(db, sent):
    cl = make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    assert userbot.on_incoming(5550001, None, "Здравствуйте! Меня интересует объект") == "logged"
    assert userbot.on_incoming(42, "Anna_Demo", "Ещё вопрос") == "logged"           # by @username, case-insensitive
    assert userbot.on_incoming(43, "stranger", "hi") == "unknown"
    assert events(db, lead.id, "message_from_client") == ["Здравствуйте! Меня интересует объект", "Ещё вопрос"]
    # the operator's own messages typed in Telegram go to the history; ones the userbot sent itself don't repeat
    assert userbot.on_outgoing(5550001, "Когда вам удобно?") == "logged"
    userbot.SENT.add((5550001, "из CRM"))
    assert userbot.on_outgoing(5550001, "из CRM") == "own"
    assert events(db, lead.id, "message_to_client") == ["Когда вам удобно?"]


def test_no_open_lead(db, sent):
    cl = make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    db.query(Lead).filter_by(id=lead.id).update({"status": "refused"})
    db.commit()
    assert userbot.on_incoming(5550001, None, "привет") == "no_lead"


def test_numbered_slot_choice_and_own_time(db, sent, monkeypatch):
    op, ag = make_user(db, "operator"), make_user(db, "agent")
    cl = make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    at = datetime.now(timezone.utc) + timedelta(days=2)
    leads.add_slots(db, lead.id, [at, at + timedelta(hours=3)], ag)
    slots = [s.id for s in db.query(LeadSlot).filter_by(lead_id=lead.id).order_by(LeadSlot.starts_at)]
    fake = FakeTelethon()                                # not cached: falls back to the @username
    mid = asyncio.run(userbot.send(fake, cl.id, "Выберите время", ["Завтра 12:00", "Завтра 15:00"],
                                   {"lead": lead.id, "kind": "slots", "options": [f"ls:pick:{x}" for x in slots]}))
    assert mid == 1 and fake.sent[0][0] == "anna_demo"
    assert "1. Завтра 12:00\n2. Завтра 15:00" in fake.sent[0][1] and "Ответьте цифрой" in fake.sent[0][1]
    assert state(db, cl.id)["dm_choice"]["options"] == [f"ls:pick:{x}" for x in slots]
    assert userbot.on_incoming(5550001, None, "А с собакой можно?") == "logged"   # not a number, not a time: a message
    assert userbot.on_incoming(5550001, None, "завтра после 18") == "own_time"
    db.expire_all()
    assert db.get(Lead, lead.id).client_note == "завтра после 18"
    leads.add_slots(db, lead.id, [at, at + timedelta(hours=3)], ag)
    slots = [s.id for s in db.query(LeadSlot).filter_by(lead_id=lead.id).order_by(LeadSlot.starts_at)]
    asyncio.run(userbot.send(FakeTelethon(cached={5550001}), cl.id, "Выберите", ["a", "b"],
                             {"lead": lead.id, "kind": "slots", "options": [f"ls:pick:{x}" for x in slots]}))
    assert userbot.on_incoming(5550001, None, "2.") == "slot"
    db.expire_all()
    assert db.get(Lead, lead.id).status == "time_chosen" and db.get(LeadSlot, slots[1]).chosen
    assert state(db, cl.id)["dm_choice"] is None
    assert [userbot.parse_choice(x, 3) for x in ("1", " #2 ", "3)", "4", "12:00", "да")] == [1, 2, 3, None, None, None]


def test_slot_card_goes_as_the_bots_inline_result(db, sent, monkeypatch):
    """userbot.send with inline: inline_query(BOT_USERNAME, "slots:<token>") -> results[0].click(client); a failed
    inline query (inline mode off) -> the numbered text instead."""
    monkeypatch.setenv("BOT_USERNAME", "@homes_test_bot")
    cl = make_client(db)
    fake = FakeTelethon(cached={5550001})
    choice = {"lead": 1, "kind": "slots", "options": ["ls:pick:7", "ls:pick:8"]}
    mid = asyncio.run(userbot.send(fake, cl.id, "Выберите время", ["a", "b"], choice, inline="slots:AbC123xyz"))
    assert (mid, fake.queries, fake.clicked, fake.sent) == (101, [("homes_test_bot", "slots:AbC123xyz")], [5550001], [])
    assert state(db, cl.id)["dm_choice"] == choice                 # a «1»/«2» typed reply still works
    broken = FakeTelethon(cached={5550001}, inline_error=RuntimeError("BOT_INLINE_DISABLED"))
    asyncio.run(userbot.send(broken, cl.id, "Выберите время", ["Завтра 12:00"], choice, inline="slots:AbC123xyz"))
    assert broken.clicked == [] and "1. Завтра 12:00" in broken.sent[0][1]


def test_own_time_after_another_time_button(db, sent):
    """«Другое время» (the bot sets dm_choice.own): the client's next DM is their own time, whatever it reads like."""
    op, ag, cl = make_user(db, "operator"), make_user(db, "agent"), make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    userbot.set_state(cl.id, dm_choice={"lead": lead.id, "own": True})
    assert userbot.on_incoming(5550001, None, "1") == "own_time"
    db.expire_all()
    assert db.get(Lead, lead.id).client_note == "1" and state(db, cl.id)["dm_choice"] is None


class FakeHttp:
    def __init__(self):
        self.urls = []

    def post(self, url, data=None, files=None):
        self.urls.append((url, data))
        chat = int(data.get("chat_id", 0))
        return type("R", (), {"json": lambda self: {"ok": True, "result": {"message_id": 1, "chat": {"id": chat}}}})()


def test_like_is_a_callback_and_the_dm_link_still_makes_the_lead(db, monkeypatch):
    """❤️ is always the bot's callback (the lead at once, the client types nothing). The prefilled DM link
    (operator_dm_link, the fallback when the operator's account can't write first) still makes the lead from that first
    DM in the userbot (create_from_like: B1 card through the one bot, the card marked) and logs it."""
    from app.models import Property
    from app.telegram import client as tg
    monkeypatch.undo()                                                        # the real notify (the autouse `sent` stubs it)
    http = FakeHttp()
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.setenv("OPERATORS_CHAT_ID", "-100777")
    monkeypatch.setenv("OPERATOR_TG_USERNAME", "@op_nino")
    cl, lst = make_client(db), make_listing(db)
    cl.lang = "ka"                                                            # clients_lang_check allows ka (migration 0011)
    db.commit()
    assert notify.like_button({"id": cl.id, "lang": "ka"}, lst.id, "❤️") == {"text": "❤️", "callback_data": f"like:{lst.id}"}
    url = notify.operator_dm_link({"id": cl.id, "lang": "ka"}, lst.id)
    assert url.startswith("https://t.me/op_nino?text=")
    text = unquote(url.split("text=", 1)[1])
    assert text == f"გამარჯობა! მომწონს ვარიანტი #{lst.id} · ვაკე, ул. Палиашвили 10 · $850"
    from app.telegram import store
    store.remember("card", f"{cl.id}:{lst.id}", 5550001, 1)
    assert userbot.on_incoming(5550001, None, text) == "liked"
    lead = db.query(Lead).filter_by(client_id=cl.id).one()
    assert events(db, lead.id, "message_from_client") == [text]               # «Клиент написал оператору в личку»
    assert ("bot123456:TEST-TOKEN", "-100777") in {(u.rsplit("/", 2)[-2], d.get("chat_id")) for u, d in http.urls}   # B1
    mark = next(d for u, d in http.urls if u.endswith("/editMessageReplyMarkup"))
    assert (mark["chat_id"], mark["message_id"]) == ("5550001", "1") and "მომწონს ✅" in mark["reply_markup"]

    other = make_client(db, external_id=5550002)
    prop = Property(listing_id=lst.id, status="published", address="x")
    db.add(prop)
    db.commit()
    url = notify.operator_dm_link({"id": other.id, "lang": "ru"}, lst.id)
    assert unquote(url.split("text=", 1)[1]) == f"Здравствуйте! Мне нравится вариант #{prop.id} · Ваке, ул. Палиашвили 10 · $850"
    assert userbot.on_incoming(5550002, None, "Мне нравится вариант #999999 · …") == "no_lead"   # no such object: no lead
    assert userbot.on_incoming(5550002, None, f"Здравствуйте! Мне нравится вариант #{prop.id} · …") == "liked"
    assert db.query(Lead).filter_by(client_id=other.id, listing_id=lst.id).count() == 1
    monkeypatch.delenv("OPERATOR_TG_USERNAME")                                # (no operator with a @username either)
    assert notify.operator_dm_link({"id": other.id, "lang": "ru"}, lst.id) is None
    assert not hasattr(notify, "client_message") and not hasattr(notify, "handoff")


def test_like_sends_the_channel_post_link(db, monkeypatch):
    """An object published in our channel: the DM link prefills a link to its post (the operator sees which flat at
    once), and the userbot finds the object by that link."""
    from app.models import Property
    from app.telegram import client as tg
    monkeypatch.undo()                                                        # the real notify (the autouse `sent` stubs it)
    monkeypatch.setattr(tg, "_client", lambda: FakeHttp())
    monkeypatch.setenv("OPERATOR_TG_USERNAME", "op_nino")
    cl, lst = make_client(db, external_id=5550011), make_listing(db)
    prop = Property(listing_id=lst.id, status="published", address="x", post_url="https://t.me/rent_tbilisi_ge/548723",
                    post_message_id="548723")
    db.add(prop)
    db.commit()
    text = unquote(notify.operator_dm_link({"id": cl.id, "lang": "ru"}, lst.id).split("text=", 1)[1])
    assert text == "Здравствуйте! Мне нравится этот вариант: https://t.me/rent_tbilisi_ge/548723"
    assert userbot.on_incoming(5550011, None, text) == "liked"
    assert db.query(Lead).filter_by(client_id=cl.id, listing_id=lst.id).count() == 1
    other = make_client(db, external_id=5550012)
    assert userbot.on_incoming(5550012, None, "Мне нравится https://t.me/rent_tbilisi_ge/1") == "no_lead"   # unknown post


def test_operator_intro_userbot_first_else_the_dm_link(db, monkeypatch):
    """First ❤️: the operator's account writes first; if it can't (Stars / Premium-only DMs, no way to reach), the bot
    sends the client the prefilled DM link instead."""
    from app.models import Property
    from app.telegram import client as tg
    monkeypatch.undo()
    http = FakeHttp()
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.setenv("OPERATOR_TG_USERNAME", "op_nino")
    cl, lst = make_client(db, external_id=5550021), make_listing(db)
    db.add(Property(listing_id=lst.id, status="published", address="x", post_url="https://t.me/rent_tbilisi_ge/7"))
    db.commit()
    lead = leads.create_from_like(db, cl.id, lst.id)
    sent = []
    monkeypatch.setattr(userbot, "send_to_client", lambda c, text, **kw: sent.append(text) or {"ok": True, "message_id": 1})
    assert notify.operator_intro(lead) == "userbot" and "https://t.me/rent_tbilisi_ge/7" in sent[0]
    assert not [d for u, d in http.urls if u.endswith("/sendMessage") and d.get("chat_id") == "5550001"]
    monkeypatch.setattr(userbot, "send_to_client", lambda c, text, **kw: None)
    assert notify.operator_intro(lead) == "link"
    msg = next(d for u, d in http.urls if u.endswith("/sendMessage") and d.get("chat_id") == "5550001")
    assert "t.me/op_nino?text=" in msg["reply_markup"]


def test_paid_dm_blocks_the_userbot_send(db):
    """A client who takes DMs only for Stars: send() refuses (the caller falls back), no message goes out."""
    from telethon.tl import types
    cl = make_client(db, external_id=5550031)

    class Paid(FakeTelethon):
        async def __call__(self, req):
            return [types.RequirementToContactPaidMessages(stars_amount=10)]
    fake = Paid(cached={5550031})
    with pytest.raises(PermissionError):
        asyncio.run(userbot.send(fake, cl.id, "hi"))
    assert not fake.sent


def fake_userbot(monkeypatch, posts):
    import httpx
    monkeypatch.setenv("USERBOT_URL", "http://userbot:8091/")
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None: posts.append((url, json, headers))
                        or type("R", (), {"status_code": 200, "json": lambda self: {"ok": True, "message_id": 9}})())


def token_of(body):
    kind, token = body["inline"].split(":")
    return kind, token


def test_slots_go_to_the_userbot_as_an_inline_query(db, monkeypatch):
    """add_slots -> a fresh inline_cards token -> POST USERBOT_URL/send {inline: "slots:<token>", the numbered
    fallback}; nothing to the client through the bot. notify.card(kind, token) is what the bot answers it with."""
    from app.telegram import client as tg
    op, ag, cl = make_user(db, "operator"), make_user(db, "agent"), make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)              # (notify stubbed by `sent`)
    leads.assign_agent(db, lead.id, ag.id, op)
    monkeypatch.undo()                                                         # the real notify.slots_to_client
    http, posts = FakeHttp(), []
    monkeypatch.setattr(tg, "_client", lambda: http)
    fake_userbot(monkeypatch, posts)
    at = datetime(2031, 3, 4, 12, tzinfo=timezone.utc)
    leads.add_slots(db, lead.id, [at, at + timedelta(hours=2)], ag)
    slots = [s.id for s in db.query(LeadSlot).filter_by(lead_id=lead.id).order_by(LeadSlot.starts_at)]
    url, body, headers = posts[0]
    kind, token = token_of(body)
    assert url == "http://userbot:8091/send" and headers == {"X-Userbot-Token": "test-secret-key-that-is-long-enough-for-hs256-ok"}
    assert kind == "slots" and len(token) >= 6 and body["client_id"] == cl.id
    assert body["choice"] == {"lead": lead.id, "kind": "slots", "options": [f"ls:pick:{x}" for x in slots]} and len(body["options"]) == 2
    assert "Выберите удобное время:" in body["text"] and "<b>" not in body["text"]
    assert not [u for u, d in http.urls if d.get("chat_id") == "5550001"]     # nothing to the client through the bot
    title, text, rows = notify.card("slots", token)
    assert "Ваке" in title and "<b>Выберите удобное время:</b>" in text
    assert [b["callback_data"] for r in rows for b in r if "callback_data" in b] == [f"ls:pick:{slots[0]}", f"ls:pick:{slots[1]}",
                                                                                    f"ls:own:{lead.id}"]
    assert rows[-1][0]["text"] == "🕐 Другое время"
    assert notify.card("slots", "nope-token") is None and notify.card("survey", token) is None
    leads.add_slots(db, lead.id, [at + timedelta(days=1)], ag)                 # a new slot set: a new token, the old one is dead
    assert notify.card("slots", token) is None and notify.card("slots", token_of(posts[-1][1])[1])


def test_viewing_cards_and_answers_from_the_dm(db, monkeypatch):
    """Reminder, survey, refusal reason and T14 go as inline cards from the operator's account; a numbered reply in the
    DM does what the button does (leads.client_action); «Другое» -> the client's comment; «3 2 3» -> T14 scores."""
    from app.models import Deal, Evaluation, Event
    from app.telegram import client as tg
    op, ag, cl = make_user(db, "operator"), make_user(db, "agent", username="geo"), make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    at = datetime.now(timezone.utc) + timedelta(days=2)
    leads.add_slots(db, lead.id, [at], ag)
    leads.choose_slot(db, db.query(LeadSlot).filter_by(lead_id=lead.id).one().id, cl.id)
    monkeypatch.undo()
    http, posts = FakeHttp(), []
    monkeypatch.setattr(tg, "_client", lambda: http)
    fake_userbot(monkeypatch, posts)
    notify.viewing_reminder(db.get(Lead, lead.id))
    kind, token = token_of(posts[-1][1])
    title, text, rows = notify.card(kind, token)
    assert kind == "remind" and "Через час просмотр" in title
    assert [b["callback_data"] for r in rows for b in r] == [f"lv:ok:{lead.id}", f"lv:late:{lead.id}", f"lv:resched:{lead.id}"]
    assert posts[-1][1]["choice"]["options"] == [f"lv:ok:{lead.id}", f"lv:late:{lead.id}", f"lv:resched:{lead.id}"]
    userbot.set_state(cl.id, dm_choice=posts[-1][1]["choice"])                 # what the userbot keeps after sending
    assert userbot.on_incoming(5550001, None, "2") == "choice"                  # «2. ⏰ Опаздываю»
    assert events(db, lead.id, "client_late") == ["Клиент опаздывает"]
    with pytest.raises(DomainError):
        leads.client_action(db, f"lv:ok:{lead.id}", make_client(db, external_id=5550009).id)   # not their lead

    leads.mark_viewing_done(db, lead.id)                                       # -> the survey card
    body = posts[-1][1]
    assert token_of(body)[0] == "survey" and body["options"] == ["Снимаю ✅", "Не подошло ❌", "Думаю 🤔"]
    userbot.set_state(cl.id, dm_choice=body["choice"])
    assert userbot.on_incoming(5550001, None, "2") == "choice"                  # refused -> the reason card
    body = posts[-1][1]
    assert token_of(body)[0] == "reason" and body["choice"]["options"][-1] == f"lr:{lead.id}:other"
    assert leads.client_action(db, f"lr:{lead.id}:other", cl.id).refusal_reason == "other"
    userbot.set_state(cl.id, dm_choice={"lead": lead.id, "comment": True})     # the bot after «Другое»
    assert userbot.on_incoming(5550001, None, "Слишком шумно") == "comment"
    assert events(db, lead.id, "client_comment") == ["Слишком шумно"]

    deal = Deal(lead_id=lead.id, agent_id=ag.id, price=850, status="confirmed")
    db.add(deal)
    db.commit()
    notify.t14_client(deal)
    body = posts[-1][1]
    kind, token = token_of(body)
    title, text, rows = notify.card(kind, token)
    assert kind == "t14" and [b["callback_data"] for b in rows[0]] == [f"t14:{deal.id}:1:{n}" for n in range(4)]
    assert "3 2 3" in body["text"] and body["choice"] == {"lead": lead.id, "kind": "t14", "deal": deal.id}
    userbot.set_state(cl.id, dm_choice=body["choice"])
    assert userbot.on_incoming(5550001, None, "3, 2, 1") == "t14"
    ev = db.query(Evaluation).filter_by(deal_id=deal.id, author="client").one()
    assert (ev.h2o_client, ev.h2o_owner, ev.h2o_process) == (3, 2, 1)
    assert not [u for u, d in http.urls if d.get("chat_id") == "5550001"]     # nothing to the client through the bot

    monkeypatch.delenv("USERBOT_URL")                                          # the userbot is down: an event in the history
    notify.refusal_reason_ask(db.get(Lead, lead.id))
    db.expire_all()
    ev = db.query(Event).filter_by(entity="lead", entity_id=str(lead.id), action="client_card_not_delivered").one()
    assert ev.data["note"] == "вопрос о причине отказа: Userbot оператора не подключён"


def test_slots_without_the_userbot_are_an_error_for_the_agent(db, monkeypatch):
    from app.telegram import client as tg
    op, ag, cl = make_user(db, "operator"), make_user(db, "agent"), make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    leads.assign_agent(db, lead.id, ag.id, op)
    monkeypatch.undo()
    http = FakeHttp()
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.delenv("USERBOT_URL", raising=False)
    with pytest.raises(DomainError) as ex:
        leads.add_slots(db, lead.id, [datetime(2031, 3, 4, 12, tzinfo=timezone.utc)], ag)
    assert (ex.value.code, ex.value.status, ex.value.message) == ("userbot_off", 503, "Userbot оператора не подключён")
    db.expire_all()
    ld = db.get(Lead, lead.id)
    assert (ld.status, ld.sla_stage) == ("agent_assigned", "agent_slots")
    assert db.query(LeadSlot).filter_by(lead_id=lead.id).count() == 0
    assert events(db, lead.id, "slots_not_delivered") == ["Userbot оператора не подключён"]
    assert not [u for u, d in http.urls if d.get("chat_id") == "5550001"]


def test_viewing_details_go_from_the_operators_account(db, monkeypatch):
    """After the client picked a time the address and the agent come as a plain DM from the operator (userbot)."""
    from app.telegram import client as tg
    cl = make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    monkeypatch.undo()
    http, got = FakeHttp(), []
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.setattr(userbot, "send_to_client", lambda c, text, *a, **kw: got.append((c["id"], text)) or {"ok": True})
    at = datetime(2031, 3, 4, 12, tzinfo=timezone.utc)
    notify.slot_confirmed_client({"id": lead.id, "client_id": cl.id, "listing_id": lead.listing_id, "viewing_at": at},
                                 {"name": "Георгий", "username": "geo", "phone": "+995 555 000 588"})
    (cid, text), = got
    assert cid == cl.id and text.startswith("✅ Просмотр: ") and "<b>" not in text
    for want in ("📍 Ваке, ул. Палиашвили 10", "👤 Ваш агент: Георгий", "💬 https://t.me/geo"):
        assert want in text
    assert "+995" not in text and "календар" not in text and "ics" not in text   # the t.me link is the contact; no calendar link
    got.clear()
    notify.slot_confirmed_client({"id": lead.id, "client_id": cl.id, "listing_id": lead.listing_id, "viewing_at": at},
                                 {"name": "Георгий", "username": None, "phone": "555 000 588"})
    assert got[0][1].endswith("👤 Ваш агент: Георгий\n📞 +995555000588")      # no @username: the phone, tappable
    assert not [u for u, d in http.urls if d.get("chat_id") == "5550001"]
