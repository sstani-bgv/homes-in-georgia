#!/bin/bash
# Auto-update of the listings base: sources (DB) -> fetchers -> ingest -> sync into Postgres (DATABASE_URL, repo .env)
# -> embed (vectors). The synced `listings` table is the funnel 1 new-listings feed (GET /api/listings/new); phones that
# agents marked as "agent" (owners.is_agent) make a listing agent-suspect on sync.
# Run by hand or on a schedule (not installed, see docs/README.md "Автообновление"), e.g. cron every 6 hours:
#   0 */6 * * * /path/to/rent-tbilisi-ai/refresh.sh >> /path/to/rent-tbilisi-ai/data/refresh.log 2>&1
# Env: PY (python, default .venv/bin/python), TG (telegram-user-session runtime), SKIP_FACEBOOK=1 (Apify costs money), SKIP_THREADS=1,
#      SKIP_LIVO=1, SKIP_KORTER=1,
#      TG_FETCH=session (default: Telethon via TG) | web (public channels via t.me/s, fetch_channel_web.py; the server).
# A failed fetcher keeps its previous raw/*.json; a failed ingest skips the sync (the base stays as it was).
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # cron/launchd PATH has no codex
if [ -z "$PY" ]; then [ -x ../.venv/bin/python ] && PY=../.venv/bin/python || PY=python; fi
TG=${TG:-$HOME/.claude/skills/telegram-user-session/runtime/tg/tg}
mkdir -p data
mkdir data/refresh.lock 2>/dev/null || { echo "$(date) refresh already running"; exit 0; }
trap 'rmdir data/refresh.lock' EXIT
echo "=== $(date) refresh start"

"$PY" db.py export-sources || exit 1          # admin edits sources in the DB, fetchers read sources.json
# Each fetcher runs at most every EVERY_<name> minutes (Управление → Источники → Расписание; db.py writes data/schedule.sh),
# refresh_loop.sh ticks every REFRESH_TICK_MIN. Paid ones (Facebook, Threads via Apify) cost per run, the page shows $/month.
. data/schedule.sh 2>/dev/null
ran=0
due() { [ ! -f "$1" ] || [ -z "$(find "$1" -mmin -"$2")" ]; }  # stamp older than N minutes (or none yet)
run() {  # run <name> <command...>; the stamp is set before the run, so a failing paid fetcher is not retried every tick
  local n=$1 every; every=$(eval echo "\${EVERY_$n:-360}"); shift
  if due "data/last_$n" $((every - 5)); then touch "data/last_$n"; ran=1; "$@" || echo "$n failed, keeping its old raw file"
  else echo "$n: ran < $every min ago, skipping"; fi
}
run ss "$PY" fetch_ss.py
run myhome "$PY" fetch_myhome.py
# livo.ge = the myhome base without Cloudflare (dedupe merges the same flat), korter.ge = owner listings with a pin
[ "${SKIP_LIVO:-0}" = 1 ]   || run livo "$PY" fetch_livo.py
[ "${SKIP_KORTER:-0}" = 1 ] || run korter "$PY" fetch_korter.py
if [ "${TG_FETCH:-session}" = web ]; then     # server: public channels via t.me/s, no user session
  run telegram "$PY" fetch_channel_web.py
elif [ -x "$TG" ]; then
  run telegram "$TG" --account main "$PWD/fetch_channel.py"
else
  echo "no tg runtime at $TG, skipping Telegram channels"
fi
# Facebook: Apify, incremental (only posts since the last run are paid: $0.0007 each) + $0.002 per run
[ "${SKIP_FACEBOOK:-0}" = 1 ] || run facebook "$PY" fetch_facebook.py
# Instagram is off (2026-11-08: agencies only, no owners there); Threads replaces it: Apify dacoder, $0.03 per query per
# run, monitorMode returns only posts not seen before
[ "${SKIP_THREADS:-0}" = 1 ] || run threads "$PY" fetch_threads.py
[ "$ran" = 1 ] || { echo "=== $(date) nothing due"; exit 0; }
"$PY" ingest.py || { echo "ingest failed"; exit 1; }
# video links (ss.ge HLS, YouTube...) -> mp4 in the album with yt-dlp, only listings without a local video; failures are
# cached for VIDEO_RETRY_DAYS and keep the "🎥 Видео" button (YouTube may block server IPs)
"$PY" fetch_videos.py || echo "fetch_videos failed, listings keep their video links"
"$PY" db.py sync || { echo "sync failed"; exit 1; }
# local photo/text vectors for search_mode=hybrid (only new listings, ~0.1 s per photo); a failure keeps the old ones
"$PY" embed.py || echo "embed failed, keeping old data/vectors.npz"
echo "=== $(date) refresh done"
