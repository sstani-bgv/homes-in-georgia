"""Telegram adapter over core.py: clients fill the questionnaire and search flats; ❤️ becomes a lead in the CRM
(journey.py: operators group, agent slots, viewing, outcome, T14); staff review doubtful listings.
Client sees the album (photos + video) and a channel-style card without sources."""
import asyncio
import html
import json
import logging
import os
import re
from pathlib import Path

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, KeyboardButton, MenuButtonDefault,
                      MenuButtonWebApp, ReplyKeyboardMarkup, Update, WebAppInfo)
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import core
import db
import inbound
from imgutil import to_jpeg
import journey
import sheets
from app.telegram import links

ENV = {}
LEGACY_MAP_URL = "https://tbilisi-rent-map.vercel.app/"  # old static copy of webapp/index.html


def map_url():
    """webapp/index.html (Leaflet map, sends {lat,lng,radius,label}), served at PUBLIC_URL/map by the backend."""
    base = core.app_url()
    return os.environ.get("MAP_URL") or (base + "/map" if base else LEGACY_MAP_URL)
CLIENT_IMG = "data/client"
REVIEW_EVERY = 600  # seconds between pushes of pending listings to agents
tasks = {}  # user id -> running search (debounce); transient, everything else is in the DB
menus = {}  # private chat id -> True (agent panel menu button set) / False (left default); once per process
Btn, Kb = InlineKeyboardButton, InlineKeyboardMarkup


def map_kb(lang):
    return ReplyKeyboardMarkup([[KeyboardButton(core.t(lang, "map_btn"), web_app=WebAppInfo(map_url()))],
                                [KeyboardButton(core.t(lang, "here_btn"), request_location=True)]], resize_keyboard=True)


LANGS = [("ru", "🇷🇺 Русский"), ("en", "🇬🇧 English"), ("ka", "🇬🇪 ქართული")]  # core.T languages = texts.LANGS


def lang_kb():
    return Kb([[Btn(label, callback_data=f"lang:{code}") for code, label in LANGS]])


def bind_agent(user):
    """An agent added in the admin by @username gets its Telegram id on the first message to the bot."""
    return core.agent(user.id, user.full_name, user.username)


def kb_of(markup):
    """core's Bot API JSON keyboard -> python-telegram-bot objects."""
    return Kb([[Btn(b["text"], callback_data=b.get("callback_data"), url=b.get("url"),
                    web_app=b.get("web_app") and WebAppInfo(b["web_app"]["url"]))
                for b in row] for row in markup["inline_keyboard"]])


def panel_url():
    return core.app_url() and links.miniapp("")


def panel_kb():
    """Inline web_app button: unlike a reply-keyboard web_app button it hands the Mini App signed initData with the user."""
    return Kb([[Btn("📋 Панель", web_app=WebAppInfo(panel_url()))]])


async def sync_menu(bot, chat_id):
    """Per-chat menu button: the agent panel for active agents, the default one for ex-agents; clients are never touched.
    Needs only AGENT_APP_URL (setChatMenuButton with chat_id, no BotFather setup)."""
    url = panel_url()
    if not url or chat_id <= 0:
        return
    is_agent = bool(await asyncio.to_thread(core.agent, chat_id))
    if menus.get(chat_id) == is_agent:
        return
    if not is_agent:
        if not await asyncio.to_thread(known_user, chat_id):  # a client: keep Telegram's default menu
            menus[chat_id] = False
            return
    try:
        await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonWebApp("Панель", WebAppInfo(url))
                                       if is_agent else MenuButtonDefault())
        menus[chat_id] = is_agent
    except TelegramError as e:
        print("menu button for", chat_id, "failed:", e)


def known_user(chat_id):
    with db.tx() as c:
        return bool(c.execute("SELECT 1 FROM users WHERE tg_id = ?", (chat_id,)).fetchone())


