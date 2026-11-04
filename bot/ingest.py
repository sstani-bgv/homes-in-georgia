"""raw/*.json -> listings.json: normalize, dedupe, flag agents, index photos with Codex.

Usage: python ingest.py            # full run (index cache in raw/index_cache.json, resumable)
       python ingest.py --test     # dedupe self-check only
"""
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

import facts
from geo import first_hit, geocode, km
from imgutil import VIDEO_DIR, download_video, local_video, to_jpeg
from llm import ask, obj

IMG = "raw/img"
CACHE = "raw/index_cache.json"
PHOTOS = 3


# ---------- normalize ----------

def _details(path):
    return json.load(open(path)) if os.path.exists(path) else {}


SS_DETAILS, MH_DETAILS, KR_DETAILS = {}, {}, {}  # raw/{ss,myhome,korter}_details.json (fetchers), loaded in main()


def from_ss(i):
    a = i["address"] or {}
    parsed, point = facts.ss_facts(i, SS_DETAILS.get(str(i["applicationId"])))
    return {
        "id": f"ss{i['applicationId']}", "source": "ss.ge", "owner_id": i.get("userId"),
        "urls": [f"https://home.ss.ge/ka/udzravi-qoneba/{i['detailUrl']}"],
        "price_usd": (i.get("price") or {}).get("priceUsd"), "area": i.get("totalArea"),
        "floor": _int(i.get("floorNumber")), "bedrooms": i.get("numberOfBedrooms"),
        "address": " ".join(filter(None, [a.get("subdistrictTitle"), a.get("streetTitle"), a.get("streetNumber")])),
        "text": f"{i.get('title') or ''}\n{i.get('description') or ''}",
        "photo_urls": [p["fileName"].replace("_Thumb", "") for p in i.get("appImages", [])[:PHOTOS]],
        "geo_queries": [f"{a.get('streetTitle') or ''} {a.get('streetNumber') or ''}", a.get("streetTitle"), a.get("subdistrictTitle")],
        "parsed": parsed, "point": point,
    }


def from_myhome(i):
    parsed, point = facts.mh_facts(i, MH_DETAILS.get(str(i["id"])))
    return {
        "id": f"mh{i['id']}", "source": "myhome.ge", "owner_id": i.get("user_id"),
        "urls": [f"https://www.myhome.ge/en/pr/{i['id']}/{i.get('dynamic_slug') or ''}"],
        "price_usd": (i.get("price") or {}).get("2", {}).get("price_total"), "area": i.get("area"),
        "floor": _int(i.get("floor")), "bedrooms": _int(i.get("bedroom")),
        "address": f"{i.get('district_name') or ''}, {i.get('urban_name') or ''}, {i.get('address') or ''}",
        "text": re.sub(r"<br\s*/?>", "\n", i.get("comment") or ""),
        "photo_urls": [p["large"] for p in i.get("images", [])[:PHOTOS]],
        "geo_queries": [i.get("address"), re.sub(r"\s*\d+\w?\s*$", "", i.get("address") or ""), i.get("urban_name")],
        "parsed": {**parsed, **({"address_en": i["address"]} if re.search(r"\d", i.get("address") or "")
                                and not re.search("[\u10a0-\u10ff]", i["address"]) else {})}, "point": point,
    }


BASE_KORTER = "https://korter.ge"


def from_livo(i):
    """livo.ge item: the myhome statement shape (fetch_livo.py), only the source and link differ."""
    it = from_myhome(i)
    it.update(id=f"lv{i['id']}", source="livo.ge",
              urls=[f"https://livo.ge/en/udzravi-qoneba/{i.get('middle_slug') or 'for-rent-apartment'}/"
                    f"{i.get('dynamic_slug') or 'apartment'}-{i['id']}"])
    return it


def from_korter(i):
    """korter.ge listing item + its detail page fields (fetch_korter.py)."""
    det = KR_DETAILS.get(str(i["objectId"])) or {}
    b = i.get("building") or {}
    pos = b.get("position") or {}
    price = i.get("price")
    if price and i.get("currency") == "GEL":
        price = round(price / GEL_PER_USD)
    bedrooms = det.get("bedroomCount")
    if bedrooms is None and i.get("roomCount"):
        bedrooms = max(i["roomCount"] - 1, 0)  # Korter counts rooms with the living room, like myhome's "room"
    floors = (i.get("house") or {}).get("floorCount")
    d = {"floors": floors, "bathrooms": det.get("bathroomCount"),
         "yes": [k for k, v in (("balcony", det.get("hasBalcony")), ("terrace", det.get("hasTerrace"))) if v]}
    if k := facts.video_link(det.get("videoLink")):
        d["video_url" if k[0] == "url" else "video_file"] = k[1]
    district = i.get("subLocalityNominative")
    return {
        "id": f"kr{i['objectId']}", "source": "korter.ge", "owner_id": f"kr:{i.get('userId')}",
        "urls": [BASE_KORTER + i["link"]], "price_usd": price, "area": i.get("area"),
        "floor": _int((i.get("floorNumbers") or [None])[0]), "bedrooms": bedrooms,
        "address": ", ".join(filter(None, [district, i.get("address")])),
        "text": det.get("description") or "",
        "photo_urls": [u for u in det.get("images") or [(i.get("mediaSrc") or {}).get("default", {}).get("x1")] if u][:PHOTOS],
        "geo_queries": [i.get("address"), re.sub(r",?\s*\d+\w?(\s*\(.*\))?\s*$", "", i.get("address") or ""), district],
        "parsed": {k: v for k, v in d.items() if v not in (None, [], "")},
        "point": ((pos["lat"], pos["lng"]), "улица") if pos.get("lat") and pos.get("lng") else None,
    }


