"""Telegram profile photos for the team (Irakli 2026-11-11: «аватарки с тг подгрузим»): an active user with a tg_id and
no photo -> getUserProfilePhotos (the main bot) -> the biggest size, 256 px JPEG -> storage avatars/u<id>.jpg ->
users.photo_url = /api/media/avatars/u<id>.jpg. A Telegram sign-in sets photo_url itself (its t.me/i/userpic link).
A user whose privacy hides the photo from bots keeps the initials.

Run: cd backend && python -m app.telegram.avatars [--all]   (--all: refresh the ones already set by this script)"""
import io
import logging
import sys

import httpx
from sqlalchemy import select

from app import storage
from app.db import SessionLocal
from app.models import User
from app.telegram import client as tg
from app.telegram.client import conf

log = logging.getLogger(__name__)
PX = 256


def fetch(tg_id: int) -> bytes | None:
    """The user's current profile photo as a PX JPEG, or None (no photo / hidden / Telegram down)."""
    res = tg.call("getUserProfilePhotos", user_id=tg_id, limit=1)
    sizes = ((res or {}).get("photos") or [[]])[0]
    if not sizes:
        return None
    f = tg.call("getFile", file_id=sizes[-1]["file_id"])
    if not f or not f.get("file_path"):
        return None
    api = conf("TG_API_BASE", "https://api.telegram.org").rstrip("/")
    r = httpx.get(f"{api}/file/bot{conf('BOT_TOKEN').strip()}/{f['file_path']}", timeout=30)
    if r.status_code != 200:
        return None
    from PIL import Image
    with Image.open(io.BytesIO(r.content)) as im:
        im = im.convert("RGB")
        im.thumbnail((PX, PX))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue()


def run(refresh: bool = False, out=print) -> tuple[int, int]:
    """-> (set, without a photo)."""
    done = missing = 0
    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.is_active.is_(True), User.tg_id.isnot(None))).all()
        for u in users:
            own = (u.photo_url or "").startswith("/api/media/avatars/")
            if u.photo_url and not (refresh and own):
                continue
            data = fetch(u.tg_id)
            if not data:
                missing += 1
                continue
            key = f"avatars/u{u.id}.jpg"
            storage.put(key, data)
            u.photo_url = f"/api/media/{key}"
            db.commit()
            done += 1
    out(f"avatars: {done} set, {missing} without a photo visible to the bot")
    return done, missing


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run(refresh="--all" in sys.argv)
