"""Business logic, channel-agnostic: clients, matching, reactions, leads, moderation.
Adapters (bot.py today; a website or WhatsApp later) call these functions and only render/deliver the results."""
import html
import json
import os
import re
from urllib.parse import urlencode

import numpy as np

import db
import facts
from geo import geocode, km
from llm import ask, obj

BRAND = "Homes in Georgia"

# ---------- client-facing texts (ru / en / ka) ----------

T = {
    "ru": {
        "greeting": f"""Привет! Я ИИ-подборщик квартир агентства {BRAND} 🏙

Просто напишите, что ищете: район или место, рядом с которым хотите жить, бюджет, сколько спален, с животными или нет.

Например: «2 спальни до $1000 рядом с метро Руставели, светлая, с балконом».

По желанию:
📸 пришлите фото квартир, которые нравятся (хоть из Pinterest), подберу похожие по стилю;
🗺 выберите место на карте кнопкой внизу, покажу квартиры поблизости.""",
        "map_btn": "🗺 Выбрать место на карте", "here_btn": "📍 Где я сейчас",
        "searching": "🔎 Смотрю варианты, это займёт до минуты…",
        "error": "Что-то пошло не так, попробуйте ещё раз.",
        "none": "Понял: {understood}\n\nПодходящих вариантов сейчас нет 😔 Попробуйте расширить бюджет или районы.",
        "found": "Понял: {understood}\n\nПодходит {total} из {base} квартир в базе, показываю {n} лучших 👇",
        "tips": "Уточните запрос сообщением или нажмите «Ещё варианты».",
        "tip_photo": "\n\n📸 Хотите точнее по стилю? Пришлите фото квартир, которые нравятся, покажу похожие.",
        "tip_map": "\n🗺 Или выберите место на карте кнопкой внизу, найду квартиры рядом.",
        "more": "🔄 Ещё варианты", "like": "❤️ Нравится", "dislike": "👎 Не то", "sent": "✅ Передано агенту", "on_map": "📍 На карте",
        "liked": "Отлично!", "disliked": "Учту ваш вкус 👌", "gone": "Объявление уже неактуально",
        "lead_sent": f"Передал агенту {BRAND} 🙌 Он свяжется с собственником, договорится о просмотре и напишет вам.",
        "radius": "📍 Ищу в радиусе {r:g} км от {label}",
        "pin": "отмеченной точки", "here": "вашего местоположения", "picked": "выбранной точки",
        "from": "от", "km": "км", "month": "мес", "beds": "сп.", "floor": "этаж", "sqm": "м²", "city": "Тбилиси",
        "lang_btn": "🌐 Язык", "noted": "Учёл ✍️ Нажмите «🔄 Ещё варианты», и я подберу квартиры с учётом этого.",
    },
    "en": {
        "greeting": f"""Hi! I'm the AI apartment finder of {BRAND} 🏙

Just write what you are looking for: a district or a place you want to live near, budget, number of bedrooms, pets or not.

For example: "2 bedrooms up to $1000 near Rustaveli metro, bright, with a balcony".

Optional:
📸 send photos of flats you like (even from Pinterest), I'll find similar ones;
🗺 pick a place on the map with the button below, I'll show flats nearby.""",
        "map_btn": "🗺 Pick a place on the map", "here_btn": "📍 Where I am now",
        "searching": "🔎 Looking for options, this takes up to a minute…",
        "error": "Something went wrong, please try again.",
        "none": "Got it: {understood}\n\nNo matching flats right now 😔 Try a bigger budget or more districts.",
        "found": "Got it: {understood}\n\n{total} of {base} flats match, here are the best {n} 👇",
        "tips": "Refine your request with a message or tap \"More options\".",
        "tip_photo": "\n\n📸 Want a closer style match? Send photos of flats you like.",
        "tip_map": "\n🗺 Or pick a place on the map with the button below, I'll find flats nearby.",
        "more": "🔄 More options", "like": "❤️ I like it", "dislike": "👎 Not this", "sent": "✅ Sent to an agent", "on_map": "📍 On the map",
        "liked": "Great!", "disliked": "Noted 👌", "gone": "This listing is no longer available",
        "lead_sent": f"Sent to a {BRAND} agent 🙌 They will contact the owner, arrange a viewing and message you.",
        "radius": "📍 Searching within {r:g} km of {label}",
        "pin": "the pinned point", "here": "your location", "picked": "the selected point",
        "from": "from", "km": "km", "month": "mo", "beds": "bd", "floor": "floor", "sqm": "m²", "city": "Tbilisi",
        "lang_btn": "🌐 Language", "noted": "Noted ✍️ Tap «🔄 More options» and I'll pick flats with this in mind.",
    },
    "ka": {
        "greeting": f"""გამარჯობა! მე ვარ {BRAND}-ის ხელოვნური ინტელექტის ასისტენტი და ბინის შერჩევაში დაგეხმარებით 🏙

უბრალოდ მომწერეთ, რას ეძებთ: უბანი ან ადგილი, რომლის მახლობლადაც გსურთ ცხოვრება, ბიუჯეტი, რამდენი საძინებელი, ცხოველით თუ მის გარეშე.

მაგალითად: „2 საძინებელი $1000-მდე, მეტრო რუსთაველთან ახლოს, ნათელი, აივნით“.

სურვილისამებრ:
📸 გამომიგზავნეთ თქვენთვის სასურველი ბინების ფოტოები (თუნდაც Pinterest-იდან) და მსგავს სტილს შეგირჩევთ;
🗺 აირჩიეთ ადგილი რუკაზე ქვემოთ მოცემული ღილაკით და ახლომახლო ბინებს გაჩვენებთ.""",
        "map_btn": "🗺 ადგილის არჩევა რუკაზე", "here_btn": "📍 სად ვარ ახლა",
        "searching": "🔎 ვარჩევ ვარიანტებს, ამას ერთ წუთამდე დასჭირდება…",
        "error": "რაღაც შეცდომა მოხდა, სცადეთ თავიდან.",
        "none": "გავიგე: {understood}\n\nამჟამად შესაფერისი ვარიანტი არ არის 😔 სცადეთ ბიუჯეტის ან უბნების გაფართოება.",
        "found": "გავიგე: {understood}\n\nბაზის {base} ბინიდან {total} გერგებათ, გაჩვენებთ {n} საუკეთესოს 👇",
        "tips": "დააზუსტეთ მოთხოვნა შეტყობინებით ან დააჭირეთ „მეტი ვარიანტი“.",
        "tip_photo": "\n\n📸 გსურთ სტილით უფრო ზუსტი შერჩევა? გამომიგზავნეთ სასურველი ბინების ფოტოები და მსგავსებს გაჩვენებთ.",
        "tip_map": "\n🗺 ან აირჩიეთ ადგილი რუკაზე ქვემოთ მოცემული ღილაკით და ახლოს მდებარე ბინებს მოგიძებნით.",
        "more": "🔄 მეტი ვარიანტი", "like": "❤️ მომწონს", "dislike": "👎 არ მინდა", "sent": "✅ გადაეცა აგენტს", "on_map": "📍 რუკაზე",
        "liked": "მშვენიერია!", "disliked": "გავითვალისწინებ 👌", "gone": "განცხადება აღარ არის აქტუალური",
        "lead_sent": f"გადავეცი {BRAND}-ის აგენტს 🙌 ის დაუკავშირდება მესაკუთრეს, შეათანხმებს ნახვას და მოგწერთ.",
        "radius": "📍 ვეძებ {r:g} კმ რადიუსში · {label}",
        "pin": "მონიშნული წერტილი", "here": "თქვენი მდებარეობა", "picked": "არჩეული წერტილი",
        "from": "—", "km": "კმ", "month": "თვე", "beds": "საძ.", "floor": "სართული", "sqm": "მ²", "city": "თბილისი",
        "lang_btn": "🌐 ენა", "noted": "გავითვალისწინე ✍️ დააჭირეთ „🔄 მეტი ვარიანტი“ და ამის გათვალისწინებით შეგირჩევთ ბინებს.",
    },
}


