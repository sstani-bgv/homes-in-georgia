"""Every outgoing Telegram message (docs/ARCHITECTURE.md §6). Texts follow the journey screens (docs/user-journey.txt):
clients in their language (clients.lang: ru/en/ka), staff in Russian, all through the one bot (BOT_TOKEN). To the client
the bot sends only listing cards (❤️ ack + the DM handoff); viewing time and the visit details come from the operator's
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

from app import storage
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
    """Never raises; without BOT_TOKEN it is a logged no-op (no DB reads either: API tests, dev without a bot)."""
    @functools.wraps(fn)
    def wrap(*a, **kw):
        if not tg.base():
            log.info("notify.%s skipped: BOT_TOKEN is not set", fn.__name__)
            return None
        try:
            return fn(*a, **kw)
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
    """[(chat_id, topic)] for operator messages: OPERATORS_CHAT_ID (+topic), else the bot's group setting, else every
    operator/admin/superadmin in private."""
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
    """Private messages to the lead's operator (and/or agent: keys); the operators chat too when the operator isn't
    reached (no operator yet, no Telegram id, or the send failed)."""
    sent, op_ok = set(), False
    for k in keys:
        u = store.user(g(lead, k)) or {}
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


SOURCE_RU = {"agency": "канал агентства", "ss.ge": "ss.ge", "myhome.ge": "myhome.ge", "livo.ge": "livo.ge",
             "korter.ge": "korter.ge"}


def group_text(lead, timer=True):
    """B1: the lead card in the operators group."""
    cl, i, _ = ctx(lead)
    src = i.get("source") or ""
    src = SOURCE_RU.get(src) or (src.startswith("tg:") and f"канал {src[3:]}") or (src.startswith("fb:") and "Facebook") or (src.startswith("ig:") and f"Instagram {src[3:]}") or src
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


def group_kb(lead, stage):
    lid = g(lead, "id")

    def make(private):
        rows = []
        if stage == "new":
            rows.append([cb("✋ Взять заявку", f"lg:take:{lid}")])
        elif stage == "taken":
            rows.append([cb("👤 Назначить агента", f"lg:assign:{lid}")])
        rows.append([app_btn("📋 Открыть в панели", links.lead_app(lid), private)])
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
    t = (f"🆕 <b>Заявка #{lid}</b>" + (f" от оператора {e(staff_name(op))}" if op else "") +
         f"\n⏱ {env_int('SLA_AGENT_SLOTS_MIN', 30)} мин, чтобы предложить время просмотра\n\n"
         f"🏠 <b>{e(address(i))}</b>\n{e(flat_line(i))}" + (" · ваш объект" if own else "") +
         f"\n\n{e(texts.profile_summary(cl.get('profile') or {}, 'ru') or 'анкета не заполнена')}\n\n"
         "🔒 Контакты клиента бот пришлёт сам, когда клиент выберет время\n"
         "Задача: узнайте у собственника свободное время и предложите 2-3 варианта.")
    if g(lead, "client_note") and g(lead, "status") in ("agent_assigned", "slots_sent"):
        t += f"\n\n🕐 <b>Клиент предложил своё время:</b> {e(g(lead, 'client_note'))}"
    if urls := i.get("urls"):
        t += "\n\n🔗 " + "\n".join(f'<a href="{e(u)}">{e(u)}</a>' for u in urls[:3])
    m = tg.send(ag["tg_id"], t, kb([[app_btn("🕐 Предложить время", links.lead_app(lid))],
                                     [cb("⚡ Время кнопками", f"ls:quick:{lid}"), cb("✍️ Написать время", f"ls:text:{lid}")]]))
    if m:
        store.remember("agent_lead", lid, m["chat"]["id"], m["message_id"])


ACK = {"ru": "✅ Передано оператору · ответим в течение {n} минут", "en": "✅ Sent to an operator · we'll reply within {n} minutes",
       "ka": "✅ გადაეცა ოპერატორს · გიპასუხებთ {n} წუთში"}
FAV = {"ru": ("❤️ В избранном", "➡️ Следующий вариант"), "en": ("❤️ In favourites", "➡️ Next option"),
       "ka": ("❤️ რჩეულებშია", "➡️ შემდეგი ვარიანტი")}


@safe
def lead_client_ack(lead):
    """A3: the same card message changes: text + «✅ Передано оператору…», buttons «❤️ В избранном», «➡️ Следующий вариант»."""
    cl, i, lang = ctx(lead)
    ack = ACK[lang].format(n=env_int("CLIENT_PROMISE_MIN", 30))
    card = (cl.get("state") or {}).get("cards", {}).get(str(g(lead, "listing_id"))) or {}
    fav, nxt = FAV[lang]
    markup = kb([[cb(fav, "noop")], [cb(nxt, "more")], *(card.get("rows") or [])])
    refs = store.refs("card", f"{g(lead, 'client_id')}:{g(lead, 'listing_id')}")
    if tapped := tapped_card.get():
        refs = [tuple(tapped)]
    if refs and card.get("text"):
        for chat, mid in refs[-1:]:
            tg.edit(chat, mid, tg.clip(card["text"], 4096 - len(ack) - 2, html=False) + f"\n\n{ack}", markup, html=False)
    elif chat := chat_of(cl):
        tg.send(chat, f"{headline(i, lang)}\n\n{ack}" if i else ack, markup, html=False)
    handoff(lead, cl, i, lang)


HANDOFF = {  # (text, write button, back button, the prefilled first message)
    "ru": ("💬 Чтобы договориться о просмотре, напишите оператору: нажмите кнопку ниже и отправьте готовое сообщение.\n\n"
           "Подбор квартир остаётся здесь, в боте: «↩️ Вернуться в бот».", "💬 Написать оператору", "↩️ Вернуться в бот",
           "Здравствуйте! Меня интересует объект #{obj} {what} (заявка #{lead})"),
    "en": ("💬 To arrange a viewing, message the operator: tap the button below and send the ready message.\n\n"
           "Flat search stays here in the bot: «↩️ Back to the bot».", "💬 Message the operator", "↩️ Back to the bot",
           "Hello! I'm interested in property #{obj} {what} (request #{lead})"),
    "ka": ("💬 ნახვის შესათანხმებლად მიწერეთ ოპერატორს: დააჭირეთ ქვემოთ ღილაკს და გაგზავნეთ მზა შეტყობინება.\n\n"
           "ბინების შერჩევა აქ, ბოტში გრძელდება: „↩️ ბოტში დაბრუნება“.", "💬 ოპერატორისთვის მიწერა", "↩️ ბოტში დაბრუნება",
           "გამარჯობა! მაინტერესებს ობიექტი #{obj} {what} (განაცხადი #{lead})"),
}


def operator_username():
    """Who the client writes to after ❤️: OPERATOR_TG_USERNAME, else the first active operator with a @username."""
    u = conf("OPERATOR_TG_USERNAME").strip().lstrip("@")
    if not u:
        r = store.q("SELECT username FROM users WHERE is_active AND role = 'operator' AND COALESCE(username, '') != '' ORDER BY id LIMIT 1",
                    one=True)
        u = r and r["username"].lstrip("@")
    return u or None


def handoff_url(lead, i, lang):
    """t.me/<operator>?text=<the first message>: one tap opens the operator's chat with it, the client presses send, so
    the first message comes from the client (no paid/privacy limits on the operator writing first)."""
    u = operator_username()
    if not u:
        return None
    what = " · ".join(x for x in (address(i, lang) if i else "", i.get("price_usd") and texts.money(i["price_usd"])) if x)
    text = HANDOFF[lang][3].format(obj=g(lead, "property_id") or g(lead, "listing_id"), what=what, lead=g(lead, "id"))
    return f"https://t.me/{u}?text={quote(text)}"


def handoff(lead, cl, i, lang):
    """After ❤️: «Написать оператору» (prefilled DM, handled by the operator's userbot) + «Вернуться в бот»."""
    url, chat = handoff_url(lead, i, lang), chat_of(cl)
    if not url or not chat:
        log.info("lead %s: no operator @username for the DM handoff", g(lead, "id"))
        return
    text, write, back, _ = HANDOFF[lang]
    tg.send(chat, text, kb([[url_btn(write, url)], [url_btn(back, links.tg_user(conf("BOT_USERNAME")))]]), html=False)


PUSH = {"ru": ("🏡 Новый вариант под ваш запрос, его ещё нет в канале:", "❤️ Нравится", "👎 Не то"),
        "en": ("🏡 A new option for your request, not in the channel yet:", "❤️ Like", "👎 Not it"),
        "ka": ("🏡 ახალი ვარიანტი თქვენი მოთხოვნით, არხში ჯერ არ არის:", "❤️ მომწონს", "👎 არ მომწონს")}


@safe
def object_to_client(cl, listing_id):
    """Stage 10, owner-first: an object the agent fast-tracked, as a client card (photo from the ad + ❤️/👎 with the
    bot's callbacks like:<id> / no:<id>, so the usual like → lead flow follows). Facts only: no ad text, links or phones."""
    i = store.listing(listing_id)
    chat = chat_of(cl or {})
    if not i or not chat:
        return None
    lang = texts.lang_of(cl.get("lang"))
    intro, like, no = PUSH[lang]
    text = "\n".join(x for x in (intro, f"<b>{e(headline(i, lang))}</b>", f"📍 {e(address(i, lang))}", e(flat_line(i, lang))) if x)
    markup = kb([[cb(like, f"like:{i['id']}"), cb(no, f"no:{i['id']}")]])
    photo = first_photo(i)
    m = photo and tg.send_photo(chat, photo if os.path.exists(photo) else os.path.join(bot_dir(), photo), text, markup)
    return m or tg.send(chat, text, markup)


SLOTS = {"ru": ("Собственник готов показать квартиру 🏡\n<b>Выберите удобное время:</b>", "🕐 Другое время", "Открыть объявление"),
         "en": ("The owner is ready to show the flat 🏡\n<b>Pick a convenient time:</b>", "🕐 Another time", "Open the listing"),
         "ka": ("მესაკუთრე მზადაა ბინის საჩვენებლად 🏡\n<b>აირჩიეთ მოსახერხებელი დრო:</b>", "🕐 სხვა დრო", "განცხადების გახსნა")}
USERBOT_OFF = "Userbot оператора не подключён"
USERBOT_FAILED = "Userbot оператора не смог отправить сообщение клиенту"


def listing_link(i):
    """«Открыть объявление»: only our own channel's post (never a competitor source)."""
    u = next((u for u in i.get("urls") or [] if i.get("source") == "agency" and u.startswith("https://t.me/")), None)
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


CONFIRMED = {"ru": ("✅ <b>Просмотр: {when}</b>\n📍 {addr}\n\n👤 Ваш агент: <b>{agent}</b>{phone}",
                    "💬 Написать агенту в Telegram", "📅 Добавить в календарь"),
             "en": ("✅ <b>Viewing: {when}</b>\n📍 {addr}\n\n👤 Your agent: <b>{agent}</b>{phone}",
                    "💬 Message the agent in Telegram", "📅 Add to calendar"),
             "ka": ("✅ <b>ნახვა: {when}</b>\n📍 {addr}\n\n👤 თქვენი აგენტი: <b>{agent}</b>{phone}",
                    "💬 აგენტისთვის მიწერა Telegram-ში", "📅 კალენდარში დამატება")}


@safe
def slot_confirmed_client(lead, agent):
    """A4b as a plain DM from the operator's account (userbot): time, address, the agent's name/phone, his Telegram and
    the calendar link. The bot itself only edits the slot message to «✅ Вы выбрали: …»."""
    cl, i, lang = ctx(lead)
    lid, at = g(lead, "id"), g(lead, "viewing_at")
    ag = agent if isinstance(agent, dict) else {k: g(agent, k) for k in ("id", "name", "username", "phone", "tg_id")}
    t, write, cal = CONFIRMED[lang]
    text = plain(t, when=texts.when_long(at, lang) if at else "—", addr=address(i, lang), agent=staff_name(ag),
                 phone=f"\n📞 {ag['phone']}" if ag.get("phone") else "")
    text += "".join(f"\n\n{label}: {url}" for label, url in ((write, links.tg_user(ag.get("username"))), (cal, links.ics_url(lid))) if url)
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
         "\n\nНапишите клиенту до встречи. Напомню вам и клиенту за 1 час.")
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
                    kb([[app_btn("🕐 Предложить время", links.lead_app(lid))], [cb("⚡ Время кнопками", f"ls:quick:{lid}")]]))
        return
    action = cb("✋ Взять заявку", f"lg:take:{lid}") if stage == "operator_take" else None
    to_operators(None, lid, f"{head}{left_t} на {STAGE_RU.get(stage, stage)}.\n{info}",
                 lambda private: kb([[action], [app_btn("📋 Открыть в панели", links.lead_app(lid), private)]]))


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
        body, rows = f"Заявку никто не взял за {env_int('SLA_OPERATOR_TAKE_MIN', 5)} минут.", [cb("✋ Взять заявку", f"lg:take:{lid}")]
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
def deal_confirmed(deal):
    """C9c to the agent; the #Rented text to the deals topic of the work group."""
    ag = store.user(g(deal, "agent_id")) or {}
    did = g(deal, "id")
    if ag.get("tg_id"):
        t = ("✅ <b>Сделка подтверждена</b>\n#Rented опубликован в топике «Сделки».\n\n"
             f"💼 Ваша доля: <b>{texts.money(g(deal, 'agent_amount'))}</b>\n📊 Эксклюзивных сделок: {ag.get('exclusive_deals') or 0}")
        if g(deal, "rent_from"):
            t += f"\n\n🗓 T14 напомню <b>{fmt_date(g(deal, 't14_date'))[:5] if g(deal, 't14_date') else '—'}</b>"
        if g(deal, "rent_to"):
            t += f"\n🔔 Конец договора {fmt_date(g(deal, 'rent_to'))}: напомню за 30, 7 и 1 день"
        tg.send(ag["tg_id"], t, kb([[app_btn("📝 T0: оценить сделку", links.deal_app(did))]]))
    chat, topic = chat_and_topic("WORK_GROUP_CHAT_ID", "TOPIC_DEAL")
    if chat:
        try:
            from app.services import deals as deals_svc
            text = deals_svc.rented_text(deal)  # HTML
        except Exception:
            text = f"#Rented\n{e(g(deal, 'address') or '')}\n{texts.money(g(deal, 'price'))}\nАгент: {e(staff_name(ag))}"
        tg.send(chat, text, thread_id=topic)


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


