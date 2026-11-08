"""PostgreSQL storage (shared with the backend/CRM). The schema lives in backend/alembic; this module keeps the
prototype's sqlite3-style API on top of psycopg 3: `?` / `:name` placeholders, rows by key and by index,
`INSERT OR IGNORE`. Tables: docs/ARCHITECTURE.md §4 (bot tables + `users` instead of the old `agents`).

CLI:  python db.py init                 # seed default settings and sources (sources.json) if empty
      python db.py sync [--partial]     # import listings.json + raw/dropped_agents.json
      python db.py export-sources       # DB -> sources.json (the fetchers still read that file)
      python db.py set <key> <value>    # settings, e.g. `set moderate_all 1`
      python db.py stats
Env: DATABASE_URL (postgresql+psycopg://... as in the repo .env; the +psycopg suffix is stripped).
"""
import json
import re
import os
from functools import lru_cache
from pathlib import Path
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg

ROOT = Path(__file__).resolve().parent.parent  # the repo: .env lives there
for _p in (str(ROOT / "backend"), str(ROOT)):  # the bot uses the backend's services and notify (app.*)
    if _p not in sys.path:
        sys.path.append(_p)
DATA_DIR = os.environ.get("DATA_DIR", "data")  # local files of the bot (vectors.npz, client photos), relative to bot/
URL = None  # tests point this at TEST_DATABASE_URL
STAFF_ROLES = ("operator", "agent", "admin", "superadmin")
STAFF_SQL = "role IN ('operator', 'agent', 'admin', 'superadmin') AND is_active"

DEFAULT_SETTINGS = {
    "moderate_all": "0",        # 1: every competitor tg:/fb: listing waits for an agent's approval
    "lead_target": "agents",    # agents: each active agent in private chat | group: group_chat_id
    "group_chat_id": "",
    "review_batch": "5",        # pending cards sent per bot tick (every 10 min)
    "search_mode": "llm",       # llm: whole catalog to Codex | hybrid: filters + local embeddings, 20 lines to Codex
    "final_llm": "on",          # hybrid only. off: top 5 by score with templated reasons (one short Codex call)
    "auto_push_variants": "0",  # 1: a client's message starts a search at once; 0: it refines the request, variants by «Ещё»
}

JSON_COLS = ("urls", "dup_sources", "photos", "photo_hashes", "features", "agent_reasons", "details")


def load_env(path=None):
    """KEY=VALUE lines of .env into os.environ (real environment variables win). ENV_FILE overrides the path."""
    path = path or os.environ.get("ENV_FILE") or str(ROOT / ".env")
    if os.path.exists(path):
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dsn(url=None):
    """libpq URL from DATABASE_URL (SQLAlchemy's postgresql+psycopg:// form accepted)."""
    url = url or URL or os.environ.get("DATABASE_URL")
    if not url:
        load_env()
        url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (repo .env or environment)")
    return re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", url)


# ---------- sqlite3 compatibility ----------

class Row(tuple):
    """Like sqlite3.Row: r[0], r["col"], dict(r), tuple(r), r.keys()."""
    def __getitem__(self, k):
        return tuple.__getitem__(self, self._idx[k] if isinstance(k, str) else k)

    def keys(self):
        return list(self._idx)

    def get(self, k, default=None):
        return self[k] if k in self._idx else default


def _rows(cur):
    idx = {d.name: n for n, d in enumerate(cur.description or [])}

    def make(values):
        r = Row(values)
        r._idx = idx
        return r
    return make


_IDENT = re.compile(r"[A-Za-z_]\w*")


@lru_cache(maxsize=1024)
def pg(sql):
    """SQLite SQL -> psycopg: ? -> %s, :name -> %(name)s, % -> %%, INSERT OR IGNORE -> ON CONFLICT DO NOTHING."""
    if re.match(r"\s*INSERT\s+OR\s+REPLACE", sql, re.I):
        raise ValueError("INSERT OR REPLACE: write an explicit ON CONFLICT ... DO UPDATE")
    ignore = re.match(r"\s*INSERT\s+OR\s+IGNORE\s+", sql, re.I)
    if ignore:
        sql = "INSERT " + sql[ignore.end():]
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":  # string literal: copy as is ('' escapes stay inside)
            j = i + 1
            while j < n and not (sql[j] == "'" and (j + 1 >= n or sql[j + 1] != "'")):
                j += 2 if sql[j] == "'" else 1
            out.append(sql[i:j + 1].replace("%", "%%"))
            i = j + 1
        elif ch == "?":
            out.append("%s")
            i += 1
        elif ch == "%":
            out.append("%%")
            i += 1
        elif ch == ":" and i + 1 < n and sql[i + 1] == ":":  # ::cast
            out.append("::")
            i += 2
        elif ch == ":" and (m := _IDENT.match(sql, i + 1)) and (i == 0 or sql[i - 1] != ":"):
            out.append(f"%({m[0]})s")
            i = m.end()
        else:
            out.append(ch)
            i += 1
    sql = "".join(out)
    if ignore:
        m = re.search(r"\s+RETURNING\s", sql, re.I)
        sql = sql[:m.start()] + " ON CONFLICT DO NOTHING" + sql[m.start():] if m else sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


