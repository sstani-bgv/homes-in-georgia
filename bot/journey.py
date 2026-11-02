"""The client journey in the bot (docs/user-journey.txt): questionnaire A1a-A2a (q:), ❤️ → lead (A3), the operators
group (B1, lg:), the agent's quick slots (C7, ls:quick/t/send/text), the client's slot pick and viewing buttons (A4, ls:pick/own,
lv:), outcomes (A5/C9a, lo:, lr:) and T14 (A6, t14:). Business rules live in the backend services (app.services.*);
outgoing cards in app.telegram.notify. Client transient state: clients.state["await"]; staff: ctx.user_data."""
import asyncio
import html
import logging
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from telegram import Update
from telegram.error import BadRequest, TelegramError

import core
import db
from app.telegram import client as tgc
from app.telegram import links, notify, texts
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


def tr(cl, ru, en):
    return en if L(cl) == "en" else ru


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
    with db.tx() as c:
        c.execute("UPDATE clients SET profile_done = ? WHERE id = ?", (int(done), cl["id"]))
    cl["profile_done"] = int(done)


def checked(rows):
    """Buttons of a PTB keyboard marked ✓ -> their callback data."""
    return [b.callback_data for row in rows for b in row if b.text.startswith("✓ ") and b.callback_data]


def only_chosen(chosen):
    """A finished question keeps just the chosen answers (journey screens: «✓ 6 мес», «✓ 12 мес»)."""
    return markup([[cb("✓ " + x, "noop") for x in chosen[i:i + 3]] for i in range(0, len(chosen), 3)] or [[cb("✓", "noop")]])


# ---------- A1a-A2a questionnaire ----------

STEPS = ("res", "pets", "terms", "bud", "beds", "place")
OPTS = {"res": texts.RESIDENTS, "pets": texts.PETS, "terms": texts.TERMS, "bud": texts.BUDGETS, "beds": texts.BEDROOMS}
MULTI = {"pets": "pets", "terms": "terms", "beds": "bedrooms"}
Q = {
    "intro": ("Здравствуйте, {name}! 👋\nЯ помогу найти квартиру в Тбилиси без комиссии.\n\nОтветьте на 6 коротких вопросов, это займёт "
              "минуту. Где подходит несколько ответов, выберите все и нажмите «Готово».",
              "Hello, {name}! 👋\nI'll help you find a flat in Tbilisi with no commission.\n\nAnswer 6 short questions, it takes a minute. "
              "Where several answers fit, pick them all and tap «Done»."),
    "res": ("<b>1 из 6.</b> Сколько человек будет жить?", "<b>1 of 6.</b> How many people will live there?"),
    "pets": ("<b>2 из 6.</b> Будут ли животные? Можно несколько.", "<b>2 of 6.</b> Any pets? You can pick several."),
    "terms": ("<b>3 из 6.</b> На какой срок? Можно несколько.", "<b>3 of 6.</b> For how long? You can pick several."),
    "bud": ("<b>4 из 6.</b> Бюджет в месяц?", "<b>4 of 6.</b> Monthly budget?"),
    "beds": ("<b>5 из 6.</b> Сколько спален? Можно несколько.", "<b>5 of 6.</b> How many bedrooms? You can pick several."),
    "place": ("<b>6 из 6.</b> Где искать?", "<b>6 of 6.</b> Where should I look?"),
    "dist": ("Отметьте районы, можно несколько:", "Pick districts, several are fine:"),
    "done": ("Готово ➜", "Done ➜"), "own": ("✏️ Своя сумма", "✏️ My own amount"),
    "own_ask": ("Напишите сумму, например «до 900» или «около 1000$».", "Write the amount, e.g. «up to 900» or «about $1000»."),
    "own_bad": ("Не понял сумму 🙈 Напишите, например, «до 900».", "I didn't get the amount 🙈 Try «up to 900»."),
    "own_ok": ("Понял: <b>{b}</b> в месяц ✓", "Got it: <b>{b}</b> a month ✓"),
    "map": ("📍 Выбрать на карте", "📍 Pick on the map"), "districts": ("🏙 Выбрать районы", "🏙 Pick districts"),
    "any": ("Любой район", "Any district"),
    "map_hint": ("Нажмите кнопку «🗺 Выбрать место на карте» внизу экрана и поставьте точку 👇",
                 "Tap «🗺 Pick a place on the map» at the bottom of the screen and drop a pin 👇"),
    "picked": ("Выбрано: {x}", "Selected: {x}"),
    "summary": ("<b>Ваш профиль</b> ✅\n{p}\n\nЕсли есть фото квартир, которые вам нравятся, пришлите их: подберу похожие по стилю 📷",
                "<b>Your profile</b> ✅\n{p}\n\nIf you have photos of flats you like, send them: I'll find similar ones 📷"),
    "back": ("С возвращением! 👋", "Welcome back! 👋"),
    "show": ("🔍 Показать варианты", "🔍 Show options"), "edit": ("✏️ Изменить профиль", "✏️ Edit profile"),
}


