"""Every outgoing Telegram message (docs/ARCHITECTURE.md §6). Texts follow the journey screens (docs/user-journey.txt):
clients in their language (clients.lang: ru/en/ka), staff in Russian, all through the one bot (BOT_TOKEN). To the client
the bot sends only listing cards (❤️ marks the card); viewing time and the visit details come from the operator's
own account (userbot). Arguments may be ORM objects or dicts. Message refs go to the `messages` table (kind, ref) so
cards can be edited later. No function ever raises (failures are logged)."""
import contextvars
import functools
import html
import json
import logging
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote

from app import progress, storage
from app.telegram import client as tg
from app.telegram import links, store, texts, userbot
from app.telegram.client import app_btn, cb, conf, kb, url_btn
from app.telegram.store import g

log = logging.getLogger(__name__)
e = lambda v: html.escape(str(v if v is not None else ""))  # noqa: E731
# (chat_id, message_id) of the card the client tapped ❤️ on: the bot sets it around leads.create_from_like (asyncio.to_thread
# copies context vars into the worker thread), so lead_client_ack edits exactly that card
tapped_card = contextvars.ContextVar("tapped_card", default=None)


def safe(fn):
    """Never raises (but a worker job's transient tg.DeliveryError); without BOT_TOKEN it is a logged no-op (no DB reads either: API tests, dev without a bot)."""
    @functools.wraps(fn)
    def wrap(*a, **kw):
        if not tg.base():
            log.info("notify.%s skipped: BOT_TOKEN is not set", fn.__name__)
            return None
        try:
            return fn(*a, **kw)
        except tg.DeliveryError:  # only inside a worker job (tg.delivery): the job retries, an API request never sees it
            raise
        except Exception:
            log.exception("notify.%s failed", fn.__name__)
            return None
    return wrap


def env_int(k, d):
    try:
        return int(float(conf(k, d)))
    except ValueError:
        return d


def chat_and_topic(chat_key, topic_key=None):
    chat = conf(chat_key).strip()
    topic = conf(topic_key).strip() if topic_key else ""
    return (chat or None), (int(topic) if topic.lstrip("-").isdigit() else None)


def operators_targets():
    """[(chat_id, topic)] for operator messages: the connected operator account (Настройки → «Аккаунт оператора»,
    settings.userbot_tg_id) in private, only it (Stanislav 11-16: the other operators work in the CRM); not connected →
    OPERATORS_CHAT_ID (+topic), else the bot's group setting, else every operator/admin/superadmin in private."""
    if uid := (store.setting("userbot_tg_id") or "").strip():
        return [(uid, None)]
    chat, topic = chat_and_topic("OPERATORS_CHAT_ID", "OPERATORS_TOPIC_ID")
    if chat:
        return [(chat, topic)]
    if store.setting("lead_target") == "group" and (gid := store.setting("group_chat_id")):
        return [(gid, None)]
    return [(t, None) for t in store.staff_tg_ids(("operator", "admin", "superadmin"))]


def superadmins():
    return [x.strip() for x in conf("SUPERADMIN_TG_IDS").split(",") if x.strip()]


def is_private(chat):
    return str(chat).lstrip("-").isdigit() and int(chat) > 0


def to_operators(kind, ref, text, keyboard_for):
    """keyboard_for(private) -> markup (web_app buttons only work in private chats)."""
    for chat, topic in operators_targets():
        m = tg.send(chat, text, keyboard_for(is_private(chat)), thread_id=topic)
        if m and kind:
            store.remember(kind, ref, m["chat"]["id"], m["message_id"])


def to_lead_staff(lead, text, keyboard_for, keys=("operator_id",)):
    """Private messages to the lead's operator (and/or agent: keys) — the connected operator account when there is one;
    the operators chat too when the operator isn't reached (no operator yet, no Telegram id, or the send failed)."""
    sent, op_ok = set(), False
    connected = (store.setting("userbot_tg_id") or "").strip()  # the connected operator account stands for the operator
    for k in keys:
        u = {"tg_id": connected} if k == "operator_id" and connected else store.user(g(lead, k)) or {}
        tid = u.get("tg_id")
        if not tid or not u.get("is_active", True):
            continue
        if str(tid) in sent or tg.send(tid, text, keyboard_for(True)):
            sent.add(str(tid))
            op_ok = op_ok or k == "operator_id"
    if not op_ok:
        for chat, topic in operators_targets():
            if str(chat) not in sent:
                tg.send(chat, text, keyboard_for(is_private(chat)), thread_id=topic)


def edit_refs(kind, ref, text, keyboard_for):
    for chat, mid in store.refs(kind, ref):
        tg.edit(chat, mid, text, keyboard_for(is_private(chat)))


# ---------- what a lead is about ----------

def ctx(lead):
    """(client row, listing row, lang) of a lead."""
    cl = store.client(g(lead, "client_id")) or {}
    i = store.listing(g(lead, "listing_id")) or {}
    return cl, i, texts.lang_of(cl.get("lang"))


def district(i, lang="ru"):
    d = i.get("details") or {}
    if lang == "ka" and i.get("district") in texts.DISTRICT_KA:
        return texts.DISTRICT_KA[i["district"]]
    if lang != "ru":
        return i.get("district_en") or d.get("district_en") or texts.DISTRICT_EN.get(i.get("district"), i.get("district")) or "Tbilisi"
    return i.get("district") or "Тбилиси"


def address(i, lang="ru"):
    d = i.get("details") or {}
    a = (lang != "ru" and d.get("address_en")) or d.get("address_ru") or i.get("address")
    return f"{district(i, lang)}, {a}" if a and district(i, lang) not in a else (a or district(i, lang))


def beds(n, lang):
    if n is None:
        return ""
    if n == 0:
        return texts.pick(lang, "студия", "studio", "სტუდიო")
    return f"{n} {texts.pick(lang, 'спальня' if n == 1 else 'спальни', 'bedroom' if n == 1 else 'bedrooms', 'საძინებელი')}"


def flat_line(i, lang="ru"):
    """2 спальни · 78 м² · $850 · ❗️ эксклюзив"""
    d = i.get("details") or {}
    parts = [beds(i.get("bedrooms"), lang), i.get("area") and f"{i['area']:g} {texts.pick(lang, 'м²', 'm²', 'მ²')}",
             i.get("price_usd") and texts.money(i["price_usd"]),
             d.get("exclusive") and texts.pick(lang, "❗️ эксклюзив", "❗️ exclusive", "❗️ ექსკლუზივი")]
    return " · ".join(p for p in parts if p)


def headline(i, lang="ru"):
    """Ваке · 2 спальни · $850"""
    parts = [district(i, lang), beds(i.get("bedrooms"), lang), i.get("price_usd") and texts.money(i["price_usd"])]
    return " · ".join(p for p in parts if p)


def client_name(cl):
    return cl.get("name") or (cl.get("username") and "@" + cl["username"]) or f"#{cl.get('id', '?')}"


def staff_name(u):
    return (u or {}).get("name") or ((u or {}).get("username") and "@" + u["username"]) or "—"


def chat_of(cl):
    return cl.get("chat_id") or (cl.get("channel") == "tg" and cl.get("external_id")) or None


def now_hm():
    return datetime.now(timezone.utc).astimezone(texts.tz()).strftime("%H:%M")


SOURCE_RU = {"agency": "канал агентства", "object": "база объектов", "ss.ge": "ss.ge", "myhome.ge": "myhome.ge", "livo.ge": "livo.ge",
             "korter.ge": "korter.ge"}


def group_text(lead, timer=True):
    """B1: the lead card in the operators group."""
    cl, i, _ = ctx(lead)
    src = i.get("source") or ""
    src = SOURCE_RU.get(src) or (src.startswith("tg:") and f"канал {src[3:]}") or (src.startswith("fb:") and "Facebook") or (src.startswith("ig:") and f"Instagram {src[3:]}") or (src.startswith("th:") and f"Threads {src[3:]}") or src
    urls = i.get("urls") or []
    t = (f"🆕 <b>Заявка #{g(lead, 'id')}</b>" + (f"  ·  ⏱ {env_int('SLA_OPERATOR_TAKE_MIN', 5)} мин на назначение" if timer else "") +
         f"\n\n👤 <b>{e(client_name(cl))}</b> · Telegram" + (f" · {cl.get('lang', 'ru')}" if cl.get("lang") not in (None, "ru") else "") +
         "\n" + e(texts.profile_summary(cl.get("profile") or {}, "ru") or "анкета не заполнена"))
    if g(lead, "request"):
        t += f"\n💬 {e(g(lead, 'request'))}"
    if i:
        t += f"\n\n🏠 <b>Объект:</b> {e(address(i))}\n{e(flat_line(i))}\nИсточник: {e(src)}"
        if urls:
            t += f" · <a href=\"{e(urls[0])}\">ссылка</a>"
    return t


def group_kb(lead, stage=None):
    """The lead card for operators (their DMs, or the group; in practice the one connected operator account): «Написать клиенту» /
    «Написать агенту» (t.me links: need a @username), «Назначить агента» in the panel while there is none.
    stage is kept for old callers; the buttons follow the lead itself."""
    lid = g(lead, "id")

    def make(private):
        cl, ag = store.client(g(lead, "client_id")) or {}, store.user(g(lead, "agent_id")) or {}
        rows = []
        rows.append([url_btn("💬 Написать клиенту", cl.get("username") and links.tg_user(cl["username"])),
                     url_btn("💬 Написать агенту", ag.get("username") and links.tg_user(ag["username"]))])
        rows.append([app_btn("📋 Открыть в панели" if ag else "👤 Назначить агента", links.lead_app(lid), private)])
        return kb(rows)
    return make


def taken_line(lead):
    op = store.user(g(lead, "operator_id"))
    t = f"\n\n✋ <b>Взяла {e(staff_name(op))}</b>" if op else ""
    if ag := store.user(g(lead, "agent_id")):
        t += f"\n👤 Агент: <b>{e(staff_name(ag))}</b>"
    return t


@safe
def lead_new_group(lead):
    to_operators("group_lead", g(lead, "id"), group_text(lead), group_kb(lead, "new"))


@safe
def lead_taken_group(lead, operator):
    t = group_text(lead, timer=False) + f"\n\n✋ <b>Взяла {e(staff_name(operator if isinstance(operator, dict) else {'name': g(operator, 'name'), 'username': g(operator, 'username')}))}</b> · {now_hm()}"
    edit_refs("group_lead", g(lead, "id"), t, group_kb(lead, "taken"))


@safe
def lead_assigned_agent(lead):
    """C6 to the agent (no client contacts), and the group card shows the agent."""
    cl, i, _ = ctx(lead)
    lid, ag, op = g(lead, "id"), store.user(g(lead, "agent_id")), store.user(g(lead, "operator_id"))
    edit_refs("group_lead", lid, group_text(lead, timer=False) + taken_line(lead), group_kb(lead, "assigned"))
    if not ag or not ag.get("tg_id"):
        log.warning("lead %s: agent %s has no Telegram id", lid, g(lead, "agent_id"))
        return
    own = i.get("id") and store.q("SELECT 1 FROM properties WHERE listing_id = %s AND agent_id = %s", (i["id"], ag["id"]), one=True)
    t = (f"🆕 <b>Заявка #{lid}</b>" + (f" от оператора {e(staff_name(op))}" if op else " · ❤️ клиент выбрал эту квартиру") +
         f"\n⏱ {env_int('SLA_AGENT_SLOTS_MIN', 30)} мин, чтобы предложить время просмотра\n\n"
         f"🏠 <b>{e(address(i))}</b>\n{e(flat_line(i))}" + (" · ваш объект" if own else "") +
         f"\n\n{e(texts.profile_summary(cl.get('profile') or {}, 'ru') or 'анкета не заполнена')}\n\n"
         "🔒 Контакты клиента бот пришлёт сам, когда клиент выберет время\n"
         "Задача: узнайте у собственника свободное время и предложите 2-3 варианта.")
    if g(lead, "client_note") and g(lead, "status") in ("agent_assigned", "slots_sent"):
        t += f"\n\n🕐 <b>Клиент предложил своё время:</b> {e(g(lead, 'client_note'))}"
    if urls := i.get("urls"):
        t += "\n\n🔗 " + "\n".join(f'<a href="{e(u)}">{e(u)}</a>' for u in urls[:3])
    t += maps_line(i)
    rows = [[app_btn("🕐 Выбрать время", links.lead_app(lid))]]
    if g(lead, "status") == "agent_assigned":
        rows.append([cb("🏠 Уже сдана", f"lo:a:{lid}:gone")])
    m = tg.send(ag["tg_id"], t, kb(rows))
    if m:
        store.remember("agent_lead", lid, m["chat"]["id"], m["message_id"])


