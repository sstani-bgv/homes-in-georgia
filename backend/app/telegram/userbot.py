"""Operator userbot: the operator's own Telegram account (Telethon), so after ❤️ the dialogue lives in the operator's DMs
(notify.handoff gives the client a t.me/<operator>?text=… button; the client sends the first message) and stays visible
in the CRM. Enabled only when OPERATOR_USERBOT_SESSION (a Telethon StringSession) + API_ID + API_HASH are set.

Run: `cd backend && python -m app.telegram.userbot` (compose: the commented `userbot` service). One process owns the
session (two clients on one session get it revoked), so the CRM/bot reach it over HTTP: USERBOT_URL (e.g.
http://userbot:8091), header X-Userbot-Token = SECRET_KEY, POST /send {client_id, text, options?, choice?}.

- incoming private message -> the client (clients.external_id = sender id, else the @username) and its latest open lead
  -> lead event message_from_client (label client_dm); clients.state["operator_dm"] marks that the client wrote, from
  then on leads.message_client and notify.slots_to_client go through the userbot (else the bot, as before);
- the operator's own messages to a client typed in Telegram -> message_to_client (label operator_dm);
- choices: a user account can't send inline buttons, so options go as a numbered list («1. Завтра 12:00») and
  clients.state["dm_choice"] keeps what the numbers mean; a reply «1»/«2.»/«#3» picks the slot (leads.choose_slot),
  any other reply while slots wait is the client's own time (leads.client_own_time). Bot buttons keep working as well.

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
PICK_HINT = {"ru": "Ответьте цифрой варианта или напишите своё время.", "en": "Reply with the option number or write your own time.",
             "ka": "უპასუხეთ ვარიანტის ნომრით ან დაწერეთ თქვენთვის სასურველი დრო."}
SENT = set()  # (peer id, text) sent by send(): the outgoing-message handler must not log them twice
# a reply that reads as a time («завтра после 18», «15:30», «tomorrow 6 pm») while slots wait = the client's own time
TIME_HINT = re.compile(r"\d{1,2}[:.]\d{2}|\d\s*(?:ч|h|am|pm)\b|после|after|завтра|сегодня|tomorrow|today|ხვალ|დღეს|შემდეგ", re.I)


def enabled():
    return all(conf(k).strip() for k in ("OPERATOR_USERBOT_SESSION", "API_ID", "API_HASH"))


def state(cl):
    """clients.state of a row dict (decoded or not) or an ORM Client."""
    st = cl.get("state") if isinstance(cl, dict) else getattr(cl, "state", None)
    return st if isinstance(st, dict) else store.jload(st, {})


def dm_active(cl):
    """The client has written to the operator's DM and the userbot is reachable: talk there instead of the bot."""
    return bool(conf("USERBOT_URL").strip() and state(cl).get("operator_dm"))


def set_state(client_id, **kv):
    """Merge keys into clients.state in one statement (no read-modify-write)."""
    store.q("UPDATE clients SET state = (COALESCE(NULLIF(state, ''), '{}')::jsonb || %s::jsonb)::text WHERE id = %s",
            (json.dumps(kv, ensure_ascii=False), client_id))


# ---------- the CRM / bot side ----------

def send_to_client(cl, text, options=None, choice=None):
    """Send through the userbot (USERBOT_URL) -> {"message_id": …} or None (not configured / failed: use the bot)."""
    url = conf("USERBOT_URL").strip().rstrip("/")
    cid = cl.get("id") if isinstance(cl, dict) else getattr(cl, "id", None)
    if not url or not cid:
        return None
    try:
        import httpx
        r = httpx.post(f"{url}/send", json={"client_id": cid, "text": text, "options": options, "choice": choice},
                       headers={"X-Userbot-Token": conf("SECRET_KEY")}, timeout=30)
        body = r.json()
        return body if r.status_code == 200 and body.get("ok") else None
    except Exception as e:  # noqa: BLE001 — the bot is the fallback
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


def options_text(text, options, lang):
    return text + "\n\n" + "\n".join(f"{n}. {o}" for n, o in enumerate(options, 1)) + "\n\n" + PICK_HINT[texts.lang_of(lang)]


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


def on_incoming(tg_id, username, text):
    """A private message to the operator -> 'unknown' | 'no_lead' | 'slot' | 'own_time' | 'logged' (tests read it)."""
    from app.services import leads
    cl = match_client(tg_id, username)
    if not cl or not (text or "").strip():
        return "unknown"
    if not cl["state"].get("operator_dm"):
        set_state(cl["id"], operator_dm=True)
    choice = cl["state"].get("dm_choice") or {}
    if choice.get("slots"):
        n = parse_choice(text, len(choice["slots"]))
        if n:
            _, err = _svc(leads.choose_slot, choice["slots"][n - 1], cl["id"])
            if not err:
                set_state(cl["id"], dm_choice=None)
                return "slot"
        elif TIME_HINT.search(text):
            _, err = _svc(leads.client_own_time, choice["lead"], text)
            if not err:
                set_state(cl["id"], dm_choice=None)
                return "own_time"
    lead = open_lead(cl["id"])
    if not lead:
        return "no_lead"
    _, err = _svc(leads.message_from_client, lead["id"], cl["id"], text, via="dm")
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


async def send(client, client_id, text, options=None, choice=None):
    """Telethon send to a client -> message id. StringSession keeps no access hashes across restarts: a user not in the
    cache is found by @username (or after get_dialogs at start)."""
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
    body = options_text(text, options, cl.get("lang")) if options else text
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
                mid = asyncio.run_coroutine_threadsafe(send(client, int(p["client_id"]), p["text"], p.get("options"), p.get("choice")),
                                                       loop).result(30)
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
    await client.get_dialogs(limit=300)  # fills the entity cache (access hashes) for clients who already wrote

    @client.on(events.NewMessage(incoming=True, func=lambda ev: ev.is_private))
    async def incoming(ev):
        sender = await ev.get_sender()
        if getattr(sender, "bot", False):
            return
        res = await asyncio.to_thread(on_incoming, ev.sender_id, getattr(sender, "username", None), ev.raw_text)
        log.info("DM from %s: %s", ev.sender_id, res)

    @client.on(events.NewMessage(outgoing=True, func=lambda ev: ev.is_private))
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
