"""Start the real bot.py application against the fake Bot API (TG_API_BASE), with config from the environment
instead of .env: BOT_TOKEN (any string), AGENT_CODE, APP_DB. Same startup steps as bot.main()."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import bot  # noqa: E402
import db  # noqa: E402

assert os.environ.get("TG_API_BASE"), "refusing to start without the fake API (TG_API_BASE)"
bot.ENV.update(BOT_TOKEN=os.environ["BOT_TOKEN"], AGENT_CODE=os.environ["AGENT_CODE"])
db.init()
if not db.stats()["listings"] and os.path.exists("listings.json"):
    db.sync(full=False)
print("bot up:", db.stats(), flush=True)
bot.build(bot.ENV["BOT_TOKEN"]).run_polling()
