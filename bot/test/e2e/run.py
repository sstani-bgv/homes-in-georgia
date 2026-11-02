"""End-to-end test of every user path in docs/TZ.md: the real bot.py against a local fake Telegram Bot API,
the real admin.py in headless Chrome, the Mini App with a mocked Telegram.WebApp, sync rules and the Sheets mirror.

Run from the project root:   .venv/bin/python test/e2e/run.py            # real Codex for matching (~11 calls)
                             .venv/bin/python test/e2e/run.py --fake-llm # deterministic stand-in, no Codex
Everything lives in /tmp/e2e (DB copy, work dir, logs); screenshots go to proof/e2e/ (agents' Mini App: proof/miniapp/),
results to proof/e2e/results.json.
Never touches data/app.db, .env, or the real Telegram API."""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
W = Path("/tmp/e2e")
WORK, BIN, FILES, SHOTS_TMP = W / "work", W / "bin", W / "files", W / "html"
APP_DB = W / "app.db"
PROOF = ROOT / "proof" / "e2e"
MINI = ROOT / "proof" / "miniapp"
PY = sys.executable
FAKE_LLM = "--fake-llm" in sys.argv
AGENT_CODE = "e2e-code-7731"            # test-only code, not the production one
BOT_TOKEN = "123456:E2E-FAKE-TOKEN"      # fake: the bot and the admin only ever talk to the fake Bot API
APP_URL = "https://agents.e2e.test"      # AGENT_APP_URL as Telegram sees it; the test opens the same paths on the local admin
ADMIN_PASSWORD = "e2e-admin-pass"
CODEX_LOG = W / "codex_calls.jsonl"

# ---------- isolated environment (before importing project modules: db.PATH and geo cache read at import) ----------
if W.exists():
    shutil.rmtree(W)
for d in (WORK / "raw", BIN, FILES, SHOTS_TMP):
    d.mkdir(parents=True)
for d in (PROOF, MINI):  # fresh screenshots; other files there (an index.html gallery) stay
    d.mkdir(parents=True, exist_ok=True)
    for old in list(d.glob("*.png")) + list(d.glob("results.json")):
        old.unlink()
for name in ("listings.json", "sources.json"):
    shutil.copy(ROOT / name, WORK / name)
(WORK / "raw" / "img").symlink_to(ROOT / "raw" / "img")  # listings store relative photo paths; read-only
if (ROOT / "raw" / "video").exists():
    (WORK / "raw" / "video").symlink_to(ROOT / "raw" / "video")  # listing videos (details.videos), read-only
for name in ("dropped_agents.json", "geo_cache.json"):
    if (ROOT / "raw" / name).exists():
        shutil.copy(ROOT / "raw" / name, WORK / "raw" / name)
(BIN / "codex").write_text(f'#!/bin/sh\nexec "{PY}" "{ROOT}/test/e2e/codex_shim.py" "$@"\n')
(BIN / "codex").chmod(0o755)
for k in ("GOOGLE_SA_JSON", "SHEET_ID", "BOT_TOKEN", "ADMIN_PASSWORD", "ADMIN_TG_IDS", "AGENT_APP_URL", "TG_API_BASE"):
    os.environ.pop(k, None)
os.environ.update(APP_DB=str(APP_DB), E2E_CODEX="fake" if FAKE_LLM else "real", E2E_CODEX_LOG=str(CODEX_LOG),
                  PATH=f"{BIN}:/opt/homebrew/bin:/usr/local/bin:{os.environ['PATH']}")
os.chdir(WORK)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test" / "e2e"))

import core  # noqa: E402
import db  # noqa: E402
import sheets  # noqa: E402
from fake_tg import FakeTelegram  # noqa: E402
from geo import km  # noqa: E402
from render import Shooter, chat_png, table_png  # noqa: E402

# ---------- actors ----------
CLIENT_RU = {"id": 1001, "name": "Анна", "username": "anna_e2e", "lang": "ru"}
CLIENT_PHOTO = {"id": 1002, "name": "Игорь", "lang": "ru"}
CLIENT_MAP = {"id": 1003, "name": "Мария", "username": "maria_e2e", "lang": "ru"}
CLIENT_EN = {"id": 1004, "name": "John Smith", "username": "john_e2e", "lang": "en"}
AGENT_A = {"id": 5001, "name": "Давид", "username": "agent_a_e2e", "lang": "ru"}
AGENT_B = {"id": 5002, "name": "Нино", "username": "agent_b_e2e", "lang": "ru"}
MEMBER_C = {"id": 5003, "name": "Гиорги", "username": "member_c_e2e", "lang": "ru"}
AGENT_D = {"id": 5004, "name": "Дмитрий", "username": "agent_d_e2e", "lang": "ru"}
STRANGER = {"id": 6001, "name": "Посторонний", "username": "stranger_e2e", "lang": "ru"}
GROUP = {"id": -1001234567890, "title": "Homes in Georgia · агенты"}
BOT_TITLE, BOT_SUB = "Homes in Georgia", "ИИ-подборщик квартир · бот (e2e, фейковый Bot API)"

f = FakeTelegram(str(FILES))
st = {}          # values handed from one scenario to the next
results = []     # {"n", "name", "ok", "checks", "shots", "error", "secs"}
shooter = None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------- processes ----------

class Proc:
    def __init__(self, args, env, log):
        self.args, self.env, self.log, self.p = args, env, log, None

    def start(self):
        self.p = subprocess.Popen(self.args, cwd=WORK, env={**os.environ, **self.env}, stdout=open(self.log, "a"),
                                  stderr=subprocess.STDOUT)
        return self.p.pid

    def stop(self):
        if self.p and self.p.poll() is None:
            self.p.send_signal(signal.SIGINT)
            try:
                self.p.wait(30)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()


TG_PORT = f.serve()
BOT = Proc([PY, str(ROOT / "test/e2e/run_bot.py")],
           {"TG_API_BASE": f"http://127.0.0.1:{TG_PORT}", "BOT_TOKEN": BOT_TOKEN, "AGENT_CODE": AGENT_CODE, "AGENT_APP_URL": APP_URL},
           W / "bot.log")


def start_admin(log="admin.log"):
    """The real admin.py on a free port, with the fake bot token and the fake Bot API (for its edits of Telegram cards)."""
    port = free_port()
    adm = Proc([PY, str(ROOT / "admin.py")], {"ADMIN_PASSWORD": ADMIN_PASSWORD, "ADMIN_PORT": str(port), "BOT_TOKEN": BOT_TOKEN,
                                             "TG_API_BASE": f"http://127.0.0.1:{TG_PORT}", "AGENT_APP_URL": APP_URL,
                                             "ADMIN_TG_IDS": ""}, W / log)
    adm.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return adm, f"http://127.0.0.1:{port}"


def overflow(pg):
    """Horizontal overflow of the page in px (0 = nothing sticks out past the viewport)."""
    return pg.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth")


def start_bot():
    polls = f.polls
    pid = BOT.start()
    end = time.time() + 60
    while f.polls <= polls + 1:  # second poll: initialization (getMe, deleteWebhook) is over
        if BOT.p.poll() is not None or time.time() > end:
            raise RuntimeError("bot did not start, see /tmp/e2e/bot.log")
        time.sleep(0.2)
    return pid


# ---------- scenario plumbing ----------

class Scenario:
    def __init__(self, n, name):
        self.n, self.name, self.checks, self.shots = n, name, [], []

    def check(self, ok, desc, detail=None, info=False):
        """info=True: recorded for the report (model-dependent quality), never fails the scenario."""
        self.checks.append((desc, None if info else bool(ok), detail if detail is not None else ("" if ok else "FAILED")))
        print(f"   {'i ' if info else '✓ ' if ok else '✗ '}{desc}" + (f"  [{detail}]" if detail not in (None, "") else ""), flush=True)
        return ok

    def chat(self, chat, slug, note=None, since=0, until=None, title=BOT_TITLE, sub=BOT_SUB):
        png = str(PROOF / f"{self.n:02d}{slug}.png")
        chat_png(shooter, f.log, chat, png, title, sub, note, since, until)
        self.shots.append(png)

    def table(self, slug, title, rows=None, note=None):
        png = str(PROOF / f"{self.n:02d}{slug}.png")
        table_png(shooter, png, title, f"Сценарий {self.n}", rows if rows is not None else self.checks, note)
        self.shots.append(png)


def run(n, name, fn):
    s = Scenario(n, name)
    print(f"\n=== {n}. {name}", flush=True)
    t0, err = time.time(), None
    try:
        fn(s)
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        s.checks.append(("сценарий выполнился без исключений", False, err.strip().splitlines()[-1]))
    ok = err is None and all(c[1] is not False for c in s.checks)
    results.append({"n": n, "name": name, "ok": ok, "secs": round(time.time() - t0), "error": err,
                    "checks": s.checks, "shots": [os.path.relpath(p, ROOT) for p in s.shots]})
    json.dump(results, open(PROOF / "results.json", "w"), ensure_ascii=False, indent=1)


def sent(chat, since, method="sendMessage"):
    return f.calls(chat=chat, method=method, since=since)


def wait_text(chat, since, contains, timeout=60):
    return f.wait(lambda calls: next((c for c in calls if c.get("chat") == chat and c["method"] == "sendMessage"
                                      and contains in c["text"]), None), timeout, since, f"«{contains}» in chat {chat}")


def wait_method(method, pred=lambda c: True, since=0, timeout=60, what=None):
    return f.wait(lambda calls: next((c for c in calls if c["method"] == method and pred(c)), None), timeout, since, what or method)


def buttons(c):
    return [b for row in f.markup(c) for b in row]


def card_ids(cards):
    return [next(b["callback_data"].split(":", 1)[1] for b in buttons(c) if b.get("callback_data", "").startswith("like:")) for c in cards]


def wait_search(chat, since, lang="ru", timeout=420):
    """Wait for a finished search in a client chat -> {"cards", "albums", "found", "tips", "texts"}."""
    err = core.t(lang, "error")

    def done(calls):
        msgs = [c for c in calls if c.get("chat") == chat and c["method"] == "sendMessage"]
        if any(c["text"] == err for c in msgs):
            raise AssertionError("bot answered with the error text (search failed), see /tmp/e2e/bot.log")
        return next((c for c in msgs if any(b.get("callback_data") == "more" for b in buttons(c))
                     or "😔" in c["text"]), None)
    tips = f.wait(done, timeout, since, f"search results in chat {chat}")
    msgs = [c for c in sent(chat, since) if c["seq"] <= tips["seq"]]
    cards = [c for c in msgs if any(b.get("callback_data", "").startswith("like:") for b in buttons(c))]
    albums = [c for c in sent(chat, since, "sendMediaGroup") if c["seq"] <= tips["seq"]]
    found = next((c for c in msgs if c["text"].startswith(("Понял:", "Got it:"))), None)
    return {"cards": cards, "albums": albums, "found": found, "tips": tips, "texts": [c["text"] for c in msgs], "msgs": msgs}