def q(cl, key, **kw):
    s = Q[key][L(cl) == "en"]
    return s.format(**kw) if kw else s


def opt_label(cl, opts, code):
    return next(lab[L(cl)] for c, lab in opts if c == code)


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
        btns = [cb(("✓ " if ru in sel else "") + (en if L(cl) == "en" else ru), f"q:dist:{n}") for n, (ru, en) in enumerate(texts.DISTRICTS)]
        return markup([btns[i:i + 3] for i in range(0, len(btns), 3)] + [[cb(q(cl, "done"), "q:dist:done")]])
    sel = selected(cl, step) if step in MULTI else []
    btns = [cb(("✓ " if code in sel else "") + lab[L(cl)], f"q:{step}:{n}") for n, (code, lab) in enumerate(OPTS[step])]
    rows = [btns[:2], btns[2:]] if step in ("pets", "bud") else [btns]
    if step == "bud":
        rows.append([cb(q(cl, "own"), "q:bud:own")])
    if step in MULTI:
        rows.append([cb(q(cl, "done"), f"q:{step}:done")])
    return markup(rows)


def question_text(cl, step):
    t = q(cl, step)
    if step in MULTI or step == "dist":
        labels = selected_labels(cl, step)
        if labels:
            t += "\n<i>" + q(cl, "picked", x=", ".join(labels)) + "</i>"
    return t


def selected_labels(cl, step):
    sel = selected(cl, step)
    if step == "dist":
        return [texts.DISTRICT_EN.get(d, d) if L(cl) == "en" else d for d in sel]
    return [lab[L(cl)] for code, lab in OPTS[step] if code in sel]


async def ask(bot, cl, step):
    await bot.send_message(int(cl["chat_id"]), question_text(cl, step), parse_mode="HTML", reply_markup=question_kb(cl, step))


async def start_questionnaire(bot, cl, name):
    await th(set_done, cl, False)
    await bot.send_message(int(cl["chat_id"]), q(cl, "intro", name=e(name or "")).replace(", !", "!"))
    await ask(bot, cl, "res")


async def summary(bot, cl, back=False):
    text = (q(cl, "back") + "\n\n" if back else "") + q(cl, "summary", p=e(texts.profile_summary(cl.get("profile") or {}, L(cl))))
    await bot.send_message(int(cl["chat_id"]), text, parse_mode="HTML",
                           reply_markup=markup([[cb(q(cl, "show"), "q:go")], [cb(q(cl, "edit"), "q:edit")]]))


async def finish(bot, cl):
    await th(set_done, cl, True)
    await th(set_await, cl)
    await summary(bot, cl)


def next_step(step):
    return STEPS[STEPS.index(step) + 1] if step in STEPS and step != "place" else None


