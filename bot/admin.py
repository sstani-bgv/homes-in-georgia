"""DEPRECATED (stage 2): superseded by the React CRM + FastAPI backend (backend/, frontend/). Not ported to Postgres:
it still uses the old SQLite-era SQL (agents table, legacy lead statuses) and will not run against the shared DB.
Still useful as a reference: /map (webapp/index.html, the clients' map Mini App) and /journey must be served by the
backend now (PUBLIC_URL/map is what the bot links to). See docs/README.md.

Web admin for the agency (TZ 3.8) and the agents' Telegram Mini App: leads, review, listings, sources, agents,
settings, statistics. Stdlib http.server + server-rendered HTML; same data and rules as the bot (data/app.db + core.py).

Two ways in:
  * desktop browser: password (ADMIN_PASSWORD), wide layout;
  * inside Telegram (menu button / "📋 Панель агента" / "📋 Открыть в панели"): /tg reads Telegram.WebApp.initData,
    the server checks its HMAC with BOT_TOKEN, and lets in active agents (and ADMIN_TG_IDS); phone layout with tabs.

Run:  .venv/bin/python admin.py        # http://127.0.0.1:8080
Env:  ADMIN_PASSWORD, BOT_TOKEN (at least one), ADMIN_TG_IDS, AGENT_APP_URL, ADMIN_HOST (127.0.0.1), ADMIN_PORT (8080),
      ADMIN_SECURE_COOKIE=1 (forced Secure cookies; also automatic when X-Forwarded-Proto is https), TG_AUTH_MAX_AGE,
      APP_DB (data/app.db), PHOTO_DIR (raw/img: the only folder photos are served from), TG_API_BASE (tests only),
      JOURNEY_NOTIFY_CHAT (comma-separated chat ids: who gets the answers sent from the /journey mockups page)

Public, no login: /map (clients' map Mini App), /journey (docs/user-journey/index.html) and POST /journey/answers.
"""
import functools
import hashlib
import hmac
import html
import io
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

import core
import db
import sheets

ROOT = Path(__file__).resolve().parent
PHOTO_DIR = ROOT / "raw" / "img"
COOKIE = "rit_admin"
SESSION_DAYS = 7
TG_SESSION = 12 * 3600
PAGE = 50
BACKGROUND = True  # Telegram card edits in a thread (tests switch it off)

LISTING_STATUS = {"published": "Опубликовано", "pending_review": "На проверке", "rejected": "Отклонено", "gone": "Снято"}
LEAD_STATUS = sheets.LEAD_STATUS
AGENT_STATUSES = ("in_work", "viewing", "rented", "refused")  # the same buttons as on the lead card in Telegram
JOURNEY_PAGE = ROOT / "docs" / "user-journey" / "index.html"
JOURNEY_MAX_BYTES = 50_000
JOURNEY_PER_HOUR = 20  # answers per sender (ip hash) per hour
SOURCE_KINDS = {"tg_competitor": "Telegram-канал конкурента", "tg_agency": "Telegram-канал агентства", "fb_group": "Facebook-группа",
                "ig_account": "Instagram-аккаунт"}


def e(v):
    return html.escape("" if v is None else str(v))


# ---------- auth: stateless signed cookie "<expiry>.<who>.<hmac>", who = pw (password) | tg<telegram id> ----------

def password():
    return os.environ.get("ADMIN_PASSWORD", "")


def bot_token():
    return os.environ.get("BOT_TOKEN", "")


def admin_tg_ids():
    return {int(x) for x in re.findall(r"\d+", os.environ.get("ADMIN_TG_IDS", ""))}


def _sign(exp, who):
    # changing the password or the bot token logs everyone out
    key = hashlib.sha256(("rit-admin-session:" + password() + "\0" + bot_token()).encode()).digest()
    return hmac.new(key, f"{exp}.{who}".encode(), "sha256").hexdigest()


def make_token(now=None, who="pw"):
    life = SESSION_DAYS * 86400 if who == "pw" else TG_SESSION
    exp = int((time.time() if now is None else now) + life)
    return f"{exp}.{who}.{_sign(exp, who)}"


def valid_token(token, now=None):
    """-> "pw" / "tg<id>" for a good unexpired token, else None."""
    try:
        exp, who, sig = token.split(".")
        exp = int(exp)
    except (AttributeError, ValueError):
        return None
    if not re.fullmatch(r"pw|tg\d{1,15}", who) or not (password() if who == "pw" else bot_token()):
        return None
    ok = exp > (time.time() if now is None else now) and hmac.compare_digest(sig, _sign(exp, who))
    return who if ok else None


def check_password(p):
    return bool(password()) and hmac.compare_digest((p or "").encode(), password().encode())


def init_data_hash(fields, token):
    """Telegram Mini App initData hash: HMAC_SHA256(data_check_string, HMAC_SHA256("WebAppData", bot_token))."""
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    return hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()


def check_init_data(init_data, token=None, max_age=None, now=None):
    """Validate Telegram.WebApp.initData (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)
    -> the Telegram user dict, or None if forged, tampered, stale or without a user."""
    token = token if token is not None else bot_token()
    max_age = int(os.environ.get("TG_AUTH_MAX_AGE", 86400)) if max_age is None else max_age
    if not token or not init_data or len(init_data) > 8000:
        return None
    pairs = parse_qsl(init_data, keep_blank_values=True)
    fields = dict(pairs)
    if len(fields) != len(pairs):  # repeated keys: ambiguous, refuse
        return None
    sig = fields.pop("hash", "")
    if not re.fullmatch(r"[0-9a-f]{64}", sig) or not hmac.compare_digest(init_data_hash(fields, token), sig):
        return None
    now = time.time() if now is None else now
    try:
        auth_date, user = int(fields.get("auth_date", "")), json.loads(fields.get("user") or "null")
    except ValueError:
        return None
    if not now - max_age <= auth_date <= now + 60 or not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return user


def viewer(who):
    """Who is looking: {"kind": admin|agent, "agent": row|None, "tg": bool, "name"} or None (no access any more)."""
    if who == "pw":
        return {"kind": "admin", "agent": None, "tg": False, "name": "Администратор"}
    tg_id = int(who[2:])
    with db.tx() as c:
        a = c.execute("SELECT * FROM agents WHERE tg_id = ? AND active = 1", (tg_id,)).fetchone()
    a = dict(a) if a else None
    if not a and tg_id not in admin_tg_ids():
        return None
    name = a and (a["name"] or (a["username"] and "@" + a["username"])) or f"id {tg_id}"
    return {"kind": "agent" if a else "admin", "agent": a, "tg": True, "tg_id": tg_id, "name": name}


def safe_next(path, default="/"):
    """Only local paths: no scheme, host or protocol-relative //evil."""
    path = path or ""
    return path if re.fullmatch(r"/(?![/\\])[\w\-./?=&%]*", path) else default


# ---------- Telegram: keep the agents' lead / review cards in chats in sync with decisions made here ----------

def tg_call(method, **params):
    token = bot_token()
    if not token:
        return None
    base = os.environ.get("TG_API_BASE", "https://api.telegram.org")
    data = urlencode({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in params.items()}).encode()
    with urllib.request.urlopen(f"{base}/bot{token}/{method}", data, timeout=15) as r:
        return json.loads(r.read())


def later(fn, *args):
    if BACKGROUND:
        threading.Thread(target=fn, args=args, daemon=True).start()
    else:
        fn(*args)