def client_of(user, chat_id=None):
    return core.client("tg", user.id, chat_id=chat_id, username=user.username, name=user.full_name,
                       lang_hint=user.language_code)


def file_id(path):
    """Telegram file_id of a file uploaded before (videos: tens of MB, uploaded once per file)."""
    with db.tx() as c:
        r = c.execute("SELECT file_id, size FROM tg_files WHERE path = ?", (path,)).fetchone()
    return r and r["size"] == os.path.getsize(path) and r["file_id"]


def remember_files(items, msgs):
    with db.tx() as c:
        for it, m in zip(items, msgs or []):
            if it["type"] == "video" and it.get("path") and getattr(m, "video", None):
                c.execute("INSERT INTO tg_files (path, file_id, size) VALUES (?, ?, ?) ON CONFLICT (path) DO UPDATE SET "
                          "file_id = excluded.file_id, size = excluded.size", (it["path"], m.video.file_id, os.path.getsize(it["path"])))


def media_of(items):
    """core.album() items -> InputMedia list (photos as bytes; a video by its channel/cached file_id or as bytes)."""
    out = []
    for it in items:
        if it["type"] == "photo":
            out.append(InputMediaPhoto(Path(it["path"]).read_bytes()))
        elif it.get("file_id"):  # the agency channel's own video: no upload, no size limit
            out.append(InputMediaVideo(it["file_id"], supports_streaming=True))
        else:
            out.append(InputMediaVideo(file_id(it["path"]) or Path(it["path"]).read_bytes(), supports_streaming=True,
                                       width=it.get("w"), height=it.get("h"), duration=it.get("duration")))
    return out


# ---------- agency channel videos: file_id from the channel itself (the bot is a channel admin), no downloads ----------

def agency_channel():
    return os.environ.get("AGENCY_CHANNEL", "rent_tbilisi_ge").lstrip("@").lower()


def channel_video(i):
    """file_id of the agency post's video (live channel_post or backfilled forward) for a listing tg<post id>, or None.
    Any message of the post's album maps to the album's video, so it doesn't matter which one ingest named the listing by."""
    m = i.get("source") == "agency" and re.fullmatch(r"tg(\d+)", i.get("id") or "")
    if not m:
        return None
    n, ch = int(m[1]), agency_channel()
    with db.tx() as c:
        r = c.execute("SELECT file_id FROM channel_videos WHERE chat = :ch AND file_id != '' AND (message_id = :n "
                      "OR media_group_id = :post OR media_group_id = (SELECT media_group_id FROM channel_videos "
                      "WHERE chat = :ch AND message_id = :n)) ORDER BY message_id LIMIT 1",
                      {"ch": ch, "n": n, "post": f"post:{n}"}).fetchone()
    return r and r[0]


def save_channel_msg(chat, message_id, group, fid):
    with db.tx() as c:
        c.execute("INSERT INTO channel_videos (chat, message_id, media_group_id, file_id, at) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT (chat, message_id) DO UPDATE SET "
                  "media_group_id = excluded.media_group_id, file_id = excluded.file_id, at = excluded.at",
                  (chat, message_id, group, fid, db.now()))


async def on_channel_post(update: Update, ctx):
    """New/edited post in the agency channel: remember every message (album membership) and its video's file_id.
    Album messages arrive as separate updates with one media_group_id."""
    m = update.channel_post or update.edited_channel_post
    if not m or (m.chat.username or "").lower() != agency_channel():
        return
    await asyncio.to_thread(save_channel_msg, agency_channel(), m.message_id, m.media_group_id and str(m.media_group_id),
                            m.video and m.video.file_id)


warned = set()  # backfill problems already logged in this process


def video_store_chat():
    """Where the backfill forwards a video to read its file_id (deleted right away): VIDEO_STORE_CHAT_ID, else the
    first agent chat / the agency group, else None (no backfill)."""
    if v := os.environ.get("VIDEO_STORE_CHAT_ID", "").strip():
        return int(v) if re.fullmatch(r"-?\d+", v) else v
    chats = core.staff_chats()
    return chats[0] if chats else None