def prop_title(prop):
    return " · ".join(str(x) for x in (g(prop, "address") or g(prop, "district"), g(prop, "price") and texts.money(g(prop, "price"))) if x)


@safe
def property_call_retry(prop):
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"📞 Пора перезвонить собственнику: <b>{e(prop_title(prop))}</b> (объект #{g(prop, 'id')})",
                kb([[app_btn("🏠 Открыть объект", links.property_app(g(prop, "id")))]]))


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
def property_published(prop):
    """C5: the agent gets the link to the channel post."""
    ag = store.user(g(prop, "agent_id")) or {}
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"✅ <b>Объект #{g(prop, 'id')} опубликован в канале</b>\n{e(prop_title(prop))}",
                kb([[url_btn("📢 Пост в канале", g(prop, "post_url"))]]))


# «Запрос оператору» (stage 6): field names as the CRM shows them
CR_KIND = {"edit": "Изменение полей", "archive": "В архив", "transfer_owner": "Передать собственника", "delete": "Удаление → архив"}
CR_FIELD = {"owner_name": "Собственник", "owner_phone": "Телефон собственника", "address": "Адрес", "cadastral_code": "Кадастр",
            "source_url": "Ссылка на объявление", "is_exclusive": "Эксклюзив", "price": "Цена", "currency": "Валюта",
            "deposit": "Залог", "sale_price": "Цена продажи", "archive_reason": "Причина", "archive_comment": "Комментарий",
            "duplicate_of": "Дубль объекта", "owner_agent": "Агент собственника"}


