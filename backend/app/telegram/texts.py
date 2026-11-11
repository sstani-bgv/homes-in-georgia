"""Shared wording for Telegram (bot questionnaire + notify): client profile, slot/time labels, T14 questions. RU/EN/KA."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import districts

# questionnaire options (A1a-A1c); the codes are what clients.profile stores (ARCHITECTURE §4.3)
def _same(x):
    return {"ru": x, "en": x, "ka": x}


RESIDENTS = [("1", _same("1")), ("2", _same("2")), ("3", _same("3")), ("4+", _same("4+"))]
PETS = [("none", {"ru": "Нет", "en": "No", "ka": "არა"}), ("cat", {"ru": "🐈 Кошка", "en": "🐈 Cat", "ka": "🐈 კატა"}),
        ("dog", {"ru": "🐕 Собака", "en": "🐕 Dog", "ka": "🐕 ძაღლი"}), ("other", {"ru": "Другое", "en": "Other", "ka": "სხვა"})]
TERMS = [("1-3", {"ru": "до 3 мес", "en": "up to 3 mo", "ka": "3 თვემდე"}), ("6", {"ru": "6 мес", "en": "6 mo", "ka": "6 თვე"}),
         ("12", {"ru": "12 мес", "en": "12 mo", "ka": "12 თვე"}), ("12+", {"ru": "Больше года", "en": "Over a year", "ka": "წელზე მეტი"})]
BUDGETS = [("0-500", {"ru": "$ до 500", "en": "up to $500", "ka": "$500-მდე"}), ("500-800", {"ru": "500-800", "en": "$500-800", "ka": "$500-800"}),
           ("800-1200", {"ru": "800-1200", "en": "$800-1200", "ka": "$800-1200"}), ("1200-", {"ru": "1200+", "en": "$1200+", "ka": "$1200+"})]
BEDROOMS = [("0", {"ru": "Студия", "en": "Studio", "ka": "სტუდიო"}), ("1", _same("1")), ("2", _same("2")), ("3", _same("3+"))]
# district names as listings.district stores them (ru) + English and Georgian labels: the channel's list (app/districts.py)
DISTRICTS = [(ru, en, ka) for _, ru, en, ka, _ in districts.DISTRICTS]
_OLD = [("Старый Тбилиси", "Old Tbilisi", "ძველი თბილისი"), ("Старый город", "Old Tbilisi", "ძველი თბილისი"),
        ("Вере", "Vera", "ვერა")]  # older profile / listing values: still named, no longer offered
DISTRICT_EN = {ru: en for ru, en, _ in DISTRICTS + _OLD}
DISTRICT_KA = {ru: ka for ru, _, ka in DISTRICTS + _OLD}

T14_QUESTIONS = {"ru": ["Квартира соответствует объявлению?", "Собственник: всё в порядке?", "Как работал агент и агентство?"],
                 "en": ["Does the flat match the listing?", "Is everything fine with the owner?", "How did the agent and the agency do?"],
                 "ka": ["ბინა შეესაბამება განცხადებას?", "მესაკუთრესთან ყველაფერი რიგზეა?", "როგორ იმუშავეს აგენტმა და სააგენტომ?"]}
REFUSAL = {"price": {"ru": "Цена", "en": "Price", "ka": "ფასი"}, "location": {"ru": "Район", "en": "Location", "ka": "მდებარეობა"},
           "condition": {"ru": "Состояние", "en": "Condition", "ka": "მდგომარეობა"}, "other": {"ru": "Другое", "en": "Other", "ka": "სხვა"}}
WEEKDAYS = {"ru": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "ka": ["ორშ", "სამ", "ოთხ", "ხუთ", "პარ", "შაბ", "კვ"]}
LANGS = ("ru", "en", "ka")  # clients.lang (DB CHECK clients_lang_check)


def lang_of(lang):
    return lang if lang in LANGS else "ru"


def pick(lang, ru, en, ka):
    """The variant of a client-facing string for the client's language."""
    return (ru, en, ka)[LANGS.index(lang_of(lang))]


def district_name(ru_name, lang):
    """listings.district (ru) in the client's language."""
    return pick(lang, ru_name, DISTRICT_EN.get(ru_name, ru_name), DISTRICT_KA.get(ru_name) or DISTRICT_EN.get(ru_name, ru_name))


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
        return pick(lang, "Сегодня ", "Today ", "დღეს ") + hm
    if d.date() == today + timedelta(days=1):
        return pick(lang, "Завтра ", "Tomorrow ", "ხვალ ") + hm
    return f"{WEEKDAYS[lang][d.weekday()].capitalize()} {d:%d.%m} {hm}"