@safe
def object_gone_staff(lead):
    """The agent pressed «🏠 Уже сдана»: the lead's operator (else the operators) offers the client other options."""
    cl, i, _ = ctx(lead)
    lid, ag = g(lead, "id"), store.user(g(lead, "agent_id")) or {}
    t = (f"🏠 <b>Заявка #{lid}: квартира уже сдана</b> (агент {e(staff_name(ag))})\n{e(address(i))}\n\n"
         f"Объект закрыт как «Сдал сам». Предложите клиенту <b>{e(client_name(cl))}</b> другие варианты.")
    to_lead_staff(lead, t, lambda private: kb([[url_btn("💬 Написать клиенту", cl.get("username") and links.tg_user(cl["username"]))],
                                               [app_btn("📋 Открыть в панели", links.lead_app(lid), private)]]))


def maps_line(i):
    """«🗺 Google Карты» under an object's address (empty without coordinates)."""
    u = links.google_maps(i)
    return f'\n🗺 <a href="{e(u)}">Google Карты</a>' if u else ""


@safe
def client_own_time_agent(lead, own_text):
    """The client wrote their own time (A4a): the event first, then the task — not a repeat of the new-lead card."""
    _, i, _ = ctx(lead)
    lid, ag = g(lead, "id"), store.user(g(lead, "agent_id"))
    if not ag or not ag.get("tg_id"):
        return
    t = (f"🕐 <b>Заявка #{lid} · клиент предложил своё время</b>\n«{e(own_text)}»\n\n"
         "Что сделать: согласуйте это время с собственником и отправьте клиенту 1-2 варианта кнопкой ниже.\n\n"
         f"🏠 {e(address(i))}" + maps_line(i))
    tg.send(ag["tg_id"], t, kb([[app_btn("🕐 Предложить время", links.lead_app(lid))]]))


LIKED = {"ru": ("❤️ Нравится ✅", "➡️ Следующий вариант"), "en": ("❤️ I like it ✅", "➡️ Next option"),
         "ka": ("❤️ მომწონს ✅", "➡️ შემდეგი ვარიანტი")}


@safe
def lead_client_ack(lead):
    """A3: the card just marks «❤️ Нравится ✅» (+ «➡️ Следующий вариант»), no message: the tapped card, or (a lead
    made by the userbot from the ❤️ DM) the latest card of that listing."""
    cl, _, lang = ctx(lead)
    card = (cl.get("state") or {}).get("cards", {}).get(str(g(lead, "listing_id"))) or {}
    liked, nxt = LIKED[lang]
    refs = store.refs("card", f"{g(lead, 'client_id')}:{g(lead, 'listing_id')}")
    if tapped := tapped_card.get():
        refs = [tuple(tapped)]
    for chat, mid in refs[-1:]:
        tg.edit_markup(chat, mid, kb([[cb(liked, "noop")], [cb(nxt, "more")], *(card.get("rows") or [])]))


# the first message of a client who never wrote to the operator; userbot.LIKE_CODE reads the object code back from it
LIKE_DM = {"ru": "Здравствуйте! Мне нравится вариант #{obj} · {what}", "en": "Hello! I like option #{obj} · {what}",
           "ka": "გამარჯობა! მომწონს ვარიანტი #{obj} · {what}"}
# the same with the object's post in our channel: the operator opens the post, userbot.LIKE_POST finds the object by it
LIKE_DM_POST = {"ru": "Здравствуйте! Мне нравится этот вариант: {url}", "en": "Hello! I like this option: {url}",
                "ka": "გამარჯობა! მომწონს ეს ვარიანტი: {url}"}


def channel_post_of(listing_id):
    """Our channel post of this listing's object (properties.post_url, else an agency listing's own t.me url) or None.
    A catalog row (obj<property id>, services/catalog.py) is its object."""
    r = store.q("SELECT post_url FROM properties WHERE (listing_id = %s OR 'obj' || id = %s) AND "
                "NULLIF(BTRIM(post_url), '') IS NOT NULL ORDER BY id LIMIT 1", (listing_id, listing_id), one=True)
    if r:
        return r["post_url"]
    i = store.listing(listing_id) or {}
    urls = i.get("urls") or []
    urls = json.loads(urls) if isinstance(urls, str) else urls
    return next((u for u in urls if "t.me/" in u), None) if i.get("source") in ("agency", "object") else None


def operator_username():
    """Who the client writes to: OPERATOR_TG_USERNAME, else the first active operator with a @username."""
    u = conf("OPERATOR_TG_USERNAME").strip().lstrip("@")
    if not u:
        r = store.q("SELECT username FROM users WHERE is_active AND role = 'operator' AND COALESCE(username, '') != '' ORDER BY id LIMIT 1",
                    one=True)
        u = r and r["username"].lstrip("@")
    return u or None


def knows_operator(client_id):
    """The client has a lead or has written to the operator's account (the userbot's client event dm_to_operator)."""
    return (store.q("SELECT EXISTS (SELECT 1 FROM leads WHERE client_id = %s) OR EXISTS (SELECT 1 FROM events WHERE entity = 'client' "
                    "AND entity_id = %s AND action = 'dm_to_operator') AS k", (client_id, str(client_id)), one=True) or {}).get("k")


def like_button(cl, listing_id, label):
    """❤️ on a client card: always the bot's callback like:<id> — the lead is made at once, the client types nothing.
    The operator's account then writes first (operator_intro); only if it can't, the client gets operator_dm_link."""
    return cb(label, f"like:{listing_id}")


def operator_dm_link(cl, listing_id):
    """t.me/<operator>?text=«…этот вариант: <post>» (or «…вариант #<obj> · …»), or None without an operator @username:
    the client's one tap opens the operator's DM, and the userbot ties that first message to the lead."""
    u = operator_username()
    i = u and store.listing(listing_id)
    if not i:
        return None
    lang = texts.lang_of(cl.get("lang"))
    if (link := channel_post_of(listing_id)) and "t.me/c/" not in link:  # a private-channel post: the operator's #obj path
        return f"https://t.me/{u}?text=" + quote(LIKE_DM_POST[lang].format(url=link))
    prop = store.q("SELECT id FROM properties WHERE listing_id = %s ORDER BY id LIMIT 1", (listing_id,), one=True)
    what = " · ".join(x for x in (address(i, lang), i.get("price_usd") and texts.money(i["price_usd"])) if x)
    return f"https://t.me/{u}?text=" + quote(LIKE_DM[lang].format(obj=prop["id"] if prop else listing_id, what=what))


INTRO = {"ru": ("Здравствуйте! Я оператор Homes in Georgia 👋\nВы отметили: {what}\n"
                "Подключаю агента — он договорится с собственником о просмотре."),
         "en": ("Hello! I'm the Homes in Georgia operator 👋\nYou liked: {what}\n"
                "I'm connecting an agent who will arrange the viewing with the owner."),
         "ka": ("გამარჯობა! მე ვარ Homes in Georgia-ს ოპერატორი 👋\nთქვენ მოიწონეთ: {what}\n"
                "გაკავშირებთ აგენტთან — ის მესაკუთრესთან ნახვას შეგითანხმებთ.")}
MORE = {"ru": "и ещё {n}", "en": "and {n} more", "ka": "და კიდევ {n}"}


WRITE_FIRST = {"ru": ("Чтобы оператор прислал вам время просмотра, отправьте ему одно сообщение — оно уже готово 👇",
                      "💬 Написать оператору"),
               "en": ("So that the operator can send you viewing times, send them one message — it's ready 👇",
                      "💬 Message the operator"),
               "ka": ("რომ ოპერატორმა ნახვის დრო გამოგიგზავნოთ, მიწერეთ ერთი შეტყობინება — უკვე მზადაა 👇",
                      "💬 მიწერეთ ოპერატორს")}


INTRO_AGAIN_MIN = 10  # «💬 Contact» tapped again within this: no second greeting


@safe
def operator_intro(lead, always=False):
    """Right after the first ❤️ of a client the operator's account never talked to: the userbot writes first (it
    checks that this needs no Stars / Premium and that the client is reachable). If it can't, the bot gives the client
    the prefilled DM link instead -> 'userbot' | 'link' | None. always («💬 Contact» under a channel post, Stanislav
    11-23): every client gets it, known or not, naming this object; once per lead in INTRO_AGAIN_MIN minutes."""
    cl, i, lang = ctx(lead)
    lid = g(lead, "id")
    if always:
        if not store.q("INSERT INTO events (entity, entity_id, action, actor_label, data, at) SELECT 'lead', %s, 'operator_intro', "
                       "'bot', '{}'::jsonb, now() WHERE NOT EXISTS (SELECT 1 FROM events WHERE entity = 'lead' AND entity_id = %s "
                       f"AND action = 'operator_intro' AND at > now() - interval '{INTRO_AGAIN_MIN} minutes') RETURNING id",
                       (str(lid), str(lid)), one=True):
            return None
        ids = [lid]
    else:
        ids = [r["id"] for r in store.q("SELECT id FROM leads WHERE client_id = %s ORDER BY id", (g(lead, "client_id"),))]
        if ids and ids[0] < lid:  # two ❤️ at once both looked «first» (knows_operator): only the earliest lead writes
            return None
    # the client picked it in the bot: name it as the bot's card does, no links (Stanislav 11-16; the street is agent-only)
    what = headline(i, lang) + (f" ({MORE[lang].format(n=len(ids) - 1)})" if len(ids) > 1 else "")
    from app.telegram import userbot
    if userbot.send_to_client(cl, INTRO[lang].format(what=what)):
        return "userbot"
    url, chat = operator_dm_link(cl, g(lead, "listing_id")), chat_of(cl)
    if url and chat:
        text, label = WRITE_FIRST[lang]
        tg.send(chat, text, kb([[url_btn(label, url)]]))
        return "link"
    return None


def _bot_core():
    """The client bot's card builders (bot/core.py), so a pushed object looks exactly like a search result."""
    import sys
    if bot_dir() not in sys.path:
        sys.path.append(bot_dir())
    import core
    return core


@safe
def object_to_client(cl, listing_id):
    """Stage 10, owner-first: an object the agent fast-tracked, sent like any search result — the album, then the
    channel-style card with ❤️ / 👎 / 📍 (callbacks like:<id> / no:<id>, so the usual like → lead flow follows)."""
    i = store.listing(listing_id)
    chat = chat_of(cl or {})
    if not i or not chat:
        return None
    lang = texts.lang_of(cl.get("lang"))
    core = _bot_core()
    photos = [x for x in (p if os.path.isabs(p) else os.path.join(bot_dir(), p) for p in i.get("photos") or []) if os.path.exists(x)]
    if photos:
        send_group(chat, None, [("photo", x) for x in photos[:core.ALBUM_PHOTOS]])
    from app.db import SessionLocal
    from app.services import site_feed
    with SessionLocal() as db:
        i["site_pid"] = site_feed.site_pid(db, listing_id)  # «📍 На карте»: the object on our site's map
    markup = core.card_keyboard(i, lang, like=like_button(cl, i["id"], core.t(lang, "like")))
    return tg.send(chat, core.card(i, None, lang=lang), markup, html=False)


