"""The client journey in the bot (docs/user-journey.txt): questionnaire A1a-A2a (q:), ❤️ → lead (A3), the operators
group (B1, lg:), the agent's quick slots (C7, ls:quick/t/send/text), the slot card in the client's DM with the operator
and the other client cards (A4a slots ls:pick/own, the reminder lv:, the survey lo:c:, the refusal reason lr:, T14
t14:): the operator's userbot sends the bot's inline result «<kind>:<token>» into the client's DM and the presses come
back here with inline_message_id; the agent's outcome (C9a, lo:a:). Business rules live in the
backend services (app.services.*); outgoing cards in app.telegram.notify. Client transient state: clients.state["await"];
staff: ctx.user_data."""
import asyncio
import html
import logging
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from telegram import InlineQueryResultArticle, InputTextMessageContent, LinkPreviewOptions, Update
from telegram.error import BadRequest, TelegramError

import core
import db
from app.telegram import client as tgc
from app.telegram import links, notify, store, texts, userbot
from app.telegram.client import cb, kb

log = logging.getLogger(__name__)
e = html.escape
STAFF = ("operator", "admin", "superadmin")
FIELD = ("agent", "recruit")


# ---------- services (sync, in a thread: one session per call) ----------

def _run(fn, args, kw):
    from app.db import SessionLocal
    with SessionLocal() as s:
        return fn(s, *args, **kw)


async def svc(fn, *args, **kw):
    """-> (result, None) or (None, DomainError code)."""
    from app.services.errors import DomainError
    try:
        return await asyncio.to_thread(_run, fn, args, kw), None
    except DomainError as ex:
        return None, ex.code


async def th(fn, *args, **kw):
    """A sync DB/HTTP helper off the event loop (the bot runs updates concurrently)."""
    return await asyncio.to_thread(fn, *args, **kw)


async def answer(query, *args, **kw):
    """query.answer that never raises: a slow handler or a double tap gets «query is too old» / already answered."""
    try:
        await query.answer(*args, **kw)
    except TelegramError as ex:
        log.info("answerCallbackQuery %s: %s", query.data, ex)


async def edit_text(query, text, **kw):
    """query.edit_message_text; «message is not modified» (two quick taps rendering the same state) is not an error."""
    try:
        await query.edit_message_text(text, **kw)
    except BadRequest as ex:
        if "not modified" not in str(ex).lower():
            raise


async def bind(user):
    """Staff added in the CRM by @username get their Telegram id bound before any role check (core.agent does it)."""
    await th(core.agent, user.id, user.full_name, user.username)


_locks = {}


def lock_for(key):
    """One asyncio.Lock per key (e.g. a lead): serializes double taps on the same object."""
    if len(_locks) > 1000:
        for k in [k for k, v in _locks.items() if not v.locked()]:
            _locks.pop(k, None)
    return _locks.setdefault(key, asyncio.Lock())


_taps = OrderedDict()


def first_tap(query, tag=None):
    """False for a repeated press on the same message (double tap): (chat, message, tag or the button's data)."""
    m = query.message
    key = (m.chat.id if m else None, m.message_id if m else query.inline_message_id, tag or query.data)
    if key in _taps:
        return False
    _taps[key] = True
    while len(_taps) > 5000:
        _taps.popitem(last=False)
    return True


def _user(s, tg_id):
    from app.services import users
    u = users.by_tg(s, tg_id)
    return u if u and u.is_active else None


async def user_of(tg_id):
    """Active users row (ORM, detached) of a Telegram user, or None."""
    return (await svc(_user, tg_id))[0]


def _with_user(fn):
    """fn(db, user, *args): the acting user loaded in the same session (services want ORM objects)."""
    def run(s, tg_id, *args, **kw):
        return fn(s, _user(s, tg_id), *args, **kw)
    return run


def lead_row(lead_id):
    with db.tx() as c:
        r = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(r) if r else None


def user_row(uid):
    with db.tx() as c:
        r = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(r) if r else None


# ---------- small helpers ----------

def L(cl):
    return texts.lang_of(cl["lang"])


def tr(cl, ru, en, ka):
    return texts.pick(L(cl), ru, en, ka)


def markup(rows):
    from bot import kb_of
    return kb_of(kb(rows))


def set_await(cl, kind=None, ref=None):
    core.update(cl, lambda st, r: st.update({"await": {"kind": kind, "ref": ref} if kind else None}))


def awaiting(cl):
    return (cl["state"].get("await") or {}).get("kind"), (cl["state"].get("await") or {}).get("ref")


def load_profile(cl):
    """The client's current profile from the DB (another tap may have changed it meanwhile)."""
    import json
    with db.tx() as c:
        r = c.execute("SELECT profile FROM clients WHERE id = ?", (cl["id"],)).fetchone()
    cl["profile"] = json.loads((r and r[0]) or "{}")
    return cl["profile"]


def save_profile(cl, fn):
    """Atomically change clients.profile (fn(profile) in place); returns the new profile."""
    import json
    with db.tx() as c:
        r = c.execute("SELECT profile FROM clients WHERE id = ? FOR UPDATE", (cl["id"],)).fetchone()
        p = json.loads(r[0] or "{}")
        fn(p)
        c.execute("UPDATE clients SET profile = ?, updated_at = ? WHERE id = ?", (json.dumps(p, ensure_ascii=False), db.now(), cl["id"]))
    cl["profile"] = p
    return p


def set_done(cl, done):
    import json
    with db.tx() as c:
        c.execute("UPDATE clients SET profile_done = ? WHERE id = ?", (int(done), cl["id"]))
        if done:  # the client card's timeline (stage 8): each finished questionnaire with what it said
            c.execute("INSERT INTO events (entity, entity_id, action, actor_label, data, at) VALUES ('client', ?, 'profile_saved', "
                      "'client', ?::jsonb, now())", (str(cl["id"]), json.dumps({"profile": load_profile(cl)}, ensure_ascii=False)))
    cl["profile_done"] = int(done)


def checked(rows):
    """Buttons of a PTB keyboard marked ✓ -> their callback data."""
    return [b.callback_data for row in rows for b in row if b.text.startswith("✓ ") and b.callback_data]


# ---------- A1a-A2a questionnaire: ONE message, each step edits it (clients.state["qmsg"] = its message id) ----------