def _val(v):
    """Numbers go as untyped text literals: Postgres casts them to the column type (sqlite-like leniency, e.g. an int
    compared with a TEXT column); bools, None, lists and dicts as is."""
    return str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def params(p):
    if p is None:
        return ()
    if isinstance(p, dict):
        return {k: _val(v) for k, v in p.items()}
    return tuple(_val(v) for v in p)


class Conn:
    """A psycopg connection with sqlite3's execute()/executemany(); `with c:` commits (or rolls back) like sqlite3."""
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, p=None):
        cur = self.raw.cursor(row_factory=_rows)
        cur.execute(pg(sql), params(p))
        return cur

    def executemany(self, sql, seq):
        cur = self.raw.cursor(row_factory=_rows)
        seq = [params(p) for p in seq]
        if seq:
            cur.executemany(pg(sql), seq)
        return cur

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, et, *a):
        self.rollback() if et else self.commit()


def connect(url=None):
    return Conn(psycopg.connect(dsn(url)))


@contextmanager
def tx(url=None):
    """One connection per unit of work (commit at the end, rollback on error): safe from threads and other processes."""
    c = connect(url)
    try:
        with c:
            yield c
    finally:
        c.close()


def init(url=None):
    """Seed what the bot needs in an existing schema (alembic creates the tables): default settings and sources from
    sources.json when that table is empty. Staff live in `users` (CRM, /agent <code>); agents.json is not imported."""
    with tx(url) as c:
        c.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", DEFAULT_SETTINGS.items())
        if not c.execute("SELECT 1 FROM sources").fetchone() and os.path.exists("sources.json"):
            import_sources(c, json.loads(Path("sources.json").read_text()))


def get_setting(key, c=None):
    if c is None:
        with tx() as c:
            return get_setting(key, c)
    r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r[0] if r else DEFAULT_SETTINGS.get(key)


def set_setting(key, value, c=None):
    if c is None:
        with tx() as c:
            return set_setting(key, value, c)
    c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))


def row(r):
    """Row -> dict with JSON columns decoded."""
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS:
        if k in d and isinstance(d[k], str):
            d[k] = json.loads(d[k])
    for k in ("state", "profile"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k] or "{}")
    return d


# ---------- sources <-> sources.json ----------

# Threads has no groups to list: the fetcher searches posts by these keywords (fetch_threads.py)
THREADS_DEFAULTS = {"queries": ["сдаю квартиру Тбилиси", "аренда квартиры Тбилиси", "квартира в аренду Тбилиси",
                                "apartment for rent Tbilisi", "flat for rent Tbilisi", "ქირავდება ბინა თბილისში"],
                    "per_query": 50, "days_back": 7, "photos": 5}


