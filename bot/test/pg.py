"""Test database for the bot: TEST_DATABASE_URL in its own schema (BOT_TEST_SCHEMA, default bot_test), so the backend's
tests on the same database don't collide. The schema comes from backend/alembic (upgrade head); while that is missing,
from test/schema_fallback.sql. A changed migration set rebuilds the schema. reset() empties every table."""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import psycopg

import db

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = ROOT / "backend" / "alembic"
FALLBACK = Path(__file__).with_name("schema_fallback.sql")
SCHEMA = os.environ.get("BOT_TEST_SCHEMA", "bot_test")
_ready = []


def _setup_env():
    """Before anything imports app.config: the backend engine and notify use the test schema; no real Telegram."""
    os.environ["DATABASE_URL"] = url(driver=True)
    os.environ.setdefault("BOT_TOKEN", "")
    db.URL = url()


def base_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url and (ROOT / ".env").exists():
        m = re.search(r"^TEST_DATABASE_URL=(.+)$", (ROOT / ".env").read_text(), re.M)
        url = m and m[1].strip()
    return url or "postgresql://postgres@/homes_test?host=/Users/mihailfedrunov/.hig-pg/data"


def url(driver=False):
    """TEST_DATABASE_URL with search_path = the test schema (driver=True keeps/sets SQLAlchemy's +psycopg)."""
    u = base_url()
    u = u + ("&" if "?" in u else "?") + "options=" + quote(f"-csearch_path={SCHEMA}")
    u = re.sub(r"^postgres(?:ql)?(\+\w+)?://", "postgresql+psycopg://" if driver else "postgresql://", u)
    return u


def fingerprint():
    if (ALEMBIC / "versions").is_dir():
        files = sorted(p for p in (ALEMBIC / "versions").glob("*.py"))
        return "alembic:" + hashlib.md5(b"".join(p.read_bytes() for p in files)).hexdigest()
    return "fallback:" + hashlib.md5(FALLBACK.read_bytes()).hexdigest()


def ensure():
    if _ready:
        return
    fp = fingerprint()
    with psycopg.connect(db.dsn(base_url()), autocommit=True) as c:
        cur = c.execute("SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = %s", (SCHEMA,)).fetchone()
        if cur and cur[0] == fp:
            _ready.append(fp)
            return
        c.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        c.execute(f'CREATE SCHEMA "{SCHEMA}"')
    if fp.startswith("alembic:"):
        env = {**os.environ, "DATABASE_URL": url(driver=True), "TEST_DATABASE_URL": url(driver=True)}
        r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", env=env,
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("alembic upgrade head failed:\n" + r.stdout + r.stderr)
    else:
        with psycopg.connect(url()) as c:
            c.execute(FALLBACK.read_text())
    with psycopg.connect(db.dsn(base_url()), autocommit=True) as c:
        c.execute(f"COMMENT ON SCHEMA \"{SCHEMA}\" IS '{fp}'")
    _ready.append(fp)


def reset():
    """Empty test schema, db.URL pointed at it, bot seeds applied (db.init)."""
    ensure()
    db.URL = url()
    with psycopg.connect(url(), autocommit=True) as c:
        tables = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s AND tablename != 'alembic_version'",
                                          (SCHEMA,))]
        if tables:
            c.execute("TRUNCATE " + ", ".join(f'"{SCHEMA}"."{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
    db.init()


_setup_env()