SLOTS = {"ru": ("Собственник готов показать квартиру 🏡\n<b>Выберите удобное время:</b>", "🕐 Другое время", "Открыть объявление"),
         "en": ("The owner is ready to show the flat 🏡\n<b>Pick a convenient time:</b>", "🕐 Another time", "Open the listing"),
         "ka": ("მესაკუთრე მზადაა ბინის საჩვენებლად 🏡\n<b>აირჩიეთ მოსახერხებელი დრო:</b>", "🕐 სხვა დრო", "განცხადების გახსნა")}
USERBOT_OFF = "Userbot оператора не подключён"
USERBOT_FAILED = "Userbot оператора не смог отправить сообщение клиенту"


def listing_link(i):
    """«Открыть объявление»: only our own channel's post (never a competitor source)."""
    u = next((u for u in i.get("urls") or [] if i.get("source") in ("agency", "object") and u.startswith("https://t.me/")), None)
    return u


def first_photo(i):
    return next((p for p in i.get("photos") or [] if os.path.exists(p) or os.path.exists(os.path.join(bot_dir(), p))), None)


def bot_dir():
    return str(store.ROOT / "bot")


def plain(template, **kw):
    """An HTML template of this module as plain text for the userbot (it sends without parse mode)."""
    return re.sub(r"</?b>", "", template).format(**kw)


def plain_html(text):
    """A card's HTML as plain text (the userbot's numbered fallback)."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


# ---------- client cards from the operator's account ----------
# Beyond the listing cards nothing goes to the client through the bot. The userbot sends the bot's inline result
# «<kind>:<token>» into the client's DM (the bot answers card(kind, token) only for the userbot's account, journey.
# on_inline_query); presses come to the bot with inline_message_id (journey.on_client_journey). The inline query failing
# -> the userbot sends the same text with numbered options (clients.state.dm_choice, userbot.on_incoming).

def new_card(kind, ref):
    """A fresh token for «<kind>:<token>»; an older card of the same kind and ref stops resolving."""
    token = secrets.token_urlsafe(9)
    store.q("DELETE FROM inline_cards WHERE kind = %s AND ref = %s", (kind, str(ref)))
    store.q("INSERT INTO inline_cards (token, kind, ref) VALUES (%s, %s, %s)", (token, kind, str(ref)))
    return token


def card(kind, token):
    """The bot's inline answer for «<kind>:<token>» -> (title, HTML text, button rows) or None."""
    r = kind in CARDS and store.q("SELECT ref FROM inline_cards WHERE token = %s AND kind = %s", (token, kind), one=True)
    return CARDS[kind](int(r["ref"])) if r else None


def _slots_card(lead_id):
    """A4a: the object, a button per open slot (ls:pick:<slot>) and «Другое время» (ls:own:<lead>)."""
    lead = store.q("SELECT * FROM leads WHERE id = %s AND status = 'slots_sent'", (lead_id,), one=True)
    slots = lead and store.q("SELECT id, starts_at FROM lead_slots WHERE lead_id = %s AND NOT chosen ORDER BY starts_at", (lead_id,))
    if not slots:
        return None
    _, i, lang = ctx(lead)
    ask, own, open_ = SLOTS[lang]
    rows = [[url_btn(open_, listing_link(i))]] + [[cb(texts.slot_label(s["starts_at"], lang), f"ls:pick:{s['id']}")] for s in slots]
    rows.append([cb(own, f"ls:own:{lead_id}")])
    text = f"<b>{e(headline(i, lang))}</b>\n📍 {e(address(i, lang))}\n\n{ask}"
    return headline(i, lang) or plain(ask).splitlines()[-1], text, kb(rows)["inline_keyboard"]


def _remind_card(lead_id):
    """An hour before the viewing: «Буду» / «Опаздываю» / «Перенести» (lv:)."""
    lead = store.q("SELECT * FROM leads WHERE id = %s AND status = 'time_chosen'", (lead_id,), one=True)
    if not lead:
        return None
    _, i, lang = ctx(lead)
    hm = texts.local(lead["viewing_at"]).strftime("%H:%M") if lead["viewing_at"] else "—"
    text = REMIND[lang][0].format(hm=hm, addr=e(address(i, lang)), agent=e(staff_name(store.user(lead["agent_id"]))))
    return plain_html(text).splitlines()[0], text, viewing_rows(lead_id, lang, ok=True)


def viewing_rows(lead_id, lang, ok=False):
    """[«Буду»,] «Опаздываю» / «Перенести»: the reminder and the slot card after the choice."""
    _, yes, late, resched = REMIND[lang]
    return kb([[cb(yes, f"lv:ok:{lead_id}") if ok else None, cb(late, f"lv:late:{lead_id}")],
               [cb(resched, f"lv:resched:{lead_id}")]])["inline_keyboard"]


def _survey_card(lead_id):
    """A5a: «Снимаю» / «Не подошло» / «Думаю» (lo:c:)."""
    lead = store.lead(lead_id)
    if not lead:
        return None
    cl, i, lang = ctx(lead)
    t, yes, no, think = SURVEY[lang]
    text = t.format(name=e(cl.get("name") or texts.pick(lang, "Здравствуйте", "Hi", "გამარჯობა")), where=e(district(i, lang)))
    rows = kb([[cb(yes, f"lo:c:{lead_id}:rented")], [cb(no, f"lo:c:{lead_id}:refused")], [cb(think, f"lo:c:{lead_id}:thinking")]])
    return plain_html(text), text, rows["inline_keyboard"]


def _reason_card(lead_id):
    """After «Не подошло»: price / location / condition / other (lr:)."""
    lead = store.lead(lead_id)
    if not lead:
        return None
    _, _, lang = ctx(lead)
    r = {k: v[lang] for k, v in texts.REFUSAL.items()}
    rows = kb([[cb(r["price"], f"lr:{lead_id}:price"), cb(r["location"], f"lr:{lead_id}:location")],
               [cb(r["condition"], f"lr:{lead_id}:condition"), cb(r["other"], f"lr:{lead_id}:other")]])
    return REASON_ASK[lang], REASON_ASK[lang], rows["inline_keyboard"]


def _t14_card(deal_id):
    """A6: the intro + question 1 (t14:<deal>:1:<0-3>); the bot edits the card to the next questions."""
    ld = store.q("SELECT l.* FROM deals d JOIN leads l ON l.id = d.lead_id WHERE d.id = %s", (deal_id,), one=True)
    if not ld:
        return None
    cl, _, lang = ctx(ld)
    intro = T14_INTRO[lang].format(name=e(cl.get("name") or texts.pick(lang, "Здравствуйте", "Hi", "გამარჯობა")))
    q, rows = t14_question(deal_id, 1, lang)
    return plain_html(intro).splitlines()[0], f"{intro}\n\n{q}", rows["inline_keyboard"]


CARD_WHAT = {"slots": "время просмотра", "remind": "напоминание о просмотре", "survey": "опрос после просмотра",
             "reason": "вопрос о причине отказа", "t14": "опрос T14"}


def client_card(kind, ref, lead, fallback=None, choice=None):
    """The card «kind» (ref: lead id, t14: deal id) to the lead's client from the operator's account. Numbered fallback:
    the card's callback buttons (not «Другое время») as options, unless `fallback` text/`choice` are given.
    -> None when the userbot sent it, else the error text (USERBOT_OFF / USERBOT_FAILED)."""
    if not conf("USERBOT_URL").strip():
        return USERBOT_OFF
    built = CARDS[kind](ref)
    if not built:
        return USERBOT_FAILED
    _, text, rows = built
    btns = [b for r in rows for b in r if b.get("callback_data") and not b["callback_data"].startswith("ls:own")]
    cl = store.client(g(lead, "client_id")) or {}
    ok = userbot.send_to_client(cl, fallback or plain_html(text), None if fallback else [b["text"] for b in btns],
                                choice or {"lead": g(lead, "id"), "kind": kind, "options": [b["callback_data"] for b in btns]},
                                inline=f"{kind}:{new_card(kind, ref)}")
    return None if ok else USERBOT_FAILED


def card_to_client(kind, ref, lead, **kw):
    """client_card for the worker's jobs and the client's own answers: not delivered -> an event in the lead history."""
    err = client_card(kind, ref, lead, **kw)
    if err:
        store.q("INSERT INTO events (entity, entity_id, action, actor_label, data, at) VALUES ('lead', %s, 'client_card_not_delivered', "
                "'bot', %s::jsonb, now())", (str(g(lead, "id")), json.dumps({"kind": kind, "note": f"{CARD_WHAT[kind]}: {err}"},
                                                                            ensure_ascii=False)))
        log.warning("lead %s: %s not delivered: %s", g(lead, "id"), kind, err)
    return err


@safe
def slots_to_client(lead):
    """A4a: the slot card -> None when sent, else the error text for the agent (add_slots logs it)."""
    return client_card("slots", g(lead, "id"), lead)


CONFIRMED = {"ru": "✅ <b>Просмотр: {when}</b>\n📍 {addr}\n\n👤 Ваш агент: <b>{agent}</b>{contact}",
             "en": "✅ <b>Viewing: {when}</b>\n📍 {addr}\n\n👤 Your agent: <b>{agent}</b>{contact}",
             "ka": "✅ <b>ნახვა: {when}</b>\n📍 {addr}\n\n👤 თქვენი აგენტი: <b>{agent}</b>{contact}"}


def intl_phone(p):
    """«+995 555 00 05 88» / «555000588» -> «+995555000588» (Telegram makes it tappable), '' without digits."""
    d = re.sub(r"\D", "", p or "")
    return d and "+" + ("995" + d if len(d) == 9 else d)


@safe
def slot_confirmed_client(lead, agent):
    """A4b as a plain DM from the operator's account (userbot): time, address, the agent and a working contact: his
    t.me link, else his phone as +995…. The bot itself only edits the slot message to «✅ Вы выбрали: …»."""
    cl, i, lang = ctx(lead)
    lid, at = g(lead, "id"), g(lead, "viewing_at")
    ag = agent if isinstance(agent, dict) else {k: g(agent, k) for k in ("id", "name", "username", "phone", "tg_id")}
    contact = (f"\n💬 {links.tg_user(ag['username'])}" if ag.get("username") else
               f"\n📞 {phone}" if (phone := intl_phone(ag.get("phone"))) else "")
    text = plain(CONFIRMED[lang], when=texts.when_long(at, lang) if at else "—", addr=address(i, lang), agent=staff_name(ag),
                 contact=contact)
    if not userbot.send_to_client(cl, text):
        log.warning("lead %s: the viewing details were not delivered (userbot)", lid)


@safe
def contacts_to_agent(lead):
    """C8: right after the client picked a time — the time and the client's contacts."""
    cl, i, _ = ctx(lead)
    ag = store.user(g(lead, "agent_id"))
    if not ag or not ag.get("tg_id"):
        return
    lid, at = g(lead, "id"), g(lead, "viewing_at")
    contact = links.tg_user(cl.get("username"), cl.get("external_id") if cl.get("channel") == "tg" else None)
    t = (f"✅ <b>Клиент выбрал: {e(texts.when_long(at)) if at else '—'}</b>\nЗаявка #{lid} · {e(address(i))}\n\n"
         f"🔓 <b>Контакты клиента</b>\n\n👤 {e(cl.get('name') or '—')}" + (f"\n📞 {e(cl['phone'])}" if cl.get("phone") else "") +
         (f"\n💬 @{e(cl['username'])}" if cl.get("username") else "") +
         "\n\nНапишите клиенту до встречи. Напомню вам и клиенту за 1 час." + maps_line(i))
    tg.send(ag["tg_id"], t, kb([[url_btn("💬 Написать", contact)], [app_btn("📍 Адрес объекта", links.map_pin(i))]]))


