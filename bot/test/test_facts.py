"""Channel-style facts: parsers (agency / competitor posts, ss.ge, myhome.ge), cards ru/en, albums with video, caps,
the index cache upgrade and the DB column. No network, no Codex. Post texts are real (raw/channel_*.json, 21.10.2026),
phones and handles kept on purpose: they must never reach a client card."""
import asyncio
import json
import os
os.environ["ENV_FILE"] = os.devnull
import tempfile
import unittest
from unittest import mock

import httpx

import pg
import core
import db
import facts
import fetch_channel_web
import fetch_myhome
import imgutil
import ingest

AGENCY = """#Sololaki 🚇 #LibertySquare
📍9 Ivane Machabeli Street

⭐️ 2-Bed apartment featuring a spacious living room with a retro vibe and a piano, plus a bright master bedroom, located on a quiet street just a 5-minute walk from Freedom Square and the metro station of the same name. Pets allowed. Available for move-in from October 18th! ⭐️

❗️#Exclusive

🏢 #2Bed #Apartment for #Rent
✨ #HistoricalBuilding | #Mixed
🏠 68 sq.m | 3/3 Floor | #ElectricalHeating | #Shower

✅ #Balcony  ✅ #Terrace  ✅ #Courtyard
✅ #WiFi  ✅ #TV
✅ #Stove  ✅ #Oven  ✅ #Conditioner

✖️ Elevator  ✖️ ParkingPlace

👬Tenants: 1-3
🐕Pets: #Allowed
🕐 #6Month #12Month

💰 1000$ + Deposit 1000$
0% Commission
#Price900to1200

📲 @David_Tibelashvili |
+995 599 20 67 16 #Michel

📍APARTMENTS ON MAP📍"""

AGENCY2 = """#Vake 🚇 #Rustaveli
📍14 Mtskheta Street

⭐️ 2bed Apartment in new Building near Round Garden Vake ⭐️

🏢 #2Bed #Apartment for #Rent
✨ #NewBuilding | #Mixed
🏠 120 sq.m | 7/9 Floor | #CentralHeating | #Bath | #2Bathroom

✅ #Balcony  ✅ #Terrace  ✅ #Courtyard
✅ #Dishwasher  ✅ #Stove
✅ #Microwave  ✅ #VacuumCleaner  ✅ #Elevator
✅ #Oven  ✅ #ParkingPlace +100$

👫Tenants: 1-3
🐕Pets: #NotAllowed
🕐 #12Month

💰 1000$ + Deposit 1000$
0% Commission"""

AGENCY_GEL = """#Saburtalo 🚇 #Delisi
📍3 Ivane Javakhishvili Street
🏢 #1Bed #Apartment for #Rent
✨ #OldBuilding | #White
🏠 45 Sq.m | 2/5 Floor | #GasHeating | #Shower
🐕 Pets: #ByAgreement (small)
💰 1620 Gel + Deposit 1620 Gel
0% Commission"""

FLATS = """✨ Сдается красивая квартира в элитном охраняемом доме в самом центре Вера!
ID: 25119 (EL0066)

Район: #Вера
Ⓜ️ #Руставели
📍 Иване Кереселидзе 17

Цена: $2000  | #от1601до2000

Новостройка | 75 м² | 2/5 этаж
#2комнаты | 1 спальня
Кондиционер | Балкон

🐾 Животные: ✖️
🕐 Срок аренды: 3 месяцев+"""

PROPERTY = """Район: Ваке
Улица: Шрошис 2
Площадь: 205м²
Этаж: 9/9
Количество спален: 4

-пентхаус
-можно с животными депозит 1750$

Договор на год

Цена: 3500$/месяц
Писать: @TamunaMt"""

KVARTIRY = """#Сабуртало ул.Данелия 57
ID 11021

Комнат: #3к
Спален: 2
Площадь: 100m²
Этаж: 7/10

Цена: 1000$

- Новый ремонт
- Метро Гос.Университет"""

MAPS = ("https://www.google.com/maps/place/11+Ivane+Machabeli+St,+T%27bilisi/@41.6906784,44.7994317,16z/data=!4m6!3m5!1s0x40:0x7f"
        "!8m2!3d41.6906784!4d44.7994317!16s%2Fg%2F11h0qfrt0")