BUDGET_CODES = {"0-500": (None, 500), "500-800": (500, 800), "800-1200": (800, 1200), "1200-": (1200, None)}


async def on_q(update: Update, ctx, cl):
    """q:<step>:<option index | done | own | map | districts | any>, q:go, q:edit."""
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
        await ask(bot, cl, "res")
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
        await edit_text(query, question_text(cl, step), parse_mode="HTML", reply_markup=question_kb(cl, step))
        return
    if step == "dist" and val != "done":
        name = texts.DISTRICTS[int(val)][0]
        await th(save_profile, cl, lambda p: p.update(districts=[d for d in p.get("districts") or [] if d != name and d != "any"]
                                                      + ([] if name in (p.get("districts") or []) else [name]), pin=None))
        await answer(query)
        await th(load_profile, cl)
        await edit_text(query, question_text(cl, "dist"), parse_mode="HTML", reply_markup=question_kb(cl, "dist"))
        return
    await answer(query)
    if step in MULTI or step == "dist":  # Готово
        await th(load_profile, cl)
        labels = selected_labels(cl, step)
        if step == "dist" and not labels:
            await th(save_profile, cl, lambda p: p.update(districts=["any"], pin=None))
            labels = [q(cl, "any")]
        await edit_text(query, q(cl, step), parse_mode="HTML", reply_markup=only_chosen(labels or ["—"]))
        return await finish(bot, cl) if step == "dist" else await ask(bot, cl, next_step(step))
    if step == "res":
        code = OPTS["res"][int(val)][0]
        await th(save_profile, cl, lambda p: p.update(residents=code))
        await edit_text(query, q(cl, "res"), parse_mode="HTML", reply_markup=only_chosen([opt_label(cl, OPTS["res"], code)]))
        return await ask(bot, cl, "pets")
    if step == "bud":
        if val == "own":
            await th(set_await, cl, "budget")
            await bot.send_message(int(cl["chat_id"]), q(cl, "own_ask"))
            return
        code = OPTS["bud"][int(val)][0]
        lo, hi = BUDGET_CODES[code]
        await th(save_profile, cl, lambda p: p.update(budget_min=lo, budget_max=hi))
        await edit_text(query, q(cl, "bud"), parse_mode="HTML", reply_markup=only_chosen([opt_label(cl, OPTS["bud"], code)]))
        return await ask(bot, cl, "beds")
    if step == "place":
        if val == "any":
            await th(save_profile, cl, lambda p: p.update(districts=["any"], pin=None, radius_km=None))
            await edit_text(query, q(cl, "place"), parse_mode="HTML", reply_markup=only_chosen([q(cl, "any")]))
            return await finish(bot, cl)
        if val == "districts":
            await edit_text(query, q(cl, "place"), parse_mode="HTML", reply_markup=only_chosen([q(cl, "districts")[2:]]))
            return await ask(bot, cl, "dist")
        await th(set_await, cl, "pin")
        from bot import map_kb
        await bot.send_message(int(cl["chat_id"]), q(cl, "map_hint"), reply_markup=map_kb(cl["lang"]))


NUM = r"(\d+(?:[.,]\d+)?)\s*(k|к|тыс\.?)?"


def parse_budget(text):
    """Free-text budget (A1b) -> (min, max) in $: «до 900», «около 1000$», «от 700 до 1000», «800-1200», «2000 лари»."""
    t = (text or "").lower().replace(" ", " ")
    t = re.sub(r"(\d)\s(\d{3})\b", r"\1\2", t)  # 1 000 -> 1000
    t = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", t)  # $1,200 -> 1200 (a comma before exactly 3 digits: thousands)
    nums = [float(n.replace(",", ".")) * (1000 if k else 1) for n, k in re.findall(NUM, t)]
    if not nums:
        return None
    if re.search(r"лар|gel|₾|lari", t):
        nums = [n / 2.7 for n in nums]
    nums = [int(round(n)) for n in nums]
    if any(n < 50 or n > 100000 for n in nums):
        return None
    if len(nums) >= 2:
        return min(nums[:2]), max(nums[:2])
    n = nums[0]
    if re.search(r"\bот\b|\bfrom\b|\bmin|не меньше|at least|\+\s*$|больше", t):
        return n, None
    return None, n  # «до», «up to», «около», «about», a bare number: the ceiling (the search allows +10%)