VIEWING_UPDATE = {"ok": "👍 Клиент {name} подтвердил просмотр #{id}: «Буду»", "late": "⏰ Клиент {name} опаздывает на просмотр #{id}"}


@safe
def viewing_update(lead, what):
    """Client's «Буду» / «Опаздываю» -> the agent at once (A4b: «агент и оператор видят ответ сразу»)."""
    cl, _, _ = ctx(lead)
    ag = store.user(g(lead, "agent_id")) or {}
    if ag.get("tg_id") and what in VIEWING_UPDATE:
        tg.send(ag["tg_id"], VIEWING_UPDATE[what].format(name=e(client_name(cl)), id=g(lead, "id")))


REMIND = {"ru": ("⏰ Через час просмотр: <b>{hm}</b>, {addr}\nАгент {agent}", "👍 Буду", "⏰ Опаздываю", "🔁 Перенести"),
          "en": ("⏰ Viewing in an hour: <b>{hm}</b>, {addr}\nAgent {agent}", "👍 I'll be there", "⏰ Running late", "🔁 Reschedule"),
          "ka": ("⏰ ერთ საათში ნახვაა: <b>{hm}</b>, {addr}\nაგენტი {agent}", "👍 მოვალ", "⏰ მაგვიანდება", "🔁 გადატანა")}


@safe
def viewing_reminder(lead):
    cl, i, _ = ctx(lead)
    lid, at = g(lead, "id"), g(lead, "viewing_at")
    hm = texts.local(at).strftime("%H:%M") if at else "—"
    ag = store.user(g(lead, "agent_id")) or {}
    card_to_client("remind", lid, lead)                      # the client: from the operator's account
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"⏰ Через час просмотр #{lid}: <b>{hm}</b>, {e(address(i))}\nКлиент {e(client_name(cl))}"
                + (f", 📞 {e(cl['phone'])}" if cl.get("phone") else ""))


SURVEY = {"ru": ("{name}, как прошёл просмотр квартиры в {where}?", "Снимаю ✅", "Не подошло ❌", "Думаю 🤔"),
          "en": ("{name}, how was the viewing of the flat in {where}?", "I'm renting it ✅", "Not for me ❌", "Thinking 🤔"),
          "ka": ("{name}, როგორ ჩაიარა ბინის ნახვამ ({where})?", "ვქირაობ ✅", "არ მომერგო ❌", "ვფიქრობ 🤔")}


@safe
def post_viewing_survey(lead):
    """A5a to the client (the survey card from the operator's account, lo:c:) and C9a to the agent (lo:a:)."""
    cl, i, _ = ctx(lead)
    lid = g(lead, "id")
    card_to_client("survey", lid, lead)
    ag = store.user(g(lead, "agent_id")) or {}
    if ag.get("tg_id"):
        m = tg.send(ag["tg_id"], f"Как прошёл просмотр #{lid}?\n{e(address(i))} · клиент {e(client_name(cl))}",
                    kb([[cb("Сдано ✅", f"lo:a:{lid}:rented"), cb("Не подошло ❌", f"lo:a:{lid}:refused")],
                        [cb("Думает 🤔", f"lo:a:{lid}:thinking"), cb("Перенос 🔁", f"lo:a:{lid}:reschedule")]]))
        if m:
            store.remember("survey_agent", lid, m["chat"]["id"], m["message_id"])


REASON_ASK = {"ru": "Жаль 😔 Что не подошло?", "en": "Sorry to hear that 😔 What didn't fit?", "ka": "სამწუხაროა 😔 რა არ მოგეწონათ?"}


@safe
def refusal_reason_ask(lead):
    """The reason card from the operator's account (lr:)."""
    card_to_client("reason", g(lead, "id"), lead)


STAGE_RU = {"operator_take": "назначение агента", "agent_slots": "время от агента", "slots_to_client": "передачу времени клиенту"}


def _sla_head(lead, icon):
    cl, i, _ = ctx(lead)
    return (f"{icon} <b>Заявка #{g(lead, 'id')}</b> · {e(client_name(cl))}\n", e(" · ".join(x for x in (
        district(i), i.get("price_usd") and texts.money(i["price_usd"]), texts.profile_summary(cl.get("profile") or {}, "ru", one_line=True)) if x)))


def _minutes_left(lead):
    due = g(lead, "sla_due_at")
    if not due:
        return None
    return max(0, round((texts.local(due) - datetime.now(timezone.utc)).total_seconds() / 60))


@safe
def sla_warning(lead, stage):
    """B4: the window is running out — a reminder to whoever has to act."""
    lid, left = g(lead, "id"), _minutes_left(lead)
    head, info = _sla_head(lead, "⚠️")
    left_t = f"Осталось <b>{left} мин</b>" if left is not None else "Скоро истекает срок"
    if stage == "agent_slots":
        ag = store.user(g(lead, "agent_id")) or {}
        if ag.get("tg_id"):
            tg.send(ag["tg_id"], f"{head}{left_t}, чтобы предложить время просмотра.\n{info}",
                    kb([[app_btn("🕐 Предложить время", links.lead_app(lid))]]))
        return
    to_operators(None, lid, f"{head}{left_t} на {STAGE_RU.get(stage, stage)}.\n{info}",
                 lambda private: kb([[app_btn("👤 Назначить агента" if stage == "operator_take" else "📋 Открыть в панели",
                                              links.lead_app(lid), private)]]))


@safe
def sla_overdue(lead, stage):
    """B4: the window is over — escalation to the operators group and the superadmins."""
    lid = g(lead, "id")
    head, info = _sla_head(lead, "⏰")
    if stage == "agent_slots":
        ag = store.user(g(lead, "agent_id")) or {}
        body = f"Агент <b>{e(staff_name(ag))}</b> {env_int('SLA_AGENT_SLOTS_MIN', 30)} минут не предлагает время просмотра.\n\nПереназначить заявку?"
        rows = [cb("🔁 Переназначить", f"lg:assign:{lid}"), url_btn(f"💬 Написать {staff_name(ag)}", links.tg_user(ag.get("username"), ag.get("tg_id")))]
    elif stage == "operator_take":
        body, rows = f"Агент не назначен за {env_int('SLA_OPERATOR_TAKE_MIN', 5)} минут.", []
    else:
        body, rows = "Время от агента пришло, клиенту ещё не отправлено.", []
    text = f"{head}{body}\n{info}"
    to_operators(None, lid, text, lambda private: kb([rows, [app_btn("📋 Открыть в панели", links.lead_app(lid), private)]]))
    for sa in superadmins():
        tg.send(sa, text, kb([[app_btn("📋 Открыть в панели", links.lead_app(lid))]]))


def deal_lead(deal):
    return store.lead(g(deal, "lead_id")) or {}


def fmt_date(d):
    if not d:
        return "—"
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    return d.strftime("%d.%m.%Y")


@safe
def deal_created(deal):
    """A deal written by an agent / a recruit waits for the operator's confirmation (David 11-23: «уведомления не
    пришло оператору»)."""
    ag = store.user(g(deal, "agent_id")) or {}
    did = g(deal, "id")
    t = (f"🤝 <b>Новая сделка на подтверждение</b> #{did}\n\n"
         f"🏠 {html.escape(g(deal, 'address') or '—')}\n👤 Агент: {html.escape(ag.get('name') or '—')}\n"
         f"💵 Цена: <b>{texts.money(g(deal, 'price'))}</b> · комиссия {texts.money(g(deal, 'commission_total'))}\n"
         f"🙋 Клиент: {html.escape(g(deal, 'client_name') or '—')}")
    to_operators(None, did, t, lambda private: kb([[app_btn("📋 Открыть сделку", links.deal_app(did), private)]]))


@safe
def deal_confirmed(deal):
    """C9c to the agent. Everything else lives in the CRM: no #Rented to a work group (Stanislav 2026-11-08)."""
    ag = store.user(g(deal, "agent_id")) or {}
    if ag.get("tg_id"):
        t = ("✅ <b>Сделка подтверждена</b>\n\n"
             f"💼 Ваша доля: <b>{texts.money(g(deal, 'agent_amount'))}</b>\n📊 Эксклюзивных сделок: {ag.get('exclusive_deals') or 0}")
        if g(deal, "rent_from"):
            t += f"\n\n🗓 T14 напомню <b>{fmt_date(g(deal, 't14_date'))[:5] if g(deal, 't14_date') else '—'}</b>"
        if g(deal, "rent_to"):
            t += f"\n🔔 Конец договора {fmt_date(g(deal, 'rent_to'))}: напомню за 30, 7 и 1 день"
        tg.send(ag["tg_id"], t, kb([[app_btn("📝 T0: оценить сделку", links.deal_app(g(deal, "id")))]]))


@safe
def level_changed(user_id, level):
    """11-23 G: an agent's level changed (a confirmed / cancelled exclusive deal, or an admin's edit)."""
    u = store.user(user_id) or {}
    if u.get("tg_id") and u.get("is_active", True):
        tg.send(u["tg_id"], f"📊 <b>Ваш уровень теперь {level}%</b>\nЭксклюзивных сделок: {u.get('exclusive_deals') or 0}")


T14_INTRO = {"ru": "{name}, вы живёте в новой квартире 14 дней 🏡\nВсё ли хорошо? Оцените, пожалуйста, три пункта (это 10 секунд):",
             "en": "{name}, you've been living in your new flat for 14 days 🏡\nIs everything fine? Please rate three points (10 seconds):",
             "ka": "{name}, ახალ ბინაში უკვე 14 დღეა ცხოვრობთ 🏡\nყველაფერი რიგზეა? გთხოვთ, შეაფასოთ სამი პუნქტი (10 წამი):"}


def t14_question(deal_id, n, lang):
    """Question n (1-3) with 0-3 buttons (t14:<deal>:<n>:<score>)."""
    return (f"<b>{n}.</b> {texts.T14_QUESTIONS[lang][n - 1]}",
            kb([[cb(str(s), f"t14:{deal_id}:{n}:{s}") for s in range(4)]]))


T14_PLAIN = {"ru": "Ответьте тремя цифрами от 0 до 3 по порядку, например «3 2 3».",
             "en": "Reply with three digits from 0 to 3 in order, e.g. «3 2 3».",
             "ka": "უპასუხეთ სამი ციფრით 0-დან 3-მდე თანმიმდევრობით, მაგალითად „3 2 3“."}


@safe
def t14_client(deal):
    """A6: the T14 card from the operator's account (intro + question 1; the bot edits it to the next ones). Numbered
    fallback: all three questions, the client answers «3 2 3» (userbot.on_incoming)."""
    ld = deal_lead(deal)
    if not ld:
        return
    cl = store.client(ld.get("client_id")) or {}
    lang = texts.lang_of(cl.get("lang"))
    fallback = (plain(T14_INTRO[lang], name=cl.get("name") or texts.pick(lang, "Здравствуйте", "Hi", "გამარჯობა")) + "\n\n" +
                "\n".join(f"{n}. {q}" for n, q in enumerate(texts.T14_QUESTIONS[lang], 1)) + "\n\n" + T14_PLAIN[lang])
    card_to_client("t14", g(deal, "id"), ld, fallback=fallback, choice={"lead": ld["id"], "kind": "t14", "deal": g(deal, "id")})