GEL_PER_USD = 2.7
_NUM = r"\d{1,3}(?:[ ,]\d{3})+|\d+"


def _fb_first(t, *pats):
    for p in pats:
        m = re.search(p, t, re.I)
        if m:
            return next(g for g in m.groups() if g)
    return None


def from_facebook(i):
    """Facebook group post (fetch_facebook.py already dropped non-rentals and merged one author's cross-posts).
    Free text in ka/ru/en: pull what regexes can, Codex reads the text and photos later anyway."""
    t = i["text"]
    usd = _fb_first(t, rf"\$\s*({_NUM})", rf"({_NUM})\s*(?:\$|💲|usd|долл|დოლარ)")
    gel = _fb_first(t, rf"({_NUM})\s*(?:₾|gel\b|лари|ლარ|ლ\b)")
    price = _int(re.sub(r"\D", "", usd)) if usd else gel and round(_int(re.sub(r"\D", "", gel)) / GEL_PER_USD)
    area = _fb_first(t, r"(?:size|площадь|ფართი|ფართობი)\s*:?\s*(\d{2,4}(?:[.,]\d)?)",
                     r"(\d{2,4}(?:[.,]\d)?)\s*(?:m²|m2|м²|м2|sq\.?\s*m|кв\.?\s*м|кв\b|მ²|კვ)")
    loc = next((ln for ln in t.splitlines() if re.search(r"📍|location|адрес|address|მისამართ|მდებარეობა", ln, re.I)), "")
    addr = re.sub(r"^\W*(?:location|район\s*/\s*адрес|адрес|address|მისამართი|მდებარეობა)\s*:?|^.*?\bfor rent in\b",
                  "", re.sub(r"[*📍\u200b]", "", loc).strip(), flags=re.I).strip(" :")
    return {
        "id": f"fb{i['id']}", "source": f"fb:{i['group']}",
        # anonymous posts can't be tied to a person: one owner per post
        "owner_id": f"fb:{i['author']}" if i.get("author") else f"fb:anon:{i['id']}",
        "author_name": i.get("author_name"), "author_type": i.get("author_type"),
        "urls": i["post_urls"], "price_usd": price if price and 100 <= price <= 20000 else None,
        "area": float(area.replace(",", ".")) if area else None,
        "floor": _int(_fb_first(t, r"(\d{1,2})\s*/\s*\d{1,2}\s*(?:этаж|floor|სართ)", r"(\d{1,2})(?:st|nd|rd|th)?\s+floor",
                                r"(?:floor|этаж)\s*:?\s*(\d{1,2})", r"(\d{1,2})\s*(?:-?[йм])?\s*этаж",
                                r"(\d{1,2})\s*(?:-?ე)?\s*სართულ(?!იან)")),
        "bedrooms": _int(_fb_first(t, r"(\d)\s*(?:bedroom|спальн|საძინებ|bed\b)", r"(?:bedrooms?|спальн\w*)\s*:?\s*(\d)")),
        "address": addr, "text": t, "photos": i["photos"], "date": i.get("date"),
        "geo_queries": [addr, re.split(r"[,(–—|]", addr)[0]],
        "parsed": {**facts.parse_ru(t), **_video(i)},
    }


def from_instagram(i):
    """Instagram post of an agency account (fetch_instagram.py): the same free text as Facebook posts."""
    return {**from_facebook(i), "id": f"ig{i['id']}", "source": f"ig:{i['group']}", "owner_id": f"ig:{i['author']}"}


def _video(i):
    """raw post's video (fetchers download it under the size cap) -> {"videos": [...]} if the file is there."""
    v = i.get("video")
    return {"videos": [v]} if v and os.path.exists(v.get("path") or "") else {}


def _point(i):
    """Exact pin from a map link in the post (the agency links every address to Google Maps)."""
    p = facts.maps_point(i.get("links"))
    return p and (p, "точный")


def from_channel(i):
    t = i["text"]
    p = facts.parse_agency(t)
    return {
        "id": f"tg{i['id']}", "source": "agency", "owner_id": "agency",
        "urls": [i["url"]], "price_usd": p.pop("price_usd", None), "area": p.pop("area", None), "floor": p.pop("floor", None),
        "bedrooms": p.pop("bedrooms", None), "address": p.get("address_en") or "",
        "text": t, "photos": i["photos"], "parsed": {**p, **_video(i), **({"video_msg": i["video_msg"]} if i.get("video_msg") else {})},
        "point": _point(i),
        "geo_queries": [p.get("address_en"), p.get("district_en")],
    }