def _edit_cards(kind, ref, text, markup):
    for chat, mid in core.messages(kind, ref):
        p = {"chat_id": chat, "message_id": mid, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        kb = markup(chat) if callable(markup) else markup
        try:
            tg_call("editMessageText", **p, **({"reply_markup": kb} if kb else {}))
        except Exception as ex:  # "message is not modified", deleted, network: never break the panel
            print("telegram edit", kind, ref, chat, "failed:", ex, file=sys.stderr)


def push_lead(ld):
    later(_edit_cards, "lead", ld["id"], core.lead_text(ld),
          lambda chat: core.lead_keyboard(ld, core.app_url(), private=chat > 0))


def push_review(i, approve, who):
    verdict = "✅ Опубликовано" if approve else "❌ Отклонено"
    later(_edit_cards, "review", i["id"], core.review_card(i, f"\n\n<b>{verdict}</b> · {html.escape(who)}"), None)


# ---------- photos: by listing id + index only, and only files inside PHOTO_DIR ----------

def photo_file(lid, n):
    i = core.listing(lid)
    if not i or not 0 <= n < len(i["photos"]):
        return None
    root, p = PHOTO_DIR.resolve(), Path(i["photos"][n])
    # stored paths may be absolute paths of another checkout: fall back to the same file name in PHOTO_DIR
    for cand in (p if p.is_absolute() else ROOT / p, root / p.name):
        r = cand.resolve()
        if r.is_relative_to(root) and r.is_file() and r.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return r
    return None


@functools.lru_cache(maxsize=1024)
def thumbnail(path, mtime):
    from PIL import Image
    im = Image.open(path)
    im.thumbnail((480, 480))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def thumb(lid, n=0, cls="thumb"):
    return f'<img class="{cls}" loading="lazy" src="/photo/{e(lid)}/{n}" alt="" onerror="this.style.visibility=\'hidden\'">'


# ---------- layout ----------

CSS = """
:root{--blue:#032D6A;--deep:#021F4A;--red:#E3172D;--grey:#F3F5F9;--muted:#4A5A75;--line:#E1E6EF}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;font:400 14px/1.5 Montserrat,'Noto Sans Georgian',system-ui,sans-serif;color:var(--deep);background:#fff}
a{color:var(--blue)}
.wrap{max-width:1440px;margin:0 auto;padding-left:28px;padding-right:28px}
header{background:linear-gradient(180deg,var(--blue),var(--deep));color:#fff;padding-top:18px}
.top{display:flex;align-items:center;gap:16px}
.brand{font-weight:900;font-size:22px;text-transform:uppercase;letter-spacing:.01em}
.brand span{font-weight:700;font-size:11px;letter-spacing:.14em;opacity:.65;margin-left:10px}
.top form{margin-left:auto}
nav.desk{display:flex;gap:2px;margin-top:14px;flex-wrap:wrap}
nav.desk a{color:#fff;opacity:.72;text-decoration:none;font-weight:700;font-size:12px;letter-spacing:.14em;
  text-transform:uppercase;padding:11px 14px;border-radius:10px 10px 0 0}
nav.desk a:hover{opacity:1}
nav.desk a.on{background:#fff;color:var(--blue);opacity:1}
main{padding-top:26px;padding-bottom:60px}
h1{font-weight:900;text-transform:uppercase;color:var(--blue);font-size:30px;line-height:1.1;margin:0 0 4px}
h2{font-weight:800;color:var(--blue);font-size:18px;margin:28px 0 10px}
.sub{color:var(--muted);margin:0 0 20px}
.label,th{font-weight:700;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.muted{color:var(--muted)} .small{font-size:12px} .nowrap{white-space:nowrap}
.badge{display:inline-block;font-weight:700;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  padding:3px 8px;border-radius:4px;background:var(--grey);color:var(--blue);white-space:nowrap}
.badge.blue{background:var(--blue);color:#fff} .badge.red{background:var(--red);color:#fff}
nav .badge{margin-left:6px;padding:1px 6px}
.card{background:var(--grey);border-radius:14px;padding:18px}
.card .badge:not(.blue):not(.red),a.lead .badge:not(.blue):not(.red){background:#fff}
form.settings label.label{display:flex;flex-direction:column;gap:6px}
form.settings label.label input{align-self:flex-start;min-width:200px}
.tablewrap{width:100%;overflow-x:auto}
table{border-collapse:collapse;width:100%}
th{text-align:left;padding:8px;border-bottom:2px solid var(--blue)}
td{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}
td.wrapall{overflow-wrap:anywhere;word-break:break-word;min-width:140px}
td.acts .v{display:flex;gap:6px;flex-wrap:wrap}
tr:hover td{background:#FAFBFD}
img.thumb{width:104px;height:78px;object-fit:cover;border-radius:8px;background:var(--grey);display:block}
img.big{width:220px;height:165px;object-fit:cover;border-radius:10px;background:#fff;flex:0 0 auto}
button,.btn{font:700 11px Montserrat,system-ui,sans-serif;letter-spacing:.14em;text-transform:uppercase;background:var(--blue);
  color:#fff;border:0;border-radius:8px;padding:10px 14px;cursor:pointer;text-decoration:none;display:inline-block;text-align:center}
button:hover,.btn:hover{background:var(--deep)}
button.ghost,.btn.ghost{background:#fff;color:var(--blue);box-shadow:inset 0 0 0 1.5px var(--blue)}
button.ghost:hover,.btn.ghost:hover{background:var(--grey)}
button.sm,.btn.sm{padding:7px 10px;font-size:10px;letter-spacing:.1em}
input,select,textarea{font:inherit;padding:8px 10px;border:1.5px solid #D5DBE6;border-radius:8px;background:#fff;color:var(--deep);max-width:100%}
input:focus,select:focus{outline:2px solid var(--blue);outline-offset:-1px}
form.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}
form.row label{display:flex;flex-direction:column;gap:4px;min-width:0}
form.inline{display:inline}
.filters{margin-bottom:18px}
.pager{display:flex;gap:8px;align-items:center;margin-top:16px;flex-wrap:wrap}
.review{display:grid;grid-template-columns:auto minmax(0,1fr);gap:20px;margin-bottom:16px}
.review a{overflow-wrap:anywhere}
.photos{display:flex;gap:8px;flex-wrap:wrap;max-width:460px}
.actions{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.stat b{display:block;font-weight:900;font-size:36px;color:var(--blue);line-height:1.1}
details summary{cursor:pointer;color:var(--blue);font-weight:700;font-size:12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 Montserrat,system-ui,sans-serif;color:var(--muted);max-width:720px}
.facts{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.55 Montserrat,system-ui,sans-serif;color:var(--ink,#111);background:#F3F5F9;border-radius:10px;padding:10px 12px;margin:10px 0;max-width:720px}
.flash{background:var(--blue);color:#fff;border-radius:10px;padding:10px 14px;margin-bottom:16px;font-weight:700}
.login{max-width:380px;margin:12vh auto;padding:0 20px}
.login .card{display:flex;flex-direction:column;gap:12px}
/* lead detail and lead cards (both layouts) */
.strip{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.strip::-webkit-scrollbar,.chips::-webkit-scrollbar,.photos::-webkit-scrollbar{display:none}
.strip img{width:220px;height:165px;object-fit:cover;border-radius:12px;background:var(--grey);flex:0 0 auto}
.detail{display:grid;gap:14px;max-width:760px}
.detail .card h2{margin:0 0 6px}
.kv{display:grid;grid-template-columns:110px minmax(0,1fr);gap:4px 12px}
.kv a{overflow-wrap:anywhere}
.statusgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.statusgrid button{padding:14px 8px;font-size:11px;letter-spacing:.06em;text-transform:none;font-size:14px}
.statusgrid button.cur{background:var(--deep);box-shadow:inset 0 0 0 3px #8FB0E8}
.btnrow{display:flex;gap:8px;flex-wrap:wrap}
.leadlist{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}
a.lead{display:grid;grid-template-columns:88px minmax(0,1fr);gap:12px;text-decoration:none;color:inherit;background:var(--grey);
  border-radius:14px;padding:10px}
a.lead img{width:88px;height:88px;object-fit:cover;border-radius:10px;background:#fff}
a.lead .t{font-weight:800;color:var(--blue)}
a.lead .req{color:var(--muted);font-size:12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.chips{display:flex;gap:6px;overflow-x:auto;margin:0 0 14px;padding-bottom:2px;scrollbar-width:none}
.chips a{flex:0 0 auto;text-decoration:none;font-weight:700;font-size:12px;padding:8px 12px;border-radius:999px;background:var(--grey);color:var(--blue)}
.chips a.on{background:var(--blue);color:#fff}
.tiles{display:grid;gap:10px}
.tiles a{display:block;background:var(--grey);border-radius:14px;padding:16px;text-decoration:none;color:var(--blue);font-weight:800;font-size:16px}
.tiles a span{display:block;font-weight:400;font-size:12px;color:var(--muted)}
/* narrow screens: tables become card rows, forms stack */
@media (max-width:760px){
  .wrap{padding-left:14px;padding-right:14px}
  h1{font-size:22px}
  .tablewrap{overflow:visible}
  table.list,table.list tbody,table.list tr,table.list td{display:block;width:100%}
  table.list tr:first-child{display:none}
  table.list tr{background:var(--grey);border-radius:14px;padding:12px 14px;margin-bottom:10px}
  table.list td{border:0;padding:3px 0;display:flex;gap:10px;min-width:0}
  table.list td[data-label]:not([data-label=""])::before{content:attr(data-label);flex:0 0 92px;font-weight:700;font-size:10px;
    letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding-top:3px}
  table.list td .v{flex:1;min-width:0;overflow-wrap:anywhere}
  table.list td.ph::before{display:none}
  table.list td.ph img{width:100%;height:190px;margin-bottom:6px}
  table.list tr .badge:not(.blue):not(.red){background:#fff}
  tr:hover td{background:none}
  form.row label{flex:1 1 140px}
  form.row label input,form.row label select{width:100%}
  .review{grid-template-columns:minmax(0,1fr)}
  .photos{flex-wrap:nowrap;overflow-x:auto;max-width:100%;scrollbar-width:none}
  img.big{width:200px;height:150px}
  .stats{grid-template-columns:1fr 1fr}
  .stat b{font-size:28px}
  .leadlist{grid-template-columns:minmax(0,1fr)}
  .kv{grid-template-columns:92px minmax(0,1fr)}
}
/* inside Telegram: compact brand bar on top, tab bar at the bottom, big tap targets */
body.tg{padding-bottom:calc(72px + env(safe-area-inset-bottom))}
body.tg header{padding:10px 0 12px}
body.tg .brand{font-size:15px}
body.tg .who{margin-left:auto;font-size:12px;opacity:.8;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:50%}
body.tg main{padding-top:16px}
body.tg .sub{font-size:13px;margin-bottom:14px}
body.tg button,body.tg .btn{padding:13px 16px;font-size:12px;border-radius:10px}
body.tg button.sm,body.tg .btn.sm{padding:10px 12px;font-size:11px}
body.tg input,body.tg select{padding:11px 12px;font-size:16px}
body.tg .actions button{flex:1}
nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:50;background:#fff;border-top:1px solid var(--line);display:grid;
  grid-template-columns:repeat(5,1fr);padding-bottom:env(safe-area-inset-bottom);box-shadow:0 -4px 16px rgba(2,31,74,.06)}
nav.tabs a{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:60px;
  text-decoration:none;color:var(--muted);font-weight:700;font-size:10px;letter-spacing:.02em}
nav.tabs a.on{color:var(--blue)}
nav.tabs a.on::before{content:"";position:absolute;top:0;left:22%;right:22%;height:3px;border-radius:0 0 3px 3px;background:var(--blue)}
nav.tabs svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
nav.tabs .badge{position:absolute;top:6px;left:calc(50% + 6px);margin:0;padding:1px 5px;font-size:9px;letter-spacing:0}
.minimap{height:180px;border-radius:12px;margin-top:12px;background:var(--grey);z-index:0}
"""
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;700;800;900'
         '&family=Noto+Sans+Georgian:wght@400;700;900&display=swap">')
