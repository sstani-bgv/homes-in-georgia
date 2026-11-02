#!/bin/bash
# All-in-one container entrypoint (prototype style): seed -> db init -> the refresh loop and the bot side by side.
# Postgres schema comes from backend alembic (run it first); the CRM/API (backend) replaces the old admin.py.
# With docker-compose prefer separate services: `python -m bot.run` and `bot/refresh_loop.sh`.
# If any of them dies, the container exits (Docker/Dokploy restarts it). Everything logs to stdout.
# Env: ADMIN_PORT (8080), REFRESH_HOURS (6; 0 = no auto refresh), REFRESH_ON_START (1 = refresh right after start, 0 = never,
#      default: only when the base is empty), SEED (/app/seed/seed.tgz), SEED_URL (download the seed), PY (python).
cd "$(dirname "$0")" || exit 1
PY=${PY:-python}
SEED=${SEED:-seed/seed.tgz}
mkdir -p data raw/img
rmdir data/refresh.lock 2>/dev/null   # a lock left by a killed container would block every future refresh

# Seed: unpack a prebuilt base into empty volumes (so the server does not pay to re-index everything)
# SEED_URL: download the seed first (an archive too big for the deploy upload)
if [ -n "$SEED_URL" ] && [ ! -e data/listings.json ] && [ -z "$(ls -A raw/img)" ]; then
  echo "start: downloading seed"
  "$PY" -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], 'data/seed.tgz')" "$SEED_URL" \
    && SEED=data/seed.tgz || { echo "start: seed download failed"; exit 1; }
fi
if [ -f "$SEED" ] && [ ! -e data/listings.json ] && [ -z "$(ls -A raw/img)" ]; then
  echo "start: unpacking seed $SEED"
  tar -xzf "$SEED" -C . || { echo "start: seed unpack failed"; exit 1; }
  [ "$SEED" = data/seed.tgz ] && rm -f data/seed.tgz
fi

"$PY" db.py init || exit 1

# Codex by subscription: first start prints a device code to the logs; approve it at the URL shown, login is kept in CODEX_HOME
if [ "$LLM_PROVIDER" = codex ]; then
  mkdir -p "${CODEX_HOME:-data/codex}"
  if codex login status >/dev/null 2>&1; then echo "codex: logged in"
  else (codex login --device-auth 2>&1 | sed -u 's/^/codex-login: /') & fi
fi
count() { "$PY" -c "import db; print(sum(db.stats()['listings'].values()))"; }
if [ "$(count)" = 0 ] && [ -f listings.json ]; then
  "$PY" db.py sync --partial || exit 1
fi
N=$(count)
echo "start: listings in base: $N"
[ -n "$SEARCH_MODE" ] && "$PY" db.py set search_mode "$SEARCH_MODE"

pids=()

refresh_loop() {
  # hybrid search needs vectors: build them once for an existing base (refresh.sh keeps them up to date)
  if [ "$N" != 0 ] && [ ! -f data/vectors.npz ] && "$PY" -c "import torch" 2>/dev/null; then "$PY" embed.py; fi
  case "${REFRESH_ON_START:-auto}" in  # 1: always, 0: never, auto: only when the base is empty
    1) ./refresh.sh ;;
    0) ;;
    *) [ "$N" = 0 ] && ./refresh.sh ;;
  esac
  local h=${REFRESH_HOURS:-6}
  [ "$h" = 0 ] && { echo "refresh: REFRESH_HOURS=0, no auto refresh"; exec sleep infinity; }
  while true; do
    sleep "$(awk -v h="$h" 'BEGIN { print int(h * 3600) }')"
    ./refresh.sh
  done
}
refresh_loop & pids+=($!)

"$PY" -u run.py & pids+=($!)

trap 'echo "start: stopping"; kill "${pids[@]}" 2>/dev/null; wait; exit 0' TERM INT
wait -n "${pids[@]}"
code=$?
echo "start: a process exited (code $code), stopping the container"
kill "${pids[@]}" 2>/dev/null
sleep 5   # no tight crash loop when e.g. BOT_TOKEN is wrong
exit $((code == 0 ? 1 : code))