DISTRICTS = ["Ваке", "Сабуртало", "Вера", "Мтацминда", "Сололаки", "Дидубе", "Диди Дигоми", "Дигоми", "Глдани",
             "Исани", "Самгори", "Авлабари", "Ортачала", "Крцаниси", "Надзаладеви", "Чугурети", "Лиси", "Вазисубани",
             "Варкетили", "Багеби", "Нуцубидзе", "Марджанишвили", "Бахтриони", "Мухиани", "Окросубани", "Цхнети",
             "Vake", "Saburtalo", "Vera", "Mtatsminda", "Sololaki", "Didube", "Digomi", "Gldani", "Isani", "Samgori",
             "Avlabari", "Ortachala", "Krtsanisi", "Nadzaladevi", "Chugureti", "Lisi", "Vazisubani", "Bagebi"]
DISTRICT_RE = re.compile(r"(?<![А-Яа-яЁёA-Za-z])(" + "|".join(DISTRICTS) + r")(?![а-яё])")
_PRICE = r"\$\s*(\d[\d,]*)|(\d[\d,]*)\s*\$"


def _first(t, *pats, flags=re.I):
    for p in pats:
        m = re.search(p, t, flags)
        if m:
            return next(g for g in m.groups() if g)
    return None


def tg_is_rental(t):
    """Competitor channels also post sales, wanted-ads and plain advertising: keep long-term rentals in Tbilisi."""
    head, low = t[:250].lower(), t.lower()
    if re.search(r"прода[её]тся|продаж|for sale|\bsale\b|იყიდება|\bищу\b|\bищем\b|\bсниму\b|looking for|wanted|"
                 r"посуточ|только краткосроч|daily rent", head):
        return False
    if re.search(r"коммерческ|commercial|офис|office", low.split("\n", 1)[0]):  # "Сдаётся коммерческое помещение"
        return False
    return not ("батуми" in low or "batumi" in low) or "тбилис" in low or "tbilisi" in low


def from_tg(i):
    """Competitor agency channel post -> listing (or None if it is not a rental). Formats differ per channel,
    so fields are pulled with label-first regexes; Codex later reads the text and photos anyway."""
    t, ch = i["text"], i["channel"]
    if not tg_is_rental(t):
        return None
    price_line = _first(t, r"((?:Цена|Price|Стоимость)[^\n]*)", flags=0) or t
    price = _int((_first(price_line, _PRICE) or _first(t, _PRICE) or "").replace(",", ""))
    area = _first(t, r"Площадь\s*:?\s*(\d+(?:[.,]\d+)?)", r"(\d+(?:[.,]\d+)?)\s*(?:м²|м2|m²|m2|кв\.?\s*м|sq\.?\s*m)")
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    addr = next((re.sub(r"^(?:📍|Улица|Адрес)\s*:?\s*", "", ln) for ln in lines
                 if re.match(r"(?:📍|Улица|Адрес)", ln) and not re.search(r"Локация|Location|Район|ON MAP", ln)), None) \
        or next((ln for ln in lines if re.search(r"\bул\.|\bулица\b|\bпр-кт\b|\bпр\.|\bш\.|\bпроспект\b", ln, re.I)), None) \
        or (lines[0] if lines and re.search(r"\d", lines[0]) and not re.search(r"\$|\bID\b", lines[0]) else "")
    addr = re.sub(r"[#|]", " ", addr).strip()
    district = (DISTRICT_RE.search(t) or [None, None])[1]
    if not price or not 100 <= price <= 30000 or not (area or district):
        return None
    return {
        "id": f"tg_{ch}_{i['id']}", "source": f"tg:{ch}", "owner_id": f"tg:{ch}", "urls": [i["url"]],
        "price_usd": price, "area": float(area.replace(",", ".")) if area else None,
        "floor": _int(_first(t, r"(\d+)\s*/\s*\d+\s*(?:этаж|floor)", r"(?:этаж|floor)\s*:?\s*(\d+)", r"(\d+)\s*этаж")),
        "bedrooms": _int(_first(t, r"спал\w*\s*:?\s*(\d+)", r"(\d+)\s*спал", r"(\d+)\s*bed")),
        "address": addr if district and district in addr else ", ".join(filter(None, [district, addr])), "text": t, "photos": i["photos"], "date": i.get("date"),
        "geo_queries": [addr, district], "point": _point(i),
        # competitors copying the agency hashtag format get the same parser, the rest only the regular Russian bits
        "parsed": {**facts.parse_ru(t), **(_agency_like(t)), **_video(i)},
    }


def _agency_like(t):
    if not re.search(r"#\d+\s*Bed|\bsq\.?\s*m\b.*Floor|Tenants\s*:", t, re.I):
        return {}
    return {k: v for k, v in facts.parse_agency(t).items() if k not in ("price_usd", "area", "floor", "bedrooms", "commission")}