def t(lang, key, **kw):
    s = T.get(lang, T["ru"])[key]
    return s.format(**kw) if kw else s


def detect_lang(text, current=None, hint=None):
    """Georgian script -> ka, Cyrillic -> ru, Latin -> en; otherwise keep the current one or use the platform language hint."""
    if text and re.search(r"[\u10A0-\u10FF]", text):
        return "ka"
    if text and re.search(r"[А-Яа-яЁё]", text):
        return "ru"
    if text and len(re.findall(r"[A-Za-z]", text)) >= 3:
        return "en"
    if current:
        return current
    h = (hint or "ru")[:2]
    return "ka" if h == "ka" else "ru" if h in ("ru", "uk", "be", "kk") else "en"


# never show the client where a listing came from: links, handles, phones, site and competitor names
_LEAK = re.compile(r"https?://\S+|t\.me/\S+|@\w+|\b[\w-]+\.(?:ge|com|ru|net|org)\b|"
                   r"\b(?:myhome|ss\.ge|facebook|cresco|blondestate|tbilisi rent(?: expert)?|rent in tbilisi)\w*", re.I)
_PHONE = re.compile(r"\+?\d[\d\s\-().:]{7,}\d")


def public(text):
    text = _PHONE.sub(lambda m: "" if len(re.sub(r"\D", "", m[0])) >= 9 else m[0], _LEAK.sub("", text or ""))
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ---------- clients ----------

NEW_STATE = {"msgs": [], "images": [], "shown": [], "pin": None, "pin_label": None, "pin_radius": None, "near": None,
             "query": None}


def client(channel, external_id, chat_id=None, username=None, name=None, lang_hint=None):
    """Get or create a client and refresh its contact fields."""
    with db.tx() as c:
        r = c.execute("SELECT * FROM clients WHERE channel = ? AND external_id = ?", (channel, str(external_id))).fetchone()
        if not r:
            c.execute("INSERT INTO clients (channel, external_id, chat_id, username, name, lang, state, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (channel, str(external_id), str(chat_id or external_id), username,
                                                            name, detect_lang(None, hint=lang_hint), json.dumps(NEW_STATE), db.now(), db.now()))
        elif (username, name) != (r["username"], r["name"]):
            c.execute("UPDATE clients SET username = ?, name = ? WHERE id = ?", (username, name, r["id"]))
        return db.row(c.execute("SELECT * FROM clients WHERE channel = ? AND external_id = ?", (channel, str(external_id))).fetchone())


def update(cl, fn):
    """Atomically change the stored client state: fn(state, client) runs on a fresh copy from the DB
    (a search running in a thread must not overwrite a message that arrived meanwhile)."""
    with db.tx() as c:
        r = db.row(c.execute("SELECT * FROM clients WHERE id = ?", (cl["id"],)).fetchone())
        fn(r["state"], r)
        c.execute("UPDATE clients SET state = ?, lang = ?, updated_at = ? WHERE id = ?",
                  (json.dumps(r["state"], ensure_ascii=False), r["lang"], db.now(), cl["id"]))
    cl.update(state=r["state"], lang=r["lang"])


def reset(cl, lang_hint=None):
    """New conversation (/start): forget the request; likes and leads stay.
    lang_hint (Telegram language_code): the greeting follows the app language until the client writes something."""
    def fn(st, r):
        # card texts (A3 edits), T14 answers, the language picked by the client and the DM slot choice outlive a /start
        keep = {k: st[k] for k in ("cards", "t14", "lang_set", "dm_choice") if k in st}
        st.clear(), st.update(json.loads(json.dumps(NEW_STATE)), **keep)
        if lang_hint and not st.get("lang_set"):
            r["lang"] = detect_lang(None, hint=lang_hint)
    update(cl, fn)


def set_lang(cl, lang):
    """The client picked a language (/start picker or /lang): it sticks, no more guessing from texts or Telegram."""
    def fn(st, r):
        st["lang_set"], r["lang"] = True, lang
    update(cl, fn)