def backfill_todo():
    """Agency listings whose post has a video (details.video_msg from t.me/s or Telethon) but no file_id yet."""
    ch = agency_channel()
    with db.tx() as c:
        rows = c.execute("SELECT id, source, details::json->>'video_msg' AS vm FROM listings WHERE source = 'agency' "
                         "AND status != 'gone' AND details::json->>'video_msg' IS NOT NULL ORDER BY id DESC").fetchall()
        tried = {r[0] for r in c.execute("SELECT message_id FROM channel_videos WHERE chat = ?", (ch,))}
    return [(r["id"], int(r["vm"])) for r in rows if int(r["vm"]) not in tried and not channel_video(dict(r))]


async def backfill_videos(bot, pause=1.0, limit=None):
    """Old agency posts: forward the video message into the store chat, keep message.video.file_id, delete the copy.
    Idempotent (done/failed messages are in channel_videos), ~1 forward per `pause` s, stops at the first access error
    (bot not an admin of the channel, store chat unavailable, flood limit) and logs it once. -> number of videos stored."""
    if os.environ.get("VIDEO_BACKFILL", "1") == "0":
        return 0  # Review deployments must not forward historic videos into staff chats.
    todo = backfill_todo()[:limit]
    if not todo:
        return 0
    store = video_store_chat()
    if store is None:
        if "store" not in warned:
            warned.add("store")
            print(f"video backfill: {len(todo)} agency videos wait, but there is no VIDEO_STORE_CHAT_ID and no agent chat")
        return 0
    ch, done = agency_channel(), 0
    try:  # without admin rights Telegram answers "message to forward not found" for every post: check first
        status = (await bot.get_chat_member(f"@{ch}", bot.id)).status
    except TelegramError as e:
        status = str(e)
    if status not in ("administrator", "creator"):
        if "admin" not in warned:
            warned.add("admin")
            print(f"video backfill: @{bot.username} is not an admin of @{ch} ({status}); {len(todo)} videos wait")
        return 0
    if "todo" not in warned:
        warned.add("todo")
        print(f"video backfill: {len(todo)} agency videos to fetch via @{ch} -> chat {store}")
    for lid, vm in todo:
        try:
            m = await bot.forward_message(store, f"@{ch}", vm, disable_notification=True)
        except TelegramError as e:
            # only a missing MESSAGE means the post is gone; "chat not found" = the bot isn't an admin yet -> retry later
            if re.search(r"message to forward not found|message_id_invalid|can't be forwarded", str(e), re.I):
                save_channel_msg(ch, vm, f"post:{lid[2:]}", "")
                continue
            if str(e) not in warned:
                warned.add(str(e))
                print(f"video backfill stopped at {lid} (is the bot an admin of @{ch}? store chat {store}?): {e}")
            break
        try:
            await bot.delete_message(m.chat_id, m.message_id)
        except TelegramError as e:
            print("video backfill: could not delete the forwarded copy:", e)
        video = getattr(m, "video", None)
        save_channel_msg(ch, vm, f"post:{lid[2:]}", video.file_id if video else "")
        done += bool(video)
        await asyncio.sleep(pause)
    if done:
        print(f"video backfill: {done} agency videos now by file_id, {len(todo) - done} left/failed")
    return done


async def send_album(bot, chat_id, i, photos=core.ALBUM_PHOTOS):
    """A listing's photos + video as one album (no captions: the buttons go on the text card after it).
    The agency channel's own video (file_id) wins over a local file."""
    items = core.album(i, photos)
    if fid := await asyncio.to_thread(channel_video, i):
        items = [x for x in items if x["type"] == "photo"] + [{"type": "video", "file_id": fid}]
    if not items:
        return
    try:
        msgs = await bot.send_media_group(chat_id, media_of(items))
    except TelegramError as e:  # a broken video must not cost the photos
        if not any(x["type"] == "video" for x in items):
            raise
        print("album with video failed, photos only:", e)
        items = [x for x in items if x["type"] == "photo"]
        msgs = await bot.send_media_group(chat_id, media_of(items)) if items else []
    await asyncio.to_thread(remember_files, items, msgs)


