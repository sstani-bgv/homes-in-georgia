"""Every outgoing Telegram message (docs/ARCHITECTURE.md §6). Texts follow the journey screens (docs/user-journey.txt):
clients in their language (clients.lang), staff in Russian. Arguments may be ORM objects or dicts. Message refs go to
the `messages` table (kind, ref) so cards can be edited later. No function ever raises (failures are logged)."""
import contextvars
import functools
import html
import logging
import os
import tempfile
from datetime import datetime, timezone

from app import storage
from app.telegram import client as tg
from app.telegram import links, store, texts
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


def to_lead_staff(lead, text, keyboard_for, keys=("operator_id", "agent_id")):
    """Private messages to the lead's operator and/or agent; the operators chat too when the operator isn't reached
    (no operator yet, no Telegram id, or the send failed)."""
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
    if lang == "en":
        return i.get("district_en") or d.get("district_en") or texts.DISTRICT_EN.get(i.get("district"), i.get("district")) or "Tbilisi"
    return i.get("district") or "Тбилиси"


def address(i, lang="ru"):
    d = i.get("details") or {}
    a = (lang == "en" and d.get("address_en")) or d.get("address_ru") or i.get("address")
    return f"{district(i, lang)}, {a}" if a and district(i, lang) not in a else (a or district(i, lang))


def beds(n, lang):
    if n is None:
        return ""
    if n == 0:
        return "studio" if lang == "en" else "студия"
    return f"{n} {('bedroom' if n == 1 else 'bedrooms') if lang == 'en' else ('спальня' if n == 1 else 'спальни')}"


def flat_line(i, lang="ru"):
    """2 спальни · 78 м² · $850 · ❗️ эксклюзив"""
    d = i.get("details") or {}
    parts = [beds(i.get("bedrooms"), lang), i.get("area") and f"{i['area']:g} {'m²' if lang == 'en' else 'м²'}",
             i.get("price_usd") and texts.money(i["price_usd"]), d.get("exclusive") and ("❗️ exclusive" if lang == "en" else "❗️ эксклюзив")]
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
         f"\n\n👤 <b>{e(client_name(cl))}</b> · Telegram" + (f" · {cl.get('lang', 'ru')}" if cl.get("lang") == "en" else "") +
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


ACK = {"ru": "✅ Передано оператору · ответим в течение {n} минут", "en": "✅ Sent to an operator · we'll reply within {n} minutes"}
FAV = {"ru": ("❤️ В избранном", "➡️ Следующий вариант"), "en": ("❤️ In favourites", "➡️ Next option")}


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


SLOTS = {"ru": ("Собственник готов показать квартиру 🏡\n<b>Выберите удобное время:</b>", "🕐 Предложить своё время", "Открыть объявление"),
         "en": ("The owner is ready to show the flat 🏡\n<b>Pick a convenient time:</b>", "🕐 Suggest my own time", "Open the listing")}


def listing_link(i):
    """«Открыть объявление»: only our own channel's post (never a competitor source)."""
    u = next((u for u in i.get("urls") or [] if i.get("source") == "agency" and u.startswith("https://t.me/")), None)
    return u


def first_photo(i):
    return next((p for p in i.get("photos") or [] if os.path.exists(p) or os.path.exists(os.path.join(bot_dir(), p))), None)


def bot_dir():
    return str(store.ROOT / "bot")


@safe
def slots_to_client(lead, slots):
    """A4a: photo + «Ваке · 2 спальни · $850» + one button per slot + «Предложить своё время»."""
    cl, i, lang = ctx(lead)
    chat = chat_of(cl)
    if not chat:
        return
    lid = g(lead, "id")
    ask, own, open_ = SLOTS[lang]
    text = f"<b>{e(headline(i, lang))}</b>\n📍 {e(address(i, lang))}\n\n{ask}"
    rows = [[url_btn(open_, listing_link(i))]] + [[cb(texts.slot_label(g(s, "starts_at"), lang), f"ls:pick:{g(s, 'id')}")] for s in slots]
    rows.append([cb(own, f"ls:own:{lid}")])
    photo = first_photo(i)
    m = None
    if photo:
        m = tg.send_photo(chat, photo if os.path.exists(photo) else os.path.join(bot_dir(), photo), text, kb(rows))
    m = m or tg.send(chat, text, kb(rows))
    if m:
        store.remember("slots", lid, m["chat"]["id"], m["message_id"])


CONFIRMED = {"ru": ("✅ <b>Просмотр: {when}</b>\n📍 {addr}\n\n👤 Ваш агент: <b>{agent}</b>{phone}",
                    "💬 Написать агенту в Telegram", "📅 Добавить в календарь", "⏰ Опаздываю", "🔁 Перенести"),
             "en": ("✅ <b>Viewing: {when}</b>\n📍 {addr}\n\n👤 Your agent: <b>{agent}</b>{phone}",
                    "💬 Message the agent in Telegram", "📅 Add to calendar", "⏰ Running late", "🔁 Reschedule")}


