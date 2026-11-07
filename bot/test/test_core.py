"""DB sync, moderation, leads and matching without Codex or network (llm/geo mocked).
Run from the project root: python -m unittest discover -s test -v"""
import json
import os
os.environ["ENV_FILE"] = os.devnull  # never read the real .env (live token, tunnel URL) in tests
from pathlib import Path
import tempfile
import unittest

import pg  # noqa: F401  (test database; before core: points DATABASE_URL at the test schema)
import core
import db


def item(id, source="ss.ge", reasons=(), **kw):
    base = {"id": id, "source": source, "owner_id": id, "urls": [f"https://example/{id}"], "price_usd": 800, "area": 60.0,
            "floor": 3, "bedrooms": 2, "address": "Vake st 1", "district": "Ваке", "latlng": [41.71, 44.76],
            "photos": [], "description_ru": f"Квартира {id}. Звоните +995 599 12 34 56, https://ss.ge/x",
            "features": ["балкон"], "agent_reasons": list(reasons), "agent_suspect": False, "text": "t"}
    return {**base, **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        pg.reset()
        db.DATA_DIR = self.tmp.name
        self.listings = os.path.join(self.tmp.name, "listings.json")
        self.dropped = os.path.join(self.tmp.name, "dropped.json")
        self.calls = []
        core.ask = self.fake_ask
        core.geocode = lambda q: (41.7151, 44.8271)

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, ok, dropped=(), full=True):
        Path(self.listings).write_text(json.dumps(ok))
        Path(self.dropped).write_text(json.dumps(list(dropped)))
        return db.sync(self.listings, self.dropped, full=full)

    def publish_objects(self, *ids):
        """Parser search fixtures become client-visible only after agency publication."""
        with db.tx() as c:
            for lid in ids:
                c.execute("INSERT INTO properties (listing_id, status, post_url, post_message_id, created_at, updated_at) VALUES (?, 'published', 'https://t.me/rent_tbilisi_ge/10', '10', now(), now())", (lid,))

    def status(self, lid):
        return core.listing(lid)["status"]

    def fake_ask(self, prompt, schema, images=(), timeout=None, **kw):
        self.calls.append(prompt)
        props = schema["properties"]
        if "top" in props:
            return {"understood": "2 спальни в Ваке", "matches_total": 3, "max_price_usd": 1000,
                    "top": [{"id": x, "reason": f"подходит, см. @agency_{x}"} for x in self.pick]}
        if "place" in props:
            return {"place": None, "label": None, "radius_km": None}
        return {"items": [{"id": i, "district": "Vake", "description": f"Flat {i}"} for i in self.ids_in(prompt)]}

    @staticmethod
    def ids_in(prompt):
        return [json.loads(line)["id"] for line in prompt.splitlines() if line.startswith("{")]


class SyncTest(Base):
    def test_statuses(self):
        c = self.sync([item("tg1", "agency"), item("ss1"), item("fb1", "fb:g"), item("tg_x_1", "tg:x", agent_suspect=True),
                       item("mh1", "myhome.ge", agent_suspect=True, agent_reason="комиссия")],
                      [item("ss2", reasons=["один телефон в 3+ объявлениях", "ИИ: агент"]),
                       item("ss3", reasons=["одинаковый текст у разных объявлений"]),
                       item("fb2", "fb:g", reasons=["FB: имя или страница автора похожи на агентство"]),
                       item("fb3", "fb:g", reasons=["FB: те же фото у другого автора в другой группе"])])
        got = {i: self.status(i) for i in ("tg1", "ss1", "fb1", "tg_x_1", "mh1", "ss2", "ss3", "fb2", "fb3")}
        self.assertEqual(got, {"tg1": "published", "ss1": "published", "fb1": "published", "tg_x_1": "published",
                               "mh1": "pending_review", "ss2": "rejected", "ss3": "pending_review", "fb2": "rejected",
                               "fb3": "pending_review"})
        self.assertEqual(c, {"published": 4, "pending_review": 3, "rejected": 2, "gone": 0})
        i = core.listing("ss1")
        self.assertEqual((i["lat"], i["urls"], i["features"]), (41.71, ["https://example/ss1"], ["балкон"]))

    def test_moderate_all_and_gone(self):
        rows = [item(f"ss{n}") for n in range(4)] + [item("tg_x_1", "tg:x"), item("fb1", "fb:g"), item("tg1", "agency")]
        self.sync(rows)
        db.set_setting("moderate_all", "1")
        self.sync(rows)
        self.assertEqual([self.status(i) for i in ("tg_x_1", "fb1", "tg1", "ss0")],
                         ["pending_review", "pending_review", "published", "published"])
        core.moderate("fb1", True, None)  # a human decision survives the next sync
        self.sync(rows)
        self.assertEqual(self.status("fb1"), "published")
        self.sync(rows[1:])  # ss0 left the market
        self.assertEqual(self.status("ss0"), "gone")
        self.sync(rows[:1])  # broken ingest (1 of 6): nothing is marked gone
        self.assertEqual(self.status("ss2"), "published")
        self.sync(rows)  # ss0 is back
        self.assertEqual(self.status("ss0"), "published")

    def test_sources_roundtrip(self):
        out = os.path.join(self.tmp.name, "sources.json")
        db.export_sources(out)
        self.assertEqual(json.loads(Path(out).read_text()), json.loads(Path("sources.json").read_text()))