def press(user, msg, text, group=None):
    f.press(user, msg, text, group)


def toast(since, text, timeout=30):
    return wait_method("answerCallbackQuery", lambda c: c.get("text") == text, since, timeout, f"toast «{text}»")


def codex_calls():
    return [json.loads(x) for x in open(CODEX_LOG)] if CODEX_LOG.exists() else []


# client must never see where a listing came from (TZ 3)
COMPETITORS = ["cresco", "rent expert", "tbilisi_rent_expert", "flats_in_georgia", "flats in georgia", "propertyintbilisi",
               "property in tbilisi", "kvartiry_tbili", "tbilisi_apartments", "blondestate", "rent in tbilisi", "rent_tbilisi",
               "kiraoba", "binebisgakiraveba", "snimu_sdayu", "snyat_kvartiru", "tbilisi_obyavleniya", "rental_apartments"]
LEAK_RE = [(r"https?://", "ссылка"), (r"t\.me/", "t.me"), (r"www\.", "www"), (r"@\w{3,}", "@handle"),
           (r"\bss\.ge\b|\bss ge\b", "ss.ge"), (r"myhome", "myhome"), (r"facebook|\bfb\b", "facebook"), (r"instagram", "instagram"),
           (r"\b(tg|fb):\w", "тег источника")]


def leaks(text):
    out = [name for rx, name in LEAK_RE if re.search(rx, text or "", re.I)]
    out += [c for c in COMPETITORS if c in (text or "").lower()]
    for m in re.finditer(r"\+?\d[\d\s\-().]{7,}\d", text or ""):
        if len(re.sub(r"\D", "", m[0])) >= 9:
            out.append("телефон " + m[0])
    return out


def client_visible(chat, since=0):
    """Every piece of text the bot sent to a client chat: messages, buttons, keyboard, toasts to that client, media captions."""
    out = []
    for c in f.calls(chat=chat, since=since):
        out += [c.get("text") or "", c.get("caption") or ""] + [b.get("text", "") for b in buttons(c)]
        rm = c.get("reply_markup") or {}
        out += [b["text"] for r in rm.get("keyboard", []) for b in r]
        out += [m.get("caption") or "" for m in c.get("media") or []]
    presses = {x["cq"] for x in f.log if x["dir"] == "in" and x["kind"] == "press" and x["chat"] == chat}
    out += [c.get("text") or "" for c in f.calls(method="answerCallbackQuery", since=since) if c.get("callback_query_id") in presses]
    return [t for t in out if t]


def check_no_leaks(s, chat, since=0):
    bad = [(t[:80], leaks(t)) for t in client_visible(chat, since) if leaks(t)]
    s.check(not bad, "клиенту не ушло ни одной ссылки, телефона, @-ника, сайта или названия конкурента", bad[:3] or
            f"проверено сообщений: {len(client_visible(chat, since))}")


def check_cards(s, res, n=5, lang="ru"):
    cards = res["cards"]
    s.check(len(cards) == n, f"пришло {n} карточек", len(cards))
    month = "/мес" if lang == "ru" else "/mo"
    s.check(all("💰 $" in c["text"] and month in c["text"] and c["text"].startswith(("🚇 ", "🏙 ")) and "\n💡 " in c["text"]
                and "🏢 " in c["text"] for c in cards),
            "карточка в формате канала: район (· метро), тип и спальни, цена, «почему подходит»")
    s.check(all(re.search(r"^📍 ", c["text"], re.M) for c in cards if (core.listing(card_ids([c])[0])["details"] or {}).get(
        "address_ru" if lang == "ru" else "address_en")), "адрес (📍 улица и дом) в карточке, если он известен", info=True)
    # every card is preceded by its album (photos, then at most one video; no captions)
    seqs = sorted([("a", a["seq"]) for a in res["albums"]] + [("c", c["seq"]) for c in cards], key=lambda x: x[1])
    with_photos = sum(1 for k, (kind, _) in enumerate(seqs) if kind == "c" and k and seqs[k - 1][0] == "a")
    s.check(with_photos == len(cards), "перед каждой карточкой альбом с фото", f"{with_photos}/{len(cards)}")
    s.check(all(1 <= len(a["media"]) <= 10 and sum(m["type"] == "photo" for m in a["media"]) <= 4
                and sum(m["type"] == "video" for m in a["media"]) <= 1 and all(m.get("file") or m["media"].startswith("fid")
                                                                            for m in a["media"])
                and not any(m.get("caption") for m in a["media"]) for a in res["albums"]),
            "альбомы: до 4 фото + до 1 видео (файлом или по file_id), без подписей")
    vids = [m for a in res["albums"] for m in a["media"] if m["type"] == "video"]
    s.check(True, f"видео в альбомах: {len(vids)}", [m.get("supports_streaming") for m in vids], info=True)
    import ingest
    hs = [{"hashes": [ingest.dhash(m["file"]) for m in a["media"] if m["type"] == "photo"]} for a in res["albums"]]
    dups = [(k, j) for k in range(len(hs)) for j in range(k + 1, len(hs)) if ingest.shared_photos(hs[k], hs[j]) >= 2]
    s.check(not dups, "на странице нет двух карточек с одними и теми же фото (дубль)", dups, info=True)
    ids = card_ids(cards)
    s.check(all(core.listing(i)["status"] == "published" for i in ids), "все показанные объявления опубликованы (status=published)")
    check_map_buttons(s, cards, ids, lang)
    return ids


def check_map_buttons(s, cards, ids, lang="ru"):
    """"📍 На карте" under a card iff the listing has coordinates: web_app → our /map with the listing's point."""
    bad = []
    for c, lid in zip(cards, ids):
        i = core.listing(lid)
        web = [b for b in buttons(c) if b.get("web_app")]
        want = i.get("lat") is not None and f"{APP_URL}/map?pin={i['lat']:.6f}%2C{i['lng']:.6f}"
        if (bool(web) != bool(want)) or (web and (web[0]["text"] != core.t(lang, "on_map") or not web[0]["web_app"]["url"].startswith(want))):
            bad.append(lid)
    s.check(not bad, "под карточкой с координатами кнопка «📍 На карте» (web_app → /map?pin=…), без координат её нет", bad)


def price(card):
    return int(re.search(r"💰 \$(\d+)", card["text"])[1])


# =====================================================================================================================

def s15_miniapp(s):
    pg = shooter.browser.new_page(viewport={"width": 430, "height": 820}, device_scale_factor=2)
    pg.route("https://telegram.org/js/telegram-web-app.js",
             lambda r: r.fulfill(body=(ROOT / "test/e2e/telegram_webapp_mock.js").read_text(), content_type="application/javascript"))
    vake = {"lat": "41.7106", "lon": "44.7547", "display_name": "Vake Park"}
    pg.route(re.compile(r"https://nominatim\.openstreetmap\.org/.*"), lambda r: r.fulfill(json=[vake]))
    pg.goto(f"file://{ROOT}/webapp/index.html")
    pg.wait_for_function("window.L && document.querySelectorAll('.radius button').length === 4")
    s.check(pg.locator(".brand").inner_text().startswith("HOMES IN GEORGIA") or "Homes in Georgia" in pg.locator(".brand").inner_text(),
            "шапка под брендом Homes in Georgia", pg.locator(".brand").inner_text())
    s.check(not pg.evaluate("window.__mb.visible"), "до выбора точки кнопка «Искать» скрыта")
    pg.mouse.click(215, 450)
    s.check(pg.locator(".pin").count() == 1 and pg.evaluate("window.__mb.visible"), "клик по карте ставит точку и показывает кнопку")
    pg.fill("#q", "Ваке парк")
    pg.click("#search button")
    pg.wait_for_function("document.getElementById('hint').textContent.includes('Ваке парк')")
    c = pg.evaluate("(() => { const m = window.__lastMarker(); return m && [m.lat, m.lng]; })()")
    s.check(c and abs(c[0] - 41.7106) < 1e-4 and abs(c[1] - 44.7547) < 1e-4, "поиск места (Nominatim) переносит точку", c)
    try:
        pg.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    pg.wait_for_timeout(800)
    p1 = str(PROOF / "15a_miniapp_poisk.png")
    pg.screenshot(path=p1)
    s.shots.append(p1)
    box = pg.locator(".pin").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    pg.mouse.move(x, y)
    pg.mouse.down()
    for k in range(1, 11):
        pg.mouse.move(x + 9 * k, y + 5 * k)
    pg.mouse.up()
    pg.wait_for_timeout(300)
    moved = pg.evaluate("(() => { const m = window.__lastMarker(); return [m.lat, m.lng]; })()")
    s.check(abs(moved[1] - 44.7547) > 1e-4, "точку можно перетащить", moved)
    s.check(pg.locator("#hint").inner_text() == "Точку можно перетащить", "после перетаскивания подпись места сбрасывается")
    pg.get_by_role("button", name="3 км").click()
    s.check(pg.locator(".radius button.on").inner_text().lower() == "3 км", "выбран радиус 3 км")
    pg.click("#__mb")
    pay = json.loads(pg.evaluate("window.__sent[0]"))
    s.check(set(pay) == {"lat", "lng", "radius", "label"} and pay["radius"] == 3 and pay["label"] is None
            and 41.6 < pay["lat"] < 41.85 and 44.6 < pay["lng"] < 45.05 and abs(pay["lng"] - moved[1]) < 1e-9,
            "sendData: {lat, lng, radius, label} — координаты перетащенной точки, radius 3", pay)
    p2 = str(PROOF / "15b_miniapp_peretashchili_3km.png")
    pg.screenshot(path=p2)
    s.shots.append(p2)
    pg.fill("#q", "Ваке парк")
    pg.click("#search button")
    pg.wait_for_function("document.getElementById('hint').textContent.includes('Ваке парк')")
    pg.click("#__mb")
    pay2 = json.loads(pg.evaluate("window.__sent[1]"))
    s.check(pay2["label"] == "Ваке парк" and pay2["radius"] == 3 and abs(pay2["lat"] - 41.7106) < 1e-6,
            "sendData после поиска места: с названием места", pay2)
    st["map_payload"] = pay2
    pg.close()