STEPS = ("res", "pets", "terms", "bud", "beds", "place")
OPTS = {"res": texts.RESIDENTS, "pets": texts.PETS, "terms": texts.TERMS, "bud": texts.BUDGETS, "beds": texts.BEDROOMS}
MULTI = {"pets": "pets", "terms": "terms", "beds": "bedrooms"}
Q = {  # (ru, en, ka)
    "intro": ("Здравствуйте, {name}! 👋\nЯ помогу найти квартиру в Тбилиси без комиссии.\n\nОтветьте на 6 коротких вопросов, это займёт "
              "минуту. Где подходит несколько ответов, выберите все и нажмите «Готово».",
              "Hello, {name}! 👋\nI'll help you find a flat in Tbilisi with no commission.\n\nAnswer 6 short questions, it takes a minute. "
              "Where several answers fit, pick them all and tap «Done».",
              "გამარჯობა, {name}! 👋\nდაგეხმარებით თბილისში ბინის პოვნაში საკომისიოს გარეშე.\n\nუპასუხეთ 6 მოკლე კითხვას, ამას ერთი "
              "წუთი დასჭირდება. სადაც რამდენიმე პასუხი გერგებათ, აირჩიეთ ყველა და დააჭირეთ „მზადაა“."),
    "res": ("<b>1 из 6.</b> Сколько человек будет жить?", "<b>1 of 6.</b> How many people will live there?",
            "<b>1 / 6.</b> რამდენი ადამიანი იცხოვრებს?"),
    "pets": ("<b>2 из 6.</b> Будут ли животные? Можно несколько.", "<b>2 of 6.</b> Any pets? You can pick several.",
             "<b>2 / 6.</b> გეყოლებათ შინაური ცხოველი? შეგიძლიათ რამდენიმე აირჩიოთ."),
    "terms": ("<b>3 из 6.</b> На какой срок? Можно несколько.", "<b>3 of 6.</b> For how long? You can pick several.",
              "<b>3 / 6.</b> რა ვადით? შეგიძლიათ რამდენიმე აირჩიოთ."),
    "bud": ("<b>4 из 6.</b> Бюджет в месяц?", "<b>4 of 6.</b> Monthly budget?", "<b>4 / 6.</b> რა ბიუჯეტი გაქვთ თვეში?"),
    "beds": ("<b>5 из 6.</b> Сколько спален? Можно несколько.", "<b>5 of 6.</b> How many bedrooms? You can pick several.",
             "<b>5 / 6.</b> რამდენი საძინებელი? შეგიძლიათ რამდენიმე აირჩიოთ."),
    "place": ("<b>6 из 6.</b> Где искать?", "<b>6 of 6.</b> Where should I look?", "<b>6 / 6.</b> სად ვეძებო?"),
    "dist": ("Отметьте районы, можно несколько:", "Pick districts, several are fine:", "მონიშნეთ უბნები, შეიძლება რამდენიმე:"),
    "done": ("Готово ➜", "Done ➜", "მზადაა ➜"), "own": ("✏️ Своя сумма", "✏️ My own amount", "✏️ ჩემი თანხა"),
    "own_ask": ("Напишите сумму, например «до 900» или «около 1000$».", "Write the amount, e.g. «up to 900» or «about $1000».",
                "დაწერეთ თანხა, მაგალითად „900-მდე“ ან „დაახლოებით 1000$“."),
    "own_bad": ("Не понял сумму 🙈 Напишите, например, «до 900».", "I didn't get the amount 🙈 Try «up to 900».",
                "თანხა ვერ გავიგე 🙈 დაწერეთ, მაგალითად, „900-მდე“."),
    "map": ("📍 Выбрать на карте", "📍 Pick on the map", "📍 რუკაზე არჩევა"),
    "districts": ("🏙 Выбрать районы", "🏙 Pick districts", "🏙 უბნების არჩევა"),
    "any": ("Любой район", "Any district", "ნებისმიერი უბანი"),
    "map_hint": ("Нажмите кнопку «🗺 Выбрать место на карте» внизу экрана и поставьте точку 👇",
                 "Tap «🗺 Pick a place on the map» at the bottom of the screen and drop a pin 👇",
                 "დააჭირეთ ეკრანის ქვედა ნაწილში ღილაკს „🗺 ადგილის არჩევა რუკაზე“ და მონიშნეთ წერტილი 👇"),
    "picked": ("Выбрано: {x}", "Selected: {x}", "არჩეულია: {x}"),
    "summary": ("<b>Ваш профиль</b> ✅\n{p}\n\n✍️ Напишите своими словами, какую квартиру ищете: где, рядом с чем, что важно "
                "(ремонт, балкон, этаж). Подберу точнее.\n\nЕсли есть фото квартир, которые вам нравятся, пришлите их: "
                "подберу похожие по стилю 📷",
                "<b>Your profile</b> ✅\n{p}\n\n✍️ Describe in your own words the flat you're looking for: where, near what, "
                "what matters (renovation, balcony, floor). I'll match it more precisely.\n\nIf you have photos of flats you "
                "like, send them: I'll find similar ones 📷",
                "<b>თქვენი პროფილი</b> ✅\n{p}\n\n✍️ საკუთარი სიტყვებით დაწერეთ, როგორ ბინას ეძებთ: სად, რასთან ახლოს, რა "
                "არის მნიშვნელოვანი (რემონტი, აივანი, სართული). უფრო ზუსტად შეგირჩევთ.\n\nთუ გაქვთ თქვენთვის სასურველი ბინების "
                "ფოტოები, გამომიგზავნეთ: მსგავს სტილს შეგირჩევთ 📷"),
    "back": ("С возвращением! 👋", "Welcome back! 👋", "კეთილი იყოს თქვენი დაბრუნება! 👋"),
    "show": ("🔍 Показать варианты", "🔍 Show options", "🔍 ვარიანტების ნახვა"),
    "edit": ("✏️ Изменить профиль", "✏️ Edit profile", "✏️ პროფილის შეცვლა"),
}


def q(cl, key, **kw):
    s = texts.pick(L(cl), *Q[key])
    return s.format(**kw) if kw else s


def selected(cl, step):
    p = cl.get("profile") or {}
    if step == "beds":
        return [str(b) for b in p.get("bedrooms") or [] if int(b) <= 3]
    if step == "dist":
        return list(p.get("districts") or [])
    return [str(x) for x in p.get(MULTI.get(step, step)) or []]


def question_kb(cl, step):
    if step == "place":
        return markup([[cb(q(cl, "map"), "q:place:map")], [cb(q(cl, "districts"), "q:place:districts")], [cb(q(cl, "any"), "q:place:any")]])
    if step == "dist":
        sel = selected(cl, "dist")
        btns = [cb(("✓ " if d[0] in sel else "") + texts.district_name(d[0], L(cl)), f"q:dist:{n}") for n, d in enumerate(texts.DISTRICTS)]
        return markup([btns[i:i + 3] for i in range(0, len(btns), 3)] + [[cb(q(cl, "done"), "q:dist:done")]])
    sel = selected(cl, step) if step in MULTI else []
    btns = [cb(("✓ " if code in sel else "") + lab[L(cl)], f"q:{step}:{n}") for n, (code, lab) in enumerate(OPTS[step])]
    rows = [btns[:2], btns[2:]] if step in ("pets", "bud") else [btns]
    if step == "bud":
        rows.append([cb(q(cl, "own"), "q:bud:own")])
    if step in MULTI:
        rows.append([cb(q(cl, "done"), f"q:{step}:done")])
    return markup(rows)


ICON = {"res": "👥", "pets": "🐾", "terms": "🕐", "bud": "💰", "beds": "🛏", "place": "📍"}


def answer_of(cl, step):
    """A finished step as the summary at the top of the questionnaire message shows it."""
    p, lang = cl.get("profile") or {}, L(cl)
    return {"res": lambda: p.get("residents") and texts._people(p["residents"], lang),
            "pets": lambda: p.get("pets") and texts.pets_text(p["pets"], lang),
            "terms": lambda: p.get("terms") and texts.terms_text(p["terms"], lang),
            "bud": lambda: texts.budget_text(p, lang),
            "beds": lambda: p.get("bedrooms") and texts._bedrooms(p["bedrooms"], lang),
            "place": lambda: texts.place_text(p, lang)}[step]() or "—"