# ---------- client side ----------

async def search(chat_id, user, ctx, delay=2.5):
    await asyncio.sleep(delay)  # debounce: an album / several quick messages = one search
    cl = await asyncio.to_thread(client_of, user)
    lang = cl["lang"]
    await ctx.bot.send_message(chat_id, core.t(lang, "searching"))
    await ctx.bot.send_chat_action(chat_id, "typing")
    try:
        res = await asyncio.to_thread(core.search, cl)
    except Exception as e:
        print("search failed:", e)
        await ctx.bot.send_message(chat_id, core.t(lang, "error"))
        return
    if not res["items"]:
        await ctx.bot.send_message(chat_id, core.t(lang, "none", understood=res["understood"]))
        return
    await ctx.bot.send_message(chat_id, core.t(lang, "found", understood=res["understood"], total=res["total"],
                                               base=res["base"], n=len(res["items"])))
    for x in res["items"]:
        i = await asyncio.to_thread(core.client_listing, x["listing"]["id"])
        if not i:  # publication may have been withdrawn while the model was choosing cards
            continue
        try:  # one broken album (file gone, Telegram refusing a photo) must not cost the other listings
            await send_album(ctx.bot, chat_id, i)
        except Exception as e:
            logging.getLogger(__name__).warning("album of %s failed: %r", i.get("id"), e)
        try:
            # buttons can't sit on an album, so ❤️/👎 and "📍 On the map" (approximate zone only) go on the text card
            text, markup = core.card(i, x["reason"], x["near"], lang), core.card_keyboard(i, lang)
            m = await ctx.bot.send_message(chat_id, text, reply_markup=kb_of(markup))
            await asyncio.to_thread(journey.remember_card, cl, i["id"], chat_id, m.message_id, text, markup["inline_keyboard"][1:],
                                    res.get("search_id"))
        except Exception as e:
            logging.getLogger(__name__).warning("card of %s failed: %r", i.get("id"), e)
    st, prof = cl["state"], cl.get("profile") or {}
    tips = (core.t(lang, "tips") + ("" if st["images"] or prof.get("photos") else core.t(lang, "tip_photo")) +
            ("" if st["pin"] or prof.get("pin") else core.t(lang, "tip_map")))
    await ctx.bot.send_message(chat_id, tips, reply_markup=Kb([[Btn(core.t(lang, "more"), callback_data="more")],
                                                              [Btn(core.t(lang, "lang_btn"), callback_data="lang:menu")]]))


def schedule(update: Update, ctx, delay=2.5):
    uid = update.effective_user.id
    if uid in tasks and not tasks[uid].done():
        tasks[uid].cancel()
    tasks[uid] = asyncio.create_task(search(update.effective_chat.id, update.effective_user, ctx, delay))


async def on_start(update: Update, ctx):
    """A1a: the questionnaire (once per client), then the saved profile; the free-text search keeps working."""
    await asyncio.to_thread(bind_agent, update.effective_user)
    await sync_menu(ctx.bot, update.effective_chat.id)
    cl = await asyncio.to_thread(client_of, update.effective_user, update.effective_chat.id)
    await asyncio.to_thread(core.reset, cl, lang_hint=update.effective_user.language_code)
    if not cl["state"].get("lang_set"):  # first contact: the language first, then the questionnaire (on_lang_button)
        await on_lang(update, ctx)
        return
    await journey.on_start(update, ctx, cl)


async def on_lang(update: Update, ctx):
    """/lang, «🌐 Язык» (lang:menu) and the first /start: pick the client language (saved in clients.lang)."""
    await ctx.bot.send_message(update.effective_chat.id, "Выберите язык / Choose your language / აირჩიეთ ენა", reply_markup=lang_kb())