def s8_agents(s):
    m = f.mark()
    f.send_text(STRANGER, "/review")
    s.check(wait_text(STRANGER["id"], m, "Только для агентов"), "не агент: /review отклонён")
    f.send_text(AGENT_A, "/agent wrong-code")
    s.check(wait_text(AGENT_A["id"], m, "Неверный код"), "неверный код: «Неверный код.»")
    s.check(core.agent(AGENT_A["id"]) is None, "с неверным кодом агент не создан")
    s.check(not f.calls(method="setChatMenuButton", since=m), "постороннему и до регистрации кнопка меню не ставится")
    f.send_text(AGENT_A, f"/agent {AGENT_CODE}")
    reg = wait_text(AGENT_A["id"], m, "✅ Вы агент")
    s.check(reg, "/agent <код>: «✅ Вы агент»")
    web = [b for b in buttons(reg) if b.get("web_app")]
    s.check(web and web[0]["text"] == "📋 Панель агента" and web[0]["web_app"]["url"] == APP_URL + "/tg"
            and "/moderate_all" not in reg["text"] and "/review" not in reg["text"],
            "после регистрации: кнопка «📋 Панель агента» (Mini App), старые команды больше не рекламируются", web)
    menu = wait_method("setChatMenuButton", lambda c: c.get("chat") == AGENT_A["id"], m)
    s.check(menu["menu_button"] == {"type": "web_app", "text": "Панель", "web_app": {"url": APP_URL + "/tg"}},
            "агенту поставлена кнопка меню чата «Панель» (setChatMenuButton с chat_id, без BotFather)", menu["menu_button"])
    f.send_text(AGENT_B, f"/agent {AGENT_CODE}")
    wait_text(AGENT_B["id"], m, "✅ Вы агент")
    a, b = core.agent(AGENT_A["id"]), core.agent(AGENT_B["id"])
    s.check(a and b and a["username"] == AGENT_A["username"] and a["name"] == AGENT_A["name"] and a["active"],
            "агенты A и B в базе: tg_id, имя, username, активны", (a and a["id"], b and b["id"]))
    s.check(set(core.staff_chats()) == {AGENT_A["id"], AGENT_B["id"]}, "лиды пойдут обоим агентам в личку", core.staff_chats())
    st["agent_a_id"], st["agent_b_id"] = a["id"], b["id"]
    s.chat(AGENT_A["id"], "a_agent_registraciya", "Агент Давид: неверный код, затем /agent <код>.", since=m)
    s.chat(STRANGER["id"], "b_ne_agent", "Посторонний пользователь пробует команду агента.", since=m)


def s1_ru_client(s):
    u, chat = CLIENT_RU, CLIENT_RU["id"]
    m = f.mark()
    f.send_text(u, "/start")
    g = wait_text(chat, m, "Homes in Georgia")
    s.check(g["text"].startswith("Привет! Я ИИ-подборщик квартир агентства Homes in Georgia"), "приветствие по-русски под брендом Homes in Georgia")
    kb = (g.get("reply_markup") or {}).get("keyboard") or []
    web = [b for r in kb for b in r if b.get("web_app")]
    s.check(web and web[0]["web_app"]["url"].startswith("https://") and any(b.get("request_location") for r in kb for b in r),
            "клавиатура: кнопка карты (Mini App) и «Где я сейчас»", web and web[0]["web_app"]["url"])
    req = "1-2 спальни до $800, Ваке или Сабуртало, можно с собакой"
    st["ru_request"] = req
    m2 = f.mark()
    f.send_text(u, req)
    res = wait_search(chat, m2)
    s.check(core.t("ru", "searching") in res["texts"], "сначала «Смотрю варианты…»")
    s.check(res["found"] and "Подходит" in res["found"]["text"], "итог: «Понял: … Подходит N из M»", res["found"] and res["found"]["text"][:160])
    ids = check_cards(s, res)
    s.check(all(price(c) <= 880 for c in res["cards"]), "цены в пределах бюджета (+10%)", [price(c) for c in res["cards"]])
    ds = [core.listing(i)["district"] for i in ids]
    s.check(sum(d in ("Ваке", "Сабуртало") for d in ds) >= 3, "районы совпадают с запросом (качество модели)", ds, info=True)
    s.check(any(b.get("callback_data") == "more" for b in buttons(res["tips"])), "после карточек кнопка «🔄 Ещё варианты»")
    check_no_leaks(s, chat, m)
    st["ru_page1"], st["ru_cards1"], st["ru_tips1"] = ids, res["cards"], res["tips"]
    s.chat(chat, "_klient_ru_zapros", "Клиент Анна: /start → текстовый запрос → 5 карточек (фото + описание, без ссылок и источника).", since=m)


def s4_more(s):
    u, chat = CLIENT_RU, CLIENT_RU["id"]
    m = f.mark()
    press(u, st["ru_tips1"], "🔄 Ещё варианты")
    res = wait_search(chat, m)
    ids = check_cards(s, res)
    s.check(not set(ids) & set(st["ru_page1"]), "следующие 5 без повторов", ids)
    s.check(len(set(ids)) == len(ids), "внутри страницы без повторов")
    m2 = f.mark()
    press(u, res["cards"][0], "👎 Не то")
    toast(m2, "Учту ваш вкус 👌")
    e = wait_method("editMessageReplyMarkup", lambda c: c["chat"] == chat and c["message_id"] == res["cards"][0]["message_id"], m2)
    s.check(not e.get("reply_markup") or not f.markup(e), "после 👎 кнопки под карточкой убраны")
    with db.tx() as c:
        r = c.execute("SELECT reaction FROM reactions WHERE client_id = (SELECT id FROM clients WHERE external_id = ?) AND listing_id = ?",
                      (str(chat), ids[0])).fetchone()
    s.check(r and r[0] == "dislike", "дизлайк записан в reactions", ids[0])
    cl = core.client("tg", chat)
    s.check(set(cl["state"]["shown"]) == set(st["ru_page1"]) | set(ids), "показанные id хранятся в состоянии клиента (БД)", len(cl["state"]["shown"]))
    check_no_leaks(s, chat, m)
    st["ru_page2"], st["ru_cards2"], st["ru_tips2"] = ids, res["cards"], res["tips"]
    s.chat(chat, "_eshche_i_ne_to", "«🔄 Ещё варианты» → следующие 5; «👎 Не то» под первой карточкой.", since=m)


def lead_msgs(since, lead_id=None):
    return [c for c in f.calls(method="sendMessage", since=since) if "Лид #" in c["text"]
            and (lead_id is None or f"Лид #{lead_id}</b>" in c["text"])]


def s5_like(s):
    u, chat = CLIENT_RU, CLIENT_RU["id"]
    card = st["ru_cards1"][0]
    lid = card_ids([card])[0]
    m = f.mark()
    press(u, card, "❤️ Нравится")
    s.check(toast(m, "Отлично!"), "клиенту всплывает «Отлично!»")
    e = wait_method("editMessageReplyMarkup", lambda c: c["chat"] == chat and c["message_id"] == card["message_id"], m)
    s.check([b["text"] for b in buttons(e)] == ["✅ Передано агенту"] + (["📍 На карте"] if core.listing(lid)["lat"] is not None else []),
            "кнопка под карточкой → «✅ Передано агенту» (карта остаётся)")
    conf = wait_text(chat, m, "Передал агенту Homes in Georgia")
    s.check(conf, "подтверждение клиенту: «Передал агенту Homes in Georgia 🙌 …»")
    f.wait(lambda calls: len(lead_msgs(m)) >= 2, 30, m, "lead cards")
    cards = lead_msgs(m)
    s.check({c["chat"] for c in cards} == {AGENT_A["id"], AGENT_B["id"]}, "карточка лида пришла обоим агентам", [c["chat"] for c in cards])
    i = core.listing(lid)
    url = i["urls"][0]
    c0 = cards[0]
    s.check(c0.get("parse_mode") == "HTML" and url.replace("&", "&amp;") in c0["text"], "в карточке лида ссылка на источник", url)
    s.check(f"Источник: {i['source']}" in c0["text"] and "@anna_e2e" in c0["text"] and st["ru_request"] in c0["text"],
            "в карточке: источник, контакт клиента, его запрос")
    pin = next((b for b in buttons(c0) if b["text"] == "📍 На карте"), None)
    s.check(i["lat"] is None or (pin and f"pin={i['lat']:.6f}%2C{i['lng']:.6f}" in pin.get("web_app", {}).get("url", "")
                                 and f"📍 Точка: {i['geo_precision']}" in c0["text"]),
            "в карточке лида точность точки и кнопка «📍 На карте» (наша карта, точный пин, web_app)", pin)
    s.check([b["text"] for b in buttons(c0)] == ["🛠 В работе", "📅 Просмотр назначен", "🏁 Сдана", "✖️ Отказ"]
            + (["📍 На карте"] if i["lat"] is not None else []) + ["📋 Открыть в панели"],
            "кнопки статусов лида + «📍 На карте» + «📋 Открыть в панели»")
    pan = buttons(c0)[-1]
    s.check(pan.get("web_app", {}).get("url") == f"{APP_URL}/tg?lead={c0['text'].split('Лид #')[1].split('<')[0]}",
            "«Открыть в панели» — web_app-кнопка, открывает эту заявку в Mini App", pan.get("web_app"))
    with db.tx() as c:
        ld = c.execute("SELECT * FROM leads WHERE listing_id = ?", (lid,)).fetchone()
        ev = c.execute("SELECT status FROM lead_events WHERE lead_id = ?", (ld["id"],)).fetchall()
        msgs = c.execute("SELECT COUNT(*) FROM messages WHERE kind = 'lead' AND ref = ?", (str(ld["id"]),)).fetchone()[0]
        like = c.execute("SELECT reaction FROM reactions WHERE listing_id = ? AND client_id = ?", (lid, ld["client_id"])).fetchone()[0]
    s.check(ld["status"] == "new" and ld["agent_id"] is None and [r[0] for r in ev] == ["new"], "лид в БД: new, без агента, история [new]", ld["id"])
    s.check(like == "like" and msgs == 2, "лайк записан; обе копии карточки запомнены (messages)", msgs)
    check_no_leaks(s, chat, m)
    st["lead1"], st["lead1_cards"] = ld["id"], {c["chat"]: c for c in cards}
    s.chat(chat, "a_like_klient", "Клиент нажал «❤️ Нравится».", since=m)
    s.chat(AGENT_A["id"], "b_like_agent", "Та же заявка у агента: со ссылкой на источник и кнопками статусов.", since=m)


def s2_photo(s):
    u, chat = CLIENT_PHOTO, CLIENT_PHOTO["id"]
    m = f.mark()
    f.send_text(u, "/start")
    wait_text(chat, m, "Homes in Georgia")
    ref = next(p for i in json.loads((ROOT / "listings.json").read_text()) if i["source"] == "agency"
               and "современ" in (i.get("style") or "") and i.get("light") == "светлая" for p in i["photos"][:1] if os.path.exists(p))
    st["photo_ref"] = ref
    m2 = f.mark()
    before = len(codex_calls())
    f.send_photo(u, ref)
    time.sleep(0.8)
    f.send_text(u, "Хочу квартиру в таком стиле, 1-2 спальни, до $1200")
    res = wait_search(chat, m2)
    s.check(res["texts"].count(core.t("ru", "searching")) == 1, "фото + текст подряд = один поиск (debounce)")
    calls = codex_calls()[before:]
    top = [c for c in calls if c["kind"] == "understood"]
    imgs = top[-1]["images"] if top else []
    s.check(imgs and Path(imgs[0]).name.startswith(f"{chat}_") and Path(WORK, imgs[0]).exists(),
            "фото клиента скачано через getFile, сконвертировано в JPEG и передано модели", imgs)
    s.check(imgs and open(Path(WORK, imgs[0]), "rb").read(3) == b"\xff\xd8\xff", "файл — JPEG")
    cl = core.client("tg", chat)
    s.check(len(cl["state"]["images"]) == 1 and len(cl["state"]["msgs"]) == 1, "в состоянии клиента 1 фото и 1 текст")
    check_cards(s, res)
    check_no_leaks(s, chat, m)
    s.chat(chat, "_foto_zapros", "Клиент присылает фото понравившегося интерьера (из raw/img) и текст.", since=m)