def add_input(cl, text=None, image=None, pin=None, pin_label=None, radius=None):
    """A client message: text, a reference photo path, and/or a map point. Starts a fresh result list."""
    def fn(st, r):
        if text:
            st["msgs"].append(text)
            if not st.get("lang_set"):
                r["lang"] = detect_lang(text, r["lang"])
        if image:
            st["images"].append(image)
        if pin:
            st["pin"], st["pin_label"], st["pin_radius"] = list(pin), pin_label, radius or 2.0
        st["shown"] = []  # new request -> earlier results that still fit may be shown again
    update(cl, fn)


# ---------- listings ----------

def listing(lid):
    with db.tx() as c:
        return db.row(c.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone())


# Client access never depends on a setting: moderation of a scraped listing is not agency publication.
# Clients get objects only (2026-11-08): published ones (the verified base, incl. agency channel posts imported by
# app.importers.channel_objects) and fast-tracked ones (stage 10: the owner agreed to show it before the shoot).
# Same rule as backend leads.client_listing_eligible.
CLIENT_ELIGIBLE = "l.status = 'published' AND (EXISTS (SELECT 1 FROM properties p WHERE p.listing_id = l.id AND ((p.status = 'published' AND NULLIF(BTRIM(p.post_url), '') IS NOT NULL AND NULLIF(BTRIM(p.post_message_id), '') IS NOT NULL) OR (p.fast_track_at IS NOT NULL AND p.status NOT IN ('archived', 'rented')))))"


def client_listing(lid):
    with db.tx() as c:
        return db.row(c.execute(f"SELECT l.* FROM listings l WHERE l.id = ? AND {CLIENT_ELIGIBLE}", (lid,)).fetchone())


def published():
    """Listings of published (or fast-tracked) objects only."""
    with db.tx() as c:
        return [db.row(r) for r in c.execute(f"SELECT l.* FROM listings l WHERE {CLIENT_ELIGIBLE}")]


def reactions(cl, kind):
    with db.tx() as c:
        return [db.row(r) for r in c.execute("SELECT l.* FROM reactions r JOIN listings l ON l.id = r.listing_id "
                                             f"WHERE r.client_id = ? AND r.reaction = ? AND {CLIENT_ELIGIBLE} ORDER BY r.created_at",
                                             (cl["id"], kind))]


# ---------- matching ----------

S = {"type": "string"}
PICK = obj(understood=S, matches_total={"type": "integer"}, max_price_usd={"type": ["integer", "null"]},
           top={"type": "array", "items": obj(id=S, reason=S)})
PROMPT = """Ты — подборщик квартир в аренду в Тбилиси. Подбери клиенту до 5 лучших квартир из каталога.

Как оценивать:
1. Жёсткие условия из запроса (бюджет, районы, число спален) — не нарушай. Бюджет можно превысить максимум на 10%.
   Животные: исключай только квартиры, где явно «нет». «Не указано» подходит — тогда в reason добавь «про животных уточнит агент».
2. Внешний вид — самое важное: сравни присланные клиентом фото (если есть) и его слова о стиле с профилем квартиры (ремонт / стиль / свет / мебель / looks / features).
3. Учитывай понравившиеся и отвергнутые клиентом квартиры: ищи похожие на первые и непохожие на вторые.
4. Верни 5 вариантов, если в каталоге есть хотя бы 5 подходящих по жёстким условиям; меньше — только если подходящих правда меньше.
   Если клиент не назвал условий (только фото или общий запрос) — жёстких условий нет, подбирай 5 самых похожих по виду.

Язык ответа для клиента: {lang}. understood и reason пиши на этом языке.
Никогда не упоминай сайты, каналы, агентства и источники объявлений.
understood: одной фразой, как ты понял запрос (для клиента). Не проси прислать фото повторно.
matches_total: сколько квартир каталога проходят жёсткие условия клиента.
max_price_usd: бюджет клиента в $ (null если не назван).
top: id из каталога + reason — одна фраза, почему подходит (без цены, её покажем отдельно).

Сообщения клиента (по порядку):
{msgs}

{feedback}
Каталог (id | район | $/мес | спальни | м² | этаж | ремонт, стиль, свет, мебель | особенности | впечатление 1-5 | как выглядит):
{catalog}"""
LANG_NAME = {"ru": "русский", "en": "English", "ka": "ქართული (грузинский)"}

NEAR = obj(place={"type": ["string", "null"]}, label={"type": ["string", "null"]}, radius_km={"type": ["number", "null"]})
NEAR_PROMPT = """Клиент ищет квартиру в Тбилиси. Если он хочет жить рядом с конкретным местом (метро, улица, парк, ТЦ,
университет, площадь, офис...), верни place — название для поиска на карте OpenStreetMap, латиницей, как в OSM
(например "Rustaveli metro station", "Vake Park", "Tbilisi Mall", "Freedom Square"). Если только район целиком
(Ваке, Сабуртало) или места нет — place = null. label: то же место на языке «{lang}» (по-русски — в родительном падеже:
«метро Руставели», «Ваке-парка»; по-английски: "Rustaveli metro"). radius_km: если клиент назвал расстояние/время пешком
(10 мин ≈ 1 км), иначе null.

Сообщения клиента:
{msgs}"""


def profile_anchor(cl):
    """The questionnaire's map point (A1c) -> (latlng, label, radius_km) or None."""
    p = cl.get("profile") or {}
    pin = p.get("pin")
    return pin and ((pin["lat"], pin["lng"]), pin.get("label") or t(cl["lang"], "picked"), p.get("radius_km") or 2.0)


def profile_query(cl):
    """Hard conditions from the questionnaire (clients.profile, A1a-A1c) in the hybrid query format."""
    p = cl.get("profile") or {}
    beds = sorted(int(b) for b in p.get("bedrooms") or [])
    pets = [x for x in p.get("pets") or []]
    return {"max_price_usd": p.get("budget_max"), "bedrooms_min": beds[0] if beds else None,
            "bedrooms_max": None if not beds or beds[-1] >= 4 else beds[-1],
            "districts": [d for d in p.get("districts") or [] if d != "any"],
            "pets": (any(x != "none" for x in pets) or None) if pets else None}


def merge_query(q, pq):
    """The client's words win over the questionnaire; the questionnaire fills what the words don't say."""
    out = dict(q)
    for k, v in pq.items():
        if out.get(k) in (None, [], "") and v not in (None, []):
            out[k] = v
    return out