def channel_items(kind):
    """Telegram channels from sources.json (raw/channel_<username>.json). The agency channel falls back to the
    pre-sources.json file raw/channel.json. Competitor reposts of the same flat inside one channel are collapsed."""
    cfg = json.load(open("sources.json")) if os.path.exists("sources.json") else {"telegram_channels": []}
    out, seen = [], set()
    for ch in cfg["telegram_channels"]:
        if ch["kind"] != kind:
            continue
        path = f"raw/channel_{ch['username']}.json"
        if kind == "agency" and not os.path.exists(path):
            path = "raw/channel.json"
        if not os.path.exists(path):
            continue
        for i in json.load(open(path)):
            it = from_channel(i) if kind == "agency" else from_tg({"channel": ch["username"], **i})
            if not it:
                continue
            key = (it["source"], it["floor"], it["bedrooms"], it["area"], it["address"])
            if kind != "agency" and it["area"] and key in seen:  # newest first: keep the latest repost
                continue
            seen.add(key)
            out.append(it)
    return out


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------- dedupe & agent signals ----------

def same_flat(a, b):
    # one flat reposted across sites: same floor and bedrooms, area within 3 m2, price within 10%
    if None in (a["floor"], b["floor"], a["area"], b["area"], a["price_usd"], b["price_usd"]):
        return False
    return (a["floor"] == b["floor"] and a["bedrooms"] == b["bedrooms"] and abs(a["area"] - b["area"]) <= 3
            and abs(a["price_usd"] - b["price_usd"]) <= 0.1 * max(a["price_usd"], b["price_usd"]))


def dedupe(items):
    # ponytail: O(n^2) pairwise scan, fine for hundreds; bucket by (floor, bedrooms) for thousands
    out = []
    for it in items:
        # across sites: same params or a shared photo. Within one site same params are usually two different cheap flats,
        # and one shared photo can be a common building shot; 2+ shared photos = the same post published twice
        dup = next((o for o in out if (o["source"] != it["source"] and (same_flat(o, it) or same_photo(o, it)))
                    or shared_photos(o, it) >= 2), None)
        if dup:
            dup["urls"] += it["urls"]
            dup["dup_sources"] = dup.get("dup_sources", []) + [it["source"]]
            # the kept copy also takes the other copy's facts (its own win), exact pin and video
            dup["parsed"] = facts.merge(it.get("parsed"), dup.get("parsed"))
            dup["point"] = dup.get("point") or it.get("point")
        else:
            out.append(it)
    return out


PHONE = re.compile(r"(?:\+?995[\s\-:.]?)?(5(?:[\s\-:.]?\d){8})")


def phones(text):
    return {re.sub(r"\D", "", m)[-9:] for m in PHONE.findall(text)}


def agent_signals(items):
    """Owner listings that look like an agent: same phone / account / text across several flats."""
    # agency and competitor channels / Instagram accounts (source "tg:<username>", "ig:<username>") are agencies by design: only dedupe them
    owners = [i for i in items if i["source"] != "agency" and not i["source"].startswith(("tg:", "ig:"))]
    by_phone = Counter(p for i in owners for p in phones(i["text"]))
    by_owner = Counter(i["owner_id"] for i in owners)
    by_text = Counter(re.sub(r"\W", "", i["text"].lower())[:200] for i in owners if len(i["text"]) > 60)
    for i in owners:
        why = []
        if any(by_phone[p] >= 3 for p in phones(i["text"])):
            why.append("один телефон в 3+ объявлениях")
        if by_owner[i["owner_id"]] >= 3:
            why.append("один аккаунт подал 3+ объявления")
        if len(i["text"]) > 60 and by_text[re.sub(r"\W", "", i["text"].lower())[:200]] >= 2:
            why.append("одинаковый текст у разных объявлений")
        # ponytail: both sides of a photo repost are dropped (can't tell original from copy); keep the older one if recall matters
        if any(o is not i and o["source"] == i["source"] and o["owner_id"] != i["owner_id"] and same_photo(i, o) for o in owners):
            why.append("те же фото у другого автора на этом сайте (перепост)")
        i["agent_reasons"] = why
    facebook_signals(owners)


AGENT_NAME = re.compile(r"realt|რეალტ|agen[tc]|სააგენტ|estate|недвиж|риелт|риэлт|маклер|broker|propert|invest|"
                        r"apartments|homes?\b|houses|binebi|yidva|უძრავ|ქონებ", re.I)


def facebook_signals(owners):
    """Facebook groups are full of agents: an agency-like author, one author with several different flats,
    or the same photos posted by another author in another group."""
    fb = [i for i in owners if i["source"].startswith("fb:")]
    for i in fb:
        if i.get("author_type") == "Page" or AGENT_NAME.search(f"{i.get('author_name')} {i['owner_id']}"):
            i["agent_reasons"].append("FB: имя или страница автора похожи на агентство")
        if any(o is not i and o["owner_id"] == i["owner_id"] and not same_flat(o, i) and not same_photo(o, i) for o in fb):
            i["agent_reasons"].append("FB: один автор выложил 2+ разных квартиры")
        if any(o["source"] != i["source"] and o["owner_id"] != i["owner_id"] and same_photo(o, i) for o in fb):
            i["agent_reasons"].append("FB: те же фото у другого автора в другой группе")


