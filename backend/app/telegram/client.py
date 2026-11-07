"""Sync Bot API client (httpx). BOT_TOKEN and TG_API_BASE come from the environment at call time (tests point
TG_API_BASE at a fake Bot API); the same bot for clients and staff. Never raises: returns the Bot API `result`, or
None on any failure (logged)."""
import html as _html
import json
import logging
import os
import re

import httpx

log = logging.getLogger(__name__)


def conf(key, default=""):
    """Config value: the process environment wins, then app.config.settings (repo .env)."""
    v = os.environ.get(key)
    if v is None:
        try:
            from app.config import settings
            v = getattr(settings, key, None)
        except Exception:
            v = None
    return default if v in (None, "") else str(v)
JSON_PARAMS = {"reply_markup", "media", "entities", "caption_entities", "link_preview_options", "allowed_updates"}
_http = None


def _client():
    global _http
    if _http is None:
        _http = httpx.Client(timeout=httpx.Timeout(30, connect=10))
    return _http


def base():
    token = conf("BOT_TOKEN").strip()
    api = conf("TG_API_BASE", "https://api.telegram.org").rstrip("/")
    return token and f"{api}/bot{token}"


def call(method, files=None, **params):
    """POST <api>/bot<token>/<method>. params: None values dropped, dict/list ones JSON-encoded (form fields, which the
    Bot API accepts for every method); files: {field: path or (name, bytes)} -> multipart."""
    url = base()
    if not url:
        log.warning("telegram %s skipped: BOT_TOKEN is not set", method)
        return None
    data = {k: json.dumps(v, ensure_ascii=False) if k in JSON_PARAMS or isinstance(v, (dict, list)) else
            ("true" if v is True else "false" if v is False else str(v)) for k, v in params.items() if v is not None}
    opened = []
    try:
        up = None
        if files:
            up = {}
            for k, f in files.items():
                if isinstance(f, (str, os.PathLike)):
                    fh = open(f, "rb")
                    opened.append(fh)
                    up[k] = (os.path.basename(str(f)), fh)
                else:
                    up[k] = f
        r = _client().post(f"{url}/{method}", data=data, files=up)
        body = r.json()
        if not body.get("ok"):
            log.warning("telegram %s failed: %s", method, body.get("description"))
            return None
        return body.get("result")
    except Exception as e:  # network, bad JSON, missing file: never propagate into business logic
        log.warning("telegram %s error: %r", method, e)
        return None
    finally:
        for fh in opened:
            fh.close()


_TOKEN = re.compile(r"<[^>]*>|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);|[\s\S]", re.S)


def clip(text, limit, html=True):
    """Fit a message into Telegram's limit (counted on the visible text). Plain text is cut; HTML is cut between
    tags/entities (never inside one) and the tags left open are closed, so the markup stays valid."""
    text = text or ""
    if not html:
        return text if len(text) <= limit else text[:limit - 1] + "…"
    toks = _TOKEN.findall(text)
    visible = sum(1 for t in toks if not t.startswith("<"))
    if visible <= limit:
        return text
    out, n, open_ = [], 0, []
    for t in toks:
        if t.startswith("<"):
            name = re.match(r"</?\s*([a-zA-Z0-9-]+)", t)
            if name and not t.endswith("/>"):
                if t.startswith("</"):
                    if name[1].lower() in open_:
                        del open_[len(open_) - 1 - open_[::-1].index(name[1].lower())]
                else:
                    open_.append(name[1].lower())
            out.append(t)
            continue
        if n >= limit - 1:
            break
        out.append(t)
        n += 1
    while out and out[-1].startswith("<") and not out[-1].startswith("</"):  # don't leave an empty opened tag at the end
        name = re.match(r"<\s*([a-zA-Z0-9-]+)", out.pop())
        if name and name[1].lower() in open_:
            del open_[len(open_) - 1 - open_[::-1].index(name[1].lower())]
    return "".join(out) + "…" + "".join(f"</{t}>" for t in reversed(open_))


def plain_to_html(text, limit):
    """Plain text -> escaped HTML that fits `limit` (cut before escaping, so no entity is ever split)."""
    return _html.escape(clip(text, limit, html=False))


def kb(rows):
    """[[button, ...], ...] -> inline_keyboard markup; falsy buttons and empty rows dropped."""
    rows = [[b for b in row if b] for row in rows or []]
    return {"inline_keyboard": [r for r in rows if r]}


def cb(text, data):
    return {"text": text, "callback_data": data}


def url_btn(text, url):
    return url and {"text": text, "url": url}


def app_btn(text, url, private=True):
    """Mini App button: web_app in private chats (https only), plain url button in groups."""
    if not url:
        return None
    if private and url.startswith("https://"):
        return {"text": text, "web_app": {"url": url}}
    return {"text": text, "url": url}


def send(chat_id, text, keyboard=None, html=True, thread_id=None, **kw):
    return call("sendMessage", chat_id=chat_id, text=clip(text, 4096, html), parse_mode="HTML" if html else None,
                reply_markup=keyboard, message_thread_id=thread_id, link_preview_options={"is_disabled": True}, **kw)


def send_photo(chat_id, photo, caption, keyboard=None, html=True, thread_id=None):
    """photo: local path (uploaded), file_id or URL."""
    p = dict(chat_id=chat_id, caption=clip(caption, 1024, html), parse_mode="HTML" if html else None, reply_markup=keyboard,
             message_thread_id=thread_id)
    if isinstance(photo, str) and os.path.exists(photo):
        return call("sendPhoto", files={"photo": photo}, **p)
    return call("sendPhoto", photo=photo, **p)


def edit(chat_id, message_id, text, keyboard=None, html=True):
    return call("editMessageText", chat_id=chat_id, message_id=message_id, text=clip(text, 4096, html),
                parse_mode="HTML" if html else None, reply_markup=keyboard, link_preview_options={"is_disabled": True})


def edit_caption(chat_id, message_id, caption, keyboard=None):
    return call("editMessageCaption", chat_id=chat_id, message_id=message_id, caption=clip(caption, 1024), parse_mode="HTML",
                reply_markup=keyboard)


def edit_markup(chat_id, message_id, keyboard):
    return call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=keyboard or {"inline_keyboard": []})