def profile_line(cl):
    """The questionnaire as one line for the matching prompt."""
    from app.telegram.texts import profile_summary
    p = cl.get("profile") or {}
    return p and ("Анкета клиента: " + profile_summary(p, "ru", one_line=True))


def locate(cl):
    """Client's anchor point: a shared pin wins, else a place named in text, else the questionnaire's map point
    -> (latlng, label, radius_km) or None."""
    st, lang = cl["state"], cl["lang"]
    if st["pin"]:
        return tuple(st["pin"]), st["pin_label"] or t(lang, "pin"), st["pin_radius"] or 2.0
    key = "\n".join(st["msgs"])
    if not key:
        return profile_anchor(cl)
    if not st["near"] or st["near"][0] != key:
        res = ask(NEAR_PROMPT.format(msgs=key, lang=LANG_NAME[lang]), NEAR, timeout=120)
        p = res["place"] and geocode(res["place"])
        st["near"] = [key, [list(p), res["label"] or res["place"], res["radius_km"] or 2.0] if p else None]
    return (st["near"][1] and (tuple(st["near"][1][0]), st["near"][1][1], st["near"][1][2])) or profile_anchor(cl)


def line(i, dist=None):
    m = (i.get("details") or {}).get("metro_ru")
    return (f"{i['id']} | {f'{dist:.1f} км | ' if dist is not None else ''}{i.get('district')}{f' (м. {m})' if m else ''} | ${i.get('price_usd')} | "
            f"{i.get('bedrooms')} сп | {i.get('area')} | {i.get('floor')} эт | {i.get('renovation')}, {i.get('style')}, "
            f"{i.get('light')}, мебель {i.get('furniture')} | {', '.join(i.get('features') or [])} | {i.get('impression')} | {i.get('looks')}")


def search(cl):
    """Up to 5 new published listings for the client's current request.
    -> {"understood", "total", "base", "items": [{"listing", "reason", "near"}]}; marks them shown.
    Setting search_mode: llm (whole catalog to Codex, default) | hybrid (see search_hybrid)."""
    return search_hybrid(cl) if db.get_setting("search_mode") == "hybrid" else search_llm(cl)


# commercial property (offices, shops: details.property=commercial) is shown only to clients who ask for it in words
COMMERCIAL = re.compile(r"(?<!home )(?<!домашний )офис|коммерч|нежил\w* помещ|торгов\w* (?:площад|помещ)|под (?:бизнес|магазин|кафе|"
                        r"ресторан|салон|офис|склад)|помещение для бизнеса|склад(?!н)|(?<!home )office|commercial|retail|storefront|"
                        r"shop space|business (?:space|premises)|warehouse|ოფის|კომერციულ|სავაჭრო|საწყობ", re.I)


def wants_commercial(msgs):
    return any(COMMERCIAL.search(m or "") for m in msgs)


def for_client(pool, st):
    """Search pool without what the client didn't ask for: shown ones; commercial unless asked."""
    biz = wants_commercial(st["msgs"])
    return [i for i in pool if i["id"] not in st["shown"] and (biz or (i.get("details") or {}).get("property") != "commercial")]


def search_llm(cl):
    st, lang = cl["state"], cl["lang"]
    base = published()
    by_id = {i["id"]: i for i in base}
    pool = for_client(base, st)
    anchor = locate(cl)
    dist = {}
    if anchor:
        point, label, radius = anchor
        dist = {i["id"]: km(point, (i["lat"], i["lng"])) for i in pool if i.get("lat") is not None}
        pool = sorted((i for i in pool if i["id"] in dist), key=lambda i: dist[i["id"]])
        near = [i for i in pool if dist[i["id"]] <= radius * 1.5]
        pool = near if len(near) >= 15 else pool[:40]  # too few nearby -> nearest 40
    pq = profile_query(cl)
    if any(v not in (None, []) for v in pq.values()):  # questionnaire: hard filter in code (relaxed if < 5 fit)
        pool = candidates(pool, pq)[0]
    fb = ""
    if liked := reactions(cl, "like"):
        fb += "Понравились клиенту:\n" + "\n".join(map(line, liked)) + "\n"
    if disliked := reactions(cl, "dislike"):
        fb += "Клиент отверг:\n" + "\n".join(map(line, disliked)) + "\n"
    msgs = ([profile_line(cl)] if cl.get("profile") else []) + st["msgs"]
    prompt = PROMPT.format(lang=LANG_NAME[lang], msgs="\n".join(f"- {m}" for m in msgs) or "- (только фото)",
                           feedback=fb, catalog="\n".join(line(i, dist.get(i["id"])) for i in pool))
    if anchor:
        prompt += (f"\n\nКлиент хочет жить рядом с: {anchor[1]} (желательно в пределах {anchor[2]:g} км). "
                   "Столбец «км» — расстояние до этого места (адреса геокодированы примерно). Ближе = лучше при прочих равных.")
    res = ask(prompt, PICK, images=st["images"][-5:], timeout=240)
    budget = res["max_price_usd"] or pq["max_price_usd"]
    cap = budget and budget * 1.1
    ok = {i["id"] for i in pool}  # the model only saw the pool: no shown, unpublished or unasked-for commercial ids
    top = [x for x in res["top"] if x["id"] in ok
           and not (cap and (by_id[x["id"]].get("price_usd") or 0) > cap)][:5]
    items = []
    for x in top:
        near = x["id"] in dist and f"~{dist[x['id']]:.1f} {t(lang, 'km')} {t(lang, 'from')} {anchor[1]}"
        items.append({"listing": by_id[x["id"]], "reason": public(x["reason"]), "near": near or None})
    return _finish(cl, items, res["understood"], res.get("matches_total"), len(base), near=st["near"])


def _finish(cl, items, understood, total, base, **state):
    """Common end of a search: English texts, mark shown, keep the caches (state), count the search."""
    if cl["lang"] != "ru":  # en and ka clients see the English card texts
        translate([x["listing"] for x in items])
    update(cl, lambda s, r: (s["shown"].extend(x["listing"]["id"] for x in items), s.update(state)))
    with db.tx() as c:
        sid = c.execute("INSERT INTO searches (client_id, results, at) VALUES (?, ?, ?) RETURNING id",
                        (cl["id"], len(items), db.now())).fetchone()[0]
    return {"understood": public(understood), "total": max(total or 0, len(items)), "base": base, "items": items,
            "search_id": sid}


