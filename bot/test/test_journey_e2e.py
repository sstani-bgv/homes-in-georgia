"""End-to-end client journey (docs/user-journey.txt, funnel 2) through the real bot application and the real backend
services against the fake Bot API (test/e2e/fake_tg.py) and the test Postgres schema; LLM and geocoding are stubbed:
/start → questionnaire (A1a-A2a) → search → ❤️ (A3 + DM handoff) → operators group take + assign (B1/B3) → agent quick
slots (C6/C7) → the operator's userbot sends the bot's inline slot card into the client's DM (A4a) → the client presses a
slot on that inline message (A4b, edited «✅ Вы выбрали») → contacts to the agent (C8), details as the operator's DM →
late → survey (A5a/C9a) → deal confirmed (C9c) → T14 (A6). The userbot is the real app.telegram.userbot HTTP endpoint
over a fake Telethon client (FakeUserbot) that asks the bot through the fake Bot API."""
import asyncio
import os
import sys
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

import pg
import core
import db

sys.path.insert(0, str(Path(__file__).parent / "e2e"))
from fake_tg import FakeTelegram  # noqa: E402

class Msg:
    def __init__(self, mid):
        self.id = mid


class FakeUserbot:
    """Telethon's TelegramClient as userbot.send uses it, for the operator's account `me`: inline_query pushes an
    inline_query update to the bot and waits for its answerInlineQuery; results[i].click(chat) puts the result into
    the fake log as a «via @bot» message (fake.inline_message); send_message logs a plain DM (fake.userbot_text)."""
    def __init__(self, fake, me):
        self.fake, self.me = fake, me

    async def get_input_entity(self, peer):
        return peer

    async def send_message(self, entity, text, parse_mode=None):
        self.fake.userbot_text(int(entity), text)
        return Msg(1)

    async def inline_query(self, bot, query):
        fake, mark = self.fake, self.fake.mark()
        iq = fake.inline_query(self.me, query)
        ans = await asyncio.to_thread(fake.wait, lambda calls: next((c for c in calls if c["method"] == "answerInlineQuery"
                                                                     and c["inline_query_id"] == iq), None), 20, mark, "inline answer")

        class Result:
            def __init__(self, r):
                self.r = r

            async def click(self, entity):
                fake.inline_message(int(entity), self.r)
                return Msg(2)
        return [Result(r) for r in ans["results"]]


CLIENT = {"id": 1001, "name": "Анна", "username": "anna_e2e", "lang": "ru"}
OPERATOR = {"id": 5001, "name": "Нино", "username": "nino_e2e"}
OPERATOR2 = {"id": 5003, "name": "Тамара", "username": "tamara_e2e"}
AGENT = {"id": 5002, "name": "Георгий", "username": "georgiy_e2e"}
GROUP = {"id": -100500, "title": "Операторы"}


def listing(id, district, price, beds, **kw):
    return {"id": id, "source": "agency", "owner_id": "agency", "urls": [f"https://t.me/rent_tbilisi_ge/{id[2:]}"], "price_usd": price,
            "area": 70.0, "floor": 5, "bedrooms": beds, "address": "ул. Каштановая, 12", "district": district,
            "latlng": [41.71, 44.76], "photos": [], "description_ru": f"Квартира {id}", "features": [], "agent_reasons": [],
            "text": "t", **kw}