def import_sources(c, cfg):
    set_setting("telegram_defaults", json.dumps(cfg.get("telegram_defaults", {})), c)
    if cfg.get("threads_defaults"):
        set_setting("threads_defaults", json.dumps(cfg["threads_defaults"], ensure_ascii=False), c)
    set_setting("facebook_defaults", json.dumps(cfg.get("facebook_defaults", {})), c)
    set_setting("instagram_defaults", json.dumps(cfg.get("instagram_defaults", {})), c)
    n = 0
    for ch in cfg.get("telegram_channels", []):
        opts = {k: v for k, v in ch.items() if k not in ("username", "kind")}
        c.execute("INSERT OR IGNORE INTO sources (kind, key, url, options, position) VALUES (?, ?, ?, ?, ?)",
                  ("tg_" + ch["kind"], ch["username"], f"https://t.me/{ch['username']}", json.dumps(opts, ensure_ascii=False), n))
        n += 1
    for g in cfg.get("facebook_groups", []):
        opts = {k: v for k, v in g.items() if k not in ("key", "url", "lang", "name")}
        c.execute("INSERT OR IGNORE INTO sources (kind, key, name, url, lang, options, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  ("fb_group", g["key"], g.get("name"), g.get("url"), g.get("lang"), json.dumps(opts, ensure_ascii=False), n))
        n += 1
    for a in cfg.get("instagram_accounts", []):
        c.execute("INSERT OR IGNORE INTO sources (kind, key, name, url, lang, options, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  ("ig_account", a["key"], a.get("name"), f"https://www.instagram.com/{a['key']}/", a.get("lang"), "{}", n))
        n += 1


def sources_config(c):
    """Enabled sources in the sources.json format the fetchers read."""
    rows = c.execute("SELECT * FROM sources WHERE enabled = 1 ORDER BY position, id").fetchall()
    tg = [{"username": r["key"], "kind": r["kind"][3:], **json.loads(r["options"])} for r in rows if r["kind"].startswith("tg_")]
    fb = [{"key": r["key"], **json.loads(r["options"]), "url": r["url"], "lang": r["lang"], "name": r["name"]}
          for r in rows if r["kind"] == "fb_group"]
    ig = [{"key": r["key"], "name": r["name"]} for r in rows if r["kind"] == "ig_account"]
    return {"telegram_defaults": json.loads(get_setting("telegram_defaults", c) or "{}"), "telegram_channels": tg,
            "facebook_defaults": json.loads(get_setting("facebook_defaults", c) or "{}"), "facebook_groups": fb,
            "instagram_defaults": json.loads(get_setting("instagram_defaults", c) or "{}"), "instagram_accounts": ig,
            "threads_defaults": json.loads(get_setting("threads_defaults", c) or "null") or THREADS_DEFAULTS}


def export_sources(path="sources.json"):
    with tx() as c:
        cfg = sources_config(c)
    j = lambda v: json.dumps(v, ensure_ascii=False)  # one source per line, like the hand-written file
    lines = ["{", f'  "telegram_defaults": {j(cfg["telegram_defaults"])},', '  "telegram_channels": [',
             ",\n".join(f"    {j(x)}" for x in cfg["telegram_channels"]), "  ],",
             f'  "facebook_defaults": {j(cfg["facebook_defaults"])},', '  "facebook_groups": [',
             ",\n".join(f"    {j(x)}" for x in cfg["facebook_groups"]), "  ],",
             f'  "instagram_defaults": {j(cfg["instagram_defaults"])},', '  "instagram_accounts": [',
             ",\n".join(f"    {j(x)}" for x in cfg["instagram_accounts"]), "  ],",
             f'  "threads_defaults": {j(cfg["threads_defaults"])}', "}"]
    Path(path).write_text("\n".join(lines) + "\n")
    return cfg


# ---------- listings sync ----------

# signals that prove an agent; the rest (same text, photo reposts, AI suspicion) are doubtful -> human review
CONCLUSIVE = ("один телефон", "один аккаунт", "FB: имя", "FB: один автор")
# explicit self-labels only; "без агентов" / "no agents" style phrases must not match
AGENT_TEXT = re.compile(r"აგენტის განცხადება|სააგენტოს განცხადება|объявление агента|агентское объявление|"
                        r"наше агенство|наше агентство|мы агентство|я агент\b|"
                        r"agent'?s (?:listing|announcement)|listing by (?:an )?agency|our agency|"
                        r"we are an agency|i am an agent\b|i'?m an agent\b", re.I)
FIELDS = ("source", "owner_id", "price_usd", "area", "floor", "bedrooms", "address", "district", "geo_precision",
          "description_ru", "pets", "renovation", "style", "light", "furniture", "impression", "looks", "text")


def classify(it, moderate_all):
    """Listing from ingest -> (auto status, reason)."""
    src = it["source"]
    if src == "agency":
        return "published", None
    reasons = it.get("agent_reasons") or []
    hard = [r for r in reasons if r.startswith(CONCLUSIVE)]
    if src != "agency" and not src.startswith(("tg:", "ig:")) and AGENT_TEXT.search(it.get("text") or ""):
        hard.append("в тексте прямо написано: объявление агента")
    if hard:
        return "rejected", "; ".join(hard)
    competitor = src.startswith(("tg:", "ig:"))  # competitor agencies: AI "agent" flag is expected there
    if reasons or (it.get("agent_suspect") and not competitor):
        return "pending_review", "; ".join(reasons) or "ИИ: " + (it.get("agent_reason") or "похоже на агента")
    if moderate_all and (competitor or src.startswith("fb:")):
        return "pending_review", "проверка всех объявлений конкурентов и FB включена"
    return "published", None


PHONE = re.compile(r"(?:\+?995[\s\-:.]?)?(5(?:[\s\-:.]?\d){8})")  # = ingest.PHONE


def phones(text):
    return {re.sub(r"\D", "", m)[-9:] for m in PHONE.findall(text or "")}


def agent_phones(c):
    """Phones the agents marked as "answered an agent" (journey C2b: owners.is_agent), as 9 digits."""
    return {re.sub(r"\D", "", r[0])[-9:] for r in c.execute("SELECT phone_norm FROM owners WHERE is_agent AND phone_norm IS NOT NULL")}


def mark_known_agent(it, known):
    """A listing whose text has a known agent phone -> agent-suspect (waits for review, flagged in the new-listings feed)."""
    hit = sorted(phones(it.get("text")) & known) if known and it["source"] != "agency" and not it["source"].startswith(("tg:", "ig:")) else []
    if not hit:
        return it
    return {**it, "agent_suspect": True, "agent_reasons": [*(it.get("agent_reasons") or []), f"телефон в базе агентов: {', '.join(hit)}"]}


def sync(listings_path="listings.json", dropped_path="raw/dropped_agents.json", full=True, path=None):
    """Import a fresh ingest. full=True: listings absent from it are marked gone (guarded against a broken ingest)."""
    items = json.loads(Path(listings_path).read_text())
    if os.path.exists(dropped_path):
        items += json.loads(Path(dropped_path).read_text())
    t = now()
    counts, new = {}, []
    with tx(path) as c:
        moderate_all = get_setting("moderate_all", c) == "1"
        known = agent_phones(c)
        ids = set()
        for it in items:
            ids.add(it["id"])
            it = mark_known_agent(it, known)
            status, reason = classify(it, moderate_all)
            old = c.execute("SELECT decision, status_reason FROM listings WHERE id = ?", (it["id"],)).fetchone()
            if old and old["decision"]:  # a human already decided: keep it (and its reason, e.g. "неактуально")
                status, reason = ("published", None) if old["decision"] == "approved" else ("rejected", old["status_reason"] or "отклонено агентом")
            latlng = it.get("latlng") or (None, None)
            vals = {k: it.get(k) for k in FIELDS}
            vals.update({k: json.dumps(it.get(k) or ({} if k == "details" else []), ensure_ascii=False) for k in JSON_COLS},
                        status=status, status_reason=reason, lat=latlng[0], lng=latlng[1],
                        agent_suspect=int(bool(it.get("agent_suspect"))), last_seen=t, updated_at=t)
            if old:
                c.execute(f"UPDATE listings SET {', '.join(f'{k} = :{k}' for k in vals)} WHERE id = :id", {**vals, "id": it["id"]})
            else:
                vals.update(id=it["id"], first_seen=t)
                c.execute(f"INSERT INTO listings ({', '.join(vals)}) VALUES ({', '.join(':' + k for k in vals)})", vals)
                new.append(it["id"])
            counts[status] = counts.get(status, 0) + 1
        if full:
            live = c.execute("SELECT id FROM listings WHERE status != 'gone'").fetchall()
            missing = [r["id"] for r in live if r["id"] not in ids]
            if len(ids) < 0.5 * len(live):  # half the base vanished: the ingest is broken, not the market
                print(f"sync: fresh ingest has {len(ids)} vs {len(live)} live, not marking gone (use a good ingest)")
            else:
                c.executemany("UPDATE listings SET status = 'gone', updated_at = ? WHERE id = ?", [(t, i) for i in missing])
                counts["gone"] = len(missing)
    match_new(new)
    return counts


def match_new(ids):
    """New listings → the CRM matcher (backend app.services.matching): the same flat is linked to its object, a similar
    one waits for an operator in «Новые объявления». Never fails the sync."""
    if not ids:
        return {}
    try:
        from app.db import SessionLocal
        from app.services import matching
        with SessionLocal() as s:
            res = matching.process(s, ids)
        if res.get("same") or res.get("similar"):
            print("matched new listings:", res)
        return res
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("matching %d new listings failed", len(ids))
        return {"error": len(ids)}


def stats():
    with tx() as c:
        return {
            "listings": dict(c.execute("SELECT status, COUNT(*) FROM listings GROUP BY status").fetchall()),
            "leads": dict(c.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall()),
            "clients": c.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
            "staff": c.execute(f"SELECT COUNT(*) FROM users WHERE {STAFF_SQL}").fetchone()[0],
            "sources": c.execute("SELECT COUNT(*) FROM sources WHERE enabled = 1").fetchone()[0],
            "settings": dict(c.execute("SELECT key, value FROM settings WHERE key NOT LIKE '%_defaults'").fetchall()),
        }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    init()
    if cmd == "sync":
        print("sync:", sync(full="--partial" not in sys.argv))
        import sheets
        sheets.safe_sync()  # mirror to Google Sheets; a no-op until configured, never fails the sync
    elif cmd == "export-sources":
        cfg = export_sources()
        print(f"sources.json: {len(cfg['telegram_channels'])} channels, {len(cfg['facebook_groups'])} groups")
    elif cmd == "set":
        set_setting(sys.argv[2], sys.argv[3])
    if cmd != "init":
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