def cr_changes(cr):
    fmt = lambda v: "—" if v in (None, "") else ("да" if v is True else "нет" if v is False else v)  # noqa: E731
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


def media_paths(pid, tmp, ids=None):
    """[(kind, local path)] of the first 10 files that exist (MEDIA_STORAGE=s3: downloaded into the tmp dir).
    ids: the operator's pick in album order (None = all, by position)."""
    rows = store.q("SELECT id, kind, path FROM property_media WHERE property_id = %s ORDER BY position, id", (pid,))
    if ids is not None:
        by_id = {r["id"]: r for r in rows}
        rows = [by_id[i] for i in ids if i in by_id]
    out = []
    for r in rows:
        if len(out) == 10:
            break
        if p := storage.fetch(r["path"], tmp):
            out.append((r["kind"] or "photo", p))
    return out


WATERMARK = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "watermark.png")
# the agency footer lines of post_text() -> links (as the old HTML generator's buildTelegramHTML)
FOOTER_LINKS = (
    ("🌟 Check all listings | Reviews", '<a href="https://t.me/rent_tbilisi_ge/9859">🌟 Check all listings</a> | '
                                       '<a href="https://t.me/reviews_rent_tbilisi">Reviews</a>'),
    ("📍APARTMENTS ON MAP📍", '<a href="https://t.me/RentInTbilisi_bot/MAP?startapp=source=sale_in_tbilisi">📍APARTMENTS ON MAP📍</a>'),
    ("📱 Instagram 🗳 FB 🎥 YouTube", '<a href="https://www.instagram.com/rent_in_tbilisi/profilecard/?igsh=N3kxbTd4bTdkN2Vk">📱 Instagram</a> '
                                     '<a href="https://www.facebook.com/share/18GSsdio3b/?mibextid=LQQJ4d">🗳 FB</a> '
                                     '<a href="https://youtube.com/@rentintbilisi">🎥 YouTube</a>'),
)