# ---------- hybrid search (search_mode=hybrid): filters in code + local embeddings, Codex reads ~20 lines ----------

INT_NULL = {"type": ["integer", "null"]}
QUERY = obj(place={"type": ["string", "null"]}, label={"type": ["string", "null"]}, radius_km={"type": ["number", "null"]},
            understood=S, max_price_usd=INT_NULL, bedrooms_min=INT_NULL, bedrooms_max=INT_NULL,
            districts={"type": "array", "items": S}, pets={"type": ["boolean", "null"]}, style=S)
QUERY_KEYS = ("understood", "max_price_usd", "bedrooms_min", "bedrooms_max", "districts", "pets", "style")
QUERY_PROMPT = NEAR_PROMPT.split("\n\nСообщения клиента:")[0] + """

Ещё извлеки жёсткие условия клиента (null или [] если не названы; более поздние сообщения уточняют ранние):
max_price_usd: бюджет в $ в месяц (лари → $ по курсу 2.7);
bedrooms_min, bedrooms_max: спальни («2 спальни» → 2 и 2; «от 2 спален» → 2 и null; «3-комнатная» = 2 спальни; студия → 0 и 1);
districts: районы Тбилиси, названные клиентом, по-русски, как в базе: {districts};
pets: true если у клиента животное, false если явно без животных, иначе null;
style: по-английски, коротко, как должна выглядеть квартира (стиль, свет, мебель, вид, балкон...) — для поиска по фото; "" если ничего;
understood: одной фразой на языке «{lang}», как ты понял запрос (для клиента).

Сообщения клиента:
{msgs}"""
HYBRID_POOL, HYBRID_MIN = 20, 5
W = {"photo": 1.0, "style": 0.8, "text": 0.4, "like": 0.6, "dislike": 0.4, "impression": 0.2, "dist": 0.5, "unknown": 1.0}
RELAX = ("districts", "bedrooms", "radius")  # dropped in this order while fewer than HYBRID_MIN fit; the budget never
RELAX_RU = {"districts": "район", "bedrooms": "число спален", "radius": "расстояние"}


def understand(cl):
    """One short Codex call per message set (cached in state like locate): place + hard constraints + style.
    -> (query dict, anchor (latlng, label, radius_km) or None)."""
    st, lang = cl["state"], cl["lang"]
    key = "\n".join(st["msgs"])
    q = {"districts": [], "style": ""}
    if key:
        if not st.get("query") or st["query"][0] != key:
            with db.tx() as c:
                ds = [r[0] for r in c.execute(f"SELECT l.district FROM listings l WHERE {CLIENT_ELIGIBLE} AND l.district != '' "
                                              "GROUP BY l.district ORDER BY COUNT(*) DESC LIMIT 40")]
            res = ask(QUERY_PROMPT.format(msgs=key, lang=LANG_NAME[lang], districts=", ".join(ds)), QUERY, timeout=120)
            p = res.get("place") and geocode(res["place"])
            st["near"] = [key, [list(p), res.get("label") or res["place"], res.get("radius_km") or 2.0] if p else None]
            st["query"] = [key, {k: res.get(k) for k in QUERY_KEYS}]
        q = st["query"][1]
    q = merge_query(q, profile_query(cl))
    if st["pin"]:
        return q, (tuple(st["pin"]), st["pin_label"] or t(lang, "pin"), st["pin_radius"] or 2.0)
    near = key and st["near"] and st["near"][0] == key and st["near"][1]
    return q, (near and (tuple(near[0]), near[1], near[2])) or profile_anchor(cl)


def _district_ok(i, wanted):
    d = (i.get("district") or "").lower()
    return any(w.lower() in d or (d and d in w.lower()) for w in wanted)


def hard_filter(pool, q, dist=None, radius=None, drop=()):
    """Listings passing the client's hard conditions; `drop` names relaxed ones (RELAX). Unknown values pass,
    except distance: with an anchor a listing without coordinates can't be checked."""
    cap = q.get("max_price_usd") and q["max_price_usd"] * 1.1
    lo, hi = q.get("bedrooms_min"), q.get("bedrooms_max")
    out = []
    for i in pool:
        if cap and (i.get("price_usd") or 0) > cap:
            continue
        b = i.get("bedrooms")
        if "bedrooms" not in drop and b is not None and ((lo is not None and b < lo) or (hi is not None and b > hi)):
            continue
        if "districts" not in drop and q.get("districts") and not _district_ok(i, q["districts"]):
            continue
        if q.get("pets") and i.get("pets") == "нет":
            continue
        if dist is not None and (i["id"] not in dist or ("radius" not in drop and dist[i["id"]] > radius * 1.5)):
            continue
        out.append(i)
    return out


def candidates(pool, q, dist=None, radius=None):
    """Hard filter, then relax (RELAX order) until at least HYBRID_MIN remain. -> (listings, strict count, relaxed names)"""
    strict = hard_filter(pool, q, dist, radius)
    got, drop = strict, ()
    for name in RELAX:
        if len(got) >= HYBRID_MIN:
            break
        if name == "radius" and dist is None:
            continue
        drop += (name,)
        got = hard_filter(pool, q, dist, radius, drop)
    return got, len(strict), drop


def _z(x):
    x = np.asarray(x, dtype=np.float32)
    s = x.std()
    return (x - x.mean()) / s if s > 1e-6 else np.zeros_like(x)


