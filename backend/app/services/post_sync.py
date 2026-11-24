"""Сверка с каналом (Stanislav 11-22, Irakli 11-20): operators still delete posts by hand in the channel, the Bot API
sends no event for that, so an object whose post is gone stayed on hge.ge (164 hidden by hand 11-20). Every few hours
each object on the site with a t.me post is checked on the public preview: «Post not found» → off the site (site_hidden),
an event on the object says why. No Telegram account needed."""
import logging
import re
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Property, PropertyPublication
from app.services.common import notify_later, now

log = logging.getLogger(__name__)
POST = re.compile(r"t\.me/([A-Za-z]\w{3,})/(\d+)")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
MAX_SHARE = 0.5  # more than this share «gone» at once (and more than 10): t.me answers oddly, hide nothing


def gone(c: httpx.Client, channel: str, msg: int) -> bool | None:
    """True: the post was deleted; False: it is there; None: can't tell (network, an odd page)."""
    try:
        r = c.get(f"https://t.me/{channel}/{msg}", params={"embed": "1", "mode": "tme"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    if "tgme_widget_message_error" in r.text:
        return True
    return False if "tgme_widget_message" in r.text else None


def alive(channel: str, msgs: list[int]) -> set[int] | None:
    """The post ids still in the channel, asked through the Telegram account (channel_import.ask): the web preview
    can't show the rich posts; None when no account answers."""
    from app.services.channel_import import ask
    out = set()
    for i in range(0, len(msgs), 100):
        r = ask("/exists", {"user": channel, "ids": msgs[i:i + 100]}, timeout=70)
        if r is None:
            return None
        out |= set(r["ids"])
    return out


def run(db: Session, pause: float = 0.5, out=log.info) -> int:
    """The objects whose channel post is gone: an object imported from the channel goes to the archive
    («Неактуально», Stanislav 11-22: what they delete in the channel is gone in the CRM too), any other leaves the
    site and the bot as rented («Сдал сам», Stanislav 11-23; its card shows why) -> how many."""
    from app.services import properties as P
    rows = db.scalars(select(Property).where(Property.status == "published", Property.post_url.is_not(None),
                                             ~Property.site_hidden | (Property.source_type == "channel"))).all()
    posts = [(p, m) for p in rows if (m := POST.search(p.post_url or ""))]
    dead = []
    by_channel: dict = {}
    for p, m in posts:
        by_channel.setdefault(m[1], []).append((p, int(m[2])))
    with httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as c:
        for channel, items in by_channel.items():
            there = alive(channel, [msg for _, msg in items])
            for p, msg in items:
                if there is not None:
                    gone_ = msg not in there
                else:
                    gone_ = gone(c, channel, msg)
                    time.sleep(pause)
                if gone_:
                    dead.append(p)
    if len(dead) > 10 and len(dead) > MAX_SHARE * len(posts):
        out(f"post sync: {len(dead)} of {len(posts)} posts look deleted, hiding nothing (t.me answers oddly?)")
        return 0
    for p in dead:
        if p.source_type == "channel":
            p.archive_reason, p.archive_comment, p.archived_at = "not_actual", "пост удалён в канале", now()
            P._status(db, p, "archived", None, "archived", label="сверка с каналом", reason="not_actual",
                      comment="пост удалён в канале", post_url=p.post_url)
        else:  # Stanislav 11-23: deleted in the channel = rented («Сдал сам»): off the site and the bot, no #FlatRented
            p.archive_reason, p.archive_comment, p.archived_at = "rented_by_owner", "пост удалён в канале", now()
            P._status(db, p, "archived", None, "archived", label="сверка с каналом", reason="rented_by_owner",
                      comment="пост удалён в канале", post_url=p.post_url)
        notify_later(db, "prop_event", p, "🗑 <b>Пост удалён в канале</b>", None, None, True,
                     f"Объект в архиве: {'Неактуально' if p.source_type == 'channel' else 'Сдал сам'}.")
        for pub in db.scalars(select(PropertyPublication).where(  # gone already: nothing for unpost to delete
                PropertyPublication.property_id == p.id, PropertyPublication.channel == "tg",
                PropertyPublication.status == "published")):
            pub.status, pub.removed_at = "removed", now()
    db.commit()
    if dead:
        from app.services import catalog, site_feed
        site_feed.clear_cache()
        catalog.sync_all(db)
        db.commit()
    out(f"post sync: {len(posts)} posts checked, {len(dead)} deleted: {[p.id for p in dead]}")
    return len(dead)
