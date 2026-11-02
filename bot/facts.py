"""Structured listing facts in the agency channel format (@rent_tbilisi_ge), their sources and the card lines.

listing["details"] (JSON column `details`, docs/schema.md) holds the fields below; every key is optional, None = unknown.
Values are language-neutral codes (rendered in ru/en here) or *_ru / *_en text pairs:
  metro_ru/metro_en, address_ru/address_en, headline_ru/headline_en, district_en,
  property (apartment|house|commercial), building (new|old|historical|construction),
  condition (new|good|mixed|old|white_frame|needs_repair|retro), floors, heating (central|gas|electric|none),
  bath (shower|bath|both), bathrooms, yes / no (amenity codes from AMENITIES), tenants ("1-3"),
  pets (allowed|by_agreement|not_allowed; the `pets` column keeps the Russian word for search), terms (months),
  deposit_usd, commission ("0%"), exclusive, video_url, tour_url, videos ([{path, w, h, duration}]).
Deterministic parsers (agency posts, ss.ge and myhome.ge fields) win over the model, which fills the rest (ingest.py)."""
import re
from urllib.parse import unquote, urlparse

# ---------- vocabularies ----------

# code: (ru, en, aliases). Order = display order on the card, like the channel (outside, media, kitchen, appliances, building)
AMENITIES = {
    "balcony": ("Балкон", "Balcony", "balcony loggia балкон лоджия აივანი"),
    "terrace": ("Терраса", "Terrace", "terrace терраса"),
    "courtyard": ("Двор", "Courtyard", "courtyard yard двор"),
    "garden": ("Сад", "Garden", "garden сад"),
    "wifi": ("Wi-Fi", "Wi-Fi", "wifi internet интернет вайфай"),
    "tv": ("ТВ", "TV", "tv smarttv телевизор smart_tv cabletelevision"),
    "stove": ("Плита", "Stove", "stove плита gasstove"),
    "oven": ("Духовка", "Oven", "oven духовка"),
    "microwave": ("Микроволновка", "Microwave", "microwave микроволновка"),
    "dishwasher": ("Посудомойка", "Dishwasher", "dishwasher посудомойка посудомоечная"),
    "fridge": ("Холодильник", "Fridge", "fridge refrigerator холодильник"),
    "conditioner": ("Кондиционер", "Air conditioner", "conditioner condintioner airconditioning кондиционер"),
    "washing_machine": ("Стиральная машина", "Washing machine", "washingmachine washer laundry стиральная стиралка"),
    "dryer": ("Сушильная машина", "Dryer", "dryer сушильная"),
    "vacuum": ("Пылесос", "Vacuum cleaner", "vacuumcleaner vacuum пылесос"),
    "fireplace": ("Камин", "Fireplace", "fireplace камин"),
    "wardrobe": ("Гардероб", "Wardrobe", "wardrobe гардероб гардеробная"),
    "furniture": ("Мебель", "Furniture", "furniture мебель"),
    "elevator": ("Лифт", "Elevator", "elevator lift лифт"),
    "parking": ("Парковка", "Parking", "parkingplace parking parkingpalace garage парковка паркинг гараж"),
    "storage": ("Кладовка", "Storage room", "storage storeroom кладовка"),
    "pool": ("Бассейн", "Pool", "pool swimmingpool бассейн"),
    "gym": ("Спортзал", "Gym", "gym спортзал тренажерный"),
    "generator": ("Генератор", "Generator", "generator генератор"),
    "security": ("Охрана", "Security", "security охрана"),
    "playground": ("Детская площадка", "Playground", "playground детская"),
}
_AMENITY = {a: code for code, (_, _, al) in AMENITIES.items() for a in al.split()}
NO_SHOWN = ("elevator", "parking", "conditioner", "oven", "dishwasher", "washing_machine")  # ✖️ lines: important absences only