# ---------- photos ----------

def dhash(path):
    """64-bit difference hash: survives resize, recompression and a small watermark."""
    from PIL import Image
    px = list(Image.open(path).convert("L").resize((9, 8)).getdata())
    return sum(1 << n for n, (r, c) in enumerate((r, c) for r in range(8) for c in range(8))
               if px[r * 9 + c] > px[r * 9 + c + 1])


def same_photo(a, b, max_bits=4):
    # measured on real data: same photo = 0-2 bits apart, different photos = 8+
    return any(bin(x ^ y).count("1") <= max_bits for x in a.get("hashes", ()) for y in b.get("hashes", ()))


def shared_photos(a, b, max_bits=4):
    return sum(1 for x in a.get("hashes", ()) if any(bin(x ^ y).count("1") <= max_bits for y in b.get("hashes", ())))


def download_photos(it):
    if "photos" in it:
        return
    paths = []
    for n, url in enumerate(it["photo_urls"]):
        path = f"{IMG}/{it['id']}_{n}.jpg"
        if not os.path.exists(path):
            try:
                r = httpx.get(url, timeout=30, follow_redirects=True)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            raw = path + ".src"
            open(raw, "wb").write(r.content)
            # webp -> jpeg: Telegram and the model both take jpeg reliably
            to_jpeg(raw, path, 1280)
            os.remove(raw)
        if os.path.exists(path):
            paths.append(path)
    it["photos"] = paths


def download_site_video(it):
    """An mp4 link from the site (ss.ge video upload) -> raw/video/<id>.mp4 under the size cap, into details.videos.
    A video fetch_videos.py (yt-dlp) saved under the same name on an earlier refresh is picked up as well."""
    it.setdefault("parsed", {})
    url = it["parsed"].pop("video_file", None)
    path = f"{VIDEO_DIR}/{it['id']}.mp4"
    if it["parsed"].get("videos"):
        return
    if url and download_video(url, path) or os.path.exists(path):
        it["parsed"]["videos"] = [local_video(path)]


# ---------- Codex indexing ----------

VOCAB = {
    "renovation": ["новый", "евроремонт", "дизайнерский", "советский", "требует ремонта", "белый каркас"],
    "style": ["минимализм", "современный", "классика", "лофт", "сканди", "старый тбилисский", "смешанный"],
    "light": ["светлая", "средняя", "тёмная"],
    "furniture": ["новая", "хорошая", "старая", "без мебели"],
}
S = {"type": "string"}
NS, NI = {"type": ["string", "null"]}, {"type": ["integer", "null"]}


def _enum(values):
    return {"type": ["string", "null"], "enum": [*values, None]}