@safe
def slot_confirmed_client(lead, agent):
    """A4b: the slot message keeps only «✓ <time>»; then address, agent name/phone, write/calendar/late/reschedule."""
    cl, i, lang = ctx(lead)
    chat = chat_of(cl)
    lid, at = g(lead, "id"), g(lead, "viewing_at")
    if at:
        for c, mid in store.refs("slots", lid):
            tg.edit_markup(c, mid, kb([[cb("✓ " + texts.slot_label(at, lang), "noop")]]))
    if not chat:
        return
    ag = agent if isinstance(agent, dict) else {k: g(agent, k) for k in ("id", "name", "username", "phone", "tg_id")}
    t, write, cal, late, resched = CONFIRMED[lang]
    text = t.format(when=texts.when_long(at, lang) if at else "—", addr=e(address(i, lang)), agent=e(staff_name(ag)),
                    phone=f"\n📞 {e(ag['phone'])}" if ag.get("phone") else "")
    tg.send(chat, text, kb([[url_btn(write, links.tg_user(ag.get("username")))], [url_btn(cal, links.ics_url(lid))],
                            [cb(late, f"lv:late:{lid}"), cb(resched, f"lv:resched:{lid}")]]))


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
          "en": ("⏰ Viewing in an hour: <b>{hm}</b>, {addr}\nAgent {agent}", "👍 I'll be there", "⏰ Running late", "🔁 Reschedule")}


@safe
def viewing_reminder(lead):
    cl, i, lang = ctx(lead)
    lid, at = g(lead, "id"), g(lead, "viewing_at")
    hm = texts.local(at).strftime("%H:%M") if at else "—"
    ag = store.user(g(lead, "agent_id")) or {}
    t, ok, late, resched = REMIND[lang]
    if chat := chat_of(cl):
        tg.send(chat, t.format(hm=hm, addr=e(address(i, lang)), agent=e(staff_name(ag))),
                kb([[cb(ok, f"lv:ok:{lid}"), cb(late, f"lv:late:{lid}")], [cb(resched, f"lv:resched:{lid}")]]))
    if ag.get("tg_id"):
        tg.send(ag["tg_id"], f"⏰ Через час просмотр #{lid}: <b>{hm}</b>, {e(address(i))}\nКлиент {e(client_name(cl))}"
                + (f", 📞 {e(cl['phone'])}" if cl.get("phone") else ""))


SURVEY = {"ru": ("{name}, как прошёл просмотр квартиры в {where}?", "Снимаю ✅", "Не подошло ❌", "Думаю 🤔"),
          "en": ("{name}, how was the viewing of the flat in {where}?", "I'm renting it ✅", "Not for me ❌", "Thinking 🤔")}


@safe
def post_viewing_survey(lead):
    """A5a to the client (lo:c:) and C9a to the agent (lo:a:)."""
    cl, i, lang = ctx(lead)
    lid = g(lead, "id")
    t, yes, no, think = SURVEY[lang]
    if chat := chat_of(cl):
        m = tg.send(chat, t.format(name=e(cl.get("name") or ("Hi" if lang == "en" else "Здравствуйте")), where=e(district(i, lang))),
                    kb([[cb(yes, f"lo:c:{lid}:rented")], [cb(no, f"lo:c:{lid}:refused")], [cb(think, f"lo:c:{lid}:thinking")]]))
        if m:
            store.remember("survey_client", lid, m["chat"]["id"], m["message_id"])
    ag = store.user(g(lead, "agent_id")) or {}
    if ag.get("tg_id"):
        m = tg.send(ag["tg_id"], f"Как прошёл просмотр #{lid}?\n{e(address(i))} · клиент {e(client_name(cl))}",
                    kb([[cb("Сдано ✅", f"lo:a:{lid}:rented"), cb("Не подошло ❌", f"lo:a:{lid}:refused")],
                        [cb("Думает 🤔", f"lo:a:{lid}:thinking"), cb("Перенос 🔁", f"lo:a:{lid}:reschedule")]]))
        if m:
            store.remember("survey_agent", lid, m["chat"]["id"], m["message_id"])


REASON_ASK = {"ru": "Жаль 😔 Что не подошло?", "en": "Sorry to hear that 😔 What didn't fit?"}


