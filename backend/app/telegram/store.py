"""Tiny psycopg access for notify: reads the rows a message needs (client chat/lang, listing, staff) and keeps
message refs (`messages` table) so cards can be edited later. Own short connections: works with ORM objects or dicts,
inside or outside a service's transaction. Never raises."""
import json
import logging
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]


def dsn():
    from app.telegram.client import conf
    url = conf("DATABASE_URL")
    return url and re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", url)


def q(sql, params=(), one=False):
    try:
        with psycopg.connect(dsn(), row_factory=dict_row) as c:
            cur = c.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        return (rows[0] if rows else None) if one else rows
    except Exception as e:
        log.warning("telegram store: %r", e)
        return None if one else []


def g(o, k, default=None):
    """Attribute of an ORM object or key of a dict."""
    if o is None:
        return default
    if isinstance(o, dict):
        return o.get(k, default)
    return getattr(o, k, default)


def jload(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v or "null") or default
        except ValueError:
            return default
    return v if v is not None else default


def client(cid):
    r = q("SELECT * FROM clients WHERE id = %s", (cid,), one=True) if cid else None
    if r:
        r["state"], r["profile"] = jload(r.get("state"), {}), jload(r.get("profile"), {})
    return r


def listing(lid):
    r = q("SELECT * FROM listings WHERE id = %s", (lid,), one=True) if lid else None
    if r:
        for k in ("urls", "photos", "details"):
            r[k] = jload(r.get(k), {} if k == "details" else [])
    return r


def user(uid):
    return q("SELECT * FROM users WHERE id = %s", (uid,), one=True) if uid else None


def lead(lead_id):
    return q("SELECT * FROM leads WHERE id = %s", (lead_id,), one=True) if lead_id else None


def remember(kind, ref, chat_id, message_id):
    if chat_id is None or message_id is None:
        return
    q("INSERT INTO messages (kind, ref, chat_id, message_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
      (kind, str(ref), chat_id, message_id))


def refs(kind, ref):
    """(chat_id, message_id) oldest first: message ids grow within a chat, so refs[-1] is the latest message."""
    return [(r["chat_id"], r["message_id"]) for r in q("SELECT chat_id, message_id FROM messages WHERE kind = %s AND ref = %s "
                                                        "ORDER BY message_id, chat_id", (kind, str(ref)))]


def staff_tg_ids(roles):
    return [r["tg_id"] for r in q("SELECT tg_id FROM users WHERE is_active AND tg_id IS NOT NULL AND role = ANY(%s) ORDER BY id",
                                  (list(roles),))]


def setting(key):
    r = q("SELECT value FROM settings WHERE key = %s", (key,), one=True)
    return r and r["value"]