TG_JS = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
# inside Telegram: brand colours over the client's theme, native back button on inner pages, external links via openLink
TG_BOOT = """<script>(function(){var w=window.Telegram&&Telegram.WebApp;if(!w||!w.initData)return;
try{w.ready();w.expand();w.setHeaderColor&&w.setHeaderColor('#032D6A');w.setBackgroundColor&&w.setBackgroundColor('#ffffff');}catch(e){}
var b=document.body.dataset.back;if(w.BackButton){if(b){w.BackButton.show();w.BackButton.onClick(function(){location.href=b});}else w.BackButton.hide();}
document.addEventListener('click',function(ev){var a=ev.target.closest&&ev.target.closest('a[target=_blank]');if(!a)return;ev.preventDefault();
if(/^https:\\/\\/t\\.me\\//.test(a.href)&&w.openTelegramLink)w.openTelegramLink(a.href);else if(w.openLink)w.openLink(a.href);else window.open(a.href);});
})();</script>"""
NAV = [("/", "Объявления"), ("/review", "Проверка"), ("/leads", "Заявки"), ("/agents", "Агенты"),
       ("/sources", "Источники"), ("/settings", "Настройки"), ("/stats", "Статистика"), ("/answers", "Ответы")]
ICON = {
    "leads": '<path d="M4 5h16v11H14l-4 3v-3H4z"/><path d="M8 9.5h8M8 12.5h5"/>',
    "review": '<circle cx="12" cy="12" r="8.5"/><path d="M8.2 12.3l2.6 2.6 5-5.4"/>',
    "listings": '<path d="M3.5 11L12 4l8.5 7"/><path d="M6 9.5V20h12V9.5"/><path d="M10 20v-5h4v5"/>',
    "sources": '<path d="M5 19a1 1 0 110-.01"/><path d="M4.5 11.5a8 8 0 018 8"/><path d="M4.5 5a14.5 14.5 0 0114.5 14.5"/>',
    "more": '<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>',
}
TABS = [("/leads", "Заявки", "leads", ("/leads", "/lead")), ("/review", "Проверка", "review", ("/review",)),
        ("/", "Объявления", "listings", ("/",)), ("/sources", "Источники", "sources", ("/sources",)),
        ("/more", "Ещё", "more", ("/more", "/agents", "/settings", "/stats", "/answers"))]


def html_doc(title, body, body_attrs="", tg=False):
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>{e(title)} · {core.BRAND}</title>{FONTS}<style>{CSS}</style>{TG_JS if tg else ""}</head>'
            f'<body{body_attrs}>{body}{TG_BOOT if tg else ""}</body></html>')


def counts():
    with db.tx() as c:
        return {"/review": c.execute("SELECT COUNT(*) FROM listings WHERE status = 'pending_review'").fetchone()[0],
                "/leads": c.execute("SELECT COUNT(*) FROM leads WHERE status = 'new'").fetchone()[0]}


def layout(path, title, body, v, flash=None, back=None):
    n = counts()
    fl = f'<div class="flash">{e(flash)}</div>' if flash else ""
    if v and v["tg"]:
        tabs = "".join(f'<a href="{p}" class="{"on" if any(path == x or (x != "/" and path.startswith(x + "/")) for x in on) else ""}">'
                       f'<svg viewBox="0 0 24 24">{ICON[ic]}</svg>{name}'
                       + (f'<span class="badge red">{n[p]}</span>' if n.get(p) else "") + "</a>" for p, name, ic, on in TABS)
        head = (f'<header><div class="wrap top"><div class="brand">{core.BRAND}</div>'
                f'<div class="who">{e(v["name"])}<br>{"агент" if v["kind"] == "agent" else "администратор"}</div></div></header>')
        attrs = ' class="tg"' + (f' data-back="{e(back)}"' if back else "")
        return html_doc(title, head + f'<main class="wrap">{fl}{body}</main><nav class="tabs">{tabs}</nav>', attrs, tg=True)
    nav = "".join(f'<a href="{p}" class="{"on" if p == path else ""}">{name}'
                  + (f'<span class="badge red">{n[p]}</span>' if n.get(p) else "") + "</a>" for p, name in NAV)
    head = (f'<header><div class="wrap"><div class="top"><div class="brand">{core.BRAND}<span>Админка</span></div>'
            '<form method="post" action="/logout"><button class="ghost sm">Выйти</button></form></div>'
            f'<nav class="desk">{nav}</nav></div></header>')
    return html_doc(title, head + f'<main class="wrap">{fl}{body}</main>')


def table(heads, rows, empty="Ничего не найдено."):
    """rows: lists of cells; a cell is html or (html, css class). Wide screens: a table; narrow: one card per row."""
    if not rows:
        return f'<p class="muted">{e(empty)}</p>'
    out = "<tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr>"
    for r in rows:
        cells = []
        for h, cell in zip(heads, r):
            body, cls = cell if isinstance(cell, tuple) else (cell, "")
            cells.append(f'<td data-label="{e(h)}" class="{cls}"><div class="v">{body}</div></td>')
        out += "<tr>" + "".join(cells) + "</tr>"
    return f'<div class="tablewrap"><table class="list">{out}</table></div>'


def options(values, selected, blank=None):
    out = f'<option value="">{e(blank)}</option>' if blank is not None else ""
    return out + "".join(f'<option value="{e(k)}"{" selected" if str(k) == str(selected) else ""}>{e(v)}</option>'
                         for k, v in values.items())


def listing_badge(status):
    cls = {"published": "blue", "pending_review": "red"}.get(status, "")
    return f'<span class="badge {cls}">{LISTING_STATUS.get(status, status)}</span>'


def lead_badge(status):
    return f'<span class="badge {"red" if status == "new" else "blue" if status == "rented" else ""}">{LEAD_STATUS[status]}</span>'


def onoff(flag, yes, no):
    return f'<span class="badge blue">{yes}</span>' if flag else f'<span class="badge">{no}</span>'


def urls_of(i):
    return i["urls"] if isinstance(i["urls"], list) else json.loads(i["urls"])


def source_link(i):
    urls = urls_of(i)
    if not urls:
        return '<span class="muted">—</span>'
    more = f' <span class="muted small">+{len(urls) - 1}</span>' if len(urls) > 1 else ""
    return f'<a href="{e(urls[0])}" target="_blank" rel="noopener noreferrer">Открыть ↗</a>{more}'


def params(i):
    bits = [f"{i['bedrooms']} сп." if i.get("bedrooms") else "", f"{i['area']:g} м²" if i.get("area") else "",
            f"{i['floor']} этаж" if i.get("floor") is not None else ""]
    return " · ".join(b for b in bits if b)


def when(ts):
    return (ts or "")[:16].replace("T", " ")


def agent_names():
    with db.tx() as c:
        return {r["id"]: (f"@{r['username']}" if r["username"] else r["name"] or f"id {r['tg_id']}")
                for r in c.execute("SELECT * FROM agents ORDER BY id")}


def contact_of(ld):
    return f"@{ld['username']}" if ld["username"] else ld["phone"] or f"{ld['channel']}:{ld['external_id']}"


def contact_links(cl):
    """Buttons to reach the client: Telegram chat (by username or id), phone call."""
    out = []
    if cl["channel"] == "tg":
        url = f"https://t.me/{cl['username']}" if cl["username"] else f"tg://user?id={cl['external_id']}"
        out.append(f'<a class="btn" href="{e(url)}" target="_blank" rel="noopener noreferrer">✉️ Написать в Telegram</a>')
    if cl.get("phone"):
        tel = re.sub(r"[^\d+]", "", cl["phone"])
        out.append(f'<a class="btn ghost" href="tel:{e(tel)}">📞 Позвонить {e(cl["phone"])}</a>')
    return '<div class="btnrow">' + "".join(out) + "</div>" if out else ""


def geo_link(i):
    """Exact point for agents on our own map (/map pin mode, stays inside the Mini App) + the geo precision."""
    q = core.pin_query(i)
    if not q:
        return '<span class="muted">нет координат</span>'
    return f'<a href="/map?{e(q)}">📍 На карте</a> <span class="muted small">{e(i.get("geo_precision") or "?")}</span>'


def facts_html(i):
    """The listing's channel-style card exactly as the client sees it (ru), plus agent-only media links."""
    d = i.get("details") or {}
    media = [f'<a href="{e(u)}" target="_blank" rel="noopener noreferrer">{label} ↗</a>'
             for label, u in (("🎥 Видео", d.get("video_url")), ("🧊 3D-тур", d.get("tour_url"))) if u]
    if d.get("videos"):
        media.insert(0, f"🎬 видео в альбоме: {len(d['videos'])}")
    return (f'<div class="facts">{e(core.card_body(i))}</div>'
            + (f'<div class="small">{" · ".join(media)}</div>' if media else ""))