async def on_lang_button(update: Update, ctx):
    q = update.callback_query
    lang = q.data.split(":", 1)[1]
    if lang not in core.T or not journey.first_tap(q, "lang"):  # a double tap must not start the questionnaire twice
        return await journey.answer(q)
    cl = await asyncio.to_thread(client_of, q.from_user, update.effective_chat.id)
    await asyncio.to_thread(core.set_lang, cl, lang)
    await journey.answer(q)
    await journey.edit_text(q, dict(LANGS)[lang])
    await journey.on_start(update, ctx, cl)


async def on_cancel(update: Update, ctx):
    """/cancel: the bot stops waiting for typed slot times (staff) or a free-text answer (client)."""
    cl = await asyncio.to_thread(client_of, update.effective_user, update.effective_chat.id)
    await journey.on_cancel(update, ctx, cl)


async def on_message(update: Update, ctx):
    msg, user = update.message, update.effective_user  # the handler takes new messages only (not edited ones)
    if msg is None:
        return
    if await journey.on_staff_text(update, ctx):  # an agent typing slot times (C7)
        return
    await asyncio.to_thread(bind_agent, user)
    await sync_menu(ctx.bot, update.effective_chat.id)
    cl = await asyncio.to_thread(client_of, user, update.effective_chat.id)
    if await journey.on_client_text(update, ctx, cl):  # budget / own time / T14 problem the bot asked for
        return
    image = pin = label = radius = None
    pic = msg.photo[-1] if msg.photo else msg.document  # images sent "as file" arrive as documents
    if pic:
        os.makedirs(CLIENT_IMG, exist_ok=True)
        image = f"{CLIENT_IMG}/{user.id}_{msg.message_id}.jpg"
        await (await pic.get_file()).download_to_drive(image + ".src")
        # files can be png/heic: normalize to jpeg so the model always reads them
        await asyncio.to_thread(to_jpeg, image + ".src", image, 1600)
        os.remove(image + ".src")
    if msg.location:
        pin, label, radius = (msg.location.latitude, msg.location.longitude), core.t(cl["lang"], "here"), 2.0
    if msg.web_app_data:  # contract with webapp/index.html: Telegram.WebApp.sendData({lat, lng, radius, label})
        d = json.loads(msg.web_app_data.data)
        pin, label, radius = (d["lat"], d["lng"]), d.get("label") or core.t(cl["lang"], "picked"), d.get("radius") or 2.0
        await msg.reply_text(core.t(cl["lang"], "radius", r=radius, label=label))
    if pin and journey.awaiting(cl)[0] == "pin":  # the questionnaire's «где искать» (A1c): the point goes to the profile
        await journey.on_pin(ctx.bot, cl, pin[0], pin[1], radius, label if msg.web_app_data else None)
        return
    if image:
        await asyncio.to_thread(journey.save_profile, cl, lambda p: p.update(photos=(p.get("photos") or 0) + 1))
    await asyncio.to_thread(core.add_input, cl, text=msg.text or msg.caption, image=image, pin=pin, pin_label=label, radius=radius)
    if db.get_setting("auto_push_variants") != "1":  # new variants only by «Ещё»: the message refines the request
        await msg.reply_text(core.t(cl["lang"], "noted"), reply_markup=Kb([[Btn(core.t(cl["lang"], "more"), callback_data="more")]]))
        return
    schedule(update, ctx)


async def on_client_button(update: Update, ctx, action, lid):
    """❤️ -> lead in the CRM (A3: the same card changes, B1 card in the operators group); 👎 -> taste."""
    q = update.callback_query
    cl = await asyncio.to_thread(client_of, q.from_user)
    if action == "like":
        await journey.on_like(update, ctx, cl, lid)
        sheets.sync_later()
        return
    if await asyncio.to_thread(core.react, cl, lid, "dislike") is None:
        await journey.answer(q, core.t(cl["lang"], "gone"))
        return
    await journey.answer(q, core.t(cl["lang"], "disliked"))
    try:
        await q.edit_message_reply_markup(None)
    except TelegramError:
        pass  # a double tap: the keyboard is already gone