def s3_map(s):
    u, chat = CLIENT_MAP, CLIENT_MAP["id"]
    pay = st["map_payload"]
    m = f.mark()
    f.send_text(u, "/start")
    wait_text(chat, m, "Homes in Georgia")
    before = codex_calls()
    m2 = f.mark()
    f.send_web_app_data(u, pay)
    r = wait_text(chat, m2, "Ищу в радиусе")
    s.check(r["text"] == f"📍 Ищу в радиусе 3 км от {pay['label']}", "бот подтвердил точку и радиус", r["text"])
    res = wait_search(chat, m2)
    ids = check_cards(s, res)
    after = codex_calls()[len(before):]
    s.check(not [c for c in after if c["kind"] == "place"], "точка с карты главнее текста: распознавание места не вызывалось")
    pin = (pay["lat"], pay["lng"])
    ok, det = True, []
    for c, i in zip(res["cards"], ids):
        mm = re.search(r"🧭 ~([\d.]+) км от (.+)", c["text"])
        it = core.listing(i)
        real = km(pin, (it["lat"], it["lng"]))
        det.append(f"{i}: {mm and mm[1]} км")
        ok &= bool(mm) and abs(float(mm[1]) - real) < 0.06 and mm[2] == pay["label"]
    s.check(ok, "в каждой карточке «🧭 ~X км от Ваке парк», расстояние верное", det)
    near = sum(float(re.search(r"🧭 ~([\d.]+)", c["text"])[1]) <= 4.5 for c in res["cards"])
    s.check(near == len(res["cards"]), "все варианты в пределах радиуса ×1.5", near)
    cl = core.client("tg", chat)
    s.check(cl["state"]["pin"] == [pay["lat"], pay["lng"]] and cl["state"]["pin_radius"] == 3, "точка сохранена в состоянии клиента")
    check_no_leaks(s, chat, m)
    st["map_cards"] = res["cards"]
    s.chat(chat, "_karta", "Клиент выбрал место в Mini App (payload из сценария 15) → квартиры рядом с расстоянием.", since=m)


def s6_english(s):
    u, chat = CLIENT_EN, CLIENT_EN["id"]
    m = f.mark()
    f.send_text(u, "/start")
    g = wait_text(chat, m, "Homes in Georgia")
    s.check(g["text"].startswith("Hi! I'm the AI apartment finder of Homes in Georgia"), "приветствие по-английски (language_code=en)")
    kb = [b["text"] for r in g["reply_markup"]["keyboard"] for b in r]
    s.check(kb == ["🗺 Pick a place on the map", "📍 Where I am now"], "кнопки клавиатуры по-английски", kb)
    m2 = f.mark()
    f.send_text(u, "1-2 bedrooms up to $900 in Vake or Saburtalo, pets are ok")
    res = wait_search(chat, m2, "en")
    s.check(core.t("en", "searching") in res["texts"] and res["found"] and res["found"]["text"].startswith("Got it:"),
            "тексты бота по-английски", res["found"] and res["found"]["text"][:150])
    ids = check_cards(s, res, lang="en")
    cyr = [(c["text"][:60], re.findall(r"[А-Яа-яЁё]+", c["text"])[:5]) for c in res["cards"] if re.search(r"[А-Яа-яЁё]", c["text"])]
    s.check(not cyr, "в английских карточках нет кириллицы (район, описание, единицы)", cyr[:3])
    s.check([b["text"] for b in buttons(res["cards"][0])][:2] == ["❤️ I like it", "👎 Not this"]
            and all(b["text"] == "📍 On the map" for b in buttons(res["cards"][0]) if b.get("web_app")), "кнопки по-английски")
    en = lambda i, k: core.listing(i).get(k) or core.listing(i)["details"].get(k)
    s.check(all((en(i, "description_en") or en(i, "headline_en")) and en(i, "district_en") for i in ids),
            "английский текст карточки есть в БД (headline_en из индексации или перевод description_en, district_en)")
    s.check(all(price(c) <= 990 for c in res["cards"]), "цены в пределах бюджета", [price(c) for c in res["cards"]])
    s.check(core.client("tg", chat)["lang"] == "en", "язык клиента в БД: en")
    check_no_leaks(s, chat, m)
    st["en_cards"] = res["cards"]
    s.chat(chat, "_english", "Англоязычный клиент: тексты и описания по-английски.", since=m, sub="AI apartment finder · bot (e2e)")


def s7_restart(s):
    chat = CLIENT_RU["id"]
    before = core.client("tg", chat)["state"]
    old_pid = BOT.p.pid
    m = f.mark()
    BOT.stop()
    f.note(chat, "🔄 Процесс бота остановлен и запущен заново")
    for a in (AGENT_A, AGENT_B):
        f.note(a["id"], "🔄 Процесс бота перезапущен: фоновая рассылка объявлений на проверку")
    new_pid = start_bot()
    s.check(new_pid != old_pid and BOT.p.poll() is None, "бот перезапущен (новый процесс)", f"{old_pid} → {new_pid}")
    s.check(core.client("tg", chat)["state"] == before, "состояние клиента после рестарта то же (хранится в БД)", f"показано {len(before['shown'])}")
    press(CLIENT_RU, st["ru_tips2"], "🔄 Ещё варианты")
    res = wait_search(chat, m)
    ids = check_cards(s, res)
    s.check(not set(ids) & (set(st["ru_page1"]) | set(st["ru_page2"])), "«Ещё варианты» после рестарта: без повторов 10 прежних", ids)
    s.check(len(core.client("tg", chat)["state"]["shown"]) == 15, "показано всего 15 (5+5+5)")
    # a like right after the restart works too: lead #2 from the map client
    m2 = f.mark()
    card = st["map_cards"][0]
    press(CLIENT_MAP, card, "❤️ Нравится")
    f.wait(lambda calls: len(lead_msgs(m2)) >= 2, 30, m2, "lead 2 cards")
    with db.tx() as c:
        ld = c.execute("SELECT id FROM leads WHERE listing_id = ? AND client_id = (SELECT id FROM clients WHERE external_id = ?)",
                       (card_ids([card])[0], str(CLIENT_MAP["id"]))).fetchone()
    s.check(ld, "«❤️ Нравится» после рестарта создаёт лид", ld and ld[0])
    st["lead2"], st["lead2_cards"] = ld[0], {c["chat"]: c for c in lead_msgs(m2)}
    # the restarted bot pushes pending listings to agents right away (review loop)
    rv = f.wait(lambda calls: [c for c in calls if c["method"] == "sendMessage" and c["chat"] == AGENT_A["id"]
                               and "Проверка объявления" in c["text"]], 60, m, "review cards")
    time.sleep(2)
    rv = [c for c in f.calls(chat=AGENT_A["id"], method="sendMessage", since=m) if "Проверка объявления" in c["text"]]
    rb = [c for c in f.calls(chat=AGENT_B["id"], method="sendMessage", since=m) if "Проверка объявления" in c["text"]]
    batch = int(db.get_setting("review_batch"))
    s.check(len(rv) == len(rb) == batch, f"после старта бот разослал {batch} объявлений на проверку обоим агентам", (len(rv), len(rb)))
    rid = [re.search(r"Проверка объявления</b> (\S+)", c["text"])[1] for c in rv]
    s.check(all(core.listing(i)["review_sent_at"] for i in rid), "review_sent_at проставлен (повторно не шлются)")
    st["review_a"], st["review_b"] = rv, rb
    s.chat(chat, "_restart_klient", "Рестарт процесса бота между шагами: «Ещё варианты» продолжает с того же места.", since=m - 1)


def s9_lead_status(s):
    A, B = st["lead1_cards"][AGENT_A["id"]], st["lead1_cards"][AGENT_B["id"]]
    lead = st["lead1"]
    m = f.mark()

    def step(user, card, btn, label, agent_tag):
        mm = f.mark()
        press(user, card, btn)
        toast(mm, btn)
        edits = f.wait(lambda calls: (lambda e: e if len(e) >= 2 else None)(
            [c for c in calls if c["method"] == "editMessageText" and f"Лид #{lead}</b>" in c["text"]]), 30, mm, "lead edits")
        s.check({e["chat"] for e in edits} == {AGENT_A["id"], AGENT_B["id"]} and all(label in e["text"] and agent_tag in e["text"] for e in edits),
                f"«{btn}» ({user['name']}): карточка обновилась у обоих агентов: {label} · {agent_tag}")

    step(AGENT_A, A, "🛠 В работе", "🛠 В работе", "@agent_a_e2e")
    step(AGENT_B, B, "📅 Просмотр назначен", "📅 Просмотр назначен", "@agent_a_e2e")
    step(AGENT_A, A, "🏁 Сдана", "🏁 Сдана", "@agent_a_e2e")
    with db.tx() as c:
        ld = c.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
        ev = [(r["status"], r["agent_id"]) for r in c.execute("SELECT * FROM lead_events WHERE lead_id = ? ORDER BY id", (lead,))]
    a, b = st["agent_a_id"], st["agent_b_id"]
    s.check(ld["status"] == "rented" and ld["agent_id"] == a, "лид 1: сдана, закреплён за первым нажавшим (A)")
    s.check(ev == [("new", None), ("in_work", a), ("viewing", b), ("rented", a)], "история lead_events: new → in_work(A) → viewing(B) → rented(A)", ev)
    mm = f.mark()
    press(STRANGER, A, "✖️ Отказ")
    s.check(toast(mm, "Только для агентов"), "посторонний не может менять статус")
    s.check(core.lead(lead)["status"] == "rented", "статус не изменился")
    L2B = st["lead2_cards"][AGENT_B["id"]]
    mm = f.mark()
    press(AGENT_B, L2B, "✖️ Отказ")
    toast(mm, "✖️ Отказ")
    time.sleep(1)
    l2 = core.lead(st["lead2"])
    s.check(l2["status"] == "refused" and l2["agent_id"] == b, "лид 2: «✖️ Отказ» от B → отказ, закреплён за B")
    s.chat(AGENT_A["id"], "a_statusy_agent_a", "Агент A: карточки лидов меняются на месте при каждом нажатии (у всех агентов).", since=m)
    s.chat(AGENT_B["id"], "b_statusy_agent_b", "Агент B: та же карточка лида 1 обновляется; лид 2 → «Отказ».", since=m)