# Tbilisi metro: canonical (ru, en) and aliases (agency hashtags, ss.ge station codes, Russian spellings), lowercased alnum
METRO = {
    ("Театр Ахметели", "Akhmeteli Theatre"): "ahmetelitheatre akhmetelitheatre akhmeteli ахметели театрахметели",
    ("Сараджишвили", "Sarajishvili"): "sarajishvili сараджишвили",
    ("Гурамишвили", "Guramishvili"): "guramishvili гурамишвили",
    ("Грмагеле", "Grmagele"): "grmagele грмагеле",
    ("Дидубе", "Didube"): "didube дидубе",
    ("Гоциридзе", "Gotsiridze"): "gotsiridze гоциридзе",
    ("Надзаладеви", "Nadzaladevi"): "nadzaladevi надзаладеви",
    ("Вокзальная площадь", "Station Square"): "stationsquare sadgurismoedani вокзальная вокзальнаяплощадь",
    ("Марджанишвили", "Marjanishvili"): "marjanishvili марджанишвили",
    ("Руставели", "Rustaveli"): "rustaveli руставели",
    ("Площадь Свободы", "Liberty Square"): "libertysquare freedomsquare tavisuplebismoedani площадьсвободы",
    ("Авлабари", "Avlabari"): "avlabari авлабари",
    ("300 Арагвели", "300 Aragveli"): "300aragveli samasiaragveli 300арагвели",
    ("Исани", "Isani"): "isanimetro isani исани",
    ("Самгори", "Samgori"): "samgorimetro samgori самгори",
    ("Варкетили", "Varketili"): "varketili варкетили",
    ("Госуниверситет", "State University"): "stuniversity stateuni stateuniversity universiteti госуниверситет госуниверситета госуниверситет",
    ("Важа-Пшавела", "Vazha-Pshavela"): "vazhapshavela важапшавела",
    ("Делиси", "Delisi"): "delisi делиси",
    ("Медуниверситет", "Medical University"): "mcuniversity medicaluni medicaluniversity медуниверситет медуниверситета",
    ("Техуниверситет", "Technical University"): "tcuniversity techuni technicaluniversity техуниверситет техуниверситета техуниверсит",
    ("Церетели", "Tsereteli"): "tsereteli церетели",
}
_METRO = {a: names for names, al in METRO.items() for a in al.split()}

# agency first-line district hashtags -> Russian (the model fills the rest)
DISTRICTS_EN = {"vake": "Ваке", "saburtalo": "Сабуртало", "vera": "Вера", "sololaki": "Сололаки", "mtatsminda": "Мтацминда",
                "chugureti": "Чугурети", "didube": "Дидубе", "gldani": "Глдани", "samgori": "Самгори", "isani": "Исани",
                "avlabari": "Авлабари", "ortachala": "Ортачала", "krtsanisi": "Крцаниси", "nadzaladevi": "Надзаладеви",
                "didigomi": "Диди Дигоми", "dididighomi": "Диди Дигоми", "digomi": "Дигоми",
                "dighomimassive": "Дигомский массив", "nutsubidze": "Нуцубидзе", "lisi": "Лиси", "tskhneti": "Цхнети",
                "vazisubani": "Вазисубани", "varketili": "Варкетили", "bagebi": "Багеби", "mukhiani": "Мухиани",
                "marjanishvili": "Марджанишвили", "bakhtrioni": "Бахтриони", "okrokana": "Окрокана", "kojori": "Коджори"}

LABELS = {
    "property": {"apartment": ("Квартира", "Apartment"), "house": ("Дом", "House"), "commercial": ("Коммерческое помещение", "Commercial space")},
    "building": {"new": ("Новостройка", "New building"), "old": ("Старый фонд", "Old building"),
                 "historical": ("Исторический дом", "Historical building"), "construction": ("Строящийся дом", "Under construction")},
    "condition": {"new": ("новый ремонт", "new renovation"), "good": ("хороший ремонт", "good renovation"),
                  "mixed": ("смешанный стиль", "mixed style"), "old": ("старый ремонт", "old renovation"),
                  "white_frame": ("белый каркас", "white frame"), "needs_repair": ("требует ремонта", "needs renovation"),
                  "retro": ("ретро", "retro")},
    "heating": {"central": ("центральное отопление", "central heating"), "gas": ("газовое отопление", "gas heating"),
                "electric": ("электрическое отопление", "electric heating"), "none": ("без отопления", "no heating")},
    "bath": {"shower": ("душ", "shower"), "bath": ("ванна", "bath"), "both": ("ванна и душ", "bath and shower")},
    "pets": {"allowed": ("можно", "allowed"), "by_agreement": ("по договорённости", "by agreement"),
             "not_allowed": ("нельзя", "not allowed")},
}
PETS_RU = {"allowed": "да", "by_agreement": "по договорённости", "not_allowed": "нет"}  # the old `pets` column (search)
ENUMS = {k: list(v) for k, v in LABELS.items()}