# ---------- staff side: review of doubtful listings ----------

async def send_reviews(bot, chats=None, limit=None):
    """Push pending listings (not sent yet) to agents with Approve / Reject."""
    chats = chats or core.staff_chats()
    if not chats:
        return 0
    items = await asyncio.to_thread(core.pending, limit or int(db.get_setting("review_batch")))
    for i in items:
        for chat in chats:
            try:
                await send_album(bot, chat, i, photos=3)
                m = await bot.send_message(chat, core.review_card(i), parse_mode="HTML", disable_web_page_preview=True,
                                           reply_markup=Kb([[Btn("✅ Опубликовать", callback_data=f"mod:{i['id']}:1"),
                                                             Btn("❌ Отклонить", callback_data=f"mod:{i['id']}:0")]]))
                await asyncio.to_thread(core.remember_message, "review", i["id"], chat, m.message_id)
            except TelegramError as e:
                print("review to", chat, "failed:", e)
        await asyncio.to_thread(core.mark_review_sent, i["id"])
    return len(items)


async def review_loop(app):
    while True:
        try:  # agency videos of posts found by the refresh (details.video_msg) -> file_id; a no-op when all are done
            await backfill_videos(app.bot)
        except Exception as e:
            print("video backfill:", e)
        await asyncio.sleep(REVIEW_EVERY)


async def edit_all(bot, kind, ref, text, kb):
    """kb: a keyboard, None, or a function of the chat id (lead cards differ between private chats and groups)."""
    for chat, mid in await asyncio.to_thread(core.messages, kind, ref):
        try:
            await bot.edit_message_text(text, chat, mid, parse_mode="HTML", disable_web_page_preview=True,
                                        reply_markup=kb(chat) if callable(kb) else kb)
        except TelegramError:
            pass  # "message is not modified" or deleted


def staff(update: Update):
    """The active CRM staff member pressing/typing (not recruits), or None. Group membership alone grants nothing."""
    u = update.effective_user
    a = core.agent(u.id, u.full_name, u.username)
    return a if a and a["role"] in db.STAFF_ROLES else None


async def on_staff_button(update: Update, ctx, data):
    q = update.callback_query
    a = await asyncio.to_thread(staff, update)
    if not a:
        await journey.answer(q, "Только для агентов")
        return
    _, ref, val = data.split(":", 2)
    i = await asyncio.to_thread(core.moderate, ref, val == "1", a["id"])
    who = html.escape(f"@{a['username']}" if a.get("username") else a.get("name") or "агент")
    await journey.answer(q, "Готово")
    verdict = "✅ Опубликовано" if val == "1" else "❌ Отклонено"
    await edit_all(ctx.bot, "review", ref, core.review_card(i, f"\n\n<b>{verdict}</b> · {who}"), None)


async def on_button(update: Update, ctx):
    q = update.callback_query
    if q.data == "noop":
        await journey.answer(q)
    elif q.data == "more":
        await journey.answer(q)
        schedule(update, ctx, delay=0)
    elif q.data == "lang:menu":
        await journey.answer(q)
        await on_lang(update, ctx)
    elif q.data.startswith("lang:"):
        await on_lang_button(update, ctx)
    elif q.data.startswith("mod:"):
        await on_staff_button(update, ctx, q.data)
    elif await journey.on_callback(update, ctx, lambda: client_of(q.from_user, update.effective_chat.id)):
        pass
    elif q.data.startswith(("like:", "no:")):
        await on_client_button(update, ctx, *q.data.split(":", 1))
    else:
        await journey.answer(q)