def s10_moderation(s):
    rv, rb = st["review_a"], st["review_b"]
    ids = [re.search(r"Проверка объявления</b> (\S+)", c["text"])[1] for c in rv]
    s.check(all(core.listing(i)["status"] == "pending_review" for i in ids), "карточки проверки — объявления pending_review", ids)
    first = rv[0]
    s.check([b["text"] for b in buttons(first)] == ["✅ Опубликовать", "❌ Отклонить"] and "Источник:" in first["text"] and "http" in first["text"],
            "карточка проверки: источник, ссылки, кнопки Опубликовать/Отклонить")
    with_geo = [i for i in ids if core.listing(i)["lat"] is not None]
    X = with_geo[0]
    Y = next(i for i in ids if i != X)
    pending_catalog = [c for c in codex_calls() if c["kind"] == "understood" and re.search(rf"^{re.escape(X)} \|", c["prompt"], re.M)]
    s.check(not pending_catalog, "до решения X не попадал в каталог подбора", X)
    m = f.mark()
    press(AGENT_A, rv[ids.index(X)], "✅ Опубликовать")
    toast(m, "Готово")
    yb = next(c for c in rb if f"</b> {Y}\n" in c["text"])
    press(AGENT_B, yb, "❌ Отклонить")
    edits = f.wait(lambda calls: (lambda e: e if len(e) >= 4 else None)([c for c in calls if c["method"] == "editMessageText"]), 30, m, "review edits")
    ex = [e for e in edits if f"</b> {X}\n" in e["text"]]
    s.check(len(ex) == 2 and all("✅ Опубликовано</b> · @agent_a_e2e" in e["text"] and not f.markup(e) for e in ex),
            "у обоих агентов карточка X: «✅ Опубликовано · @agent_a_e2e», кнопки убраны")
    ey = [e for e in edits if f"</b> {Y}\n" in e["text"]]
    s.check(len(ey) == 2 and all("❌ Отклонено</b> · @agent_b_e2e" in e["text"] for e in ey), "карточка Y: «❌ Отклонено · @agent_b_e2e»")
    x, y = core.listing(X), core.listing(Y)
    s.check(x["status"] == "published" and x["decision"] == "approved" and x["decided_by"] == st["agent_a_id"], "X: published, decision approved, decided_by A")
    s.check(y["status"] == "rejected" and y["decision"] == "rejected" and y["status_reason"] == "отклонено агентом", "Y: rejected, «отклонено агентом»")
    s.check(X in {i["id"] for i in core.published()} and Y not in {i["id"] for i in core.published()}, "X в пуле подбора, Y нет")
    st["X"], st["Y"] = X, Y
    # the approved listing now reaches client search: the map client asks for flats right at X
    chat = CLIENT_MAP["id"]
    before = len(codex_calls())
    m2 = f.mark()
    f.send_web_app_data(CLIENT_MAP, {"lat": x["lat"], "lng": x["lng"], "radius": 1, "label": None})
    res = wait_search(chat, m2)
    top = [c for c in codex_calls()[before:] if c["kind"] == "understood"]
    s.check(top and re.search(rf"^{re.escape(X)} \|", top[-1]["prompt"], re.M), "после одобрения X попал в каталог поиска клиента", X)
    s.check(X in card_ids(res["cards"]), "X показан клиенту (выбор модели)", card_ids(res["cards"]), info=True)
    s.check(r"📍 Ищу в радиусе 1 км от выбранной точки" in [c["text"] for c in sent(chat, m2)], "без названия места: «от выбранной точки»")
    s.check(not [c for c in codex_calls() if c["kind"] == "understood" and re.search(rf"^{re.escape(Y)} \|", c["prompt"], re.M)],
            "отклонённый Y ни разу не попадал в каталог подбора")
    check_no_leaks(s, chat, m2)
    s.chat(AGENT_A["id"], "a_proverka_agent_a", "Объявления на проверку в Telegram: агент A публикует X; решение B по Y видно и у A.",
           since=st["review_a"][0]["seq"] - 3)
    s.chat(chat, "b_odobrennoe_v_poiske", f"После одобрения: клиент ищет у точки объявления {X}.", since=m2)


def run_sync():
    r = subprocess.run([PY, str(ROOT / "db.py"), "sync"], cwd=WORK, capture_output=True, text=True, env=os.environ)
    assert r.returncode == 0, r.stderr
    return r.stdout


def status_count(prefix):
    with db.tx() as c:
        return dict(c.execute("SELECT status, COUNT(*) FROM listings WHERE source LIKE ? GROUP BY status", (prefix + "%",)).fetchall())


def s11_group(s):
    G, gid = GROUP, GROUP["id"]
    m = f.mark()
    f.send_text(AGENT_A, "/leads_here", group=G)
    s.check(wait_text(gid, m, "теперь приходят в эту группу"), "/leads_here в группе: подтверждение")
    s.check(db.get_setting("lead_target") == "group" and db.get_setting("group_chat_id") == str(gid), "настройки: lead_target=group, group_chat_id")
    m2 = f.mark()
    card = st["ru_cards2"][1]
    press(CLIENT_RU, card, "❤️ Нравится")
    lc = f.wait(lambda calls: lead_msgs(m2), 30, m2, "lead in group")
    time.sleep(1)
    lc = lead_msgs(m2)
    s.check([c["chat"] for c in lc] == [gid], "лид ушёл только в группу, не в личку агентам", [c["chat"] for c in lc])
    s.check(not [b for b in buttons(lc[0]) if b.get("web_app")], "в группе карточка без web_app-кнопки (Telegram разрешает их только в личке)")
    gl = core.listing(card_ids([card])[0])
    gpin = [b for b in buttons(lc[0]) if b["text"] == "📍 На карте"]
    s.check(gl["lat"] is None or (gpin and gpin[0].get("url", "").startswith(APP_URL + "/map?pin=")),
            "в группе «📍 На карте» — обычная URL-кнопка на нашу карту с точным пином", gpin)
    lead3 = int(re.search(r"Лид #(\d+)", lc[0]["text"])[1])
    s.check(core.agent(MEMBER_C["id"]) is None, "участник группы Гиорги пока не агент")
    m3 = f.mark()
    press(MEMBER_C, lc[0], "🛠 В работе", group=G)
    toast(m3, "🛠 В работе")
    c_agent = core.agent(MEMBER_C["id"])
    s.check(c_agent and core.lead(lead3)["agent_id"] == c_agent["id"], "в группе любой участник при нажатии становится агентом и берёт лид")
    f.send_text(AGENT_A, "/leads_private", group=G)
    s.check(wait_text(gid, m, "агентам в личку"), "/leads_private: лиды снова в личку")
    s.check(db.get_setting("lead_target") == "agents", "lead_target=agents")
    s.chat(gid, "a_gruppa", "Группа агентства: /leads_here → лид в группу → участник берёт его в работу → /leads_private.",
           since=m, title=GROUP["title"], sub="группа · бот Homes in Georgia AI")
    # moderate_all: every competitor listing waits for an agent after the next sync
    m4 = f.mark()
    before_tg = status_count("tg:")
    f.send_text(AGENT_A, "/moderate_all on")
    s.check(wait_text(AGENT_A["id"], m4, "Проверка всех объявлений конкурентов и FB: вкл"), "/moderate_all on")
    out = run_sync()
    after_tg, after_fb = status_count("tg:"), status_count("fb:")
    decided_tg = [i for i in (st["X"], st["Y"]) if core.listing(i)["source"].startswith("tg:")]
    s.check(after_tg.get("published", 0) <= len(decided_tg) and after_tg.get("pending_review", 0) > before_tg.get("pending_review", 0),
            "после sync все объявления tg-конкурентов на проверке (кроме уже решённых людьми)", f"до {before_tg} → после {after_tg}")
    s.check(after_fb.get("published", 0) == 0, "и все FB тоже", after_fb)
    f.note(AGENT_A["id"], "⚙️ refresh: python db.py sync — " + out.splitlines()[0])
    m5 = f.mark()
    f.send_text(AGENT_A, "/review")
    f.wait(lambda calls: len([c for c in calls if c["method"] == "sendMessage" and c["chat"] == AGENT_A["id"]
                              and "Проверка объявления" in c["text"]]) >= 10, 60, m5, "10 review cards")
    time.sleep(1)
    rv = [c for c in sent(AGENT_A["id"], m5) if "Проверка объявления" in c["text"]]
    s.check(len(rv) == 10, "/review прислал 10 объявлений на проверку", len(rv))
    s.check(any("проверка всех объявлений конкурентов" in c["text"] for c in rv), "среди них «проверка всех объявлений конкурентов и FB включена»")
    s.check(not [c for c in f.calls(chat=AGENT_B["id"], since=m5) if "Проверка объявления" in (c.get("text") or "")], "/review шлёт только в чат, где его вызвали")
    f.send_text(AGENT_A, "/moderate_all off")
    wait_text(AGENT_A["id"], m5, ": выкл")
    run_sync()
    restored = status_count("tg:")
    s.check(restored.get("published", 0) >= before_tg.get("published", 0) - 2, "/moderate_all off + sync: tg-конкуренты снова опубликованы", restored)
    s.check(core.listing(st["X"])["status"] == "published" and core.listing(st["Y"])["status"] == "rejected",
            "решения агентов по X и Y пережили две повторные синхронизации")
    s.chat(AGENT_A["id"], "b_moderate_all_review", "Агент A: /moderate_all on → sync → /review → /moderate_all off.", since=m4)