@safe
def t14_agent(deal):
    ag = store.user(g(deal, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"🗓 <b>T14 по сделке #{g(deal, 'id')}</b>\n{e(g(deal, 'address') or '')}\nКлиент живёт 14 дней: оцените сделку.",
                kb([[app_btn("📝 T14: оценить", links.deal_app(g(deal, "id")))]]))


@safe
def contract_expiry(deal, days):
    ag = store.user(g(deal, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"🔔 Через <b>{days} дн.</b> заканчивается договор: {e(g(deal, 'address') or '')} "
                f"(до {fmt_date(g(deal, 'rent_to'))}).\nКлиент: {e(g(deal, 'client_name') or '—')}",
                kb([[app_btn("📄 Открыть сделку", links.deal_app(g(deal, "id")))]]))


@safe
def property_lease_expiry(prop, days):
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id") and g(prop, "archive_reason") == "rented_by_owner":  # «Собственник сдал»: the flat frees up
        addr = g(prop, "address") or f"#{g(prop, 'id')}"
        tg.send(ag["tg_id"], f"🔔 Квартира {e(addr)} освобождается "
                f"<b>{fmt_date(g(prop, 'rent_to'))}</b> — позвоните собственнику и выложите заново.",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))
    elif ag.get("tg_id"):
        tenant = " ".join(str(x) for x in (g(prop, "tenant_name"), g(prop, "tenant_phone"), g(prop, "tenant_tg")) if x) or "—"
        tg.send(ag["tg_id"], f"🔔 Через <b>{days} дн.</b> заканчивается аренда: {e(g(prop, 'address') or '')} "
                f"(до {fmt_date(g(prop, 'rent_to'))}).\nАрендатор: {e(tenant)}",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))


def prop_title(prop):
    return " · ".join(str(x) for x in (g(prop, "address") or g(prop, "district"), g(prop, "price") and texts.money(g(prop, "price"))) if x)


@safe
def property_call_retry(prop):
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"📞 Пора перезвонить собственнику: <b>{e(prop_title(prop))}</b> (объект #{g(prop, 'id')})",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))


@safe
def property_idle_warning(prop, minutes):
    ag = store.user(g(prop, "agent_id")) or {}
    left = f"{minutes // 60} ч" if minutes % 60 == 0 else f"{minutes} минут"
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"⏳ Через {left} объект вернётся в «Новые объявления», если не взять его в работу "
                             f"(собственник подтвердил или думает): "
                             f"<b>{e(prop_title(prop))}</b> (объект #{g(prop, 'id')})",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))


@safe
def property_idle_returned(prop, agent_id, minutes):
    ag = store.user(agent_id) or {}
    span = "суток" if minutes == 1440 else f"{minutes // 60} ч" if minutes % 60 == 0 else f"{minutes} мин"
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"↩️ Объект возвращён: за {span} не взят в работу: "
                             f"<b>{e(prop_title(prop))}</b> (объект #{g(prop, 'id')}). Его снова может взять любой агент.")


@safe
def owner_transferred(agent_id, owner_name, by_name, props, reason=None):
    """«Передать собственника агенту…» (David 11-22: Arthur got #8462 and never knew): the new agent gets the owner and a
    button per object; an archived one says how to bring it back."""
    ag = store.user(agent_id) or {}
    if not ag.get("tg_id"):
        return
    lines = [f"👤 {e(by_name or 'Оператор')} передал вам собственника <b>{e(owner_name or '—')}</b>"
             + (f" ({e(reason)})" if reason else "") + "."]
    for p in props[:10]:
        st = "в архиве — откройте и нажмите «Снова в работу»" if p["status"] == "archived" else "в работе"
        lines.append(f"• #{p['id']} {e(p['address'] or '')} — {st}")
    tg.send(ag["tg_id"], "\n".join(lines),
            kb([[app_btn(f"🏠 Объект #{p['id']}", links.property_app(p["id"]))] for p in props[:5]]) if props else None)


@safe
def owner_transferred_away(agent_id, owner_name, to_name, by_name, props):
    """The previous agent of a transferred owner (11-23 inventory G): his objects went along."""
    ag = store.user(agent_id) or {}
    if not ag.get("tg_id"):
        return
    ids = ", ".join(f"#{p['id']}" for p in props[:10])
    tg.send(ag["tg_id"], f"👤 {e(by_name or 'Оператор')} передал собственника <b>{e(owner_name or '—')}</b> агенту {e(to_name)}"
            + (f"\nОбъекты: {ids}" if ids else ""),
            kb([[app_btn(f"🏠 Объект #{p['id']}", links.property_app(p["id"]))] for p in props[:5]]) if props else None)


@safe
def property_owner_followup(prop):
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"🤔 Собственник обещал подумать — пора перезвонить: <b>{e(prop_title(prop))}</b> (объект #{g(prop, 'id')})",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))


@safe
def property_review_request(prop):
    ag = store.user(g(prop, "agent_id")) or {}
    pid = g(prop, "id")
    to_operators("property_review", pid, f"🕵️ <b>Объект #{pid} на проверке</b>\n{e(prop_title(prop))}\nАгент: {e(staff_name(ag))}",
                 lambda private: kb([[app_btn("🔍 Проверить", links.property_app(pid), private)]]))


@safe
def property_returned(prop):
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"↩️ <b>Объект #{g(prop, 'id')} вернули на доработку</b>\n{e(prop_title(prop))}\n\n"
                f"Комментарий оператора: {e(g(prop, 'review_comment') or '—')}",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))


@safe
def exclusive_conflict(prop, source, url):
    """Stage 4: the agent's exclusive showed up on another site → a task to get the owner to take the ad down."""
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"🔴 <b>Эксклюзив #{g(prop, 'id')} висит на {e(source)}</b>\n{e(prop_title(prop))}\n\n"
                f"Задача: попросить собственника снять объявление {e(url or '')}\n"
                "Публикация как эксклюзив закрыта, пока оператор не отметит «Проверено: снято».",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))


@safe
def exclusive_duplicates(prop, n, strong):
    """Irakli 11-18: the duplicate search of an exclusive found strong candidates (a shared photo, the owner's phone,
    the cadastral code) → the object's agent and the operators."""
    pid = g(prop, "id")
    def line(s):
        head = " ".join(x for x in (s.get("where"), f"#{s['property_id']}" if s.get("property_id") else None,
                                    f"· {s['address']}" if s.get("address") else None) if x)
        return f"• {e(head)}: {e(', '.join(s.get('reasons') or []))}"
    lines = "\n".join(line(s) for s in strong[:5])
    more = f"\n…и ещё {len(strong) - 5}" if len(strong) > 5 else ""
    text = (f"🔎 <b>Эксклюзив #{pid}: найдены возможные дубли — {n}</b>\n{e(prop_title(prop))}\n\n{lines}{more}\n\n"
            "Проверьте в карточке → «Похожие объекты»: дубль или нет.")
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], text, kb([[app_btn("🏠 Открыть объект", links.property_app(pid))]]))
    to_operators("exclusive_duplicates", pid, text, lambda private: kb([[app_btn("🏠 Открыть объект", links.property_app(pid), private)]]))


@safe
def object_copies(prop, copies):
    """David 11-23: the duplicate search found new ads of the object elsewhere → its agent, one message per scan
    (only the ones not reported before). The card's «Где ещё выложен» has «Попросить снять» on each."""
    pid = g(prop, "id")
    ag = store.user(g(prop, "agent_id")) or {}
    if not ag.get("tg_id"):
        return
    def line(c):
        pct = f" · {round(c['pct'])}%" if c.get("pct") is not None else ""
        return f"• {e(c.get('where') or 'другой сайт')}{pct}" + (f"\n{e(c['url'])}" if c.get("url") else "")
    more = f"\n…и ещё {len(copies) - 10}" if len(copies) > 10 else ""
    text = (f"📍 <b>Объект #{pid} выложен ещё где-то: {len(copies)}</b>\n{e(prop_title(prop))}\n\n"
            + "\n".join(line(c) for c in copies[:10]) + more
            + "\n\nВ карточке → «Где ещё выложен»: «Попросить снять» или «Не дубль».")
    tg.send(ag["tg_id"], text, kb([[app_btn("🏠 Открыть объект", links.property_app(pid))]]))


CHDUP_DONE = {"deleted": "🗑 Новый пост удалён, объект #{drop} объединён с #{keep}", "kept": "✅ Отмечено: не дубль",
              "gone": "Один из постов уже снят — ничего не делаю"}


def channel_duplicate_text(keep, drop, pct, why, done=None, who=None):
    def line(p, label):
        return f"{label} #{g(p, 'id')} · {e(prop_title(p))}\n{e(g(p, 'post_url') or '')}"
    head = f"♊️ <b>Дубль в канале{f' · {pct}%' if pct else ''}</b>\n{e(why or '')}\n\n" \
           f"{line(keep, 'Старый пост')}\n\n{line(drop, 'Новый пост')}"
    if done:
        return f"{head}\n\n<b>{e(CHDUP_DONE[done].format(keep=g(keep, 'id'), drop=g(drop, 'id')))}</b>" + (f" · {e(who)}" if who else "")
    return f"{head}\n\nУдалить новый пост? Объект уйдёт в архив как дубль старого: заявки и файлы перейдут к нему."


@safe
def channel_duplicate(keep, drop, pct, why):
    """David 11-20: the same flat posted twice in the channel (services/channel_dups.py) → the operator confirms."""
    k, d = g(keep, "id"), g(drop, "id")
    to_operators("channel_dup", f"{k}:{d}", channel_duplicate_text(keep, drop, pct, why),
                 lambda private: kb([[cb("🗑 Удалить новый пост", f"chdup:{k}:{d}:1"), cb("Не дубль", f"chdup:{k}:{d}:0")],
                                     [app_btn("🏠 Новый объект", links.property_app(d), private)]]))


DUPS_STATE = {"clear": "эксклюзив подтверждён: дублей нет", "waiting": "ожидается устранение дублей",
              "review": "сомнительные совпадения — ручная проверка", "operator": "дубли остались — решение оператора"}
DUPS_LEVEL = {"confirmed": "дубль", "probable": "вероятный дубль", "doubtful": "сомнительно"}


@safe
def exclusive_dups_gate(prop, state, items, deadline):
    """§12 (David 11-19): the duplicates gate of an exclusive without the e-signature (similar.no_esign_gate) → the agent;
    the operators too unless it passed by itself."""
    pid = g(prop, "id")
    def line(x):
        pct = f" · {round(x['pct'] * 100)}%" if x.get("pct") is not None else ""
        head = " ".join(v for v in (x.get("where"), f"#{x['property_id']}" if x.get("property_id") else None) if v)
        link = f' · <a href="{e(x["url"])}">ссылка</a>' if x.get("url") else ""
        return f"• {e(head)} — {DUPS_LEVEL.get(x.get('level'), '')}{pct}{link}: {e(', '.join(x.get('reasons') or []))}"
    text = f"🔎 <b>Эксклюзив #{pid} без электронной подписи: {DUPS_STATE[state]}</b>\n{e(prop_title(prop))}"
    if items:
        text += "\n\n" + "\n".join(line(x) for x in items[:8]) + (f"\n…и ещё {len(items) - 8}" if len(items) > 8 else "")
    if state == "waiting":
        until = texts.local(deadline).strftime("%d.%m %H:%M") if deadline else "—"
        text += (f"\n\nДо {until} (24 ч): подписанный собственником эксклюзивный договор и снятие остальных объявлений. "
                 "Система сама чужие объявления не удаляет. Потом проверим ещё раз.")
    elif state == "operator":
        text += "\n\nПовторная проверка через 24 ч: дубли не устранены или нет договора. Решает оператор в «Запросах»."
    elif state == "review":
        text += "\n\nСовпадения сомнительные: объект не блокируется, оператор проверит вручную."
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], text, kb([[app_btn("🏠 Открыть объект", links.property_app(pid))]]))
    if state != "clear":
        to_operators("exclusive_dups", pid, text, lambda private: kb([[app_btn("🏠 Открыть объект", links.property_app(pid), private)]]))