def question_text(cl, step, note=None):
    """✓ answers of the steps before `step`, then the current question (+ the picked options, + a note)."""
    cur = "place" if step == "dist" else step
    done = [f"✓ {ICON[s]} {e(answer_of(cl, s))}" for s in STEPS[:STEPS.index(cur)]]
    t = q(cl, step)
    if step in MULTI or step == "dist":
        labels = selected_labels(cl, step)
        if labels:
            t += "\n<i>" + e(q(cl, "picked", x=", ".join(labels))) + "</i>"
    if note:
        t += "\n\n" + note
    return "\n".join(done) + ("\n\n" if done else "") + t


def selected_labels(cl, step):
    sel = selected(cl, step)
    if step == "dist":
        return [texts.district_name(d, L(cl)) for d in sel]
    return [lab[L(cl)] for code, lab in OPTS[step] if code in sel]


async def render(bot, cl, text, mk, query=None):
    """The questionnaire message shows `text`: the tapped message (query) or clients.state["qmsg"] is edited; only when
    there is none (or it is gone) a new message is sent and remembered."""
    chat = int(cl["chat_id"])
    mid = query.message.message_id if query and query.message else cl["state"].get("qmsg")
    if mid:
        try:
            await bot.edit_message_text(text, chat, mid, parse_mode="HTML", reply_markup=mk)
            if cl["state"].get("qmsg") != mid:
                await th(core.update, cl, lambda st, r: st.update(qmsg=mid))
            return
        except BadRequest as ex:
            if "not modified" in str(ex).lower():
                return
            log.info("questionnaire message %s: %s, sending a new one", mid, ex)
    m = await bot.send_message(chat, text, parse_mode="HTML", reply_markup=mk)
    await th(core.update, cl, lambda st, r: st.update(qmsg=m.message_id))


async def ask(bot, cl, step, query=None, note=None):
    await render(bot, cl, question_text(cl, step, note), question_kb(cl, step), query)


async def start_questionnaire(bot, cl, name):
    await th(set_done, cl, False)
    await th(core.update, cl, lambda st, r: st.update(qmsg=None))  # a fresh questionnaire message
    await bot.send_message(int(cl["chat_id"]), q(cl, "intro", name=e(name or "")).replace(", !", "!"))
    await ask(bot, cl, "res")


def summary_kb(cl):
    return markup([[cb(q(cl, "show"), "q:go")], [cb(q(cl, "edit"), "q:edit")], [cb(core.t(cl["lang"], "lang_btn"), "lang:menu")]])


def summary_text(cl, back=False):
    return (q(cl, "back") + "\n\n" if back else "") + q(cl, "summary", p=e(texts.profile_summary(cl.get("profile") or {}, L(cl))))


async def summary(bot, cl, back=False):
    await bot.send_message(int(cl["chat_id"]), summary_text(cl, back), parse_mode="HTML", reply_markup=summary_kb(cl))


async def finish(bot, cl, query=None):
    """A2a: the questionnaire message becomes the profile summary with «Показать варианты» / «Изменить» / «Язык»."""
    await th(set_done, cl, True)
    await th(set_await, cl)
    await render(bot, cl, summary_text(cl), summary_kb(cl), query)
    await th(core.update, cl, lambda st, r: st.update(qmsg=None))


def next_step(step):
    return STEPS[STEPS.index(step) + 1] if step in STEPS and step != "place" else None


BUDGET_CODES = {"0-500": (None, 500), "500-800": (500, 800), "800-1200": (800, 1200), "1200-": (1200, None)}


async def on_q(update: Update, ctx, cl):
    """q:<step>:<option index | done | own | map | districts | any>, q:go, q:edit. Every step edits the tapped message."""
    query = update.callback_query
    parts = query.data.split(":")
    bot, step, val = ctx.bot, parts[1], parts[2] if len(parts) > 2 else None
    if step == "go":
        await answer(query)
        from bot import schedule
        schedule(update, ctx, delay=0)
        return
    if step == "edit":
        await answer(query)
        await th(set_done, cl, False)
        await ask(bot, cl, "res", query)
        return
    if step in MULTI and val != "done":  # toggle; «Нет» (pets) excludes the rest
        code = OPTS[step][int(val)][0]
        key = MULTI[step]

        def toggle(p):
            cur = [str(x) for x in p.get(key) or []]
            if step == "beds":
                codes = ["3", "4"] if code == "3" else [code]
                cur = [c for c in cur if c not in codes] if code in cur else cur + codes
                p[key] = sorted({int(c) for c in cur})
                return
            cur = [c for c in cur if c != code] if code in cur else cur + [code]
            if step == "pets":
                cur = ["none"] if code == "none" and "none" in cur else [c for c in cur if c != "none"]
            p[key] = [c for c, _ in OPTS[step] if c in cur]
        await th(save_profile, cl, toggle)
        await answer(query)
        await th(load_profile, cl)  # render the latest state: a concurrent tap may have saved after us
        await ask(bot, cl, step, query)
        return
    if step == "dist" and val != "done":
        name = texts.DISTRICTS[int(val)][0]
        await th(save_profile, cl, lambda p: p.update(districts=[d for d in p.get("districts") or [] if d != name and d != "any"]
                                                      + ([] if name in (p.get("districts") or []) else [name]), pin=None))
        await answer(query)
        await th(load_profile, cl)
        await ask(bot, cl, "dist", query)
        return
    await answer(query)
    if step in MULTI or step == "dist":  # Готово
        await th(load_profile, cl)
        if step == "dist" and not selected_labels(cl, step):
            await th(save_profile, cl, lambda p: p.update(districts=["any"], pin=None))
        return await finish(bot, cl, query) if step == "dist" else await ask(bot, cl, next_step(step), query)
    if step == "res":
        code = OPTS["res"][int(val)][0]
        await th(save_profile, cl, lambda p: p.update(residents=code))
        return await ask(bot, cl, "pets", query)
    if step == "bud":
        if val == "own":
            await th(set_await, cl, "budget")
            return await ask(bot, cl, "bud", query, note=q(cl, "own_ask"))
        code = OPTS["bud"][int(val)][0]
        lo, hi = BUDGET_CODES[code]
        await th(save_profile, cl, lambda p: p.update(budget_min=lo, budget_max=hi))
        return await ask(bot, cl, "beds", query)
    if step == "place":
        if val == "any":
            await th(save_profile, cl, lambda p: p.update(districts=["any"], pin=None, radius_km=None))
            return await finish(bot, cl, query)
        if val == "districts":
            return await ask(bot, cl, "dist", query)
        await th(set_await, cl, "pin")
        from bot import map_kb  # the map is a reply-keyboard button: it can only come with a new message
        await bot.send_message(int(cl["chat_id"]), q(cl, "map_hint"), reply_markup=map_kb(cl["lang"]))


NUM = r"(\d+(?:[.,]\d+)?)\s*(k|к|тыс\.?)?"