def rank(cands, q, images=(), liked=(), disliked=(), dist=None, vec=None, emb=None):
    """Score = weighted sum (W) of z-scored signals over the candidates:
    photo  client photos <-> listing photos (best photo pair, averaged over client photos)
    style  client style text <-> listing photos;  text  client style text <-> listing profile text
    like / dislike  similarity to liked (+) / disliked (-) listings' photos;  impression;  dist (closer = better);
    unknown  a fixed penalty per asked-for condition the listing can't confirm (no price / no bedrooms).
    Listings without vectors get the average for that signal. -> {id: score}"""
    n = len(cands)
    if not n:
        return {}
    score = np.zeros(n, np.float32)
    row = [vec["index"].get(i["id"]) if vec else None for i in cands]
    have = np.array([r is not None for r in row])

    def add(name, values):  # values: per-candidate sims for those with vectors
        x = np.zeros(n, np.float32)
        x[have] = _z(values)
        score[:] += W[name] * x

    if vec and have.any():
        rows = [r for r in row if r is not None]
        img, txt = vec["img"][rows], vec["txt"][rows]
        if len(images) and emb:
            ci = emb.client_images(list(images))
            if ci is not None:
                sims = ci @ vec["photo"].T  # (k, all photos)
                best = np.full((len(ci), len(vec["ids"])), -1.0, np.float32)
                for k in range(len(ci)):
                    np.maximum.at(best[k], vec["owner"], sims[k])
                add("photo", best[:, rows].mean(0))
        if q.get("style") and emb:
            st = emb.text(q["style"])
            if st is not None:
                add("style", img @ st)
                add("text", txt @ st)
        for name, ids in (("like", liked), ("dislike", disliked)):
            idx = [vec["index"][x] for x in ids if x in vec["index"]]
            if idx:
                c = vec["img"][idx].mean(0)
                add(name, (img @ c) * (1 if name == "like" else -1))
    score += W["impression"] * _z([i.get("impression") or 3 for i in cands])
    beds = q.get("bedrooms_min") is not None or q.get("bedrooms_max") is not None  # unknown price/bedrooms pass the
    score -= W["unknown"] * np.array([bool(q.get("max_price_usd") and i.get("price_usd") is None)  # filter, but go last
                                      + bool(beds and i.get("bedrooms") is None) for i in cands], np.float32)
    if dist:
        score -= W["dist"] * _z([dist.get(i["id"], 99) for i in cands])
    return {i["id"]: float(s) for i, s in zip(cands, score)}


EN_VOCAB = {"новый": "new", "евроремонт": "modern renovation", "дизайнерский": "designer", "советский": "Soviet-era",
            "требует ремонта": "needs renovation", "белый каркас": "unfinished", "минимализм": "minimalist",
            "современный": "modern", "классика": "classic", "лофт": "loft", "сканди": "Scandinavian",
            "старый тбилисский": "old Tbilisi", "смешанный": "mixed", "светлая": "bright", "средняя": "medium light",
            "тёмная": "dark", "хорошая": "good", "старая": "old", "без мебели": "unfurnished", "новая": "new"}


def template_reason(i, lang):
    """final_llm=off: a short reason from the profile, no model."""
    if lang != "ru":
        v = lambda k: EN_VOCAB.get(i.get(k) or "", i.get(k) or "")
        return f"{v('style').capitalize()} style, {v('light')}, {v('renovation')} renovation, {v('furniture')} furniture"
    return (i.get("looks") or f"{i.get('style')}, {i.get('light')}, ремонт {i.get('renovation')}").rstrip(".")


def search_hybrid(cl):
    """Hard conditions from one short Codex call -> filter in code (relaxed if < 5 fit) -> rank by local SigLIP
    embeddings (embed.py) -> Codex picks 5 of the top 20 (final_llm=on) or the top 5 go as they are (off)."""
    import embed
    st, lang = cl["state"], cl["lang"]
    base = published()
    by_id = {i["id"]: i for i in base}
    pool = for_client(base, st)
    q, anchor = understand(cl)
    dist = {}
    if anchor:
        dist = {i["id"]: km(anchor[0], (i["lat"], i["lng"])) for i in pool if i.get("lat") is not None}
    cands, strict, relaxed = candidates(pool, q, dist if anchor else None, anchor and anchor[2])
    liked, disliked = reactions(cl, "like"), reactions(cl, "dislike")
    scores = rank(cands, q, st["images"][-5:], [i["id"] for i in liked], [i["id"] for i in disliked],
                  dist if anchor else None, embed.load(), embed)
    top20 = sorted(cands, key=lambda i: -scores[i["id"]])[:HYBRID_POOL]
    cap = q.get("max_price_usd") and q["max_price_usd"] * 1.1
    if top20 and db.get_setting("final_llm") != "off":
        fb = ""
        if liked:
            fb += "Понравились клиенту:\n" + "\n".join(map(line, liked)) + "\n"
        if disliked:
            fb += "Клиент отверг:\n" + "\n".join(map(line, disliked)) + "\n"
        msgs = ([profile_line(cl)] if cl.get("profile") else []) + st["msgs"]
        prompt = PROMPT.format(lang=LANG_NAME[lang], msgs="\n".join(f"- {m}" for m in msgs) or "- (только фото)",
                               feedback=fb, catalog="\n".join(line(i, dist.get(i["id"])) for i in top20))
        prompt += "\n\nКаталог уже отобран по условиям клиента и отсортирован по похожести (лучшие сверху)."
        if relaxed:
            prompt += (" Точно подходящих мало, поэтому в каталоге есть варианты с ослабленными условиями "
                       f"({', '.join(RELAX_RU[x] for x in relaxed)}): верни 5 лучших и в reason честно скажи, чем вариант отличается от запроса.")
        if anchor:
            prompt += (f"\n\nКлиент хочет жить рядом с: {anchor[1]} (желательно в пределах {anchor[2]:g} км). "
                       "Столбец «км» — расстояние до этого места (адреса геокодированы примерно). Ближе = лучше при прочих равных.")
        res = ask(prompt, PICK, images=st["images"][-5:], timeout=240)
        ok = {i["id"] for i in top20}
        picked = [(x["id"], x["reason"]) for x in res["top"] if x["id"] in ok]
        picked = list(dict(picked).items())[:5]
        understood = res["understood"] or q.get("understood") or ""
        if len(picked) < 5:  # the model is pickier than the hard conditions: top up with ones that meet them all
            if not picked:
                understood = q.get("understood") or understood  # its "nothing fits" sentence would contradict the cards
            fits = {i["id"] for i in hard_filter(top20, q, dist if anchor else None, anchor and anchor[2])}
            picked += [(i["id"], template_reason(i, lang)) for i in top20
                       if i["id"] in fits and i["id"] not in dict(picked)][:5 - len(picked)]
    else:
        picked = [(i["id"], template_reason(i, lang)) for i in top20[:5]]
        understood = q.get("understood") or ""
    items = []
    for lid, reason in picked:
        if cap and (by_id[lid].get("price_usd") or 0) > cap:
            continue
        near = lid in dist and f"~{dist[lid]:.1f} {t(lang, 'km')} {t(lang, 'from')} {anchor[1]}"
        items.append({"listing": by_id[lid], "reason": public(reason), "near": near or None})
    return _finish(cl, items, understood, strict, len(base), near=st["near"], query=st.get("query"))