@safe
def property_published(prop):
    """C5: the agent gets the link to the channel post."""
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"✅ <b>Объект #{g(prop, 'id')} опубликован в канале</b>\n{e(prop_title(prop))}",
                kb([[url_btn("📢 Пост в канале", g(prop, "post_url"))]]))


# «Запрос оператору» (stage 6): field names as the CRM shows them
CR_KIND = {"edit": "Изменение полей", "archive": "В архив", "transfer_owner": "Передать собственника", "delete": "Удаление → архив",
           "no_esign": "Эксклюзив без электронной подписи"}
CR_FIELD = {"owner_name": "Собственник", "owner_phone": "Телефон собственника", "owner_phones": "Телефоны собственника", "address": "Адрес", "cadastral_code": "Кадастр",
            "source_url": "Ссылка на объявление", "is_exclusive": "Эксклюзив", "price": "Цена", "currency": "Валюта",
            "deposit": "Залог", "sale_price": "Цена продажи", "archive_reason": "Причина", "archive_comment": "Комментарий",
            "duplicate_of": "Дубль объекта", "owner_agent": "Агент собственника"}


def phones_text(rows, sep=", "):
    """owners.phones → «+995…, +7… (сестра)»."""
    return sep.join(r["phone"] + (f" ({r['note']})" if r.get("note") else "") for r in rows or [] if r.get("phone"))


def cr_changes(cr):
    fmt = lambda v: "—" if v in (None, "", []) else ("да" if v is True else "нет" if v is False else  # noqa: E731
                                                    phones_text(v) if isinstance(v, list) else v)
    return "\n".join(f"• {e(CR_FIELD.get(k, k))}: {e(fmt(v.get('old')))} → <b>{e(fmt(v.get('new')))}</b>"
                     for k, v in (g(cr, "payload") or {}).items() if isinstance(v, dict))


@safe
def change_request_new(cr):
    """To the operators: a new request in «Запросы»."""
    pid, who = g(cr, "entity_id"), store.user(g(cr, "requested_by")) or {}
    reason = g(cr, "reason")
    to_operators("change_request", g(cr, "id"),
                 f"✋ <b>Запрос оператору · объект #{pid}</b>\n{e(CR_KIND.get(g(cr, 'kind'), g(cr, 'kind')))} · {e(staff_name(who))}\n"
                 f"{cr_changes(cr)}" + (f"\n\nПричина: {e(reason)}" if reason else ""),
                 lambda private: kb([[app_btn("🏠 Открыть объект", links.property_app(pid), private)]]))


@safe
def change_request_decided(cr):
    """To the agent who asked: approved / rejected with the operator's comment."""
    u = store.user(g(cr, "requested_by")) or {}
    if not u.get("tg_id"):
        return
    ok = g(cr, "status") == "approved"
    comment = g(cr, "decision_comment")
    tg.send(u["tg_id"], f"{'✅' if ok else '❌'} <b>Запрос по объекту #{g(cr, 'entity_id')} {'одобрен' if ok else 'отклонён'}</b>\n"
            f"{e(CR_KIND.get(g(cr, 'kind'), g(cr, 'kind')))}\n{cr_changes(cr)}" + (f"\n\nКомментарий оператора: {e(comment)}" if comment else ""),
            kb([[app_btn("🏠 Открыть объект", links.property_app(g(cr, "entity_id")))]]))


def agency_chat():
    ch = conf("PUBLISH_CHAT_ID").strip() or "@" + conf("AGENCY_CHANNEL", "rent_tbilisi_ge").lstrip("@")
    return ch


# David / Stanislav 11-23: flats / houses for rent → the agency channel; sale → @sale_in_tbilisi, commercial → @homesingeorgia
# (the Homes in Georgia community). Управление → Настройки: settings publish_chat_<kind>, empty → the agency channel.


def _can_post(chat):
    """The bot may post and delete posts in the chat (getChatMember of itself; not a member → Telegram refuses)."""
    bot = conf("BOT_TOKEN").split(":")[0]
    m = tg.call("getChatMember", chat_id=chat, user_id=bot) if bot.isdigit() else None
    return bool(m) and (m.get("status") == "creator" or (m.get("status") == "administrator" and bool(m.get("can_post_messages"))
                                                        and bool(m.get("can_delete_messages"))))


def post_chat(kind):
    """-> (chat, warning) for a post of this kind (rent | sale | commercial | cars | daily | developments). The bot without the rights there:
    the agency channel and the warning (the object card shows it) — publishing never breaks over the channel."""
    main = agency_chat()
    from app.services.settings import DEFAULTS  # store.setting reads the row only: no row = the default
    key = f"publish_chat_{kind}"
    raw = store.setting(key)
    chat = (DEFAULTS.get(key, "") if raw is None else raw).strip() or main
    if chat == main or _can_post(chat):
        return chat, None
    log.warning("publish: the bot can't post to %s (%s), the post goes to %s", chat, kind, main)
    return main, f"Бот не админ в {chat} — пост ушёл в основной канал {main}"


def _topic(chat, key):
    """PUBLISH_TOPIC_* are topics of PUBLISH_CHAT_ID only."""
    return chat_and_topic("PUBLISH_CHAT_ID", key)[1] if chat == agency_chat() else None


def _album(items, n_photos=9):
    """The album's share of [(kind, ...)] in order: the first n_photos photos + the videos, ≤10 (Telegram's limit);
    the rest of the photos are only on the site (properties.ALBUM_PHOTOS)."""
    out, ph = [], 0
    for it in items:
        if it[0] != "video":
            ph += 1
            if ph > n_photos:
                continue
        out.append(it)
    return out[:10]


COLLAGE_TILE, COLLAGE_GAP = (427, 320), 4


def _collage(paths, tmp):
    """The first 9 photos in a 3×3 grid (each cropped to 4:3 from its centre, thin white gaps) -> a JPEG path, or None."""
    from PIL import Image, ImageOps
    tw, th = COLLAGE_TILE
    try:
        sheet = Image.new("RGB", (3 * tw + 2 * COLLAGE_GAP, 3 * th + 2 * COLLAGE_GAP), "white")
        for n, p in enumerate(paths[:9]):
            with Image.open(p) as im:
                tile = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (tw, th), Image.LANCZOS)
            sheet.paste(tile, ((n % 3) * (tw + COLLAGE_GAP), (n // 3) * (th + COLLAGE_GAP)))
        out = os.path.join(tmp, "collage.jpg")
        sheet.save(out, "JPEG", quality=88)
        return out
    except Exception:  # noqa: BLE001 — no cover is no reason to lose the post
        log.warning("collage failed", exc_info=True)
        return None


def with_collage(media, tmp):
    """Irakli 11-22: the album opens with a 3×3 collage of its first 9 photos, then the photos one by one in their order
    (the collage is a cover, it replaces none). Telegram's 10-item limit: collage + 9 photos, or collage + 8 photos +
    the video. Fewer than 9 photos: no collage."""
    photos = [p for k, p in media if k != "video"]
    if len(photos) < 9 or not (c := _collage(photos, tmp)):
        return media
    videos = [x for x in media if x[0] == "video"][:1]
    keep = 9 - len(videos)
    return [("photo", c)] + [x for x in media if x[0] != "video"][:keep] + videos


def media_paths(pid, tmp, ids=None):
    """[(kind, local path, storage key)] of the album's files that exist (MEDIA_STORAGE=s3: downloaded into the tmp dir):
    _album() of ids, the operator's pick in album order (None = all, by position)."""
    rows = store.q("SELECT id, kind, path FROM property_media WHERE property_id = %s AND kind IN ('photo', 'video') ORDER BY position, id", (pid,))
    if ids is not None:
        by_id = {r["id"]: r for r in rows}
        rows = [by_id[i] for i in ids if i in by_id]
    out = []
    for k, key in _album([(r["kind"] or "photo", r["path"]) for r in rows]):
        if p := storage.fetch(key, tmp):
            out.append((k, p, key))
    return out


WATERMARK = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "watermark.png")
# the agency footer lines of post_text() -> links (as the old HTML generator's buildTelegramHTML)
FOOTER_LINKS = (
    ("🌟 Check all listings | Reviews", '<a href="https://t.me/rent_tbilisi_ge/9859">🌟 Check all listings</a> | '
                                       '<a href="https://t.me/reviews_rent_tbilisi">Reviews</a>'),
    ("SALE IN TBILISI🇬🇪", '<a href="https://t.me/sale_in_tbilisi">SALE IN TBILISI🇬🇪</a>'),  # David 11-15
    ("📱 Instagram 🗳 FB 🎥 YouTube", '<a href="https://www.instagram.com/rent_in_tbilisi/profilecard/?igsh=N3kxbTd4bTdkN2Vk">📱 Instagram</a> '
                                     '<a href="https://www.facebook.com/share/18GSsdio3b/?mibextid=LQQJ4d">🗳 FB</a> '
                                     '<a href="https://youtube.com/@rentintbilisi">🎥 YouTube</a>'),
)


def post_html(text, pid=None):
    """The post text as Telegram HTML: the address and footer lines become links; «APARTMENTS ON MAP» opens our site's
    map on the object pid (properties.hge_map_url), without it the whole map."""
    out = html.escape(text or "", quote=False)
    # the address line `📍[address](maps link)` (services.properties.post_text) → a link, as the old generator did
    out = re.sub(r"📍\[([^\]\n]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{m[2].replace(chr(34), "%22")}">📍{m[1]}</a>', out)
    for plain, linked in FOOTER_LINKS:
        out = out.replace(plain, linked)
    from app.services.properties import hge_map_url
    return out.replace("📍APARTMENTS ON MAP📍", f'📍<b><a href="{html.escape(hge_map_url(pid))}">APARTMENTS ON MAP</a></b>📍')


PHOTO_PX = 2560  # Telegram: a photo's width + height at most 10000 and 10 MB (a 48 MP iPhone original is 8064x6048)


def watermarked(path, tmp):
    """The agency watermark over a photo (40 %, stretched like the old generator did) -> a new JPEG of at most
    PHOTO_PX, upright by its EXIF, HEIC read too; the original on any failure (no Pillow, odd format)."""
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as src, Image.open(WATERMARK) as wm:
            im = ImageOps.exif_transpose(src)
            im.thumbnail((PHOTO_PX, PHOTO_PX))
            im = im.convert("RGBA")
            mark = wm.convert("RGBA").resize(im.size)
            mark.putalpha(mark.getchannel("A").point(lambda a: a * 2 // 5))
            out = os.path.join(tmp, "wm_" + os.path.basename(path) + ".jpg")
            Image.alpha_composite(im, mark).convert("RGB").save(out, "JPEG", quality=92)
            return out
    except Exception:  # noqa: BLE001
        log.warning("watermark failed for %s", path, exc_info=True)
        return path


BOT_UPLOAD_MAX = 48 * 1024 * 1024  # Bot API takes ≤ 50 MB per request; a phone video is often 150 MB+


def _bot_media(media, tmp):
    """[(kind, path)] ready for the Bot API: photos watermarked; videos squeezed with ffmpeg (720p, H.264) into what the
    photos left of BOT_UPLOAD_MAX, dropped (logged) if they still don't fit — else the whole post (rich or album) failed
    with «Request Entity Too Large» (2026-11-11: a 157 MB video)."""
    out = [(k, p if k == "video" else watermarked(p, tmp)) for k, p in media]
    budget = BOT_UPLOAD_MAX - sum(os.path.getsize(p) for k, p in out if k != "video")
    videos = [i for i, (k, _) in enumerate(out) if k == "video"]
    for i in videos:
        share = budget // len(videos)
        path = out[i][1]
        if os.path.getsize(path) > share or _rotation(path):  # ffmpeg writes the frames upright
            path = _squeeze_video(path, tmp, share)
        out[i] = ("video", path) if path else None
    return [x for x in out if x]


TG_VIDEO_MAX = 36 * 2**20  # the prepared copy leaves ~12 MB of the upload for the album's photos


def _tg_video_key(key):
    return f"tgvideo/{key}.mp4"


def _tg_video(key, tmp, src=None):
    """The Telegram-ready copy of a stored video (upright 720p H.264 ≤ TG_VIDEO_MAX) -> a local path: from the storage
    when made before (worker job tg_video at upload), else made now and stored, so a re-publish never encodes again;
    the original when it already fits and is upright or ffmpeg fails. 2026-11-16: a 4K HEVC iPhone .mov took ~80 s of
    ffmpeg (decoding, not the x264 preset) inside the publish request."""
    ck = _tg_video_key(key)
    if (not storage.is_s3() or storage.s3_has(ck)) and (p := storage.fetch(ck, tmp)):
        return p
    src = src or storage.fetch(key, tmp)
    if not src or (os.path.getsize(src) <= TG_VIDEO_MAX and not _rotation(src)):
        return src
    out = _squeeze_video(src, tmp, TG_VIDEO_MAX)
    if not out:
        return src
    with open(out, "rb") as fh:
        storage.put(ck, fh.read())
    return out


def _squeeze_video(path, tmp, max_bytes):
    """ffmpeg re-encode to fit max_bytes (bitrate from the duration, 720p, AAC 64k) -> the new path or None."""
    import subprocess
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                   capture_output=True, text=True, timeout=60).stdout.strip() or 0)
        kbps = min(int(max_bytes * 8 / 1000 / max(dur, 1) * 0.9) - 64, 6000)  # a short clip: no 100 Mbit/s
        if dur <= 0 or kbps < 300:  # too long for the budget: a mush is worse than no video
            raise ValueError(f"{dur:.0f} s does not fit {max_bytes // 2**20} MB")
        out = os.path.join(tmp, "v_" + os.path.basename(path) + ".mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264",
                        "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
                        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out], check=True, timeout=900,
                       preexec_fn=lambda: os.nice(10))  # the API shares the CPU
        if os.path.getsize(out) <= max_bytes:
            return out
        raise ValueError(f"{os.path.getsize(out) // 2**20} MB after re-encoding")
    except Exception as ex:  # noqa: BLE001
        log.warning("video %s left out of the post: %s", os.path.basename(path), ex)
        return None


def post_link(m, chat):
    uname = (m.get("chat") or {}).get("username") or (chat.lstrip("@") if str(chat).startswith("@") else None)
    if uname:
        return f"https://t.me/{uname}/{m['message_id']}"
    cid = str((m.get("chat") or {}).get("id") or chat)
    return f"https://t.me/c/{cid[4:]}/{m['message_id']}" if cid.startswith("-100") else None


def _probe(path):
    import json
    import subprocess
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height:stream_side_data=rotation:stream_tags=rotate:format=duration",
                                      "-of", "json", path], capture_output=True, text=True, timeout=60).stdout)
    st = info["streams"][0]
    rot = int(float((st.get("tags") or {}).get("rotate") or next((d.get("rotation") for d in st.get("side_data_list") or []
                                                                      if "rotation" in d), 0) or 0))
    return int(st["width"]), int(st["height"]), rot, float(info["format"]["duration"])


