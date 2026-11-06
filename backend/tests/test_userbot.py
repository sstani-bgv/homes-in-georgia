"""Stage 9: the operator userbot (matching DMs to clients/leads, logging, numbered choices) with a fake Telethon client;
the staff bot switch; the DM handoff link; Georgian in clients.lang."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from app.models import Client, Lead, LeadEvent, LeadSlot
from app.services import leads
from app.telegram import notify, userbot
from tests.conftest import make_client, make_client_listing as make_listing, make_user


class FakeTelethon:
    """get_input_entity / send_message as Telethon's TelegramClient: users not in the entity cache raise ValueError."""
    def __init__(self, cached=()):
        self.cached, self.sent = set(cached), []

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
    assert sent.of("lead_client_reply") == []          # the operator has it in their own Telegram already
    assert state(db, cl.id)["operator_dm"] is True
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


def test_message_client_goes_through_the_userbot_after_a_dm(db, sent, monkeypatch):
    op = make_user(db, "operator")
    cl = make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)
    assert userbot.send_to_client({"id": cl.id}, "x") is None      # USERBOT_URL not set: the caller falls back to the bot
    got, bot = [], []
    monkeypatch.setattr(notify, "client_message", lambda *a: bot.append(a[1]) or {"message_id": 1})
    monkeypatch.setattr(userbot, "send_to_client", lambda c, text, *a: got.append(text) or {"ok": True, "message_id": 5})
    monkeypatch.setenv("USERBOT_URL", "http://userbot:8091")
    leads.message_client(db, lead.id, "Через бота", op)          # the client never wrote to the DM: the bot
    assert (got, bot) == ([], ["Через бота"])
    userbot.set_state(cl.id, operator_dm=True)
    db.expire_all()
    leads.message_client(db, lead.id, "Из личного аккаунта", op)
    assert (got, bot) == (["Из личного аккаунта"], ["Через бота"])


class FakeHttp:
    def __init__(self):
        self.urls = []

    def post(self, url, data=None, files=None):
        self.urls.append((url, data))
        chat = int(data.get("chat_id", 0))
        return type("R", (), {"json": lambda self: {"ok": True, "result": {"message_id": 1, "chat": {"id": chat}}}})()


def test_staff_bot_token_routes_staff_cards(db, monkeypatch):
    from app.telegram import client as tg
    monkeypatch.undo()                                                        # the real notify (the autouse `sent` stubs it)
    http = FakeHttp()
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.setenv("OPERATORS_CHAT_ID", "-100777")
    monkeypatch.setenv("OPERATOR_TG_USERNAME", "@op_nino")
    monkeypatch.delenv("STAFF_BOT_TOKEN", raising=False)
    cl, lst = make_client(db), make_listing(db)
    cl.lang = "ka"                                                            # clients_lang_check allows ka (migration 0011)
    db.commit()
    lead = leads.create_from_like(db, cl.id, lst.id)                          # no staff bot: everything as before
    assert {u.rsplit("/", 2)[-2] for u, _ in http.urls} == {"bot123456:TEST-TOKEN"}
    monkeypatch.setenv("STAFF_BOT_TOKEN", "999:STAFF")
    http.urls.clear()
    notify.lead_new_group(lead)                                                # operators group -> the staff bot
    notify.lead_client_ack(lead)                                               # client card + handoff -> the client bot
    by = {(u.rsplit("/", 2)[-2], d.get("chat_id")) for u, d in http.urls}
    assert ("bot999:STAFF", "-100777") in by
    assert ("bot123456:TEST-TOKEN", "5550001") in by and not any(t == "bot999:STAFF" and c == "5550001" for t, c in by)
    hand = next(d for u, d in http.urls if "op_nino" in (d.get("reply_markup") or ""))
    url = json.loads(hand["reply_markup"])["inline_keyboard"][0][0]["url"]
    assert url.startswith("https://t.me/op_nino?text=")
    assert unquote(url.split("text=", 1)[1]) == f"გამარჯობა! მაინტერესებს ობიექტი #{lead.property_id or lst.id} ვაკე, ул. Палиашвили 10 · $850 " \
                                                f"(განაცხადი #{lead.id})"
    assert "ოპერატორისთვის მიწერა" in hand["reply_markup"]


def test_slots_go_as_numbered_options_in_the_dm(db, monkeypatch):
    from app.telegram import client as tg
    op, ag, cl = make_user(db, "operator"), make_user(db, "agent"), make_client(db)
    lead = leads.create_from_like(db, cl.id, make_listing(db).id)              # (notify stubbed by `sent`)
    leads.assign_agent(db, lead.id, ag.id, op)
    userbot.set_state(cl.id, operator_dm=True)
    monkeypatch.undo()                                                         # the real notify.slots_to_client
    http, got = FakeHttp(), []
    monkeypatch.setattr(tg, "_client", lambda: http)
    monkeypatch.setenv("USERBOT_URL", "http://userbot:8091")
    monkeypatch.setattr(userbot, "send_to_client", lambda c, text, options=None, choice=None: got.append((text, options, choice)) or {"ok": 1})
    at = datetime(2031, 3, 4, 12, tzinfo=timezone.utc)
    leads.add_slots(db, lead.id, [at], ag)
    text, options, choice = got[0]
    assert "Выберите удобное время:" in text and "<b>" not in text and len(options) == 1
    assert choice["lead"] == lead.id and len(choice["slots"]) == 1
    assert not [u for u, d in http.urls if d.get("chat_id") == "5550001"]     # nothing to the client through the bot