async def on_budget_text(bot, cl, text):
    got = parse_budget(text)
    if not got:
        await bot.send_message(int(cl["chat_id"]), q(cl, "own_bad"))
        return
    await th(save_profile, cl, lambda p: p.update(budget_min=got[0], budget_max=got[1]))
    await th(set_await, cl)
    await bot.send_message(int(cl["chat_id"]), q(cl, "own_ok", b=e(texts.budget_text(cl["profile"], L(cl)))), parse_mode="HTML")
    await ask(bot, cl, "beds")


async def on_pin(bot, cl, lat, lng, radius, label):
    """A1c map point (Mini App map / location) while the questionnaire waits for it."""
    await th(save_profile, cl, lambda p: p.update(pin={"lat": lat, "lng": lng, "label": label}, radius_km=radius or 2.0, districts=[]))
    await finish(bot, cl)


async def on_start(update: Update, ctx, cl):
    """/start: questionnaire once (A1a), afterwards the saved profile with «Показать варианты» / «Изменить»."""
    if cl.get("profile_done"):
        await summary(ctx.bot, cl, back=True)
    else:
        await start_questionnaire(ctx.bot, cl, update.effective_user.first_name)


# ---------- A2b/A3: the card and ❤️ ----------

def remember_card(cl, listing_id, chat_id, message_id, text, rows):
    """The sent card (text + extra button rows) so notify.lead_client_ack can rewrite it (A3)."""
    def fn(st, r):
        cards = st.setdefault("cards", {})
        cards.pop(listing_id, None)
        cards[listing_id] = {"text": text, "rows": rows}
        for k in list(cards)[:-30]:
            cards.pop(k)
    core.update(cl, fn)
    core.remember_message("card", f"{cl['id']}:{listing_id}", chat_id, message_id)


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
        tok = notify.tapped_card.set((query.message.chat.id, query.message.message_id) if query.message else None)
        try:
            lead, err = await svc(leads.create_from_like, cl["id"], lid, request_of(cl))
        finally:
            notify.tapped_card.reset(tok)
        await answer(query, core.t(cl["lang"], "liked"))
    if err:
        log.warning("create_from_like %s/%s: %s", cl["id"], lid, err)
        try:
            await query.edit_message_reply_markup(markup([[cb(core.t(cl["lang"], "sent"), "noop")]]))
        except TelegramError:
            pass


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
        return False, {"bad_status": "Заявка уже на другом шаге", "forbidden": "Это не ваша заявка"}.get(err, f"Ошибка: {err}")
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
    await update.effective_message.reply_text(tr(cl, "Отменено 👌", "Cancelled 👌") if had else tr(cl, "Нечего отменять.", "Nothing to cancel."))


# ---------- A4/A5/A6: client buttons ----------

OUTCOME_REPLY = {
    "rented": ("Отлично, поздравляем! 🎉\nАгент {agent} поможет с договором. Оператор проверит сделку и пришлёт подтверждение.\n\n"
               "Через 14 дней спрошу, всё ли хорошо в новой квартире.",
               "Great, congratulations! 🎉\nAgent {agent} will help with the contract. An operator will check the deal and confirm.\n\n"
               "In 14 days I'll ask if everything is fine in the new flat."),
    "thinking": ("Хорошо, подумайте 🙂 Напомню завтра.", "Sure, take your time 🙂 I'll remind you tomorrow."),
}
REASON_REPLY = {
    "price": ("Понял, учту цену. Подобрать похожие квартиры дешевле?", "Got it, I'll keep the price in mind. Find similar cheaper flats?"),
    "location": ("Понял, учту район. Подобрать похожие в другом месте?", "Got it. Find similar flats in another area?"),
    "condition": ("Понял, учту состояние. Подобрать похожие в лучшем состоянии?", "Got it. Find similar flats in better condition?"),
    "other": ("Понял, учту. Подобрать похожие квартиры?", "Got it. Shall I find similar flats?"),
}
REASON_MSG = {"price": ("хочу дешевле, чем ${p}", "cheaper than ${p}"), "location": ("другой район, не {d}", "another area, not {d}"),
              "condition": ("в лучшем состоянии, с хорошим ремонтом", "better condition, good renovation"), "other": (None, None)}


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