# channel-style facts (facts.py), asked in the same call as the profile; parsed source fields override them
CODES = {"type": "array", "items": {"type": "string", "enum": list(facts.AMENITIES)}}
DETAILS = dict(
    metro_ru=NS, metro_en=NS, address_ru=NS, address_en=NS, headline_ru=NS, headline_en=NS, district_en=NS,
    property=_enum(facts.ENUMS["property"]), building=_enum(facts.ENUMS["building"]), condition=_enum(facts.ENUMS["condition"]),
    floors=NI, heating=_enum(facts.ENUMS["heating"]), bath=_enum(facts.ENUMS["bath"]), bathrooms=NI, yes=CODES, no=CODES,
    tenants=NS, terms={"type": "array", "items": {"type": "integer"}}, deposit_usd=NI, commission=NS,
)
INDEX_V = 2  # cache entries without "v": 2 (built before the facts) get only the facts, text-only (see index_all)
PROFILE = obj(
    id=S, district=S, bedrooms={"type": ["integer", "null"]}, pets={"type": "string", "enum": ["да", "нет", "по договорённости", "не указано"]},
    renovation={"type": "string", "enum": VOCAB["renovation"]}, style={"type": "string", "enum": VOCAB["style"]},
    light={"type": "string", "enum": VOCAB["light"]}, furniture={"type": "string", "enum": VOCAB["furniture"]},
    features={"type": "array", "items": S}, impression={"type": "integer"}, looks=S, description_ru=S,
    agent_suspect={"type": "boolean"}, agent_reason=S, **DETAILS,
)
FACTS = obj(id=S, pets={"type": "string", "enum": ["да", "нет", "по договорённости", "не указано"]}, **DETAILS)
METROS = ", ".join(f"{ru}/{en}" for ru, en in facts.METRO)
DETAILS_RULES = f"""Факты для карточки в стиле поста агентства (null / [] если в тексте этого нет — НЕ выдумывай):
- metro_ru / metro_en: станция метро, если она названа в тексте или адрес явно в 10 минутах пешком от неё; только из списка: {METROS};
- address_ru / address_en: улица и номер дома без района и города (ru «ул. Ивана Мачабели, 9», en «9 Ivane Machabeli St»); null, если улицы нет;
- headline_ru / headline_en: короткое описание в 1-2 фразы (до 160 знаков), как строка ⭐️ в посте агентства: чем хороша квартира и где она. Если в тексте есть строка между ⭐️, переведи её. Без телефонов, ссылок, имён, названий сайтов и агентств, без цены;
- district_en: район по-английски (Vake, Saburtalo, Old Tbilisi...);
- property: apartment / house / commercial; building: new — новостройка, old — старый (советский) фонд, historical — исторический дом в старом городе, construction — строящийся; condition: new — новый ремонт, good — хороший, mixed — смешанный стиль, old — старый ремонт, white_frame — белый каркас, needs_repair — нужен ремонт, retro — ретро;
- floors: этажей в доме; heating: central / gas / electric / none; bath: shower / bath / both; bathrooms: число санузлов;
- yes: что ЕСТЬ (коды: {", ".join(facts.AMENITIES)}); no: чего точно НЕТ по тексту («без лифта», «парковки нет»);
- tenants: сколько жильцов можно («1-3»), иначе null; terms: возможные сроки аренды в месяцах ([6, 12]); deposit_usd: депозит в $ (лари / 2.7);
- commission: комиссия как написано («0%», «50%», «$200»), иначе null."""
INDEX_PROMPT = """Ты индексируешь объявления об аренде квартир в Тбилиси для ИИ-подборщика.
К сообщению приложены фото: у каждого объявления свои, порядок фото = порядок объявлений, их количество указано.
Для КАЖДОГО объявления верни профиль:
- district: район Тбилиси по-русски (Ваке, Сабуртало, Ваке, Дидубе, Глдани, Исани, Самгори, Сололаки, Мтацминда, Авлабари, Дидгори, Дигоми, Надзаладеви, Чугурети, Крцаниси, Ортачала, Варкетили, Лиси и т.п.);
- bedrooms: число спален (null если непонятно);
- renovation, style, light, furniture — ТОЛЬКО по фото, из заданных вариантов;
- features: короткие теги по-русски (балкон, вид на город, посудомойка, кондиционер, парковка, бассейн, камин, высокие потолки, терраса, лифт, центральное отопление...);
- impression: общее впечатление от квартиры по фото 1..5;
- looks: одна фраза по-русски, как квартира выглядит;
- description_ru: 2-4 предложения по-русски для клиента: суть объявления. БЕЗ телефонов, ссылок, имён, названий сайтов и агентств, без условий комиссии;
- agent_suspect: true если по тексту похоже на агента/посредника, а не собственника (комиссия, «наше агентство», несколько объектов, «сотрудничаю с агентами 50/50» НЕ делает автора агентом). Для source=agency всегда false;
- agent_reason: кратко почему (или пустая строка).

""" + DETAILS_RULES + """

Объявления:
{items}"""
FACTS_PROMPT = """Ты дополняешь объявления об аренде квартир в Тбилиси фактами для карточки (по тексту, без фото).
Для КАЖДОГО объявления верни id, pets (можно ли с животными: да / нет / по договорённости / не указано) и:

""" + DETAILS_RULES + """

Объявления:
{items}"""


def small(path):
    # 640px copy for the model: image tokens are what eat the Codex quota
    out = path.replace(".jpg", "_640.jpg")
    if not os.path.exists(out):
        to_jpeg(path, out, 640)
    return out if os.path.exists(out) else path


def _line(it, text=1200):
    return json.dumps({"id": it["id"], "source": it["source"], "address": it["address"], "price_usd": it["price_usd"],
                       "area": it["area"], "floor": it["floor"], "bedrooms": it["bedrooms"], "text": it["text"][:text]},
                      ensure_ascii=False)


def index_batch(batch):
    images, lines = [], []
    for it in batch:
        ph = [small(p) for p in it["photos"][:2]]
        images += ph
        lines.append(_line(it).replace('{"id"', f'{{"photos": {len(ph)}, "id"', 1))
    schema = obj(listings={"type": "array", "items": PROFILE})
    res = ask(INDEX_PROMPT.format(items="\n".join(lines)), schema, images=images)
    return {p["id"]: {**p, "v": INDEX_V} for p in res["listings"]}


def facts_batch(batch, cache):
    """Old cache entries: only the new facts, text only (no photos: cheap), merged into the cached profile."""
    res = ask(FACTS_PROMPT.format(items="\n".join(_line(it, 1500) for it in batch)), obj(listings={"type": "array", "items": FACTS}))
    got = {p["id"]: p for p in res["listings"]}
    return {i: {**cache[i], **{k: v for k, v in p.items() if k != "pets"}, "v": INDEX_V}
            for i, p in got.items() if i in cache}