def mini_map(i):
    """Small Leaflet map with the exact pin (agents only), or nothing without coordinates."""
    if core.pin_query(i) is None:
        return ""
    return ('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
            f'<div id="minimap" class="minimap" data-lat="{i["lat"]}" data-lng="{i["lng"]}"></div>'
            "<script>(function(){var d=document.getElementById('minimap');if(!window.L)return;var p=[+d.dataset.lat,+d.dataset.lng];"
            "var m=L.map(d,{zoomControl:false,scrollWheelZoom:false}).setView(p,16);m.attributionControl.setPrefix(false);"
            "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(m);"
            "L.circleMarker(p,{radius:8,color:'#fff',weight:3,fillColor:'#E3172D',fillOpacity:1}).addTo(m);})();</script>")


# ---------- pages: (query, viewer) -> (title, body) or (title, body, back) ----------

def page_listings(q, v):
    where, args = [], []
    if q.get("status") in LISTING_STATUS:
        where.append("status = ?"), args.append(q["status"])
    if q.get("source"):
        where.append("source = ?"), args.append(q["source"])
    if q.get("district"):
        where.append("district = ?"), args.append(q["district"])
    for key, op in (("min", ">="), ("max", "<=")):
        if (q.get(key) or "").isdigit():
            where.append(f"price_usd {op} ?"), args.append(int(q[key]))
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    page = int(q["page"]) if (q.get("page") or "").isdigit() else 0
    with db.tx() as c:
        total = c.execute("SELECT COUNT(*) FROM listings" + sql_where, args).fetchone()[0]
        rows = [db.row(r) for r in c.execute(f"SELECT * FROM listings{sql_where} ORDER BY first_seen DESC, id LIMIT ? OFFSET ?",
                                             args + [PAGE, page * PAGE])]
        sources = [r[0] for r in c.execute("SELECT DISTINCT source FROM listings ORDER BY source")]
        districts = [r[0] for r in c.execute("SELECT DISTINCT district FROM listings WHERE district IS NOT NULL AND district != '' ORDER BY district")]
    form = ('<form class="row filters card" method="get" action="/">'
            f'<label class="label">Статус<select name="status">{options(LISTING_STATUS, q.get("status"), "Все")}</select></label>'
            f'<label class="label">Источник<select name="source">{options({s: s for s in sources}, q.get("source"), "Все")}</select></label>'
            f'<label class="label">Район<select name="district">{options({d: d for d in districts}, q.get("district"), "Все")}</select></label>'
            f'<label class="label">Цена от, $<input name="min" size="6" inputmode="numeric" value="{e(q.get("min"))}"></label>'
            f'<label class="label">до, $<input name="max" size="6" inputmode="numeric" value="{e(q.get("max"))}"></label>'
            '<button>Показать</button> <a class="btn ghost" href="/">Сбросить</a></form>')
    if v["tg"]:  # filters folded on the phone (open when some are set)
        active = any(q.get(k) for k in ("status", "source", "district", "min", "max"))
        form = f'<details class="filters"{" open" if active else ""}><summary>Фильтры</summary><div style="height:8px"></div>{form}</details>'
    body = [[(thumb(i["id"]), "ph"),
             f"<b>{e(i['id'])}</b><div class='muted small'>{e(i['source'])}</div>",
             f"{e(i.get('district') or '—')}<div class='muted small'>{e(i.get('address'))}</div><div class='small'>{geo_link(i)}</div>"
             f"<details class='small'><summary>Карточка</summary>{facts_html(i)}</details>",
             (f"<b>${e(i.get('price_usd'))}</b><div class='muted small'>{e(params(i))}</div>", "nowrap"),
             f"{listing_badge(i['status'])}<div class='muted small'>{e(i.get('status_reason'))}</div>",
             (source_link(i), "nowrap"),
             (f"<span class='muted small'>{e((i.get('first_seen') or '')[:10])}</span>", "nowrap")] for i in rows]
    keep = {k: v_ for k, v_ in q.items() if k not in ("page", "msg") and v_}
    pager = '<div class="pager">' + (
        f'<a class="btn ghost" href="/?{urlencode({**keep, "page": page - 1})}">← Назад</a>' if page else "") + (
        f'<span class="muted">{page * PAGE + 1}–{min(total, (page + 1) * PAGE)} из {total}</span>' if total else "") + (
        f'<a class="btn ghost" href="/?{urlencode({**keep, "page": page + 1})}">Дальше →</a>' if (page + 1) * PAGE < total else "") + "</div>"
    tbl = table(["Фото", "ID", "Район, адрес", "Цена", "Статус", "Источник", "Добавлено"], body)
    return "Объявления", f'<h1>Объявления</h1><p class="sub">Найдено: {total}</p>{form}{tbl}{pager}'


def page_review(q, v):
    items = core.pending(limit=50, unsent_only=False)
    cards = []
    for i in items:
        photos = "".join(thumb(i["id"], n, "big") for n in range(min(4, len(i["photos"]))))
        urls = "<br>".join(f'<a href="{e(u)}" target="_blank" rel="noopener noreferrer">{e(u)}</a>' for u in i["urls"])
        cards.append(
            f'<div class="card review"><div class="photos">{photos or "<span class=muted>Нет фото</span>"}</div><div>'
            f'<span class="badge red">На проверке</span> <span class="muted small">{e(i["id"])} · {e(i["source"])}</span>'
            f'<h2 style="margin:8px 0 2px">{e(i.get("district") or "Тбилиси")} · ${e(i.get("price_usd"))}/мес</h2>'
            f'<div class="muted">{e(" · ".join(x for x in (i.get("address"), params(i)) if x))}</div>'
            f'<div class="small">{geo_link(i)}</div>'
            f'<p><span class="label">Почему на проверке</span><br>{e(i.get("status_reason") or "—")}</p>'
            f'{facts_html(i)}'
            f'<details><summary>Исходный текст и ссылки</summary><pre>{e(i.get("text"))}</pre>{urls}</details>'
            f'<form method="post" action="/review" class="actions"><input type="hidden" name="id" value="{e(i["id"])}">'
            '<button name="approve" value="1">✓ Опубликовать</button><button class="ghost" name="approve" value="0">✕ Отклонить</button>'
            "</form></div></div>")
    body = "".join(cards) or '<p class="muted">Очередь пуста: всё проверено.</p>'
    return "Проверка", (f'<h1>Проверка</h1><p class="sub">Сомнительные объявления: клиенты их не видят, пока агент не опубликует. '
                        f'Решение общее с ботом и не перетирается обновлением базы. В очереди: {len(items)}</p>{body}')


LEADS_SQL = ("SELECT l.*, s.district, s.price_usd, s.urls, s.source, s.status AS lstatus, c.channel, c.external_id, "
             "c.username, c.name, c.phone FROM leads l JOIN listings s ON s.id = l.listing_id JOIN clients c ON c.id = l.client_id")


def page_leads(q, v):
    st = q.get("status") if q.get("status") in LEAD_STATUS else None
    me = v["agent"] and v["agent"]["id"]
    if v["tg"]:
        return leads_cards(q, v, st, me)
    agents = agent_names()
    with db.tx() as c:
        active = {r["id"]: agents[r["id"]] for r in c.execute("SELECT id FROM agents WHERE active = 1")}
        leads = c.execute(LEADS_SQL + (" WHERE l.status = ?" if st else "") + " ORDER BY l.id DESC LIMIT 300",
                          (st,) if st else ()).fetchall()
        events = {}
        for ev in c.execute("SELECT * FROM lead_events ORDER BY id"):
            events.setdefault(ev["lead_id"], []).append(ev)
    rows = []
    for ld in leads:
        contact = contact_of(ld)
        hist = "".join(f"<div class='small'>{e(when(ev['at']))} · {e(LEAD_STATUS.get(ev['status'], ev['status']))}"
                       f"{' · ' + e(agents.get(ev['agent_id'])) if ev['agent_id'] else ''}</div>" for ev in events.get(ld["id"], []))
        rows.append([
            f"<a href='/lead/{ld['id']}'><b>#{ld['id']}</b></a><div class='muted small nowrap'>{e(when(ld['created_at']))}</div>",
            f"<b>{e(ld['name'] or contact)}</b><div class='small'>{e(contact)}</div><div class='muted small'>{e(ld['request'])}</div>",
            (thumb(ld["listing_id"]), "ph"),
            f"{e(ld['district'] or '—')} · <b>${e(ld['price_usd'])}</b><div class='muted small'>{e(ld['listing_id'])} · {e(ld['source'])}</div>"
            f"{source_link(ld)}",
            f"{lead_badge(ld['status'])}<div class='small' style='margin-top:6px'>{e(agents.get(ld['agent_id']) or '')}</div>",
            f"<form method='post' action='/leads' class='row'><input type='hidden' name='id' value='{ld['id']}'>"
            f"<select name='status'>{options(LEAD_STATUS, ld['status'])}</select>"
            f"<select name='agent' title='Агент (если ещё не назначен)'>{options(active, ld['agent_id'], 'Агент…')}</select>"
            f"<button>Сохранить</button></form>",
            f"<details><summary>История</summary>{hist}</details>"])
    filt = ('<form class="row filters" method="get" action="/leads">'
            f'<label class="label">Статус<select name="status" onchange="this.form.submit()">{options(LEAD_STATUS, st, "Все")}</select></label></form>')
    tbl = table(["Заявка", "Клиент и запрос", "Фото", "Объект", "Статус", "Изменить", "История"], rows, "Заявок нет.")
    return "Заявки", (f'<h1>Заявки</h1><p class="sub">Клиент нажал «Нравится» → заявка. Агент закрепляется за заявкой при первой '
                      f'смене статуса (в боте или здесь).</p>{filt}{tbl}')