class CoreTest(Base):
    def setUp(self):
        super().setUp()
        self.sync([item("ss1"), item("ss2", price_usd=900), item("ss3", price_usd=5000), item("tg1", "agency")],
                  [item("ss9", reasons=["одинаковый текст у разных объявлений"])])
        self.publish_objects("ss1", "ss2", "ss3")
        self.cl = core.client("tg", 42, username="anna", name="Anna", lang_hint="ru")

    def test_search_only_published_and_budget(self):
        self.pick = ["ss9", "ss3", "ss1", "nope", "ss2"]
        core.add_input(self.cl, text="2 спальни в Ваке до 1000$")
        res = core.search(self.cl)
        self.assertEqual([x["listing"]["id"] for x in res["items"]], ["ss1", "ss2"])  # pending, over budget, unknown dropped
        self.assertEqual(res["base"], 4)
        self.assertNotIn("@", res["items"][0]["reason"])
        self.assertNotIn("ss9", self.calls[-1])  # pending listings never reach the catalog
        # state persists in the DB: a fresh client object sees the shown ids
        self.assertEqual(core.client("tg", 42)["state"]["shown"], ["ss1", "ss2"])
        res = core.search(core.client("tg", 42))  # "more": already shown ones are skipped
        self.assertEqual([x["listing"]["id"] for x in res["items"]], [])

    def test_card_hides_source(self):
        text = core.card(core.listing("ss1"), "светлая", "~1.0 км от метро", "ru")
        for leak in ("+995", "599", "ss.ge", "http"):
            self.assertNotIn(leak, text)
        self.assertIn("$800/мес", text)

    def test_english_client(self):
        self.pick = ["ss1"]
        cl = core.client("tg", 7, name="Bob", lang_hint="en")
        self.assertEqual(cl["lang"], "en")
        core.add_input(cl, text="2 bedrooms in Vake under $1000")
        res = core.search(cl)
        self.assertIn("English", self.calls[-2])  # pick prompt asks for the client's language
        text = core.card(res["items"][0]["listing"], res["items"][0]["reason"], None, "en")
        self.assertIn("Vake", text)
        self.assertIn("Flat ss1", text)
        self.assertEqual(core.listing("ss1")["description_en"], "Flat ss1")  # stored, translated once
        self.assertEqual(core.detect_lang("хочу в Ваке", "en"), "ru")

    def test_reactions(self):
        self.assertEqual(core.react(self.cl, "ss1", "like")["listing"]["id"], "ss1")
        self.assertIsNone(core.react(self.cl, "ss9", "like"))  # not published
        self.assertTrue(core.react(self.cl, "ss2", "dislike"))
        core.react(self.cl, "ss2", "like")  # changed mind
        self.assertEqual([i["id"] for i in core.reactions(self.cl, "like")], ["ss1", "ss2"])
        self.assertEqual(core.reactions(self.cl, "dislike"), [])

    def test_client_catalog_requires_agency_publication(self):
        with db.tx() as c:
            c.execute("DELETE FROM properties")
        self.assertEqual([i["id"] for i in core.published()], ["tg1"])
        # Old settings and stale result cards cannot reopen the parser catalog.
        db.set_setting("client_catalog", "listings")
        self.assertIsNone(core.react(self.cl, "ss1", "like"))
        self.assertIsNone(core.client_listing("ss1"))
        with db.tx() as c:
            c.execute("INSERT INTO reactions (client_id, listing_id, reaction, created_at) VALUES (?, 'ss1', 'like', ?)", (self.cl["id"], db.now()))
        self.assertEqual(core.reactions(self.cl, "like"), [])
        with db.tx() as c:
            c.execute("INSERT INTO properties (listing_id, status, created_at, updated_at) VALUES ('ss3', 'published', now(), now())")
        self.assertIsNone(core.client_listing("ss3"))  # status without successful channel publication is insufficient
        self.publish_objects("ss2")
        with db.tx() as c:
            c.execute("INSERT INTO properties (listing_id, status, created_at, updated_at) VALUES ('ss1', 'taken', now(), now())")
        self.assertEqual(sorted(i["id"] for i in core.published()), ["ss2", "tg1"])
        self.assertTrue(core.react(self.cl, "ss2", "like"))
        with db.tx() as c:
            c.execute("UPDATE properties SET status = 'rented' WHERE listing_id = 'ss2'")
        self.assertIsNone(core.client_listing("ss2"))
        self.assertIsNone(core.react(self.cl, "ss2", "like"))
        self.assertEqual(core.reactions(self.cl, "like"), [])
        with db.tx() as c:
            c.execute("UPDATE listings SET status = 'gone' WHERE id = 'tg1'")
        self.assertEqual(core.published(), [])

    def test_staff_from_users(self):
        a = core.agent(100, "David", "david", create=True)
        self.assertEqual((a["role"], a["name"], a["is_active"]), ("agent", "David", True))
        self.assertIsNone(core.agent(555))
        with db.tx() as c:  # added in the CRM by username, no Telegram id yet: bound on the first message
            c.execute("INSERT INTO users (name, username, role) VALUES ('Нино', 'nino_op', 'operator')")
            c.execute("UPDATE users SET is_active = false WHERE tg_id = 100")
        self.assertEqual(core.agent(200, "Nino", "Nino_Op")["role"], "operator")
        self.assertIsNone(core.agent(100))  # deactivated in the CRM
        self.assertIsNone(core.agent(100, create=True))  # /agent <code> does not re-activate a deactivated user
        self.assertEqual(core.staff_chats()[-1:], [200])

    def test_moderation_and_routing(self):
        self.assertEqual([i["id"] for i in core.pending()], ["ss9"])
        core.mark_review_sent("ss9")
        self.assertEqual(core.pending(), [])
        a = core.agent(100, "David", "david", create=True)
        core.moderate("ss9", True, a["id"])
        self.assertEqual(self.status("ss9"), "published")
        self.assertIn(100, core.staff_chats())
        db.set_setting("lead_target", "group")
        db.set_setting("group_chat_id", "-100500")
        self.assertEqual(core.staff_chats(), [-100500])