GEL_PER_USD, EUR_TO_USD = 2.7, 1.08


def _key(s):
    return re.sub(r"[^0-9a-zа-яё]", "", (s or "").lower())


def amenity(word):
    return _AMENITY.get(_key(word))


def metro(name):
    """Any spelling (agency hashtag, ss.ge code, Russian) -> (ru, en) or None."""
    k = _key(name)
    if k in _METRO:
        return _METRO[k]
    return next((v for a, v in _METRO.items() if len(a) >= 6 and k.startswith(a)), None)


def camel(tag):
    """'LibertySquare' -> 'Liberty Square', 'DidiDighomi' -> 'Didi Dighomi'."""
    return re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", tag)


def usd(amount, cur):
    a = float(str(amount).replace(",", "").replace(" ", ""))
    return round(a / GEL_PER_USD if cur in ("gel", "₾", "лари") else a * EUR_TO_USD if cur in ("€", "eur") else a)


# ---------- coordinates from map links in a post ----------

def maps_point(links):
    """Google Maps place link -> (lat, lng): the place pin (!3d..!4d..) or the viewport centre (@lat,lng)."""
    for u in links or ():
        u = unquote(u)
        if "google." not in u or "/maps" not in u:
            continue
        m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", u) or re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", u) \
            or re.search(r"[?&](?:q|query|ll)=(-?\d+\.\d+),\s*(-?\d+\.\d+)", u)
        if m:
            lat, lng = float(m[1]), float(m[2])
            if 41.5 < lat < 42.0 and 44.5 < lng < 45.2:  # Tbilisi only: a wrong link must not move the pin abroad
                return lat, lng
    return None


# ---------- the agency channel format ----------