class JourneyE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(__file__).parent / "_e2e_tmp"
        cls.fake = FakeTelegram(str(cls.tmp))
        port = cls.fake.serve()
        from app.telegram import userbot
        cls.ub_loop = asyncio.new_event_loop()                      # the operator's userbot: its own loop, as in its process
        threading.Thread(target=cls.ub_loop.run_forever, daemon=True).start()
        cls.ub = userbot.serve_http(FakeUserbot(cls.fake, OPERATOR), cls.ub_loop, 0)
        cls.env = {"TG_API_BASE": f"http://127.0.0.1:{port}", "BOT_TOKEN": "123456:E2E-FAKE", "PUBLIC_URL": "https://crm.e2e.test",
                   "OPERATORS_CHAT_ID": str(GROUP["id"]), "AGENT_CODE": "e2e-code", "WORK_GROUP_CHAT_ID": "", "SUPERADMIN_TG_IDS": "",
                   "USERBOT_URL": f"http://127.0.0.1:{cls.ub.server_address[1]}", "BOT_USERNAME": "homes_in_georgia_test_bot"}
        cls.saved = {k: os.environ.get(k) for k in cls.env}
        os.environ.update(cls.env)
        pg.reset()
        import json
        lst = cls.tmp / "listings.json"
        lst.write_text(json.dumps([listing("tg1", "Ваке", 850, 2, details={"exclusive": True}), listing("tg2", "Сабуртало", 700, 1),
                                   listing("tg3", "Глдани", 400, 1), listing("tg4", "Ваке", 3000, 4)]))
        db.sync(str(lst), str(cls.tmp / "none.json"))
        with db.tx() as c:
            c.execute("INSERT INTO users (tg_id, name, username, role) VALUES (?, 'Нино', 'nino_e2e', 'operator')", (OPERATOR["id"],))
            c.execute("INSERT INTO users (tg_id, name, username, role) VALUES (?, 'Тамара', 'tamara_e2e', 'operator')", (OPERATOR2["id"],))
            c.execute("INSERT INTO users (tg_id, name, username, role, phone, districts) VALUES (?, 'Георгий', 'georgiy_e2e', 'agent', "
                      "'+995 555 000 588', '[\"Ваке\"]')", (AGENT["id"],))
        db.set_setting("userbot_tg_id", OPERATOR["id"])               # what the userbot saves on start: its account = Нино
        cls.prompts = []
        core.ask = cls.fake_ask
        core.geocode = lambda q: None
        import bot
        cls.app = bot.build(cls.env["BOT_TOKEN"])
        cls.stop = threading.Event()
        cls.thread = threading.Thread(target=lambda: asyncio.run(cls.run_bot()), daemon=True)
        cls.thread.start()
        cls.fake.wait(lambda _: cls.fake.polls > 0, timeout=20, what="bot polling")

    @classmethod
    async def run_bot(cls):
        app = cls.app
        await app.initialize()
        await app.start()
        await app.updater.start_polling(poll_interval=0.05, timeout=1)
        while not cls.stop.is_set():
            await asyncio.sleep(0.1)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    @classmethod
    def tearDownClass(cls):
        cls.stop.set()
        cls.thread.join(15)
        cls.fake.server.shutdown()
        cls.ub.shutdown()
        cls.ub_loop.call_soon_threadsafe(cls.ub_loop.stop)
        for k, v in cls.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def fake_ask(cls, prompt, schema, images=(), timeout=None, **kw):
        cls.prompts.append(prompt)
        props = schema["properties"]
        if "top" in props:
            ids = [ln.split(" | ")[0] for ln in prompt.split("Каталог")[-1].splitlines() if ln.startswith("tg")]
            return {"understood": "2 спальни в Ваке до $900", "matches_total": len(ids), "max_price_usd": None,
                    "top": [{"id": x, "reason": "подходит по анкете"} for x in ids]}
        return {"place": None, "label": None, "radius_km": None}

    # ---------- driver helpers ----------

    def find(self, chat, pred, since=0, what="message", timeout=20):
        def look(calls):
            return next((c for c in reversed(calls) if c.get("chat") == chat and pred(c)), None)
        try:
            return self.fake.wait(look, timeout=timeout, since=since, what=what)
        except AssertionError:
            for c in self.fake.calls(since=since)[-8:]:
                print({k: c.get(k) for k in ("method", "chat", "text", "reply_markup", "show_alert")})
            raise

    def with_button(self, chat, text, since=0, prefix=False):
        def has(c):
            return any((b["text"].startswith(text) if prefix else b["text"] == text) for row in self.fake.markup(c) for b in row)
        return self.find(chat, has, since, f"button {text!r} in chat {chat}")

    def with_text(self, chat, needle, since=0):
        return self.find(chat, lambda c: needle in (c.get("text") or c.get("caption") or ""), since, f"text {needle!r} in chat {chat}")

    def press(self, user, chat, text, group=None, since=0, prefix=False):
        out = self.with_button(chat, text, since, prefix)
        if prefix:
            text = next(b["text"] for row in self.fake.markup(out) for b in row if b["text"].startswith(text))
        mark = self.fake.mark()
        self.fake.press(user, out, text, group=group)
        self.fake.wait(lambda calls: any(c["method"] == "answerCallbackQuery" for c in calls), since=mark, what=f"answer to {text!r}")
        return mark

    def inline_card(self, chat, since, what="slot card via the bot"):
        return self.find(chat, lambda c: c["method"] == "viaBot", since, what)

    def press_inline(self, user, card, text):
        """-> (the answerCallbackQuery, the edit of that inline message or None)."""
        mark = self.fake.mark()
        self.fake.press_inline(user, card, text)
        ans = self.fake.wait(lambda calls: next((c for c in calls if c["method"] == "answerCallbackQuery"), None), since=mark,
                             what=f"answer to {text!r}")
        time.sleep(0.3)
        ed = next((c for c in self.fake.calls(since=mark) if c["method"] == "editMessageText"
                   and c.get("inline_message_id") == card["inline_message_id"]), None)
        return ans, ed

    def say(self, user, text):
        mark = self.fake.mark()
        self.fake.send_text(user, text)
        return mark

    def svc(self, fn, *a, **kw):
        from app.db import SessionLocal
        with SessionLocal() as s:
            return fn(s, *a, **kw)

    def user(self, s, tg_id):
        from app.services import users
        return users.by_tg(s, tg_id)

    # ---------- the journey ----------

    def test_client_journey(self):
        f, cid = self.fake, CLIENT["id"]
        m = self.say(CLIENT, "/start")                                # first contact: the language first
        picked = self.press(CLIENT, cid, "🇷🇺 Русский", since=m)
        self.assertFalse([c for c in f.calls(chat=cid, since=m) if c["seq"] <= picked and "1 из 6" in (c.get("text") or "")])
        self.with_text(cid, "Ответьте на 6 коротких вопросов", m)
        first = self.with_text(cid, "1 из 6", m)                      # the ONE questionnaire message
        self.press(CLIENT, cid, "2", since=m)                         # A1a: 1 of 6
        step2 = self.with_text(cid, "2 из 6", m)
        self.assertEqual((step2["method"], step2["message_id"]), ("editMessageText", first["message_id"]))
        self.assertIn("✓ 👥 2 человека", step2["text"])               # finished answers on top
        m = self.press(CLIENT, cid, "🐕 Собака")                      # 2 of 6, multi
        self.with_button(cid, "✓ 🐕 Собака", m)
        self.press(CLIENT, cid, "Готово ➜", since=m)
        m = self.press(CLIENT, cid, "6 мес")                          # 3 of 6
        self.with_button(cid, "✓ 6 мес", m)
        m = self.press(CLIENT, cid, "12 мес", since=m)
        self.with_button(cid, "✓ 12 мес", m)
        self.press(CLIENT, cid, "Готово ➜", since=m)
        m = self.press(CLIENT, cid, "✏️ Своя сумма")                  # A1b: free text budget
        self.with_text(cid, "Напишите сумму", m)
        m = self.say(CLIENT, "до 900")                                # the client's message stays; the questionnaire moves on
        beds = self.find(cid, lambda c: c["method"] == "editMessageText" and "5 из 6" in c["text"], m, "bedrooms step")
        self.assertEqual(beds["message_id"], first["message_id"])
        self.assertIn("✓ 💰 до $900", beds["text"])
        self.assertIn("✓ 🐾 🐕 собака", beds["text"])
        m = self.press(CLIENT, cid, "1", since=m)                     # 5 of 6, bedrooms
        self.with_button(cid, "✓ 1", m)
        m = self.press(CLIENT, cid, "2", since=m)
        self.with_button(cid, "✓ 2", m)
        self.press(CLIENT, cid, "Готово ➜", since=m)
        m = self.press(CLIENT, cid, "🏙 Выбрать районы")               # A1c
        m = self.press(CLIENT, cid, "Ваке", since=m)
        self.with_button(cid, "✓ Ваке", m)
        m = self.press(CLIENT, cid, "Сабуртало", since=m)
        self.with_button(cid, "✓ Сабуртало", m)
        m = self.press(CLIENT, cid, "Готово ➜", since=m)
        summary = self.with_text(cid, "Ваш профиль", m)               # A2a
        for want in ("2 человека · 🐕 собака", "6 или 12 мес · 💰 до $900", "1-2 спальни", "Ваке, Сабуртало"):
            self.assertIn(want, summary["text"])
        # one message: every step and the summary edit it, no sendMessage per step (only the map keyboard would need one)
        q = [c for c in f.calls(chat=cid, since=first["seq"] - 1) if c["seq"] <= summary["seq"] and c["method"] == "sendMessage"]
        self.assertEqual([c["seq"] for c in q], [first["seq"]], [c.get("text") for c in q])
        self.assertEqual(summary["message_id"], first["message_id"])
        self.assertIn("🌐 Язык", [b["text"] for row in f.markup(summary) for b in row])
        cl = core.client("tg", cid)
        self.assertEqual((cl["profile_done"], cl["profile"]["budget_max"], cl["profile"]["bedrooms"], cl["profile"]["districts"]),
                         (1, 900, [1, 2], ["Ваке", "Сабуртало"]))

        m = self.press(CLIENT, cid, "🔍 Показать варианты")            # A2b: the profile feeds the search
        card = self.with_button(cid, "❤️ Нравится", m)
        cat = self.prompts[-1].split("Каталог")[-1]
        self.assertIn("Анкета клиента:", self.prompts[-1])
        self.assertNotIn("tg4", cat)  # $3000: over the questionnaire budget (districts may be relaxed when < 5 fit, budget never)
        self.assertIn("💡 подходит по анкете", card["text"])
        self.assertNotIn("t.me", card["text"])  # no sources on client cards
        with db.tx() as c:  # stage 8: the shown cards and the saved questionnaire outlive the next request
            views = c.execute("SELECT v.listing_id, v.search_id, s.results FROM client_views v JOIN searches s ON s.id = v.search_id "
                              "WHERE v.client_id = ? AND v.source = 'search'", (cl["id"],)).fetchall()
            saved = c.execute("SELECT data->'profile'->>'budget_max' FROM events WHERE entity = 'client' AND entity_id = ? "
                              "AND action = 'profile_saved'", (str(cl["id"]),)).fetchall()
        self.assertTrue(views and len(views) == views[0][2], views)
        self.assertEqual([r[0] for r in saved], ["900"])

        m = self.fake.mark()
        f.press(CLIENT, card, "❤️ Нравится")                           # A3
        grp = self.with_text(GROUP["id"], "Заявка #1", m)              # B1
        self.assertIn("2 человека", grp["text"])
        ack = self.find(cid, lambda c: c["method"] == "editMessageText" and "✅ Передано оператору" in c["text"], m, "A3 edit")
        self.assertEqual(ack["message_id"], card["message_id"])       # the same card changed
        self.assertEqual(self.fake.markup(ack)[0][0]["text"], "❤️ В избранном")
        hand = self.with_button(cid, "💬 Написать оператору", m)       # DM handoff: prefilled first message to the operator
        with db.tx() as c:
            obj, district, price = c.execute("SELECT COALESCE(l.property_id::text, l.listing_id), s.district, s.price_usd FROM leads l "
                                             "JOIN listings s ON s.id = l.listing_id WHERE l.id = 1").fetchone()[:]
        from urllib.parse import quote
        urls = [b["url"] for row in f.markup(hand) for b in row]
        self.assertEqual(urls[0], "https://t.me/nino_e2e?text=" + quote(f"Здравствуйте! Меня интересует объект #{obj} "
                                                                         f"{district}, ул. Каштановая, 12 · ${price:g} (заявка #1)"))
        self.assertTrue(urls[1].startswith("https://t.me/") and urls[1] != urls[0])   # «↩️ Вернуться в бот»
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT status, sla_stage FROM leads WHERE id = 1").fetchone()[:], ("new", "operator_take"))

        m = self.press(OPERATOR, GROUP["id"], "✋ Взять заявку", group=GROUP)
        taken = self.with_text(GROUP["id"], "✋ <b>Взяла Нино</b>", m)
        self.assertEqual(self.fake.markup(taken)[0][0]["text"], "👤 Назначить агента")
        m = self.fake.mark()
        f.press(OPERATOR2, grp, "✋ Взять заявку", group=GROUP)        # second operator: too late
        alert = f.wait(lambda calls: next((c for c in calls if c["method"] == "answerCallbackQuery"), None), since=m)
        self.assertEqual(alert["text"], "Уже взяла Нино")

        m = self.press(OPERATOR, GROUP["id"], "👤 Назначить агента", group=GROUP)   # B3
        m = self.press(OPERATOR, GROUP["id"], "⭐ Георгий", group=GROUP, since=m, prefix=True)
        c6 = self.with_text(AGENT["id"], "Заявка #1", m)                           # C6: no contacts
        self.assertIn("🔒 Контакты клиента бот пришлёт сам", c6["text"])
        self.assertNotIn("anna_e2e", c6["text"])
        self.with_text(GROUP["id"], "Агент: <b>Георгий</b>", m)

        m = self.press(AGENT, AGENT["id"], "⚡ Время кнопками")                    # C7 in the bot
        quick = self.with_button(AGENT["id"], "📤 Отправить клиенту", m)
        times = [b["text"] for row in f.markup(quick) for b in row if b["callback_data"].startswith("ls:t:")][:2]
        m = self.press(AGENT, AGENT["id"], times[0], since=m)
        self.with_button(AGENT["id"], "✓ " + times[0], m)
        m = self.press(AGENT, AGENT["id"], times[1], since=m)
        m = self.press(AGENT, AGENT["id"], "📤 Отправить клиенту · 2", since=m)
        self.with_text(AGENT["id"], "✅ Отправил клиенту", m)
        slots = self.inline_card(cid, m)                                         # A4a: in the operator's DM, «via @bot»
        iq = next(c for c in f.calls(since=m) if c["method"] == "answerInlineQuery")
        self.assertEqual((iq["cache_time"], iq["is_personal"]), ("0", "true"))
        self.assertIn("Выберите удобное время", slots["text"])
        labels = [b["text"] for row in f.markup(slots) for b in row]
        self.assertEqual(labels[-1], "🕐 Другое время")
        self.assertIn(times[0], labels)
        self.assertFalse([c for c in f.calls(chat=cid, since=m) if c["method"].startswith("send")])   # nothing from the bot itself
        with db.tx() as c:
            token = c.execute("SELECT token FROM inline_cards WHERE kind = 'slots' AND ref = '1'").fetchone()[0]
        for who in (CLIENT, AGENT):                                              # only the operator's account resolves the token
            mark = f.mark()
            qid = f.inline_query(who, f"slots:{token}")
            ans = f.wait(lambda calls: next((c for c in calls if c["method"] == "answerInlineQuery" and c["inline_query_id"] == qid), None),
                         since=mark, what="rejected inline query")
            self.assertEqual(ans["results"], [])

        ans, ed = self.press_inline(OPERATOR, slots, times[0])                   # someone else presses the client's card
        self.assertEqual((ans["text"], ans["show_alert"], ed), ("Это не ваша заявка", "true", None))
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT status FROM leads WHERE id = 1").fetchone()[0], "slots_sent")

        m = f.mark()
        ans, ed = self.press_inline(CLIENT, slots, times[0])                     # A4b
        self.assertEqual(ans.get("text"), "✅")
        self.assertEqual(ed["text"], f"✅ Вы выбрали: {times[0]}")
        self.assertEqual([b["text"] for row in f.markup(ed) for b in row], ["⏰ Опаздываю", "🔁 Перенести"])   # instead of the slots
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT status FROM leads WHERE id = 1").fetchone()[0], "time_chosen")
        conf = self.find(cid, lambda c: c["method"] == "userbotText" and "✅ Просмотр:" in c["text"], m, "details from the operator")
        for want in ("Ваш агент: Георгий", "+995 555 000 588", "https://t.me/georgiy_e2e", "ул. Каштановая, 12"):
            self.assertIn(want, conf["text"])
        contacts = self.with_text(AGENT["id"], "🔓 <b>Контакты клиента</b>", m)   # C8
        self.assertIn("@anna_e2e", contacts["text"])
        picked = self.with_text(OPERATOR["id"], "Клиент выбрал время просмотра · заявка #1", m)   # the operator too
        self.assertIn("Агент: Георгий", picked["text"])
        self.assertFalse([c for c in f.calls(chat=cid, since=m) if c["method"].startswith("send")])
        with db.tx() as c:
            hist = [r[0] for r in c.execute("SELECT kind FROM lead_events WHERE lead_id = 1 ORDER BY id")]
        self.assertIn("time_chosen", hist)                                       # the CRM history shows the client's choice

        from app.telegram import notify, store, userbot
        m = f.mark()
        notify.viewing_reminder(store.lead(1))                                   # the worker's job an hour before
        remind = self.inline_card(cid, m, "reminder card")                       # from the operator's account too
        self.assertIn("Через час просмотр", remind["text"])
        self.with_text(AGENT["id"], "⏰ Через час просмотр #1", m)               # the agent: from the bot
        m = f.mark()
        ans, _ = self.press_inline(CLIENT, remind, "⏰ Опаздываю")
        self.assertIn("опаздываете", ans["text"])
        self.with_text(AGENT["id"], "опаздывает на просмотр #1", m)
        time.sleep(0.5)
        self.assertEqual(len([c for c in f.calls(chat=AGENT["id"], since=m) if "опаздывает" in (c.get("text") or "")]), 1)

        from app.services import deals, leads
        m = f.mark()
        self.svc(leads.mark_viewing_done, 1)                                     # what the worker does after the viewing
        card = self.inline_card(cid, m, "survey card")                           # A5a
        self.assertIn("как прошёл просмотр квартиры", card["text"])
        self.with_text(AGENT["id"], "Как прошёл просмотр #1?", m)                # C9a
        survey = m
        _, ed = self.press_inline(CLIENT, card, "Снимаю ✅")
        self.assertIn("✓ Снимаю ✅", ed["text"])
        self.assertIn("Отлично, поздравляем", ed["text"])
        m = self.press(AGENT, AGENT["id"], "Сдано ✅", since=survey)
        form = self.with_text(AGENT["id"], "Заполните сделку", m)
        self.assertEqual(f.markup(form)[0][0]["web_app"]["url"], "https://crm.e2e.test/m/deals/new?lead=1")
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT status, client_outcome, agent_outcome FROM leads WHERE id = 1").fetchone()[:],
                             ("deal_pending", "rented", "rented"))

        today = date.today()
        deal = self.svc(lambda s: deals.create(s, {"lead_id": 1, "price": 850, "rent_from": today.isoformat(),
                                                   "rent_to": (today + timedelta(days=365)).isoformat(), "is_exclusive": True},
                                               self.user(s, AGENT["id"])))
        m = f.mark()
        self.svc(lambda s: deals.confirm(s, deal.id, self.user(s, OPERATOR["id"])))
        self.with_text(AGENT["id"], "Сделка подтверждена", m)                    # C9c

        m = f.mark()
        notify.t14_client(deal)                                                  # the worker's t14_client job
        card = self.inline_card(cid, m, "T14 card")                              # A6: one card, edited per question
        self.assertIn("вы живёте в новой квартире 14 дней", card["text"])
        _, ed = self.press_inline(CLIENT, card, "3")
        self.assertIn("<b>2.</b>", ed["text"])
        _, ed = self.press_inline(CLIENT, ed, "2")
        m = f.mark()
        _, ed = self.press_inline(CLIENT, ed, "1")
        self.assertIn("Спасибо", ed["text"])
        self.with_text(GROUP["id"], f"Сигнал по сделке #{deal.id}", m)           # low score → operators
        _, ed = self.press_inline(CLIENT, ed, "✍️ Есть проблема")
        self.assertIn("Опишите, пожалуйста, проблему", ed["text"])
        self.assertEqual(userbot.on_incoming(cid, "anna_e2e", "Не работает бойлер"), "problem")   # written in the operator's DM
        with db.tx() as c:
            evs = c.execute("SELECT author, h2o_client, h2o_owner, h2o_process, complaints, complaints_text FROM evaluations "
                            "WHERE deal_id = ? ORDER BY id", (deal.id,)).fetchall()
            self.assertEqual([tuple(r) for r in evs], [("client", 3, 2, 1, False, None), ("client", None, None, None, True, "Не работает бойлер")])
            self.assertEqual(c.execute("SELECT status FROM leads WHERE id = 1").fetchone()[0], "rented")
            kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM messages")}
        self.assertTrue({"card", "group_lead", "agent_lead", "survey_agent"} <= kinds, kinds)
        self.assertFalse([c for c in f.calls(chat=cid) if c["method"].startswith("send") and any(  # the bot: listings only
            (b.get("callback_data") or "").startswith(("ls:", "lv:", "lo:", "lr:", "t14:")) for row in f.markup(c) for b in row)])
        self.assertFalse([b for c in f.calls(chat=cid) for row in f.markup(c) for b in row         # no «Ответить» to the client
                          if b["text"] == "💬 Ответить" or (b.get("callback_data") or "").startswith("lm:")])

        m = self.say(CLIENT, "/start")                                           # never re-asked
        self.with_text(cid, "С возвращением", m)
        time.sleep(0.5)
        self.assertFalse([c for c in f.calls(chat=cid, since=m) if "1 из 6" in (c.get("text") or "")])

    def test_georgian_and_language_switch(self):
        """ka: the picker, the questionnaire in Georgian in one message, clients.lang = ka (DB CHECK), «🌐 Язык» and /lang."""
        f, u = self.fake, {"id": 1020, "name": "ნინო", "username": "nino_ka", "lang": "ka"}
        cid = u["id"]
        self.assertEqual((core.detect_lang("გამარჯობა, ბინა მინდა"), core.detect_lang(None, hint="ka")), ("ka", "ka"))
        m = self.say(u, "/start")
        self.press(u, cid, "🇬🇪 ქართული", since=m)
        self.with_text(cid, "უპასუხეთ 6 მოკლე კითხვას", m)
        first = self.with_text(cid, "1 / 6.", m)
        self.assertEqual(core.client("tg", cid)["lang"], "ka")
        m = self.press(u, cid, "1", since=m)
        self.assertIn("✓ 👥 1 ადამიანი", self.with_text(cid, "2 / 6.", m)["text"])
        m = self.press(u, cid, "🐈 კატა", since=m)
        m = self.press(u, cid, "მზადაა ➜", since=m)
        m = self.press(u, cid, "6 თვე", since=self.with_text(cid, "3 / 6.", m)["seq"] - 1)
        m = self.press(u, cid, "მზადაა ➜", since=m)
        m = self.press(u, cid, "$500-800", since=self.with_text(cid, "4 / 6.", m)["seq"] - 1)
        m = self.press(u, cid, "სტუდიო", since=self.with_text(cid, "5 / 6.", m)["seq"] - 1)
        m = self.press(u, cid, "მზადაა ➜", since=m)
        m = self.press(u, cid, "🏙 უბნების არჩევა", since=self.with_text(cid, "6 / 6.", m)["seq"] - 1)
        m = self.press(u, cid, "ვაკე", since=m)
        m = self.press(u, cid, "მზადაა ➜", since=m)
        summary = self.with_text(cid, "თქვენი პროფილი", m)
        for want in ("1 ადამიანი · 🐈 კატა", "6 თვე · 💰 $500-800", "სტუდიო", "ვაკე"):
            self.assertIn(want, summary["text"])
        self.assertEqual(summary["message_id"], first["message_id"])
        self.assertEqual(core.client("tg", cid)["profile"]["districts"], ["Ваке"])   # stored as listings.district (ru)
        m = self.press(u, cid, "🌐 ენა", since=summary["seq"] - 1)            # language switch from the profile summary
        m = self.press(u, cid, "🇬🇧 English", since=m)
        self.with_text(cid, "Welcome back", m)
        self.assertEqual(core.client("tg", cid)["lang"], "en")
        m = self.say(u, "/lang")                                                 # and by command
        m = self.press(u, cid, "🇷🇺 Русский", since=m)
        self.with_text(cid, "С возвращением", m)
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT lang FROM clients WHERE external_id = ?", (str(cid),)).fetchone()[0], "ru")

    def test_text_slots_and_own_time(self):
        """C7 by text («завтра 12:00, завтра 17:30»), then the client asks for another time (A4a «Предложить своё время»)."""
        from app.services import leads
        f, u = self.fake, {"id": 1002, "name": "John", "username": "john_e2e", "lang": "en"}
        cl = core.client("tg", u["id"], chat_id=u["id"], username="john_e2e", name="John", lang_hint="en")
        lead = self.svc(leads.create_from_like, cl["id"], "tg2", "2 bedrooms")
        m = f.mark()
        self.svc(lambda s: leads.assign_agent(s, lead.id, self.user(s, AGENT["id"]).id, self.user(s, OPERATOR["id"])))
        self.with_text(AGENT["id"], f"Заявка #{lead.id}", m)
        m = self.press(AGENT, AGENT["id"], "✍️ Написать время", since=m)
        self.with_text(AGENT["id"], "Напишите свободное время", m)
        m = self.say(AGENT, "завтра 12:00, завтра 17:30")
        self.with_text(AGENT["id"], "✅ Отправил клиенту: Завтра 12:00, Завтра 17:30", m)
        slots = self.inline_card(u["id"], m)
        self.assertIn("Pick a convenient time", slots["text"])
        self.assertEqual([b["text"] for row in f.markup(slots) for b in row],
                         ["Open the listing", "Tomorrow 12:00", "Tomorrow 17:30", "🕐 Another time"])
        _, ed = self.press_inline(u, slots, "🕐 Another time")
        self.assertIn("Reply with a time that suits you", ed["text"])
        from app.telegram import userbot
        m = f.mark()
        self.assertEqual(userbot.on_incoming(u["id"], "john_e2e", "Saturday after 3 pm"), "own_time")   # typed in the operator's DM
        again = self.with_text(AGENT["id"], "Клиент предложил своё время:</b> Saturday after 3 pm", m)
        self.with_text(OPERATOR["id"], f"Клиент предложил своё время · заявка #{lead.id}</b>\n«Saturday after 3 pm»", m)
        self.assertIn(f"ls:quick:{lead.id}", [b.get("callback_data") for row in f.markup(again) for b in row])
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT status, client_note FROM leads WHERE id = ?", (lead.id,)).fetchone()[:],
                             ("slots_sent", "Saturday after 3 pm"))

    def test_refusal_reason_and_comment(self):
        """«Не подошло» on the survey card -> the reason card from the operator's account -> «Другое»: the client's
        words come as a DM (userbot: client_comment); «🔄 Да, подобрать» on that card searches in the bot."""
        from app.services import leads
        from app.telegram import userbot
        f, u = self.fake, {"id": 1030, "name": "Ира", "username": "ira_e2e", "lang": "ru"}
        cl = core.client("tg", u["id"], chat_id=u["id"], username="ira_e2e", name="Ира", lang_hint="ru")
        lead = self.svc(leads.create_from_like, cl["id"], "tg2")
        self.svc(lambda s: leads.assign_agent(s, lead.id, self.user(s, AGENT["id"]).id, self.user(s, OPERATOR["id"])))
        from datetime import datetime, timezone
        self.svc(lambda s: leads.add_slots(s, lead.id, [datetime.now(timezone.utc) + timedelta(days=1)], self.user(s, AGENT["id"])))
        with db.tx() as c:
            slot = c.execute("SELECT id FROM lead_slots WHERE lead_id = ?", (lead.id,)).fetchone()[0]
        self.svc(leads.choose_slot, slot, cl["id"])
        m = f.mark()
        self.svc(leads.mark_viewing_done, lead.id)
        survey = self.inline_card(u["id"], m, "survey card")
        m = f.mark()
        _, ed = self.press_inline(u, survey, "Не подошло ❌")
        self.assertIn("✓ Не подошло ❌", ed["text"])
        reason = self.inline_card(u["id"], m, "reason card")
        self.assertIn("Что не подошло?", reason["text"])
        _, ed = self.press_inline(u, reason, "Другое")
        self.assertIn("напишите ответным сообщением, что не подошло", ed["text"])
        self.assertEqual(userbot.on_incoming(u["id"], "ira_e2e", "Слишком шумная улица"), "comment")
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT status, refusal_reason FROM leads WHERE id = ?", (lead.id,)).fetchone()[:], ("refused", "other"))
            self.assertEqual(c.execute("SELECT note FROM lead_events WHERE lead_id = ? AND kind = 'client_comment'", (lead.id,)).fetchone()[0],
                             "Слишком шумная улица")
        m = f.mark()
        self.press_inline(u, ed, "🔄 Да, подобрать")
        self.with_text(u["id"], "Смотрю варианты", m)                            # listings: from the bot, as always

    def test_client_text_refines_the_request(self):
        """Operators talk to clients in their own DMs: the bot has no «Ответить» flow; a client's message is always a
        search request, and an old «💬 Ответить» button (lm:) does nothing."""
        from app.services import leads
        f, u = self.fake, {"id": 1010, "name": "Лера", "username": "lera_e2e", "lang": "ru"}
        cl = core.client("tg", u["id"], chat_id=u["id"], name=u["name"], lang_hint="ru")
        first = self.svc(leads.create_from_like, cl["id"], "tg1")
        self.svc(lambda s: leads.operator_take(s, first.id, self.user(s, OPERATOR["id"])))
        # (auto_push_variants=0) new variants come only by «Ещё»: no search until the button.
        before = len(self.prompts)
        mark = self.say(u, "Ищу квартиру до 900")
        noted = self.with_text(u["id"], "Учёл", mark)
        time.sleep(0.5)
        self.assertEqual(len(self.prompts), before)
        mark = f.mark()
        f.press(u, noted, "🔄 Ещё варианты")
        self.find(u["id"], lambda c: c is not noted and any(b.get("callback_data") == "more" for row in f.markup(c) for b in row)
                  and "Учёл" not in (c.get("text") or ""), mark, "search after «Ещё»")
        self.assertGreater(len(self.prompts), before)
        db.set_setting("auto_push_variants", "1")                     # the old behaviour: a message searches at once
        try:
            before, mark = len(self.prompts), self.say(u, "Ищу квартиру до 1000")
            self.find(u["id"], lambda c: "Учёл" not in (c.get("text") or "") and
                      any(b.get("callback_data") == "more" for row in f.markup(c) for b in row), mark, "auto search")
            self.assertGreater(len(self.prompts), before)
        finally:
            db.set_setting("auto_push_variants", "0")
        old = {"chat": u["id"], "message_id": 999, "reply_markup": {"inline_keyboard": [[{"text": "💬 Ответить",
                                                                                          "callback_data": f"lm:reply:{first.id}"}]]}}
        mark = f.mark()
        f.press(u, old, "💬 Ответить")
        f.wait(lambda calls: any(c["method"] == "answerCallbackQuery" for c in calls), since=mark, what="answer")
        time.sleep(0.5)
        self.assertFalse([c for c in f.calls(chat=u["id"], since=mark) if c["method"].startswith("send")])
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM lead_events WHERE kind = 'message_from_client'").fetchone()[0], 0)

    def test_z_staff_robustness(self):
        """An operator added in the CRM by @username takes a lead from the group; «📤 Отправить» tapped twice sends the
        slots once; /cancel ends the typed-slots mode; an edited message is ignored."""
        from app.services import leads
        f = self.fake
        zura, u = {"id": 5004, "name": "Зураб", "username": "zura_e2e"}, {"id": 1003, "name": "Кети", "username": "keti_e2e"}
        with db.tx() as c:
            c.execute("INSERT INTO users (name, username, role) VALUES ('Зураб', '@Zura_e2e', 'operator')")
        cl = core.client("tg", u["id"], chat_id=u["id"], username="keti_e2e", name="Кети", lang_hint="ru")
        m = f.mark()
        lead = self.svc(leads.create_from_like, cl["id"], "tg3", "1 спальня")
        self.with_text(GROUP["id"], f"Заявка #{lead.id}", m)
        m = self.press(zura, GROUP["id"], "✋ Взять заявку", group=GROUP, since=m)
        ans = f.wait(lambda calls: next((c for c in calls if c["method"] == "answerCallbackQuery"), None), since=m)
        self.assertEqual(ans.get("text"), "Заявка ваша ✋")
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT tg_id FROM users WHERE name = 'Зураб'").fetchone()[0], zura["id"])

        m = f.mark()
        self.svc(lambda s: leads.assign_agent(s, lead.id, self.user(s, AGENT["id"]).id, self.user(s, zura["id"])))
        m = self.press(AGENT, AGENT["id"], "⚡ Время кнопками", since=m)
        quick = self.with_button(AGENT["id"], "📤 Отправить клиенту", m)
        first = next(b["text"] for row in f.markup(quick) for b in row if b["callback_data"].startswith("ls:t:"))
        m = self.press(AGENT, AGENT["id"], first, since=m)
        send = self.with_button(AGENT["id"], "📤 Отправить клиенту · 1", m)
        m = f.mark()
        f.press(AGENT, send, "📤 Отправить клиенту · 1")
        f.press(AGENT, send, "📤 Отправить клиенту · 1")
        f.wait(lambda calls: len([c for c in calls if c["method"] == "answerCallbackQuery"]) >= 2, since=m, what="two answers")
        self.inline_card(u["id"], m)
        time.sleep(1)
        self.assertEqual(len([c for c in f.calls(chat=u["id"], since=m) if "Выберите удобное время" in (c.get("text") or c.get("caption") or "")]), 1)

        m = self.press(AGENT, AGENT["id"], "✍️ Написать время", since=0)
        self.with_text(AGENT["id"], "Напишите свободное время", m)
        m = self.say(AGENT, "когда-нибудь")
        self.with_text(AGENT["id"], "Не понял время", m)                         # still waiting
        m = self.say(AGENT, "/cancel")
        self.with_text(AGENT["id"], "Отменено", m)
        m = self.say(AGENT, "завтра 12:00")                                      # not slot times any more: a search
        self.find(AGENT["id"], lambda c: any(b.get("callback_data") == "more" for row in f.markup(c) for b in row), m, "search done")
        self.assertFalse([c for c in f.calls(chat=AGENT["id"], since=m) if "Отправил клиенту" in (c.get("text") or "")])

        m = f.mark()
        f.edit_text(u, 999, "edited text")                                       # edited_message: no handler, no crash
        time.sleep(1)
        self.assertEqual(f.calls(chat=u["id"], since=m), [])


if __name__ == "__main__":
    unittest.main()