def leads_cards(q, v, st, me):
    """Phone list: my leads + new ones by default (agents), or all / by status; each card opens the lead."""
    f = q.get("f") or ("mine" if me and not st else "all")
    where, args = [], []
    if st:
        where.append("l.status = ?"), args.append(st)
    elif f == "mine" and me:
        where.append("(l.agent_id = ? OR l.status = 'new')"), args.append(me)
    agents = agent_names()
    with db.tx() as c:
        leads = c.execute(LEADS_SQL + (" WHERE " + " AND ".join(where) if where else "") +
                          " ORDER BY l.status = 'new' DESC, l.updated_at DESC LIMIT 200", args).fetchall()
    chips = ([("mine", None, "Мои и новые")] if me else []) + [("all", None, "Все")] + [(None, s, LEAD_STATUS[s]) for s in LEAD_STATUS]
    nav = "".join(f'<a href="/leads?{urlencode({"f": k} if k else {"status": s})}" class="{"on" if (s and s == st) or (k and not st and k == f) else ""}">'
                  f"{e(t)}</a>" for k, s, t in chips)
    cards = "".join(
        f'<a class="lead" href="/lead/{ld["id"]}">{thumb(ld["listing_id"], 0, "")}<div style="min-width:0">'
        f'<div>{lead_badge(ld["status"])} <span class="muted small">#{ld["id"]} · {e(when(ld["created_at"]))}</span></div>'
        f'<div class="t">{e(ld["district"] or "Тбилиси")} · ${e(ld["price_usd"])}</div>'
        f'<div class="small">{e(ld["name"] or contact_of(ld))} · {e(contact_of(ld))}</div>'
        f'<div class="req">{e(ld["request"])}</div>'
        + (f'<div class="muted small">агент {e(agents.get(ld["agent_id"]))}</div>' if ld["agent_id"] else "")
        + "</div></a>" for ld in leads)
    sub = "Ваши заявки и новые, которые ещё никто не взял." if f == "mine" and me and not st else f"Заявок: {len(leads)}"
    return "Заявки", (f'<h1>Заявки</h1><p class="sub">{sub}</p><div class="chips">{nav}</div>'
                      + (f'<div class="leadlist">{cards}</div>' if cards else '<p class="muted">Заявок нет.</p>'))


def page_lead(q, v, lead_id):
    ld = core.lead(lead_id)
    if not ld:
        return "Не найдено", '<h1>Заявка не найдена</h1><p><a href="/leads">← Все заявки</a></p>', "/leads"
    i, cl = ld["listing"], ld["client"]
    agents = agent_names()
    with db.tx() as c:
        events = c.execute("SELECT * FROM lead_events WHERE lead_id = ? ORDER BY id", (lead_id,)).fetchall()
        active = {r["id"]: agents[r["id"]] for r in c.execute("SELECT id FROM agents WHERE active = 1")}
    photos = "".join(thumb(i["id"], n, "") for n in range(min(8, len(i["photos"])))) or '<span class="muted">Нет фото</span>'
    links = "".join(f'<a class="btn ghost sm" href="{e(u)}" target="_blank" rel="noopener noreferrer">Объявление{" " + str(k + 1) if len(i["urls"]) > 1 else ""} ↗</a>'
                    for k, u in enumerate(i["urls"]))
    buttons = "".join(f'<button name="status" value="{s}" class="{"cur" if ld["status"] == s else ""}">{e(core.LEAD_STATUSES[s])}</button>'
                      for s in AGENT_STATUSES)
    pick = ("" if v["agent"] else f'<label class="label" style="grid-column:1/-1">Агент (если ещё не назначен)'
            f'<select name="agent">{options(active, ld["agent_id"], "—")}</select></label>')
    status_form = (f'<form method="post" action="/leads" class="statusgrid"><input type="hidden" name="id" value="{ld["id"]}">'
                   f'<input type="hidden" name="back" value="/lead/{ld["id"]}">{pick}{buttons}</form>')
    hist = "".join(f"<div class='small'>{e(when(ev['at']))} · {e(LEAD_STATUS.get(ev['status'], ev['status']))}"
                   f"{' · ' + e(agents.get(ev['agent_id'])) if ev['agent_id'] else ''}</div>" for ev in events)
    agent = agents.get(ld["agent_id"]) or "ещё не назначен (закрепится за первым, кто сменит статус)"
    dups = f" (+ дубли: {e(', '.join(i['dup_sources']))})" if i.get("dup_sources") else ""
    body = (f'<p class="muted small" style="margin:0 0 6px"><a href="/leads">← Заявки</a></p>'
            f'<h1>Заявка #{ld["id"]}</h1><p class="sub">{lead_badge(ld["status"])} · {e(when(ld["created_at"]))}</p>'
            f'<div class="detail"><div class="strip">{photos}</div>'
            f'<div class="card"><div class="label" style="margin-bottom:8px">Статус</div>{status_form}'
            f'<div class="small muted" style="margin-top:10px">Агент: {e(agent)}</div></div>'
            f'<div class="card"><h2>{e(cl["name"] or who_plain(cl))}</h2>'
            f'<div class="kv"><span class="label">Контакт</span><span>{e(who_plain(cl))} · {e(cl["lang"])}</span>'
            f'<span class="label">Запрос</span><span>{e(ld["request"])}</span></div>'
            f'<div style="margin-top:12px">{contact_links(cl)}</div></div>'
            f'<div class="card"><h2>{e(i.get("district") or "Тбилиси")} · ${e(i.get("price_usd"))}/мес</h2>'
            f'<div class="kv"><span class="label">Адрес</span><span>{e(i.get("address") or "—")}</span>'
            f'<span class="label">Параметры</span><span>{e(params(i) or "—")}</span>'
            f'<span class="label">Источник</span><span>{e(i["source"])}{dups} · {e(i["id"])}</span>'
            f'<span class="label">На карте</span><span>{geo_link(i)}</span>'
            f'<span class="label">Объявление</span><span>{listing_badge(i["status"])}</span></div>{mini_map(i)}'
            f'{facts_html(i)}<div class="btnrow">{links}</div>'
            f'<details style="margin-top:12px"><summary>Исходный текст</summary><pre>{e(i.get("text"))}</pre></details></div>'
            f'<div class="card"><div class="label">История</div>{hist}</div></div>')
    return f"Заявка #{ld['id']}", body, "/leads"


def who_plain(cl):
    return core.who(cl, as_html=False)


def page_agents(q, v):
    with db.tx() as c:
        rows = c.execute("SELECT a.*, (SELECT COUNT(*) FROM leads WHERE agent_id = a.id) AS n FROM agents a ORDER BY active DESC, id").fetchall()
    body = [[f"<b>{e(r['name'] or '—')}</b>", "@" + e(r["username"]) if r["username"] else "—",
             e(r["tg_id"]) if r["tg_id"] else "<span class=muted>привяжется при первом сообщении боту</span>",
             str(r["n"]), onoff(r["active"], "Активен", "Отключён"),
             (f"<form method='post' action='/agents/toggle' class='inline'><input type='hidden' name='id' value='{r['id']}'>"
              f"<button class='ghost sm'>{'Отключить' if r['active'] else 'Включить'}</button></form>", "acts")] for r in rows]
    add = ('<form method="post" action="/agents/add" class="row card filters">'
           '<label class="label">Telegram ID или @username<input name="tg" required placeholder="123456789 или @david"></label>'
           '<label class="label">Имя<input name="name" placeholder="Давид"></label><button>Добавить агента</button></form>')
    return "Агенты", (f'<h1>Агенты</h1><p class="sub">Активные агенты получают заявки и объявления на проверку (если заявки идут '
                      f'в личку) и открывают эту панель в Telegram. Агент также может подключиться сам: <b>/agent &lt;код&gt;</b> в боте.</p>{add}'
                      + table(["Имя", "Username", "Telegram ID", "Заявок", "Статус", ""], body, "Агентов нет."))


