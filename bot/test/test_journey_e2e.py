"""End-to-end client journey (docs/user-journey.txt, funnel 2) through the real bot application and the real backend
services against the fake Bot API (test/e2e/fake_tg.py) and the test Postgres schema; LLM and geocoding are stubbed:
/start → questionnaire (A1a-A2a) → search → ❤️ (A3) → operators group take + assign (B1/B3) → agent quick slots (C6/C7)
→ client picks a slot (A4) → contacts to the agent (C8) → late → survey (A5a/C9a) → deal confirmed (C9c) → T14 (A6)."""
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
        cls.env = {"TG_API_BASE": f"http://127.0.0.1:{port}", "BOT_TOKEN": "123456:E2E-FAKE", "PUBLIC_URL": "https://crm.e2e.test",
                   "OPERATORS_CHAT_ID": str(GROUP["id"]), "AGENT_CODE": "e2e-code", "WORK_GROUP_CHAT_ID": "", "SUPERADMIN_TG_IDS": ""}
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
        self.press(CLIENT, cid, "2", since=m)                         # A1a: 1 of 6
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
        m = self.say(CLIENT, "до 900")
        self.with_text(cid, "Понял: <b>до $900</b>", m)
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

        m = self.fake.mark()
        f.press(CLIENT, card, "❤️ Нравится")                           # A3
        grp = self.with_text(GROUP["id"], "Заявка #1", m)              # B1
        self.assertIn("2 человека", grp["text"])
        ack = self.find(cid, lambda c: c["method"] == "editMessageText" and "✅ Передано оператору" in c["text"], m, "A3 edit")
        self.assertEqual(ack["message_id"], card["message_id"])       # the same card changed
        self.assertEqual(self.fake.markup(ack)[0][0]["text"], "❤️ В избранном")
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
        slots = self.with_text(cid, "Выберите удобное время", m)                   # A4a
        labels = [b["text"] for row in f.markup(slots) for b in row]
        self.assertEqual(labels[-1], "🕐 Предложить своё время")
        self.assertIn(times[0], labels)

        m = self.press(CLIENT, cid, times[0], since=m)                           # A4b
        conf = self.with_text(cid, "✅ <b>Просмотр:", m)
        self.assertIn("Ваш агент: <b>Георгий</b>", conf["text"])
        self.assertIn("+995 555 000 588", conf["text"])
        self.assertEqual(f.markup(conf)[0][0]["url"], "https://t.me/georgiy_e2e")
        contacts = self.with_text(AGENT["id"], "🔓 <b>Контакты клиента</b>", m)   # C8
        self.assertIn("@anna_e2e", contacts["text"])
        self.find(cid, lambda c: c["method"] == "editMessageReplyMarkup" and f.markup(c)[0][0]["text"] == "✓ " + times[0], m, "✓ slot")

        m = self.press(CLIENT, cid, "⏰ Опаздываю", since=m)
        self.with_text(AGENT["id"], "опаздывает на просмотр #1", m)

        from app.services import deals, leads
        m = f.mark()
        self.svc(leads.mark_viewing_done, 1)                                     # what the worker does after the viewing
        self.with_text(cid, "как прошёл просмотр квартиры", m)                   # A5a
        self.with_text(AGENT["id"], "Как прошёл просмотр #1?", m)                # C9a
        survey = m
        m = self.press(CLIENT, cid, "Снимаю ✅", since=survey)
        self.with_text(cid, "Отлично, поздравляем", m)
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

        from app.telegram import notify
        m = f.mark()
        notify.t14_client(deal)                                                  # the worker's t14_client job
        self.with_text(cid, "вы живёте в новой квартире 14 дней", m)             # A6
        m = self.press(CLIENT, cid, "3", since=m)
        m = self.press(CLIENT, cid, "2", since=m)
        m = self.press(CLIENT, cid, "1", since=m)
        self.with_button(cid, "✍️ Есть проблема", m)
        self.with_text(GROUP["id"], f"Сигнал по сделке #{deal.id}", m)           # low score → operators
        m = self.press(CLIENT, cid, "✍️ Есть проблема", since=m)
        m = self.say(CLIENT, "Не работает бойлер")
        self.with_text(cid, "Передал оператору", m)
        with db.tx() as c:
            evs = c.execute("SELECT author, h2o_client, h2o_owner, h2o_process, complaints, complaints_text FROM evaluations "
                            "WHERE deal_id = ? ORDER BY id", (deal.id,)).fetchall()
            self.assertEqual([tuple(r) for r in evs], [("client", 3, 2, 1, False, None), ("client", None, None, None, True, "Не работает бойлер")])
            self.assertEqual(c.execute("SELECT status FROM leads WHERE id = 1").fetchone()[0], "rented")
            kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM messages")}
        self.assertTrue({"card", "group_lead", "agent_lead", "slots", "survey_client", "survey_agent"} <= kinds, kinds)

        m = self.say(CLIENT, "/start")                                           # never re-asked
        self.with_text(cid, "С возвращением", m)
        time.sleep(0.5)
        self.assertFalse([c for c in f.calls(chat=cid, since=m) if "1 из 6" in (c.get("text") or "")])

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
        slots = self.with_text(u["id"], "Pick a convenient time", m)
        self.assertEqual([b["text"] for row in f.markup(slots) for b in row],
                         ["Open the listing", "Tomorrow 12:00", "Tomorrow 17:30", "🕐 Suggest my own time"])
        m = self.press(u, u["id"], "🕐 Suggest my own time", since=m)
        self.with_text(u["id"], "Write a time that suits you", m)
        m = self.say(u, "Saturday after 3 pm")
        self.with_text(u["id"], "Sent to the agent", m)
        again = self.with_text(AGENT["id"], "Клиент предложил своё время:</b> Saturday after 3 pm", m)
        self.assertIn(f"ls:quick:{lead.id}", [b.get("callback_data") for row in f.markup(again) for b in row])
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT status, client_note FROM leads WHERE id = ?", (lead.id,)).fetchone()[:],
                             ("slots_sent", "Saturday after 3 pm"))

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
        self.with_text(u["id"], "Выберите удобное время", m)
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