def parse_budget(text):
    """Free-text budget (A1b) -> (min, max) in $: «до 900», «около 1000$», «от 700 до 1000», «800-1200», «2000 лари», «900-მდე»."""
    t = (text or "").lower().replace(" ", " ")
    t = re.sub(r"(\d)\s(\d{3})\b", r"\1\2", t)  # 1 000 -> 1000
    t = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", t)  # $1,200 -> 1200 (a comma before exactly 3 digits: thousands)
    nums = [float(n.replace(",", ".")) * (1000 if k else 1) for n, k in re.findall(NUM, t)]
    if not nums:
        return None
    if re.search(r"лар|gel|₾|lari|ლარ", t):
        nums = [n / 2.7 for n in nums]
    nums = [int(round(n)) for n in nums]
    if any(n < 50 or n > 100000 for n in nums):
        return None
    if len(nums) >= 2:
        return min(nums[:2]), max(nums[:2])
    n = nums[0]
    if re.search(r"\bот\b|\bfrom\b|\bmin|не меньше|at least|\+\s*$|больше|დან|მინიმუმ", t):
        return n, None
    return None, n  # «до», «up to», «около», «about», a bare number: the ceiling (the search allows +10%)


async def on_budget_text(bot, cl, text):
    """The client's typed amount (A1b): the questionnaire message moves to the bedrooms step; nothing is deleted."""
    got = parse_budget(text)
    if not got:
        await bot.send_message(int(cl["chat_id"]), q(cl, "own_bad"))
        return
    await th(save_profile, cl, lambda p: p.update(budget_min=got[0], budget_max=got[1]))
    await th(set_await, cl)
    await ask(bot, cl, "beds")


async def on_pin(bot, cl, lat, lng, radius, label):
    """A1c map point (Mini App map / location) while the questionnaire waits for it: the questionnaire message becomes
    the profile summary."""
    await th(save_profile, cl, lambda p: p.update(pin={"lat": lat, "lng": lng, "label": label}, radius_km=radius or 2.0, districts=[]))
    await finish(bot, cl)


async def on_start(update: Update, ctx, cl):
    """/start: questionnaire once (A1a), afterwards the saved profile with «Показать варианты» / «Изменить»."""
    if cl.get("profile_done"):
        await summary(ctx.bot, cl, back=True)
    else:
        await start_questionnaire(ctx.bot, cl, update.effective_user.first_name)


# ---------- A2b/A3: the card and ❤️ ----------

def remember_card(cl, listing_id, chat_id, message_id, text, rows, search_id=None):
    """The sent card (text + extra button rows) so notify.lead_client_ack can rewrite it (A3); logged in client_views."""
    def fn(st, r):
        cards = st.setdefault("cards", {})
        cards.pop(listing_id, None)
        cards[listing_id] = {"text": text, "rows": rows}
        for k in list(cards)[:-30]:
            cards.pop(k)
    core.update(cl, fn)
    core.remember_message("card", f"{cl['id']}:{listing_id}", chat_id, message_id)
    core.log_view(cl, listing_id, search_id)


def request_of(cl):
    """What the client wrote (the questionnaire goes to the lead card separately); None if only the profile/photos."""
    msgs = cl["state"].get("msgs") or []
    return " / ".join(msgs[-3:]) or (cl["state"].get("images") and "по фото") or None


async def on_like(update: Update, ctx, cl, lid):
    from app.services import leads
    query = update.callback_query
    async with lock_for(("like", cl["id"], lid)):  # a double tap: one lead, one A3 edit
        res = await th(core.react, cl, lid, "like")
        if res is None:
            await answer(query, core.t(cl["lang"], "gone"))
            return
        # the lead first (B1 card + A3 edit of exactly the tapped card, via notify.tapped_card), then the toast
        knew = await th(notify.knows_operator, cl["id"])
        tok = notify.tapped_card.set((query.message.chat.id, query.message.message_id) if query.message else None)
        try:
            lead, err = await svc(leads.create_from_like, cl["id"], lid, request_of(cl))
        finally:
            notify.tapped_card.reset(tok)
        await answer(query, core.t(cl["lang"], "gone" if err else "liked"))
        if not err and not knew:  # the first ❤️: the operator's account writes first (or the client gets the DM link)
            await th(notify.operator_intro, lead)
    if err:
        log.warning("create_from_like %s/%s: %s", cl["id"], lid, err)
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass


async def on_post_start(update: Update, ctx, cl, pid: int):
    """/start p<object id>: «❤️ I like it» / «💬 Contact» under a channel rich post (Irakli 2026-11-09) -> the same lead
    as ❤️ on a card in the bot (B1 card for operators, the operator's intro), then «передал агенту»."""
    from app.models import Property
    from app.services import leads
    lid = await th(_run, lambda s: (s.get(Property, pid) or Property()).listing_id, (), {})
    if not lid:
        await ctx.bot.send_message(update.effective_chat.id, core.t(cl["lang"], "gone"))
        return
    async with lock_for(("like", cl["id"], lid)):
        knew = await th(notify.knows_operator, cl["id"])
        lead, err = await svc(leads.create_from_like, cl["id"], lid, request_of(cl))
    if err:
        log.warning("post start %s/%s: %s", cl["id"], lid, err)
        await ctx.bot.send_message(update.effective_chat.id, core.t(cl["lang"], "gone"))
        return
    await ctx.bot.send_message(update.effective_chat.id, core.t(cl["lang"], "lead_sent"))
    if not knew:
        await th(notify.operator_intro, lead)


# ---------- B1/B3: the operators group ----------

async def on_group(update: Update, ctx):
    """lg:take:<lead> | lg:assign:<lead> | lg:assign:<lead>:<agent> | lg:back:<lead>."""
    from app.services import leads
    query = update.callback_query
    parts = query.data.split(":")
    act, lead_id = parts[1], int(parts[2])
    tg_id = update.effective_user.id
    await bind(update.effective_user)
    me = await user_of(tg_id)
    if not me or me.role not in STAFF:
        await answer(query, "Только для операторов: попросите администратора добавить вас в CRM", show_alert=True)
        return
    if act == "take":
        _, err = await svc(_with_user(lambda s, u: leads.operator_take(s, lead_id, u)), tg_id)
        if err == "already_taken":
            op = await th(user_row, ((await th(lead_row, lead_id)) or {}).get("operator_id"))
            await answer(query, f"Уже взяла {notify.staff_name(op)}", show_alert=True)
        else:
            await answer(query, "Заявка ваша ✋" if not err else f"Ошибка: {err}", show_alert=bool(err))
        return
    if act == "back":
        await answer(query)
        private = bool(query.message and query.message.chat.type == "private")
        await query.edit_message_reply_markup(markup(notify.group_kb({"id": lead_id}, "taken")(private)["inline_keyboard"]))
        return
    if act == "assign" and len(parts) == 3:
        recs, err = await svc(leads.recommend_agents, lead_id)
        if err or not recs:
            await answer(query, "Нет активных агентов" if not err else f"Ошибка: {err}", show_alert=True)
            return
        await answer(query)
        rows = [[cb(("⭐ " if r.get("recommended") else "") + f"{r['agent']['name']} · " + ", ".join(r["reasons"][:2]),
                    f"lg:assign:{lead_id}:{r['agent']['id']}")] for r in recs[:8]]
        private = bool(query.message and query.message.chat.type == "private")
        rows += [[cb("⬅️ Назад", f"lg:back:{lead_id}")], [tgc.app_btn("📋 Открыть в панели", links.lead_app(lead_id), private)]]
        await query.edit_message_reply_markup(markup(rows))
        return
    if act == "assign":
        agent_id = int(parts[3])
        ld = await th(lead_row, lead_id) or {}
        fn = leads.reassign if ld.get("agent_id") and ld.get("status") not in ("new", "operator_taken", "agent_assigned") else leads.assign_agent
        _, err = await svc(_with_user(lambda s, u: fn(s, lead_id, agent_id, u)), tg_id)
        ag = await th(user_row, agent_id) or {}
        await answer(query, f"Назначен агент {ag.get('name')}" if not err else f"Ошибка: {err}", show_alert=bool(err))


