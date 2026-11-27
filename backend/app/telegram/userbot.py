"""Operator userbot: the operator's own Telegram account (Telethon). The dialogue lives in the operator's DMs: a client
who never contacted the operator gets ❤️ as a t.me/<operator>?text=«…вариант #<obj> · …» link (notify.like_button), and
that first DM makes the lead here; the operator talks to clients there, and the CRM keeps the history. Needs API_ID +
API_HASH; the login itself is a QR code in the CRM (Управление → Настройки → «Аккаунт оператора»): without a login the
process waits for the admin's «Подключить», shows the QR (+ the 2FA password if the account has one), saves the
Telethon StringSession into settings.userbot_session and starts working. OPERATOR_USERBOT_SESSION (.env) is the old way
and is used only while no QR login is saved.

Run: `cd backend && python -m app.telegram.userbot` (compose: the commented `userbot` service). One process owns the
session (two clients on one session get it revoked), so the CRM/bot reach it over HTTP: USERBOT_URL (e.g.
http://userbot:8091), header X-Userbot-Token = SECRET_KEY, POST /send {client_id, text, options?, choice?, inline?}; a
contract to its client: {client_id | peer (@username / +phone), text, files: [{name, b64}]} (send_files).

- incoming «…вариант #<obj>…» (LIKE_CODE) -> leads.create_from_like for that object + message_from_client in it;
- any other incoming private message -> the client (clients.external_id = sender id, else the @username) and its latest open lead
  -> lead event message_from_client (via dm);
- the operator's own messages to a client typed in Telegram -> message_to_client (via dm);
- client cards (notify.client_card: slots, viewing reminder, survey, refusal reason, T14): a user account can't attach
  buttons, but it can send an inline-bot result («via @bot») that carries the bot's keyboard:
  inline_query(BOT_USERNAME, "<kind>:<token>") -> results[0].click(client). The presses go to the bot
  (journey.on_client_journey, inline_message_id). On start the userbot saves its Telegram id (settings.userbot_tg_id):
  the bot answers those queries from that account only. If the inline query fails (inline mode off, bot down) the
  options go as a numbered list («1. Завтра 12:00»);
- clients.state["dm_choice"] = what the client's next DM answers (dm_answer): {kind, lead, options: [callback data]}
  -> a reply «1»/«2.»/«#3» does that button (leads.client_action); slots: a reply that reads as a time is the own
  time; {own} after «Другое время» -> leads.client_own_time; {comment} after the refusal reason «Другое» ->
  leads.client_comment; {kind: t14, deal} -> «3 2 3» = the three T14 scores; {problem: deal} -> a T14 complaint.

HTTP (compose network only, X-Userbot-Token = SECRET_KEY): POST /send; POST /photo {tg_id?, username?} (a team member's
profile photo for app.telegram.avatars); GET /status; POST /login (start the QR),
/password {password} (2FA, never stored), /logout (log the session out in Telegram and forget it).

Creating a StringSession by hand instead (the operator logs in THEMSELVES, on their own computer):
    pip install telethon
    python -c "from telethon.sync import TelegramClient; from telethon.sessions import StringSession; \
import os; c = TelegramClient(StringSession(), int(os.environ['API_ID']), os.environ['API_HASH']); c.start(); \
print(c.session.save())"
API_ID/API_HASH come from https://my.telegram.org (the operator's account, «API development tools»). The printed string
is a full login: put it only into the server .env as OPERATOR_USERBOT_SESSION; revoke it in Telegram → Settings →
Devices if it leaks."""
import asyncio
import hmac
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.telegram import store, texts
from app.telegram.client import conf

log = logging.getLogger(__name__)
OPEN = ("new", "operator_taken", "agent_assigned", "slots_sent", "time_chosen", "viewing_done", "contract", "deal_pending", "thinking",
        "in_work", "viewing")
PICK_HINT = {"ru": "Ответьте цифрой варианта.", "en": "Reply with the option number.", "ka": "უპასუხეთ ვარიანტის ნომრით."}
TIME_PICK_HINT = {"ru": "Ответьте цифрой варианта или напишите своё время.", "en": "Reply with the option number or write your own time.",
                  "ka": "უპასუხეთ ვარიანტის ნომრით ან დაწერეთ თქვენთვის სასურველი დრო."}
T14_SCORES = re.compile(r"\s*([0-3])\D{1,3}([0-3])\D{1,3}([0-3])\s*")
SENT = set()  # (peer id, text) sent by send(): the outgoing-message handler must not log them twice
# a reply that reads as a time («завтра после 18», «15:30», «tomorrow 6 pm») while slots wait = the client's own time
# the object code in notify.LIKE_DM («вариант #12 · …»): a property id, else a listing id
LIKE_CODE = re.compile(r"(?:вариант|option|ვარიანტი) #([\w-]+)", re.I)
LIKE_POST = re.compile(r"t\.me/([A-Za-z]\w{3,})/(\d+)")  # notify.LIKE_DM_POST: a link to the object's post in our channel
TIME_HINT = re.compile(r"\d{1,2}[:.]\d{2}|\d\s*(?:ч|h|am|pm)\b|после|after|завтра|сегодня|tomorrow|today|ხვალ|დღეს|შემდეგ", re.I)


