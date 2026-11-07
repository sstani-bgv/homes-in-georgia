"""Proof run of the whole user journey (docs/user-journey.txt): both funnels end to end on the new platform, with
screenshots. `.venv/bin/python bot/test/e2e/journey_proof.py` from the repo root (rerunnable, ~3 min).

Isolation: its own database `homes_e2e` on the local Postgres (~/.hig-pg/data; dropped and rebuilt every run:
alembic upgrade head + app.seed_demo + journey fixtures), a fake Bot API (fake_tg.py) instead of api.telegram.org,
a fake bot token, its own backend instance on 127.0.0.1:8010 serving frontend/dist, media under tmp/journey_e2e.
Nothing from the repo .env is used: every key of it is overridden in the environment before anything is imported.
The live DB `homes` and the live services (uvicorn :8000, worker, bot) are never touched.

Real code paths: the bot application (bot.build) polls the fake Bot API; the backend services, notify and worker
handlers run for real; the CRM and the Telegram Mini App (/m/*) run in headless Chrome against the isolated backend
(Mini App auth = Telegram initData signed with the fake token, desktop CRM = /api/auth/login). Stubbed: the LLM
(core.ask, deterministic) and geocoding. Time travel: a job's due_at is moved to now and the worker's run_job executes
it (what the live worker loop does when the time comes).

Output: proof/journey/*.png, results.json, README.md (step, what was checked, PASS/FAIL, screenshots)."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

E2E = Path(__file__).resolve().parent
BOT = E2E.parents[1]
ROOT = BOT.parent
OUT = ROOT / "proof" / "journey"
TMP = ROOT / "tmp" / "journey_e2e"
PG_HOST = Path.home() / ".hig-pg" / "data"
DB_NAME = "homes_e2e"
PORT = 8010
BASE = f"http://127.0.0.1:{PORT}"
PUBLIC = "https://crm.e2e.test"  # PUBLIC_URL in buttons (Telegram web_app needs https); opened locally at BASE
TOKEN = "123456:JOURNEY-E2E-FAKE"
assert DB_NAME not in ("homes", "homes_test")

CLIENT = {"id": 1001, "name": "Анна", "username": "anna_client", "lang": "ru"}
NINO = {"id": 5001, "name": "Нино", "username": "nino_operator"}
MARIAM = {"id": 5003, "name": "Мариам", "username": "mariam_operator"}
GEORGIY = {"id": 5002, "name": "Георгий", "username": "georgiy_agent"}
JOHN = {"id": 1002, "name": "John", "username": "john_client", "lang": "en"}
GROUP = {"id": -100500, "title": "Операторы"}
WORK = {"id": -100600, "title": "Рабочая группа"}
PUBLISH = {"id": -1007770001, "originals": 2, "owner": 22, "album": 207}  # PUBLISH_CHAT_ID and its topics (fake)

# journey listings (the parser's output format, bot/listings.json); photos are real scraped flats, relative to bot/
PH = lambda n: [f"raw/img/mh{n}_{i}.jpg" for i in range(3)]  # noqa: E731
LISTINGS = [
    ("ss9101", "ss", "Ваке", 850, 2, "ул. Абашидзе 17", PH(21319607), False, [],
     "Светлая 2-спальная квартира, новый ремонт, балкон с видом на парк. Сдаёт собственник. +995 591 23 45 67"),
    ("mh9102", "myhome", "Сабуртало", 700, 1, "ул. Казбеги 40", PH(20655565), True, ["один номер в 6 объявлениях"],
     "Квартира у метро. Звоните +995 577 31 31 31"),
    ("ss9103", "ss", "Ваке", 780, 2, "ул. Гамсахурдиа 8", PH(19709146), False, [],
     "Уютная квартира, мебель и техника. +995 555 12 34 56"),
    ("ss9104", "ss", "Сабуртало", 780, 2, "пр. Пекини 30", PH(20577620), False, [],
     "Две спальни, тёплый пол, рядом школа."),
    ("ss9105", "ss", "Сабуртало", 650, 1, "ул. Нуцубидзе 12", PH(22651770), False, [],
     "Компактная квартира дешевле, с хорошим ремонтом."),
]
MINE = ["ss9101", "ss9104", "ss9105"]  # what the stub LLM picks first (the journey's cards)


def free_port(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


# ============================================================ environment (before any app/bot import)

def setup_env(tg_port):
    db_url = f"postgresql+psycopg://postgres@/{DB_NAME}?host={PG_HOST}"
    over = {
        "DATABASE_URL": db_url, "TEST_DATABASE_URL": "", "BOT_TOKEN": TOKEN, "TG_API_BASE": f"http://127.0.0.1:{tg_port}",
        "PUBLIC_URL": PUBLIC, "AGENT_APP_URL": PUBLIC, "OPERATORS_CHAT_ID": str(GROUP["id"]), "OPERATORS_TOPIC_ID": "",
        "WORK_GROUP_CHAT_ID": str(WORK["id"]), "TOPIC_DEAL": "7", "PUBLISH_CHAT_ID": str(PUBLISH["id"]), "PUBLISH_TOPIC_ORIGINALS": str(PUBLISH["originals"]),
        "PUBLISH_TOPIC_OWNER": str(PUBLISH["owner"]), "PUBLISH_TOPIC_ALBUM": str(PUBLISH["album"]), "AGENCY_CHANNEL": "hig_e2e_channel",
        "AGENCY_CHANNEL_ID": "", "DEALS_CHANNEL_ID": "", "NEWS_CHAT_ID": "", "SECRET_KEY": "journey-e2e-secret-0123456789abcdef-xyz",
        "JWT_TTL_HOURS": "72", "APP_ENV": "dev", "DEV_CORS": "false", "MEDIA_DIR": str(TMP / "media"), "DATA_DIR": str(TMP / "botdata"),
        "AGENT_CODE": "e2e-code", "SUPERADMIN_TG_IDS": "", "ADMIN_BOOTSTRAP_LOGIN": "", "ADMIN_BOOTSTRAP_PASSWORD": "",
        "OPENAI_API_KEY": "", "OPENAI_BASE_URL": "", "APIFY_TOKEN": "", "GOOGLE_SA_JSON": "", "SHEET_ID": "", "LLM_PROVIDER": "openai",
        "BOT_USERNAME": "homes_in_georgia_test_bot", "TZ": "Asia/Tbilisi", "ENV_FILE": str(TMP / "no.env"),
    }
    env_file = ROOT / ".env"
    keys = [ln.split("=", 1)[0].strip() for ln in env_file.read_text().splitlines()
            if "=" in ln and not ln.lstrip().startswith("#")] if env_file.exists() else []
    for k in keys:  # every repo .env key gets an explicit value: real env vars win over .env in pydantic and load_env
        os.environ[k] = over.get(k, "")
    os.environ.update(over)


def create_db():
    import psycopg
    with psycopg.connect(f"postgresql://postgres@/postgres?host={PG_HOST}", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{DB_NAME}"')
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", env=os.environ.copy(),
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("alembic upgrade head failed:\n" + r.stdout + r.stderr)


# ============================================================ results

RESULTS = []


class Step:
    """with Step("A1a", "what is checked") as s: ...  An exception = FAIL with the error; s.shot()/s.chat() add PNGs."""

    def __init__(self, sid, what):
        self.sid, self.what, self.shots, self.checks, self.page = sid, what, [], [], None

    def __enter__(self):
        print(f"--- {self.sid}: {self.what}", flush=True)
        return self

    def ok(self, cond, msg):
        if not cond:
            raise AssertionError(msg)
        self.checks.append(msg)

    def eq(self, got, want, msg):
        if got != want:
            raise AssertionError(f"{msg}: got {got!r}, want {want!r}")
        self.checks.append(f"{msg}: {got!r}")

    def __exit__(self, et, ev, tb):
        err = None
        if et is not None:
            frames = [f for f in traceback.extract_tb(tb) if f.filename == __file__]
            where = f" (journey_proof.py:{frames[-1].lineno})" if frames else ""
            err = f"{et.__name__}: {ev}{where}"[:1500]
            print("FAIL", self.sid, err, flush=True)
            if self.page is not None:
                try:
                    self.shot(self.page, f"{self.sid}_FAIL.png")
                except Exception:  # noqa: BLE001
                    pass
        RESULTS.append({"id": self.sid, "what": self.what, "status": "FAIL" if err else "PASS", "checks": self.checks,
                        "screenshots": self.shots, "error": err})
        return True

    def shot(self, page, name):
        vp = page.viewport_size
        if vp and vp["width"] < 500:  # Mini App: grow the viewport to the page so the fixed bottom tab bar stays at the bottom
            h = page.evaluate("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)")
            page.set_viewport_size({"width": vp["width"], "height": max(vp["height"], h)})
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / name))
            page.set_viewport_size(vp)
        else:
            page.screenshot(path=str(OUT / name), full_page=True)
        self.shots.append(name)

    def chat(self, name, chat, since=0, until=None, title=None, subtitle=None, note=None, thread=None):
        titles = {CLIENT["id"]: ("Homes in Georgia AI", f"чат клиента · {CLIENT['name']} (@{CLIENT['username']})"),
                  GEORGIY["id"]: ("Homes in Georgia AI", "чат агента · Георгий"),
                  JOHN["id"]: ("Homes in Georgia AI", f"English client · {JOHN['name']} (@{JOHN['username']})"),
                  NINO["id"]: ("Homes in Georgia AI", "чат оператора · Нино"),
                  GROUP["id"]: ("Операторы", "общая группа операторов"),
                  WORK["id"]: ("Рабочая группа", "топик «Сделки»")}
        t, sub = titles.get(chat, (str(chat), ""))
        render.chat_png(SHOOTER, FAKE.log, chat, str(OUT / name), title or t, subtitle or sub, note, since, until, thread=thread)
        self.shots.append(name)


# ============================================================ fake Telegram driver (as in test_journey_e2e)

def find(chat, pred, since=0, what="message", timeout=25):
    def look(calls):
        return next((c for c in reversed(calls) if c.get("chat") == chat and pred(c)), None)
    try:
        return FAKE.wait(look, timeout=timeout, since=since, what=what)
    except AssertionError:
        for c in FAKE.calls(chat=chat, since=since)[-6:]:
            print("   last:", {k: (str(c.get(k))[:160] if c.get(k) else None) for k in ("method", "text", "caption", "reply_markup")})
        raise


def buttons(c):
    return [b for row in FAKE.markup(c) for b in row]


def with_button(chat, text, since=0, prefix=False):
    return find(chat, lambda c: any((b["text"].startswith(text) if prefix else b["text"] == text) for b in buttons(c)), since,
                f"button {text!r} in chat {chat}")


def with_text(chat, needle, since=0):
    return find(chat, lambda c: needle in (c.get("text") or c.get("caption") or ""), since, f"text {needle!r} in chat {chat}")


def press(user, chat, text, group=None, since=0, prefix=False, msg=None):
    out = msg or with_button(chat, text, since, prefix)
    if prefix:
        text = next(b["text"] for b in buttons(out) if b["text"].startswith(text))
    mark = FAKE.mark()
    FAKE.press(user, out, text, group=group)
    FAKE.wait(lambda calls: any(c["method"] == "answerCallbackQuery" for c in calls), since=mark, what=f"answer to {text!r}")
    return mark


def press_data(user, chat, data_pred, since=0):
    out = find(chat, lambda c: any(data_pred(b.get("callback_data") or "") for b in buttons(c)), since, "button by data")
    text = next(b["text"] for b in buttons(out) if data_pred(b.get("callback_data") or ""))
    return press(user, chat, text, msg=out), text


def say(user, text):
    mark = FAKE.mark()
    FAKE.send_text(user, text)
    return mark


def btn_url(c, text_prefix, key="url"):
    """The url of an inline button (key="web_app": a Mini App button) or of a reply-keyboard web_app button."""
    rows = (c.get("reply_markup") or {}).get("keyboard") or []
    for b in buttons(c) + [b for row in rows for b in row if isinstance(b, dict)]:
        if b["text"].startswith(text_prefix):
            return (b.get("web_app") or {}).get("url") if key == "web_app" else b.get(key)
    return None


def local(url):
    return url.replace(PUBLIC, BASE, 1)


# ============================================================ LLM stub (deterministic, like test_journey_e2e)

ASKS = []


def fake_ask(prompt, schema, images=(), timeout=None, **kw):
    ASKS.append({"prompt": prompt, "images": list(images or [])})
    if os.environ.get("JP_DEBUG"):
        print("ASK", prompt[-3000:], flush=True)
    props = schema.get("properties", {})
    if "top" in props:
        head, _, cat = prompt.rpartition("Каталог")
        ids = [ln.split(" | ")[0].strip() for ln in cat.splitlines() if " | " in ln]
        mine = [x for x in MINE if x in ids and x not in head]  # skip what the client already liked / rejected
        pick = (mine or ids)[:2]
        return {"understood": "2 спальни в Ваке или Сабуртало до $900", "matches_total": len(ids), "max_price_usd": None,
                "top": [{"id": x, "reason": "подходит по анкете и по стилю фото" if images else "подходит по анкете"} for x in pick]}
    return {"place": None, "label": None, "radius_km": None}


# ============================================================ backend helpers

def svc(fn, *a, **kw):
    from app.db import SessionLocal
    with SessionLocal() as s:
        return fn(s, *a, **kw)


def sql(q, params=None):
    from sqlalchemy import text
    from app.db import SessionLocal
    with SessionLocal() as s:
        r = s.execute(text(q), params or {})
        rows = r.fetchall() if r.returns_rows else None
        s.commit()
        return rows


TOKENS = {}


def api(login, method, path, **kw):
    import httpx
    if login not in TOKENS:
        r = httpx.post(f"{BASE}/api/auth/login", json={"login": login, "password": "demo"})
        r.raise_for_status()
        TOKENS[login] = r.json()["token"]
    return httpx.request(method, f"{BASE}/api{path}", headers={"Authorization": f"Bearer {TOKENS[login]}"}, timeout=30, **kw)


def fire(kind, ref, before=None):
    """Time travel: the job (kind, ref) becomes due now and the worker's run_job executes it. -> the handler's result."""
    from sqlalchemy import select
    from app import worker
    from app.db import SessionLocal
    from app.models import Job
    from app.services.common import now
    with SessionLocal() as s:
        if before:
            before(s)
        job = s.scalar(select(Job).where(Job.kind == kind, Job.ref == str(ref), Job.done_at.is_(None))
                       .order_by(Job.due_at).limit(1).with_for_update())
        if job is None:
            raise AssertionError(f"no pending job {kind}:{ref}")
        job.due_at = now() - timedelta(seconds=1)
        s.flush()
        return worker.run_job(s, job)


def itemsof(d):
    return d["items"] if isinstance(d, dict) else d


def lead_row(lead_id):
    return sql("SELECT status, sla_stage, agent_id, operator_id, client_outcome, agent_outcome, refusal_reason, deal_id, "
               "property_id, contacts_shared_at FROM leads WHERE id = :i", {"i": lead_id})[0]


def user_id(login):
    return sql("SELECT id FROM users WHERE login = :l", {"l": login})[0][0]


# ============================================================ browser

def init_data(user):
    """Telegram.WebApp.initData for `user`, signed the way Telegram does it (with the fake bot token)."""
    pairs = {"auth_date": str(int(time.time())), "query_id": "AAHjourneyE2E",
             "user": json.dumps({"id": user["id"], "first_name": user["name"], "username": user["username"], "language_code": "ru"},
                                ensure_ascii=False, separators=(",", ":"))}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


MOCK_JS = (E2E / "telegram_webapp_mock.js").read_text()


def _route_tg(ctx):
    def handle(route):
        if route.request.url.startswith("https://telegram.org/js/telegram-web-app.js"):
            route.fulfill(body=MOCK_JS, content_type="application/javascript")
        else:
            route.abort()
    ctx.route(re.compile(r"https?://([a-z0-9-]+\.)*telegram\.org/.*"), handle)


def desktop(login):
    api(login, "GET", "/auth/me")
    ctx = SHOOTER.browser.new_context(viewport={"width": 1440, "height": 900}, timezone_id="Asia/Tbilisi", locale="ru-RU")
    ctx.add_init_script(f"try {{ localStorage.setItem('hig_token', {json.dumps(TOKENS[login])}); }} catch (e) {{}}")
    _route_tg(ctx)
    return ctx


def miniapp(user):
    ctx = SHOOTER.browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                      has_touch=True, timezone_id="Asia/Tbilisi", locale="ru-RU")
    ctx.add_init_script(f"window.__tgInitData = {json.dumps(init_data(user))};")
    _route_tg(ctx)
    return ctx


def open_page(ctx, path, wait_text=None, s=None):
    pg = ctx.new_page()
    if s is not None:
        s.page = pg
    pg.goto(BASE + path if path.startswith("/") else path)
    pg.wait_for_load_state("networkidle")
    if wait_text:
        pg.get_by_text(wait_text).first.wait_for(timeout=20000)
    pg.wait_for_timeout(400)
    return pg


def click(pg, name, exact=False, role="button", nth=0):
    loc = pg.get_by_role(role, name=name, exact=exact)
    loc.nth(nth).click()


def toast(pg, text):
    pg.locator(".toast", has_text=text).first.wait_for(timeout=15000)


# ============================================================ setup

def start():
    global FAKE, SHOOTER, render
    if not free_port(PORT):
        raise SystemExit(f"port {PORT} is busy: stop whatever runs there (the live server is on :8000, this needs :{PORT})")
    shutil.rmtree(TMP, ignore_errors=True)
    (TMP / "media").mkdir(parents=True)
    (TMP / "botdata").mkdir(parents=True)
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    sys.path[:0] = [str(BOT), str(E2E), str(ROOT / "backend")]
    from fake_tg import FakeTelegram
    import render as _render
    render = _render
    FAKE = FakeTelegram(str(TMP / "tg_files"))
    setup_env(FAKE.serve())
    os.chdir(BOT)  # like bot/run.py: listing photos are relative to bot/
    client_img = BOT / "data" / "client"
    S["client_img_before"] = set(client_img.glob("*")) if client_img.exists() else set()

    from app.config import settings
    assert DB_NAME in settings.DATABASE_URL and settings.BOT_TOKEN == TOKEN and settings.TG_API_BASE.startswith("http://127.0.0.1")
    assert settings.MEDIA_DIR.startswith(str(TMP)) and settings.SHEET_ID == "" and settings.SUPERADMIN_TG_IDS == ""
    print("isolated:", settings.DATABASE_URL, settings.TG_API_BASE, flush=True)
    create_db()

    import db
    db.init()
    from app import seed_demo
    seed_demo.seed()  # demo users (nino / georgiy / david ... password demo), listings, leads in every status, deals
    for u in (NINO, GEORGIY, MARIAM):
        sql("UPDATE users SET tg_id = :t, username = :u WHERE name = :n", {"t": u["id"], "u": u["username"], "n": u["name"]})
    items = [{"id": lid, "source": src, "owner_id": src, "urls": [f"https://{'ss.ge' if src == 'ss' else 'myhome.ge'}/ru/pr/{lid[2:]}"],
              "price_usd": price, "area": 45.0 + 15 * beds, "floor": 6, "bedrooms": beds, "address": addr, "district": distr,
              "latlng": [41.709 + 0.004 * k, 44.765 + 0.003 * k], "photos": photos, "description_ru": text.split(" +995")[0].split(" Звоните")[0],
              "features": [], "agent_suspect": suspect, "agent_reasons": reasons, "text": text}
             for k, (lid, src, distr, price, beds, addr, photos, suspect, reasons, text) in enumerate(LISTINGS)]
    lst = TMP / "listings.json"
    lst.write_text(json.dumps(items, ensure_ascii=False))
    db.sync(str(lst), str(TMP / "none.json"), full=False)
    sql("UPDATE listings SET first_seen = :t WHERE id LIKE '%91__'", {"t": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()})

    import core
    core.ask = fake_ask
    core.geocode = lambda q: None

    import uvicorn
    from app.main import app as api_app
    server = uvicorn.Server(uvicorn.Config(api_app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    import httpx
    for _ in range(100):
        try:
            if httpx.get(f"{BASE}/health").json().get("db"):
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError("isolated backend did not start")

    import bot as botmod
    app = botmod.build(TOKEN)
    stop = threading.Event()

    async def run_bot():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(poll_interval=0.05, timeout=1)
        while not stop.is_set():
            await asyncio.sleep(0.1)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    th = threading.Thread(target=lambda: asyncio.run(run_bot()), daemon=True)
    th.start()
    FAKE.wait(lambda _: FAKE.polls > 0, timeout=20, what="bot polling")
    SHOOTER = render.Shooter(str(TMP / "shots"))
    return server, stop, th


# ============================================================ funnel 1: objects

S = {}  # state shared by the steps


def funnel1():
    gid = user_id("georgiy")

    with Step("C1", "Новые объявления в Mini App агента: метки ИИ, «Беру в работу» закрепляет объект, у другого агента он заблокирован") as s:
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, "/m/new", "Беру в работу", s)
        s.ok(pg.get_by_text("ул. Абашидзе 17").count() > 0, "объявление ss9101 (ss.ge) в списке «Новые»")
        s.ok(pg.get_by_text("ул. Казбеги 40").count() > 0, "объявление mh9102 (myhome) в списке")
        s.shot(pg, "C1_new_listings.png")
        pg.get_by_placeholder("Район, адрес").fill("Абашидзе 17")
        pg.wait_for_timeout(800)
        click(pg, "Беру в работу")
        pg.wait_for_url(re.compile(r"/m/properties/\d+"), timeout=15000)
        S["p1"] = int(pg.url.rstrip("/").rsplit("/", 1)[-1])
        pg.get_by_text("Подсказка").first.wait_for()
        s.shot(pg, "C1_taken.png")
        p = api("georgiy", "GET", f"/properties/{S['p1']}").json()
        s.eq((p["status"], p["agent"]["name"] if p.get("agent") else p.get("agent_id")), ("taken", "Георгий"), "объект взят Георгием")
        r = api("levan", "POST", "/properties", json={"listing_id": "ss9101"})
        s.eq((r.status_code, r.json().get("code")), (409, "taken_by_other"), "Леван не может взять тот же объект")
        ids = [x["id"] for x in api("levan", "GET", "/listings/new", params={"limit": 500}).json()["items"]]
        s.ok("ss9101" not in ids and "mh9102" in ids, "взятое объявление пропало из «Новых» у других агентов")
        ctx.close()

    with Step("C2a", "Звонок: «Не дозвонился» → статус call_failed, бот напоминает позвонить снова (worker call_retry)") as s:
        r = api("georgiy", "POST", "/properties", json={"listing_id": "mh9102"})
        S["p2"] = r.json()["id"]
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, f"/m/properties/{S['p2']}", "Не дозвонился", s)
        click(pg, "Не дозвонился")
        toast(pg, "Бот напомнит позвонить")
        pg.get_by_text("Не дозвонились · бот напомнит").first.wait_for()
        s.shot(pg, "C2a_not_reached.png")
        p = api("georgiy", "GET", f"/properties/{S['p2']}").json()
        s.eq(p["status"], "call_failed", "статус объекта")
        due = datetime.fromisoformat(p["next_call_at"]) - datetime.now(timezone.utc)
        s.ok(timedelta(hours=1, minutes=50) < due < timedelta(hours=2, minutes=10), f"следующий звонок через ~2 ч ({due})")
        m = FAKE.mark()
        s.eq(fire("call_retry", S["p2"]), "ok", "задача worker call_retry выполнена")
        rem = with_text(GEORGIY["id"], "Пора перезвонить собственнику", m)
        s.ok(btn_url(rem, "🏠 Открыть объект", "web_app") == f"{PUBLIC}/m/properties/{S['p2']}", "кнопка открывает объект в Mini App")
        s.chat("C2a_reminder_bot.png", GEORGIY["id"], since=m)
        ctx.close()

    with Step("C2b", "Ответил агент → объект в базе с пометкой «агент», телефон в списке агентов, следующее объявление с ним помечается") as s:
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, f"/m/properties/{S['p2']}", "Дозвонился", s)
        click(pg, "Дозвонился")
        click(pg, "Агент", exact=True, role="radio")
        pg.get_by_label("Имя").fill("Агентство «Дом»")
        pg.get_by_label("Телефон").fill("+995 577 31 31 31")
        click(pg, "Сохранить")
        toast(pg, "пометкой «агент»")
        pg.get_by_text("Ответил агент").first.wait_for()
        s.shot(pg, "C2b_agent_answered.png")
        p = api("georgiy", "GET", f"/properties/{S['p2']}").json()
        s.eq((p["status"], p["owner"]["is_agent"]), ("agent_marked", True), "статус и пометка собственника")
        import db
        new = [{"id": "ss9106", "source": "ss", "owner_id": "ss", "urls": [], "price_usd": 690, "area": 50.0, "floor": 3, "bedrooms": 1,
                "address": "ул. Казбеги 44", "district": "Сабуртало", "latlng": None, "photos": [], "description_ru": "Новое объявление",
                "features": [], "agent_reasons": [], "text": "Сдаю квартиру, звоните 577 31 31 31"}]
        (TMP / "next.json").write_text(json.dumps(new, ensure_ascii=False))
        db.sync(str(TMP / "next.json"), str(TMP / "none.json"), full=False)
        st = sql("SELECT status, agent_reasons FROM listings WHERE id = 'ss9106'")[0]
        s.ok(st[0] == "pending_review" and "телефон в базе агентов" in st[1], f"новое объявление с этим номером помечено: {st[0]}, {st[1]}")
        ctx.close()

    with Step("C3", "Собственник подтвердил → съёмка назначена, проверка базы «✅ Добавлено в базу»") as s:
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, f"/m/properties/{S['p1']}", "Дозвонился", s)
        click(pg, "Дозвонился")
        pg.get_by_label("Имя").fill("Нана Кобахидзе")
        pg.get_by_label("Телефон").fill("+995 591 23 45 67")
        click(pg, "Сохранить")
        toast(pg, "Собственник подтверждён")
        shoot = (datetime.now() + timedelta(days=1)).replace(hour=11, minute=0)
        pg.get_by_label("Дата и время съёмки").fill(shoot.strftime("%Y-%m-%dT%H:%M"))
        click(pg, "Назначить съёмку")
        toast(pg, "Съёмка назначена")
        pg.get_by_text("Добавлено в базу").first.wait_for()
        s.shot(pg, "C3_shoot_added.png")
        p = api("georgiy", "GET", f"/properties/{S['p1']}").json()
        s.eq((p["status"], p["dup_check"]), ("shoot_scheduled", "added"), "статус и проверка дубля")
        s.ok(sql("SELECT count(*) FROM owners WHERE phone_norm LIKE '%591234567'")[0][0] == 1, "собственник сразу в базе собственников")
        ctx.close()

    with Step("C3", "Дубль собственника: тот же телефон уже есть в базе → «⚠️ Уже есть в базе»") as s:
        p3 = api("georgiy", "POST", "/properties", json={"listing_id": "ss9103"}).json()["id"]
        S["p3"] = p3
        api("georgiy", "POST", f"/properties/{p3}/call", json={"reached": True}).raise_for_status()
        api("georgiy", "POST", f"/properties/{p3}/owner", json={"is_owner": True, "owner_name": "Гиорги Беридзе",
                                                                  "owner_phone": "+995 555 12 34 56"}).raise_for_status()
        r = api("georgiy", "POST", f"/properties/{p3}/shoot", json={"shoot_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        s.eq(r.json().get("dup_check"), "exists", "проверка дубля по телефону (собственник из seed, объект Левана)")
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, f"/m/properties/{p3}", "Уже есть в базе", s)
        s.shot(pg, "C3_duplicate_exists.png")
        ctx.close()

    with Step("C4", "Фото и видео загружены в Mini App по комнатам, чек-лист, «Готово — отправить оператору» → оператору") as s:
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, f"/m/properties/{S['p1']}", "Загрузить фото и видео", s)
        for room, f in (("Гостиная", "img/mh21319607_0.jpg"), ("Гостиная", "video/ss37055835.mp4"), ("Кухня", "img/mh21319607_1.jpg"),
                        ("Спальня 1", "img/mh21319607_2.jpg")):
            pg.get_by_label("Комната").select_option(room)
            n = pg.locator(".photo-strip .ph").count()
            pg.set_input_files("input[type=file]", str(BOT / "raw" / f))
            pg.wait_for_function(f"document.querySelectorAll('.photo-strip .ph').length > {n}", timeout=15000)
        click(pg, "Ванная")
        pg.wait_for_timeout(600)
        s.shot(pg, "C4_media_checklist.png")
        m = FAKE.mark()
        pg.on("dialog", lambda d: d.accept())
        click(pg, "Готово — отправить оператору")
        toast(pg, "Отправлено оператору")
        pg.get_by_text("Отправлено оператору · фото и чек-лист закрыты").first.wait_for()
        s.shot(pg, "C4_submitted.png")
        p = api("georgiy", "GET", f"/properties/{S['p1']}").json()
        s.eq(p["status"], "review", "статус объекта")
        s.eq(len(p["media"]), 4, "файлов загружено (3 фото + видео)")
        s.eq(sorted(m_["kind"] for m_ in p["media"]), ["photo", "photo", "photo", "video"], "видео сохранено как видео")
        s.ok(all(Path(os.environ["MEDIA_DIR"], "properties", str(S["p1"])).glob("*")), "файлы лежат в изолированном MEDIA_DIR")
        g = with_text(GROUP["id"], f"Объект #{S['p1']} на проверке", m)
        s.ok(btn_url(g, "🔍 Проверить", "url") or btn_url(g, "🔍 Проверить", "web_app"), "в группе операторов кнопка «Проверить»")
        s.chat("B7a_review_request_group.png", GROUP["id"], since=m)
        ctx.close()

    with Step("B7a", "Оператор проверяет объект в CRM: автопроверки, «Вернуть агенту» с комментарием → агенту приходит возврат") as s:
        ctx = desktop("nino")
        pg = open_page(ctx, f"/objects/{S['p1']}", "Автопроверки", s)
        s.ok(pg.get_by_text("Добавлено в базу").count() > 0, "автопроверка дубля видна оператору")
        pg.get_by_label("Комментарий агенту").fill("Нет фото ванной, доснимите, пожалуйста")
        s.shot(pg, "B7a_review.png")
        m = FAKE.mark()
        click(pg, "Вернуть агенту")
        toast(pg, "Возвращено агенту")
        s.eq(api("nino", "GET", f"/properties/{S['p1']}").json()["status"], "returned", "статус после возврата")
        with_text(GEORGIY["id"], "вернули на доработку", m)
        s.chat("B7a_returned_agent_bot.png", GEORGIY["id"], since=m)
        ctx.close()

    with Step("B7a", "Агент дозагрузил фото и нажал «Готово — отправить оператору»; оператор видит объект в очереди «На публикацию», в карточке генератор поста заполнен из объекта") as s:
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, f"/m/properties/{S['p1']}", "Оператор вернул", s)
        pg.get_by_label("Комната").select_option("Ванная")
        pg.set_input_files("input[type=file]", str(BOT / "raw" / "img" / "mh20361085_1.jpg"))
        pg.wait_for_function("document.querySelectorAll('.photo-strip .ph').length >= 5", timeout=15000)
        pg.on("dialog", lambda d: d.accept())
        click(pg, "Готово — отправить оператору")
        toast(pg, "Отправлено оператору")
        s.ok(pg.get_by_text("фото и чек-лист закрыты").count() > 0 and pg.locator("input[type=file]").count() == 0,
             "после отправки агент больше не меняет фото и чек-лист")
        ctx.close()
        ctx = desktop("nino")
        pg = open_page(ctx, "/objects?tab=review", "На публикацию", s)
        s.ok(pg.get_by_text("Абашидзе 17").count() > 0, "объект в очереди «На публикацию»")
        pg = open_page(ctx, f"/objects/{S['p1']}", "Автопроверки", s)
        pg.wait_for_function("document.querySelector('textarea') && document.querySelector('textarea').value.includes('#Vake')", timeout=15000)
        text = pg.locator("textarea").first.input_value()
        for part in ("#Vake", "#2Bed #Apartment for #Rent", "850$ + Deposit 850$", "#Price700to900", "#Георгий"):
            s.ok(part in text, f"текст поста из объекта: «{part}»")
        s.eq(pg.locator("[role=checkbox][aria-checked=true]").count(), 5, "все 5 файлов объекта в посте")
        s.shot(pg, "B7b_generator.png")
        S["m_pub"] = FAKE.mark()
        click(pg, "Опубликовать")
        toast(pg, "Опубликовано")
        s.shot(pg, "B7b_generator_published.png")
        p = api("nino", "GET", f"/properties/{S['p1']}").json()
        s.eq(p["status"], "published", "статус объекта")
        S["post_url"] = p.get("post_url") or ""
        s.ok(re.fullmatch(r"https://t\.me/c/7770001/\d+", S["post_url"]), f"ссылка на пост сохранена: {S['post_url']}")
        s.ok(any(x["url"] == S["post_url"] for x in p["publications"]), "пост в «Где опубликован»")
        hist = [h.get("action") for h in itemsof(api("nino", "GET", f"/properties/{S['p1']}/history").json())]
        s.ok("approved" in hist, f"в истории объекта «approved»: {hist}")
        ctx.close()

    with Step("B7b", "Опубликовано из карточки: оригиналы, альбом с водяным знаком и подписью в формате канала, Owner Information") as s:
        ch, m = PUBLISH["id"], S["m_pub"]
        calls = [c for c in FAKE.calls(chat=ch, since=m)]
        topic = lambda c: int(c.get("message_thread_id") or 0)  # noqa: E731
        orig = [c for c in calls if topic(c) == PUBLISH["originals"]]
        album = [c for c in calls if topic(c) == PUBLISH["album"]]
        owner = [c for c in calls if topic(c) == PUBLISH["owner"]]
        s.eq([(c["method"], [x["type"] for x in c["media"]]) for c in orig], [("sendMediaGroup", ["document"] * 5)], "топик оригиналов: 5 файлов документами")
        s.eq([(c["method"], [x["type"] for x in c["media"]]) for c in album], [("sendMediaGroup", ["photo"] * 4 + ["video"])], "топик альбома: 4 фото + видео")
        s.eq(S["post_url"].rsplit("/", 1)[-1], str(album[0]["message_id"]), "post_url = альбом")
        cap = album[0]["media"][0].get("caption") or ""
        for part in ("#Vake", "#2Bed #Apartment for #Rent", "850$ + Deposit 850$", "0% Commission", "#Price700to900",
                     "#Георгий", "APARTMENTS ON MAP"):
            s.ok(part in cap, f"подпись альбома: «{part}»")
        s.ok("<a href=" in cap and album[0]["media"][0].get("parse_mode") == "HTML", "подпись в HTML со ссылками")
        from PIL import Image, ImageChops
        o0, a0 = Image.open(orig[0]["media"][0]["file"]).convert("RGB"), Image.open(album[0]["media"][0]["file"]).convert("RGB")
        diff = ImageChops.difference(o0.resize(a0.size), a0).getbbox()
        s.ok(a0.size == o0.size and diff is not None, f"фото альбома с водяным знаком (отличается от оригинала, {a0.size})")
        side = Image.new("RGB", (a0.width * 2 + 20, a0.height), "white")  # original | watermarked, side by side
        side.paste(o0, (0, 0))
        side.paste(a0, (a0.width + 20, 0))
        side.thumbnail((1400, 1400))
        side.save(OUT / "B7b_watermark.png")
        s.shots.append("B7b_watermark.png")
        s.eq(len(owner), 1, "одно сообщение в топике собственников")
        ot = owner[0]["text"]
        s.ok("Owner Information" in ot and "+995 591 23 45 67" in ot and S["post_url"] in ot, "Owner Information: телефон и ссылка на пост")
        s.chat("B7b_channel_post.png", ch, since=m, thread=PUBLISH["album"], title="Канал агентства · топик альбома",
               subtitle=f"тестовый канал {ch}, топик {PUBLISH['album']} · пост {S['post_url']}")
        s.chat("B7b_originals.png", ch, since=m, thread=PUBLISH["originals"], title="Канал агентства · топик оригиналов",
               subtitle=f"топик {PUBLISH['originals']}: фото без сжатия")
        s.chat("B7b_owner_info.png", ch, since=m, thread=PUBLISH["owner"], title="Канал агентства · топик собственников",
               subtitle=f"топик {PUBLISH['owner']}")

    with Step("C5", "Агенту пришло «опубликован» со ссылкой на пост; в Mini App объект «Опубликовано»") as s:
        msg = with_text(GEORGIY["id"], f"Объект #{S['p1']} опубликован", S["m_pub"])
        s.eq(btn_url(msg, "📢 Пост в канале", "url"), S["post_url"], "кнопка «📢 Пост в канале»")
        s.chat("C5_published_agent_bot.png", GEORGIY["id"], since=S["m_pub"])
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, f"/m/properties/{S['p1']}", "Опубликовано", s)
        s.shot(pg, "C5_published_miniapp.png")
        ctx.close()


# ============================================================ funnel 2: clients

def funnel2():
    f, cid = FAKE, CLIENT["id"]

    with Step("A1a", "/start: язык, затем анкета по одному вопросу, мультивыбор с ✓ и «Готово»") as s:
        m0 = S["m_client"] = say(CLIENT, "/start")
        picked = press(CLIENT, cid, "🇷🇺 Русский", since=m0)
        s.ok(not [c for c in f.calls(chat=cid, since=m0) if c["seq"] <= picked and "1 из 6" in (c.get("text") or "")],
             "сначала только выбор языка")
        with_text(cid, "Ответьте на 6 коротких вопросов", m0)
        press(CLIENT, cid, "2", since=m0)
        m = press(CLIENT, cid, "🐕 Собака")
        with_button(cid, "✓ 🐕 Собака", m)
        press(CLIENT, cid, "Готово ➜", since=m)
        m = press(CLIENT, cid, "6 мес")
        with_button(cid, "✓ 6 мес", m)
        m = press(CLIENT, cid, "12 мес", since=m)
        with_button(cid, "✓ 12 мес", m)
        S["m_a1b"] = press(CLIENT, cid, "Готово ➜", since=m)
        s.checks.append("кнопки ✓ 🐕 Собака, ✓ 6 мес, ✓ 12 мес отмечены")
        s.chat("A1a_start.png", cid, since=m0)

    with Step("A1b", "Бюджет своей суммой «до 900» (бот понял → фильтр), спальни мультивыбором") as s:
        m0 = S["m_a1b"]
        press(CLIENT, cid, "✏️ Своя сумма", since=m0)
        m = say(CLIENT, "до 900")
        with_text(cid, "Понял: <b>до $900</b>", m)
        m = press(CLIENT, cid, "1", since=m)
        with_button(cid, "✓ 1", m)
        m = press(CLIENT, cid, "2", since=m)
        with_button(cid, "✓ 2", m)
        S["m_a1c"] = press(CLIENT, cid, "Готово ➜", since=m)
        s.checks.append("«Понял: до $900», ✓ 1, ✓ 2")
        s.chat("A1b_budget_bedrooms.png", cid, since=m0)

    with Step("A1c", "Где искать: несколько районов сразу (мультивыбор ✓, «Готово»)") as s:
        m0 = S["m_a1c"]
        q = with_button(cid, "🏙 Выбрать районы", m0)
        s.ok({b["text"] for b in buttons(q)} == {"📍 Выбрать на карте", "🏙 Выбрать районы", "Любой район"}, "варианты: карта / районы / любой")
        m = press(CLIENT, cid, "🏙 Выбрать районы", since=m0)
        m = press(CLIENT, cid, "Ваке", since=m)
        with_button(cid, "✓ Ваке", m)
        m = press(CLIENT, cid, "Сабуртало", since=m)
        with_button(cid, "✓ Сабуртало", m)
        S["m_a2a"] = press(CLIENT, cid, "Готово ➜", since=m)
        s.chat("A1c_districts.png", cid, since=m0)

    with Step("A1c", "English client: the questionnaire in English, place = a pin on the map (Mini App map → sendData → profile)") as s:
        u, uid = JOHN, JOHN["id"]
        m0 = say(u, "/start")
        press(u, uid, "🇬🇧 English", since=m0)
        with_text(uid, "Answer 6 short questions", m0)
        for data in ("q:res:0", "q:pets:0", "q:pets:done", "q:terms:2", "q:terms:done", "q:bud:1", "q:beds:0", "q:beds:done"):
            press_data(u, uid, lambda d, data=data: d == data, since=m0)
        m = press_data(u, uid, lambda d: d == "q:place:map", since=m0)[0]
        hint = find(uid, lambda c: btn_url(c, "🗺", "web_app"), m, "map keyboard")
        map_url = btn_url(hint, "🗺", "web_app")
        s.ok(map_url.startswith(f"{PUBLIC}/map"), f"reply-кнопка открывает карту как Mini App: {map_url}")
        ctx = miniapp(u)
        pg = ctx.new_page()
        s.page = pg
        pg.goto(local(map_url))
        pg.wait_for_function("window.L && document.querySelectorAll('.radius button').length >= 3", timeout=20000)
        pg.wait_for_timeout(1500)
        pg.mouse.click(195, 420)
        pg.wait_for_function("window.__mb.visible", timeout=5000)
        pg.wait_for_timeout(800)
        s.shot(pg, "A1c_map_miniapp.png")
        pg.click("#__mb")
        pay = json.loads(pg.evaluate("window.__sent[0]"))
        s.ok({"lat", "lng", "radius"} <= set(pay) and 41.6 < pay["lat"] < 41.85, f"Mini App отправила точку: {pay}")
        ctx.close()
        m = f.mark()
        f.send_web_app_data(u, pay, button_text="🗺 Pick a place on the map")
        summ = with_text(uid, "Your profile", m)
        import core
        prof = core.client("tg", uid)["profile"]
        s.ok(prof.get("pin") and abs(prof["pin"]["lat"] - pay["lat"]) < 1e-9 and prof.get("radius_km") == pay["radius"],
             f"точка и радиус в профиле: {prof.get('pin')}, {prof.get('radius_km')} км")
        s.ok("Your profile" in summ["text"], "бот отвечает по-английски")
        s.chat("A1c_map_english.png", uid, since=m0, subtitle=f"English client · {u['name']} (@{u['username']})")

    with Step("A2a", "Профиль готов (итог анкеты сохранён); клиент присылает фото понравившейся квартиры") as s:
        summary = with_text(cid, "Ваш профиль", S["m_a2a"])
        for want in ("2 человека · 🐕 собака", "6 или 12 мес · 💰 до $900", "1-2 спальни", "Ваке, Сабуртало"):
            s.ok(want in summary["text"], f"в итоге «{want}»")
        import core
        cl = core.client("tg", cid)
        s.eq((cl["profile_done"], cl["profile"]["budget_max"], cl["profile"]["bedrooms"], cl["profile"]["districts"]),
             (1, 900, [1, 2], ["Ваке", "Сабуртало"]), "профиль в БД")
        n = len(ASKS)
        S["m_search"] = m = f.mark()
        f.send_photo(CLIENT, str(BOT / "raw" / "img" / "mh24073265_1.jpg"), caption="Хочу что-то в таком стиле")
        f.wait(lambda _: any("Каталог" in a["prompt"] for a in ASKS[n:]), timeout=40, what="search after the photo")
        s.ok(next(a for a in ASKS[n:] if "Каталог" in a["prompt"])["images"], "фото клиента передано в подбор (ИИ учитывает стиль)")
        s.chat("A2a_profile_photo.png", cid, since=S["m_a2a"])

    with Step("A2b", "Подбор: карточки как в канале (альбом с фото, поля, «почему подходит»), без ссылок и телефонов") as s:
        m = S["m_search"]
        card = find(cid, lambda c: any("ss9101" in (b.get("callback_data") or "") for b in buttons(c))
                    and any(b["text"] == "❤️ Нравится" for b in buttons(c)), m, "card ss9101")
        find(cid, lambda c: any("ss9104" in (b.get("callback_data") or "") for b in buttons(c)), m, "card ss9104")
        albums = f.calls(chat=cid, method="sendMediaGroup", since=m)
        s.ok(len(albums) >= 2 and all(len(a["media"]) == 3 for a in albums[:2]), "у карточек альбомы по 3 фото")
        s.ok("💡" in card["text"] and "t.me" not in card["text"] and "ss.ge" not in card["text"] and "+995" not in card["text"],
             "карточка с «почему подходит», без источника и телефона")
        cat = ASKS[-1]["prompt"].split("Каталог")[-1]
        s.ok("mh9102" not in cat, "объявление «похоже на агента» (на проверке) клиенту не показывается")
        S["card1"] = card
        S["m_like"] = f.mark()
        s.chat("A2b_cards.png", cid, since=m)

    with Step("A3", "❤️ Нравится (клиент ещё не писал оператору) → личка оператора с готовым сообщением; заявка, когда оно пришло; "
                    "карточка отмечает «❤️ Нравится ✅»") as s:
        from urllib.parse import unquote
        from app.telegram import userbot

        def like_dm(card):  # the client taps the ❤️ link and sends the prefilled message; the userbot sees it
            url = next(b["url"] for b in buttons(card) if b["text"] == "❤️ Нравится")
            return url, userbot.on_incoming(CLIENT["id"], CLIENT["username"], unquote(url.split("text=", 1)[1]))
        m = S["m_like"]
        url, res = like_dm(S["card1"])
        s.ok(url.startswith("https://t.me/") and "?text=" in url and res == "liked", "❤️ = ссылка в личку оператора, заявка из первого сообщения")
        grp = with_text(GROUP["id"], "🆕 <b>Заявка #", m)
        S["lead1"] = lead1 = int(re.search(r"Заявка #(\d+)", grp["text"])[1])
        ack = find(cid, lambda c: c["method"] == "editMessageReplyMarkup" and c.get("message_id") == S["card1"]["message_id"], m, "A3 mark")
        # the second card (sent before the client wrote, so a link too): liked as well (lead 2 → B2/B3 in the CRM, B4, A5b)
        S["m_like2"] = m2 = f.mark()
        card2 = find(cid, lambda c: any("ss9104" in (b.get("callback_data") or "") for b in buttons(c))
                     and any(b["text"] == "❤️ Нравится" for b in buttons(c)), S["m_search"], "card ss9104")
        like_dm(card2)
        g2 = find(GROUP["id"], lambda c: "Заявка #" in (c.get("text") or "") and "Пекини" in (c.get("text") or ""), m2, "lead 2")
        S["lead2"] = int(re.search(r"Заявка #(\d+)", g2["text"])[1])
        s.eq(f.markup(ack)[0][0]["text"], "❤️ Нравится ✅", "кнопка")
        r = lead_row(lead1)
        s.eq((r[0], r[1], r[8]), ("new", "operator_take", S.get("p1")), "заявка: статус, таймер 5 мин, связана с объектом воронки 1")
        s.chat("A3_like.png", cid, since=S["m_search"])

    lead1 = S.get("lead1")
    with Step("B1", "Заявка в общей группе операторов; кто первым нажал «Взять», тот ведёт (второй оператор: «Уже взяла Нино»)") as s:
        m = S["m_like"]
        grp = with_text(GROUP["id"], f"Заявка #{lead1}", m)
        s.ok("5 мин на назначение" in grp["text"] and "2 человека" in grp["text"] and "Абашидзе 17" in grp["text"],
             "профиль клиента, объект и таймер 5 мин")
        s.chat("B1_group_new.png", GROUP["id"], since=m, until=S["m_like2"])
        m1 = press(NINO, GROUP["id"], "✋ Взять заявку", group=GROUP, msg=grp)
        taken = with_text(GROUP["id"], "✋ <b>Взяла Нино</b>", m1)
        s.eq(f.markup(taken)[0][0]["text"], "👤 Назначить агента", "кнопка после взятия")
        m2 = f.mark()
        f.press(MARIAM, grp, "✋ Взять заявку", group=GROUP)
        alert = f.wait(lambda calls: next((c for c in calls if c["method"] == "answerCallbackQuery"), None), since=m2)
        s.eq(alert.get("text"), "Уже взяла Нино", "ответ второму оператору")
        s.eq(lead_row(lead1)[0], "operator_taken", "статус заявки")
        S["m_b3"] = f.mark()
        s.chat("B1_group_taken.png", GROUP["id"], since=m, until=S["m_b3"])

    with Step("B3", "Назначение агента из группы: список с «рекомендуем» (его объект, район, загрузка) → заявка агенту") as s:
        m = press(NINO, GROUP["id"], "👤 Назначить агента", group=GROUP, since=S["m_like"])
        lst = with_button(GROUP["id"], "⭐ Георгий", m, prefix=True)
        s.ok(any("Левана" in b["text"] or "Леван" in b["text"] for b in buttons(lst)), "в списке есть и другие агенты")
        S["m_c6"] = m = press(NINO, GROUP["id"], "⭐ Георгий", group=GROUP, since=m, prefix=True)
        with_text(GROUP["id"], "Агент: <b>Георгий</b>", m)
        r = lead_row(lead1)
        s.eq((r[0], r[1], r[2]), ("agent_assigned", "agent_slots", user_id("georgiy")), "статус, таймер агента 30 мин, агент")
        rec = api("nino", "GET", f"/leads/{lead1}/recommend-agents").json()
        s.ok(rec[0]["agent"]["name"] == "Георгий" and "его объект" in rec[0]["reasons"], f"рекомендация: {rec[0]['reasons']}")
        s.chat("B3_group_assign.png", GROUP["id"], since=S["m_b3"])

    with Step("C6", "Агенту пришла заявка: объект + профиль клиента, без имени и телефона, таймер 30 мин") as s:
        c6 = with_text(GEORGIY["id"], f"Заявка #{lead1}", S["m_c6"])
        S["c7_url"] = btn_url(c6, "🕐 Предложить время", "web_app")
        s.ok("🔒 Контакты клиента бот пришлёт сам" in c6["text"] and "30 мин" in c6["text"], "текст C6")
        s.ok("anna_client" not in c6["text"] and "Анна" not in c6["text"], "нет имени и username клиента")
        s.ok("ваш объект" in c6["text"], "отмечено «ваш объект» (объект из воронки 1)")
        S["c7_url"] = btn_url(c6, "🕐 Предложить время", "web_app")
        s.eq(S["c7_url"], f"{PUBLIC}/m/leads/{lead1}", "кнопка открывает заявку в Mini App")
        r = api("georgiy", "GET", f"/leads/{lead1}").json()
        s.ok(r["client"]["name"] is None and r["client"]["username"] is None and not r["contacts_visible"], "API тоже скрывает контакты")
        s.chat("C6_agent_new_lead.png", GEORGIY["id"], since=S["m_c6"])

    with Step("C7", "Агент вводит свободное время в Mini App (по ссылке из бота) → бот сам отправляет варианты клиенту") as s:
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, local(S["c7_url"]), "Свободное время для просмотра", s)
        s.ok(pg.get_by_text("anna_client").count() == 0, "в Mini App контактов клиента нет")
        click(pg, "12:00", exact=True)
        click(pg, "16:00", exact=True)
        s.shot(pg, "C7_miniapp_slots.png")
        S["m_a4a"] = m = f.mark()
        click(pg, "Отправить клиенту · 2")
        toast(pg, "Бот отправил варианты клиенту")
        r = lead_row(lead1)
        s.eq((r[0], r[1]), ("slots_sent", None), "статус slots_sent, таймер агента остановлен")
        s.shot(pg, "C7_miniapp_sent.png")
        ctx.close()

    with Step("A4a", "Клиенту одно сообщение: фото квартиры, «Ваке · 2 спальни · $850», кнопки времени и «Предложить своё время»") as s:
        slots = with_text(cid, "Выберите удобное время", S["m_a4a"])
        s.eq(slots["method"], "sendPhoto", "сообщение с фото")
        labels = [b["text"] for b in buttons(slots)]
        s.ok(any("12:00" in x for x in labels) and any("16:00" in x for x in labels), f"кнопки времени {labels}")
        s.eq(labels[-1], "🕐 Предложить своё время", "последняя кнопка")
        s.ok("Ваке" in slots["caption"] and "$850" in slots["caption"], "подпись: район и цена")
        S["slots_msg"] = slots
        s.chat("A4a_pick_time.png", cid, since=S["m_a4a"])

    with Step("A4b", "Клиент нажал время → подтверждение: адрес, агент и телефон, «Написать агенту», «Добавить в календарь» (.ics)") as s:
        t12 = next(b["text"] for b in buttons(S["slots_msg"]) if "12:00" in b["text"])
        S["m_c8"] = m = press(CLIENT, cid, t12, msg=S["slots_msg"])
        conf = with_text(cid, "✅ <b>Просмотр:", m)
        s.ok("Ваш агент: <b>Георгий</b>" in conf["text"] and "+995 599 11 22 33" in conf["text"], "агент и телефон")
        s.eq(f.markup(conf)[0][0]["url"], "https://t.me/georgiy_agent", "«Написать агенту в Telegram»")
        ics = btn_url(conf, "📅 Добавить в календарь")
        import httpx
        body = httpx.get(local(ics)).text
        s.ok(ics.startswith(PUBLIC) and "BEGIN:VCALENDAR" in body and "BEGIN:VEVENT" in body, "календарь: .ics с событием")
        find(cid, lambda c: c["method"] == "editMessageReplyMarkup" and f.markup(c)[0][0]["text"] == "✓ " + t12, m, "✓ slot")
        s.eq(lead_row(lead1)[0], "time_chosen", "статус заявки")
        s.chat("A4b_confirmed.png", cid, since=S["m_a4a"])

    with Step("C8", "Сразу после выбора времени бот сам прислал агенту контакты клиента") as s:
        c8 = with_text(GEORGIY["id"], "🔓 <b>Контакты клиента</b>", S["m_c8"])
        s.ok("@anna_client" in c8["text"] and "Клиент выбрал" in c8["text"], "контакты и время")
        r = api("georgiy", "GET", f"/leads/{lead1}").json()
        s.ok(r["contacts_visible"] and r["client"]["username"] == "anna_client", "в API контакты теперь видны агенту")
        s.chat("C8_contacts_to_agent.png", GEORGIY["id"], since=S["m_a4a"])

    with Step("B5", "История заявки в CRM: «Бот передал контакты агенту», все шаги со временем; оператор ничего не нажимал") as s:
        hist = api("nino", "GET", f"/leads/{lead1}").json()["history"]
        acts = [h["action"] for h in hist]
        s.ok({"created", "operator_taken", "agent_assigned", "slots_sent", "time_chosen", "contacts_shared"} <= set(acts), f"события: {acts}")
        s.ok(any((h.get("data") or {}).get("note") == "Бот передал контакты агенту" for h in hist), "строка «Бот передал контакты агенту»")
        ctx = desktop("nino")
        pg = open_page(ctx, f"/leads/{lead1}", "Лог заявки", s)
        pg.get_by_text("Бот передал контакты агенту").first.wait_for()
        s.shot(pg, "B5_lead_history_crm.png")
        ctx.close()

    with Step("A4b", "Напоминание за 1 час клиенту и агенту (worker viewing_reminder); «👍 Буду» → агент видит сразу") as s:
        from app.models import Job, Lead
        from sqlalchemy import update

        def soon(db):  # time travel: the viewing is in 59.5 min; its reminder job is due
            v = datetime.now(timezone.utc) + timedelta(minutes=59, seconds=30)
            db.execute(update(Lead).where(Lead.id == lead1).values(viewing_at=v))
            db.execute(update(Job).where(Job.kind == "viewing_reminder", Job.ref == str(lead1), Job.done_at.is_(None))
                       .values(due_at=v - timedelta(minutes=60)))
        m = f.mark()
        s.eq(fire("viewing_reminder", lead1, before=soon), "ok", "задача worker viewing_reminder")
        rem = with_text(cid, "Через час просмотр", m)
        with_text(GEORGIY["id"], f"Через час просмотр #{lead1}", m)
        m2 = press(CLIENT, cid, "👍 Буду", msg=rem)
        with_text(GEORGIY["id"], "подтвердил просмотр", m2)
        s.chat("A4b_reminder_client.png", cid, since=m)
        s.chat("A4b_reminder_agent.png", GEORGIY["id"], since=m)

    with Step("A5a", "После просмотра бот спрашивает клиента (worker post_viewing_survey): «Снимаю ✅»") as s:
        S["m_survey"] = m = f.mark()
        s.eq(fire("post_viewing_survey", lead1), "ok", "задача worker post_viewing_survey")
        with_text(cid, "как прошёл просмотр квартиры", m)
        m2 = press(CLIENT, cid, "Снимаю ✅", since=m)
        with_text(cid, "Отлично, поздравляем", m2)
        s.eq(lead_row(lead1)[4], "rented", "ответ клиента записан в заявку")
        s.chat("A5a_survey_client.png", cid, since=m)

    with Step("C9a", "Итог агента «Сдано ✅» → сразу ссылка на форму сделки (Mini App)") as s:
        m = press(GEORGIY, GEORGIY["id"], "Сдано ✅", since=S["m_survey"])
        form = with_text(GEORGIY["id"], "Заполните сделку", m)
        S["deal_url"] = btn_url(form, "", "web_app") or next((b.get("web_app") or {}).get("url") for b in buttons(form) if b.get("web_app"))
        s.eq(S["deal_url"], f"{PUBLIC}/m/deals/new?lead={lead1}", "кнопка формы сделки")
        r = lead_row(lead1)
        s.eq((r[0], r[4], r[5]), ("deal_pending", "rented", "rented"), "статус и ответы клиента/агента")
        s.chat("C9a_outcome_agent.png", GEORGIY["id"], since=S["m_survey"])

    with Step("C9b", "Форма сделки в Mini App: поля из заявки, комиссия и доля агента считаются сразу по уровню; T0") as s:
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, local(S["deal_url"]), f"По заявке #{lead1}", s)
        pg.wait_for_function("document.querySelector('.calc-big') && !document.querySelector('.calc-big').textContent.includes('—')",
                             timeout=15000)
        s.eq(pg.get_by_label("Цена аренды, $/мес").input_value(), "850", "цена подставлена из заявки")
        s.eq(pg.get_by_label("Telegram клиента").input_value(), "@anna_client", "контакты клиента подставлены")
        pg.get_by_label("Аренда до").fill((date.today() + timedelta(days=365)).isoformat())
        pg.get_by_label("Собственник", exact=True).fill("Нана Кобахидзе")
        for k in range(3):
            pg.locator(".seg").filter(has=pg.get_by_role("radio", name="0", exact=True)).nth(k).get_by_role("radio", name="3", exact=True).click()
        pg.wait_for_timeout(700)
        share = pg.locator(".calc-big").inner_text()
        s.ok(re.search(r"29[78]", share), f"доля агента 70% от $425 = $297.5 на экране: {share!r}")
        s.shot(pg, "C9b_deal_form.png")
        click(pg, "Отправить на подтверждение")
        pg.wait_for_url(re.compile(r"/m/deals/\d+"), timeout=15000)
        S["deal"] = deal = int(pg.url.rsplit("/", 1)[-1])
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(500)
        s.shot(pg, "C9b_deal_sent.png")
        d = api("nino", "GET", f"/deals/{deal}").json()
        s.eq((d["status"], float(d["commission_total"]), float(d["agent_amount"]), float(d["platform_amount"])),
             ("pending", 425.0, 297.5, 127.5), "сделка: статус, комиссия, доля агента 70%, платформа 30%")
        ev = api("nino", "GET", "/evaluations", params={"deal_id": deal}).json()
        ev = ev["items"] if isinstance(ev, dict) else ev
        s.ok(any(e.get("kind") == "t0" and e.get("h2o_client") == 3 for e in ev), "оценка T0 сохранена")
        ctx.close()

    with Step("B6", "Оператор видит рядом итог агента и ответ клиента и жмёт «Подтвердить сделку» (CRM)") as s:
        ctx = desktop("nino")
        pg = open_page(ctx, f"/leads/{lead1}", "Итог: агент и клиент", s)
        s.ok(pg.locator(".outcome-card").count() == 2, "два ответа рядом")
        s.ok(pg.get_by_text("Итог агента: Снимает").count() > 0 and pg.get_by_text("deal_created").count() == 0,
             "лог заявки по-русски («Итог агента: Снимает», без сырых имён событий)")
        s.shot(pg, "B6_outcomes_crm.png")
        S["m_c9c"] = f.mark()
        click(pg, "Подтвердить сделку")
        toast(pg, "Сделка подтверждена")
        pg.wait_for_timeout(800)
        s.shot(pg, "B6_confirmed_crm.png")
        s.eq(api("nino", "GET", f"/deals/{S['deal']}").json()["status"], "confirmed", "сделка подтверждена")
        s.eq(lead_row(lead1)[0], "rented", "заявка закрыта: rented")
        s.eq(sql("SELECT status FROM properties WHERE id = :p", {"p": S["p1"]})[0][0], "rented", "объект воронки 1 → rented")
        post = with_text(WORK["id"], "#Rented", S["m_c9c"])
        s.eq(str(post.get("message_thread_id")), "7", "#Rented в топике «Сделки» рабочей группы")
        s.chat("B6_rented_post.png", WORK["id"], since=S["m_c9c"])
        jobs = {r[0] for r in sql("SELECT kind FROM jobs WHERE done_at IS NULL AND ref LIKE :r", {"r": f"{S['deal']}%"})}
        s.ok({"t14_client", "t14_agent", "contract_expiry"} <= jobs, f"поставлены T14 и напоминания о конце договора: {sorted(jobs)}")
        ctx.close()

    with Step("C9c", "Агенту итог: сделка подтверждена, доля, T14 и напоминания о конце договора (30/7/1)") as s:
        msg = with_text(GEORGIY["id"], "Сделка подтверждена", S["m_c9c"])
        s.ok(re.search(r"29[78]", msg["text"]) and "за 30, 7 и 1 день" in msg["text"], "доля и напоминания в тексте")
        m = f.mark()
        s.eq(fire("contract_expiry", f"{S['deal']}:30"), "ok", "worker contract_expiry (30 дней)")
        with_text(GEORGIY["id"], "Через <b>30 дн.</b> заканчивается договор", m)
        s.chat("C9c_agent_confirmed.png", GEORGIY["id"], since=S["m_c9c"])

    with Step("A6", "T14 через 14 дней: три вопроса клиенту; низкая оценка и «Есть проблема» → сразу операторам") as s:
        m = f.mark()
        s.eq(fire("t14_client", S["deal"]), "ok", "worker t14_client")
        with_text(cid, "вы живёте в новой квартире 14 дней", m)
        m1 = press(CLIENT, cid, "3", since=m)
        m1 = press(CLIENT, cid, "3", since=m1)
        m1 = press(CLIENT, cid, "1", since=m1)
        with_button(cid, "✍️ Есть проблема", m1)
        alert = with_text(GROUP["id"], f"Сигнал по сделке #{S['deal']}", m1)
        m2 = press(CLIENT, cid, "✍️ Есть проблема", since=m1)
        say(CLIENT, "Не работает бойлер")
        with_text(cid, "Передал оператору", m2)
        ev = sql("SELECT author, h2o_client, h2o_owner, h2o_process, complaints, complaints_text FROM evaluations "
                 "WHERE deal_id = :d AND kind = 't14' ORDER BY id", {"d": S["deal"]})
        s.eq([tuple(r) for r in ev], [("client", 3, 3, 1, False, None), ("client", None, None, None, True, "Не работает бойлер")],
             "оценки H2O и жалоба сохранены")
        s.chat("A6_t14_client.png", cid, since=m)
        s.chat("A6_t14_alert_group.png", GROUP["id"], since=m1)
        s.ok(alert is not None, "сигнал в группе операторов")


def lead2_flow():
    """Lead 2 (the second ❤️): B2 board, B3 assign dialog in the CRM, B4 SLA, then «Не подошло» → similar (A5b)."""
    f, cid, lead2 = FAKE, CLIENT["id"], S.get("lead2")

    with Step("B4", "Сроки: никто не взял за 5 мин → напоминание в группе, потом эскалация; красный таймер на доске") as s:
        m = f.mark()
        s.eq(fire("lead_sla", lead2), "ok", "worker lead_sla (warning)")
        warn = with_text(GROUP["id"], "⚠️", m)
        s.ok(f"Заявка #{lead2}" in warn["text"] and "на назначение агента" in warn["text"], "напоминание операторам")

        def overdue(db):
            from app.models import Lead
            from sqlalchemy import update
            db.execute(update(Lead).where(Lead.id == lead2).values(sla_due_at=datetime.now(timezone.utc) - timedelta(minutes=3)))
        s.eq(fire("lead_sla", lead2, before=overdue), "ok", "worker lead_sla (overdue)")
        esc = with_text(GROUP["id"], "никто не взял за 5 минут", m)
        s.ok(any(b["text"] == "✋ Взять заявку" for b in buttons(esc)), "в эскалации кнопка «Взять заявку»")
        s.ok(sql("SELECT count(*) FROM events WHERE entity = 'lead' AND entity_id = :i AND action = 'sla_overdue'", {"i": str(lead2)})[0][0],
             "просрочка записана в историю заявки")
        s.chat("B4_group_escalation.png", GROUP["id"], since=m)
        card = next(c for c in api("nino", "GET", "/leads/board").json()["new"] if c["id"] == lead2)
        s.eq(card["sla"]["color"], "red", "цвет таймера на карточке")

    with Step("B2", "Панель оператора: все заявки по статусам, таймер на карточке (CRM на компьютере и Mini App)") as s:
        board = api("nino", "GET", "/leads/board").json()
        s.ok(any(c["id"] == lead2 for c in board["new"]) and any(c["id"] == S["lead1"] for c in board["rented"]),
             "заявка 2 в «Новая», заявка 1 в «Сдано»")
        colors = {c["sla"]["color"] for col in board.values() for c in col if c["sla"]["color"]}
        s.ok({"red", "green"} <= colors, f"таймеры разных цветов: {sorted(colors)}")
        ctx = desktop("nino")
        pg = open_page(ctx, "/leads", f"#{lead2}", s)
        chips = {t: pg.locator(f".sla-{t}").count() for t in ("green", "yellow", "red")}
        s.ok(chips["red"] > 0 and chips["green"] + chips["yellow"] > 0, f"таймеры на карточках доски: {chips}")
        s.shot(pg, "B2_board_crm.png")
        ctx.close()
        ctx = miniapp(NINO)
        pg = open_page(ctx, "/m/leads", f"#{lead2}", s)
        s.shot(pg, "B2_board_miniapp.png")
        ctx.close()

    with Step("B3", "CRM: оператор берёт заявку и назначает агента в диалоге с «рекомендуем» → агенту C6") as s:
        ctx = desktop("nino")
        pg = open_page(ctx, f"/leads/{lead2}", "Взять", s)
        click(pg, "Взять", exact=True)
        toast(pg, "Заявка ваша")
        click(pg, "Назначить агента")
        dlg = pg.get_by_role("dialog")
        dlg.get_by_text("рекомендуем").wait_for()
        s.ok(dlg.get_by_text("заявок в работе").count() > 0, "в диалоге районы и загрузка агентов")
        s.shot(pg, "B3_assign_dialog_crm.png")
        m = f.mark()
        dlg.locator(".list-row", has_text="Георгий").get_by_role("button", name="Назначить").click()
        toast(pg, "Заявка отправлена агенту")
        r = lead_row(lead2)
        s.eq((r[0], r[2], r[3]), ("agent_assigned", user_id("georgiy"), user_id("nino")), "статус, агент, оператор")
        with_text(GEORGIY["id"], f"Заявка #{lead2}", m)
        S["m_b4b"] = m
        ctx.close()

    with Step("B4", "Сроки агента: напоминание агенту, потом эскалация операторам «Переназначить?»") as s:
        m = S["m_b4b"]
        s.eq(fire("lead_sla", lead2), "ok", "worker lead_sla (warning агенту)")
        with_text(GEORGIY["id"], "чтобы предложить время просмотра", m)

        def overdue(db):
            from app.models import Lead
            from sqlalchemy import update
            db.execute(update(Lead).where(Lead.id == lead2).values(sla_due_at=datetime.now(timezone.utc) - timedelta(minutes=2)))
        s.eq(fire("lead_sla", lead2, before=overdue), "ok", "worker lead_sla (overdue)")
        esc = with_text(GROUP["id"], "минут не предлагает время просмотра", m)
        s.ok(any(b["text"] == "🔁 Переназначить" for b in buttons(esc)), "кнопка «Переназначить»")
        s.chat("B4_agent_reminder.png", GEORGIY["id"], since=m)
        s.chat("B4_group_agent_overdue.png", GROUP["id"], since=m)

    with Step("A5b", "Не подошло: клиент выбирает причину «дорого» → бот сразу предлагает похожие (дешевле)") as s:
        m = press(GEORGIY, GEORGIY["id"], "⚡ Время кнопками", since=S["m_b4b"])
        quick = with_button(GEORGIY["id"], "📤 Отправить клиенту", m, prefix=True)
        t = next(b["text"] for b in buttons(quick) if (b.get("callback_data") or "").startswith("ls:t:"))
        m = press(GEORGIY, GEORGIY["id"], t, since=m)
        m = press(GEORGIY, GEORGIY["id"], "📤 Отправить клиенту · 1", since=m)
        slots = with_text(cid, "Выберите удобное время", m)
        m = press(CLIENT, cid, next(b["text"] for b in buttons(slots) if (b.get("callback_data") or "").startswith("ls:pick:")), msg=slots)
        with_text(cid, "✅ <b>Просмотр:", m)
        m0 = f.mark()
        s.eq(fire("post_viewing_survey", lead2), "ok", "worker post_viewing_survey")
        survey = find(cid, lambda c: any(b.get("callback_data") == f"lo:c:{lead2}:refused" for b in buttons(c)), m0, "survey 2")
        m = press(CLIENT, cid, "Не подошло ❌", msg=survey)
        with_text(cid, "Что не подошло?", m)
        m, label = press_data(CLIENT, cid, lambda d: d == f"lr:{lead2}:price", since=m)
        with_text(cid, "Подобрать похожие квартиры дешевле?", m)
        r = lead_row(lead2)
        s.eq((r[0], r[4], r[6]), ("refused", "refused", "price"), "заявка: отказ клиента и причина сохранены")
        m = press(CLIENT, cid, "🔄 Да, подобрать", since=m)
        card = find(cid, lambda c: any("ss9105" in (b.get("callback_data") or "") for b in buttons(c)), m, "similar card ss9105")
        s.ok("Нуцубидзе" in card["text"] or "650" in card["text"], "похожий вариант дешевле: $650")
        s.ok("хочу дешевле" in ASKS[-1]["prompt"] and "Клиент отверг" in ASKS[-1]["prompt"], "причина отказа учтена в подборе")
        s.chat("A5b_not_suitable_similar.png", cid, since=m0)


def calendar():
    with Step("CAL", "Календарь: просмотры и съёмки (CRM оператора и Mini App агента)") as s:
        ctx = desktop("nino")
        pg = open_page(ctx, "/calendar", None, s)
        pg.wait_for_timeout(1200)
        s.shot(pg, "CAL_calendar_crm.png")
        ctx.close()
        ctx = miniapp(GEORGIY)
        pg = open_page(ctx, "/m/calendar", None, s)
        pg.wait_for_timeout(1200)
        s.shot(pg, "CAL_calendar_miniapp.png")
        ctx.close()
        s.ok(True, "страницы календаря открываются")


# ============================================================ report

def report(elapsed):
    (OUT / "results.json").write_text(json.dumps({"run_at": datetime.now().isoformat(timespec="seconds"), "seconds": round(elapsed),
                                                  "database": DB_NAME, "steps": RESULTS}, ensure_ascii=False, indent=1))
    ok = sum(r["status"] == "PASS" for r in RESULTS)
    lines = ["# User journey proof", "",
             f"Run {datetime.now():%d.%m.%Y %H:%M}: **{ok}/{len(RESULTS)} PASS**. Rerun: `.venv/bin/python bot/test/e2e/journey_proof.py` "
             "(isolated DB `homes_e2e`, fake Bot API, backend on :8010; see the script's docstring).", "",
             "| Step | What was checked | Result | Screenshots |", "|---|---|---|---|"]
    for r in RESULTS:
        shots = " ".join(f"[{n}]({n})" for n in r["screenshots"])
        res = r["status"] if not r["error"] else f"FAIL: `{r['error'][:300]}`"
        lines.append(f"| {r['id']} | {r['what']} | {res} | {shots} |")
    lines += ["", "## Checks per step", ""]
    for r in RESULTS:
        lines.append(f"### {r['id']} · {r['status']}\n{r['what']}\n")
        lines += [f"- {c}" for c in r["checks"]]
        if r["error"]:
            lines.append(f"- **FAIL:** `{r['error']}`")
        lines += [""] + [f"![{n}]({n})" for n in r["screenshots"]] + [""]
    (OUT / "README.md").write_text("\n".join(lines))
    print(f"\n{ok}/{len(RESULTS)} PASS")
    for r in RESULTS:
        print(f"{r['status']:4} {r['id']:4} {r['what'][:90]}" + (f"\n      {r['error']}" if r["error"] else ""))


def main():
    t0 = time.time()
    server, stop, th = start()
    try:
        only = os.environ.get("JP_PARTS")  # debugging: JP_PARTS=funnel2,lead2_flow runs only these blocks
        for part in (funnel1, funnel2, lead2_flow, calendar):
            if only and part.__name__ not in only.split(","):
                continue
            try:
                part()
            except Exception as e:  # noqa: BLE001  (a step outside `with Step` broke: record it, keep going)
                RESULTS.append({"id": part.__name__, "what": "блок шагов", "status": "FAIL", "checks": [], "screenshots": [],
                                "error": f"{type(e).__name__}: {e}"})
                traceback.print_exc()
    finally:
        report(time.time() - t0)
        SHOOTER.close()
        stop.set()
        th.join(15)
        server.should_exit = True
        FAKE.server.shutdown()
        for p in set((BOT / "data" / "client").glob(f"{CLIENT['id']}_*")) - S.get("client_img_before", set()):
            p.unlink()  # the photo the client "sent" (bot.py saves client photos to bot/data/client, not DATA_DIR)
    sys.exit(0 if all(r["status"] == "PASS" for r in RESULTS) else 1)


if __name__ == "__main__":
    main()