# ---------- C7: the agent enters free time in the bot ----------

QUICK_HOURS = ("10:00", "12:00", "14:00", "16:00", "18:00", "20:00")


def now_local():
    return datetime.now(timezone.utc).astimezone(texts.tz())


def quick_kb(lead_id, chosen=(), now=None):
    """Today / tomorrow / the day after: hour buttons (ls:t:<lead>:<YYMMDDHHMM>), ✓ = picked."""
    now = now or now_local()
    rows = []
    for d in range(3):
        day = (now + timedelta(days=d)).date()
        btns = []
        for hm in QUICK_HOURS:
            at = datetime.combine(day, datetime.strptime(hm, "%H:%M").time(), texts.tz())
            if at < now + timedelta(minutes=30):
                continue
            code = at.strftime("%y%m%d%H%M")
            data = f"ls:t:{lead_id}:{code}"
            btns.append(cb(("✓ " if data in chosen else "") + texts.slot_label(at, "ru", now), data))
        rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    n = len(chosen)
    rows.append([cb(f"📤 Отправить клиенту · {n}" if n else "📤 Отправить клиенту", f"ls:send:{lead_id}")])
    rows.append([cb("✍️ Написать время текстом", f"ls:text:{lead_id}")])
    return markup(rows)


WEEKDAY_RE = {"пн": 0, "понед": 0, "mon": 0, "вт": 1, "втор": 1, "tue": 1, "ср": 2, "сред": 2, "wed": 2, "чт": 3, "четв": 3, "thu": 3,
              "пт": 4, "пятн": 4, "fri": 4, "сб": 5, "субб": 5, "sat": 5, "вс": 6, "воскр": 6, "sun": 6}


DATE_RE = re.compile(r"(?<![\d:.])(\d{1,2})([./])(\d{1,2})(?:[./](\d{2,4}))?(?![\d:])")
TIME_RE = re.compile(r"(?:\b(в|к|at|by)\s+)?(?<![\d:.])([01]?\d|2[0-3])(?:[:.]([0-5]\d))?(?!\d)"
                     r"\s*(a\.?m\.?|p\.?m\.?|ч|h)?(?![a-zа-я])")
SLOT_TTL = 30 * 60  # seconds the bot waits for the agent's typed slot times


def _times(c):
    """(hour, minute) of every time in a chunk: «18:00», «18.30», «в 11», «15h», «11 ч», «6 pm», «at 6:30 am».
    A bare number counts only with a preposition or a suffix (so «2 спальни» is not 02:00)."""
    out = []
    for prep, hh, mm, suf in TIME_RE.findall(c):
        if not (mm or suf or prep):
            continue
        h, m = int(hh), int(mm or 0)
        if suf.startswith("p") and h < 12:
            h += 12
        elif suf.startswith("a") and h == 12:
            h = 0
        out.append((h, m))
    return out


def _date(now, d, mo, y=None):
    """A calendar date or None; without a year a date already past rolls to next year."""
    try:
        if y:
            return date(int(y) + (2000 if len(y) == 2 else 0), mo, d)
        got = date(now.year, mo, d)
        return got if got >= now.date() else date(now.year + 1, mo, d)
    except ValueError:
        return None


def parse_slots(text, now=None):
    """«сегодня 18:00, завтра 12:00 и 17:30», «пт 24.10 12:00», «25.10 в 11», «пт 18.30», «tomorrow at 6 pm» -> aware
    datetimes (agency TZ), future only. A day word carries over to the following times; no day = today (or tomorrow if
    that time has passed). «D.M» is a date unless it reads as a time and no other time is in the chunk («18.30», «18.00»);
    an impossible date («31.02») is skipped and the chunk's times still count."""
    now = now or now_local()
    day, out = None, []
    for chunk in re.split(r"[,;\n]+|\s+и\s+|\s+and\s+", (text or "").lower()):
        c = chunk.strip()
        if not c:
            continue
        got_day = None
        if re.search(r"послезавтра|day after tomorrow", c):
            got_day = now.date() + timedelta(days=2)
        elif re.search(r"завтра|tomorrow", c):
            got_day = now.date() + timedelta(days=1)
        elif re.search(r"сегодня|today", c):
            got_day = now.date()
        for m in list(DATE_RE.finditer(c)):
            d, sep, mo, y = int(m[1]), m[2], int(m[3]), m[4]
            as_time = sep == "." and not y and len(m[3]) == 2 and d <= 23 and mo <= 59
            rest = c[:m.start()] + " " + c[m.end():]
            dt = _date(now, d, mo, y)
            if dt and (not as_time or _times(rest)):
                got_day = got_day or dt
                c = rest
            elif not as_time:  # neither a date nor a time: drop the token, keep the rest of the chunk
                c = rest
        if got_day is None:
            for w, n in WEEKDAY_RE.items():
                if re.search(rf"\b{w}", c):
                    got_day = now.date() + timedelta(days=(n - now.weekday()) % 7 or 7)
                    break
        day = got_day or day
        for hh, mm in _times(c):
            d = day or now.date()
            at = datetime.combine(d, datetime.min.time(), texts.tz()).replace(hour=hh, minute=mm)
            if day is None and at < now:
                at += timedelta(days=1)
            if at > now:
                out.append(at)
    return sorted(set(out))


def _same_slots_sent(lead_id, starts):
    """The lead already has exactly these open slots sent (a double tap / a repeated message)."""
    with db.tx() as c:
        r = c.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not r or r[0] != "slots_sent":
            return False
        have = [x[0] for x in c.execute("SELECT starts_at FROM lead_slots WHERE lead_id = ? AND NOT chosen", (lead_id,))]
    ts = lambda v: (datetime.fromisoformat(v) if isinstance(v, str) else v).timestamp()  # noqa: E731
    return {ts(x) for x in have} == {ts(x) for x in starts}


async def send_slots(update, ctx, me_tg, lead_id, starts):
    """-> (done, message): done=None when nothing was parsed (keep waiting), else True/False."""
    from app.services import leads
    if not starts:
        return None, "Не понял время 🙈 Пример: «сегодня 18:00, завтра 12:00». /cancel — отменить"
    label = ", ".join(texts.slot_label(s, "ru") for s in starts)
    async with lock_for(("slots", lead_id)):  # «📤 Отправить» tapped twice: the client gets the slots once
        if await th(_same_slots_sent, lead_id, starts):
            return True, "✅ Уже отправлено клиенту: " + label
        _, err = await svc(_with_user(lambda s, u: leads.add_slots(s, lead_id, starts, u)), me_tg)
    if err:
        return False, {"bad_status": "Заявка уже на другом шаге", "forbidden": "Это не ваша заявка",
                       "userbot_off": "⚠️ Userbot оператора не подключён: время клиенту не отправлено. Сообщите оператору."}.get(err, f"Ошибка: {err}")
    return True, "✅ Отправил клиенту: " + label