def s12_admin(s):
    adm, base = start_admin()
    pg = shooter.browser.new_page(viewport={"width": 1280, "height": 900})
    for _ in range(50):
        try:
            pg.goto(base + "/login")
            break
        except Exception:
            time.sleep(0.2)
    shot = lambda slug: (pg.screenshot(path=str(PROOF / f"12{slug}.png"), full_page=True), s.shots.append(str(PROOF / f"12{slug}.png")))
    try:
        pg.fill("input[name=password]", "wrong")
        with pg.expect_response(lambda r: r.url.endswith("/login") and r.request.method == "POST") as r:
            pg.click("button")
        s.check(r.value.status == 401 and "Неверный пароль" in pg.content(), "неверный пароль отклонён (401)")
        shot("a_login_neverno")
        pg.goto(base + "/")
        s.check(pg.url.endswith("/login"), "без входа — редирект на /login")
        pg.fill("input[name=password]", ADMIN_PASSWORD)
        pg.click("button")
        s.check(pg.locator("h1").inner_text().lower() == "объявления", "верный пароль: вход, страница «Объявления»")
        # listings with filters
        comp = "tg:" + next(k for k in ("Tbilisi_Rent_Expert", "flats_in_georgia", "tbilisi_apartments") if status_count("tg:" + k))
        with db.tx() as c:
            dist = c.execute("SELECT district FROM listings WHERE source = ? AND district IS NOT NULL GROUP BY district ORDER BY COUNT(*) DESC",
                             (comp,)).fetchone()[0]
            n = c.execute("SELECT COUNT(*) FROM listings WHERE source = ? AND district = ? AND price_usd BETWEEN 300 AND 1500",
                          (comp, dist)).fetchone()[0]
        pg.select_option("select[name=source]", comp)
        pg.select_option("select[name=district]", dist)
        pg.fill("input[name=min]", "300")
        pg.fill("input[name=max]", "1500")
        pg.get_by_role("button", name="Показать").click()
        s.check(f"Найдено: {n}" in pg.locator(".sub").inner_text(), f"фильтр {comp} + {dist} + $300–1500: найдено столько же, сколько в БД", n)
        rows = pg.locator("table tr").all()[1:]
        okr = all(comp in r.inner_text() and dist in r.inner_text() for r in rows)
        prices = [int(re.search(r"\$(\d+)", r.inner_text())[1]) for r in rows]
        s.check(okr and all(300 <= p <= 1500 for p in prices), "все строки: этот источник, район, цена в диапазоне", prices[:10])
        shot("b_obyavleniya_filtr")
        # review (shared with the bot)
        pg.goto(base + "/review")
        txt = pg.content()
        s.check(st["X"] not in txt and st["Y"] not in txt, "решённые в Telegram X и Y в очереди админки уже нет")
        q = core.pending(limit=50, unsent_only=False)
        s.check(f"В очереди: {len(q)}" in pg.locator(".sub").inner_text(), "очередь = pending_review из той же БД", len(q))
        z1, z2 = q[0]["id"], q[1]["id"]
        pg.locator(".review", has=pg.locator(f"input[name=id][value='{z1}']")).get_by_role("button", name="✓ Опубликовать").click()
        s.check(f"{z1}: опубликовано" in pg.locator(".flash").inner_text(), "Опубликовать → флеш")
        pg.locator(".review", has=pg.locator(f"input[name=id][value='{z2}']")).get_by_role("button", name="✕ Отклонить").click()
        a1, a2 = core.listing(z1), core.listing(z2)
        s.check(a1["status"] == "published" and a1["decision"] == "approved" and a1["decided_by"] is None, "Z1 опубликовано из админки (decided_by пусто)")
        s.check(a2["status"] == "rejected" and a2["decision"] == "rejected", "Z2 отклонено")
        s.check(z1 in {i["id"] for i in core.published()} and z2 not in {i["id"] for i in core.pending(100, False)},
                "решение админки сразу видно боту (подбор и очередь проверки)")
        st["Z1"], st["Z2"] = z1, z2
        shot("c_proverka")
        # leads
        pg.goto(base + "/leads")
        lead2 = st["lead2"]
        row = pg.locator("tr", has=pg.locator(f"input[name=id][value='{lead2}']"))
        row.locator("select[name=status]").select_option("viewing")
        row.get_by_role("button", name="Сохранить").click()
        s.check(f"Заявка #{lead2}: Просмотр назначен" in pg.locator(".flash").inner_text(), "смена статуса заявки → флеш")
        with db.tx() as c:
            ev = c.execute("SELECT status, agent_id FROM lead_events WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lead2,)).fetchone()
        l2 = core.lead(lead2)
        s.check(l2["status"] == "viewing" and l2["agent_id"] == st["agent_b_id"] and tuple(ev) == ("viewing", st["agent_b_id"]),
                "в БД: статус viewing, агент остался B, событие в истории (агент из формы)", tuple(ev))
        s.check("📅 Просмотр назначен" in core.lead_text(l2), "бот покажет новый статус в карточке лида (общая БД)")
        pg.locator("tr", has=pg.locator(f"input[name=id][value='{lead2}']")).locator("summary").click()
        shot("d_zayavki")
        # agents: add by @username, deactivate B
        pg.goto(base + "/agents")
        pg.fill("input[name=tg]", "@" + AGENT_D["username"])
        pg.fill("input[name=name]", AGENT_D["name"])
        pg.get_by_role("button", name="Добавить агента").click()
        with db.tx() as c:
            d = c.execute("SELECT * FROM agents WHERE username = ?", (AGENT_D["username"],)).fetchone()
        s.check(d and d["tg_id"] is None and d["active"], "агент по @username добавлен (tg_id пока пуст)")
        pg.locator("tr", has_text=AGENT_B["username"]).get_by_role("button", name="Отключить").click()
        s.check(core.agent(AGENT_B["id"]) is None, "агент B отключён")
        shot("e_agenty")
        # the agent added by username writes to the bot -> gets bound and starts receiving leads
        m = f.mark()
        f.send_text(AGENT_D, "/start")
        wait_text(AGENT_D["id"], m, "Homes in Georgia")
        time.sleep(0.5)
        with db.tx() as c:
            d = c.execute("SELECT * FROM agents WHERE username = ?", (AGENT_D["username"],)).fetchone()
        s.check(d["tg_id"] == AGENT_D["id"], "агент из админки написал боту (/start) → Telegram id привязан", d["tg_id"])
        m2 = f.mark()
        press(CLIENT_EN, st["en_cards"][0], "❤️ I like it")
        f.wait(lambda calls: lead_msgs(m2), 30, m2, "lead after admin changes")
        time.sleep(1)
        to = {c["chat"] for c in lead_msgs(m2)}
        s.check(AGENT_B["id"] not in to and AGENT_D["id"] in to and AGENT_A["id"] in to, "новый лид: A и D получили, отключённый B — нет", sorted(to))
        en_conf = wait_text(CLIENT_EN["id"], m2, "Sent to a Homes in Georgia agent")
        s.check(en_conf, "англоязычному клиенту подтверждение по-английски")
        st["lead_en_seq"] = m2
        # sources
        pg.goto(base + "/sources")
        for kind, link, name in (("tg_competitor", "https://t.me/new_rent_channel_e2e", "Новый канал"),
                                 ("fb_group", "https://www.facebook.com/groups/tbilisi_rent_e2e/", "FB группа e2e")):
            pg.select_option("select[name=kind]", kind)
            pg.fill("input[name=link]", link)
            pg.fill("input[name=name]", name)
            pg.get_by_role("button", name="Добавить").click()
            s.check("добавлен" in pg.locator(".flash").inner_text(), f"добавлен источник {link}")
        pg.fill("input[name=link]", "not a link")
        pg.select_option("select[name=kind]", "fb_group")
        pg.get_by_role("button", name="Добавить").click()
        s.check("Нужна ссылка" in pg.locator(".flash").inner_text(), "кривая ссылка FB отклоняется")
        pg.locator("tr", has_text="crescotbilisi").get_by_role("button", name="Выключить").click()
        pg.on("dialog", lambda d: d.accept())
        pg.locator("tr", has_text="propertyintbilisi").get_by_role("button", name="Удалить").click()
        s.check("удалён" in pg.locator(".flash").inner_text(), "источник удалён")
        shot("f_istochniki")
        exp = W / "export"
        exp.mkdir(exist_ok=True)
        r = subprocess.run([PY, str(ROOT / "db.py"), "export-sources"], cwd=exp, capture_output=True, text=True, env=os.environ)
        cfg = json.loads((exp / "sources.json").read_text())
        tg = [c["username"] for c in cfg["telegram_channels"]]
        fb = [g["key"] for g in cfg["facebook_groups"]]
        s.check(r.returncode == 0 and "new_rent_channel_e2e" in tg and "tbilisi_rent_e2e" in fb, "export-sources: новые канал и группа в sources.json", r.stdout.strip()[:80])
        s.check("crescotbilisi" not in tg and "propertyintbilisi" not in tg, "выключенный и удалённый источники в sources.json не попали")
        new = next(c for c in cfg["telegram_channels"] if c["username"] == "new_rent_channel_e2e")
        s.check(new["kind"] == "competitor" and new.get("want") == 40, "формат для сборщика: kind=competitor, want=40", new)
        # settings
        pg.goto(base + "/settings")
        pg.check("input[name=moderate_all]")
        pg.get_by_role("button", name="Сохранить").click()
        s.check(db.get_setting("moderate_all") == "1", "Настройки: галочка «проверять все» → moderate_all=1")
        shot("g_nastroyki")
        m3 = f.mark()
        f.send_text(AGENT_A, "/moderate_all")
        s.check(wait_text(AGENT_A["id"], m3, ": вкл"), "бот видит настройку из админки (/moderate_all → вкл)")
        pg.uncheck("input[name=moderate_all]")
        pg.get_by_role("button", name="Сохранить").click()
        s.check(db.get_setting("moderate_all") == "0", "снята обратно → 0")
        # stats
        pg.goto(base + "/stats")
        with db.tx() as c:
            nsearch = c.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
            nleads = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            nclients = c.execute("SELECT COUNT(DISTINCT client_id) FROM searches").fetchone()[0]
        stat = {c.locator(".label").inner_text().lower(): int(c.locator("b").inner_text()) for c in pg.locator(".stats").first.locator(".stat").all()}
        s.check(stat.get("запросов (поисков)") == nsearch and stat.get("заявок") == nleads and stat.get("клиентов искали") == nclients,
                "Статистика: поиски, клиенты и заявки из сценариев бота", stat)
        s.check(stat.get("сдано") == 1 and stat.get("отказов") == 1, "сдано 1, отказ 1 (сценарий 9)")
        shot("h_statistika")
        # layout: nothing sticks out past the right edge, header as wide as the content, on desktop and on a phone
        bad = []
        for w in (1280, 390):
            pg.set_viewport_size({"width": w, "height": 900})
            for path, name in admin_nav():
                pg.goto(base + path)
                ov = overflow(pg)
                hw = pg.evaluate("[document.querySelector('header').getBoundingClientRect().width, document.documentElement.scrollWidth]")
                if ov > 0 or hw[0] != hw[1]:
                    bad.append((w, path, ov, hw))
        s.check(not bad, "все страницы админки без горизонтального переполнения на 1280 и 390 px, шапка шириной с контент", bad)
        pg.set_viewport_size({"width": 1280, "height": 900})
        for path, slug in (("/sources", "istochniki"), ("/", "obyavleniya")):
            pg.goto(base + path)
            pg.wait_for_timeout(300)
            p = str(MINI / f"desktop_{slug}.png")
            pg.screenshot(path=p)
            s.shots.append(p)
            if path == "/sources":
                right = max(b["x"] + b["width"] for b in (x.bounding_box() for x in pg.locator("button", has_text="Удалить").all()))
                s.check(right <= 1280, "«Удалить» на «Источниках» внутри экрана (1280 px)", round(right))
    finally:
        pg.close()
        adm.stop()
    s.chat(AGENT_D["id"], "i_agent_iz_adminki", "Агент, добавленный в админке по @username: /start → привязка → получает лиды.", since=0)


def admin_nav():
    sys.path.insert(0, str(ROOT))
    import admin
    return admin.NAV + [("/more", "Ещё")]


def init_data(user, token=BOT_TOKEN):
    """Telegram.WebApp.initData for `user`, signed the way Telegram does it (here with the fake test token)."""
    import admin
    from urllib.parse import urlencode
    tu = {"id": user["id"], "first_name": user["name"], "language_code": "ru", **({"username": user["username"]} if user.get("username") else {})}
    fields = {"auth_date": str(int(time.time())), "query_id": "AAE2E", "user": json.dumps(tu, ensure_ascii=False, separators=(",", ":"))}
    return urlencode({**fields, "hash": admin.init_data_hash(fields, token)})


def miniapp_page(user, base):
    """A 390x844 phone page with the Telegram.WebApp stand-in carrying initData of `user`."""
    pg = shooter.browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg.add_init_script(f"window.__tgInitData = {json.dumps(init_data(user))};")
    pg.route("https://telegram.org/js/telegram-web-app.js",
             lambda r: r.fulfill(body=(ROOT / "test/e2e/telegram_webapp_mock.js").read_text(), content_type="application/javascript"))
    return pg


def s17_agent_app(s):
    adm, base = start_admin("admin_miniapp.log")

    def go(pg, sel):
        with pg.expect_navigation():
            pg.click(sel)

    def shot(pg, name, full=False):
        pg.wait_for_timeout(400)
        p = str(MINI / f"{name}.png")
        pg.screenshot(path=p, full_page=full)
        s.shots.append(p)
    try:
        # a stranger with a valid Telegram signature is not let in
        pg = miniapp_page(STRANGER, base)
        pg.goto(base + "/tg")
        pg.wait_for_function("document.getElementById('tgmsg').textContent.length > 0")
        s.check("только для агентов" in pg.locator("#tg").inner_text(), "не агент (подпись Telegram верная): «Панель только для агентов»")
        s.check(pg.request.get(base + "/leads", max_redirects=0).status == 303, "и без сессии: заявки не открываются")
        pg.close()
        # agent A opens the lead from the Telegram card: «📋 Открыть в панели»
        card = next(c for c in lead_msgs(st["lead_en_seq"]) if c["chat"] == AGENT_A["id"])
        lead = int(re.search(r"Лид #(\d+)", card["text"])[1])
        url = next(b["web_app"]["url"] for b in buttons(card) if b["text"] == "📋 Открыть в панели")
        pg = miniapp_page(AGENT_A, base)
        pg.goto(url.replace(APP_URL, base))
        pg.wait_for_url(re.compile(rf".*/lead/{lead}(\?.*)?$"))
        s.check(pg.locator("h1").inner_text().lower() == f"заявка #{lead}", "кнопка из карточки лида открыла эту заявку в Mini App (initData проверен сервером)", pg.url)
        s.check(pg.evaluate("Telegram.WebApp.BackButton.visible"), "на странице заявки показана нативная кнопка «Назад» Telegram")
        ld = core.lead(lead)
        page = pg.content()
        s.check(ld["listing"]["urls"][0].replace("&", "&amp;") in page and "Написать в Telegram" in page and ld["request"] in page,
                "в заявке: запрос клиента, «Написать в Telegram», ссылка на объявление", ld["request"][:60])
        s.check(pg.locator(".strip img").count() >= 1, "фото объявления", pg.locator(".strip img").count())
        s.check(overflow(pg) <= 0, "390 px: без горизонтального переполнения")
        shot(pg, "02_zayavka_statusy")
        m = f.mark()
        with pg.expect_navigation():
            pg.get_by_role("button", name="🛠 В работе").click()
        s.check(f"Заявка #{lead}: В работе" in pg.locator(".flash").inner_text(), "«🛠 В работе» в Mini App → флеш")
        ld = core.lead(lead)
        s.check(ld["status"] == "in_work" and ld["agent_id"] == st["agent_a_id"], "в БД: in_work, закреплена за агентом A (тот, кто вошёл)")
        edits = f.wait(lambda calls: (lambda x: x if len(x) >= 2 else None)(
            [c for c in calls if c["method"] == "editMessageText" and f"Лид #{lead}</b>" in c["text"]]), 30, m, "lead card edits")
        got = {c["chat"] for c in lead_msgs(st["lead_en_seq"])}
        s.check({e["chat"] for e in edits} == got and all("🛠 В работе · @agent_a_e2e" in e["text"] for e in edits),
                "все копии карточки этой заявки в Telegram обновились: «🛠 В работе · @agent_a_e2e»", sorted({e["chat"] for e in edits}))
        s.check(all(buttons(e)[-1].get("web_app") for e in edits), "…и кнопка «Открыть в панели» на месте")
        # tabs
        go(pg, "nav.tabs a[href='/leads']")
        s.check(pg.locator(f"a.lead[href='/lead/{lead}']").count() == 1 and "Мои и новые" in pg.locator(".chips a.on").inner_text(),
                "«Заявки»: по умолчанию мои и новые, карточками")
        mine = {int(x.split("/")[-1]) for x in pg.locator("a.lead").evaluate_all("els => els.map(e => e.getAttribute('href'))")}
        with db.tx() as c:
            expect = {r[0] for r in c.execute("SELECT id FROM leads WHERE agent_id = ? OR status = 'new'", (st["agent_a_id"],))}
        s.check(mine == expect, "в списке ровно заявки агента A и новые", sorted(mine))
        shot(pg, "01_zayavki")
        go(pg, "nav.tabs a[href='/review']")
        q = core.pending(limit=50, unsent_only=False)
        s.check(pg.locator(".review").count() == len(q), "«Проверка»: та же очередь pending_review", len(q))
        shot(pg, "03_proverka")
        z = q[0]["id"]
        with pg.expect_navigation():
            pg.locator(".review", has=pg.locator(f"input[name=id][value='{z}']")).get_by_role("button", name="✕ Отклонить").click()
        zz = core.listing(z)
        s.check(zz["status"] == "rejected" and zz["decided_by"] == st["agent_a_id"], "отклонено из Mini App: решение записано за агентом A", z)
        go(pg, "nav.tabs a[href='/']")
        s.check(pg.locator("table.list tr").count() > 1 and overflow(pg) <= 0, "«Объявления»: карточки вместо таблицы, без переполнения")
        shot(pg, "04_obyavleniya")
        go(pg, "nav.tabs a[href='/sources']")
        s.check(overflow(pg) <= 0 and pg.locator("button", has_text="Выключить").first.is_visible(), "«Источники»: кнопки на экране")
        shot(pg, "05_istochniki")
        go(pg, "nav.tabs a[href='/more']")
        go(pg, ".tiles a[href='/settings']")
        pg.check("input[name=moderate_all]")
        with pg.expect_navigation():
            pg.get_by_role("button", name="Сохранить").click()
        s.check(db.get_setting("moderate_all") == "1", "«Настройки» в Mini App: moderate_all=1 (вместо /moderate_all on)")
        shot(pg, "06_nastroyki")
        pg.uncheck("input[name=moderate_all]")
        with pg.expect_navigation():
            pg.get_by_role("button", name="Сохранить").click()
        s.check(db.get_setting("moderate_all") == "0", "…и обратно")
        go(pg, "nav.tabs a[href='/more']")
        go(pg, ".tiles a[href='/stats']")
        s.check(pg.locator(".stats .stat").count() >= 8, "«Статистика» открывается")
        shot(pg, "07_statistika")
        go(pg, "nav.tabs a[href='/more']")
        go(pg, ".tiles a[href='/agents']")
        shot(pg, "08_agenty")
        bad = []
        for path, name in admin_nav() + [(f"/lead/{lead}", "заявка")]:
            pg.goto(base + path)
            if overflow(pg) > 0:
                bad.append((path, overflow(pg)))
        s.check(not bad, "все разделы Mini App на 390 px без горизонтального переполнения", bad)
        pg.close()
        clients = {u["id"] for u in (CLIENT_RU, CLIENT_PHOTO, CLIENT_MAP, CLIENT_EN, STRANGER)}
        menus = f.calls(method="setChatMenuButton")
        s.check(not [c for c in menus if c["chat"] in clients], "клиентам кнопка меню агента не ставилась ни разу", len(menus))
        s.check({c["chat"] for c in menus if c["menu_button"].get("type") == "web_app"} >= {AGENT_A["id"], AGENT_B["id"]},
                "агентам A и B — кнопка «Панель»")
        m = f.mark()
        f.send_text(AGENT_B, "/start")  # B was disabled in the admin (scenario 12)
        off = wait_method("setChatMenuButton", lambda c: c["chat"] == AGENT_B["id"], m)
        s.check(off["menu_button"] == {"type": "default"}, "отключённый агент B пишет боту → кнопка меню возвращена к обычной", off["menu_button"])
    finally:
        adm.stop()


def item(id, source="ss.ge", reasons=(), **kw):
    base = {"id": id, "source": source, "owner_id": id, "urls": [f"https://example/{id}"], "price_usd": 800, "area": 60.0,
            "floor": 3, "bedrooms": 2, "address": "Vake st 1", "district": "Ваке", "latlng": [41.71, 44.76], "photos": [],
            "description_ru": f"Квартира {id}", "features": [], "agent_reasons": list(reasons), "agent_suspect": False, "text": "t"}
    return {**base, **kw}


def s13_sync(s):
    p = str(W / "sync_rules.db")
    db.init(p)
    L, D = W / "sync_listings.json", W / "sync_dropped.json"

    def sync(ok, dropped=(), full=True):
        L.write_text(json.dumps(ok))
        D.write_text(json.dumps(list(dropped)))
        return db.sync(str(L), str(D), full=full, path=p)

    def status(i):
        with db.tx(p) as c:
            return c.execute("SELECT status FROM listings WHERE id = ?", (i,)).fetchone()[0]
    good = [item(f"ss{n}") for n in range(6)] + [item("tg1", "agency"), item("tgx", "tg:comp", agent_suspect=True), item("fb1", "fb:g")]
    dropped = [item("ag1", reasons=["один телефон в 3+ объявлениях"]), item("ag2", "fb:g", reasons=["FB: один автор 2+ квартиры"]),
               item("dub1", reasons=["одинаковый текст у разных объявлений"]), item("ai1", "myhome.ge", agent_suspect=True, agent_reason="комиссия")]
    sync(good, dropped)
    s.check(status("ag1") == "rejected" and status("ag2") == "rejected", "точный сигнал агента (телефон 3+, FB-автор) → rejected")
    s.check(status("dub1") == "pending_review" and status("ai1") == "pending_review", "сомнительное (дубль текста, ИИ) → pending_review")
    s.check(status("tgx") == "published" and status("tg1") == "published", "конкурент с флагом ИИ «агент» и канал агентства → published")
    with db.tx(p) as c:
        r = c.execute("SELECT status_reason FROM listings WHERE id = 'ai1'").fetchone()[0]
    s.check(r == "ИИ: комиссия", "причина для агента: «ИИ: комиссия»", r)
    with db.tx(p) as c:
        c.execute("UPDATE listings SET decision = 'approved' WHERE id = 'dub1'")
        c.execute("UPDATE listings SET decision = 'rejected' WHERE id = 'ss0'")
    sync(good, dropped)
    s.check(status("dub1") == "published" and status("ss0") == "rejected", "решение человека переживает повторный sync (оба направления)")
    c2 = sync(good[1:], dropped)
    s.check(status("ss0") == "gone" and c2.get("gone") == 1, "пропавшее из выгрузки → gone", c2)
    c3 = sync(good[:2])
    s.check(c3.get("gone") is None and status("ss5") == "published", "сломанная выгрузка (меньше половины базы) → gone не ставится", c3)
    c4 = sync(good[:2], full=False)
    s.check(c4.get("gone") is None, "--partial: шаг gone пропущен", c4)
    s.check(core.listing(st["X"])["status"] == "published" and core.listing(st["Y"])["status"] == "rejected"
            and core.listing(st["Z1"])["status"] == "published" and core.listing(st["Z2"])["status"] == "rejected",
            "основная тестовая БД: решения из Telegram (X, Y) и админки (Z1, Z2) живы после реальных sync", (st["X"], st["Y"], st["Z1"], st["Z2"]))
    out = run_sync()
    s.check(core.listing(st["Z1"])["status"] == "published" and core.listing(st["Z2"])["status"] == "rejected",
            "…и после ещё одного `db.py sync` на реальной выгрузке", out.splitlines()[0])
    # dedupe (TZ 10) on the real base: a repost of an agency flat (same photos, another post id) is merged into it;
    # the real listings (already deduped, some share a building shot) stay apart
    import ingest
    items = json.loads((ROOT / "listings.json").read_text())
    for i in items:
        i["hashes"] = [ingest.dhash(p) for p in i["photos"] if os.path.exists(p)]
    first = next(i for i in items if i["source"] == "agency" and len(i["hashes"]) >= 2)
    items.append({**first, "id": "tg_repost", "floor": (first["floor"] or 0) + 1, "urls": ["https://t.me/x/1"]})
    kept = {i["id"] for i in ingest.dedupe(items)}
    gone = sorted({i["id"] for i in items} - kept)
    s.check(gone == ["tg_repost"], "дедуп по фото на реальной базе: повторный пост агентства склеен, разные квартиры с 1 общим фото — нет", gone)
    s.table("_sync_pravila", "Правила синхронизации и дедупа")


def s14_sheets(s):
    s.check(sheets.configured() is None, "Google Sheets не настроен в тестовом окружении")
    s.check(sheets.sync() is False and sheets.safe_sync() is False, "sync()/safe_sync() без настройки: no-op, False, без исключений")
    import threading
    n = threading.active_count()
    sheets.sync_later()
    s.check(threading.active_count() == n, "sync_later() без настройки не запускает фоновый поток")
    tabs = sheets.build(str(APP_DB))
    s.check(list(tabs) == ["Объявления", "Заявки", "Клиенты", "Агенты"], "4 вкладки", list(tabs))
    with db.tx() as c:
        cnt = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("listings", "leads", "clients", "agents")}
    s.check([len(r) - 1 for r in tabs.values()] == [cnt["listings"], cnt["leads"], cnt["clients"], cnt["agents"]],
            "строк столько же, сколько в БД", cnt)
    s.check(all(len(set(map(len, rows))) == 1 for rows in tabs.values()), "у всех строк столько же столбцов, сколько в заголовке")
    leads = {r[0]: r for r in tabs["Заявки"][1:]}
    l1 = leads[st["lead1"]]
    i = core.listing(core.lead(st["lead1"])["listing_id"])
    s.check(l1[2] == "Сдана" and l1[4] == "@anna_e2e" and i["urls"][0] in l1[10] and l1[11] == "@agent_a_e2e",
            "Заявки: статус по-русски, контакт, ссылки источника, агент", l1[:5])
    ag = {r[2]: r for r in tabs["Агенты"][1:]}
    s.check(ag["@agent_b_e2e"][4] == "нет" and ag["@agent_a_e2e"][5] >= 1, "Агенты: отключённый B «нет», у A есть заявки")
    cl = {r[2]: r for r in tabs["Клиенты"][1:] if r[2]}
    s.check(cl["@anna_e2e"][6].startswith(st["ru_request"]) and cl["@anna_e2e"][7] == 3, "Клиенты: текущий запрос и число поисков", cl["@anna_e2e"][6:9])
    lst = {r[0]: r for r in tabs["Объявления"][1:]}
    s.check(lst[st["Y"]][1] == "Отклонено" and lst[st["Y"]][3] == "rejected", "Объявления: статус и решение")
    s.check(not any(isinstance(x, (list, dict)) or x is None for rows in tabs.values() for r in rows for x in r), "ячейки — простые значения (без None/списков)")
    rows = s.checks[:]
    for t, data in tabs.items():
        rows.append((f"{t}: {' | '.join(map(str, data[0]))}", None, " / ".join(" | ".join(str(x)[:24] for x in r) for r in data[1:3])))
    s.table("_sheets", "Google Sheets: зеркало БД (без сети)", rows)