def page_sources(q, v):
    with db.tx() as c:
        rows = c.execute("SELECT s.*, (SELECT COUNT(*) FROM listings WHERE status = 'published' AND source = "
                         "CASE s.kind WHEN 'tg_agency' THEN 'agency' WHEN 'fb_group' THEN 'fb:' || s.key WHEN 'ig_account' THEN 'ig:' || s.key ELSE 'tg:' || s.key END) AS n "
                         "FROM sources s ORDER BY s.kind, s.position, s.id").fetchall()
    body = [[f"<span class='badge'>{e(SOURCE_KINDS[r['kind']])}</span>",
             (f"<b>{e(r['key'])}</b><div class='muted small'>{e(r['name'])}</div>", "wrapall"),
             (f"<a href='{e(r['url'])}' target='_blank' rel='noopener noreferrer'>{e(r['url'])}</a>", "wrapall"),
             e(r["lang"] or ""), str(r["n"]), onoff(r["enabled"], "Вкл", "Выкл"),
             (f"<form method='post' action='/sources/toggle' class='inline'><input type='hidden' name='id' value='{r['id']}'>"
              f"<button class='ghost sm'>{'Выключить' if r['enabled'] else 'Включить'}</button></form>"
              f"<form method='post' action='/sources/delete' class='inline' onsubmit=\"return confirm('Удалить источник {e(r['key'])}?')\">"
              f"<input type='hidden' name='id' value='{r['id']}'><button class='ghost sm'>Удалить</button></form>", "acts")] for r in rows]
    add = ('<form method="post" action="/sources/add" class="row card filters">'
           f'<label class="label">Тип<select name="kind">{options(SOURCE_KINDS, "tg_competitor")}</select></label>'
           '<label class="label" style="flex:2 1 220px">Ссылка или @username<input name="link" required placeholder="https://t.me/channel или facebook.com/groups/…"></label>'
           '<label class="label">Название<input name="name"></label>'
           '<label class="label" style="flex:0 1 130px">Язык<select name="lang"><option value="ru">Русский</option><option value="en">English</option><option value="ka">ქართული</option><option value="ru/en">Рус + Eng</option><option value="ka/en">ქარ + Eng</option></select></label><button>Добавить</button></form>')
    return "Источники", (f'<h1>Источники</h1><p class="sub">Telegram-каналы и Facebook-группы, откуда собираются объявления. '
                         f'Изменения попадут в сбор при следующем обновлении базы. Опубликовано — объявлений в базе сейчас.</p>{add}'
                         + table(["Тип", "Ключ", "Ссылка", "Язык", "Опубликовано", "Статус", ""], body, "Источников нет."))


def page_settings(q, v):
    with db.tx() as c:
        s = {k: db.get_setting(k, c) for k in db.DEFAULT_SETTINGS}
    hint = 'style="text-transform:none;letter-spacing:0;font-weight:400"'
    body = ('<form method="post" action="/settings" class="card settings" style="max-width:640px;display:flex;flex-direction:column;gap:18px">'
            f'<label><input type="checkbox" name="moderate_all" value="1"{" checked" if s["moderate_all"] == "1" else ""}> '
            '<b>Проверять все объявления конкурентов и Facebook</b><div class="muted small">Иначе на проверку попадают только '
            'сомнительные. Действует со следующего обновления базы.</div></label>'
            '<div><div class="label">Куда отправлять заявки и проверку в Telegram</div>'
            f'<label><input type="radio" name="lead_target" value="agents"{" checked" if s["lead_target"] != "group" else ""}> '
            'Каждому активному агенту в личку</label><br>'
            f'<label><input type="radio" name="lead_target" value="group"{" checked" if s["lead_target"] == "group" else ""}> '
            'В группу агентства</label></div>'
            f'<label class="label">ID группы агентства<input name="group_chat_id" value="{e(s["group_chat_id"])}" placeholder="-100…">'
            f'<span class="muted small" {hint}>Узнать ID проще всего командой /leads_here в самой группе: она заодно включит отправку в группу.</span></label>'
            f'<label class="label">Сколько объявлений на проверку бот присылает за раз (каждые 10 мин)'
            f'<input name="review_batch" type="number" min="1" max="50" value="{e(s["review_batch"])}"></label>'
            '<div><button>Сохранить</button></div></form>')
    return "Настройки", f'<h1>Настройки</h1><p class="sub">Общие для бота, админки и панели агента.</p>{body}'


def page_more(q, v):
    tiles = [("/agents", "Агенты", "кто получает заявки, добавить или отключить"),
             ("/settings", "Настройки", "проверка всех объявлений, куда слать заявки"),
             ("/stats", "Статистика", "поиски, заявки, просмотры, сдано"),
             ("/answers", "Ответы", "что агентство ответило на макетах /journey")]
    body = "".join(f'<a href="{p}">{t}<span>{d}</span></a>' for p, t, d in tiles)
    who = f'<p class="muted small" style="margin-top:18px">Вы вошли как {e(v["name"])} через Telegram.</p>' if v["tg"] else ""
    return "Ещё", f'<h1>Ещё</h1><div class="tiles">{body}</div>{who}'


def page_stats(q, v):
    today = date.today()
    try:
        d1 = date.fromisoformat(q.get("from") or "")
    except ValueError:
        d1 = today - timedelta(days=30)
    try:
        d2 = date.fromisoformat(q.get("to") or "")
    except ValueError:
        d2 = today
    a, b = d1.isoformat(), (d2 + timedelta(days=1)).isoformat()  # ISO strings compare in time order
    with db.tx() as c:
        one = lambda sql: c.execute(sql, (a, b)).fetchone()[0]
        nums = [
            ("Запросов (поисков)", one("SELECT COUNT(*) FROM searches WHERE at >= ? AND at < ?")),
            ("Клиентов искали", one("SELECT COUNT(DISTINCT client_id) FROM searches WHERE at >= ? AND at < ?")),
            ("Новых клиентов", one("SELECT COUNT(*) FROM clients WHERE created_at >= ? AND created_at < ?")),
            ("Лайков", one("SELECT COUNT(*) FROM reactions WHERE reaction = 'like' AND created_at >= ? AND created_at < ?")),
            ("Заявок", one("SELECT COUNT(*) FROM leads WHERE created_at >= ? AND created_at < ?")),
            ("Просмотров назначено", one("SELECT COUNT(DISTINCT lead_id) FROM lead_events WHERE status = 'viewing' AND at >= ? AND at < ?")),
            ("Сдано", one("SELECT COUNT(DISTINCT lead_id) FROM lead_events WHERE status = 'rented' AND at >= ? AND at < ?")),
            ("Отказов", one("SELECT COUNT(DISTINCT lead_id) FROM lead_events WHERE status = 'refused' AND at >= ? AND at < ?")),
        ]
        by_source = c.execute("SELECT s.source, COUNT(*), SUM(l.status = 'rented') FROM leads l JOIN listings s ON s.id = l.listing_id "
                              "WHERE l.created_at >= ? AND l.created_at < ? GROUP BY s.source ORDER BY 2 DESC", (a, b)).fetchall()
        by_agent = c.execute("SELECT COALESCE('@' || a.username, a.name, a.tg_id), COUNT(DISTINCT ev.lead_id), "
                             "COUNT(DISTINCT CASE WHEN ev.status = 'rented' THEN ev.lead_id END) FROM lead_events ev "
                             "JOIN agents a ON a.id = ev.agent_id WHERE ev.at >= ? AND ev.at < ? GROUP BY a.id ORDER BY 2 DESC", (a, b)).fetchall()
        base = dict(c.execute("SELECT status, COUNT(*) FROM listings GROUP BY status").fetchall())
    form = ('<form class="row filters card" method="get" action="/stats">'
            f'<label class="label">С<input type="date" name="from" value="{a}"></label>'
            f'<label class="label">По<input type="date" name="to" value="{d2.isoformat()}"></label><button>Показать</button></form>')
    cards = "".join(f'<div class="card stat"><b>{n}</b><span class="label">{e(t)}</span></div>' for t, n in nums)
    tbl = lambda head, rows: table(head, [[e(x if x is not None else 0) for x in r] for r in rows], "Нет данных за период.")
    base_cards = "".join(f'<div class="card stat"><b>{base.get(k, 0)}</b><span class="label">{v_}</span></div>' for k, v_ in LISTING_STATUS.items())
    return "Статистика", (f'<h1>Статистика</h1><p class="sub">За период {d1:%d.%m.%Y} – {d2:%d.%m.%Y}</p>{form}'
                          f'<div class="stats">{cards}</div><h2>Заявки по источникам</h2>{tbl(["Источник", "Заявок", "Сдано"], by_source)}'
                          f'<h2>Агенты</h2>{tbl(["Агент", "Заявок в работе", "Сдано"], by_agent)}'
                          f'<h2>База объявлений сейчас</h2><div class="stats">{base_cards}</div>')


def page_answers(q, v):
    with db.tx() as c:
        rows = c.execute("SELECT * FROM journey_answers ORDER BY id DESC LIMIT 200").fetchall()
    if not rows:
        return "Ответы", ('<h1>Ответы на макеты</h1><p class="sub">Форма на странице <a href="/journey">/journey</a>.</p>'
                          '<p class="muted">Пока никто не отправил ответы.</p>')
    out = []
    for r in rows:
        answers = json.loads(r["payload"]).get("answers", [])
        items = "".join(f'<li><b>{e(a.get("question"))}</b><br>{e(a.get("answer")).replace(chr(10), "<br>")}</li>'
                        for a in answers if (a.get("answer") or "").strip())
        out.append(f'<div class="card" style="margin-bottom:12px"><div class="label">{when(r["created_at"])} · '
                   f'{e(r["name"] or "без имени")} · #{r["id"]}</div><ol style="margin:8px 0 0;padding-left:20px">{items}</ol></div>')
    return "Ответы", (f'<h1>Ответы на макеты</h1><p class="sub">Форма на странице <a href="/journey">/journey</a> · '
                      f'всего {len(rows)}.</p>{"".join(out)}')


PAGES = {"/": page_listings, "/review": page_review, "/leads": page_leads, "/agents": page_agents,
         "/sources": page_sources, "/settings": page_settings, "/stats": page_stats, "/more": page_more,
         "/answers": page_answers}


# ---------- actions (POST): (form, viewer) -> (redirect path, flash message) ----------