TR = obj(items={"type": "array", "items": obj(id=S, district=S, description=S)})


def translate(listings):
    """English district + description, made once per listing on first show and stored."""
    # cards in the channel format already have an English headline from indexing: no call for those
    en = lambda i, k: i.get(k) or (i.get("details") or {}).get(k)
    todo = [i for i in listings if not (i.get("description_en") or en(i, "headline_en")) or not en(i, "district_en")]
    if not todo:
        return
    res = ask("Translate each apartment listing to natural English for a client. Keep facts, no phones, links, "
              "site or agency names. Return district (Tbilisi district in English) and description.\n\n"
              + "\n".join(json.dumps({"id": i["id"], "district": i.get("district"), "description": i.get("description_ru")},
                                     ensure_ascii=False) for i in todo), TR, timeout=120)
    got = {x["id"]: x for x in res["items"]}
    with db.tx() as c:
        for i in todo:
            if i["id"] in got:
                i["district_en"], i["description_en"] = got[i["id"]]["district"], got[i["id"]]["description"]
                c.execute("UPDATE listings SET district_en = ?, description_en = ? WHERE id = ?",
                          (i["district_en"], i["description_en"], i["id"]))


def card_body(i, lang="ru", escape=None):
    """The channel-style facts of a listing (facts.card_lines), source-free: every text field goes through public().
    No headline -> the indexed description. escape: html.escape for HTML messages (agents)."""
    blocks = facts.card_lines(i, lang)
    d = i.get("details") or {}
    if not d.get("headline_ru" if lang == "ru" else "headline_en"):
        desc = (lang != "ru" and i.get("description_en")) or i.get("description_ru") or ""
        if desc := public(desc):
            blocks.insert(1, [desc])
    text = "\n\n".join("\n".join(public(ln) for ln in b) for b in blocks)
    return escape(text) if escape else text


def card(i, reason, near=None, lang="ru"):
    """Client card in the format of the agency channel posts: the album (photos + video) is sent separately;
    no links, phones or sources. Telegram's 4096 chars at most (a real card is ~1000)."""
    tail = "\n".join(x for x in (near and f"🧭 {near}", reason and f"💡 {public(reason)}") if x)
    return (card_body(i, lang) + (f"\n\n{tail}" if tail else ""))[:4096]


ALBUM_PHOTOS, ALBUM_MAX = 4, 10  # Telegram albums take 2-10 items


def album(i, photos=ALBUM_PHOTOS):
    """What goes into a listing's album: up to `photos` photos, then at most one video (files that exist, video under
    the size cap: VIDEO_MAX_MB). -> [{"type": "photo"|"video", "path", ...video meta}]"""
    from imgutil import video_max
    out = [{"type": "photo", "path": p} for p in i.get("photos") or [] if os.path.exists(p)][:photos]
    for v in ((i.get("details") or {}).get("videos") or [])[:1]:
        if os.path.exists(v.get("path") or "") and os.path.getsize(v["path"]) <= video_max():
            out.append({"type": "video", **v})
    return out[:ALBUM_MAX]


# ---------- map: where the flat is (our OSM Mini App, webapp/index.html in pin mode) ----------

def pin_query(i, lang="ru"):
    """'pin=lat,lng&prec=…&title=…&lang=…' for /map, or None without coordinates. Title: district · price (no id or source)."""
    if i.get("lat") is None or i.get("lng") is None:
        return None
    district = (lang != "ru" and i.get("district_en")) or i.get("district") or t(lang, "city")
    return urlencode({"pin": f"{i['lat']:.6f},{i['lng']:.6f}", "prec": i.get("geo_precision") or "",
                      "title": f"{district} · ${i.get('price_usd')}", "lang": "ru" if lang == "ru" else "en"})


def pin_link(i, base=None, lang="ru"):
    """Full /map URL on our public host (AGENT_APP_URL), or None without coordinates or a public URL."""
    base = app_url() if base is None else base
    q = pin_query(i, lang)
    return f"{base}/map?{q}" if q and base else None


def card_keyboard(i, lang="ru", base=None, liked=False, like=None):
    """Client card buttons (Bot API JSON): ❤️/👎 (or "sent" after a like) and, when possible, "📍 On the map"
    (a web_app button: private chats only, which is where clients are). like: the ❤️ button (notify.like_button: the
    operator DM link for a client who never contacted the operator), default the callback like:<id>."""
    rows = [[{"text": t(lang, "sent"), "callback_data": "noop"}] if liked else
            [like or {"text": t(lang, "like"), "callback_data": f"like:{i['id']}"},
             {"text": t(lang, "dislike"), "callback_data": f"no:{i['id']}"}]]
    extra = facts.media_links(i, lang)  # 🎥 video / 🧊 3D tour links, only on hosts that don't reveal the source
    if url := pin_link(i, base, lang):
        extra.insert(0, {"text": t(lang, "on_map"), "web_app": {"url": url}})
    if extra:
        rows.append(extra)
    return {"inline_keyboard": rows}


def geo_line(i):
    """Agent card line: how precise the point is (the map itself is a button under the card)."""
    return f"\n📍 Точка: {html.escape(i.get('geo_precision') or '?')}" if i.get("lat") is not None else "\n📍 нет координат"


# ---------- reactions & leads ----------

