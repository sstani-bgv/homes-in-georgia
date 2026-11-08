#!/bin/bash
# Auto-update of the listings base: sources (DB) -> fetchers -> ingest -> sync into Postgres (DATABASE_URL, repo .env)
# -> embed (vectors). The synced `listings` table is the funnel 1 new-listings feed (GET /api/listings/new); phones that
# agents marked as "agent" (owners.is_agent) make a listing agent-suspect on sync.
# Run by hand or on a schedule (not installed, see docs/README.md "Автообновление"), e.g. cron every 6 hours:
#   0 */6 * * * /path/to/rent-tbilisi-ai/refresh.sh >> /path/to/rent-tbilisi-ai/data/refresh.log 2>&1
# Env: PY (python, default .venv/bin/python), TG (telegram-user-session runtime), SKIP_FACEBOOK=1 (Apify costs money), FB_EVERY_MIN (120), SKIP_THREADS=1, TH_EVERY_MIN (60),
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
"$PY" fetch_ss.py      || echo "fetch_ss failed, keeping old raw/ss.json"
"$PY" fetch_myhome.py  || echo "fetch_myhome failed, keeping old raw/myhome.json"
# livo.ge = the myhome base without Cloudflare (dedupe merges the same flat), korter.ge = owner listings with a pin
[ "${SKIP_LIVO:-0}" = 1 ]   || "$PY" fetch_livo.py   || echo "fetch_livo failed, keeping old raw/livo.json"
[ "${SKIP_KORTER:-0}" = 1 ] || "$PY" fetch_korter.py || echo "fetch_korter failed, keeping old raw/korter.json"
if [ "${TG_FETCH:-session}" = web ]; then     # server: public channels via t.me/s, no user session
  "$PY" fetch_channel_web.py || echo "fetch_channel_web failed for some channels, they keep old files"
elif [ -x "$TG" ]; then
  "$TG" --account main "$PWD/fetch_channel.py" || echo "fetch_channel failed, keeping old channel files"
else
  echo "no tg runtime at $TG, skipping Telegram channels"
fi
# Facebook goes through Apify and is incremental (only posts since the last run are paid for), but each
# run has a floor: FB $0.005 per start, IG ~$0.005 for "no new posts" rows. So they run at most every *_EVERY_MIN
# (FB every 2 h: ~900 posts/day x $0.0015 + 12 starts -> ~$40/month) even when refresh itself runs every 5 minutes.
due() { [ ! -f "$1" ] || [ -z "$(find "$1" -mmin -"$2")" ]; }  # state file older than N minutes (or none yet)
if [ "${SKIP_FACEBOOK:-0}" = 1 ]; then :
elif due raw/facebook_state.json "${FB_EVERY_MIN:-120}"; then
  "$PY" fetch_facebook.py || echo "fetch_facebook failed, keeping old raw/facebook.json"
else echo "facebook: fetched < ${FB_EVERY_MIN:-120} min ago, skipping Apify"; fi
# Instagram is off (2026-11-08: agencies only, no owners there); Threads replaces it: official API keyword search, free,
# skipped without a token (THREADS_ACCESS_TOKEN / THREADS_TOKEN_FILE)
if [ "${SKIP_THREADS:-0}" = 1 ]; then :
elif due raw/threads_state.json "${TH_EVERY_MIN:-60}"; then
  "$PY" fetch_threads.py || echo "fetch_threads failed, keeping old raw/threads.json"
else echo "threads: fetched < ${TH_EVERY_MIN:-60} min ago, skipping"; fi
"$PY" ingest.py || { echo "ingest failed"; exit 1; }
# video links (ss.ge HLS, YouTube...) -> mp4 in the album with yt-dlp, only listings without a local video; failures are
# cached for VIDEO_RETRY_DAYS and keep the "🎥 Видео" button (YouTube may block server IPs)
"$PY" fetch_videos.py || echo "fetch_videos failed, listings keep their video links"
"$PY" db.py sync || { echo "sync failed"; exit 1; }
# local photo/text vectors for search_mode=hybrid (only new listings, ~0.1 s per photo); a failure keeps the old ones
"$PY" embed.py || echo "embed failed, keeping old data/vectors.npz"
echo "=== $(date) refresh done"