def _rotation(path):
    """A phone video's rotation tag (90, -90, 180) or 0; Telegram plays the stored frames, so such a video lies on its side."""
    try:
        return _probe(path)[2] % 360
    except Exception:  # noqa: BLE001
        return 0


def video_meta(path):
    """width/height/duration of a video for the Bot API (without them Telegram shows it as a square, David 11-15);
    a rotation tag swaps width and height. {} when ffprobe can't read it."""
    try:
        w, h, rot, dur = _probe(path)
    except Exception:  # noqa: BLE001
        return {}
    if rot % 180:
        w, h = h, w
    return {"width": w, "height": h, "duration": round(dur), "supports_streaming": True}


def _meta(kind, path):
    return video_meta(path) if kind == "video" else {}


def send_group(chat, thread, media, caption=None):
    """[(photo|video|document, path)] -> one album (a single file goes as sendPhoto/sendVideo/sendDocument: an album
    needs 2–10 items); the HTML caption on the first item. -> [message, ...] or None."""
    files = {f"f{n}": p for n, (_, p) in enumerate(media)}
    cap = {"caption": tg.clip(caption, 1024), "parse_mode": "HTML"} if caption else {}
    if len(media) == 1:
        kind = media[0][0]
        m = tg.call({"photo": "sendPhoto", "video": "sendVideo"}.get(kind, "sendDocument"), files={kind: files["f0"]},
                    chat_id=chat, message_thread_id=thread, **cap, **_meta(kind, media[0][1]))
        return [m] if m else None
    items = [{"type": k, "media": f"attach://f{n}", **_meta(k, p), **(cap if n == 0 else {})} for n, (k, p) in enumerate(media)]
    return tg.call("sendMediaGroup", files=files, chat_id=chat, message_thread_id=thread, media=items)


def send_rich(chat, thread, prop, media):
    """The post as one rich message (sendRichMessage): the files go as its media, the slideshow links them by id.
    -> the message or None (the caller falls back to the album)."""
    ids = [f"tg://{'video' if k == 'video' else 'photo'}?id=m{n}" for n, (k, _) in enumerate(media)]
    rich = {"html": prop.post_rich(ids), "media": [{"id": f"m{n}", "media": {"type": k if k == "video" else "photo", "media": f"attach://f{n}",
                                                                  **_meta(k, p)}}
                                    for n, (k, p) in enumerate(media)]}
    return tg.call("sendRichMessage", files={f"f{n}": p for n, (_, p) in enumerate(media)} or None, chat_id=chat,
                   message_thread_id=thread, rich_message=rich)


@safe
def publish_post(prop):
    """Posts the property to PUBLISH_CHAT_ID (else the agency channel), as the old HTML generator did: an album
    (≤10 picked photos/videos, photos watermarked) with the caption on the first item, or a text post, into
    PUBLISH_TOPIC_ALBUM; the files without watermark as documents into PUBLISH_TOPIC_ORIGINALS; the owner's contact
    with the post link into PUBLISH_TOPIC_OWNER (topics only when set). Caption: prop.post_text (services.properties.
    publish) or the description; the footer lines become links. -> (message_id, url, chat, [every message id of the
    post: originals, album, owner card], warning) or None; the ids let properties.unpost delete the whole post later.
    The chat: post_chat(prop.post_channel) — rent by default; warning: it went to the agency channel instead."""
    chat, warning = post_chat(g(prop, "post_channel") or "rent")
    album_topic = _topic(chat, "PUBLISH_TOPIC_ALBUM")
    originals_topic = _topic(chat, "PUBLISH_TOPIC_ORIGINALS")
    owner_topic = _topic(chat, "PUBLISH_TOPIC_OWNER")
    keys = g(prop, "post_media_keys")  # [(photo|video, storage key)]: sale objects and cars keep keys in the row
    # a property (no keys) is on the site as o<id>; cars have no «APARTMENTS ON MAP»
    caption = post_html(g(prop, "post_text") or g(prop, "description") or prop_title(prop),
                        g(prop, "id") if keys is None else None)
    pk = g(prop, "progress_key")  # properties.publish: the card shows each step (app/progress.py)
    with tempfile.TemporaryDirectory() as tmp:  # [(photo|video, storage key)]: sale objects and cars keep keys in the row
        progress.step(pk, "files")
        media = ([(k, p, key) for k, key in _album(keys) if (p := storage.fetch(key, tmp))] if keys is not None
                 else media_paths(g(prop, "id"), tmp, g(prop, "post_media_ids")))
        sent = []
        if media and originals_topic:
            progress.step(pk, "originals", chat=chat)
            sent += send_group(chat, originals_topic, [("document", p) for _, p, _ in media]) or []
        m = None
        # once: a squeezed video is reused by the album fallback; videos as prepared at upload (tg_video)
        videos = [(p, key) for k, p, key in media if k == "video"]
        prepared = {}
        for n, (p, key) in enumerate(videos):
            progress.step(pk, "video", n=n + 1, of=len(videos))
            prepared[key] = _tg_video(key, tmp, p)
        progress.step(pk, "photos")
        ready = with_collage(_bot_media([(k, prepared[key] if k == "video" else p) for k, p, key in media], tmp), tmp)
        progress.step(pk, "post", chat=chat)
        # RICH_POSTS=1: the post as a rich message (Irakli 2026-11-09), unless the operator rewrote the caption by hand;
        # Telegram refusing it falls back to the album below
        if conf("RICH_POSTS").strip() in ("1", "true") and callable(getattr(prop, "post_rich", None)):
            m = send_rich(chat, album_topic, prop, ready)
        if m:
            pass  # the rich post went out
        elif media:
            res = send_group(chat, album_topic, ready, caption)
            m = res[0] if res else None
            sent += res[1:] if res else []
        else:
            m = tg.send(chat, caption, thread_id=album_topic)
    if not m:
        return None
    sent.append(m)
    url = post_link(m, chat)
    owner = store.q("SELECT name, phone, phones FROM owners WHERE id = %s", (g(prop, "owner_id"),)) if g(prop, "owner_id") else []
    if owner_topic and owner and (owner[0]["name"] or owner[0]["phone"]):
        o = owner[0]
        # every number (David 11-18: the ad's and the one given on the call), the main first
        rows = o.get("phones") or ([{"phone": o["phone"]}] if o["phone"] else [])
        sent.append(tg.send(chat, "\n".join(x for x in ("🏠 <b>Owner Information</b>", g(prop, "address") and f"📍 {e(g(prop, 'address'))}",
                                            o["name"] and f"👤 {e(o['name'])}", rows and "📞 " + e(phones_text(rows, "\n📞 ")),
                                            url and f'🔗 <a href="{e(url)}">Ссылка на объявление</a>') if x),
                            thread_id=owner_topic))
    return m["message_id"], url, chat, [x["message_id"] for x in sent if x], warning


@safe
def originals_post(pid, chat, text):
    """«HIG Original Photos» (Irakli / David 11-23, their sample): every photo without the watermark as documents (albums
    of 10), then the post text (HTML), then the videos as documents. A video over BOT_UPLOAD_MAX (the Bot API's 50 MB) is
    left out with a line in the text. -> how many files went out, or None."""
    with tempfile.TemporaryDirectory() as tmp:
        rows = store.q("SELECT kind, path FROM property_media WHERE property_id = %s AND kind IN ('photo', 'video') "
                       "ORDER BY position, id", (pid,))
        files = [(r["kind"], p, r["path"]) for r in rows if (p := storage.fetch(r["path"], tmp))]
        photos = [p for k, p, _ in files if k == "photo"]
        videos, big = [], []
        for k, p, key in files:
            if k == "video":
                (videos if os.path.getsize(p) <= BOT_UPLOAD_MAX else big).append((p, key))
        if not photos:
            return None
        n = 0
        for i in range(0, len(photos), 10):
            if send_group(chat, None, [("document", p) for p in photos[i:i + 10]]):
                n += len(photos[i:i + 10])
        tg.send(chat, text)
        for p, _ in videos:
            if tg.call("sendDocument", files={"document": p}, chat_id=chat):
                n += 1
        from app.telegram import userbot
        for p, key in big:  # over the Bot API's 50 MB: the operator account sends it (Stanislav 11-24)
            if userbot.send_media(chat, key):
                n += 1
                continue
            log.warning("originals of %s: video %s is %d MB, the operator account didn't send it", pid, key, os.path.getsize(p) >> 20)
            tg.send(chat, f"🎥 Видео {os.path.getsize(p) >> 20} МБ не отправилось (больше 50 МБ, аккаунт оператора не смог) — "
                          "скачайте в карточке CRM (кнопка «Скачать видео»)")
        return n or None


