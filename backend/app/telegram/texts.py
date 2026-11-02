"""Shared wording for Telegram (bot questionnaire + notify): client profile, slot/time labels, T14 questions. RU/EN."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# questionnaire options (A1a-A1c); the codes are what clients.profile stores (ARCHITECTURE §4.3)
RESIDENTS = [("1", {"ru": "1", "en": "1"}), ("2", {"ru": "2", "en": "2"}), ("3", {"ru": "3", "en": "3"}), ("4+", {"ru": "4+", "en": "4+"})]
PETS = [("none", {"ru": "Нет", "en": "No"}), ("cat", {"ru": "🐈 Кошка", "en": "🐈 Cat"}),
        ("dog", {"ru": "🐕 Собака", "en": "🐕 Dog"}), ("other", {"ru": "Другое", "en": "Other"})]
TERMS = [("1-3", {"ru": "до 3 мес", "en": "up to 3 mo"}), ("6", {"ru": "6 мес", "en": "6 mo"}),
         ("12", {"ru": "12 мес", "en": "12 mo"}), ("12+", {"ru": "Больше года", "en": "Over a year"})]
BUDGETS = [("0-500", {"ru": "$ до 500", "en": "up to $500"}), ("500-800", {"ru": "500-800", "en": "$500-800"}),
           ("800-1200", {"ru": "800-1200", "en": "$800-1200"}), ("1200-", {"ru": "1200+", "en": "$1200+"})]
BEDROOMS = [("0", {"ru": "Студия", "en": "Studio"}), ("1", {"ru": "1", "en": "1"}), ("2", {"ru": "2", "en": "2"}),
            ("3", {"ru": "3+", "en": "3+"})]
# district names as listings.district stores them (ru) + English labels
DISTRICTS = [("Ваке", "Vake"), ("Сабуртало", "Saburtalo"), ("Мтацминда", "Mtatsminda"), ("Сололаки", "Sololaki"),
             ("Вера", "Vera"), ("Дидубе", "Didube"), ("Дигоми", "Digomi"), ("Исани", "Isani"), ("Глдани", "Gldani"),
             ("Старый Тбилиси", "Old Tbilisi"), ("Надзаладеви", "Nadzaladevi"), ("Чугурети", "Chughureti")]
DISTRICT_EN = dict(DISTRICTS)

T14_QUESTIONS = {"ru": ["Квартира соответствует объявлению?", "Собственник: всё в порядке?", "Как работал агент и агентство?"],
                 "en": ["Does the flat match the listing?", "Is everything fine with the owner?", "How did the agent and the agency do?"]}
REFUSAL = {"price": {"ru": "Цена", "en": "Price"}, "location": {"ru": "Район", "en": "Location"},
           "condition": {"ru": "Состояние", "en": "Condition"}, "other": {"ru": "Другое", "en": "Other"}}
WEEKDAYS = {"ru": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


def lang_of(lang):
    return "en" if lang == "en" else "ru"


def tz():
    try:
        from app.telegram.client import conf
        return ZoneInfo(conf("TZ", "Asia/Tbilisi"))
    except Exception:
        return ZoneInfo("Asia/Tbilisi")


def local(dt):
    """datetime / ISO string -> aware datetime in the agency time zone."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz())


def slot_label(dt, lang="ru", now=None):
    """Button label: «Сегодня 18:00», «Завтра 12:00», «Пт 24.10 12:00»."""
    lang, d = lang_of(lang), local(dt)
    today = local(now or datetime.now(timezone.utc)).date()
    hm = d.strftime("%H:%M")
    if d.date() == today:
        return ("Today " if lang == "en" else "Сегодня ") + hm
    if d.date() == today + timedelta(days=1):
        return ("Tomorrow " if lang == "en" else "Завтра ") + hm
    return f"{WEEKDAYS[lang][d.weekday()].capitalize()} {d:%d.%m} {hm}"


def when_long(dt, lang="ru", now=None):
    """«завтра, пт 23.10, в 12:00» / «tomorrow, Fri 23.10, at 12:00»."""
    lang, d = lang_of(lang), local(dt)
    today = local(now or datetime.now(timezone.utc)).date()
    day = {0: ("сегодня", "today"), 1: ("завтра", "tomorrow")}.get((d.date() - today).days)
    wd = f"{WEEKDAYS[lang][d.weekday()]} {d:%d.%m}"
    head = f"{day[lang == 'en']}, {wd}" if day else wd
    return f"{head}, {'at' if lang == 'en' else 'в'} {d:%H:%M}"