async def on_agent(update: Update, ctx):
    u = update.effective_user
    if ctx.args and ctx.args[0] == ENV.get("AGENT_CODE"):
        await asyncio.to_thread(core.agent, u.id, u.full_name, u.username, create=True)
        if panel_url():
            await sync_menu(ctx.bot, update.effective_chat.id)
            await update.message.reply_text(
                "✅ Вы агент. Сюда будут приходить новые заявки и объявления на проверку.\n\n"
                "Объекты, заявки, сделки и отчёты — в панели: кнопка «Панель» слева от поля ввода или кнопка ниже.",
                reply_markup=panel_kb())
        else:
            await update.message.reply_text("✅ Вы агент. Сюда будут приходить новые заявки и объявления на проверку.")
    else:
        await update.message.reply_text("Неверный код.")


async def on_panel(update: Update, ctx):
    """/panel: staff open the Mini App (PUBLIC_URL/m/)."""
    u = update.effective_user
    if not await asyncio.to_thread(core.agent, u.id, u.full_name, u.username):
        await update.message.reply_text("Панель только для сотрудников: /agent <код> или попросите администратора добавить вас в CRM.")
    elif not panel_url():
        await update.message.reply_text("Панель недоступна: не задан PUBLIC_URL (https).")
    else:
        await sync_menu(ctx.bot, update.effective_chat.id)
        await update.message.reply_text("📋 Панель: объекты, заявки, сделки и отчёты", reply_markup=panel_kb())


async def on_staff_cmd(update: Update, ctx):
    """Old chat commands, kept working for compatibility; the same settings now live in the agents' Mini App."""
    u = update.effective_user
    if not await asyncio.to_thread(core.agent, u.id, u.full_name, u.username):
        await update.message.reply_text("Только для агентов: /agent <код> в личке бота")
        return
    cmd, chat = update.message.text.split()[0][1:].split("@")[0], update.effective_chat
    await sync_menu(ctx.bot, chat.id)
    if cmd == "leads_here":
        if chat.type == "private":
            await update.message.reply_text("Отправьте эту команду в группе агентства.")
            return
        db.set_setting("group_chat_id", chat.id)
        db.set_setting("lead_target", "group")
        await update.message.reply_text("✅ Лиды и проверка объявлений теперь приходят в эту группу.")
    elif cmd == "leads_private":
        db.set_setting("lead_target", "agents")
        await update.message.reply_text("✅ Лиды и проверка объявлений теперь приходят агентам в личку.")
    elif cmd == "moderate_all":
        if ctx.args and ctx.args[0] in ("on", "off"):
            db.set_setting("moderate_all", "1" if ctx.args[0] == "on" else "0")
        await update.message.reply_text(f"Проверка всех объявлений конкурентов и FB: "
                                        f"{'вкл' if db.get_setting('moderate_all') == '1' else 'выкл'} "
                                        "(действует со следующего обновления базы).")
    elif cmd == "review":
        n = await send_reviews(ctx.bot, chats=[chat.id], limit=10)
        if not n:
            await update.message.reply_text("Новых объявлений на проверку нет.")


# explicit: getUpdates otherwise keeps the previous call's list; channel_post carries the agency channel's videos and
# listings/#Rented (inbound.py); edited_* updates re-parse listings/news
ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "channel_post", "edited_channel_post"]


COMMANDS = {"ru": (("start", "Подбор квартиры"), ("lang", "🌐 Язык")), "en": (("start", "Find a flat"), ("lang", "🌐 Language")),
            "ka": (("start", "ბინის შერჩევა"), ("lang", "🌐 ენა"))}


