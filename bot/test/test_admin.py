"""Admin panel over HTTP (temp DB, real server on a free port) and Google Sheets row building (no network)."""
import unittest
raise unittest.SkipTest("admin.py is deprecated: superseded by the React CRM (backend/frontend); see docs/README.md")

import http.client
import json
import os
from pathlib import Path
import threading
from unittest import mock
from urllib.parse import urlencode

from PIL import Image

import admin
import core
import db
import sheets
from test_core import Base, item


class AdminBase(Base):
    def setUp(self):
        super().setUp()
        os.environ["ADMIN_PASSWORD"] = "s3cret"
        os.environ.pop("AGENT_APP_URL", None)  # the real .env may set it; tests run over plain http
        os.environ.pop("GOOGLE_SA_JSON", None)
        os.environ.pop("SHEET_ID", None)
        admin.PHOTO_DIR = Path(self.tmp.name, "img")
        admin.PHOTO_DIR.mkdir()
        Image.new("RGB", (800, 600), "#032D6A").save(admin.PHOTO_DIR / "ss1_0.jpg")
        Path(self.tmp.name, "secret.jpg").write_bytes((admin.PHOTO_DIR / "ss1_0.jpg").read_bytes())
        self.sync([item("ss1", photos=["/elsewhere/raw/img/ss1_0.jpg", "../secret.jpg", str(Path(self.tmp.name, "secret.jpg"))]),
                   item("ss2", price_usd=1500, district="Сабуртало"), item("tg1", "agency")],
                  [item("ss9", reasons=["одинаковый текст у разных объявлений"])])
        self.server = admin.serve("127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cookie = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def req(self, method, path, form=None, cookie=True, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        h = dict(headers or {})
        if cookie and self.cookie:
            h["Cookie"] = self.cookie
        body = None
        if form is not None:
            body = urlencode(form)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body, h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), data

    def login(self):
        st, h, _ = self.req("POST", "/login", {"password": "s3cret"})
        self.assertEqual(st, 303)
        self.cookie = h["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])

    def text(self, path):
        st, _, data = self.req("GET", path)
        self.assertEqual(st, 200, path)
        return data.decode()


class AdminTest(AdminBase):
    def test_auth(self):
        st, h, _ = self.req("GET", "/")
        self.assertEqual((st, h["Location"]), (303, "/login"))
        self.assertEqual(self.req("GET", "/photo/ss1/0")[0], 303)  # photos too
        self.assertEqual(self.req("POST", "/review", {"id": "ss9", "approve": "1"})[0], 303)
        self.assertEqual(self.status("ss9"), "pending_review")  # unauthenticated post did nothing
        with mock.patch("admin.time.sleep"):
            self.assertEqual(self.req("POST", "/login", {"password": "wrong"})[0], 401)
        self.cookie = f"{admin.COOKIE}=9999999999.forged"
        self.assertEqual(self.req("GET", "/")[0], 303)
        self.login()
        self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertFalse(admin.valid_token(admin.make_token(now=0)))  # expired
        os.environ["ADMIN_PASSWORD"] = "changed"  # password change logs everyone out
        self.assertEqual(self.req("GET", "/")[0], 303)
        os.environ["ADMIN_PASSWORD"] = ""
        self.assertFalse(admin.check_password(""))

    def test_cross_site_post_blocked(self):
        self.login()
        st, _, _ = self.req("POST", "/review", {"id": "ss9", "approve": "1"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        self.assertEqual(self.status("ss9"), "pending_review")

    def test_pages_and_filters(self):
        self.login()
        for path, _ in admin.NAV:
            self.assertIn("Homes in Georgia", self.text(path))
        page = self.text("/?" + urlencode({"source": "ss.ge", "max": "1000"}))
        self.assertIn("ss1", page)
        self.assertNotIn(">ss2<", page)
        self.assertIn("Сабуртало", self.text("/?" + urlencode({"district": "Сабуртало"})))
        self.assertIn("https://example/ss1", page)  # link to the source for staff
        self.assertIn("ss9", self.text("/?status=pending_review"))
        self.assertEqual(self.req("GET", "/nope")[0], 404)

    def test_moderation_shared_with_bot(self):
        self.login()
        self.assertIn("ss9", self.text("/review"))
        st, h, _ = self.req("POST", "/review", {"id": "ss9", "approve": "1"})
        self.assertEqual(st, 303)
        i = core.listing("ss9")
        self.assertEqual((i["status"], i["decision"]), ("published", "approved"))
        self.assertEqual(core.pending(unsent_only=False), [])  # the bot won't send it for review any more
        self.sync([item("ss1"), item("ss2"), item("tg1", "agency")], [item("ss9", reasons=["одинаковый текст у разных объявлений"])])
        self.assertEqual(self.status("ss9"), "published")  # survives the next sync like a bot decision
        self.req("POST", "/review", {"id": "ss2", "approve": "0"})
        self.assertEqual(self.status("ss2"), "rejected")

    def test_leads(self):
        self.login()
        cl = core.client("tg", 42, username="anna", name="Anna")
        ld = core.react(cl, "ss1", "like")
        a = core.agent(100, "David", "david", create=True)
        self.assertIn("@anna", self.text("/leads"))
        self.req("POST", "/leads", {"id": ld["id"], "status": "viewing", "agent": a["id"]})
        ld = core.lead(ld["id"])
        self.assertEqual((ld["status"], ld["agent_id"]), ("viewing", a["id"]))
        self.req("POST", "/leads", {"id": ld["id"], "status": "bogus"})
        self.assertEqual(core.lead(ld["id"])["status"], "viewing")
        page = self.text("/leads")
        self.assertIn("Просмотр назначен", page)
        self.assertIn("@david", page)
        lead_page = self.text(f"/lead/{ld['id']}")  # agents see the exact point: our map + small Leaflet map
        self.assertIn('href="/map?pin=41.710000%2C44.760000', lead_page)
        self.assertIn('id="minimap"', lead_page)
        self.assertIn("/map?pin=41.710000%2C44.760000", self.text("/"))

    def test_agents(self):
        self.login()
        self.req("POST", "/agents/add", {"tg": "555666777", "name": "Nino"})
        self.assertEqual(core.agent(555666777)["name"], "Nino")
        self.req("POST", "/agents/add", {"tg": "@Levan_ge"})
        self.assertIsNone(core.agent(777))  # unknown Telegram id
        a = core.agent(777, "Levan", "levan_ge")  # first contact with the bot binds the username
        self.assertEqual(a["username"], "Levan_ge")
        self.assertIn(777, core.staff_chats())
        self.req("POST", "/agents/toggle", {"id": a["id"]})
        self.assertIsNone(core.agent(777))
        self.assertNotIn(777, core.staff_chats())
        st, h, _ = self.req("POST", "/agents/add", {"tg": "not valid!"})
        self.assertIn("msg=", h["Location"])
        self.assertIn("Levan_ge", self.text("/agents"))

    def test_sources(self):
        self.login()
        self.req("POST", "/sources/add", {"kind": "tg_competitor", "link": "https://t.me/new_flats_tbs", "name": "New"})
        self.req("POST", "/sources/add", {"kind": "fb_group", "link": "https://www.facebook.com/groups/123456789/?ref=share", "lang": "ru"})
        self.req("POST", "/sources/add", {"kind": "tg_competitor", "link": "@new_flats_tbs"})  # duplicate
        self.req("POST", "/sources/add", {"kind": "fb_group", "link": "https://evil.example/groups/x"})
        with db.tx() as c:
            cfg = db.sources_config(c)
            rows = {r["key"]: dict(r) for r in c.execute("SELECT * FROM sources")}
        self.assertIn({"username": "new_flats_tbs", "kind": "competitor", "want": 40}, cfg["telegram_channels"])
        self.assertIn({"key": "123456789", "url": "https://www.facebook.com/groups/123456789/", "lang": "ru", "name": None},
                      cfg["facebook_groups"])
        self.assertEqual(sum(k.lower() == "new_flats_tbs" for k in rows), 1)
        self.req("POST", "/sources/toggle", {"id": rows["new_flats_tbs"]["id"]})
        with db.tx() as c:
            self.assertNotIn("new_flats_tbs", [x["username"] for x in db.sources_config(c)["telegram_channels"]])
        self.req("POST", "/sources/delete", {"id": rows["123456789"]["id"]})
        with db.tx() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM sources WHERE key = '123456789'").fetchone())
        self.assertIn("new_flats_tbs", self.text("/sources"))
        with self.assertRaises(ValueError):
            admin.parse_source("tg_competitor", "https://t.me/../x")

    def test_settings(self):
        self.login()
        self.req("POST", "/settings", {"moderate_all": "1", "lead_target": "group", "group_chat_id": "-100500", "review_batch": "7"})
        self.assertEqual([db.get_setting(k) for k in ("moderate_all", "lead_target", "group_chat_id", "review_batch")],
                         ["1", "group", "-100500", "7"])
        self.req("POST", "/settings", {"lead_target": "agents", "group_chat_id": "x", "review_batch": "7"})  # invalid: ignored
        self.assertEqual(db.get_setting("lead_target"), "group")

    def test_stats(self):
        self.login()
        self.pick = ["ss1"]
        cl = core.client("tg", 42, username="anna", name="Anna")
        core.add_input(cl, text="Ваке")
        core.search(cl)
        ld = core.react(cl, "ss1", "like")
        core.set_lead_status(ld["id"], "rented", None)
        page = self.text("/stats")
        for label in ("Запросов (поисков)", "Заявок", "Сдано"):
            self.assertIn(label, page)
        self.assertIn('<b>1</b><span class="label">Сдано', page)
        self.assertIn('<b>1</b><span class="label">Запросов (поисков)', page)
        self.assertIn('<b>0</b><span class="label">Сдано', self.text("/stats?from=2020-01-01&to=2020-01-31"))

    def test_photos_safe(self):
        self.login()
        st, h, data = self.req("GET", "/photo/ss1/0")  # stored path of another checkout -> same name in PHOTO_DIR
        self.assertEqual((st, h["Content-Type"]), (200, "image/jpeg"))
        self.assertLessEqual(max(Image.open(__import__("io").BytesIO(data)).size), 480)  # a thumbnail
        self.assertEqual(self.req("GET", "/photo/ss1/1")[0], 404)  # ../secret.jpg
        self.assertEqual(self.req("GET", "/photo/ss1/2")[0], 404)  # absolute path outside PHOTO_DIR
        self.assertEqual(self.req("GET", "/photo/ss1/9")[0], 404)
        self.assertEqual(self.req("GET", "/photo/..%2F..%2Fetc/0")[0], 404)
        self.assertEqual(self.req("GET", "/photo/ss2/0")[0], 404)


class SheetsTest(Base):
    def test_rows(self):
        self.sync([item("ss1"), item("tg1", "agency", urls=["https://t.me/x/1", "https://ss.ge/2"])],
                  [item("ss9", reasons=["одинаковый текст у разных объявлений"])])
        cl = core.client("tg", 42, username="anna", name="Anna")
        core.add_input(cl, text="2 спальни в Ваке")
        ld = core.react(cl, "ss1", "like")
        a = core.agent(100, "David", "david", create=True)
        core.set_lead_status(ld["id"], "in_work", a["id"])
        core.agent(101, "Old", None, create=True)
        with db.tx() as c:
            c.execute("UPDATE agents SET active = 0 WHERE tg_id = 101")
        tabs = sheets.build()
        self.assertEqual(list(tabs), ["Объявления", "Заявки", "Клиенты", "Агенты"])
        for rows in tabs.values():
            self.assertTrue(all(len(r) == len(rows[0]) for r in rows))  # rectangular
            self.assertTrue(all(x is not None for r in rows for x in r))  # no None for the API
        lst = {r[0]: dict(zip(tabs["Объявления"][0], r)) for r in tabs["Объявления"][1:]}
        self.assertEqual(lst["ss9"]["Статус"], "На проверке")
        self.assertEqual(lst["tg1"]["Ссылки"], "https://t.me/x/1\nhttps://ss.ge/2")
        lead = dict(zip(tabs["Заявки"][0], tabs["Заявки"][1]))
        self.assertEqual((lead["Статус"], lead["Контакт"], lead["Агент"], lead["Объявление"], lead["Запрос"]),
                         ("В работе", "@anna", "@david", "ss1", "2 спальни в Ваке"))
        client = dict(zip(tabs["Клиенты"][0], tabs["Клиенты"][1]))
        self.assertEqual((client["Username"], client["Заявок"], client["Запрос сейчас"]), ("@anna", 1, "2 спальни в Ваке"))
        agents = {r[1]: dict(zip(tabs["Агенты"][0], r)) for r in tabs["Агенты"][1:]}
        self.assertEqual((agents[100]["Заявок"], agents[100]["Активен"], agents[101]["Активен"]), (1, "да", "нет"))

    def test_not_configured_is_noop(self):
        os.environ.pop("GOOGLE_SA_JSON", None)
        os.environ["SHEET_ID"] = "x"
        self.assertFalse(sheets.safe_sync())
        sheets.sync_later()  # returns at once, no thread, no error
        os.environ["GOOGLE_SA_JSON"] = os.path.join(self.tmp.name, "missing.json")
        self.assertIsNone(sheets.configured())
        os.environ.pop("GOOGLE_SA_JSON"), os.environ.pop("SHEET_ID")


FAKE_TOKEN = "123456:TEST-FAKE-TOKEN"  # never a real bot token: nothing here talks to Telegram


def init_data(user_id=100, token=FAKE_TOKEN, auth_date=None, username="david", **extra):
    """initData the way Telegram signs it (the Mini App gets it in Telegram.WebApp.initData)."""
    import json
    import time
    fields = {"auth_date": str(int(time.time() if auth_date is None else auth_date)), "query_id": "AAH-test",
              "user": json.dumps({"id": user_id, "first_name": "David", "username": username}, separators=(",", ":")), **extra}
    return urlencode({**fields, "hash": admin.init_data_hash(fields, token)})


class MiniAppTest(AdminBase):
    def setUp(self):
        super().setUp()
        os.environ["BOT_TOKEN"] = FAKE_TOKEN
        os.environ.pop("ADMIN_TG_IDS", None)
        os.environ.pop("AGENT_APP_URL", None)
        admin.BACKGROUND = False
        self.tg = []
        self.patch = mock.patch("admin.tg_call", lambda method, **p: self.tg.append((method, p)))
        self.patch.start()
        self.agent = core.agent(100, "David", "david", create=True)
        cl = core.client("tg", 42, username="anna", name="Anna")
        core.add_input(cl, text="2 спальни в Ваке до $900")
        self.lead = core.react(cl, "ss1", "like")

    def tearDown(self):
        self.patch.stop()
        admin.BACKGROUND = True
        for k in ("BOT_TOKEN", "ADMIN_TG_IDS", "AGENT_APP_URL"):
            os.environ.pop(k, None)
        super().tearDown()

    def tg_login(self, data=None, headers=None):
        st, h, body = self.req("POST", "/tg/auth", {"init_data": data or init_data(), "next": "/leads"}, cookie=False, headers=headers)
        if st == 200:
            self.cookie = h["Set-Cookie"].split(";")[0]
        return st, h, __import__("json").loads(body)

    def test_init_data_validation(self):
        good = init_data()
        self.assertEqual(admin.check_init_data(good)["id"], 100)
        tampered = good.replace("david", "evil")
        self.assertIsNone(admin.check_init_data(tampered))  # user changed, hash kept
        self.assertIsNone(admin.check_init_data(init_data(token="999:OTHER")))  # signed by another bot
        self.assertIsNone(admin.check_init_data(init_data(auth_date=0)))  # expired
        self.assertIsNone(admin.check_init_data(init_data(auth_date=__import__("time").time() + 3600)))  # from the future
        self.assertIsNone(admin.check_init_data(good + "&hash=" + "0" * 64))  # repeated key
        self.assertIsNone(admin.check_init_data(good.split("&hash=")[0]))  # no hash
        self.assertIsNone(admin.check_init_data(""))
        os.environ["BOT_TOKEN"] = ""
        self.assertIsNone(admin.check_init_data(good))  # no token configured: no Telegram login at all
        os.environ["BOT_TOKEN"] = FAKE_TOKEN
        self.assertEqual(admin.check_init_data(init_data(auth_date=1000), max_age=100, now=1050)["id"], 100)
        # the signed string is exactly what Telegram documents: sorted key=value lines
        f = {"b": "2", "a": "1"}
        secret = __import__("hmac").new(b"WebAppData", FAKE_TOKEN.encode(), "sha256").digest()
        self.assertEqual(admin.init_data_hash(f, FAKE_TOKEN), __import__("hmac").new(secret, b"a=1\nb=2", "sha256").hexdigest())

    def test_tg_auth(self):
        st, _, body = self.tg_login(init_data(user_id=555))  # valid signature, but not an agent
        self.assertEqual((st, body["ok"]), (403, False))
        self.assertIn("/agent", body["error"])
        st, _, body = self.tg_login(init_data().replace("david", "evil"))
        self.assertEqual(st, 403)
        self.assertEqual(self.req("GET", "/leads")[0], 303)  # no cookie was set
        st, h, body = self.tg_login()
        self.assertEqual((st, body), (200, {"ok": True, "next": "/leads"}))
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Lax", h["Set-Cookie"])  # plain http (tests): Lax, not Secure
        self.assertNotIn("Secure", h["Set-Cookie"])
        self.assertIn('class="tabs"', self.text("/leads"))  # phone layout with the tab bar
        os.environ["ADMIN_TG_IDS"] = "555, 777"  # an owner who is not an agent
        self.cookie = None
        self.assertEqual(self.tg_login(init_data(user_id=555))[0], 200)
        self.assertIn("администратор", self.text("/leads"))

    def test_agent_bound_by_username(self):
        admin.add_agent("@nino_ge", "Nino")
        st, _, _ = self.tg_login(init_data(user_id=4242, username="Nino_GE"))
        self.assertEqual(st, 200)
        self.assertEqual(core.agent(4242)["name"], "Nino")

    def test_pages_for_agent(self):
        self.tg_login()
        for path in ["/leads", "/review", "/", "/sources", "/agents", "/settings", "/stats", "/more", f"/lead/{self.lead['id']}",
                     "/leads?f=all", "/leads?status=new", "/?status=pending_review"]:
            page = self.text(path)
            self.assertIn("Homes in Georgia", page)
            self.assertIn('class="tabs"', page)
            self.assertIn("telegram-web-app.js", page)
        mine = self.text("/leads")
        self.assertIn(f'href="/lead/{self.lead["id"]}"', mine)  # new lead is in "my leads + new"
        self.assertIn("Мои и новые", mine)
        detail = self.text(f"/lead/{self.lead['id']}")
        for s in ("🛠 В работе", "📅 Просмотр назначен", "🏁 Сдана", "✖️ Отказ"):
            self.assertIn(s, detail)
        self.assertIn("https://t.me/anna", detail)          # write to the client
        self.assertIn("https://example/ss1", detail)        # the source link (staff only)
        self.assertIn("2 спальни в Ваке до $900", detail)   # the client's request
        self.assertIn("/photo/ss1/0", detail)
        self.assertIn('data-back="/leads"', detail)          # Telegram BackButton
        self.assertEqual(self.req("GET", "/lead/999")[0], 200)  # friendly "not found"

    def test_status_change_in_miniapp(self):
        core.remember_message("lead", self.lead["id"], 100, 11)      # the card in the agent's private chat
        core.remember_message("lead", self.lead["id"], -100500, 12)  # and in a group
        os.environ["AGENT_APP_URL"] = "https://panel.example.trycloudflare.com/tg/"
        self.tg_login()
        st, h, _ = self.req("POST", "/leads", {"id": self.lead["id"], "status": "viewing", "back": f"/lead/{self.lead['id']}"})
        self.assertEqual(st, 303)
        self.assertTrue(h["Location"].startswith(f"/lead/{self.lead['id']}?msg="))
        ld = core.lead(self.lead["id"])
        self.assertEqual((ld["status"], ld["agent_id"]), ("viewing", self.agent["id"]))  # the agent himself, not a form field
        with db.tx() as c:
            ev = c.execute("SELECT status, agent_id FROM lead_events WHERE lead_id = ? ORDER BY id DESC", (ld["id"],)).fetchone()
        self.assertEqual(tuple(ev), ("viewing", self.agent["id"]))
        # both Telegram copies of the card were edited; the panel button only where web_app buttons are allowed
        edits = {p["chat_id"]: p for m, p in self.tg if m == "editMessageText"}
        self.assertEqual(set(edits), {100, -100500})
        self.assertIn("📅 Просмотр назначен", edits[100]["text"])
        flat = lambda p: [b for row in p["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(flat(edits[100])[-1]["web_app"]["url"], f"https://panel.example.trycloudflare.com/tg?lead={ld['id']}")
        self.assertFalse(any("web_app" in b for b in flat(edits[-100500])))
        self.assertIn("Просмотр назначен", self.text(f"/lead/{ld['id']}"))
        self.req("POST", "/leads", {"id": ld["id"], "status": "bogus"})
        self.assertEqual(core.lead(ld["id"])["status"], "viewing")
        self.assertEqual(self.req("POST", "/leads", {"id": ld["id"], "status": "rented", "back": "//evil.example/x"})[1]["Location"][:7],
                         "/leads?")  # open redirect refused

    def test_review_in_miniapp(self):
        core.remember_message("review", "ss9", 100, 21)
        self.tg_login()
        self.assertIn("ss9", self.text("/review"))
        self.req("POST", "/review", {"id": "ss9", "approve": "1"})
        i = core.listing("ss9")
        self.assertEqual((i["status"], i["decided_by"]), ("published", self.agent["id"]))
        (m, p), = self.tg
        self.assertEqual((m, p["chat_id"], p["message_id"]), ("editMessageText", 100, 21))
        self.assertIn("✅ Опубликовано</b> · @david", p["text"])
        self.assertNotIn("reply_markup", p)  # buttons removed, like in the bot

    def test_settings_in_miniapp(self):
        self.tg_login()
        self.req("POST", "/settings", {"moderate_all": "1", "lead_target": "group", "group_chat_id": "-100777", "review_batch": "5"})
        self.assertEqual((db.get_setting("moderate_all"), db.get_setting("lead_target")), ("1", "group"))
        self.req("POST", "/settings", {"lead_target": "group", "group_chat_id": "", "review_batch": "5"})
        self.assertEqual(db.get_setting("group_chat_id"), "-100777")  # group without an id: refused
        self.req("POST", "/agents/toggle", {"id": self.agent["id"]})
        self.assertTrue(core.agent(100))  # cannot lock himself out

    def test_access_revoked(self):
        self.tg_login()
        self.assertEqual(self.req("GET", "/leads")[0], 200)
        with db.tx() as c:
            c.execute("UPDATE agents SET active = 0 WHERE tg_id = 100")
        st, h, _ = self.req("GET", "/leads")
        self.assertEqual((st, h["Location"]), (303, "/login?next=%2Fleads"))
        self.assertEqual(self.req("POST", "/review", {"id": "ss9", "approve": "1"})[0], 303)
        self.assertEqual(self.status("ss9"), "pending_review")

    def test_behind_https_tunnel(self):
        host = "abc-def.trycloudflare.com"
        fwd = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": host, "Origin": f"https://{host}"}
        st, h, _ = self.tg_login(headers=fwd)
        self.assertEqual(st, 200)
        self.assertIn("Secure", h["Set-Cookie"])
        self.assertIn("SameSite=None", h["Set-Cookie"])  # Telegram Web runs the Mini App in an iframe
        self.assertEqual(self.req("POST", "/review", {"id": "ss9", "approve": "1"}, headers=fwd)[0], 303)
        self.assertEqual(self.status("ss9"), "published")
        evil = {**fwd, "Origin": "https://evil.example"}
        self.assertEqual(self.req("POST", "/review", {"id": "ss2", "approve": "0"}, headers=evil)[0], 403)
        self.cookie = None
        st, h, _ = self.req("POST", "/login", {"password": "s3cret"}, headers=fwd)
        self.assertIn("Secure", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])
        st, h, _ = self.req("GET", "/")
        self.assertIn("frame-ancestors 'self' https://web.telegram.org", h["Content-Security-Policy"])

    def test_tokens_are_scoped(self):
        tok = admin.make_token(who="tg100")
        self.assertEqual(admin.valid_token(tok), "tg100")
        self.assertIsNone(admin.valid_token(tok.replace("tg100", "tg101")))  # cannot become another user
        self.assertIsNone(admin.valid_token(tok.replace("tg100", "pw")))
        os.environ["BOT_TOKEN"] = "other:TOKEN"
        self.assertIsNone(admin.valid_token(tok))  # bot token change logs Telegram sessions out


class JourneyTest(AdminBase):
    """Public /journey page and its answers form: store, notify (Telegram mocked), origin, size and rate limits."""

    def setUp(self):
        super().setUp()
        admin.BACKGROUND = False
        os.environ["JOURNEY_NOTIFY_CHAT"] = "-100123, 456"
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.page = Path(self.tmp.name, "journey.html")
        self.page.write_text("<!doctype html><title>journey</title>")
        self._page = admin.JOURNEY_PAGE
        admin.JOURNEY_PAGE = self.page

    def tearDown(self):
        admin.BACKGROUND = True
        admin.JOURNEY_PAGE = self._page
        os.environ.pop("JOURNEY_NOTIFY_CHAT", None)
        super().tearDown()

    def post(self, payload, origin="self", ip=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        h = {"Content-Type": "application/json"}
        if origin:
            h["Origin"] = f"http://{self.host}" if origin == "self" else origin
        if ip:
            h["X-Forwarded-For"] = ip
        body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode()
        conn.request("POST", "/journey/answers", body, h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, json.loads(data or b"{}")

    def rows(self):
        with db.tx() as c:
            return c.execute("SELECT * FROM journey_answers ORDER BY id").fetchall()

    def test_page_is_public(self):
        st, _, data = self.req("GET", "/journey", cookie=False)
        self.assertEqual((st, data), (200, b"<!doctype html><title>journey</title>"))

    def test_store_and_notify(self):
        sent = []
        with mock.patch("admin.tg_call", lambda method, **p: sent.append((method, p))):
            st, body = self.post({"name": "Давид", "answers": [
                {"id": "q1", "question": "Срок ответа клиенту", "answer": "30 минут"},
                {"id": "q2", "question": "Таймер оператора", "answer": ""}]}, ip="203.0.113.7")
        self.assertEqual((st, body), (200, {"ok": True}))
        r = self.rows()[0]
        self.assertEqual(r["name"], "Давид")
        self.assertEqual(json.loads(r["payload"])["answers"][0]["answer"], "30 минут")
        self.assertNotIn("203.0.113.7", r["ip_hash"])
        self.assertEqual(len(r["ip_hash"]), 24)
        self.assertEqual([p["chat_id"] for _, p in sent], ["-100123", "456"])
        self.assertEqual(sent[0][1]["text"], "📝 Ответы на макеты от Давид:\n1. Срок ответа клиенту: 30 минут")
        self.login()
        page = self.text("/answers")
        self.assertIn("Давид", page)
        self.assertIn("30 минут", page)
        self.assertEqual(self.req("GET", "/answers", cookie=False)[0], 303)  # the list is behind login

    def test_long_answers_are_split(self):
        chunks = admin.journey_text("A", [{"id": str(i), "question": f"Вопрос {i}", "answer": "x" * 1500} for i in range(5)])
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 4000 for c in chunks))
        self.assertTrue(all(len(c) <= 4000 for c in admin.journey_text("A", [{"id": "1", "question": "Q", "answer": "y" * 9000}])))

    def test_rejects(self):
        with mock.patch("admin.tg_call") as tg:
            self.assertEqual(self.post({"answers": [{"id": "q", "question": "Q", "answer": "a"}]}, origin="https://evil.example")[0], 403)
            self.assertEqual(self.post({"answers": [{"id": "q", "question": "Q", "answer": "a"}]}, origin=None)[0], 403)
            self.assertEqual(self.post(None, raw=b"not json")[0], 400)
            self.assertEqual(self.post({"answers": [{"id": "q", "question": "Q", "answer": "  "}]})[0], 400)
            self.assertEqual(self.post({"answers": "x"})[0], 400)
            big = {"answers": [{"id": "q", "question": "Q", "answer": "x" * 60_000}]}
            self.assertEqual(self.post(big)[0], 413)
            tg.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_rate_limit(self):
        one = {"answers": [{"id": "q", "question": "Q", "answer": "a"}]}
        with mock.patch("admin.tg_call"):
            for _ in range(admin.JOURNEY_PER_HOUR):
                self.assertEqual(self.post(one, ip="198.51.100.1")[0], 200)
            self.assertEqual(self.post(one, ip="198.51.100.1")[0], 429)
            self.assertEqual(self.post(one, ip="198.51.100.2")[0], 200)  # another sender is not affected
        self.assertEqual(len(self.rows()), admin.JOURNEY_PER_HOUR + 1)


class BotMenuTest(Base):
    def test_menu_button_only_for_agents(self):
        import asyncio
        import bot
        calls = []

        class FakeBot:
            async def set_chat_menu_button(self, chat_id, menu_button):
                calls.append((chat_id, menu_button.type, getattr(getattr(menu_button, "web_app", None), "url", None)))

        bot.menus.clear()
        os.environ.pop("AGENT_APP_URL", None)
        asyncio.run(bot.sync_menu(FakeBot(), 100))
        self.assertEqual(calls, [])  # not configured: quietly nothing
        os.environ["AGENT_APP_URL"] = "https://panel.example.com"
        try:
            core.agent(100, "David", "david", create=True)
            core.client("tg", 42)
            for chat in (100, 42, 100, 42):
                asyncio.run(bot.sync_menu(FakeBot(), chat))
            self.assertEqual(calls, [(100, "web_app", "https://panel.example.com/tg")])  # once; the client untouched
            with db.tx() as c:
                c.execute("UPDATE agents SET active = 0 WHERE tg_id = 100")
            asyncio.run(bot.sync_menu(FakeBot(), 100))
            self.assertEqual(calls[-1][:2], (100, "default"))  # disabled agent loses the panel button
            kb = core.lead_keyboard({"id": 7}, core.app_url())
            self.assertEqual(kb["inline_keyboard"][-1][0]["web_app"]["url"], "https://panel.example.com/tg?lead=7")
            self.assertEqual(len(core.lead_keyboard({"id": 7})["inline_keyboard"]), 2)
            self.assertEqual(bot.lead_kb({"id": 7}, -100).inline_keyboard[-1][0].text, "🏁 Сдана")
            self.assertEqual(bot.lead_kb({"id": 7}, 100).inline_keyboard[-1][0].web_app.url, "https://panel.example.com/tg?lead=7")
        finally:
            os.environ.pop("AGENT_APP_URL", None)
            bot.menus.clear()