def money(v):
    if v is None or v == "":
        return "—"
    v = float(v)
    return f"${v:,.0f}".replace(",", " ") if v == int(v) else f"${v:,.2f}".replace(",", " ")


def _people(n, lang):
    if lang == "en":
        return f"{n} {'person' if n == '1' else 'people'}"
    return {"1": "1 человек", "2": "2 человека", "3": "3 человека"}.get(n, f"{n} человек")


def _bedrooms(beds, lang):
    beds = sorted({int(b) for b in beds})
    studio = 0 in beds
    rest = [b for b in beds if b]
    parts = ["studio" if lang == "en" else "студия"] if studio else []
    if rest:
        plus = rest[-1] >= 4 or rest == [3]  # the «3+» button stores 3 and 4
        rest = [b for b in rest if b < 4] or [4]
        rng = f"{rest[0]}-{rest[-1]}" if len(rest) > 1 and rest == list(range(rest[0], rest[-1] + 1)) else ", ".join(map(str, rest))
        rng += "+" if plus else ""
        word = ("bedroom" if rest == [1] else "bedrooms") if lang == "en" else ("спальня" if rest == [1] else "спальни")
        parts.append(f"{rng} {word}")
    return ", ".join(parts)


def budget_text(p, lang="ru"):
    lo, hi = p.get("budget_min"), p.get("budget_max")
    if lo and hi:
        return f"${lo}-{hi}"
    if hi:
        return f"{'up to' if lang == 'en' else 'до'} ${hi}"
    if lo:
        return f"{'from' if lang == 'en' else 'от'} ${lo}"
    return "any budget" if lang == "en" else "бюджет любой"


def terms_text(terms, lang="ru"):
    nums = [t for t in ("6", "12") if t in terms]
    out = []
    if "1-3" in terms:
        out.append("up to 3 mo" if lang == "en" else "до 3 мес")
    if nums:
        out.append(f" {'or' if lang == 'en' else 'или'} ".join(nums) + (" mo" if lang == "en" else " мес"))
    if "12+" in terms:
        out.append("over a year" if lang == "en" else "больше года")
    return ", ".join(out)


def pets_text(pets, lang="ru"):
    names = {"none": ("без животных", "no pets"), "cat": ("🐈 кошка", "🐈 cat"), "dog": ("🐕 собака", "🐕 dog"),
             "other": ("другое животное", "other pet")}
    return ", ".join(names[x][lang == "en"] for x in pets if x in names)


def place_text(p, lang="ru"):
    if p.get("pin"):
        r = p.get("radius_km") or 2
        label = p["pin"].get("label")
        return (f"{label}, " if label else ("point on the map, " if lang == "en" else "точка на карте, ")) + f"{r:g} {'km' if lang == 'en' else 'км'}"
    ds = p.get("districts") or []
    if not ds or "any" in ds:
        return "any district" if lang == "en" else "любой район"
    return ", ".join(DISTRICT_EN.get(d, d) if lang == "en" else d for d in ds)


def profile_summary(p, lang="ru", one_line=False):
    """Client profile as the journey screens show it (A2a, B1, C6):
    👥 2 человека · 🐕 собака / 🕐 6 или 12 мес · 💰 до $900 / 🛏 1-2 спальни / 📍 Ваке, Сабуртало"""
    lang, p = lang_of(lang), p or {}
    if not any(v not in (None, [], "", 0) for v in p.values()):
        return ""
    rows = [[("👥", p.get("residents") and _people(p["residents"], lang)), ("", p.get("pets") and pets_text(p["pets"], lang))],
            [("🕐", p.get("terms") and terms_text(p["terms"], lang)), ("💰", budget_text(p, lang))],
            [("🛏", p.get("bedrooms") and _bedrooms(p["bedrooms"], lang))],
            [("📍", place_text(p, lang))]]
    if p.get("move_in"):
        rows.append([("📅", p["move_in"])])
    if one_line:
        return " · ".join(v for r in rows for _, v in r if v)
    lines = [" · ".join(f"{e} {v}".strip() if e else v for e, v in r if v) for r in rows]
    return "\n".join(x for x in lines if x)