def index_all(items, workers=4, size=5, facts_size=8):
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [i for i in items if i["id"] not in cache and i["photos"]]
    old = [i for i in items if i["id"] in cache and cache[i["id"]].get("v") != INDEX_V]
    jobs = [(index_batch, todo[n:n + size]) for n in range(0, len(todo), size)]
    jobs += [(lambda b: facts_batch(b, cache), old[n:n + facts_size]) for n in range(0, len(old), facts_size)]
    print(f"indexing {len(todo)} new listings + facts for {len(old)} cached ones in {len(jobs)} codex calls")

    def run(job):
        try:
            return job[0](job[1])
        except Exception as e:  # one bad batch must not kill a 20-minute run
            print("batch failed:", str(e).splitlines()[-1][:200])
            return {}

    with ThreadPoolExecutor(workers) as ex:
        for n, res in enumerate(ex.map(run, jobs), 1):
            cache.update(res)
            json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
            print(f"  {n}/{len(jobs)} done")
    return cache


PETS_CODE = {"да": "allowed", "нет": "not_allowed", "по договорённости": "by_agreement"}


def apply_profile(it, p):
    """Cached profile + parsed source facts -> listing fields and listing["details"] (facts.merge: parsed wins)."""
    parsed = it.pop("parsed", None) or {}
    llm = {k: p.get(k) for k in DETAILS}
    llm["pets"] = PETS_CODE.get(p.get("pets"))
    it.update({k: v for k, v in p.items() if k not in DETAILS and k != "v" and (v is not None or k != "bedrooms")})
    for k in ("price_usd", "area", "floor", "bedrooms"):  # parsed numbers fill gaps the normalizer left
        if it.get(k) is None and parsed.get(k) is not None:
            it[k] = parsed[k]
    d = facts.merge(llm, {k: v for k, v in parsed.items() if k not in ("price_usd", "area", "floor", "bedrooms", "district_ru")})
    if parsed.get("district_ru"):
        it["district"] = parsed["district_ru"]
    if d.get("pets"):
        it["pets"] = facts.PETS_RU[d["pets"]]
    it["details"] = d


# ---------- main ----------

def main():
    os.makedirs(IMG, exist_ok=True)
    SS_DETAILS.update(_details("raw/ss_details.json"))
    MH_DETAILS.update(_details("raw/myhome_details.json"))
    KR_DETAILS.update(_details("raw/korter_details.json"))
    # agency first, competitor channels last: dedupe keeps the first copy of a flat, the rest become extra urls
    items = channel_items("agency") + [conv(i) for path, conv in [("raw/ss.json", from_ss),
                                         ("raw/myhome.json", from_myhome), ("raw/livo.json", from_livo),
                                         ("raw/korter.json", from_korter), ("raw/facebook.json", from_facebook),
                                         ("raw/instagram.json", from_instagram)]
             if os.path.exists(path) for i in json.load(open(path))]
    items += channel_items("competitor")
    counts = Counter(i["source"] for i in items)
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(download_photos, items))
        list(ex.map(download_site_video, items))
    for it in items:
        it["hashes"] = [dhash(p) for p in it["photos"]]
    agent_signals(items)  # before dedupe: the same flat on two sites is not "the same text twice"
    items = dedupe(items)
    print("collected:", dict(counts), "| after dedupe:", len(items), f"(merged {sum(counts.values()) - len(items)})")

    print("geocoding (OSM, 1 req/s, cached)...")
    for it in items:
        qs = [q for q in it.pop("geo_queries") if q and q.strip()]
        if it.get("point"):  # exact pin from the source (map link in the post, the site's own pin): no geocoding
            latlng, it["geo_precision"] = it.pop("point")
            it["latlng"] = list(latlng)
            continue
        it.pop("point", None)
        it["latlng"], it["geo_precision"] = first_hit(qs)
        # same street name exists in several districts: a hit far from the listing's own district is wrong
        area = geocode(qs[-1]) if len(qs) > 1 else None
        if it["latlng"] and area and km(it["latlng"], area) > 4:
            it["latlng"], it["geo_precision"] = area, "район"
    print("geocoded:", sum(1 for i in items if i["latlng"]), "/", len(items))

    profiles = index_all(items)
    out, dropped = [], []
    for it in items:
        p = profiles.get(it["id"])
        if not p:
            continue
        if p["agent_suspect"] and it["source"] != "agency" and not it["source"].startswith(("tg:", "ig:")):
            it["agent_reasons"].append("ИИ: " + p["agent_reason"])
        apply_profile(it, p)
        it.pop("photo_urls", None)
        it.pop("hashes", None)
        (dropped if it.get("agent_reasons") else out).append(it)

    json.dump(out, open("listings.json", "w"), ensure_ascii=False, indent=1)
    json.dump(dropped, open("raw/dropped_agents.json", "w"), ensure_ascii=False, indent=1)
    print(f"listings.json: {len(out)} | dropped as agents: {len(dropped)}", Counter(i["source"] for i in out))


