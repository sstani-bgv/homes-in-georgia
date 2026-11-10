"""Videos without downloads on the server: agency channel posts (channel_post -> file_id, album grouping), the backfill
(forward -> file_id -> delete), albums preferring the channel file_id; yt-dlp step (fetch_videos.py) with a mocked
downloader; commercial listings kept out of client search. No network, no Telegram. Run: python -m unittest discover -s test"""
import os
os.environ["ENV_FILE"] = os.devnull  # never read the real .env
import asyncio
import json
import tempfile
import unittest
from unittest import mock

from telegram.error import BadRequest, Forbidden

import bot
import core
import db
import fetch_videos
from test_core import Base, item


def post(mid, group=None, video=None, chat="rent_tbilisi_ge", edited=False):
    m = mock.Mock(message_id=mid, media_group_id=group, video=video and mock.Mock(file_id=video))
    m.chat.username = chat
    return mock.Mock(channel_post=None if edited else m, edited_channel_post=m if edited else None)


class ChannelPosts(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("AGENCY_CHANNEL", None)

    def feed(self, *updates):
        for u in updates:
            asyncio.run(bot.on_channel_post(u, None))

    def test_album_video_maps_to_listing_whatever_the_order(self):
        # the album's video (message 109) arrives before the caption photo (100) the listing is named after
        self.feed(post(109, "g1", video="VID"), post(101, "g1"), post(100, "g1"), post(200, None, video="SINGLE"))
        self.assertEqual(bot.channel_video({"id": "tg100", "source": "agency"}), "VID")
        self.assertEqual(bot.channel_video({"id": "tg101", "source": "agency"}), "VID")  # any album message works
        self.assertEqual(bot.channel_video({"id": "tg200", "source": "agency"}), "SINGLE")  # a single video post
        self.assertIsNone(bot.channel_video({"id": "tg300", "source": "agency"}))
        self.assertIsNone(bot.channel_video({"id": "ss100", "source": "ss.ge"}))
        self.feed(post(109, "g1", video="VID2", edited=True))  # the video was replaced in the post
        self.assertEqual(bot.channel_video({"id": "tg100", "source": "agency"}), "VID2")

    def test_other_channels_ignored_and_channel_configurable(self):
        self.feed(post(100, "g", video="X", chat="someone_else"))
        self.assertIsNone(bot.channel_video({"id": "tg100", "source": "agency"}))
        with mock.patch.dict(os.environ, {"AGENCY_CHANNEL": "@My_Channel"}):
            self.feed(post(100, "g", video="X", chat="my_channel"))
            self.assertEqual(bot.channel_video({"id": "tg100", "source": "agency"}), "X")

    def test_bot_listens_to_channel_posts(self):
        self.assertIn("channel_post", bot.ALLOWED_UPDATES)
        self.assertIn("edited_channel_post", bot.ALLOWED_UPDATES)
        app = bot.build("123456:TEST")
        self.assertIn("on_channel_post", {h.callback.__name__ for hs in app.handlers.values() for h in hs})


class FakeBot:
    id, username = 42, "TestBot"

    def __init__(self, fail=None, status="administrator"):
        self.fail = fail or {}  # message id -> exception
        self.forwards, self.deleted, self.n, self.status = [], [], 1000, status

    async def get_chat_member(self, chat_id, user_id):
        if isinstance(self.status, Exception):
            raise self.status
        return mock.Mock(status=self.status)

    async def forward_message(self, chat_id, from_chat_id, message_id, disable_notification=None):
        self.forwards.append((chat_id, from_chat_id, message_id, disable_notification))
        if message_id in self.fail:
            raise self.fail[message_id]
        self.n += 1
        return mock.Mock(chat_id=chat_id, message_id=self.n, video=mock.Mock(file_id=f"F{message_id}"))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class Backfill(Base):
    def test_disabled_does_not_forward_or_mark_history(self):
        b = FakeBot()
        with mock.patch.dict(os.environ, {"VIDEO_BACKFILL": "0"}):
            self.assertEqual(self.run_bf(b), 0)
        self.assertEqual(b.forwards, [])
        self.assertEqual(len(bot.backfill_todo()), 3)

    def setUp(self):
        super().setUp()
        bot.warned.clear()
        self.sync([item(f"tg{n}", "agency", details={"video_msg": n + 9}) for n in (100, 200, 300)]
                  + [item("tg400", "agency"), item("ss1", details={"video_msg": 5})])

    def run_bf(self, b, **kw):
        with mock.patch.dict(os.environ, {"VIDEO_STORE_CHAT_ID": "-100777"}):
            return asyncio.run(bot.backfill_videos(b, pause=0, **kw))

    def test_forward_store_delete_idempotent(self):
        b = FakeBot()
        self.assertEqual(self.run_bf(b), 3)
        self.assertEqual(sorted(x[2] for x in b.forwards), [109, 209, 309])  # only agency posts with a video
        self.assertEqual(b.forwards[0][:2], (-100777, "@rent_tbilisi_ge"))
        self.assertTrue(all(x[3] for x in b.forwards))  # silent
        self.assertEqual(len(b.deleted), 3)  # every forwarded copy deleted
        self.assertEqual(bot.channel_video({"id": "tg200", "source": "agency"}), "F209")
        b2 = FakeBot()
        self.assertEqual(self.run_bf(b2), 0)
        self.assertEqual(b2.forwards, [])  # done ones are never forwarded again

    def test_live_channel_post_makes_backfill_unneeded(self):
        asyncio.run(bot.on_channel_post(post(309, "g", video="LIVE"), None))
        asyncio.run(bot.on_channel_post(post(300, "g"), None))
        b = FakeBot()
        self.run_bf(b)
        self.assertNotIn(309, [x[2] for x in b.forwards])
        self.assertEqual(bot.channel_video({"id": "tg300", "source": "agency"}), "LIVE")

    def test_stops_on_access_error_and_logs_once(self):
        b = FakeBot(fail={309: Forbidden("bot is not a member of the channel chat"),
                          209: Forbidden("bot is not a member of the channel chat"),
                          109: Forbidden("bot is not a member of the channel chat")})
        with mock.patch("builtins.print") as p:
            self.assertEqual(self.run_bf(b), 0)
            self.run_bf(b)
        self.assertEqual(len(b.forwards), 2)  # one try per run, then stop
        self.assertEqual(sum("stopped" in str(c) for c in p.call_args_list), 1)
        self.assertIsNone(bot.channel_video({"id": "tg300", "source": "agency"}))
        self.assertEqual(self.run_bf(FakeBot()), 3)  # bot made an admin later: everything gets done

    def test_deleted_post_is_skipped_for_good(self):
        b = FakeBot(fail={309: BadRequest("Message to forward not found")})
        self.assertEqual(self.run_bf(b), 2)
        b2 = FakeBot()
        self.run_bf(b2)
        self.assertEqual(b2.forwards, [])

    def test_chat_not_found_is_not_a_deleted_post(self):
        # the bot isn't an admin of the channel yet: Telegram says "Chat not found" -> retry later, never mark as gone
        b = FakeBot(fail={n: BadRequest("Chat not found") for n in (109, 209, 309)})
        with mock.patch("builtins.print"):
            self.assertEqual(self.run_bf(b), 0)
        self.assertEqual(len(b.forwards), 1)  # stopped at the first access error
        self.assertEqual(self.run_bf(FakeBot()), 3)  # admin now: all three are fetched

    def test_not_admin_marks_nothing(self):
        for st in ("member", BadRequest("Member list is inaccessible")):
            b = FakeBot(status=st, fail={n: BadRequest("Message to forward not found") for n in (109, 209, 309)})
            with mock.patch("builtins.print"):
                self.assertEqual(self.run_bf(b), 0)
            self.assertEqual(b.forwards, [])  # nothing forwarded, nothing marked as gone
        self.assertEqual(self.run_bf(FakeBot()), 3)  # once admin: all three

    def test_no_store_chat_skips(self):
        with db.tx() as c:
            c.execute("DELETE FROM users")  # db.init seeds agents.json
        with mock.patch.dict(os.environ, {"VIDEO_STORE_CHAT_ID": ""}):
            b = FakeBot()
            self.assertEqual(asyncio.run(bot.backfill_videos(b, pause=0)), 0)  # no agents either
            self.assertEqual(b.forwards, [])
            core.agent(555, "A", "a", create=True)
            self.assertEqual(asyncio.run(bot.backfill_videos(b, pause=0)), 3)
            self.assertEqual(b.forwards[0][0], 555)  # default: the first agent chat


class AlbumPrefersChannelVideo(Base):
    def test_file_id_instead_of_upload(self):
        from telegram import InputMediaVideo
        d = self.tmp.name
        photos = [f"{d}/{n}.jpg" for n in range(2)]
        for p in photos:
            open(p, "wb").write(b"jpg")
        v = f"{d}/v.mp4"
        open(v, "wb").write(b"0" * 3000)
        i = {"id": "tg100", "source": "agency", "photos": photos, "details": {"videos": [{"path": v}]}}
        sent = []

        class B:
            async def send_media_group(self, chat, media):
                sent.append(media)
                return [mock.Mock(video=None)] * 2 + [mock.Mock(video=mock.Mock(file_id="X"))]

        asyncio.run(bot.on_channel_post(post(100, "g"), None))
        asyncio.run(bot.on_channel_post(post(105, "g", video="CHAN"), None))
        asyncio.run(bot.send_album(B(), 1, i))
        self.assertEqual(len(sent[0]), 3)
        self.assertIsInstance(sent[0][2], InputMediaVideo)
        self.assertEqual(sent[0][2].media, "CHAN")  # no local upload
        with db.tx() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM tg_files").fetchone())


class FetchVideos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.items = [{"id": "ss1", "details": {"video_url": "https://static.ss.ge/1.m3u8"}},
                      {"id": "ss2", "details": {"video_url": "https://youtu.be/blocked"}},
                      {"id": "ss3", "details": {"video_url": "https://youtu.be/big"}},
                      {"id": "ss4", "details": {}}]
        json.dump(self.items, open("listings.json", "w"))
        self.calls = []

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def dl(self, url, out_dir, cap):
        self.calls.append(url)
        if "blocked" in url:
            raise RuntimeError("ERROR: [youtube] blocked: Sign in to confirm you're not a bot")
        f = f"{out_dir}/v.mp4"
        open(f, "wb").write(b"0" * (cap + 1 if "big" in url else 1000))
        return f, {"width": 1280, "height": 720, "duration": 31.5}

    def test_download_cap_and_failure_cache(self):
        with mock.patch.dict(os.environ, {"VIDEO_MAX_MB": "0.01"}):  # ~10 KB
            st = fetch_videos.run("listings.json", self.dl, now=1000)
            self.assertEqual((st["ok"], st["failed"]), (1, 2))
            out = {i["id"]: i for i in json.load(open("listings.json"))}
            self.assertEqual(out["ss1"]["details"]["videos"], [{"path": "raw/video/ss1.mp4", "w": 1280, "h": 720, "duration": 31}])
            self.assertEqual(out["ss1"]["details"]["video_url"], "https://static.ss.ge/1.m3u8")  # kept
            self.assertNotIn("videos", out["ss2"]["details"])
            self.assertFalse(os.path.exists("raw/video/ss3.mp4"))  # over the cap: thrown away
            failed = json.load(open("raw/video/failed.json"))
            self.assertIn("not a bot", failed["https://youtu.be/blocked"]["error"])
            self.assertIn("too big", failed["https://youtu.be/big"]["error"])
            self.calls.clear()
            fetch_videos.run("listings.json", self.dl, now=1000 + 86400)  # a day later: nothing retried
            self.assertEqual(self.calls, [])
            fetch_videos.run("listings.json", self.dl, now=1000 + 4 * 86400)  # after VIDEO_RETRY_DAYS (3): retried
            self.assertEqual(sorted(self.calls), ["https://youtu.be/big", "https://youtu.be/blocked"])

    def test_reuses_existing_file_and_card_button(self):
        os.makedirs("raw/video")
        open("raw/video/ss1.mp4", "wb").write(b"x")
        json.dump({"w": 640, "h": 360, "duration": 5}, open("raw/video/ss1.json", "w"))
        with mock.patch.dict(os.environ, {"VIDEO_MAX_PER_RUN": "0"}):
            st = fetch_videos.run("listings.json", self.dl, now=1)
        self.assertEqual((st["reused"], self.calls), (1, []))
        ss1 = json.load(open("listings.json"))[0]
        self.assertEqual(ss1["details"]["videos"][0]["w"], 640)
        # the downloaded video replaces the "🎥 Видео" button; a link we couldn't download keeps it
        self.assertEqual(core.facts.media_links({"details": {"video_url": "https://youtu.be/x"}}), [{"text": "🎥 Видео", "url": "https://youtu.be/x"}])
        self.assertEqual(core.facts.media_links(ss1), [])

    def test_ingest_picks_up_a_downloaded_video(self):
        import ingest
        os.makedirs("raw/video")
        open("raw/video/ss1.mp4", "wb").write(b"x")
        json.dump({"w": 640, "h": 360}, open("raw/video/ss1.json", "w"))
        it = {"id": "ss1", "parsed": {}}
        ingest.download_site_video(it)
        self.assertEqual(it["parsed"]["videos"], [{"path": "raw/video/ss1.mp4", "w": 640, "h": 360}])

    def test_format_prefers_single_file_720p_under_cap(self):
        f = fetch_videos.fmt(20 << 20)
        self.assertTrue(f.startswith("best[ext=mp4][vcodec^=avc1][height<=?720][filesize<?20971520]"))


COMMERCIAL = {"property": "commercial"}


class CommercialLlm(Base):
    def setUp(self):
        super().setUp()
        self.sync([item("ss1"), item("tg2", "agency", details=COMMERCIAL), item("tg3", "agency", details={"property": "apartment"})])
        self.publish_objects("ss1")
        self.cl = core.client("tg", 42, lang_hint="ru")
        self.pick = ["tg2", "ss1", "tg3"]

    def test_excluded_unless_asked(self):
        core.add_input(self.cl, text="2 спальни в Ваке")
        res = core.search(self.cl)
        self.assertNotIn(f"{self.o('tg2')} |", self.calls[-1])
        self.assertEqual(self.ids(res), ["ss1", "tg3"])
        core.add_input(core.client("tg", 42), text="а ещё нужен офис под студию")
        res = core.search(core.client("tg", 42))
        self.assertIn(f"{self.o('tg2')} |", self.calls[-1])
        self.assertEqual(self.ids(res), ["tg2", "ss1", "tg3"])  # a new request: all again

    def test_keywords(self):
        for t in ("ищу офис", "коммерческое помещение", "помещение под кафе", "need an office in Vake",
                  "commercial space", "retail space", "ოფისი ვაკეში", "კომერციული ფართი"):
            self.assertTrue(core.wants_commercial([t]), t)
        for t in ("2 спальни рядом с магазинами", "flat with a home office corner", "складной диван", "квартира в Ваке"):
            self.assertFalse(core.wants_commercial([t]), t)


class CommercialHybrid(Base):
    query = {"place": None, "label": None, "radius_km": None, "understood": "x", "max_price_usd": 1000,
             "bedrooms_min": None, "bedrooms_max": None, "districts": [], "pets": None, "style": ""}

    def fake_ask(self, prompt, schema, images=(), timeout=None, **kw):
        if "bedrooms_min" in schema["properties"]:
            self.calls.append(prompt)
            return dict(self.query)
        return super().fake_ask(prompt, schema, images, timeout, **kw)

    def setUp(self):
        super().setUp()
        self.cl = core.client("tg", 42, lang_hint="ru")
        self.sync([item("ss1"), item("ss2"), item("tg2", "agency", details=COMMERCIAL)])
        self.publish_objects("ss1", "ss2")
        db.set_setting("search_mode", "hybrid")
        db.set_setting("final_llm", "off")

    def test_hybrid_excludes_unless_asked(self):
        core.add_input(self.cl, text="2 спальни в Ваке до 1000$")
        ids = self.ids(core.search(self.cl))
        self.assertEqual(sorted(ids), ["ss1", "ss2"])
        cl = core.client("tg", 43, lang_hint="ru")
        core.add_input(cl, text="commercial space in Vake up to 1000$")
        ids = self.ids(core.search(cl))
        self.assertIn("tg2", ids)


if __name__ == "__main__":
    unittest.main()