async def on_agent_slots(update: Update, ctx, parts):
    query = update.callback_query
    act, lead_id = parts[1], int(parts[2])
    await bind(update.effective_user)
    me = await user_of(update.effective_user.id)
    ld = await th(lead_row, lead_id)
    if not me or not ld or (ld["agent_id"] != me.id and me.role not in STAFF):
        await answer(query, "Это не ваша заявка", show_alert=True)
        return
    if act == "quick":
        await answer(query)
        await ctx.bot.send_message(query.message.chat.id, f"<b>Заявка #{lead_id}</b> · когда собственник может показать?\n"
                                   "Выберите 2-3 варианта, клиент выберет одним нажатием.", parse_mode="HTML", reply_markup=quick_kb(lead_id))
    elif act == "t":
        rows = query.message.reply_markup.inline_keyboard if query.message.reply_markup else []
        chosen = set(checked(rows))
        chosen ^= {query.data}
        await answer(query)
        try:
            await query.edit_message_reply_markup(quick_kb(lead_id, chosen))
        except BadRequest as ex:
            if "not modified" not in str(ex).lower():
                raise
    elif act == "send":
        rows = query.message.reply_markup.inline_keyboard if query.message.reply_markup else []
        starts = [datetime.strptime(d.rsplit(":", 1)[1], "%y%m%d%H%M").replace(tzinfo=texts.tz()) for d in checked(rows)]
        if not starts:
            await answer(query, "Отметьте хотя бы одно время", show_alert=True)
            return
        _, msg = await send_slots(update, ctx, update.effective_user.id, lead_id, starts)
        await answer(query)
        await edit_text(query, msg)  # no reply_markup: the keyboard goes away
    elif act == "text":
        ctx.user_data["slots_lead"] = {"id": lead_id, "at": time.monotonic()}
        await answer(query)
        await ctx.bot.send_message(query.message.chat.id, f"Напишите свободное время для заявки #{lead_id}, например:\n"
                                   "«сегодня 18:00, завтра 12:00 и 17:30»\n/cancel — отменить")


def slots_waiting(ctx):
    """The lead id the bot waits typed slot times for, or None (expired after SLOT_TTL)."""
    w = ctx.user_data.get("slots_lead")
    if isinstance(w, int):  # an older process's format
        w = {"id": w, "at": time.monotonic()}
    if not w or time.monotonic() - w.get("at", 0) > SLOT_TTL:
        ctx.user_data.pop("slots_lead", None)
        return None
    return w["id"]


async def on_staff_text(update: Update, ctx):
    """Free text from staff that the bot is waiting for (slot times). -> True if handled."""
    lead_id = slots_waiting(ctx)
    text = update.effective_message and update.effective_message.text
    if not lead_id or not text:
        return False
    done, msg = await send_slots(update, ctx, update.effective_user.id, lead_id, parse_slots(text))
    if done is not None:  # sent or failed for good (wrong step / not yours): stop waiting; unparsed text: ask again
        ctx.user_data.pop("slots_lead", None)
    await update.effective_message.reply_text(msg)
    return True


async def on_cancel(update: Update, ctx, cl):
    """/cancel: stop waiting for typed slot times (staff) or for a free-text answer (client)."""
    had = ctx.user_data.pop("slots_lead", None)
    if not had and awaiting(cl)[0]:
        await th(set_await, cl)
        had = True
    await update.effective_message.reply_text(tr(cl, "Отменено 👌", "Cancelled 👌", "გაუქმდა 👌") if had else tr(cl, "Нечего отменять.", "Nothing to cancel.", "გასაუქმებელი არაფერია."))


# ---------- A4/A5/A6: client buttons ----------

OUTCOME_REPLY = {
    "rented": ("Отлично, поздравляем! 🎉\nАгент {agent} поможет с договором. Оператор проверит сделку и пришлёт подтверждение.\n\n"
               "Через 14 дней спрошу, всё ли хорошо в новой квартире.",
               "Great, congratulations! 🎉\nAgent {agent} will help with the contract. An operator will check the deal and confirm.\n\n"
               "In 14 days I'll ask if everything is fine in the new flat.",
               "მშვენიერია, გილოცავთ! 🎉\nაგენტი {agent} ხელშეკრულებაში დაგეხმარებათ. ოპერატორი გარიგებას შეამოწმებს და დადასტურებას გამოგიგზავნით.\n\n"
               "14 დღეში გკითხავთ, ყველაფერი რიგზეა თუ არა ახალ ბინაში."),
    "thinking": ("Хорошо, подумайте 🙂 Напомню завтра.", "Sure, take your time 🙂 I'll remind you tomorrow.",
                 "კარგი, დაფიქრდით 🙂 ხვალ შეგახსენებთ."),
}
REASON_REPLY = {
    "price": ("Понял, учту цену. Подобрать похожие квартиры дешевле?", "Got it, I'll keep the price in mind. Find similar cheaper flats?",
              "გასაგებია, ფასს გავითვალისწინებ. შეგირჩიოთ მსგავსი, უფრო იაფი ბინები?"),
    "location": ("Понял, учту район. Подобрать похожие в другом месте?", "Got it. Find similar flats in another area?",
                 "გასაგებია, უბანს გავითვალისწინებ. შეგირჩიოთ მსგავსი ბინები სხვა ადგილას?"),
    "condition": ("Понял, учту состояние. Подобрать похожие в лучшем состоянии?", "Got it. Find similar flats in better condition?",
                  "გასაგებია, მდგომარეობას გავითვალისწინებ. შეგირჩიოთ მსგავსი ბინები უკეთეს მდგომარეობაში?"),
    "other": ("Понял, учту. Подобрать похожие квартиры?", "Got it. Shall I find similar flats?",
              "გასაგებია, გავითვალისწინებ. შეგირჩიოთ მსგავსი ბინები?"),
}
# what goes into the search request (the model reads any language; the ka client gets the English hint)
REASON_MSG = {"price": ("хочу дешевле, чем ${p}", "cheaper than ${p}", "cheaper than ${p}"),
              "location": ("другой район, не {d}", "another area, not {d}", "another area, not {d}"),
              "condition": ("в лучшем состоянии, с хорошим ремонтом", "better condition, good renovation", "better condition, good renovation"),
              "other": (None, None, None)}


def my_lead(cl, lead_id):
    ld = lead_row(lead_id)
    return ld if ld and ld["client_id"] == cl["id"] else None


async def mark_choice(query, label):
    try:
        await query.edit_message_reply_markup(markup([[cb("✓ " + label, "noop")]]))
    except TelegramError:
        pass


def pressed_label(query):
    rows = query.message.reply_markup.inline_keyboard if query.message and query.message.reply_markup else []
    return next((b.text for row in rows for b in row if b.callback_data == query.data), "")