def enabled():
    return all(conf(k).strip() for k in ("API_ID", "API_HASH"))


def reader() -> bool:
    """USERBOT_ROLE=reader: the second process (compose service «reader», Stanislav 11-22): an account that only reads
    public channels for the parser (POST /channel) — flats_in_georgia banned the operator's. No DMs, no sending."""
    return os.environ.get("USERBOT_ROLE", "operator") == "reader"


def session_key() -> str:
    return "reader_session" if reader() else "userbot_session"


# the running process: the Telethon client, who is logged in, and the QR login in progress (shown in the CRM)
STATE = {"client": None, "me": None, "login": {"status": "idle"}, "start": None, "password": None, "code": None, "phone": None,
         "loop": None}
LOGIN_WINDOW_S = 300  # a QR (refreshed every ~30 s) or a 2FA prompt waits this long, then the admin presses again


def stored_session():
    """The QR login saved by this process wins; OPERATOR_USERBOT_SESSION (.env) is the old manual way."""
    r = store.q("SELECT value FROM settings WHERE key = %s", (session_key(),), one=True)
    v = (r and r["value"] or "").strip()
    return "" if v == "-" or reader() and not v else v or conf("OPERATOR_USERBOT_SESSION").strip()


def save_session(value):
    """value: a StringSession; "" = logged out from the CRM ("-": the .env session is not used either)."""
    store.q("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (session_key(), value or "-"))
    if not value and not reader():
        store.q("DELETE FROM settings WHERE key = 'userbot_tg_id'")


def status():
    me = STATE["me"]
    return {"enabled": enabled(), "authorized": bool(me),
            "me": me and {"id": me.id, "username": me.username, "name": " ".join(filter(None, [me.first_name, me.last_name]))},
            "login": {k: v for k, v in dict(STATE["login"]).items() if k in ("status", "url", "error", "expires")}}


async def _phone_login(client, phone, L, deadline) -> bool:
    """Login by the phone number (Stanislav 11-22: «без QR-кода»): Telegram sends a code to the account's other sessions
    (or an SMS), the owner types it in the CRM -> True once signed in; False: the window closed. 2FA: the caller's
    SessionPasswordNeededError branch. The code passes through, never stored."""
    from telethon.errors import PhoneCodeInvalidError
    sent = await client.send_code_request(phone)
    STATE["code"] = asyncio.get_running_loop().create_future()
    L.update(status="code", url=None, error=None)
    while True:
        try:
            code = await asyncio.wait_for(STATE["code"], max(1, deadline - time.time()))
        except asyncio.TimeoutError:
            STATE["login"] = {"status": "expired"}
            return False
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            return True
        except PhoneCodeInvalidError:
            STATE["code"] = asyncio.get_running_loop().create_future()
            L.update(error="wrong_code")


async def login(client):
    """QR (or phone + code: STATE["phone"], POST /phone) login on a connected, not authorized client -> True once logged
    in (session saved). STATE["login"]: status qr (url = tg://login?token=…, scan in Telegram → Settings → Devices) |
    code (the code Telegram sent) -> password (2FA) -> done | expired | error."""
    from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError
    phone, STATE["phone"] = STATE["phone"], None
    L = STATE["login"] = {"status": "starting"}
    deadline = time.time() + LOGIN_WINDOW_S
    try:
        try:
            if phone:
                if not await _phone_login(client, phone, L, deadline):
                    return False
            else:
                qr = await client.qr_login()
                while True:
                    L.update(status="qr", url=qr.url, expires=qr.expires.timestamp())
                    try:
                        await qr.wait()
                        break
                    except asyncio.TimeoutError:  # this token expired: a new one, until the window closes
                        if time.time() > deadline:
                            STATE["login"] = {"status": "expired"}
                            return False
                        await qr.recreate()
        except SessionPasswordNeededError:
            # the future goes up before the status that invites a password: the HTTP thread may POST it at once
            STATE["password"] = asyncio.get_running_loop().create_future()
            L.update(status="password", url=None, error=None)
            while True:
                try:
                    pw = await asyncio.wait_for(STATE["password"], max(1, deadline - time.time()))
                except asyncio.TimeoutError:
                    STATE["login"] = {"status": "expired"}
                    return False
                try:
                    await client.sign_in(password=pw)
                    break
                except PasswordHashInvalidError:
                    STATE["password"] = asyncio.get_running_loop().create_future()
                    L.update(error="wrong_password")
    except Exception as e:  # noqa: BLE001 — shown in the CRM, the admin presses again
        log.warning("userbot login failed: %r", e)
        STATE["login"] = {"status": "error", "error": type(e).__name__}
        return False
    finally:
        STATE["password"] = STATE["code"] = None
    save_session(client.session.save())
    STATE["login"] = {"status": "done"}
    return True


def set_state(client_id, **kv):
    """Merge keys into clients.state in one statement (no read-modify-write)."""
    store.q("UPDATE clients SET state = (COALESCE(NULLIF(state, ''), '{}')::jsonb || %s::jsonb)::text WHERE id = %s",
            (json.dumps(kv, ensure_ascii=False), client_id))


# ---------- the CRM / bot side ----------

def forget_choice(client_id, data):
    """A card answered by its button (the bot): drop dm_choice if it waits for that card (`data` among its options, or
    its kind), so a number typed later doesn't repeat the answer."""
    store.q("UPDATE clients SET state = (COALESCE(NULLIF(state, ''), '{}')::jsonb - 'dm_choice')::text WHERE id = %s AND "
            "(COALESCE(NULLIF(state, ''), '{}')::jsonb->'dm_choice'->'options' ? %s "
            "OR COALESCE(NULLIF(state, ''), '{}')::jsonb->'dm_choice'->>'kind' = %s)", (client_id, data, data))


def send_files(text, files, client_id=None, peer=None):
    """Documents with `text` as the first one's caption (the contract + registry extract, 11-13) to a client row or a
    peer «@username» / «+995…» -> {"message_id": …} or None. files: [(name, bytes)]."""
    url = conf("USERBOT_URL").strip().rstrip("/")
    if not url or not (client_id or peer):
        return None
    try:
        import base64
        import httpx
        r = httpx.post(f"{url}/send", json={"client_id": client_id, "peer": peer, "text": text,
                                            "files": [{"name": n, "b64": base64.b64encode(b).decode()} for n, b in files]},
                       headers={"X-Userbot-Token": conf("SECRET_KEY")}, timeout=90)
        body = r.json()
        return body if r.status_code == 200 and body.get("ok") else None
    except Exception as e:  # noqa: BLE001 — the caller reports it
        log.warning("userbot files to %s failed: %r", client_id or peer, e)
        return None


def delete(chat, ids, album=False):
    """Deletes messages in a channel/group through the userbot (USERBOT_URL; the operator must be its admin with
    «delete messages») -> the deleted ids or None. album: also the rest of each message's album (notify.delete_post)."""
    url = conf("USERBOT_URL").strip().rstrip("/")
    if not url:
        return None
    try:
        import httpx
        r = httpx.post(f"{url}/delete", json={"chat": str(chat), "ids": [int(i) for i in ids], "album": album},
                       headers={"X-Userbot-Token": conf("SECRET_KEY")}, timeout=60)
        body = r.json()
        return body.get("ids") if r.status_code == 200 and body.get("ok") else None
    except Exception as e:  # noqa: BLE001 — the caller reports it
        log.warning("userbot delete in %s failed: %r", chat, e)
        return None


def send_media(chat, key, caption=None):
    """A stored file (media key) into a chat as a document through the operator account — for videos over the Bot API's
    50 MB (Stanislav 11-24: «давай аккаунт отправляет»; a user account sends up to 2 GB). The account must be a member
    of the chat. -> the message id or None."""
    url = conf("USERBOT_URL").strip().rstrip("/")
    if not url:
        return None
    try:
        import httpx
        r = httpx.post(f"{url}/media", json={"chat": str(chat), "key": key, "caption": caption},
                       headers={"X-Userbot-Token": conf("SECRET_KEY")}, timeout=900)
        body = r.json()
        return body.get("message_id") if r.status_code == 200 and body.get("ok") else None
    except Exception as e:  # noqa: BLE001 — the caller falls back to a note
        log.warning("userbot media to %s failed: %r", chat, e)
        return None


async def send_stored(client, chat, key, caption=None):
    """Telethon side of send_media(): fetch the key (s3 or the volume) into a temp dir and send it as a document."""
    import tempfile

    from app import storage
    with tempfile.TemporaryDirectory() as tmp:
        path = await asyncio.to_thread(storage.fetch, key, tmp)
        if not path:
            raise FileNotFoundError(key)
        ent = await client.get_input_entity(int(chat) if chat.lstrip("-").isdigit() else chat.lstrip("@"))
        m = await client.send_file(ent, path, caption=caption or None, force_document=True)
        return m.id


def send_to_client(cl, text, options=None, choice=None, inline=None):
    """Send through the userbot (USERBOT_URL) -> {"message_id": …} or None (not configured / failed). inline: the bot's
    inline query to send as the message (options are then the numbered fallback)."""
    url = conf("USERBOT_URL").strip().rstrip("/")
    cid = cl.get("id") if isinstance(cl, dict) else getattr(cl, "id", None)
    if not url or not cid:
        return None
    try:
        import httpx
        r = httpx.post(f"{url}/send", json={"client_id": cid, "text": text, "options": options, "choice": choice, "inline": inline},
                       headers={"X-Userbot-Token": conf("SECRET_KEY")}, timeout=30)
        body = r.json()
        return body if r.status_code == 200 and body.get("ok") else None
    except Exception as e:  # noqa: BLE001 — the caller reports it
        log.warning("userbot send to client %s failed: %r", cid, e)
        return None


# ---------- the userbot side (sync parts: tests call them with a fake Telethon client) ----------

def match_client(tg_id, username=None):
    """clients row of a Telegram user: by id (external_id), else by @username (case-insensitive)."""
    r = store.q("SELECT * FROM clients WHERE channel = 'tg' AND external_id = %s ORDER BY id DESC LIMIT 1", (str(tg_id),), one=True)
    if not r and username:
        r = store.q("SELECT * FROM clients WHERE channel = 'tg' AND lower(username) = lower(%s) ORDER BY id DESC LIMIT 1",
                    (username.lstrip("@"),), one=True)
    if r:
        r["state"] = store.jload(r.get("state"), {})
    return r


def open_lead(client_id):
    return store.q("SELECT * FROM leads WHERE client_id = %s AND status = ANY(%s) ORDER BY updated_at DESC, id DESC LIMIT 1",
                   (client_id, list(OPEN)), one=True)


def options_text(text, options, lang, kind=None):
    hint = (TIME_PICK_HINT if kind == "slots" else PICK_HINT)[texts.lang_of(lang)]
    return text + "\n\n" + "\n".join(f"{n}. {o}" for n, o in enumerate(options, 1)) + "\n\n" + hint


def parse_choice(text, n):
    """«1», «2.», «#3», «3)» -> 1..n; anything else None."""
    m = re.fullmatch(r"\s*#?(\d{1,2})\s*[.)]?\s*", text or "")
    return int(m[1]) if m and 1 <= int(m[1]) <= n else None


def _svc(fn, *a, **kw):
    """A backend service in its own session -> (result, None) or (None, DomainError code)."""
    from app.db import SessionLocal
    from app.services.errors import DomainError
    try:
        with SessionLocal() as s:
            return fn(s, *a, **kw), None
    except DomainError as e:
        return None, e.code


def dm_answer(cl, choice, text):
    """The client's DM as the answer dm_choice waits for -> 'slot' | 'choice' | 'own_time' | 'comment' | 't14' |
    'problem', or None (not an answer: an ordinary message)."""
    from app.services import evaluations, leads
    cid, lead_id = cl["id"], choice.get("lead")
    if choice.get("problem"):
        _, err = _svc(evaluations.create, {"deal_id": choice["problem"], "kind": "t14", "author": "client", "complaints": True,
                                           "complaints_text": text}, None)
        return None if err else "problem"
    if choice.get("kind") == "t14":
        m = T14_SCORES.fullmatch(text)
        _, err = (None, "no") if not m else _svc(evaluations.create, {"deal_id": choice["deal"], "kind": "t14", "author": "client",
                                                                      "h2o_client": int(m[1]), "h2o_owner": int(m[2]),
                                                                      "h2o_process": int(m[3])}, None)
        return None if err else "t14"
    if choice.get("comment"):
        _, err = _svc(leads.client_comment, lead_id, cid, text)
        return None if err else "comment"
    own = choice.get("own")
    n = not own and parse_choice(text, len(choice.get("options") or []))
    if n:
        data = choice["options"][n - 1]
        _, err = _svc(leads.client_action, data, cid)
        return None if err else ("slot" if data.startswith("ls:") else "choice")
    if own or (choice.get("kind") == "slots" and TIME_HINT.search(text)):
        _, err = _svc(leads.client_own_time, lead_id, text)
        return None if err else "own_time"
    return None


def like_dm(cl, text):
    """The ❤️ link's first message («…вариант #<obj> · …») -> the lead id (create_from_like: the same B1 card, auto-assign
    and card mark as a ❤️ press), or None (no code / no such object / not available to clients)."""
    from app.services import leads
    if p := LIKE_POST.search(text):
        url = f"https://t.me/{p[1]}/{p[2]}"
        r = (store.q("SELECT listing_id FROM properties WHERE post_url = %s AND listing_id IS NOT NULL ORDER BY id LIMIT 1", (url,), one=True)
             or store.q("SELECT id AS listing_id FROM listings WHERE urls LIKE %s ORDER BY id LIMIT 1", (f'%"{url}"%',), one=True))
        if not r:
            return None
        lid = r["listing_id"]
    elif m := LIKE_CODE.search(text):
        code = m[1]
        prop = code.isdigit() and store.q("SELECT listing_id FROM properties WHERE id = %s AND listing_id IS NOT NULL", (int(code),), one=True)
        lid = prop["listing_id"] if prop else code
    else:
        return None
    lead_id, _ = _svc(lambda s: leads.create_from_like(s, cl["id"], lid).id)
    return lead_id


def on_incoming(tg_id, username, text):
    """A private message to the operator -> 'unknown' | 'no_lead' | 'logged' | 'liked' | a dm_answer result (tests read it)."""
    from app.services import leads
    cl = match_client(tg_id, username)
    if not cl or not (text or "").strip():
        return "unknown"
    choice = cl["state"].get("dm_choice") or {}
    if choice and (res := dm_answer(cl, choice, text)):
        set_state(cl["id"], dm_choice=None)
        return res
    if lead_id := like_dm(cl, text):
        _, err = _svc(leads.message_from_client, lead_id, cl["id"], text)   # «Клиент написал оператору в личку»
        return "liked" if not err else err
    lead = open_lead(cl["id"])
    if not lead:  # remembered: this client's ❤️ is a plain callback from now on (notify.knows_operator)
        store.q("INSERT INTO events (entity, entity_id, action, actor_label, data, at) SELECT 'client', %s, 'dm_to_operator', 'client', "
                "'{}'::jsonb, now() WHERE NOT EXISTS (SELECT 1 FROM events WHERE entity = 'client' AND entity_id = %s AND "
                "action = 'dm_to_operator')", (str(cl["id"]), str(cl["id"])))
        return "no_lead"
    _, err = _svc(leads.message_from_client, lead["id"], cl["id"], text)
    return "logged" if not err else err


def on_outgoing(peer_id, text, me_id=None):
    """The operator typed to a client in Telegram -> message_to_client in the client's open lead."""
    if (peer_id, text) in SENT:
        SENT.discard((peer_id, text))
        return "own"
    cl = match_client(peer_id)
    lead = cl and open_lead(cl["id"])
    if not lead or not (text or "").strip():
        return "unknown"
    from app.services import leads

    def log_it(s):
        from app.services import users
        return leads.log_operator_dm(s, lead["id"], text, users.by_tg(s, me_id) if me_id else None)
    _, err = _svc(log_it)
    return "logged" if not err else err


async def _peer_entity(client, peer):
    """«@name» / «+995…» -> an input entity; a phone not in the operator's contacts is imported as a contact first
    (Telegram finds users by phone only that way, and only if their privacy allows it)."""
    if not peer.startswith("+"):
        return await client.get_input_entity(peer.lstrip("@"))
    try:
        return await client.get_input_entity(peer)
    except ValueError:
        from telethon.tl import functions, types
        res = await client(functions.contacts.ImportContactsRequest([types.InputPhoneContact(0, peer, peer, "")]))
        if not res.users:
            raise LookupError(f"no Telegram account for {peer}") from None
        return await client.get_input_entity(res.users[0])


async def photo(client, tg_id=None, username=None):
    """A user's profile photo as the operator account sees it -> bytes or None. tg_id needs the entity in the cache
    (StringSession keeps none across restarts), so @username is tried too."""
    for peer in (tg_id, username):
        if not peer:
            continue
        try:
            ent = await client.get_input_entity(int(peer) if peer == tg_id else str(peer).lstrip("@"))
            if data := await client.download_profile_photo(ent, file=bytes):
                return data
        except Exception as e:  # noqa: BLE001 — unknown id / username, privacy
            log.info("userbot photo %s: %r", peer, e)
    return None


def rich_text(rm) -> tuple[str, list[str]]:
    """A «rich» channel post (Telegram 11-2026: RichMessage of PageBlocks, no message text; flats_in_georgia since 11-16)
    -> (its text, a line per block, the links behind it)."""
    lines, links = [], []

    def text_of(x) -> str:
        if x is None or isinstance(x, (int, float, bool, bytes)):
            return ""
        if isinstance(x, str):
            return x
        if isinstance(x, list):
            return "".join(text_of(v) for v in x)
        if x.__class__.__name__ == "TextStrike":  # the old price, crossed out: «$900 ~~$1000~~»
            return ""
        if getattr(x, "url", None) and isinstance(x.url, str) and x.url.startswith("http"):
            links.append(x.url)
        return "".join(text_of(getattr(x, k, None)) for k in ("text", "texts"))

    def walk(b):
        if b.__class__.__name__ in ("PageBlockPhoto", "PageBlockVideo", "PageBlockSlideshow", "PageBlockCollage"):
            return
        if getattr(b, "blocks", None):  # details / lists: the title, then the inner blocks
            if t := text_of(getattr(b, "title", None)).strip():
                lines.append(t)
            for x in b.blocks:
                walk(x)
            return
        for item in getattr(b, "items", None) or []:  # a list's items
            for x in getattr(item, "blocks", None) or []:
                walk(x)
            if t := text_of(getattr(item, "text", None)).strip():
                lines.append("• " + t)
        if t := text_of(getattr(b, "text", None)).strip("\u2800 \n"):
            lines.append(t)

    for b in rm.blocks or []:
        walk(b)
    return "\n".join(lines), list(dict.fromkeys(links))


async def channel_posts(client, user, min_id=0, limit=200, photos=3, max_id=0):
    """A public channel's posts newer than min_id (older than max_id: paging back) as the operator account reads them
    (no join, nothing marked read) -> [{id, date, text, links, photos: [{id, b64}]}]: the rich ones and the photo posts
    with a caption."""
    import base64
    out, albums = [], {}
    async for m in client.iter_messages(user, limit=limit, min_id=int(min_id or 0), max_id=int(max_id or 0)):
        rm = getattr(m, "rich_message", None)
        if rm is not None:
            text, links = rich_text(rm)
            pics = list(rm.photos or [])
        else:  # an ordinary album / photo post: the caption is on one of its messages
            a = albums.setdefault(m.grouped_id or m.id, {"id": m.id, "date": m.date, "text": "", "links": [], "pics": []})
            if m.photo:
                a["pics"].insert(0, m.photo)
            if m.message and len(m.message) > len(a["text"]):
                a.update(text=m.message, id=m.id, date=m.date)
            continue
        out.append({"id": m.id, "date": m.date, "text": text, "links": links, "pics": pics})
    out += [a for a in albums.values() if a["text"] and a["pics"]]
    for p in out:
        got = []
        for ph in p.pop("pics")[:photos]:
            if data := await client.download_media(ph, file=bytes):
                got.append({"id": ph.id, "b64": base64.b64encode(data).decode()})
        p.update(photos=got, date=p["date"].isoformat())
    return [p for p in out if p["text"] and p["photos"]]


async def delete_messages(client, chat, ids, album=False):
    """Telethon side of delete(): «@name» / «-100…» -> the ids deleted (album: + the same grouped_id within ±9 ids)."""
    ent = await client.get_input_entity(int(chat) if chat.lstrip("-").isdigit() else chat.lstrip("@"))
    ids = [int(i) for i in ids]
    if album:
        groups = {m.grouped_id for m in await client.get_messages(ent, ids=ids) if m and m.grouped_id}
        if groups:
            near = await client.get_messages(ent, ids=sorted({i + d for i in ids for d in range(-9, 10) if i + d > 0}))
            ids = sorted({*ids, *(m.id for m in near if m and m.grouped_id in groups)})
    await client.delete_messages(ent, ids)
    return ids


async def send(client, client_id, text, options=None, choice=None, inline=None, peer=None, files=None):
    """Telethon send to a client -> message id. StringSession keeps no access hashes across restarts: a user not in the
    cache is found by @username (or after get_dialogs at start). inline: the bot's inline query whose first result is
    sent (its buttons come with it); any failure there -> `text` + numbered `options`. peer: «@username» / «+phone»
    instead of a client row; files: [{name, b64}] documents, `text` as the first one's caption."""
    if peer and not client_id:
        entity = await _peer_entity(client, peer)
    else:
        cl = match_client_by_id(client_id)
        if not cl:
            raise LookupError("no such client")
        try:
            entity = await client.get_input_entity(int(cl["external_id"]))
        except ValueError:
            if not cl.get("username"):
                raise
            entity = cl["username"]
    await _check_free_dm(client, entity, client_id or peer)
    if files:
        import base64
        import io

        from telethon import utils
        SENT.add((utils.get_peer_id(await client.get_input_entity(entity)), text))  # the caption: not a typed operator DM
        first = None
        for i, f in enumerate(files):
            doc = io.BytesIO(base64.b64decode(f["b64"]))
            doc.name = f["name"]  # Telethon's file name
            m = await client.send_file(entity, doc, caption=text if i == 0 else None, force_document=True)
            first = first or m
        return first.id
    if not client_id:
        raise ValueError("a peer without a client row: files only")
    peer = int(cl["external_id"])
    if inline:
        try:
            res = await client.inline_query(conf("BOT_USERNAME").strip().lstrip("@"), inline)
            m = await res[0].click(entity)
            if choice:
                set_state(client_id, dm_choice=choice)
            return m.id
        except Exception as e:  # noqa: BLE001 — inline mode off, bot down, no result: the numbered text
            log.warning("userbot inline %r to client %s failed: %r", inline, client_id, e)
    body = options_text(text, options, cl.get("lang"), (choice or {}).get("kind")) if options else text
    if len(SENT) > 1000:  # ponytail: own sends may never come back as events; a full reset is enough to bound it
        SENT.clear()
    SENT.add((peer, body))
    m = await client.send_message(entity, body, parse_mode=None)
    if choice:
        set_state(client_id, dm_choice=choice)
    return m.id


async def _check_free_dm(client, entity, client_id):
    """PermissionError if this client takes DMs only for Stars (paid messages) or only from Premium users; an API
    error of the check itself does not block the send (the send then fails or not on its own)."""
    from telethon.tl import functions, types
    try:
        req = await client(functions.users.GetRequirementsToContactRequest(id=[entity]))
    except Exception as e:  # noqa: BLE001
        log.info("requirements to contact client %s: %r", client_id, e)
        return
    if req and not isinstance(req[0], types.RequirementToContactEmpty):
        raise PermissionError(f"client {client_id}: {type(req[0]).__name__}")


def match_client_by_id(client_id):
    return store.q("SELECT * FROM clients WHERE id = %s", (client_id,), one=True)


# ---------- the process ----------

def serve_http(client, loop, port):
    """POST /send, GET /status, POST /login /password /logout for the CRM/bot (the compose network only;
    X-Userbot-Token = SECRET_KEY). client: the logged-in client (tests); the process sets STATE["client"] itself."""
    if client is not None:
        STATE["client"] = client

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            return hmac.compare_digest(self.headers.get("X-Userbot-Token", ""), conf("SECRET_KEY"))

        def do_GET(self):
            if self.path != "/status" or not self.allowed():
                return self.reply(403, {"ok": False})
            self.reply(200, status())

        def channel(self, p):  # POST /channel {user, min_id?, limit?, photos?}: bot/fetch_channel_web.py, rich posts
            c = STATE["client"]
            try:
                posts = asyncio.run_coroutine_threadsafe(channel_posts(
                    c, str(p["user"]).lstrip("@"), p.get("min_id") or 0, min(int(p.get("limit") or 200), 500),
                    min(int(p.get("photos") or 3), 10), p.get("max_id") or 0), loop).result(240)
                return self.reply(200, {"ok": True, "posts": posts})
            except Exception as e:  # noqa: BLE001
                log.warning("userbot /channel %s failed: %r", p.get("user"), e)
                return self.reply(502, {"ok": False, "error": repr(e)})

        def do_POST(self):
            reply = self.reply
            paths = ("/login", "/phone", "/code", "/password", "/logout", "/channel", "/exists") + (() if reader() else ("/send", "/delete", "/photo", "/media"))
            if self.path not in paths or not self.allowed():
                return reply(403, {"ok": False})
            p = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/login":
                if not STATE["me"] and STATE["start"] and STATE["login"].get("status") not in ("qr", "password", "starting"):
                    loop.call_soon_threadsafe(STATE["start"].set)
                    STATE["login"] = {"status": "starting"}
                return reply(200, status())
            if self.path == "/phone":  # {phone}: login by the number, Telegram sends a code
                phone = "+" + re.sub(r"\D", "", str(p.get("phone") or ""))
                busy = STATE["login"].get("status") in ("qr", "password", "starting", "code")
                if len(phone) >= 9 and not STATE["me"] and STATE["start"] and not busy:
                    STATE["phone"] = phone
                    STATE["login"] = {"status": "starting"}
                    loop.call_soon_threadsafe(STATE["start"].set)
                return reply(200, status())
            if self.path in ("/password", "/code"):
                k = self.path[1:]
                fut = STATE[k]
                if fut and not fut.done() and p.get(k):
                    STATE["login"].update(error=None)  # before the hand-over: a fast «wrong_…» must not be wiped
                    loop.call_soon_threadsafe(fut.set_result, str(p[k]).strip())
                return reply(200, status())
            if self.path == "/logout":
                if STATE["me"] and STATE["client"]:
                    asyncio.run_coroutine_threadsafe(STATE["client"].log_out(), loop).result(30)
                save_session("")
                STATE["me"] = None
                return reply(200, status())
            c = STATE["client"]
            if not STATE["me"] and client is None:
                return reply(503, {"ok": False, "error": "operator account is not logged in"})
            if self.path == "/photo":  # {tg_id?, username?} -> {ok, b64: the profile photo or null} (avatars.from_userbot)
                import base64
                data = asyncio.run_coroutine_threadsafe(photo(c, p.get("tg_id"), p.get("username")), loop).result(40)
                return reply(200, {"ok": True, "b64": base64.b64encode(data).decode() if data else None})
            if self.path == "/channel":
                return self.channel(p)
            if self.path == "/exists":  # {user, ids} -> the ids still in the channel (post_sync: deleted posts)
                try:
                    ms = asyncio.run_coroutine_threadsafe(c.get_messages(str(p["user"]).lstrip("@"), ids=[int(i) for i in p["ids"]]),
                                                          loop).result(60)
                    return reply(200, {"ok": True, "ids": [m.id for m in ms if m]})
                except Exception as e:  # noqa: BLE001
                    log.warning("userbot /exists failed: %r", e)
                    return reply(502, {"ok": False, "error": repr(e)})
            if self.path == "/media":  # {chat, key, caption?}: a big video into the originals chat (send_media)
                try:
                    mid = asyncio.run_coroutine_threadsafe(send_stored(c, p["chat"], p["key"], p.get("caption")), loop).result(880)
                    return reply(200, {"ok": True, "message_id": mid})
                except Exception as e:  # noqa: BLE001
                    log.warning("userbot /media failed: %r", e)
                    return reply(502, {"ok": False, "error": repr(e)})
            if self.path == "/delete":
                try:
                    ids = asyncio.run_coroutine_threadsafe(delete_messages(c, p["chat"], p["ids"], p.get("album")), loop).result(50)
                    return reply(200, {"ok": True, "ids": ids})
                except Exception as e:  # noqa: BLE001
                    log.warning("userbot /delete failed: %r", e)
                    return reply(502, {"ok": False, "error": repr(e)})
            try:
                cid = int(p["client_id"]) if p.get("client_id") else None
                mid = asyncio.run_coroutine_threadsafe(send(c, cid, p["text"], p.get("options"), p.get("choice"), p.get("inline"),
                                                            p.get("peer"), p.get("files")), loop).result(80)
                reply(200, {"ok": True, "message_id": mid})
            except Exception as e:  # noqa: BLE001
                log.warning("userbot /send failed: %r", e)
                reply(502, {"ok": False, "error": repr(e)})
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def cache_dialogs(client, limit=300, page=100):
    """Fills the entity cache (access hashes) for clients who already wrote: raw GetDialogs pages (Telethon caches
    the users / chats of every response). Not get_dialogs(): Telethon 1.46 dies on a «DialogCommunity» (no .peer) and
    the rest of the list was lost; such dialogs are skipped here. -> the number of dialogs seen."""
    from telethon import utils
    from telethon.tl import functions, types
    seen, offset_date, offset_id, offset_peer = 0, None, 0, types.InputPeerEmpty()
    while seen < limit:
        r = await client(functions.messages.GetDialogsRequest(offset_date=offset_date, offset_id=offset_id, offset_peer=offset_peer,
                                                              limit=page, hash=0))
        dialogs = getattr(r, "dialogs", [])
        seen += len(dialogs)
        last = next((d for d in reversed(dialogs) if getattr(d, "peer", None)), None)
        if len(dialogs) < page or not last:
            break
        ents = {utils.get_peer_id(x): x for x in [*r.users, *r.chats]}
        pid = utils.get_peer_id(last.peer)
        msg = next((m for m in r.messages if m.id == last.top_message and utils.get_peer_id(m.peer_id) == pid), None)
        if pid not in ents:
            break
        offset_date, offset_id, offset_peer = msg and msg.date, last.top_message, utils.get_input_peer(ents[pid])
    return seen


async def run(client, http=True):
    """Work as a connected, logged-in client until it disconnects (logged out in Telegram, session revoked, network).
    http: also start the HTTP server (a local stand with its own session; the process starts it once in amain)."""
    from telethon import events
    me = STATE["me"] = await client.get_me()
    STATE["client"] = client
    if reader():  # reads channels on request (POST /channel), nothing else
        if http:
            serve_http(client, asyncio.get_running_loop(), int(conf("USERBOT_PORT", "8091")))
        log.info("reader up as @%s", me.username)
        try:
            await client.run_until_disconnected()
        finally:
            STATE["me"] = None
        return
    store.q("INSERT INTO settings (key, value) VALUES ('userbot_tg_id', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (str(me.id),))  # the bot answers «slots:» inline queries from this account only
    try:
        log.info("userbot: %s dialogs cached", await cache_dialogs(client))
    except Exception as e:  # noqa: BLE001 — the cache stays partial, the account works on (it restarted every 10 s, 11-22)
        log.warning("userbot: dialogs not cached: %r", e)

    @client.on(events.NewMessage(incoming=True, func=lambda ev: ev.is_private))
    async def incoming(ev):
        sender = await ev.get_sender()
        if getattr(sender, "bot", False):
            return
        res = await asyncio.to_thread(on_incoming, ev.sender_id, getattr(sender, "username", None), ev.raw_text)
        log.info("DM from %s: %s", ev.sender_id, res)

    @client.on(events.NewMessage(outgoing=True, func=lambda ev: ev.is_private and not ev.message.via_bot_id))  # not the slot cards
    async def outgoing(ev):
        await asyncio.to_thread(on_outgoing, ev.chat_id, ev.raw_text, me.id)

    if http:
        serve_http(client, asyncio.get_running_loop(), int(conf("USERBOT_PORT", "8091")))
    log.info("userbot up as @%s", me.username)
    try:
        await client.run_until_disconnected()
    finally:
        STATE["me"] = None


async def amain():
    """Forever: log in from the saved session and work; without a login wait for the CRM's «Подключить» (QR)."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    loop = STATE["loop"] = asyncio.get_running_loop()
    STATE["start"] = asyncio.Event()
    port = int(conf("USERBOT_PORT", "8091"))
    serve_http(None, loop, port)
    log.info("userbot HTTP on :%s", port)
    while True:
        client = TelegramClient(StringSession(stored_session()), int(conf("API_ID")), conf("API_HASH"))
        STATE["client"] = client
        try:
            await client.connect()
            if await client.is_user_authorized():
                await run(client, http=False)
            else:
                log.info("userbot: no login, waiting for the QR login from the CRM")
                STATE["start"].clear()
                if STATE["login"].get("status") == "starting":
                    STATE["start"].set()
                await STATE["start"].wait()
                await login(client)
        except Exception as e:  # noqa: BLE001 — network or a revoked session: next round
            log.warning("userbot: %r", e, exc_info=True)
            await asyncio.sleep(10)
        finally:
            await client.disconnect()


def main():
    from app import logs
    logs.setup("userbot")
    if not enabled():
        raise SystemExit("userbot disabled: set API_ID and API_HASH (https://my.telegram.org)")
    asyncio.run(amain())


if __name__ == "__main__":
    main()