async def on_client_journey(update: Update, ctx, cl):
    """ls:pick / ls:own / lv:* / lo:c:* / lr:* / t14:* pressed by a client."""
    from app.services import leads
    query, bot, chat = update.callback_query, ctx.bot, update.effective_chat.id
    parts = query.data.split(":")
    kind = parts[0]
    if kind == "ls" and parts[1] == "pick":
        _, err = await svc(leads.choose_slot, int(parts[2]), cl["id"])  # notify: A4b to the client, C8 contacts to the agent
        await answer(query, "✅" if not err else tr(cl, "Это время уже недоступно", "This time is no longer available"), show_alert=bool(err))
        return
    if kind == "t14":
        return await on_t14(update, ctx, cl, parts)
    lead_id = int(parts[2] if kind in ("ls", "lv", "lo") else parts[1])
    ld = await th(my_lead, cl, lead_id)
    if not ld:
        await answer(query, core.t(cl["lang"], "gone"))
        return
    if kind == "ls":  # own time
        await th(set_await, cl, "own_time", lead_id)
        await answer(query)
        await bot.send_message(chat, tr(cl, "Напишите удобное время, например «завтра после 18:00». Передам агенту.",
                                        "Write a time that suits you, e.g. «tomorrow after 6 pm». I'll pass it to the agent."))
    elif kind == "lv":
        act = parts[1]
        if act == "ok":
            await answer(query, tr(cl, "Отлично, ждём вас 👍", "Great, see you there 👍"))
            await mark_choice(query, pressed_label(query))
            await th(notify.viewing_update, ld, "ok")
        elif act == "late":
            await svc(leads.client_late, lead_id)
            await answer(query)
            await th(notify.viewing_update, ld, "late")
            await bot.send_message(chat, tr(cl, "Передал агенту, что вы опаздываете 👌", "I told the agent you're running late 👌"))
        elif act == "resched":
            _, err = await svc(leads.request_reschedule, lead_id, "client")
            await answer(query)
            await bot.send_message(chat, tr(cl, "Передал агенту: он согласует с собственником новое время и пришлёт варианты 🙌",
                                            "I asked the agent: they'll agree a new time with the owner and send options 🙌")
                                   if not err else tr(cl, "Перенести уже нельзя, напишите оператору.", "Can't reschedule now, please write to the operator."))
    elif kind == "lo":
        outcome = parts[3]
        _, err = await svc(leads.set_outcome, lead_id, "client", outcome)  # refused -> notify.refusal_reason_ask
        await answer(query)
        if err:
            return
        await mark_choice(query, pressed_label(query))
        if outcome in OUTCOME_REPLY:
            ag = await th(user_row, ld["agent_id"]) or {}
            await bot.send_message(chat, OUTCOME_REPLY[outcome][L(cl) == "en"].format(agent=ag.get("name") or ""))
    elif kind == "lr":
        reason = parts[2] if len(parts) > 2 and parts[2] in REASON_MSG else "other"
        if not first_tap(query, "lr"):  # a double tap (or a second reason on the same card): one answer
            await answer(query)
            return
        await svc(leads.set_outcome, lead_id, "client", "refused", reason)
        await answer(query)
        await mark_choice(query, pressed_label(query))
        i = await th(core.listing, ld["listing_id"]) or {}
        await th(core.react, cl, ld["listing_id"], "dislike")
        hint = REASON_MSG[reason][L(cl) == "en"]
        if hint:
            await th(core.add_input, cl, text=hint.format(p=i.get("price_usd") or "", d=i.get("district") or ""))
        await bot.send_message(chat, REASON_REPLY[reason][L(cl) == "en"],
                               reply_markup=markup([[cb(tr(cl, "🔄 Да, подобрать", "🔄 Yes, find"), "more")],
                                                    [cb(tr(cl, "Пока нет", "Not now"), "noop")]]))


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
    """t14:<deal>:<1-3>:<0-3> -> next question; after the third an evaluation (author=client). t14:<deal>:problem -> free text."""
    from app.services import evaluations
    query = update.callback_query
    deal_id = int(parts[1])
    if await th(_deal_client, deal_id) != cl["id"]:
        await answer(query, core.t(cl["lang"], "gone"))
        return
    await answer(query)
    if not first_tap(query, "t14"):  # a double tap on a score: one next question / one evaluation
        return
    if parts[2] == "problem":
        await th(set_await, cl, "problem", deal_id)
        await ctx.bot.send_message(update.effective_chat.id, tr(cl, "Опишите, пожалуйста, проблему одним сообщением, оператор ответит.",
                                                                 "Please describe the problem in one message, an operator will reply."))
        return
    n, score = int(parts[2]), int(parts[3])
    await th(core.update, cl, lambda st, r: st.setdefault("t14", {}).setdefault(str(deal_id), {}).update({str(n): score}))
    await mark_choice(query, str(score))
    if n < 3:
        text, mk = notify.t14_question(deal_id, n + 1, L(cl))
        await ctx.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=markup(mk["inline_keyboard"]))
        return
    a = cl["state"]["t14"][str(deal_id)]
    async with lock_for(("t14", deal_id)):
        if await th(_t14_scored, deal_id):
            return
        await svc(evaluations.create, {"deal_id": deal_id, "kind": "t14", "author": "client", "h2o_client": a.get("1"),
                                       "h2o_owner": a.get("2"), "h2o_process": a.get("3")}, None)
    await ctx.bot.send_message(update.effective_chat.id, tr(cl, "Спасибо! 🙏 0 = плохо, 3 = отлично. Если есть проблема, напишите, оператор ответит.",
                                                             "Thank you! 🙏 0 = bad, 3 = great. If there is a problem, write it, an operator will reply."),
                               reply_markup=markup([[cb(tr(cl, "✍️ Есть проблема", "✍️ There is a problem"), f"t14:{deal_id}:problem")]]))


async def on_client_text(update: Update, ctx, cl):
    """Free text the client's state is waiting for (budget, own time, T14 problem). -> True if handled."""
    from app.services import evaluations, leads
    kind, ref = awaiting(cl)
    text = update.effective_message and update.effective_message.text
    if not kind or not text:
        return False
    bot, chat = ctx.bot, update.effective_chat.id
    if kind == "budget":
        await on_budget_text(bot, cl, text)
    elif kind == "own_time":
        await th(set_await, cl)
        await svc(leads.client_own_time, int(ref), text)  # notify: the agent gets the lead again with the client's time
        await bot.send_message(chat, tr(cl, "Передал агенту, он согласует время с собственником 👌",
                                        "Sent to the agent, they'll agree the time with the owner 👌"))
    elif kind == "problem":
        await th(set_await, cl)
        await svc(evaluations.create, {"deal_id": int(ref), "kind": "t14", "author": "client", "complaints": True,
                                       "complaints_text": text}, None)  # -> notify.evaluation_alert to operators
        await bot.send_message(chat, tr(cl, "Передал оператору, он скоро ответит 🙏", "Sent to an operator, they'll reply soon 🙏"))
    else:
        return False
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
