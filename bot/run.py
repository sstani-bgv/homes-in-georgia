"""Bot entrypoint: `python -m bot.run` from the repo root (or `python bot/run.py`). Long polling; config from env / the
repo .env (DATABASE_URL, BOT_TOKEN, TG_API_BASE, PUBLIC_URL, OPERATORS_CHAT_ID, AGENT_CODE ...).
`--check`: seed + print DB stats and exit (no Telegram)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)  # listing photos/videos are stored relative to bot/ (raw/img/...)
sys.modules.pop("bot", None)  # under `-m bot.run` the name is this directory; the bot itself is bot/bot.py

import bot  # noqa: E402
import db  # noqa: E402


def main():
    if "--check" in sys.argv:
        db.load_env()
        db.init()
        print("db ok:", db.stats())
        return
    bot.main()


if __name__ == "__main__":
    main()
