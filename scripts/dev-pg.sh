#!/bin/bash
# Local dev PostgreSQL without Docker (pgserver wheel). Data: ~/.hig-pg/data, unix socket only.
# DATABASE_URL=postgresql+psycopg://postgres@/homes?host=$HOME/.hig-pg/data
set -e
PG="$HOME/.hig-pg"
[ -x "$PG/venv/bin/python" ] || { uv venv -q -p 3.12 "$PG/venv" && uv pip install -q -p "$PG/venv/bin/python" pgserver 'psycopg[binary]'; }
"$PG/venv/bin/python" - <<PY
import pgserver, psycopg
srv = pgserver.get_server("$PG/data", cleanup_mode=None)
c = psycopg.connect(f"postgresql://postgres@/postgres?host=$PG/data", autocommit=True)
have = {r[0] for r in c.execute("select datname from pg_database")}
for d in ("homes", "homes_test"):
    if d not in have: c.execute(f"create database {d}")
print("postgres running:", srv.get_uri())
PY