def when_long(dt, lang="ru", now=None):
    """«завтра, пт 23.10, в 12:00» / «tomorrow, Fri 23.10, at 12:00» / «ხვალ, პარ 23.10, 12:00-ზე»."""
    lang, d = lang_of(lang), local(dt)
    today = local(now or datetime.now(timezone.utc)).date()
    day = {0: ("сегодня", "today", "დღეს"), 1: ("завтра", "tomorrow", "ხვალ")}.get((d.date() - today).days)
    wd = f"{WEEKDAYS[lang][d.weekday()]} {d:%d.%m}"
    head = f"{pick(lang, *day)}, {wd}" if day else wd
    hm = f"{d:%H:%M}"
    return f"{head}, {pick(lang, 'в ' + hm, 'at ' + hm, hm + '-ზე')}"


def money(v):
    if v is None or v == "":
        return "—"
    v = float(v)
    return f"${v:,.0f}".replace(",", " ") if v == int(v) else f"${v:,.2f}".replace(",", " ")


def _people(n, lang):
    if lang == "en":
        return f"{n} {'person' if n == '1' else 'people'}"
    if lang == "ka":
        return f"{n} ადამიანი"
    return {"1": "1 человек", "2": "2 человека", "3": "3 человека"}.get(n, f"{n} человек")


def _bedrooms(beds, lang):
    beds = sorted({int(b) for b in beds})
    studio = 0 in beds
    rest = [b for b in beds if b]
    parts = [pick(lang, "студия", "studio", "სტუდიო")] if studio else []
    if rest:
        plus = rest[-1] >= 4 or rest == [3]  # the «3+» button stores 3 and 4
        rest = [b for b in rest if b < 4] or [4]
        rng = f"{rest[0]}-{rest[-1]}" if len(rest) > 1 and rest == list(range(rest[0], rest[-1] + 1)) else ", ".join(map(str, rest))
        rng += "+" if plus else ""
        word = pick(lang, "спальня" if rest == [1] else "спальни", "bedroom" if rest == [1] else "bedrooms", "საძინებელი")
        parts.append(f"{rng} {word}")
    return ", ".join(parts)


def budget_text(p, lang="ru"):
    lo, hi = p.get("budget_min"), p.get("budget_max")
    if lo and hi:
        return f"${lo}-{hi}"
    if hi:
        return pick(lang, f"до ${hi}", f"up to ${hi}", f"${hi}-მდე")
    if lo:
        return pick(lang, f"от ${lo}", f"from ${lo}", f"${lo}-დან")
    return pick(lang, "бюджет любой", "any budget", "ნებისმიერი ბიუჯეტი")


def terms_text(terms, lang="ru"):
    nums = [t for t in ("6", "12") if t in terms]
    out = []
    if "1-3" in terms:
        out.append(pick(lang, "до 3 мес", "up to 3 mo", "3 თვემდე"))
    if nums:
        out.append(f" {pick(lang, 'или', 'or', 'ან')} ".join(nums) + pick(lang, " мес", " mo", " თვე"))
    if "12+" in terms:
        out.append(pick(lang, "больше года", "over a year", "წელზე მეტი"))
    return ", ".join(out)


def pets_text(pets, lang="ru"):
    names = {"none": ("без животных", "no pets", "ცხოველის გარეშე"), "cat": ("🐈 кошка", "🐈 cat", "🐈 კატა"),
             "dog": ("🐕 собака", "🐕 dog", "🐕 ძაღლი"), "other": ("другое животное", "other pet", "სხვა ცხოველი")}
    return ", ".join(pick(lang, *names[x]) for x in pets if x in names)


def place_text(p, lang="ru"):
    if p.get("pin"):
        r = p.get("radius_km") or 2
        label = p["pin"].get("label")
        return (f"{label}, " if label else pick(lang, "точка на карте, ", "point on the map, ", "წერტილი რუკაზე, ")) + \
            f"{r:g} {pick(lang, 'км', 'km', 'კმ')}"
    ds = p.get("districts") or []
    if not ds or "any" in ds:
        return pick(lang, "любой район", "any district", "ნებისმიერი უბანი")
    return ", ".join(district_name(d, lang) for d in ds)


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