def label_of(query, cl):
    """The pressed button's text: from the message's keyboard, or (an inline message: Telegram doesn't send it) from
    the card's own labels (notify.REMIND / SURVEY, texts.REFUSAL)."""
    if got := pressed_label(query):
        return got
    p, lang = query.data.split(":"), L(cl)
    if p[0] == "lv":
        return notify.REMIND[lang][{"ok": 1, "late": 2, "resched": 3}[p[1]]]
    if p[0] == "lo":
        return notify.SURVEY[lang][{"rented": 1, "refused": 2, "thinking": 3}.get(p[3], 2)]
    if p[0] == "lr":
        return texts.REFUSAL.get(p[2], texts.REFUSAL["other"])[lang]
    return ""


async def edit_card(query, text, rows=None):
    """The pressed card (an inline message from the operator's account, or an older bot message) shows `text` and
    `rows` (None: no buttons); «not modified», a photo card or a gone message are not errors."""
    try:
        await edit_text(query, text, parse_mode="HTML", reply_markup=markup(rows) if rows else None)
    except TelegramError as ex:
        log.info("edit %s: %s", query.inline_message_id or (query.message and query.message.message_id), ex)


def _act(s, data, client_id, lang):
    """leads.client_action (only the lead's client) -> (lead id, the chosen time's label for ls:pick)."""
    from app.services import leads
    lead = leads.client_action(s, data, client_id)
    return lead.id, texts.slot_label(lead.viewing_at, lang) if data.startswith("ls:pick") else ""


def card_text(kind, lead_id):
    """The card's current HTML text (an inline message's text doesn't come with the press)."""
    return (notify.CARDS[kind](lead_id) or ("", "", None))[1]


CHOSEN = ("✅ Вы выбрали: {when}", "✅ You chose: {when}", "✅ თქვენ აირჩიეთ: {when}")
NOT_YOURS = ("Это не ваша заявка", "This is not your request", "ეს თქვენი განაცხადი არ არის")
OWN_ASK = ("Напишите удобное время ответным сообщением, например «завтра после 18:00». Передам агенту.",
           "Reply with a time that suits you, e.g. «tomorrow after 6 pm». I'll pass it to the agent.",
           "უპასუხეთ თქვენთვის მოსახერხებელი დროით, მაგალითად „ხვალ 18:00-ის შემდეგ“. აგენტს გადავცემ.")
COMMENT_ASK = ("Если хотите, напишите ответным сообщением, что не подошло: передам агенту.",
               "If you like, reply with what didn't fit: I'll pass it to the agent.",
               "თუ გსურთ, უპასუხეთ, რა არ მოგეწონათ: აგენტს გადავცემ.")


async def on_client_journey(update: Update, ctx, cl):
    """A client's press on a card from the operator's account (an inline message: inline_message_id, no chat) or on an
    older bot message: ls:pick / ls:own / lv:* / lo:c:* / lr:* / t14:*. Recording: leads.client_action (the same as a
    number typed in the operator's DM, userbot.dm_answer); then the card itself is edited."""
    query = update.callback_query
    parts = query.data.split(":")
    kind = parts[0]
    if kind == "t14":
        return await on_t14(update, ctx, cl, parts)
    lead_id = None if kind == "ls" and parts[1] == "pick" else int(parts[2] if kind in ("ls", "lv", "lo") else parts[1])
    if lead_id is not None and not await th(my_lead, cl, lead_id):
        await answer(query, texts.pick(L(cl), *NOT_YOURS), show_alert=True)
        return
    if kind == "ls" and parts[1] == "own":  # «Другое время»: the client writes it to the operator's DM (dm_choice.own)
        await th(userbot.set_state, cl["id"], dm_choice={"lead": lead_id, "own": True})
        await answer(query)
        await edit_card(query, texts.pick(L(cl), *OWN_ASK))
        return
    if kind == "lr" and not first_tap(query, "lr"):  # a double tap (or a second reason on the same card): one answer
        await answer(query)
        return
    label = label_of(query, cl)
    res, err = await svc(_act, query.data, cl["id"], L(cl))  # notify: C8 contacts, staff updates, the next card
    if err:
        await answer(query, texts.pick(L(cl), *NOT_YOURS) if err == "forbidden" else
                     tr(cl, "Это время уже недоступно", "This time is no longer available", "ეს დრო უკვე აღარ არის ხელმისაწვდომი")
                     if kind == "ls" else tr(cl, "Уже нельзя изменить, напишите оператору.", "Can't change this now, please write to the operator.",
                                             "ამის შეცვლა უკვე შეუძლებელია, მიწერეთ ოპერატორს."), show_alert=True)
        return
    await th(userbot.forget_choice, cl["id"], query.data)  # answered by the button: a typed number won't repeat it
    if kind == "ls":  # A4b: the details come as a DM from the operator; the card keeps «Опаздываю» / «Перенести»
        await answer(query, "✅")
        await edit_card(query, texts.pick(L(cl), *CHOSEN).format(when=res[1]), notify.viewing_rows(res[0], L(cl)))
        return
    ld = await th(lead_row, lead_id)
    if kind == "lv":
        if parts[1] == "ok":
            await answer(query, tr(cl, "Отлично, ждём вас 👍", "Great, see you there 👍", "მშვენიერია, გელოდებით 👍"))
            await edit_card(query, f"{await th(card_text, 'remind', lead_id)}\n\n✓ {e(label)}", notify.viewing_rows(lead_id, L(cl)))
        elif parts[1] == "late":
            await answer(query, tr(cl, "Передал агенту, что вы опаздываете 👌", "I told the agent you're running late 👌",
                                   "აგენტს გადავეცი, რომ აგვიანებთ 👌"), show_alert=True)
        else:
            await answer(query)
            await edit_card(query, tr(cl, "🔁 Передал агенту: он согласует с собственником новое время и пришлёт варианты 🙌",
                                      "🔁 I asked the agent: they'll agree a new time with the owner and send options 🙌",
                                      "🔁 აგენტს გადავეცი: ის მესაკუთრესთან ახალ დროს შეათანხმებს და ვარიანტებს გამოგიგზავნით 🙌"))
    elif kind == "lo":  # refused -> notify.refusal_reason_ask sends the reason card
        await answer(query)
        reply = OUTCOME_REPLY.get(parts[3])
        ag = await th(user_row, ld["agent_id"]) or {}
        await edit_card(query, f"{await th(card_text, 'survey', lead_id)}\n\n✓ {e(label)}" +
                        ("\n\n" + e(texts.pick(L(cl), *reply).format(agent=ag.get("name") or "")) if reply else ""))
    elif kind == "lr":
        reason = parts[2] if len(parts) > 2 and parts[2] in REASON_MSG else "other"
        await answer(query)
        i = await th(core.listing, ld["listing_id"]) or {}
        await th(core.react, cl, ld["listing_id"], "dislike")
        hint = texts.pick(L(cl), *REASON_MSG[reason])
        if hint:
            await th(core.add_input, cl, text=hint.format(p=i.get("price_usd") or "", d=i.get("district") or ""))
        text = f"{await th(card_text, 'reason', lead_id)}\n\n✓ {e(label)}\n\n" + e(texts.pick(L(cl), *REASON_REPLY[reason]))
        if reason == "other":  # the client's own words come as a DM to the operator (userbot: leads.client_comment)
            await th(userbot.set_state, cl["id"], dm_choice={"lead": lead_id, "comment": True})
            text += "\n\n" + e(texts.pick(L(cl), *COMMENT_ASK))
        await edit_card(query, text, [[cb(tr(cl, "🔄 Да, подобрать", "🔄 Yes, find", "🔄 დიახ, შემირჩიე"), "more")],
                                      [cb(tr(cl, "Пока нет", "Not now", "ჯერ არა"), "noop")]])