TZ = [
    (1, "Бот под брендом Homes in Georgia", [1, 6, 15, 12], "—"),
    (2, "Запрос текстом, фото понравившихся квартир, точка на карте", [1, 2, 3, 15], "реальная доставка Telegram (фейковый Bot API)"),
    (3, "Клиент видит только фото и описание: без ссылок, телефонов, источника", [1, 2, 3, 4, 5, 6, 10], "—"),
    (4, "«Нравится» → заявка агентам в Telegram со ссылкой на источник", [5, 7, 9, 11, 12], "реальная доставка Telegram"),
    (5, "Канал агентства", [1, 13], "живой сбор (fetch_channel.py не запускался)"),
    (6, "ss.ge и myhome.ge, только собственники", [13], "живой сбор с сайтов не запускался"),
    (7, "Telegram-каналы конкурентов", [11, 12], "живой сбор не запускался"),
    (8, "Facebook-группы", [11, 12, 13], "Apify (платно) не запускался"),
    (9, "База обновляется автоматически", [11, 13], "расписание refresh.sh не установлено, фетчеры не запускались"),
    (10, "Отсев агентов и дублей (телефоны, адреса, фото)", [13], "—"),
    (11, "Сомнительные объявления сначала проверяет агент", [7, 10, 11, 12], "—"),
    (12, "Админка: объявления, проверка, заявки, агенты (и Mini App агентов в Telegram)", [12, 17], "работа на сервере 24/7, HTTPS, настоящий клиент Telegram"),
    (13, "Данные сохраняются в Google Sheets", [14], "запись в реальную таблицу (нет ключа сервисного аккаунта)"),
    (14, "Карта на OpenStreetMap", [15, 3], "ключ/сервис карты агентства не дан; Mini App в настоящем Telegram"),
    (15, "Оформление по дизайн-системе агентства", [15, 12], "только визуально по скриншотам"),
    (16, "Потом подключить сайт и WhatsApp", [16], "сами адаптеры сайта/WhatsApp не написаны (этап 2)"),
]


