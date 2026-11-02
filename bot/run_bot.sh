#!/bin/bash
# Keeps the bot alive: restarts on crash, holds the Mac awake (idle sleep) while running.
# Stop: pkill -f run_bot.sh; pkill -f "Python -u bot.py"
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # codex lives here; nohup/launchd PATH lacks it
caffeinate -i -w $$ &
while true; do
  .venv/bin/python -u bot.py >> raw/bot.log 2>&1
  echo "bot exited $?, restarting in 5s" >> raw/bot.log
  sleep 5
done