def _deal_client(deal_id):
    with db.tx() as c:
        r = c.execute("SELECT l.client_id FROM deals d JOIN leads l ON l.id = d.lead_id WHERE d.id = ?", (deal_id,)).fetchone()
    return r and r[0]


def _t14_scored(deal_id):
    """The client's T14 scores for this deal are already saved (the «problem» text is a separate evaluation)."""
    with db.tx() as c:
        return bool(c.execute("SELECT 1 FROM evaluations WHERE deal_id = ? AND kind = 't14' AND author = 'client' AND "
                              "(h2o_client IS NOT NULL OR h2o_owner IS NOT NULL OR h2o_process IS NOT NULL)", (deal_id,)).fetchone())


async def on_t14(update, ctx, cl, parts):
    """t14:<deal>:<1-3>:<0-3> -> the same card shows the next question; after the third an evaluation (author=client)
    and «Есть проблема» (t14:<deal>:problem -> the client describes it in the operator's DM, userbot dm_answer)."""
    from app.services import evaluations
    query = update.callback_query
    deal_id = int(parts[1])
    if await th(_deal_client, deal_id) != cl["id"]:
        await answer(query, texts.pick(L(cl), *NOT_YOURS), show_alert=True)
        return
    await answer(query)
    if not first_tap(query, "t14:" + parts[2]):  # a double tap on a score: one next question / one evaluation (one card)
        return
    if parts[2] == "problem":
        await th(userbot.set_state, cl["id"], dm_choice={"problem": deal_id})
        await edit_card(query, tr(cl, "Опишите, пожалуйста, проблему одним сообщением в этот чат, оператор ответит.",
                                  "Please describe the problem in one message in this chat, an operator will reply.",
                                  "გთხოვთ, აღწეროთ პრობლემა ერთი შეტყობინებით ამ ჩატში, ოპერატორი გიპასუხებთ."))
        return
    n, score = int(parts[2]), int(parts[3])
    await th(core.update, cl, lambda st, r: st.setdefault("t14", {}).setdefault(str(deal_id), {}).update({str(n): score}))
    if n < 3:
        text, mk = notify.t14_question(deal_id, n + 1, L(cl))
        await edit_card(query, f"✓ {n}: {score}\n\n{text}", mk["inline_keyboard"])
        return
    a = cl["state"]["t14"][str(deal_id)]
    async with lock_for(("t14", deal_id)):
        if await th(_t14_scored, deal_id):
            return
        await svc(evaluations.create, {"deal_id": deal_id, "kind": "t14", "author": "client", "h2o_client": a.get("1"),
                                       "h2o_owner": a.get("2"), "h2o_process": a.get("3")}, None)
    await th(userbot.forget_choice, cl["id"], "t14")
    await edit_card(query, tr(cl, "Спасибо! 🙏 0 = плохо, 3 = отлично. Если есть проблема, напишите, оператор ответит.",
                              "Thank you! 🙏 0 = bad, 3 = great. If there is a problem, write it, an operator will reply.",
                              "გმადლობთ! 🙏 0 = ცუდი, 3 = შესანიშნავი. თუ რამე პრობლემაა, მოგვწერეთ, ოპერატორი გიპასუხებთ."),
                    [[cb(tr(cl, "✍️ Есть проблема", "✍️ There is a problem", "✍️ პრობლემა მაქვს"), f"t14:{deal_id}:problem")]])


async def on_client_text(update: Update, ctx, cl):
    """Free text the client's state is waiting for in the bot (the budget). -> True if handled."""
    kind, _ = awaiting(cl)
    text = update.effective_message and update.effective_message.text
    if kind != "budget" or not text:
        return False
    await on_budget_text(ctx.bot, cl, text)
    return True


# ---------- C9a: agent outcome ----------

async def on_agent_outcome(update: Update, ctx, parts):
    from app.services import leads
    query = update.callback_query
    lead_id, outcome = int(parts[2]), parts[3]
    tg_id = update.effective_user.id
    await bind(update.effective_user)
    if not await user_of(tg_id):
        await answer(query, "Это не ваша заявка", show_alert=True)
        return
    _, err = await svc(_with_user(lambda s, u: leads.set_outcome(s, lead_id, "agent", outcome, actor=u)), tg_id)
    if err:
        await answer(query, {"forbidden": "Это не ваша заявка", "bad_status": "Заявка уже на другом шаге"}.get(err, err), show_alert=True)
        return
    await answer(query)
    await mark_choice(query, pressed_label(query))
    chat = query.message.chat.id
    if outcome == "rented":
        await ctx.bot.send_message(chat, "Отлично! Заполните сделку, это 1 минута 👇",
                                   reply_markup=markup([[tgc.app_btn("📝 Заполнить сделку", links.deal_app(lead_id=lead_id))]]))
    elif outcome == "refused":
        await ctx.bot.send_message(chat, "Понял. Клиенту предложим похожие варианты.")
    elif outcome == "thinking":
        await ctx.bot.send_message(chat, "Хорошо, напомним клиенту завтра.")


# ---------- client cards for the operator's userbot (inline mode) ----------

async def on_inline_query(update: Update, ctx):
    """«<kind>:<token>» (slots / remind / survey / reason / t14) from the operator's userbot (settings.userbot_tg_id)
    -> one personal article: the card's text and buttons (notify.card); the userbot sends it into the client's DM.
    Anyone else / an unknown token: no results."""
    iq = update.inline_query
    m = re.fullmatch(r"(\w+):([\w-]{6,32})", (iq.query or "").strip())
    card = None
    if m and str(iq.from_user.id) == (await th(store.setting, "userbot_tg_id") or ""):
        card = await th(notify.card, m[1], m[2])
    results = []
    if card:
        title, text, rows = card
        results = [InlineQueryResultArticle(m[2], title, InputTextMessageContent(text, parse_mode="HTML",
                                                                                 link_preview_options=LinkPreviewOptions(is_disabled=True)),
                                            reply_markup=markup(rows))]
    else:
        log.info("inline query %r from %s: no result", iq.query, iq.from_user.id)
    try:
        await iq.answer(results, cache_time=0, is_personal=True)
    except TelegramError as ex:
        log.warning("answerInlineQuery %r: %s", iq.query, ex)


# ---------- dispatch ----------

async def on_callback(update: Update, ctx, cl_of):
    """Journey callbacks -> True if handled."""
    data = update.callback_query.data or ""
    parts = data.split(":")
    if data.startswith("lg:"):
        await on_group(update, ctx)
    elif data.startswith("ls:") and parts[1] in ("quick", "t", "send", "text"):
        await on_agent_slots(update, ctx, parts)
    elif data.startswith("lo:a:"):
        await on_agent_outcome(update, ctx, parts)
    elif data.startswith("q:"):
        await on_q(update, ctx, await th(cl_of))
    elif data.startswith(("ls:", "lv:", "lo:c:", "lr:", "t14:")):
        await on_client_journey(update, ctx, await th(cl_of))
    else:
        return False
    return True