def s16_coverage(s):
    # channel-agnostic core: a "web" client goes through the same functions as Telegram, no bot involved
    cl = core.client("web", "session-e2e-1", name="Web visitor", lang_hint="en")
    core.add_input(cl, text="2 bedrooms in Vake")
    lid = st["X"]
    ld = core.react(cl, lid, "like")
    s.check(ld and ld["client"]["channel"] == "web" and "web:session-e2e-1" in core.lead_text(ld) and core.staff_chats(),
            "ядро без Telegram: web-клиент → лайк → лид с контактом web:… для агентов", ld and ld["id"])
    s.check(core.client("web", "session-e2e-1")["lang"] == "en" and core.t("en", "greeting").startswith("Hi!"),
            "язык и тексты клиента — в core, не в боте")
    by_n = {r["n"]: r["ok"] for r in results}
    rows = []
    for n, req, scen, gap in TZ:
        ok = all(by_n.get(k, k == 16) for k in scen if k != 16) and (16 not in scen or s.checks[0][1])
        rows.append((f"ТЗ {n}. {req}", ok, f"сценарии {', '.join(map(str, scen))}; не доказано локально: {gap}"))
    s.checks += [(d, ok, det) for d, ok, det in rows]
    s.table("_pokrytie_tz", "Покрытие ТЗ (16 пунктов)", rows)


def main():
    global shooter
    print("e2e dir:", W, "| codex:", "FAKE" if FAKE_LLM else "real", "| fake Bot API port:", TG_PORT)
    out = subprocess.run([PY, str(ROOT / "db.py"), "sync"], cwd=WORK, capture_output=True, text=True, env=os.environ)
    print(out.stdout.splitlines()[0] if out.stdout else out.stderr)
    shooter = Shooter(str(SHOTS_TMP))
    start_bot()
    try:
        for n, name, fn in [(15, "Mini App: карта, поиск места, перетаскивание, радиус, sendData", s15_miniapp),
                            (8, "Регистрация агента /agent <код>", s8_agents),
                            (1, "Русский клиент: /start → текстовый запрос → 5 карточек без утечек", s1_ru_client),
                            (4, "«Ещё варианты» без повторов и «👎 Не то»", s4_more),
                            (5, "«❤️ Нравится» → лид агентам со ссылкой", s5_like),
                            (2, "Запрос с фото понравившейся квартиры", s2_photo),
                            (3, "Точка на карте (web_app_data) → квартиры рядом", s3_map),
                            (6, "Англоязычный клиент", s6_english),
                            (7, "Перезапуск бота: состояние в БД", s7_restart),
                            (9, "Статусы лидов кнопками", s9_lead_status),
                            (10, "Проверка объявлений в Telegram", s10_moderation),
                            (11, "Группа агентства, /leads_private, /moderate_all, /review", s11_group),
                            (12, "Админка (Playwright)", s12_admin),
                            (17, "Mini App агента: вход по initData, заявки, статусы, проверка, настройки", s17_agent_app),
                            (13, "Правила синхронизации", s13_sync),
                            (14, "Google Sheets", s14_sheets),
                            (16, "Покрытие ТЗ + ядро без Telegram (сайт/WhatsApp)", s16_coverage)]:
            run(n, name, fn)
    finally:
        BOT.stop()
        shooter.close()
        f.server.shutdown()
    calls = codex_calls()
    print("\n=== summary")
    for r in sorted(results, key=lambda r: r["n"]):
        print(f"{r['n']:>2}. {'PASS' if r['ok'] else 'FAIL'}  {r['name']}  ({r['secs']}s, {len(r['shots'])} png)")
    print(f"codex calls: {len(calls)} ({'fake' if FAKE_LLM else 'real'}), total {sum(c['secs'] for c in calls):.0f}s")
    json.dump({"codex_calls": len(calls), "codex_mode": "fake" if FAKE_LLM else "real", "results": results},
              open(PROOF / "results.json", "w"), ensure_ascii=False, indent=1)
    sys.exit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