class Parsers(unittest.TestCase):
    def test_agency_post(self):
        d = facts.parse_agency(AGENCY)
        self.assertEqual((d["district_ru"], d["district_en"], d["metro_ru"], d["metro_en"]),
                         ("Сололаки", "Sololaki", "Площадь Свободы", "Liberty Square"))
        self.assertEqual(d["address_en"], "9 Ivane Machabeli Street")
        self.assertTrue(d["headline_en"].startswith("2-Bed apartment featuring") and "⭐" not in d["headline_en"])
        self.assertEqual((d["exclusive"], d["property"], d["bedrooms"], d["building"], d["condition"]),
                         (True, "apartment", 2, "historical", "mixed"))
        self.assertEqual((d["area"], d["floor"], d["floors"], d["heating"], d["bath"]), (68, 3, 3, "electric", "shower"))
        self.assertEqual(d["yes"], ["balcony", "terrace", "courtyard", "wifi", "tv", "stove", "oven", "conditioner"])
        self.assertEqual(d["no"], ["elevator", "parking"])
        self.assertEqual((d["tenants"], d["pets"], d["terms"]), ("1-3", "allowed", [6, 12]))
        self.assertEqual((d["price_usd"], d["deposit_usd"], d["commission"]), (1000, 1000, "0%"))

    def test_agency_variants(self):
        d = facts.parse_agency(AGENCY2)
        self.assertEqual((d["building"], d["heating"], d["bath"], d["bathrooms"], d["pets"], d["terms"]),
                         ("new", "central", "bath", 2, "not_allowed", [12]))
        self.assertIn("parking", d["yes"])  # "✅ #ParkingPlace +100$"
        self.assertNotIn("exclusive", d)
        g = facts.parse_agency(AGENCY_GEL)
        self.assertEqual((g["price_usd"], g["deposit_usd"], g["condition"], g["heating"], g["pets"]),
                         (600, 600, "white_frame", "gas", "by_agreement"))  # lari / 2.7

    def test_competitor_posts(self):
        f = facts.parse_ru(FLATS)
        self.assertEqual((f["metro_ru"], f["building"], f["pets"], f["terms"]), ("Руставели", "new", "not_allowed", [3]))
        p = facts.parse_ru(PROPERTY)
        self.assertEqual((p["pets"], p["terms"], p["deposit_usd"]), ("by_agreement", [12], 1750))
        self.assertEqual(facts.parse_ru(KVARTIRY)["metro_ru"], "Госуниверситет")
        # through the normalizer: agency-format parser only for posts in that format
        it = ingest.from_tg({"id": 5, "channel": "x", "url": "u", "photos": [], "text": FLATS})
        self.assertEqual(it["parsed"]["metro_en"], "Rustaveli")
        self.assertNotIn("yes", it["parsed"])

    def test_agency_normalizer_exact_pin(self):
        it = ingest.from_channel({"id": 7, "url": "https://t.me/rent_tbilisi_ge/7", "photos": [], "text": AGENCY,
                                  "links": [MAPS, "https://t.me/David_Tibelashvili"]})
        self.assertEqual((it["price_usd"], it["area"], it["floor"], it["bedrooms"]), (1000, 68, 3, 2))
        self.assertEqual(it["point"], ((41.6906784, 44.7994317), "точный"))
        self.assertEqual(facts.maps_point(["https://www.google.com/maps?q=41.7221103,44.7469748&entry=gps"]), (41.7221103, 44.7469748))
        self.assertIsNone(facts.maps_point(["https://www.google.com/maps/@48.85,2.35,12z"]))  # not Tbilisi

    def test_ss_fields(self):
        item = {"applicationId": 1, "totalAmountOfFloor": 17.0, "furniture": True, "videoLink": "https://youtu.be/abc",
                "nearbySubwayStations": [{"stationTitle": "isani", "timeDistance": 20}, {"stationTitle": "samgori", "timeDistance": 10}]}
        det = {"locationLatitude": 41.6826, "locationLongitude": 44.8566, "isManualPin": True, "realEstateStatus": "New building",
               "state": "Newly renovated", "toilet": "1", "isPetFriendly": False, "airConditioning": True, "balcony": True,
               "elevator": True, "fridge": True, "internet": True, "tv": True, "washingMachine": True, "garage": False}
        d, point = facts.ss_facts(item, det)
        self.assertEqual((d["metro_ru"], d["floors"], d["building"], d["condition"], d["bathrooms"]),
                         ("Самгори", 17, "new", "new", 1))
        self.assertEqual(set(d["yes"]), {"conditioner", "balcony", "elevator", "fridge", "wifi", "tv", "washing_machine", "furniture"})
        self.assertNotIn("pets", d)  # an unticked box is "unknown", not "no"
        self.assertEqual(d["video_url"], "https://youtu.be/abc")
        self.assertEqual(point, ((41.6826, 44.8566), "точный"))
        d, point = facts.ss_facts({**item, "videoLink": "https://static.ss.ge/v/1.mp4", "nearbySubwayStations": []}, {})
        self.assertEqual(d["video_file"], "https://static.ss.ge/v/1.mp4")  # a file: downloaded, never shown as a link
        self.assertIsNone(point)

    def test_myhome_fields(self):
        item = {"id": 2, "total_floors": 15, "3d_url": "https://my.matterport.com/show/?m=x", "address": "Kvatchadze Street 19"}
        det = {"lat": 41.72, "lng": 44.73, "balconies": 2, "rent_period": 12, "youtube_link": "https://www.youtube.com/watch?v=1",
               "parameters": [{"key": k} for k in ("internet", "tv", "conditioner", "refrigerator", "washing_machine", "pets-allowed", "oven")],
               "labels": {"Status": "New building", "Condition": "Newly renovated", "Bathrooms": "2", "Heating": "Central Heating",
                          "Parking": "Without parking"}}
        d, point = facts.mh_facts(item, det)
        self.assertEqual((d["floors"], d["building"], d["condition"], d["heating"], d["bathrooms"], d["pets"], d["terms"]),
                         (15, "new", "new", "central", 2, "allowed", [12]))
        self.assertEqual(set(d["yes"]), {"wifi", "tv", "conditioner", "fridge", "washing_machine", "oven", "balcony"})
        self.assertEqual(d["no"], ["parking"])
        self.assertEqual((d["video_url"], d["tour_url"]), ("https://www.youtube.com/watch?v=1", "https://my.matterport.com/show/?m=x"))
        self.assertEqual(point, ((41.72, 44.73), "точный"))
        html = ("<div><span>Status</span><span>New building</span><span>Heating</span><span>Central Heating</span>"
                "<script>Heating</script><span>Parking</span><span>Without parking</span></div>")
        self.assertEqual(fetch_myhome.labels(html), {"Status": "New building", "Heating": "Central Heating", "Parking": "Without parking"})

    def test_web_page_video_and_links(self):
        page = ('<div class="tgme_widget_message_wrap"><div data-post="ch/10">'
                '<a class="tgme_widget_message_photo_wrap" href="https://t.me/ch/8" style="background-image:url(\'https://cdn/p8.jpg\')"></a>'
                '<a class="tgme_widget_message_video_player not_supported" href="https://t.me/ch/9?single"><time>1:26</time>'
                '<div class="message_media_not_supported_label">Media is too big</div></a>'
                '<div class="tgme_widget_message_text js-message_text" dir="auto">#Vake<br/>📍<a href="https://www.google.com/maps/'
                'place/x/@41.7,44.75,16z/data=&amp;#33;3d41.71&amp;#33;4d44.76">1 Street</a></div><time datetime="2026-10-21T10:00:00+00:00">'
                '</div></div><div class="tgme_widget_message_wrap"><div data-post="ch/12">'
                '<a class="tgme_widget_message_photo_wrap" href="https://t.me/ch/11" style="background-image:url(\'https://cdn/p11.jpg\')"></a>'
                '<a class="tgme_widget_message_video_player" href="https://t.me/ch/12?single"><video src="https://cdn/v12.mp4" class="x"></video></a>'
                '<div class="tgme_widget_message_text js-message_text" dir="auto">text</div></div></div>')
        p1, p2 = fetch_channel_web.parse(page)
        self.assertEqual((p1["id"], p1["videos"], p2["videos"]), (10, [(9, None)], [(12, "https://cdn/v12.mp4")]))
        self.assertEqual(facts.maps_point(p1["links"]), (41.71, 44.76))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(fetch_channel_web, "OUT", d):
            os.makedirs(f"{d}/video")
            self.assertIsNone(fetch_channel_web.video_of(None, "ch", "competitor", p1, {}))  # too big, nothing local
            open(f"{d}/video/tg_ch_9.mp4", "wb").write(b"x")  # same name as the Telethon fetch / the seed
            old = {10: {"video": {"path": f"{d}/video/tg_ch_9.mp4", "w": 1280, "h": 720, "duration": 86}}}
            self.assertEqual(fetch_channel_web.video_of(None, "ch", "competitor", p1, old)["w"], 1280)
            json.dump({"w": 852, "h": 480, "duration": 86}, open(f"{d}/video/tg_ch_9.json", "w"))  # Telethon's sidecar wins
            self.assertEqual(fetch_channel_web.video_of(None, "ch", "competitor", p1, {}),
                             {"w": 852, "h": 480, "duration": 86, "path": f"{d}/video/tg_ch_9.mp4"})

    def test_video_download_cap(self):
        body = b"0" * 5000
        tr = httpx.MockTransport(lambda r: httpx.Response(200, content=body))
        with tempfile.TemporaryDirectory() as d, httpx.Client(transport=tr) as c:
            self.assertIsNone(imgutil.download_video("https://x/v.mp4", f"{d}/big.mp4", c, cap=4000))
            self.assertFalse(os.listdir(d))  # no partial file left
            self.assertEqual(imgutil.download_video("https://x/v.mp4", f"{d}/ok.mp4", c, cap=6000), f"{d}/ok.mp4")
            self.assertEqual(os.path.getsize(f"{d}/ok.mp4"), 5000)