def who_label(v):
    a = v["agent"]
    if a:
        return f"@{a['username']}" if a.get("username") else a.get("name") or "агент"
    return "админка"


def act_review(f, v):
    approve = f.get("approve") == "1"
    if not core.listing(f.get("id", "")):
        return "/review", "Объявление не найдено"
    i = core.moderate(f["id"], approve, v["agent"] and v["agent"]["id"])  # same rule as the bot's buttons; NULL = admin panel
    push_review(i, approve, who_label(v))
    sheets.sync_later()
    return "/review", f"{f['id']}: {'опубликовано' if approve else 'отклонено'}"


def act_lead(f, v):
    back = safe_next(f.get("back"), "/leads")
    if f.get("status") not in LEAD_STATUS or not (f.get("id") or "").isdigit() or not core.lead(int(f["id"])):
        return back, "Неверные данные"
    if v["agent"]:  # an agent in the Mini App acts as himself, like pressing the button in Telegram
        agent = v["agent"]["id"]
    else:
        agent = int(f["agent"]) if (f.get("agent") or "").isdigit() else None
    ld = core.set_lead_status(int(f["id"]), f["status"], agent)
    push_lead(ld)
    sheets.sync_later()
    return back, f"Заявка #{f['id']}: {LEAD_STATUS[f['status']]}"


def add_agent(tg, name=None):
    """'123456' -> by Telegram id; '@user' / 't.me/user' -> by username (Telegram id bound on first contact)."""
    tg = re.sub(r"^(https?://)?t\.me/", "", (tg or "").strip()).lstrip("@")
    name = (name or "").strip() or None
    if re.fullmatch(r"\d{4,15}", tg):
        return core.agent(int(tg), name, None, create=True)
    if not re.fullmatch(r"[A-Za-z]\w{3,31}", tg):
        raise ValueError("Нужен числовой Telegram ID или @username")
    with db.tx() as c:
        r = c.execute("SELECT id FROM agents WHERE lower(username) = lower(?)", (tg,)).fetchone()
        if r:
            c.execute("UPDATE agents SET active = 1, name = COALESCE(?, name) WHERE id = ?", (name, r["id"]))
        else:
            c.execute("INSERT INTO agents (username, name, created_at) VALUES (?, ?, ?)", (tg, name, db.now()))
    return tg


def act_agent_add(f, v):
    try:
        add_agent(f.get("tg"), f.get("name"))
    except ValueError as ex:
        return "/agents", str(ex)
    return "/agents", "Агент добавлен"


def act_agent_toggle(f, v):
    if v["agent"] and str(v["agent"]["id"]) == f.get("id"):
        return "/agents", "Себя отключить нельзя"  # would lock the agent out of this very panel
    with db.tx() as c:
        c.execute("UPDATE agents SET active = 1 - active WHERE id = ?", (f.get("id"),))
    return "/agents", "Сохранено"


def parse_source(kind, link):
    """-> (key, url) or ValueError. Telegram: @name / t.me/name / name. Facebook: group URL."""
    link = (link or "").strip()
    if kind in ("tg_competitor", "tg_agency"):
        key = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/(s/)?", "", link).lstrip("@").split("/")[0].split("?")[0]
        if not re.fullmatch(r"[A-Za-z]\w{3,31}", key):
            raise ValueError("Не похоже на Telegram-канал: нужен @username или ссылка t.me/…")
        return key, f"https://t.me/{key}"
    if kind == "fb_group":
        m = re.fullmatch(r"(?:https?://)?(?:www\.|m\.)?facebook\.com/groups/([\w.-]+)/?(?:\?.*)?", link)
        if not m:
            raise ValueError("Нужна ссылка вида https://www.facebook.com/groups/…")
        return m[1], f"https://www.facebook.com/groups/{m[1]}/"
    if kind == "ig_account":
        key = re.sub(r"^(https?://)?(www\.)?instagram\.com/", "", link).lstrip("@").split("/")[0].split("?")[0]
        if not re.fullmatch(r"[\w.]{1,30}", key):
            raise ValueError("Нужен @username или ссылка instagram.com/…")
        return key, f"https://www.instagram.com/{key}/"
    raise ValueError("Неизвестный тип источника")