def post_html(text):
    out = html.escape(text or "", quote=False)
    # the address line `📍[address](maps link)` (services.properties.post_text) → a link, as the old generator did
    out = re.sub(r"📍\[([^\]\n]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{m[2].replace(chr(34), "%22")}">📍{m[1]}</a>', out)
    for plain, linked in FOOTER_LINKS:
        out = out.replace(plain, linked)
    return out


def watermarked(path, tmp):
    """The agency watermark over a photo (40 %, stretched like the old generator did) -> a new JPEG; the original
    on any failure (no Pillow, odd format)."""
    try:
        from PIL import Image
        with Image.open(path) as im, Image.open(WATERMARK) as wm:
            im = im.convert("RGBA")
            mark = wm.convert("RGBA").resize(im.size)
            mark.putalpha(mark.getchannel("A").point(lambda a: a * 2 // 5))
            out = os.path.join(tmp, "wm_" + os.path.basename(path) + ".jpg")
            Image.alpha_composite(im, mark).convert("RGB").save(out, "JPEG", quality=92)
            return out
    except Exception:  # noqa: BLE001
        log.warning("watermark failed for %s", path, exc_info=True)
        return path


def post_link(m, chat):
    uname = (m.get("chat") or {}).get("username") or (chat.lstrip("@") if str(chat).startswith("@") else None)
    if uname:
        return f"https://t.me/{uname}/{m['message_id']}"
    cid = str((m.get("chat") or {}).get("id") or chat)
    return f"https://t.me/c/{cid[4:]}/{m['message_id']}" if cid.startswith("-100") else None


def send_group(chat, thread, media, caption=None):
    """[(photo|video|document, path)] -> one album (a single file goes as sendPhoto/sendVideo/sendDocument: an album
    needs 2–10 items); the HTML caption on the first item. -> [message, ...] or None."""
    files = {f"f{n}": p for n, (_, p) in enumerate(media)}
    cap = {"caption": tg.clip(caption, 1024), "parse_mode": "HTML"} if caption else {}
    if len(media) == 1:
        kind = media[0][0]
        m = tg.call({"photo": "sendPhoto", "video": "sendVideo"}.get(kind, "sendDocument"), files={kind: files["f0"]},
                    chat_id=chat, message_thread_id=thread, **cap)
        return [m] if m else None
    items = [{"type": k, "media": f"attach://f{n}", **(cap if n == 0 else {})} for n, (k, _) in enumerate(media)]
    return tg.call("sendMediaGroup", files=files, chat_id=chat, message_thread_id=thread, media=items)


@safe
def publish_post(prop):
    """Posts the property to PUBLISH_CHAT_ID (else the agency channel), as the old HTML generator did: an album
    (≤10 picked photos/videos, photos watermarked) with the caption on the first item, or a text post, into
    PUBLISH_TOPIC_ALBUM; the files without watermark as documents into PUBLISH_TOPIC_ORIGINALS; the owner's contact
    with the post link into PUBLISH_TOPIC_OWNER (topics only when set). Caption: prop.post_text (services.properties.
    publish) or the description; the footer lines become links. -> (message_id, url) or None."""
    chat = agency_chat()
    _, album_topic = chat_and_topic("PUBLISH_CHAT_ID", "PUBLISH_TOPIC_ALBUM")
    _, originals_topic = chat_and_topic("PUBLISH_CHAT_ID", "PUBLISH_TOPIC_ORIGINALS")
    _, owner_topic = chat_and_topic("PUBLISH_CHAT_ID", "PUBLISH_TOPIC_OWNER")
    caption = post_html(g(prop, "post_text") or g(prop, "description") or prop_title(prop))
    with tempfile.TemporaryDirectory() as tmp:
        media = media_paths(g(prop, "id"), tmp, g(prop, "post_media_ids"))
        if media and originals_topic:
            send_group(chat, originals_topic, [("document", p) for _, p in media])
        if media:
            res = send_group(chat, album_topic, [(k, p if k == "video" else watermarked(p, tmp)) for k, p in media], caption)
            m = res[0] if res else None
        else:
            m = tg.send(chat, caption, thread_id=album_topic)
    if not m:
        return None
    url = post_link(m, chat)
    owner = store.q("SELECT name, phone FROM owners WHERE id = %s", (g(prop, "owner_id"),)) if g(prop, "owner_id") else []
    if owner_topic and owner and (owner[0]["name"] or owner[0]["phone"]):
        o = owner[0]
        tg.send(chat, "\n".join(x for x in ("🏠 <b>Owner Information</b>", g(prop, "address") and f"📍 {e(g(prop, 'address'))}",
                                            o["name"] and f"👤 {e(o['name'])}", o["phone"] and f"📞 {e(o['phone'])}",
                                            url and f'🔗 <a href="{e(url)}">Ссылка на объявление</a>') if x),
                thread_id=owner_topic)
    return m["message_id"], url


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