@safe
def refusal_reason_ask(lead):
    cl, _, lang = ctx(lead)
    lid = g(lead, "id")
    r = {k: v[lang] for k, v in texts.REFUSAL.items()}
    if chat := chat_of(cl):
        tg.send(chat, REASON_ASK[lang], kb([[cb(r["price"], f"lr:{lid}:price"), cb(r["location"], f"lr:{lid}:location")],
                                            [cb(r["condition"], f"lr:{lid}:condition"), cb(r["other"], f"lr:{lid}:other")]]))


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
             "en": "{name}, you've been living in your new flat for 14 days 🏡\nIs everything fine? Please rate three points (10 seconds):"}


def t14_question(deal_id, n, lang):
    """Question n (1-3) with 0-3 buttons (t14:<deal>:<n>:<score>)."""
    return (f"<b>{n}.</b> {texts.T14_QUESTIONS[lang][n - 1]}",
            kb([[cb(str(s), f"t14:{deal_id}:{n}:{s}") for s in range(4)]]))


@safe
def t14_client(deal):
    """A6: intro + the first of three questions; the bot asks the next ones as the client answers."""
    ld = deal_lead(deal)
    cl = store.client(ld.get("client_id")) or {}
    chat, lang = chat_of(cl), texts.lang_of(cl.get("lang"))
    if not chat:
        return
    tg.send(chat, T14_INTRO[lang].format(name=e(cl.get("name") or ("Hi" if lang == "en" else "Здравствуйте"))))
    tg.send(chat, *t14_question(g(deal, "id"), 1, lang))


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


def agency_chat():
    ch = conf("PUBLISH_CHAT_ID").strip() or "@" + conf("AGENCY_CHANNEL", "rent_tbilisi_ge").lstrip("@")
    return ch


def media_paths(pid, tmp):
    """[(kind, local path)] of the first 10 files that exist (MEDIA_STORAGE=s3: downloaded into the tmp dir)."""
    rows = store.q("SELECT kind, path FROM property_media WHERE property_id = %s ORDER BY position, id", (pid,))
    out = []
    for r in rows:
        if len(out) == 10:
            break
        if p := storage.fetch(r["path"], tmp):
            out.append((r["kind"] or "photo", p))
    return out


@safe
def publish_post(prop):
    """Posts the property to the agency channel: album (≤10 photos/videos) with the caption on the first item, or a
    text post. Caption: prop.post_text (built by services.properties.publish) or the description.
    -> (message_id, url) or None."""
    chat = agency_chat()
    caption = g(prop, "post_text") or g(prop, "description") or prop_title(prop)
    with tempfile.TemporaryDirectory() as tmp:
        media = media_paths(g(prop, "id"), tmp)
        if media:
            files, items = {}, []
            for n, (kind, path) in enumerate(media):
                files[f"f{n}"] = path
                items.append({"type": "video" if kind == "video" else "photo", "media": f"attach://f{n}",
                              **({"caption": tg.clip(caption, 1024, html=False)} if n == 0 else {})})
            res = tg.call("sendMediaGroup", files=files, chat_id=chat, media=items)
            m = res[0] if res else None
        else:
            m = tg.send(chat, tg.plain_to_html(caption, 4096))
    if not m:
        return None
    mid = m["message_id"]
    uname = (m.get("chat") or {}).get("username") or (chat.lstrip("@") if chat.startswith("@") else None)
    return mid, (f"https://t.me/{uname}/{mid}" if uname else None)


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


@safe
def client_message(client, text, lead_id):
    """Operator -> client via the bot (CRM «Написать клиенту»)."""
    cl = client if isinstance(client, dict) else {k: g(client, k) for k in ("chat_id", "external_id", "channel", "lang")}
    if chat := chat_of(cl):
        head = "💬 Message from the operator:" if cl.get("lang") == "en" else "💬 Сообщение от оператора:"
        hint = "Use Reply below or reply to this message." if cl.get("lang") == "en" else "Нажмите «Ответить» или ответьте на это сообщение."
        label = "💬 Reply" if cl.get("lang") == "en" else "💬 Ответить"
        m = tg.send(chat, f"{head} #{lead_id}\n{e(text)}\n\n{hint}", kb([[cb(label, f"lm:reply:{lead_id}")]]))
        if m:
            store.remember("client_message", lead_id, m["chat"]["id"], m["message_id"])
        return m


def lead_btn(lid):
    return lambda private: kb([[app_btn("📋 Открыть заявку", links.lead_app(lid), private)]])


@safe
def lead_client_reply(lead, text):
    """A client reply is saved in the lead and delivered to its operator and agent (the operators chat as fallback)."""
    lid = g(lead, "id")
    to_lead_staff(lead, f"💬 <b>Ответ клиента по заявке #{lid}</b>\n\n{e(text)}", lead_btn(lid))


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
    to_lead_staff(lead, text, lead_btn(lid), keys=("operator_id",))


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
