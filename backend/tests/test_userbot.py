"""Stage 9: the operator userbot (matching DMs to clients/leads, logging, numbered choices, the slot card as the bot's
inline result) with a fake Telethon client; one bot for everyone; the DM handoff link; Georgian in clients.lang."""
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
                                   {"lead": lead.id, "slots": slots}))
    assert mid == 1 and fake.sent[0][0] == "anna_demo"
    assert "1. Завтра 12:00\n2. Завтра 15:00" in fake.sent[0][1] and "Ответьте цифрой" in fake.sent[0][1]
    assert state(db, cl.id)["dm_choice"]["slots"] == slots
    assert userbot.on_incoming(5550001, None, "А с собакой можно?") == "logged"   # not a number, not a time: a message
    assert userbot.on_incoming(5550001, None, "завтра после 18") == "own_time"
    db.expire_all()
    assert db.get(Lead, lead.id).client_note == "завтра после 18"
    leads.add_slots(db, lead.id, [at, at + timedelta(hours=3)], ag)
    slots = [s.id for s in db.query(LeadSlot).filter_by(lead_id=lead.id).order_by(LeadSlot.starts_at)]
    asyncio.run(userbot.send(FakeTelethon(cached={5550001}), cl.id, "Выберите", ["a", "b"], {"lead": lead.id, "slots": slots}))
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
    choice = {"lead": 1, "slots": [7, 8]}
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


def test_one_bot_and_the_dm_handoff(db, monkeypatch):
    """Staff cards and the client card go through the one bot (BOT_TOKEN); after ❤️ the client gets only the handoff:
    «Написать оператору» (t.me/<operator>?text=…) + «Вернуться в бот», no «Ответить»."""
    from app.telegram import client as tg
    monkeypatch.undo()                                                        # the real notify (the autouse `sent` stubs it)
    http = FakeHttp()
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.setenv("OPERATORS_CHAT_ID", "-100777")
    monkeypatch.setenv("OPERATOR_TG_USERNAME", "@op_nino")
    cl, lst = make_client(db), make_listing(db)
    cl.lang = "ka"                                                            # clients_lang_check allows ka (migration 0011)
    db.commit()
    lead = leads.create_from_like(db, cl.id, lst.id)
    by = {(u.rsplit("/", 2)[-2], d.get("chat_id")) for u, d in http.urls}
    assert by >= {("bot123456:TEST-TOKEN", "-100777"), ("bot123456:TEST-TOKEN", "5550001")}
    assert {t for t, _ in by} == {"bot123456:TEST-TOKEN"}
    to_client = [d for u, d in http.urls if d.get("chat_id") == "5550001"]
    hand = next(d for d in to_client if "op_nino" in (d.get("reply_markup") or ""))
    rows = json.loads(hand["reply_markup"])["inline_keyboard"]
    url = rows[0][0]["url"]
    assert url.startswith("https://t.me/op_nino?text=") and len(rows) == 2 and "url" in rows[1][0]
    assert unquote(url.split("text=", 1)[1]) == f"გამარჯობა! მაინტერესებს ობიექტი #{lead.property_id or lst.id} ვაკე, ул. Палиашвили 10 · $850 " \
                                                f"(განაცხადი #{lead.id})"
    assert "ოპერატორისთვის მიწერა" in hand["reply_markup"]
    assert not [d for d in to_client if "lm:" in (d.get("reply_markup") or "") or "Ответить" in (d.get("reply_markup") or "")]
    assert not hasattr(notify, "client_message") and not hasattr(notify, "lead_client_reply")


def test_slots_go_to_the_userbot_as_an_inline_query(db, monkeypatch):
    """add_slots -> a fresh leads.slots_token -> POST USERBOT_URL/send {inline: "slots:<token>", the numbered fallback};
    nothing to the client through the bot. notify.slots_card(token) is what the bot answers that inline query with."""
    import httpx
    from app.telegram import client as tg
    op, ag, cl = make_user(db, "operator"), make_user(db, "agent"), make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)              # (notify stubbed by `sent`)
    leads.assign_agent(db, lead.id, ag.id, op)
    monkeypatch.undo()                                                         # the real notify.slots_to_client
    http, posts = FakeHttp(), []
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.setenv("USERBOT_URL", "http://userbot:8091/")
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None: posts.append((url, json, headers))
                        or type("R", (), {"status_code": 200, "json": lambda self: {"ok": True, "message_id": 9}})())
    at = datetime(2031, 3, 4, 12, tzinfo=timezone.utc)
    leads.add_slots(db, lead.id, [at, at + timedelta(hours=2)], ag)
    db.expire_all()
    token = db.get(Lead, lead.id).slots_token
    slots = [s.id for s in db.query(LeadSlot).filter_by(lead_id=lead.id).order_by(LeadSlot.starts_at)]
    url, body, headers = posts[0]
    assert url == "http://userbot:8091/send" and headers == {"X-Userbot-Token": "test-secret-key-that-is-long-enough-for-hs256-ok"}
    assert token and body["inline"] == f"slots:{token}" and body["client_id"] == cl.id
    assert body["choice"] == {"lead": lead.id, "slots": slots} and len(body["options"]) == 2
    assert "Выберите удобное время:" in body["text"] and "<b>" not in body["text"]
    assert not [u for u, d in http.urls if d.get("chat_id") == "5550001"]     # nothing to the client through the bot
    title, text, rows = notify.slots_card(token)
    assert "Ваке" in title and "<b>Выберите удобное время:</b>" in text
    assert [b["callback_data"] for r in rows for b in r if "callback_data" in b] == [f"ls:pick:{slots[0]}", f"ls:pick:{slots[1]}",
                                                                                    f"ls:own:{lead.id}"]
    assert rows[-1][0]["text"] == "🕐 Другое время"
    assert notify.slots_card("nope-token") is None
    leads.add_slots(db, lead.id, [at + timedelta(days=1)], ag)                 # a new slot set: a new token, the old one is dead
    assert notify.slots_card(token) is None


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
    assert (ld.status, ld.slots_token, ld.sla_stage) == ("agent_assigned", None, "agent_slots")
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
    for want in ("📍 Ваке, ул. Палиашвили 10", "👤 Ваш агент: Георгий", "📞 +995 555 000 588", "💬 Написать агенту в Telegram: https://t.me/geo"):
        assert want in text
    assert not [u for u, d in http.urls if d.get("chat_id") == "5550001"]