@safe
def rented_post(pid, text, chat=None):
    """«Сдано» in the channel (David 11-23): the object's first photo (watermarked) with the short text, as the agency's
    own #FlatRented posts; a text post without a photo. chat: where the object's post was (default the agency channel).
    -> (message_id, url) or None."""
    chat = chat or agency_chat()
    topic = _topic(chat, "PUBLISH_TOPIC_ALBUM")
    with tempfile.TemporaryDirectory() as tmp:
        photo = next(((k, p) for k, p, _ in media_paths(pid, tmp) if k == "photo"), None)
        res = send_group(chat, topic, _bot_media([photo], tmp), text) if photo else [tg.send(chat, text, thread_id=topic)]
    m = res[0] if res else None
    return (m["message_id"], post_link(m, chat)) if m else None


@safe
def delete_post(chat, ids, album=False):
    """Deletes our post's messages -> True when they are gone. The bot first (deleteMessages, ≤100 ids; ids already
    gone are skipped): an admin with «delete messages» removes posts of any age in a channel / supergroup — the 48 h
    limit is for basic groups and private chats. The bot refused -> the operator userbot (Telethon delete_messages,
    needs the same admin right). album: a legacy post known by one message id — the userbot finds the rest of its album
    (grouped_id) first, the bot can only delete that one message."""
    if not album and tg.call("deleteMessages", chat_id=chat, message_ids=ids):
        return True
    if userbot.delete(chat, ids, album):
        return True
    return bool(album and tg.call("deleteMessages", chat_id=chat, message_ids=ids))


@safe
def report_posted(report):
    """Work-group topic posts of a report: report.data["posts"] = [{"topic": photo|showing|stickers|deal|t14|daily|general,
    "text": HTML}] (built by services.reports) -> WORK_GROUP_CHAT_ID, thread TOPIC_<TOPIC>."""
    chat = conf("WORK_GROUP_CHAT_ID").strip()
    if not chat:
        return
    posts = store.jload(g(report, "data"), {}).get("posts") or []
    for p in posts:
        _, topic = chat_and_topic("WORK_GROUP_CHAT_ID", f"TOPIC_{str(p.get('topic') or 'general').upper()}")
        if p.get("text"):
            tg.send(chat, p["text"], thread_id=topic)


MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря")


def _bday_who(x, age=False):
    tag = f" (@{e(x['username'])})" if x.get("username") else ""
    return f"<b>{e(x['name'])}</b>{tag}" + (f", {x['age']}" if age else "")


@safe
def birthdays_today(people):
    """The morning greeting: WORK_GROUP_CHAT_ID topic TOPIC_GENERAL (the team's work group, where the reports go);
    without a work group, every active employee in private. Day only, no age (privacy)."""
    text = "🎂 <b>Сегодня день рождения</b>\n" + "\n".join(f"🎉 {_bday_who(x)}" for x in people) + "\n\nПоздравляем! 🥳"
    chat, topic = chat_and_topic("WORK_GROUP_CHAT_ID", "TOPIC_GENERAL")
    if chat:
        return tg.send(chat, text, thread_id=topic)
    mine = {str(x.get("tg_id")) for x in people}
    for t in store.staff_tg_ids(("superadmin", "admin", "operator", "agent", "recruit")):
        if str(t) not in mine:
            tg.send(t, text)


@safe
def birthdays_tomorrow(people):
    """The day-before reminder, to admins and superadmins in private (with the age)."""
    d = people[0]["date"]
    text = (f"🎁 <b>Завтра, {d.day} {MONTHS[d.month - 1]}, день рождения</b>\n"
            + "\n".join(f"• {_bday_who(x, age=True)}" for x in people) + "\n\nНе забудьте поздравить.")
    for t in store.staff_tg_ids(("admin", "superadmin")):
        tg.send(t, text)


@safe
def daily_rent_conflicts(conflicts, names):
    """«Посуточная аренда»: the same nights sold twice (services/daily_rent_ical.new_conflicts) → superadmins, admins and
    the Daily Rent managers in private, with the «Шахматка» button (ApartFlow's «⚠️ Двойная занятость»)."""
    who = lambda x: (f"проживание {x.get('guest') or ''} ({x.get('platform') or ''})" if x["kind"] == "stay"  # noqa: E731
                     else {"airbnb": "Airbnb", "booking": "Booking", "other": "Другая площадка", "manual": "Закрыто вручную"}.get(x["origin"], x["origin"]))
    for c in conflicts:
        text = (f"⚠️ <b>Двойная занятость</b>\n{e(names.get(c['apartment_id'], c['apartment_id']))}: {c['start']} — {c['end']}\n"
                f"{e(who(c['a']))} и {e(who(c['b']))}")
        for t in store.staff_tg_ids(("superadmin", "admin", "daily_rent_manager")):
            url = links.miniapp("daily-rent/calendar")
            tg.send(t, text, kb([[app_btn("📅 Открыть шахматку", url, True)]]) if url else None)


def lead_btn(lid):
    return lambda private: kb([[app_btn("📋 Открыть заявку", links.lead_app(lid), private)]])


@safe
def client_time_staff(lead, own_text=None):
    """The client picked a slot (own_text None) or wrote their own time -> the lead's operator (operators chat as
    fallback). The agent gets C8 contacts_to_agent / C6 lead_assigned_agent."""
    _, i, _ = ctx(lead)
    lid, at = g(lead, "id"), g(lead, "viewing_at")
    if own_text is None:
        head = f"✅ <b>Клиент выбрал время просмотра · заявка #{lid}</b>\n🕐 {e(texts.when_long(at)) if at else '—'}"
    else:
        head = f"🕐 <b>Клиент предложил своё время · заявка #{lid}</b>\n«{e(own_text)}»"
    ag = store.user(g(lead, "agent_id"))
    text = head + (f"\n🏠 {e(address(i))}" if i else "") + f"\n👤 Агент: {e(staff_name(ag))}"
    to_lead_staff(lead, text, lead_btn(lid))


@safe
def evaluation_alert(evaluation):
    """A6: a low T14/T0 score (≤ 1) or «Есть проблема» -> operators."""
    scores = [g(evaluation, k) for k in ("h2o_client", "h2o_owner", "h2o_process")]
    did = g(evaluation, "deal_id")
    text = (f"⚠️ <b>Сигнал по сделке #{did}</b> ({e(g(evaluation, 'kind') or 't14')}, {e(g(evaluation, 'author') or '')})\n"
            f"Оценки: квартира {scores[0] if scores[0] is not None else '—'} · собственник {scores[1] if scores[1] is not None else '—'} · "
            f"агент/агентство {scores[2] if scores[2] is not None else '—'}")
    if g(evaluation, "complaints_text") or g(evaluation, "comment"):
        text += f"\n\n💬 {e(g(evaluation, 'complaints_text') or g(evaluation, 'comment'))}"
    to_operators(None, did, text, lambda private: kb([[app_btn("📄 Открыть сделку", links.deal_app(did), private)]]))


CARDS = {"slots": _slots_card, "remind": _remind_card, "survey": _survey_card, "reason": _reason_card, "t14": _t14_card}


# ---------- key actions → the agent and the operator (David 11-23: «на каждое ключевое действие и агенту, и оператору»)

OPEN = {"property": ("🏠 Открыть объект", links.property_app), "lead": ("📋 Открыть заявку", links.lead_app),
        "deal": ("🤝 Открыть сделку", links.deal_app), "contract": ("📄 Открыть договор", lambda i: links.miniapp(f"contracts?id={i}"))}


@safe
def staff_event(text, kind, ref, agents=(), actor=None, ops=True):
    """To the agents (user ids), the one who pressed it (actor: a confirmation, David 11-24 «уведомление не пришло что
    снято») and, ops, the operators (operators_targets: the connected operator account), nobody twice. The button
    opens the object / lead / deal / contract."""
    label, url = OPEN[kind]
    skip: set[str] = set()
    for aid in dict.fromkeys(a for a in (*agents, actor) if a):
        u = store.user(aid) or {}
        if u.get("tg_id") and u.get("is_active", True) and str(u["tg_id"]) not in skip:
            skip.add(str(u["tg_id"]))
            tg.send(u["tg_id"], text, kb([[app_btn(label, url(ref))]]))
    if ops:
        for chat, topic in operators_targets():
            if str(chat) not in skip:
                tg.send(chat, text, kb([[app_btn(label, url(ref), is_private(chat))]]), thread_id=topic)


def _note(note):
    return f"\n\n{e(note)}" if note else ""


@safe
def prop_event(prop, head, actor=None, agents=None, ops=True, note=None):
    """head (HTML) · объект #N + address · price; agents: default the object's agent."""
    pid = g(prop, "id")
    staff_event(f"{head} · объект #{pid}\n{e(prop_title(prop))}{_note(note)}", "property", pid,
                [g(prop, "agent_id")] if agents is None else agents, actor, ops)


@safe
def lead_event(lead, head, actor=None, agents=None, ops=True, note=None):
    """head · заявка #N + the client and the flat; agents: default the lead's agent."""
    cl, i, _ = ctx(lead)
    lid = g(lead, "id")
    staff_event(f"{head} · заявка #{lid}\n👤 {e(client_name(cl))}" + (f" · 🏠 {e(address(i))}" if i else "") + _note(note),
                "lead", lid, [g(lead, "agent_id")] if agents is None else agents, actor, ops)


@safe
def deal_event(deal, head, actor=None, agents=None, ops=True, note=None):
    """head · сделка #N + address · price; agents: default the deal's agent."""
    did = g(deal, "id")
    staff_event(f"{head} · сделка #{did}\n🏠 {e(g(deal, 'address') or '—')} · {texts.money(g(deal, 'price'))}{_note(note)}",
                "deal", did, [g(deal, "agent_id")] if agents is None else agents, actor, ops)


@safe
def contract_event(contract, head, actor=None, ops=True, note=None):
    """head · договор #N; to the lead's agent (else the contract's author) and the operators."""
    cid, ld = g(contract, "id"), store.lead(g(contract, "lead_id")) or {}
    agent = ld.get("agent_id") or g(contract, "created_by")
    who = " · ".join(e(x) for x in (g(contract, "address"), g(contract, "client_name")) if x)
    staff_event(f"{head} · договор #{cid}" + (f"\n🏠 {who}" if who else "") + (f" · заявка #{ld['id']}" if ld else "") + _note(note),
                "contract", cid, [agent], actor, ops)


@safe
def deal_overdue(contract, agents):
    """24 h after both parties signed and no deal (David 11-23): the agent / recruit and the operators."""
    cid = g(contract, "id")
    who = " · ".join(e(x) for x in (g(contract, "address"), g(contract, "client_name")) if x)
    staff_event(f"⛔ <b>Оформите сделку по договору #{cid}</b>" + (f"\n🏠 {who}" if who else "")
                + "\n\nДоговор подписан обеими сторонами больше 24 часов назад, а сделки нет. "
                  "Пока сделка не оформлена, остальная работа в CRM закрыта.", "contract", cid, agents)