def parse_agency(t):
    """@rent_tbilisi_ge post (hashtag format, see the README "Карточка") -> details. Also fits competitors that copy it."""
    d, lines = {}, [ln.strip() for ln in t.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    if "🚇" in first or first.startswith("#"):
        left, _, right = first.partition("🚇")
        tags = re.findall(r"#(\w+)", left)
        if tags:
            d["district_en"] = camel(tags[0])
            d["district_ru"] = DISTRICTS_EN.get(tags[0].lower())
        if m := re.search(r"#(\w+)", right):
            ru_en = metro(m[1])
            d["metro_ru"], d["metro_en"] = ru_en or (None, camel(m[1]))
    for ln in lines:
        if ln.startswith("📍") and "ON MAP" not in ln.upper():
            d.setdefault("address_en", ln.strip("📍 ").strip())
    if m := re.search(r"⭐️\s*(.+?)\s*⭐️", t, re.S):
        d["headline_en"] = re.sub(r"\s+", " ", m[1]).strip()
    if re.search(r"#Exclusive", t, re.I):
        d["exclusive"] = True
    if m := re.search(r"🏢\s*(.+)|🏡\s*(#.+)", t):
        line = m[1] or m[2]
        d["property"] = ("commercial" if re.search(r"#Commercial", line, re.I) and not re.search(r"#Apartment", line, re.I)
                         else "house" if re.search(r"#House", line, re.I) else "apartment")
        if b := re.search(r"#(\d+)\s*Bed", line, re.I):
            d["bedrooms"] = int(b[1])
    if m := re.search(r"✨\s*(#.+)", t):
        line = m[1].lower()
        d["building"] = next((v for k, v in (("historical", "historical"), ("underconstruction", "construction"),
                                              ("newbuilding", "new"), ("oldbuilding", "old")) if k in line), None)
        tags = re.findall(r"#(\w+)", line)[1:]
        d["condition"] = next((v for k, v in (("whiteframe", "white_frame"), ("white", "white_frame"), ("mixed", "mixed"),
                                              ("retro", "retro"), ("new", "new"), ("old", "old")) if k in tags), None)
    if m := re.search(r"🏠\s*(.+)", t):
        line = m[1]
        if a := re.search(r"(\d+(?:[.,]\d+)?)\s*sq\.?\s*m", line, re.I):
            d["area"] = float(a[1].replace(",", "."))
        if f := re.search(r"(\d+)\s*/\s*(\d+)\s*Floor", line, re.I):
            d["floor"], d["floors"] = int(f[1]), int(f[2])
        elif f := re.search(r"(\d+)\s*Floor", line, re.I):
            d["floor"] = int(f[1])
        low = line.lower()
        d["heating"] = next((v for k, v in (("centralheating", "central"), ("gasheating", "gas"),
                                             ("electricalheating", "electric"), ("electricheating", "electric")) if k in low), None)
        sh, bt = "#shower" in low, re.search(r"#bath\b", low)
        d["bath"] = "both" if sh and bt else "shower" if sh else "bath" if bt else None
        if n := re.search(r"#(\d)\s*bathroom", low):
            d["bathrooms"] = int(n[1])
    yes = [c for c in (amenity(w) for w in re.findall(r"✅\s*#?(\w+)", t)) if c]
    no = [c for c in (amenity(w) for w in re.findall(r"✖️\s*#?(\w+)", t)) if c]
    d["yes"], d["no"] = list(dict.fromkeys(yes)), [c for c in dict.fromkeys(no) if c not in yes]
    if m := re.search(r"Tenants\s*:?\s*(\d+\s*-\s*\d+|\d+)", t, re.I):
        d["tenants"] = re.sub(r"\s", "", m[1])
    if m := re.search(r"Pets\s*:?\s*#?(\w+)", t, re.I):
        d["pets"] = {"allowed": "allowed", "notallowed": "not_allowed", "byagreement": "by_agreement"}.get(m[1].lower())
    if m := re.search(r"🕐\s*(.+)", t):
        d["terms"] = sorted({int(x) for x in re.findall(r"#?(\d+)\s*Month", m[1], re.I)})
    if m := re.search(r"💰\s*(.+)", t):
        line = m[1]
        p = re.search(r"\$\s*([\d,]+)|([\d,]+)\s*(\$|€|gel|₾)", line, re.I)
        if p:
            cur = "$" if p[1] else p[3].lower()
            d["price_usd"] = usd(p[1] or p[2], cur)
        dep = re.search(r"deposit\s*\$?\s*([\d,]+)\s*(\$|€|gel|₾)?|([\d,]+)\s*(\$|€|gel|₾)\s*deposit", line, re.I)
        if dep:
            d["deposit_usd"] = usd(dep[1] or dep[3], (dep[2] or dep[4] or "$").lower())
    if m := re.search(r"(\d+)\s*%\s*Comm?iss?ion", t, re.I):
        d["commission"] = f"{m[1]}%"
    return {k: v for k, v in d.items() if v not in (None, [], "")}


def parse_ru(t):
    """Russian competitor posts: only the regular bits (metro, pets, rental term, deposit, commission); the model does the rest."""
    d, low = {}, t.lower()
    m = re.search(r"Ⓜ️\s*#?([\w\-]+)", t) or re.search(r"метро\s*[«\"]?([А-Яа-яЁё.\-]+(?:\s+[А-Яа-яЁё.\-]+)?)", t, re.I)
    if m and (ru_en := metro(m[1]) or metro(m[1].split()[0])):
        d["metro_ru"], d["metro_en"] = ru_en
    if re.search(r"новостро", low):
        d["building"] = "new"
    elif re.search(r"старый (?:фонд|дом)|старом доме", low):
        d["building"] = "old"
    pets = re.search(r"(?:животн|питом|🐾)[^\n]*", low)
    if pets:
        p = pets[0]
        d["pets"] = ("not_allowed" if re.search(r"✖️|❌|нельзя|запрещ|без живот|не допуска", p) or "без животных" in low
                     else "by_agreement" if re.search(r"договор|депозит|небольш|маленьк", p)
                     else "allowed" if re.search(r"разреш|можно|допуска|✅", p) else None)
    elif "без животных" in low:
        d["pets"] = "not_allowed"
    if m := re.search(r"срок аренды\s*:?\s*(?:от\s*)?(\d+)\s*мес", low):
        d["terms"] = [int(m[1])]
    elif re.search(r"договор на год|на год\b", low):
        d["terms"] = [12]
    if m := re.search(r"депозит[^\d\n]{0,25}(\d[\d,]*)\s*\$|депозит[^\n$]{0,25}\$\s*(\d[\d,]*)", low):
        d["deposit_usd"] = usd(m[1] or m[2], "$")
    return {k: v for k, v in d.items() if v not in (None, [], "")}


# ---------- ss.ge / myhome.ge fields ----------

SS_YES = {"airConditioning": "conditioner", "balcony": "balcony", "elevator": "elevator", "fridge": "fridge", "furniture": "furniture",
          "internet": "wifi", "wiFi": "wifi", "tv": "tv", "cableTelevision": "tv", "washingMachine": "washing_machine",
          "withPool": "pool", "storage": "storage", "garage": "parking"}
SS_STATE = {"newly renovated": "new", "old renovated": "old", "being renovated": "needs_repair", "white frame": "white_frame",
            "black frame": "needs_repair", "green frame": "needs_repair", "under renovation": "needs_repair"}


def video_link(url):
    """External video/3D link -> ('url', link) for a button, ('file', link) for an mp4 we download, or None."""
    if not url:
        return None
    if re.search(r"\.mp4(\?|$)", url, re.I):
        return "file", url
    return ("url", url) if url.startswith("http") else None


def ss_facts(i, det=None):
    """ss.ge search item (+ the detail page's applicationData, fetch_ss.py) -> details, exact point."""
    det = det or {}
    d = {"floors": int(i["totalAmountOfFloor"]) if i.get("totalAmountOfFloor") else None}
    st = sorted(i.get("nearbySubwayStations") or det.get("nearbySubwayStations") or [], key=lambda s: s.get("timeDistance") or 99)
    if st and (st[0].get("timeDistance") or 99) <= 15 and (ru_en := metro(st[0]["stationTitle"])):
        d["metro_ru"], d["metro_en"] = ru_en
    d["yes"] = list(dict.fromkeys(c for k, c in SS_YES.items() if det.get(k)))
    if i.get("furniture") and "furniture" not in d["yes"]:
        d["yes"].append("furniture")
    status = (det.get("realEstateStatus") or "").lower()
    d["building"] = "new" if "new" in status else "old" if "old" in status else "construction" if "construction" in status else None
    d["condition"] = SS_STATE.get((det.get("state") or "").lower())
    if det.get("toilet") and str(det["toilet"]).isdigit():
        d["bathrooms"] = int(det["toilet"])
    if det.get("isPetFriendly"):
        d["pets"] = "allowed"
    for v in [i.get("videoLink"), det.get("applicationVideoLink"), det.get("uploadVideoLink"),
              *[x.get("videoLink") or x.get("url") for x in det.get("applicationVideos") or [] if isinstance(x, dict)]]:
        if (k := video_link(v)) and k[0] == "url":
            d.setdefault("video_url", k[1])
        elif k:
            d.setdefault("video_file", k[1])
    point = None
    if det.get("locationLatitude") and det.get("locationLongitude"):
        point = (det["locationLatitude"], det["locationLongitude"]), "точный" if det.get("isManualPin") else "улица"
    return {k: v for k, v in d.items() if v not in (None, [], "")}, point


MH_PARAMS = {"internet": "wifi", "tv": "tv", "conditioner": "conditioner", "refrigerator": "fridge", "washing_machine": "washing_machine",
             "dishwasher": "dishwasher", "stove": "stove", "oven": "oven", "furniture": "furniture", "elevator": "elevator",
             "fireplace": "fireplace", "storeroom": "storage", "pool": "pool", "swimming_pool": "pool", "security": "security"}


def mh_facts(i, det=None):
    """myhome.ge search item (+ detail statement and rendered labels, fetch_myhome.py) -> details, exact point."""
    det = det or {}
    labels = det.get("labels") or {}
    d = {"floors": i.get("total_floors") or det.get("total_floors")}
    keys = [p.get("key") for p in det.get("parameters") or i.get("parameters") or []]
    d["yes"] = list(dict.fromkeys(MH_PARAMS[k] for k in keys if k in MH_PARAMS))
    d["no"] = []
    if det.get("balconies"):
        d["yes"].append("balcony")
    park = (labels.get("Parking") or "").lower()
    if park.startswith("without") or park.startswith("no "):
        d["no"].append("parking")
    elif park:
        d["yes"].append("parking")
    if "pets-allowed" in keys:
        d["pets"] = "allowed"
    heat = (labels.get("Heating") or "").lower()
    d["heating"] = "central" if "central" in heat else "gas" if "gas" in heat else "electric" if "electr" in heat \
        else "none" if heat.startswith("without") else None
    status = (labels.get("Status") or "").lower()
    d["building"] = "new" if "new" in status else "old" if "old" in status else "construction" if "construction" in status else None
    cond = (labels.get("Condition") or det.get("condition") or "").lower()
    d["condition"] = {"newly renovated": "new", "old renovated": "old", "white frame": "white_frame", "black frame": "needs_repair",
                      "green frame": "needs_repair", "under renovation": "needs_repair", "being renovated": "needs_repair"}.get(cond)
    if (labels.get("Bathrooms") or "").isdigit():
        d["bathrooms"] = int(labels["Bathrooms"])
    if det.get("rent_period"):
        d["terms"] = [int(det["rent_period"])]
    if (k := video_link(det.get("youtube_link"))) and k[0] == "url":
        d["video_url"] = k[1]
    if (k := video_link(i.get("3d_url") or det.get("3d_url"))) and k[0] == "url":
        d["tour_url"] = k[1]
    point = (det["lat"], det["lng"]) if det.get("lat") and det.get("lng") else None
    return {k: v for k, v in d.items() if v not in (None, [], "")}, point and (point, "точный")


# ---------- merge ----------

def merge(llm, *parsed):
    """Model output first, each deterministic parser overrides it (later ones win). Amenity lists are united, but a
    parsed "no" removes the model's "yes" for it and a parsed "yes" removes a "no"."""
    out = {k: v for k, v in (llm or {}).items() if v not in (None, [], "")}
    for p in parsed:
        p = p or {}
        yes, no = p.get("yes") or [], p.get("no") or []
        out["yes"] = [c for c in out.get("yes", []) if c not in no]
        out["no"] = [c for c in out.get("no", []) if c not in yes]
        for k, v in p.items():
            if v in (None, [], ""):
                continue
            out[k] = list(dict.fromkeys(out[k] + v)) if k in ("yes", "no") else v
    out["no"] = [c for c in out.get("no", []) if c not in out.get("yes", [])]
    return {k: v for k, v in out.items() if v not in (None, [], "")}


# ---------- buttons with external links ----------

HIDDEN_HOSTS = ("ss.ge", "myhome", "tnet.ge", "facebook", "fb.", "fbcdn", "t.me", "telegram", "instagram", "korter", "livo")


def safe_link(url):
    """Client-visible URL (video / 3D-tour button) only when its host does not reveal the source site."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    return url if url and url.startswith("https://") and host and not any(h in host for h in HIDDEN_HOSTS) else None


# ---------- card lines ----------

def _lab(kind, code, lang):
    pair = LABELS[kind].get(code)
    return pair and pair[lang == "en"]


def _chunks(items, n=3):
    return ["  ".join(items[k:k + n]) for k in range(0, len(items), n)]


def card_lines(i, lang="ru"):
    """The channel-style body of a card: [block, block, ...] (each a list of lines); no hashtags, only known facts.
    Text fields are raw (core.card runs them through public())."""
    en = lang == "en"
    d = i.get("details") or {}
    pick = lambda k: d.get(f"{k}_en") if en else d.get(f"{k}_ru") or d.get(f"{k}_en")  # Latin is fine in a ru card
    district = (en and (d.get("district_en") or i.get("district_en"))) or i.get("district") or ("Tbilisi" if en else "Тбилиси")
    m = pick("metro")
    head = [f"🚇 {district}" + (f" · {m}" if m.lower() != district.lower() else "") if m else f"🏙 {district}"]
    if pick("address"):
        head.append(f"📍 {pick('address')}")
    blocks = [head]
    if pick("headline"):
        blocks.append([f"⭐️ {pick('headline')}"])
    if d.get("exclusive"):
        blocks.append(["❗️ " + ("Exclusive" if en else "Эксклюзив")])
    beds = i.get("bedrooms")
    prop = _lab("property", d.get("property") or "apartment", lang)
    what = [f"🏢 {prop}" + (f" · {beds} {'bedroom' if beds == 1 else 'bedrooms'}" if en else f" · {beds} {_sp(beds)}") if beds
            else f"🏢 {prop}"]
    bl = " · ".join(x for x in (_lab("building", d.get("building"), lang), _lab("condition", d.get("condition"), lang)) if x)
    if bl:
        what.append(f"✨ {bl[0].upper() + bl[1:]}")
    size = []
    if i.get("area"):
        size.append(f"{i['area']:g} {'sq.m' if en else 'м²'}")
    if i.get("floor") is not None:
        fl = f"{i['floor']}/{d['floors']}" if d.get("floors") else f"{i['floor']}"
        size.append(f"{fl} {'floor' if en else 'этаж'}")
    for k in ("heating", "bath"):
        if x := _lab(k, d.get(k), lang):
            size.append(x)
    if (d.get("bathrooms") or 0) > 1:
        size.append(f"{d['bathrooms']} {'bathrooms' if en else 'санузла' if d['bathrooms'] < 5 else 'санузлов'}")
    if size:
        what.append("🏠 " + " · ".join(size))
    blocks.append(what)
    yes = [f"✅ {AMENITIES[c][en]}" for c in AMENITIES if c in (d.get("yes") or [])]
    no = [f"✖️ {AMENITIES[c][en]}" for c in AMENITIES if c in (d.get("no") or []) and c in NO_SHOWN]
    blocks += [b for b in (_chunks(yes), _chunks(no)) if b]
    terms = []
    if m := re.match(r"\s*(\d+)(?:\s*[-–]\s*(\d+))?", str(d.get("tenants") or "")):  # numbers only ("1-2 девушки" -> 1–2)
        terms.append(f"👬 {'Tenants' if en else 'Жильцов'}: {m[1]}" + (f"–{m[2]}" if m[2] else ""))
    if x := _lab("pets", d.get("pets"), lang):
        terms.append(f"🐕 {'Pets' if en else 'Животные'}: {x}")
    if d.get("terms"):
        ms = sorted(d["terms"])
        terms.append(f"🕐 {'Term' if en else 'Срок'}: " + (" / ".join(map(str, ms)) + (" months" if en else " мес.")
                                                       if len(ms) > 1 else (f"from {ms[0]} months" if en else f"от {ms[0]} мес.")))
    if terms:
        blocks.append(terms)
    money = [f"💰 ${i.get('price_usd')}/{'mo' if en else 'мес'}" + (
        f" + {'deposit' if en else 'депозит'} ${d['deposit_usd']}" if d.get("deposit_usd") else "")]
    if d.get("commission") and i.get("source") == "agency":  # other sources' fees are not the agency's terms
        money.append(f"{d['commission']} {'commission' if en else 'комиссии'}")
    blocks.append(money)
    return blocks


def _sp(n):
    return "спальня" if n % 10 == 1 and n % 100 != 11 else "спальни" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "спален"


def media_links(i, lang="ru"):
    """URL buttons for the client card: 🎥 video (e.g. YouTube) and 🧊 3D tour, only on hosts that don't reveal the source."""
    d, en = i.get("details") or {}, lang == "en"
    out = []
    if not d.get("videos") and (u := safe_link(d.get("video_url"))):  # downloaded (fetch_videos.py) = already in the album
        out.append({"text": "🎥 Video" if en else "🎥 Видео", "url": u})
    if u := safe_link(d.get("tour_url")):
        out.append({"text": "🧊 3D tour" if en else "🧊 3D-тур", "url": u})
    return out