def add_source(kind, link, name=None, lang=None):
    key, url = parse_source(kind, link)
    opts = {"want": 40} if kind == "tg_competitor" else {}
    with db.tx() as c:
        if c.execute("SELECT 1 FROM sources WHERE lower(key) = lower(?)", (key,)).fetchone():
            raise ValueError(f"Источник {key} уже есть")
        pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM sources").fetchone()[0]
        c.execute("INSERT INTO sources (kind, key, name, url, lang, options, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (kind, key, (name or "").strip() or None, url, (lang or "").strip() or None, json.dumps(opts), pos))
    return key


def act_source_add(f, v):
    try:
        key = add_source(f.get("kind"), f.get("link"), f.get("name"), f.get("lang"))
    except ValueError as ex:
        return "/sources", str(ex)
    return "/sources", f"Источник {key} добавлен"


def act_source_toggle(f, v):
    with db.tx() as c:
        c.execute("UPDATE sources SET enabled = 1 - enabled WHERE id = ?", (f.get("id"),))
    return "/sources", "Сохранено"


def act_source_delete(f, v):
    with db.tx() as c:
        c.execute("DELETE FROM sources WHERE id = ?", (f.get("id"),))
    return "/sources", "Источник удалён (объявления из него остаются в базе до следующего обновления)"


def act_settings(f, v):
    batch = f.get("review_batch", "")
    group = (f.get("group_chat_id") or "").strip()
    if not batch.isdigit() or not 1 <= int(batch) <= 50 or not re.fullmatch(r"-?\d*", group):
        return "/settings", "Проверьте значения"
    if f.get("lead_target") == "group" and not group:
        return "/settings", "Для отправки в группу нужен ID группы"
    with db.tx() as c:
        db.set_setting("moderate_all", "1" if f.get("moderate_all") == "1" else "0", c)
        db.set_setting("lead_target", "group" if f.get("lead_target") == "group" else "agents", c)
        db.set_setting("group_chat_id", group, c)
        db.set_setting("review_batch", batch, c)
    return "/settings", "Настройки сохранены"


ACTIONS = {"/review": act_review, "/leads": act_lead, "/agents/add": act_agent_add, "/agents/toggle": act_agent_toggle,
           "/sources/add": act_source_add, "/sources/toggle": act_source_toggle, "/sources/delete": act_source_delete,
           "/settings": act_settings}


# ---------- HTTP ----------

# One entry page for both: inside Telegram (initData present) it signs in through /tg/auth, in a browser it asks the password.
LOGIN = """<div class="login"><div class="brand" style="font-weight:900;font-size:26px;color:#032D6A;text-transform:uppercase">
{brand}</div><p class="label">{label}</p>{err}<div id="tg" class="card" style="display:none"><b>Вход через Telegram…</b>
<span id="tgmsg" class="muted small"></span></div>
<form id="pw" method="post" action="/login" class="card"{pw_style}><input type="hidden" name="next" value="{next}">
<label class="label">Пароль<input type="password" name="password" required style="width:100%"></label>
<button>Войти</button></form></div>
<script>(function(){{var w=window.Telegram&&Telegram.WebApp,d=w&&w.initData;if(!d)return;
document.getElementById('pw').style.display='none';var box=document.getElementById('tg');box.style.display='flex';
try{{w.ready();w.expand();w.setHeaderColor&&w.setHeaderColor('#032D6A');}}catch(e){{}}
fetch('/tg/auth',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
body:new URLSearchParams({{init_data:d,next:{next_js}}})}}).then(function(r){{return r.json()}}).then(function(j){{
if(j.ok)location.replace(j.next);else{{box.querySelector('b').textContent='Нет доступа';document.getElementById('tgmsg').textContent=j.error;}}
}}).catch(function(){{document.getElementById('tgmsg').textContent='Сервер недоступен, попробуйте ещё раз.';}});}})();</script>"""


def login_page(nxt="/", err="", tg=False):
    body = LOGIN.format(brand=core.BRAND, label="Панель агента" if tg else "Админка", next=e(nxt), next_js=json.dumps(nxt),
                        err=f'<div class="flash">{e(err)}</div>' if err else "",
                        pw_style="" if password() else ' style="display:none"')
    return html_doc("Вход", body + ('<script>if(!(window.Telegram&&Telegram.WebApp&&Telegram.WebApp.initData)){var t=document.getElementById("tg");'
                                    't.style.display="flex";t.querySelector("b").textContent="Откройте панель из Telegram";'
                                    'document.getElementById("tgmsg").textContent="Кнопка «Панель» в чате с ботом.";}</script>'
                                    if not password() else ""), tg=True)


# ---------- /journey: the mockups page and its answers form (public) ----------

def journey_clean(raw):
    """JSON body -> (name, answers) or None. answers: [{"id", "question", "answer"}], at least one non-empty."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list) or len(data["answers"]) > 50:
        return None
    name = data.get("name") or ""
    if not isinstance(name, str):
        return None
    out = []
    for a in data["answers"]:
        if not isinstance(a, dict) or not all(isinstance(a.get(k, ""), str) for k in ("id", "question", "answer")):
            return None
        out.append({"id": a.get("id", "")[:40], "question": a.get("question", "")[:500].strip(), "answer": a.get("answer", "")[:5000].strip()})
    if not any(a["answer"] for a in out):
        return None
    return name.strip()[:100], out


def journey_text(name, answers):
    """Telegram notification, split into messages of at most 4000 characters."""
    lines = [f"📝 Ответы на макеты от {name or 'без имени'}:"]
    lines += [f"{n}. {a['question']}: {a['answer']}" for n, a in enumerate((a for a in answers if a["answer"]), 1)]
    chunks, cur = [], ""
    for line in lines:
        while len(line) > 4000:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:4000])
            line = line[4000:]
        if cur and len(cur) + 1 + len(line) > 4000:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    return chunks + ([cur] if cur else [])


def journey_notify(name, answers):
    chats = [x.strip() for x in os.environ.get("JOURNEY_NOTIFY_CHAT", "").split(",") if x.strip()]
    for chat in chats:
        for text in journey_text(name, answers):
            try:
                tg_call("sendMessage", chat_id=chat, text=text, disable_web_page_preview="true")
            except Exception as ex:  # never lose the stored answers because Telegram failed
                print("journey notify", chat, "failed:", ex, file=sys.stderr)


def ip_hash(ip):
    key = (bot_token() or password() or "journey").encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:24]


class Handler(BaseHTTPRequestHandler):
    server_version = "admin"

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # framing only by Telegram Web (the Mini App runs in its iframe there); everyone else is refused
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' https://web.telegram.org")
        self.send_header("Referrer-Policy", "same-origin")  # no-referrer would make browsers send "Origin: null"
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, cookie=None):
        self.send(303, b"", headers=[("Location", location)] + ([("Set-Cookie", cookie)] if cookie else []))

    def https(self):
        """Behind a TLS reverse proxy / Cloudflare tunnel the request comes as http to 127.0.0.1 with X-Forwarded-Proto."""
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        return os.environ.get("ADMIN_SECURE_COOKIE") == "1" or proto == "https"

    def hosts(self):
        """Host names this site is reached by: Host, X-Forwarded-Host (tunnel/proxy) and AGENT_APP_URL."""
        out = {self.headers.get("Host") or ""}
        out |= {h.strip() for h in (self.headers.get("X-Forwarded-Host") or "").split(",")}
        if core.app_url():
            out.add(urlparse(core.app_url()).netloc)
        return out - {""}

    def cookie(self, value, max_age, tg=False):
        secure = self.https()
        # Telegram Web shows the Mini App in a cross-site iframe: its cookie must be SameSite=None (allowed only with Secure)
        same = ("None" if secure else "Lax") if tg else "Strict"
        return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite={same}; Max-Age={max_age}{'; Secure' if secure else ''}"

    def session(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, val = part.strip().partition("=")
            if k == COOKIE and (who := valid_token(val)):
                return viewer(who)
        return None

    def handle_request(self, method):
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if url.path in ("/journey", "/journey/"):
            if not JOURNEY_PAGE.exists():
                return self.send(404, "Not found", "text/plain")
            return self.send(200, JOURNEY_PAGE.read_bytes(), headers=[("Cache-Control", "no-cache")])
        if url.path == "/journey/answers" and method == "POST":
            return self.journey_answers()
        form = {}
        if method == "POST":
            n = min(int(self.headers.get("Content-Length") or 0), 100_000)
            form = {k: v[-1] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True).items()}
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc not in self.hosts():  # cross-site form post
                return self.send(403, "Forbidden", "text/plain")
        try:
            if url.path == "/map":  # the clients' Mini App map (public, no data): same version as the bot
                return self.send(200, Path(__file__).with_name("webapp").joinpath("index.html").read_text())
            if url.path in ("/login", "/tg"):
                nxt = safe_next(q.get("next") or form.get("next"), "/")
                if url.path == "/tg":
                    nxt = f"/lead/{q['lead']}" if (q.get("lead") or "").isdigit() else safe_next(q.get("next"), "/leads")
                if method == "POST":
                    if check_password(form.get("password")):
                        return self.redirect(nxt, self.cookie(make_token(), SESSION_DAYS * 86400))
                    time.sleep(1)  # slow down guessing
                    return self.send(401, login_page(nxt, "Неверный пароль"))
                return self.send(200, login_page(nxt, tg=url.path == "/tg"))
            if url.path == "/tg/auth" and method == "POST":
                return self.tg_auth(form)
            v = self.session()
            if not v:
                return self.redirect("/login" if url.path == "/" else "/login?" + urlencode({"next": safe_next(self.path)}))
            if url.path == "/logout" and method == "POST":
                return self.redirect("/login", self.cookie("", 0))
            if url.path.startswith("/photo/") and method == "GET":
                return self.photo(url.path)
            m = re.fullmatch(r"/lead/(\d{1,9})", url.path)
            if method == "GET" and (url.path in PAGES or m):
                res = page_lead(q, v, int(m[1])) if m else PAGES[url.path](q, v)
                title, body, back = (res + (None,))[:3]
                return self.send(200, layout(url.path, title, body, v, q.get("msg"), back))
            if method == "POST" and url.path in ACTIONS:
                path, msg = ACTIONS[url.path](form, v)
                return self.redirect(f"{path}{'&' if '?' in path else '?'}{urlencode({'msg': msg})}")
            self.send(404, layout("", "Не найдено", '<h1>Не найдено</h1>', v))
        except Exception as ex:
            print("admin error:", url.path, repr(ex), file=sys.stderr)
            self.send(500, html_doc("Ошибка", f'<main class="wrap"><h1>Ошибка</h1><pre>{e(repr(ex))}</pre><a href="/">На главную</a></main>'))

    def tg_auth(self, form):
        """initData from Telegram.WebApp -> session cookie for an active agent (or ADMIN_TG_IDS)."""
        def answer(code, **body):
            self.send(code, json.dumps(body, ensure_ascii=False), "application/json; charset=utf-8")
        user = check_init_data(form.get("init_data"))
        if not user:
            time.sleep(0.5)
            return answer(403, ok=False, error="Не удалось проверить вход через Telegram. Закройте и откройте панель заново.")
        full = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or None
        a = core.agent(user["id"], full, user.get("username"))  # binds an agent added in the admin by @username
        if not a and user["id"] not in admin_tg_ids():
            return answer(403, ok=False, error="Панель только для агентов агентства. Чтобы подключиться, отправьте боту /agent <код>.")
        cookie = self.cookie(make_token(who=f"tg{user['id']}"), TG_SESSION, tg=True)
        body = json.dumps({"ok": True, "next": safe_next(form.get("next"), "/leads")})
        self.send(200, body, "application/json; charset=utf-8", [("Set-Cookie", cookie)])

    def journey_answers(self):
        def answer(code, **body):
            self.send(code, json.dumps(body, ensure_ascii=False), "application/json; charset=utf-8")
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin or urlparse(origin).netloc not in self.hosts():  # only from the /journey page itself
            return answer(403, ok=False, error="forbidden")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > JOURNEY_MAX_BYTES:
            return answer(413, ok=False, error="Слишком длинные ответы, сократите, пожалуйста.")
        clean = journey_clean(self.rfile.read(n))
        if not clean:
            return answer(400, ok=False, error="Заполните хотя бы один ответ.")
        name, answers = clean
        ip = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or self.client_address[0]
        h, at = ip_hash(ip), db.now()
        hour_ago = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))
        with db.tx() as c:
            if c.execute("SELECT COUNT(*) FROM journey_answers WHERE ip_hash = ? AND created_at > ?", (h, hour_ago)).fetchone()[0] >= JOURNEY_PER_HOUR:
                return answer(429, ok=False, error="Слишком много отправок, попробуйте через час.")
            c.execute("INSERT INTO journey_answers (created_at, name, payload, ip_hash) VALUES (?, ?, ?, ?)",
                      (at, name or None, json.dumps({"name": name, "answers": answers}, ensure_ascii=False), h))
        later(journey_notify, name, answers)
        return answer(200, ok=True)

    def photo(self, path):
        m = re.fullmatch(r"/photo/([\w.-]+)/(\d{1,3})", path)
        f = m and photo_file(m[1], int(m[2]))
        if not f:
            return self.send(404, b"", "text/plain")
        self.send(200, thumbnail(str(f), f.stat().st_mtime), "image/jpeg", [("Cache-Control", "private, max-age=86400")])

    def log_message(self, fmt, *args):
        if os.environ.get("ADMIN_LOG") == "1":
            super().log_message(fmt, *args)


def serve(host, port):
    return ThreadingHTTPServer((host, port), Handler)


def main():
    global PHOTO_DIR
    os.chdir(ROOT)
    db.load_env()
    db.PATH = os.environ.get("APP_DB", db.PATH)
    PHOTO_DIR = Path(os.environ.get("PHOTO_DIR", PHOTO_DIR))
    if not password() and not bot_token():
        sys.exit("Set ADMIN_PASSWORD (browser login) and/or BOT_TOKEN (Telegram Mini App login) in .env")
    db.init()
    host, port = os.environ.get("ADMIN_HOST", "127.0.0.1"), int(os.environ.get("ADMIN_PORT", "8080"))
    print(f"admin: http://{host}:{port}  db={db.PATH}  password={'on' if password() else 'off'}  "
          f"telegram={'on' if bot_token() else 'off'}  mini app={core.app_url() or 'AGENT_APP_URL not set'}  "
          f"sheets={'on' if sheets.configured() else 'off'}")
    serve(host, port).serve_forever()


if __name__ == "__main__":
    main()