class BotWiringTest(unittest.TestCase):
    def test_handlers(self):
        import bot
        app = bot.build("123456:TEST")
        callbacks = {h.callback.__name__ for hs in app.handlers.values() for h in hs}
        self.assertEqual(callbacks, {"on_start", "on_agent", "on_panel", "on_cancel", "on_lang", "on_staff_cmd", "on_button", "on_message",
                                     "on_channel_post", "on_forwarded_deal", "on_group_or_channel", "on_inline_query"})
        self.assertIn("inline_query", bot.ALLOWED_UPDATES)  # the slot card for the operator's userbot
        names = [h.callback.__name__ for h in app.handlers[0]]  # the forward handler is checked before the client journey
        self.assertLess(names.index("on_forwarded_deal"), names.index("on_message"))
        self.assertEqual([h.callback.__name__ for h in app.handlers[1]], ["on_group_or_channel"])
        self.assertEqual(app.error_handlers.keys(), {bot.on_error})



class StartLangTest(Base):
    def test_start_follows_telegram_language(self):
        cl = core.client("tg", 77, lang_hint="ru")
        core.add_input(cl, text="hello, 2 bedrooms please")  # writing in English switches to en
        self.assertEqual(core.client("tg", 77)["lang"], "en")
        core.reset(cl, lang_hint="ru")  # /start again from a Russian Telegram -> Russian greeting
        self.assertEqual(core.client("tg", 77)["lang"], "ru")
        core.reset(cl, lang_hint="en")
        self.assertEqual(core.client("tg", 77)["lang"], "en")

    def test_picked_language_sticks(self):
        cl = core.client("tg", 78, lang_hint="ru")
        core.set_lang(cl, "en")
        core.reset(cl, lang_hint="ru")  # /start from a Russian Telegram keeps the picked language
        core.add_input(cl, text="2 спальни в Ваке")  # so does writing in Russian
        cl = core.client("tg", 78)
        self.assertEqual((cl["lang"], cl["state"]["lang_set"]), ("en", True))