def listing(**kw):
    d = facts.parse_agency(AGENCY)
    base = {"id": "tg1", "source": "agency", "district": "Сололаки", "price_usd": d.pop("price_usd"), "area": d.pop("area"),
            "floor": d.pop("floor"), "bedrooms": d.pop("bedrooms"), "photos": [], "urls": ["https://t.me/rent_tbilisi_ge/1"],
            "address": "9 Ivane Machabeli Street", "description_ru": "Описание. Звоните +995 599 20 67 16",
            "details": {**d, "headline_ru": "Ретро-гостиная с пианино, 5 минут до площади Свободы @David_Tibelashvili",
                        "address_ru": "ул. Ивана Мачабели, 9"}}
    return {**base, **kw}


LEAKS = ("+995", "599", "@", "http", "t.me", "ss.ge", "myhome", "#")


class Cards(unittest.TestCase):
    def test_ru_card_like_the_channel(self):
        text = core.card(listing(), "Сололаки, можно с собакой", "~0.6 км от площади Свободы", "ru")
        for want in ("🚇 Сололаки · Площадь Свободы", "📍 ул. Ивана Мачабели, 9", "⭐️ Ретро-гостиная", "❗️ Эксклюзив",
                     "🏢 Квартира · 2 спальни", "✨ Исторический дом · смешанный стиль",
                     "🏠 68 м² · 3/3 этаж · электрическое отопление · душ", "✅ Балкон", "✖️ Лифт", "✖️ Парковка",
                     "👬 Жильцов: 1–3", "🐕 Животные: можно", "🕐 Срок: 6 / 12 мес.", "💰 $1000/мес + депозит $1000",
                     "0% комиссии", "🧭 ~0.6 км от площади Свободы", "💡 Сололаки, можно с собакой"):
            self.assertIn(want, text)
        for leak in LEAKS:
            self.assertNotIn(leak, text)
        self.assertNotIn("Описание", text)  # the headline replaces the long description
        self.assertLess(len(text), 4096)

    def test_en_card(self):
        text = core.card(listing(), "Old Tbilisi, pets allowed", None, "en")
        for want in ("🚇 Sololaki · Liberty Square", "📍 9 Ivane Machabeli Street", "⭐️ 2-Bed apartment", "❗️ Exclusive",
                     "🏢 Apartment · 2 bedrooms", "✨ Historical building · mixed style", "68 sq.m · 3/3 floor · electric heating",
                     "✅ Air conditioner", "✖️ Elevator", "🐕 Pets: allowed", "🕐 Term: 6 / 12 months", "💰 $1000/mo + deposit $1000",
                     "0% commission"):
            self.assertIn(want, text)
        self.assertFalse(any("а" <= ch <= "я" for ch in text.lower()), text)  # no Cyrillic left in an English card
        for leak in LEAKS:
            self.assertNotIn(leak, text)

    def test_sparse_listing_only_known_lines(self):
        i = {"id": "fb1", "source": "fb:g", "district": "Ваке", "price_usd": 700, "area": None, "floor": None, "bedrooms": None,
             "photos": [], "description_ru": "Светлая квартира у парка, пишите @owner или 599123456", "details": {}}
        text = core.card(i, "", None, "ru")
        self.assertEqual(text, "🏙 Ваке\n\nСветлая квартира у парка, пишите или\n\n🏢 Квартира\n\n💰 $700/мес")
        # another source's commission is not the agency's terms
        self.assertNotIn("комисс", core.card({**listing(), "source": "ss.ge"}, "", None, "ru"))

    def test_media_buttons_hide_source_hosts(self):
        i = listing()
        i["details"].update(video_url="https://youtu.be/x", tour_url="https://www.myhome.ge/3d/1")
        rows = core.card_keyboard(i, "ru", base="")["inline_keyboard"]
        self.assertEqual(rows[-1], [{"text": "🎥 Видео", "url": "https://youtu.be/x"}])  # no 3D button on myhome's domain
        i["details"]["tour_url"] = "https://my.matterport.com/show/?m=1"
        self.assertEqual(core.card_keyboard(i, "en", base="")["inline_keyboard"][-1][1], {"text": "🧊 3D tour", "url": "https://my.matterport.com/show/?m=1"})
        self.assertIsNone(facts.safe_link("http://youtu.be/x"))  # https only

    def test_agent_cards_have_facts_and_source(self):
        i = listing(lat=41.69, lng=44.79, geo_precision="точный", dup_sources=["ss.ge"])
        i["details"]["videos"] = [{"path": "raw/video/tg_2.mp4"}]
        text = core.review_text({**i, "status_reason": "x"})
        for want in ("🚇 Сололаки · Площадь Свободы", "✖️ Лифт", "Адрес в источнике: 9 Ivane Machabeli Street",
                     "📍 Точка: точный", "🎬 видео в альбоме (1)", "Источник: agency (+ дубли: ss.ge)", "https://t.me/rent_tbilisi_ge/1"):
            self.assertIn(want, text)
        self.assertIn("🚇 Сололаки", core.review_text({**i, "status_reason": "x"}))