def react(cl, lid, kind):
    """kind: like | dislike. -> None if the listing is no longer shown, else {"listing": ...}.
    The lead itself (❤️ -> заявка) is created by the backend: app.services.leads.create_from_like."""
    i = client_listing(lid)
    if not i:
        return None
    with db.tx() as c:
        c.execute("INSERT INTO reactions (client_id, listing_id, reaction, created_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT (client_id, listing_id) DO UPDATE SET reaction = excluded.reaction",
                  (cl["id"], lid, kind, db.now()))
    return {"listing": i}


def log_view(cl, lid, search_id=None, source="search"):
    """A card the client got, kept for good (client_views, stage 8): state["shown"] is wiped by every new request."""
    with db.tx() as c:
        c.execute("INSERT INTO client_views (client_id, listing_id, property_id, search_id, source) "
                  "VALUES (?, ?, (SELECT id FROM properties WHERE listing_id = ?), ?, ?)", (cl["id"], lid, lid, search_id, source))


def who(cl, as_html=True):
    if cl["channel"] == "tg":
        if cl["username"]:
            return f"@{cl['username']}"
        return f'<a href="tg://user?id={cl["external_id"]}">{html.escape(cl["name"] or "?")}</a>' if as_html else f"tg id {cl['external_id']}"
    return cl["phone"] or f"{cl['channel']}:{cl['external_id']}"


def staff_lines(i):
    """Agent-only part of lead/review cards (HTML): the address as the source wrote it, the pin, media, source and links."""
    e = lambda v: html.escape(str(v or ""))
    d = i.get("details") or {}
    media = [f"🎬 видео в альбоме ({len(d['videos'])})"] if d.get("videos") else []
    media += [f"🎥 {e(d['video_url'])}"] if d.get("video_url") else []
    media += [f"🧊 {e(d['tour_url'])}"] if d.get("tour_url") else []
    return (f"Адрес в источнике: {e(i.get('address')) or '—'}{geo_line(i)}\n" + "".join(m + "\n" for m in media) +
            f"Источник: {i['source']}" + (f" (+ дубли: {', '.join(i['dup_sources'])})" if i.get("dup_sources") else "") +
            "\n" + "\n".join(map(e, i["urls"])))


def app_url():
    """Public HTTPS base of the platform (Mini App, /map), env PUBLIC_URL (legacy AGENT_APP_URL); '' = not set up."""
    u = (os.environ.get("PUBLIC_URL") or os.environ.get("AGENT_APP_URL") or "").strip().rstrip("/")
    return u.removesuffix("/tg") if u.startswith("https://") else ""


# ---------- agents & delivery ----------

def agent(tg_id, name=None, username=None, create=False):
    """Active staff user (users table: operator/agent/admin/superadmin/recruit) by Telegram id, or None. A user added
    in the CRM by @username gets bound to the Telegram id the first time that username shows up here.
    create=True (/agent <code>): registers a new Telegram user as an agent; a deactivated user stays deactivated."""
    with db.tx() as c:
        r = c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        if not r and username:
            c.execute("UPDATE users SET tg_id = ? WHERE id = (SELECT id FROM users WHERE tg_id IS NULL "
                      "AND lower(ltrim(username, '@')) = lower(?) LIMIT 1)", (tg_id, username))
            r = c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        if create:
            if r:
                c.execute("UPDATE users SET username = COALESCE(?, username) WHERE id = ?", (username, r["id"]))
            else:
                c.execute("INSERT INTO users (tg_id, name, username, role, is_active, created_at) VALUES (?, ?, ?, 'agent', true, now())",
                          (tg_id, name or username or f"tg {tg_id}", username))
            r = c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        return dict(r) if r and r["is_active"] and r["role"] in (*db.STAFF_ROLES, "recruit") else None


def staff_chats():
    """Where review cards go: the group chat (setting lead_target=group) or every active staff member in private."""
    with db.tx() as c:
        if db.get_setting("lead_target", c) == "group" and db.get_setting("group_chat_id", c):
            return [int(db.get_setting("group_chat_id", c))]
        return [r[0] for r in c.execute(f"SELECT tg_id FROM users WHERE {db.STAFF_SQL} AND tg_id IS NOT NULL ORDER BY id")]


def remember_message(kind, ref, chat_id, message_id):
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO messages (kind, ref, chat_id, message_id) VALUES (?, ?, ?, ?)",
                  (kind, str(ref), chat_id, message_id))


def messages(kind, ref):
    with db.tx() as c:
        return [tuple(r) for r in c.execute("SELECT chat_id, message_id FROM messages WHERE kind = ? AND ref = ?", (kind, str(ref)))]


# ---------- moderation ----------

def pending(limit=5, unsent_only=True):
    with db.tx() as c:
        q = "SELECT * FROM listings WHERE status = 'pending_review'" + (" AND review_sent_at IS NULL" if unsent_only else "")
        return [db.row(r) for r in c.execute(q + " ORDER BY first_seen LIMIT ?", (limit,))]


def mark_review_sent(lid):
    with db.tx() as c:
        c.execute("UPDATE listings SET review_sent_at = ? WHERE id = ?", (db.now(), lid))


def moderate(lid, approve, agent_id):
    """Human decision on a listing: published or rejected; kept across future syncs."""
    with db.tx() as c:
        c.execute("UPDATE listings SET decision = ?, decided_by = ?, decided_at = ?, updated_at = ?, "
                  "status = CASE WHEN status = 'gone' THEN 'gone' ELSE ? END, "
                  "status_reason = CASE WHEN ?::boolean THEN NULL ELSE 'отклонено агентом' END WHERE id = ?",
                  ("approved" if approve else "rejected", agent_id, db.now(), db.now(),
                   "published" if approve else "rejected", approve, lid))
    return listing(lid)


def review_text(i):
    """Agent-facing card for a pending listing (HTML, not length-limited: send it via review_card)."""
    e = lambda v: html.escape(str(v or ""))
    return (f"🕵️ <b>Проверка объявления</b> {i['id']}\nПочему: {e(i.get('status_reason')) or '—'}\n\n"
            f"{card_body(i, escape=html.escape)}\n\n" + staff_lines(i))


def review_card(i, footer=""):
    """review_text + an optional HTML footer, fitted to Telegram's 4096 without cutting a tag/entity or the footer."""
    from app.telegram.client import clip
    return clip(review_text(i), 4096 - len(footer) - 2) + footer