def build(token, staff=False):
    """staff=True: the STAFF_BOT_TOKEN bot (notify sends staff cards through it): the same handlers, so its buttons
    (lg:/ls:/lo:a:) work, but no channel/work-group intake and no background loop (the client bot does those)."""
    async def post_init(app):
        if staff:
            return
        for lc, cmds in COMMANDS.items():  # the client's ☰ menu: /start and /lang in their Telegram language
            try:
                await app.bot.set_my_commands([BotCommand(c, d) for c, d in cmds], language_code=None if lc == "en" else lc)
            except TelegramError as e:
                print("set_my_commands", lc, "failed:", e)
        if panel_url():  # the panel menu button for every known staff user (and back to default for disabled ones)
            with db.tx() as c:
                known = [r[0] for r in c.execute("SELECT tg_id FROM users WHERE tg_id IS NOT NULL")]
            for chat in known:
                await sync_menu(app.bot, chat)
        # keep a strong reference: a bare asyncio task can be garbage-collected and the loop silently dies
        with db.tx() as c:  # forget "no video" marks: an access error before the bot became an admin could leave them
            c.execute("DELETE FROM channel_videos WHERE file_id = '' AND media_group_id LIKE 'post:%'")
        app.bot_data["review_task"] = asyncio.get_running_loop().create_task(review_loop(app))

    b = Application.builder().token(token).concurrent_updates(True).post_init(post_init)
    if api := os.environ.get("TG_API_BASE"):  # a local fake Bot API for end-to-end tests (test/e2e)
        b = b.base_url(f"{api}/bot").base_file_url(f"{api}/file/bot")
    app = b.build()
    # new messages only: edited ones (edited_message is in ALLOWED_UPDATES) have update.message = None
    new = filters.UpdateType.MESSAGE
    private = filters.ChatType.PRIVATE & new
    app.add_handler(CommandHandler("start", on_start, filters=private))
    app.add_handler(CommandHandler("agent", on_agent, filters=private))
    app.add_handler(CommandHandler("panel", on_panel, filters=private))
    app.add_handler(CommandHandler("cancel", on_cancel, filters=private))
    app.add_handler(CommandHandler("lang", on_lang, filters=private))
    app.add_handler(CommandHandler(["leads_here", "leads_private", "moderate_all", "review"], on_staff_cmd, filters=new))
    app.add_handler(CallbackQueryHandler(on_button))
    if not staff:  # the agency channel (the bot is its admin): videos of new posts by file_id
        app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, on_channel_post))
    # a staff member forwards a #Rented post to the bot → pending deal (before the client journey sees the text)
    inbound.register_private(app)
    app.add_handler(MessageHandler(private & ((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.IMAGE
                                              | filters.LOCATION | filters.StatusUpdate.WEB_APP_DATA), on_message))
    if not staff:  # work group topics (listings, news, Make-Foto, #Rented) and the agency channel → backend (group 1: alongside)
        inbound.register_groups(app)
    app.add_error_handler(on_error)
    return app


async def on_error(update, ctx):
    """Any handler exception: logged, never re-raised (PTB would otherwise only print «No error handlers registered»)."""
    logging.getLogger(__name__).error("update %s failed", getattr(update, "update_id", None), exc_info=ctx.error)


def main():
    db.load_env()  # repo .env if present (the server passes real env vars instead); real env vars win
    ENV.update(os.environ)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db.init()
    if not db.stats()["listings"] and os.path.exists("listings.json"):  # first start on the DB: import the current base
        db.sync(full=False)
    print("bot up:", db.stats())
    staff = ENV.get("STAFF_BOT_TOKEN", "").strip()
    if not staff or staff == ENV["BOT_TOKEN"]:
        build(ENV["BOT_TOKEN"]).run_polling(allowed_updates=ALLOWED_UPDATES)
        return
    asyncio.run(run_both(build(ENV["BOT_TOKEN"]), build(staff, staff=True)))


async def run_both(*apps):
    """The client bot and the staff bot in one process (run_polling handles one application only)."""
    for app in apps:
        await app.initialize()
        if app.post_init:
            await app.post_init(app)
        await app.start()
        await app.updater.start_polling(allowed_updates=ALLOWED_UPDATES)
    try:
        await asyncio.Event().wait()
    finally:
        for app in apps:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


if __name__ == "__main__":
    main()
