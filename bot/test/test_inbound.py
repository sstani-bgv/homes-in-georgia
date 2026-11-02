"""Inbound work-group / channel updates through the real bot application (bot.build → inbound.py → app.services.inbound)
against the fake Bot API (test/e2e/fake_tg.py) and the test Postgres schema: a Listings-topic message lands in
channel_posts, a Make-Foto template gets an owner + report and a reply in its topic, the agency channel keeps saving videos
and also yields a listing, a forwarded #Rented becomes a pending deal, other chats are ignored."""
import asyncio
import os
import sys
import threading
import time
import unittest
from pathlib import Path

import pg
import db

sys.path.insert(0, str(Path(__file__).parent / "e2e"))
from fake_tg import FakeTelegram  # noqa: E402

AGENT = {"id": 6101, "name": "Nika", "username": "nika_inb"}
STRANGER = {"id": 6199, "name": "Guest"}
WORK = {"id": -100800, "title": "Homes work group"}
OTHER = {"id": -100801, "title": "Some other group"}
CHANNEL = {"id": -100802, "title": "Rent Tbilisi", "username": "rent_tbilisi_ge"}

TOPIC_POST = """Studio in Saburtalo near metro, renovated, all appliances
Price: $650 per month
#Saburtalo #Studio #OldBuilding
📞 +995 555 12 34 56 #Nika"""
CHANNEL_POST = """🏠 Cozy 2 bedroom apartment in Vake, new building with a view
📍 Chavchavadze avenue 10, 5/12 floor, 75 m²
💰 1200$ + deposit
#Vake #2Bed #NewBuilding #Exclusive
📞 +995 555 12 34 56 #Nika
[Check all listings](https://t.me/rent_tbilisi_ge)"""
TEMPLATE = "Make Foto\nAddress: Vake, Paliashvili 3\nAgent: #Nika\nOwner: Nino +995 599 44 55 66\nDate: 27.11 14:00\nLink: https://ss.ge/1"
DEAL = """#Rented #Exclusive
Link: https://t.me/rent_tbilisi_ge/501
Price: 900$
Commission: 450$
Platform Commission: 225$
#Nika Commission: 225$
Date of Rent: 01.12.2026 - 01.12.2027"""


class InboundE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(__file__).parent / "_inbound_tmp"
        cls.fake = FakeTelegram(str(cls.tmp))
        port = cls.fake.serve()
        cls.env = {"TG_API_BASE": f"http://127.0.0.1:{port}", "BOT_TOKEN": "123456:INB-FAKE", "PUBLIC_URL": "",
                   "OPERATORS_CHAT_ID": "", "WORK_GROUP_CHAT_ID": str(WORK["id"]), "TOPIC_LISTINGS": "3",
                   "TOPIC_PHOTO_QUEUE": "458", "TOPIC_DEAL": "9", "TOPIC_NEWS": "107", "NEWS_CHAT_ID": "",
                   "AGENCY_CHANNEL": "rent_tbilisi_ge", "AGENCY_CHANNEL_ID": "", "DEALS_CHANNEL_ID": "", "SUPERADMIN_TG_IDS": ""}
        cls.saved = {k: os.environ.get(k) for k in cls.env}
        os.environ.update(cls.env)
        pg.reset()
        with db.tx() as c:
            c.execute("INSERT INTO users (tg_id, name, username, role) VALUES (?, 'Nika', 'nika_inb', 'agent')", (AGENT["id"],))
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

    def q(self, sql, *args):
        with db.tx() as c:
            return c.execute(sql, args).fetchall()

    def until(self, sql, *args, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            rows = self.q(sql, *args)
            if rows:
                return rows
            time.sleep(0.2)
        raise AssertionError(f"timeout waiting for {sql} {args}")

    def test_1_topic_listing_lands_in_db_other_chats_ignored(self):
        self.fake.send_topic_text(AGENT, OTHER, 3, TOPIC_POST)  # same topic id, other group: ignored
        mid = self.fake.send_topic_text(AGENT, WORK, 3, TOPIC_POST)
        rows = self.until("SELECT message_id, agent_name, district, price, rooms, status FROM channel_posts WHERE message_id = ?",
                          f"wg:{mid}")
        self.assertEqual(tuple(rows[0][1:]), ("Nika", "Saburtalo", 650, "Studio", "active"))
        self.fake.send_topic_text(AGENT, WORK, 3, TOPIC_POST.replace("650", "700"))  # later message processed → the other was too
        self.until("SELECT 1 FROM channel_posts WHERE price = 700")
        self.assertEqual(self.q("SELECT count(*) FROM channel_posts")[0][0], 2)

    def test_2_photo_queue_reply_in_topic(self):
        mark = self.fake.mark()
        mid = self.fake.send_topic_text(AGENT, WORK, 458, TEMPLATE)
        out = self.fake.wait(lambda calls: next((c for c in calls if c["method"] == "sendMessage" and c.get("chat") == WORK["id"]
                                                 and "Добавлено в базу" in (c.get("text") or "")), None),
                             since=mark, timeout=15, what="makefoto reply")
        self.assertEqual(str(out.get("message_thread_id")), "458")
        self.assertIn(f'"message_id": {mid}', str(out.get("reply_parameters")))  # a reply to the template message
        self.until("SELECT 1 FROM reports WHERE kind = 'makefoto' AND data->>'tg_ref' = ?", f"{WORK['id']}:{mid}")
        self.assertEqual(self.q("SELECT phone_norm FROM owners")[0][0], "599445566")

    def test_3_channel_post_video_and_listing(self):
        mid = self.fake.channel_post(CHANNEL, CHANNEL_POST, video="VIDX", media_group_id="g7")
        self.until("SELECT 1 FROM channel_posts WHERE message_id = ?", str(mid))
        rows = self.until("SELECT file_id FROM channel_videos WHERE message_id = ?", mid)  # channel-video logic still works
        self.assertEqual(rows[0][0], "VIDX")

    def test_4_forwarded_deal_in_private(self):
        mark = self.fake.mark()
        _, m = self.fake.message(AGENT, text=DEAL, forward_origin={"type": "channel", "date": int(time.time()), "message_id": 4242,
                                                                   "chat": {"id": CHANNEL["id"], "type": "channel", "title": "x"}})
        self.fake.push("text", {"message": m}, {"chat": AGENT["id"], "from": AGENT["name"], "text": DEAL})
        rows = self.until("SELECT status, source, tg_ref FROM deals")
        self.assertEqual(tuple(rows[0]), ("pending", "telegram", f"{CHANNEL['id']}:4242"))
        self.fake.wait(lambda calls: any(c["method"] == "sendMessage" and c.get("chat") == AGENT["id"]
                                         and "Сохранена" in (c.get("text") or "") for c in calls), since=mark, timeout=15,
                       what="forward reply")
        # a stranger's forward is not a deal (and doesn't start the client search either)
        _, m2 = self.fake.message(STRANGER, text=DEAL.replace("501", "502"),
                                  forward_origin={"type": "hidden_user", "sender_user_name": "x", "date": int(time.time())})
        self.fake.push("text", {"message": m2}, {"chat": STRANGER["id"], "from": STRANGER["name"], "text": DEAL})
        time.sleep(1.5)
        self.assertEqual(self.q("SELECT count(*) FROM deals")[0][0], 1)


if __name__ == "__main__":
    unittest.main()