if __name__ == "__main__":
    unittest.main()


class AgentTextTest(unittest.TestCase):
    def test_explicit_agent_label_rejected(self):
        for text in ("ქირავდება ბინა\nაგენტის განცხადება. დაწერეთ WhatsApp-ზე", "Agent's announcement, I do not cooperate",
                     "🚨 LISTING BY AGENCY (agencies, do not disturb)", "I am an agent!!!", "We are an agency and cooperate"):
            self.assertEqual(db.classify({"source": "myhome.ge", "text": text}, False)[0], "rejected", text)

    def test_owner_saying_no_agents_kept(self):
        for text in ("Сдаю сам, без агентов", "No agents please", "აგენტებთან არ ვთანამშრომლობ"):
            self.assertEqual(db.classify({"source": "ss.ge", "text": text}, False)[0], "published", text)


class ClientMapTest(unittest.TestCase):
    """"📍 На карте" under a client card: our map with the listing's point (as the agency's own channel shows it)."""
    BASE = "https://agents.example"
    I = {"id": "ss1", "lat": 41.709123, "lng": 44.761234, "geo_precision": "район", "district": "Ваке",
         "district_en": "Vake", "price_usd": 800}

    def test_pin_link(self):
        from urllib.parse import parse_qs, urlparse
        url = core.pin_link(self.I, self.BASE, "en")
        self.assertTrue(url.startswith(self.BASE + "/map?pin="))
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q, {"pin": ["41.709123,44.761234"], "prec": ["район"], "title": ["Vake · $800"], "lang": ["en"]})
        self.assertNotIn("ss1", url)  # no listing id (it names the source) in what the client sees
        self.assertIsNone(core.pin_link(self.I, "", "ru"))
        self.assertIsNone(core.pin_link({**self.I, "lat": None}, self.BASE))

    def test_card_keyboard(self):
        def texts(kb):
            return [b["text"] for row in kb["inline_keyboard"] for b in row]
        kb = core.card_keyboard(self.I, "ru", self.BASE)
        self.assertEqual(texts(kb), ["❤️ Нравится", "👎 Не то", "📍 На карте"])
        self.assertEqual(kb["inline_keyboard"][1][0]["web_app"]["url"], core.pin_link(self.I, self.BASE, "ru"))
        self.assertEqual(texts(core.card_keyboard(self.I, "en", self.BASE)), ["❤️ I like it", "👎 Not this", "📍 On the map"])
        self.assertEqual(texts(core.card_keyboard(self.I, "ru", "")), ["❤️ Нравится", "👎 Не то"])  # no public URL
        self.assertEqual(texts(core.card_keyboard({**self.I, "lat": None}, "ru", self.BASE)), ["❤️ Нравится", "👎 Не то"])
        self.assertEqual(texts(core.card_keyboard(self.I, "ru", self.BASE, liked=True)), ["✅ Передано агенту", "📍 На карте"])
        from unittest.mock import patch
        with patch.dict(os.environ, {"PUBLIC_URL": "", "AGENT_APP_URL": ""}):
            self.assertEqual(texts(core.card_keyboard(self.I, "ru")), ["❤️ Нравится", "👎 Не то"])


class AgentMapTest(Base):
    def test_review_card_has_the_pin_precision(self):
        self.sync([item("ss1", latlng=[41.709123, 44.761234], geo_precision="точный"), item("ss2", latlng=None)])
        self.assertIn("нет координат", core.review_text(core.listing("ss2")))
        self.assertIn("📍 Точка: точный", core.review_text(core.listing("ss1")))
