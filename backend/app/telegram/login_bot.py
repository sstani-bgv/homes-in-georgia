"""CRM sign-in bot (LOGIN_BOT_TOKEN, @LOGIN_BOT_USERNAME): answers /start inv_<token> from a staff invite
(services/invites.py) — the Telegram account that opens the link becomes the new user — and replies with a «Войти в CRM»
button (a Telegram LoginUrl: one tap signs in on PUBLIC_URL, the login page reads the signed fields). Long polling in
a thread of the worker (app/worker.py); it runs only when the login bot is a separate bot from BOT_TOKEN."""
import json
import logging
import threading
import time

import httpx

from app.db import SessionLocal
from app.models import ROLES
from app.services import invites
from app.services.errors import DomainError
from app.telegram.client import conf

log = logging.getLogger(__name__)
ROLE_RU = {"superadmin": "суперадмин", "admin": "администратор", "operator": "оператор", "agent": "агент", "recruit": "рекрут",
           "daily_rent_manager": "менеджер посуточной аренды"}
assert set(ROLE_RU) == set(ROLES)


def token() -> str:
    t = conf("LOGIN_BOT_TOKEN").strip()
    return t if t and t != conf("BOT_TOKEN").strip() else ""


def call(method: str, **params):
    api = conf("TG_API_BASE", "https://api.telegram.org").rstrip("/")
    data = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in params.items() if v is not None}
    try:
        r = httpx.post(f"{api}/bot{token()}/{method}", data=data, timeout=70)
        body = r.json()
    except Exception:  # noqa: BLE001
        log.warning("login bot %s failed", method)
        return None
    if not body.get("ok"):
        log.warning("login bot %s: %s", method, body.get("description"))
        return None
    return body["result"]


def login_button():
    url = conf("PUBLIC_URL", "").rstrip("/") + "/login"
    return {"inline_keyboard": [[{"text": "Войти в CRM", "login_url": {"url": url, "request_write_access": True}}]]}


def reply(chat_id, text):
    if call("sendMessage", chat_id=chat_id, text=text, reply_markup=login_button()) is None:
        # LoginUrl needs the bot's domain (BotFather /setdomain) = PUBLIC_URL's host; else a plain link still works
        url = conf("PUBLIC_URL", "").rstrip("/") + "/login"
        call("sendMessage", chat_id=chat_id, text=text,
             reply_markup={"inline_keyboard": [[{"text": "Войти в CRM", "url": url}]]})


def handle(update: dict) -> str | None:
    """One update → the text sent back (None = ignored). Only private /start messages count."""
    msg = update.get("message") or {}
    text, frm, chat = (msg.get("text") or "").strip(), msg.get("from") or {}, msg.get("chat") or {}
    if chat.get("type") != "private" or not frm.get("id") or frm.get("is_bot") or not text.startswith("/start"):
        return None
    arg = text.split(maxsplit=1)[1] if " " in text else ""
    if not arg.startswith("inv_"):
        out = "Это бот входа в CRM Homes in Georgia. Нажмите «Войти в CRM»."
    else:
        try:
            with SessionLocal() as db:
                u = invites.accept(db, arg[4:], frm)
                out = (f"Готово, {u.name}! Ваш Telegram привязан к CRM Homes in Georgia, роль: {ROLE_RU[u.role]}.\n"
                       "Нажмите «Войти в CRM» и заполните профиль: имя и фамилия, дата рождения, телефон, email.")
        except DomainError as e:
            out = e.message
    reply(chat["id"], out)
    return out


def poll_forever():
    offset = None
    while True:
        updates = call("getUpdates", offset=offset, timeout=50, allowed_updates=["message"])
        if updates is None:
            time.sleep(10)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                handle(upd)
            except Exception:  # noqa: BLE001
                log.exception("login bot update failed")


def start_thread() -> bool:
    if not token():
        log.info("login bot: LOGIN_BOT_TOKEN is not set (or equals BOT_TOKEN), invite deep links are off")
        return False
    threading.Thread(target=poll_forever, name="login-bot", daemon=True).start()
    log.info("login bot polling started")
    return True
