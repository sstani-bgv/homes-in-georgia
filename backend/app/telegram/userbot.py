"""Operator userbot: the operator's own Telegram account (Telethon). The dialogue lives in the operator's DMs: a client
who never contacted the operator gets ❤️ as a t.me/<operator>?text=«…вариант #<obj> · …» link (notify.like_button), and
that first DM makes the lead here; the operator talks to clients there, and the CRM keeps the history. Enabled only when OPERATOR_USERBOT_SESSION (a Telethon
StringSession) + API_ID + API_HASH are set.

Run: `cd backend && python -m app.telegram.userbot` (compose: the commented `userbot` service). One process owns the
session (two clients on one session get it revoked), so the CRM/bot reach it over HTTP: USERBOT_URL (e.g.
http://userbot:8091), header X-Userbot-Token = SECRET_KEY, POST /send {client_id, text, options?, choice?, inline?}.

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

Creating the StringSession (the operator logs in THEMSELVES, on their own computer; nobody else sees the code):
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
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.telegram import store, texts
from app.telegram.client import conf

log = logging.getLogger(__name__)
OPEN = ("new", "operator_taken", "agent_assigned", "slots_sent", "time_chosen", "viewing_done", "deal_pending", "thinking",
        "in_work", "viewing")
PICK_HINT = {"ru": "Ответьте цифрой варианта.", "en": "Reply with the option number.", "ka": "უპასუხეთ ვარიანტის ნომრით."}
TIME_PICK_HINT = {"ru": "Ответьте цифрой варианта или напишите своё время.", "en": "Reply with the option number or write your own time.",
                  "ka": "უპასუხეთ ვარიანტის ნომრით ან დაწერეთ თქვენთვის სასურველი დრო."}
T14_SCORES = re.compile(r"\s*([0-3])\D{1,3}([0-3])\D{1,3}([0-3])\s*")
SENT = set()  # (peer id, text) sent by send(): the outgoing-message handler must not log them twice
# a reply that reads as a time («завтра после 18», «15:30», «tomorrow 6 pm») while slots wait = the client's own time
# the object code in notify.LIKE_DM («вариант #12 · …»): a property id, else a listing id
LIKE_CODE = re.compile(r"(?:вариант|option|ვარიანტი) #([\w-]+)", re.I)
TIME_HINT = re.compile(r"\d{1,2}[:.]\d{2}|\d\s*(?:ч|h|am|pm)\b|после|after|завтра|сегодня|tomorrow|today|ხვალ|დღეს|შემდეგ", re.I)


def enabled():
    return all(conf(k).strip() for k in ("OPERATOR_USERBOT_SESSION", "API_ID", "API_HASH"))


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
    m = LIKE_CODE.search(text)
    if not m:
        return None
    code = m[1]
    prop = code.isdigit() and store.q("SELECT listing_id FROM properties WHERE id = %s AND listing_id IS NOT NULL", (int(code),), one=True)
    lead_id, _ = _svc(lambda s: leads.create_from_like(s, cl["id"], prop["listing_id"] if prop else code).id)
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


async def send(client, client_id, text, options=None, choice=None, inline=None):
    """Telethon send to a client -> message id. StringSession keeps no access hashes across restarts: a user not in the
    cache is found by @username (or after get_dialogs at start). inline: the bot's inline query whose first result is
    sent (its buttons come with it); any failure there -> `text` + numbered `options`."""
    cl = match_client_by_id(client_id)
    if not cl:
        raise LookupError("no such client")
    peer = int(cl["external_id"])
    try:
        entity = await client.get_input_entity(peer)
    except ValueError:
        if not cl.get("username"):
            raise
        entity = cl["username"]
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


def match_client_by_id(client_id):
    return store.q("SELECT * FROM clients WHERE id = %s", (client_id,), one=True)


# ---------- the process ----------

def serve_http(client, loop, port):
    """POST /send for the CRM/bot (the compose network only; X-Userbot-Token = SECRET_KEY)."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            def reply(code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            if self.path != "/send" or not hmac.compare_digest(self.headers.get("X-Userbot-Token", ""), conf("SECRET_KEY")):
                return reply(403, {"ok": False})
            try:
                p = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                mid = asyncio.run_coroutine_threadsafe(send(client, int(p["client_id"]), p["text"], p.get("options"), p.get("choice"),
                                                            p.get("inline")), loop).result(30)
                reply(200, {"ok": True, "message_id": mid})
            except Exception as e:  # noqa: BLE001
                log.warning("userbot /send failed: %r", e)
                reply(502, {"ok": False, "error": repr(e)})
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def run():
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(conf("OPERATOR_USERBOT_SESSION").strip()), int(conf("API_ID")), conf("API_HASH"))
    await client.connect()
    if not await client.is_user_authorized():  # never an interactive login on the server
        raise SystemExit("userbot: OPERATOR_USERBOT_SESSION is not a valid login; the operator must create a new one")
    me = await client.get_me()
    store.q("INSERT INTO settings (key, value) VALUES ('userbot_tg_id', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (str(me.id),))  # the bot answers «slots:» inline queries from this account only
    await client.get_dialogs(limit=300)  # fills the entity cache (access hashes) for clients who already wrote

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

    port = int(conf("USERBOT_PORT", "8091"))
    serve_http(client, asyncio.get_running_loop(), port)
    log.info("userbot up as @%s, /send on :%s", me.username, port)
    await client.run_until_disconnected()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not enabled():
        raise SystemExit("userbot disabled: set OPERATOR_USERBOT_SESSION, API_ID and API_HASH")
    asyncio.run(run())


if __name__ == "__main__":
    main()