def test():
    a = {"floor": 5, "bedrooms": 2, "area": 80, "price_usd": 900}
    assert same_flat(a, {**a, "area": 82, "price_usd": 950})
    assert not same_flat(a, {**a, "floor": 6})
    assert not same_flat(a, {**a, "price_usd": 1100})
    merged = dedupe([{**a, "urls": ["x"], "source": "ss.ge"}, {**a, "area": 81, "urls": ["y"], "source": "myhome.ge"}])
    assert len(merged) == 1 and merged[0]["urls"] == ["x", "y"]
    assert len(dedupe([{**a, "urls": ["x"], "source": "ss.ge"}, {**a, "urls": ["y"], "source": "ss.ge"}])) == 2
    p = {"hashes": [0b1011]}
    assert same_photo(p, {"hashes": [0b1010]}) and not same_photo(p, {"hashes": [0xFFFF0000]})
    assert len(dedupe([{**a, "floor": 1, "urls": ["x"], "source": "ss.ge", **p},
                       {**a, "floor": 9, "urls": ["y"], "source": "myhome.ge", **p}])) == 1  # same photo, other site
    two, one = {"hashes": [0b1011, 0xF0F0]}, {"hashes": [0b1011, 0x0F0F0000]}  # one channel posted a flat twice / a common photo
    assert len(dedupe([{**a, "urls": ["x"], "source": "agency", **two}, {**a, "floor": 2, "urls": ["y"], "source": "agency", **two}])) == 1
    assert len(dedupe([{**a, "urls": ["x"], "source": "agency", **two}, {**a, "floor": 2, "urls": ["y"], "source": "agency", **one}])) == 2
    assert phones("call +995 599 12 34 56 or 5.8.5.8.9.0.3.2.7") == {"599123456", "585890327"}
    # competitor channel posts (real formats from tbilisi_apartments / crescotbilisi)
    tg = {"id": 1, "channel": "x", "url": "https://t.me/x/1", "photos": []}
    c = from_tg({**tg, "text": "Сдаётся квартира\n\n📍Район #Ортачала #аренда #2спальни #до1000\n\nУл. Николадзе, 15\n\n"
                              "🪜68 кв. м., этаж 3/14\n\n💵850$🤝КОМИССИЯ 200$\n\nПолезные ссылки:\n🔑 Купить @sale_tbi"})
    assert (c["source"], c["price_usd"], c["area"], c["floor"], c["bedrooms"]) == ("tg:x", 850, 68, 3, 2)
    assert c["geo_queries"] == ["Ул. Николадзе, 15", "Ортачала"]
    c = from_tg({**tg, "text": "Аренда | ID 13918\nВаке | ул.Шатберашвили\n\n2 спальни | 145м² | 5/7 этаж\n$1,650\n"})
    assert (c["price_usd"], c["area"], c["floor"], c["bedrooms"]) == (1650, 145, 5, 2)
    assert from_tg({**tg, "text": "Продается квартира\n📍Район #Крцаниси\n113 кв. м.\n💵369 000$"}) is None
    assert from_tg({**tg, "text": "🔥 ИЩЕМ МОДЕРАТОРОВ ЧАТА В КОМАНДУ\nдоход от 800$"}) is None
    assert from_tg({**tg, "text": "✨ Сдаётся коммерческое помещение в районе Сабуртало\n100 м²\nЦена: $2000"}) is None
    # competitors are agencies by design: phone/account signals must not drop them
    same = {"text": "call 599 12 34 56", "owner_id": "tg:x", "source": "tg:x", "hashes": []}
    rows = [dict(same) for _ in range(3)]
    agent_signals(rows)
    assert all("agent_reasons" not in r for r in rows)
    # facebook group posts (real formats): free-text fields, author-based agent signals
    fb = {"id": "1", "group": "g1", "post_urls": ["u"], "author": "https://facebook.com/x", "photos": []}
    c = from_facebook({**fb, "text": "🏡 Beautiful Apartment for Rent in Vake\n📍 Location: Chavchavadze Avenue, Vake\n"
                                     "📐 Size: 75 m²\n🏢 Floor: 9th floor\n🛏 2 Bedrooms\n💰 Price: $1,300 / month"})
    assert (c["source"], c["owner_id"], c["price_usd"], c["area"], c["floor"], c["bedrooms"]) == \
        ("fb:g1", "fb:https://facebook.com/x", 1300, 75, 9, 2) and c["geo_queries"][0] == "Chavchavadze Avenue, Vake"
    c = from_facebook({**fb, "author": None, "text": "ქირავდება 1 ოთახიანი ბინა 40 კვ 5 სართული 800₾"})
    assert (c["owner_id"], c["price_usd"], c["area"], c["floor"]) == ("fb:anon:1", 296, 40, 5)
    rows = [{**from_facebook({**fb, "id": str(n), "text": f"For rent {n} bedroom, {50 + 20 * n} m2, ${500 * n}"}),
             "hashes": [(1 << 32 * n) - 1]} for n in (1, 2)]  # far-apart photo hashes: two different flats
    rows.append({**from_facebook({**fb, "id": "9", "author": "https://facebook.com/tbilisi.realty", "text": "For rent $900"}),
                 "hashes": [0xFFFF]})
    agent_signals(rows)
    assert all(r["agent_reasons"] for r in rows), rows  # same author, two flats / agency-like name
    print("ok")


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