class Albums(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [os.path.join(self.tmp.name, f"{n}.jpg") for n in range(6)]
        for p in self.p:
            open(p, "wb").write(b"jpg")
        self.v = os.path.join(self.tmp.name, "v.mp4")
        open(self.v, "wb").write(b"0" * 3000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_photos_then_one_video_under_cap(self):
        i = {"photos": self.p + ["missing.jpg"], "details": {"videos": [{"path": self.v, "w": 1280, "h": 720, "duration": 86},
                                                                        {"path": self.v}]}}
        a = core.album(i)
        self.assertEqual([x["type"] for x in a], ["photo"] * 4 + ["video"])
        self.assertEqual(a[-1]["w"], 1280)
        with mock.patch.dict(os.environ, {"VIDEO_MAX_MB": "0.001"}):  # 1 KB cap: the 3 KB video is left out
            self.assertEqual([x["type"] for x in core.album(i)], ["photo"] * 4)
        self.assertEqual(len(core.album({"photos": self.p, "details": {}}, photos=3)), 3)

    def test_bot_sends_one_album_and_reuses_the_video_upload(self):
        import bot
        from telegram import InputMediaVideo
        with tempfile.TemporaryDirectory():
            pg.reset()
            i = {"photos": self.p[:2], "details": {"videos": [{"path": self.v, "w": 640, "h": 360, "duration": 10}]}}
            sent = []

            class Bot:
                async def send_media_group(self, chat, media):
                    sent.append(media)
                    return [mock.Mock(video=None)] * 2 + [mock.Mock(video=mock.Mock(file_id="VID1"))]

            asyncio.run(bot.send_album(Bot(), 1, i))
            asyncio.run(bot.send_album(Bot(), 1, i))
            first, second = sent
            self.assertEqual(len(first), 3)
            self.assertIsInstance(first[2], InputMediaVideo)
            self.assertTrue(first[2].supports_streaming)
            self.assertEqual((first[2].width, first[2].height), (640, 360))
            self.assertEqual(second[2].media, "VID1")  # second time by file_id, no 3 MB upload


class IndexCache(unittest.TestCase):
    def test_old_entries_get_only_the_facts_text_only(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ingest, "CACHE", f"{d}/cache.json"):
            json.dump({"a": {"id": "a", "district": "Ваке", "pets": "да", "looks": "x"}, "b": {"id": "b", "v": ingest.INDEX_V}},
                      open(ingest.CACHE, "w"))
            calls = []

            def ask(prompt, schema, images=()):
                calls.append((prompt, list(images), schema))
                ids = [json.loads(ln)["id"] for ln in prompt.splitlines() if ln.startswith("{")]
                none = {k: ([] if k in ("yes", "no", "terms") else None) for k in ingest.DETAILS}
                return {"listings": [{**none, "id": x, "pets": "нет", "metro_ru": "Руставели", "yes": ["balcony"]} for x in ids]}

            items = [{"id": x, "source": "ss.ge", "address": "", "price_usd": 1, "area": 1, "floor": 1, "bedrooms": 1,
                      "text": "t", "photos": ["p.jpg"]} for x in ("a", "b")]
            with mock.patch.object(ingest, "ask", ask):
                cache = ingest.index_all(items)
            self.assertEqual(len(calls), 1)
            prompt, images, schema = calls[0]
            self.assertEqual(images, [])  # text only
            self.assertNotIn("renovation", schema["properties"]["listings"]["items"]["properties"])
            self.assertIn('"id": "a"', prompt)
            self.assertNotIn('"id": "b"', prompt)
            self.assertEqual((cache["a"]["v"], cache["a"]["metro_ru"], cache["a"]["looks"], cache["a"]["pets"]),
                             (ingest.INDEX_V, "Руставели", "x", "да"))  # the cached profile stays, facts are added

    def test_parsed_facts_beat_the_model(self):
        it = {"id": "tg1", "source": "agency", "price_usd": None, "area": 68, "floor": 3, "bedrooms": None,
              "parsed": {"metro_ru": "Площадь Свободы", "yes": ["balcony"], "no": ["elevator"], "pets": "allowed", "price_usd": 1000,
                         "bedrooms": 2, "district_ru": "Сололаки"}}
        p = {"id": "tg1", "district": "Мтацминда", "bedrooms": None, "pets": "нет", "metro_ru": "Руставели",
             "yes": ["elevator", "tv"], "no": [], "headline_ru": "Коротко", "v": 2, **{k: None for k in ("building", "floors")}}
        ingest.apply_profile(it, p)
        d = it["details"]
        self.assertEqual((it["district"], it["pets"], it["price_usd"], it["bedrooms"]), ("Сололаки", "да", 1000, 2))
        self.assertEqual((d["metro_ru"], d["pets"], d["headline_ru"]), ("Площадь Свободы", "allowed", "Коротко"))
        self.assertEqual((d["yes"], d["no"]), (["tv", "balcony"], ["elevator"]))  # the post's "✖️ Elevator" beats the model
        self.assertNotIn("building", d)


class Column(unittest.TestCase):
    def test_details_json_synced(self):
        with tempfile.TemporaryDirectory() as d:
            pg.reset()
            listings = os.path.join(d, "l.json")
            json.dump([{"id": "tg1", "source": "agency", "urls": [], "photos": [], "details": {"metro_ru": "Руставели"}},
                       {"id": "ss2", "source": "ss.ge", "urls": [], "photos": []}], open(listings, "w"))
            db.sync(listings, os.path.join(d, "none.json"))
            self.assertEqual(core.listing("tg1")["details"], {"metro_ru": "Руставели"})
            self.assertEqual(core.listing("ss2")["details"], {})


if __name__ == "__main__":
    unittest.main()
