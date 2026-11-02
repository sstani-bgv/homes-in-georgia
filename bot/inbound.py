"""Inbound updates from the agency work group (forum topics) and the agency/deals channel → backend services
(app.services.inbound: listings, news, Make-Foto queue, #Rented deals). Port of the Apps Script pollUpdates handlers.

Registered by bot.build():
- group 1 (runs alongside the group-0 handlers, e.g. the channel-video one): every group/channel message and edit;
- group 0, before the client journey handler: a forwarded #Rented message in a private chat (so it doesn't reach search).
Messages from chats that aren't configured (WORK_GROUP_CHAT_ID, NEWS_CHAT_ID, AGENCY_CHANNEL(_ID), DEALS_CHANNEL_ID) are
ignored without touching the DB. The bot must see group messages: make it an admin of the work group (or disable privacy
mode in BotFather)."""
import asyncio
import logging

from telegram import ReplyParameters, Update
from telegram.error import TelegramError
from telegram.ext import MessageHandler, filters

log = logging.getLogger(__name__)
DEAL_RE = r"#Rented|#Sold|#Sale"


def _process(msg):
    from app.db import SessionLocal
    from app.services import inbound
    with SessionLocal() as s:
        return inbound.process(s, msg)


def parse(update: Update):
    from app.services import inbound
    m = update.effective_message
    if m is None:
        return None
    edited = bool(update.edited_message or update.edited_channel_post)
    return inbound.from_telegram(m.to_dict(), edited=edited)


async def handle(update: Update, ctx):
    from app.services import inbound
    msg = parse(update)
    if msg is None or not inbound.relevant(msg):
        return
    res = await asyncio.to_thread(_process, msg)
    for r in res.get("replies") or []:
        try:
            reply = ReplyParameters(r["reply_to"], allow_sending_without_reply=True) if r.get("reply_to") else None
            await ctx.bot.send_message(r["chat_id"], r["text"], parse_mode="HTML", message_thread_id=r.get("thread_id"),
                                       reply_parameters=reply)
        except TelegramError as ex:
            log.info("inbound reply to %s failed: %s", r["chat_id"], ex)


async def on_group_or_channel(update: Update, ctx):
    await handle(update, ctx)


async def on_forwarded_deal(update: Update, ctx):
    await handle(update, ctx)


def forwarded_deal_filter():
    return (filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE & filters.FORWARDED
            & (filters.Regex(DEAL_RE) | filters.CaptionRegex(DEAL_RE)))


def register_private(app):
    """Group 0, must be added before the client message handler."""
    app.add_handler(MessageHandler(forwarded_deal_filter(), on_forwarded_deal))


def register_groups(app):
    chats = filters.ChatType.GROUPS | filters.ChatType.CHANNEL
    app.add_handler(MessageHandler(chats & (filters.UpdateType.MESSAGES | filters.UpdateType.CHANNEL_POSTS),
                                   on_group_or_channel), group=1)
