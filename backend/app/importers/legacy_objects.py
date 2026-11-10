"""Stage 3: one registry of unique objects from David's two work groups, the agency channel and the Google Sheets.

    python -m app.importers.legacy_objects build --groups DIR --channel DIR --sheets DIR --out DIR
    python -m app.importers.legacy_objects load --out DIR      # after sheets_csv, into DATABASE_URL
    python -m app.importers.legacy_objects texts --groups DIR --channel DIR --out DIR   # every post's text per object
    python -m app.importers.legacy_objects prefill [--texts DIR/post_texts.json]      # fill empty fields from them

Offline (no DB, no Telegram): reads the raw exports and writes out/registry.json (objects with sources, owner, agent,
status history, deals, media to download), out/review.csv (doubtful merges) and out/summary.json.

Units are object posts: an own post in an object topic (text + the media sent around it), a channel post forwarded
into a topic, a live channel album. Units are merged only on exact evidence:
  - the same channel post id (forwards, t.me/rent_tbilisi_ge/N links);
  - Owner INFORMATION card: originals in Advertising ↔ ready post (+ owner phone, name, agent);
  - the same normalized post body (a repost / copy of the same text);
  - the same address + bedrooms + floor + area (±5%).
Text / address evidence never joins two objects with different known owner phones. Same address + bedrooms that
stayed separate (floor or area differ or unknown, or owners differ) go to review.csv.
Agent: Owner INFORMATION hashtag > Sheets (Listings by Advertising id, Active_Listings by channel id) > hashtag at the
end of the post; none → David. Status: the last event (Archives, Rented By Owner, САМ СДАЛ, Сданные / deals, Update,
Discount) by date.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.services.dedup import _address_parts, norm_phone

csv.field_size_limit(10 ** 8)
CHANNEL_ID = -1001832464267  # @rent_tbilisi_ge
CH_LINK = re.compile(r"t\.me/rent_tbilisi_ge/(\d+)")
GROUP_LINK = re.compile(r"t\.me/c/(\d+)/(?:(\d+)/)?(\d+)")
GROUPS = {"team-chat": 1933642420, "recruiting-team-chat": 2252468572}
GAP = 600  # seconds: media sent within 10 min of an object text by the same person belong to it

# topic → role. "object": object posts; others are events about posts (the post itself may be forwarded there too)
TOPICS = {
    ("recruiting-team-chat", "Advertising"): "object",
    ("recruiting-team-chat", "Ready For Publication"): "object",
    ("recruiting-team-chat", "Archives"): "archived",
    ("recruiting-team-chat", "Rented By Owner"): "rented_by_owner",
    ("recruiting-team-chat", "Update"): "updated",
    ("recruiting-team-chat", "Discount"): "discount",
    ("recruiting-team-chat", "Rented/Sold By Agent"): "rented_by_us",
    ("recruiting-team-chat", "Exclusive Listing 💎"): "exclusive",
    ("team-chat", "Объявления - Tbilisi"): "object",
    ("team-chat", "Архив объявления Tbilisi"): "archived",
    ("team-chat", "Архив объявления Батуми"): "archived",
    ("team-chat", "Discount - Tbilisi"): "discount",
    ("team-chat", "Updates 🔝 Tbilisi"): "updated",
    ("team-chat", "САМ СДАЛ собственник или Дубль - Тбилиси"): "rented_by_owner",
    ("team-chat", "Сданные объекты - Tbilisi"): "rented_by_us",
}
# status after an event; None = the object stays as it was (still on the market)
EVENT_STATUS = {"archived": ("archived", "not_actual"), "rented_by_owner": ("archived", "rented_by_owner"),
                "rented_by_us": ("rented", "rented_by_us"), "updated": None, "discount": None, "exclusive": None,
                "posted": None}


def ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def jl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- post parsing (David's hashtag template)

def plain(t: str) -> str:
    """Markdown links → their text; bold markers off."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t or "").replace("**", "")


def address_of(t: str) -> str | None:
    for line in plain(t).splitlines():
        if "📍" not in line or "APARTMENTS ON MAP" in line.upper():
            continue
        a = line.split("📍", 1)[1].strip(" |#")
        a = re.sub(r"#\w+", "", a).strip(" |,")
        if re.search(r"\d", a) and len(a) > 3:
            return a
    return None


def bedrooms_of(t: str) -> int | None:
    if re.search(r"#?studio\b", t, re.I):
        return 0
    m = re.search(r"#?(\d)\s*-?\s*bed", t, re.I)
    return int(m.group(1)) if m else None


def area_of(t: str) -> float | None:
    m = re.search(r"(\d{2,4}(?:[.,]\d)?)\s*(?:sq\.?\s*m|m2|м2|кв\.?\s*м|sqm)", t, re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def floor_of(t: str) -> int | None:
    m = (re.search(r"(\d{1,2})\s*/\s*\d{1,2}\s*floor", t, re.I) or re.search(r"(\d{1,2})\s*floor", t, re.I)
         or re.search(r"#(\d{1,2})floor", t, re.I))
    return int(m.group(1)) if m else None


def total_floors_of(t: str) -> int | None:
    m = re.search(r"\b\d{1,2}\s*/\s*(\d{1,2})\s*(?:-?th\s*)?floor", t, re.I)
    return int(m.group(1)) if m and 0 < int(m.group(1)) < 80 else None


# old post hashtags -> the generator's values (properties.POST_ONE / POST_MANY); case and the agents' typos folded
_TAG_FIX = {"smeshed": "Mixed", "showercabin": "Shower", "byagreemnt": "ByAgreement", "ac": "Conditioner",
            "parking": "ParkingPlace", "washer": "WashingMachine"}
_PETS = {"byagreement": "ByAgreement", "byagreemnt": "ByAgreement", "notallowed": "NotAllowed", "allowed": "Allowed"}


def post_fields_of(t: str) -> dict:
    """The generator fields an old agency post already states by its hashtags (#OldBuilding #White #CentralHeating
    #6Month, Pets: #ByAgreement, Tenants: 1-2, Deposit 400$). Only what the text says; the rest stays empty."""
    from app.services.properties import POST_MANY, POST_ONE, clean_post_fields
    one = {k: {v.lower(): v for v in vals} for k, vals in POST_ONE.items() if k != "pets"}
    many = {k: {v.lower(): v for v in vals} for k, vals in POST_MANY.items()}
    pf: dict = {}
    for tag in re.findall(r"#(\w+)", re.sub(r"(?:✖️|❌|✖)\s*#\w+", "", t)):  # «✖️#Dishwasher» = there is none
        low = tag.lower()
        low = _TAG_FIX.get(low, tag).lower()
        if m := re.fullmatch(r"(\d{1,2})months?", low):
            low = f"{int(m.group(1))}month"
        for k, vals in one.items():
            if low in vals and k not in pf:
                pf[k] = vals[low]
        for k, vals in many.items():
            if low in vals and vals[low] not in pf.setdefault(k, []):
                pf[k].append(vals[low])
    if m := re.search(r"pets?\s*:?\s*#?([a-z\- ]{5,16})", t, re.I):
        if pet := _PETS.get(re.sub(r"[^a-z]", "", m.group(1).lower())):
            pf["pets"] = pet
    if "prop_type" not in pf and re.search(r"#?studio\b|\d\s*-?\s*bed", t, re.I):
        pf["prop_type"] = "Apartment"  # «#Studio», «1Bed», «2 bedroom»: a flat unless the post says otherwise
    if m := re.search(r"tenants?\s*:?\s*(?:\d+\s*-\s*)?(\d{1,2})\b", t, re.I):
        pf["tenants"] = int(m.group(1))
    if m := re.search(r"deposit\s*:?\s*\$?\s*(\d{2,6})", t, re.I):
        pf["deposit"] = int(m.group(1))
    if pf.get("deal") == "Sale" and pf.get("prop_type") is None:
        pf.pop("prop_type", None)
    return clean_post_fields({k: v for k, v in pf.items() if v not in (None, [], "")})


def price_of(t: str) -> int | None:
    m = (re.search(r"💰[^\d\n]*(\d[\d ,.]*)\s*\$", t) or re.search(r"💰\s*\$\s*(\d[\d ,.]*)", t)
         or re.search(r"price[^\d\n]{0,20}(\d[\d ,.]*)\s*\$", t, re.I))
    if not m:
        return None
    v = int(re.sub(r"\D", "", m.group(1).split(".")[0]) or 0)
    return v if 50 <= v <= 200000 else None


def district_of(t: str) -> str | None:
    head = plain(t).strip()[:120]
    m = re.match(r"\s*(?:\[[^\]]*\])?\s*📍?\s*#?([A-Z][a-zA-Z]+)", head)
    return m.group(1) if m and m.group(1).lower() not in ("for", "update", "discount", "rented") else None


def body_key(t: str) -> str | None:
    """Fingerprint of a post body: the same text reposted (or copied draft → channel) gets the same key."""
    s = plain(t).lower()
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"#(update|discount|exclusive|rented\w*|owner)\b", " ", s)
    s = re.sub(r"\+?\d[\d \-]{7,}\d", " ", s)  # phones
    s = re.sub(r"[^a-zа-яё0-9]+", "", s)
    return hashlib.sha1(s[:600].encode()).hexdigest() if len(s) > 80 else None


# channel announcements, not flats: «❗️#Rented Commercial space in 📍Vera has found a business», «#FlatRented ... found a
# tenant», «Daily Actual Listing», «#NewUpdate Hi, Friends!»
ANNOUNCEMENT = re.compile(r"found (?:a |new )*(?:tenant|business)|daily actual listing|#newupdate", re.I)


def is_object_text(t: str | None) -> bool:
    return bool(t) and "📍" in t and not ANNOUNCEMENT.search(t) and (bedrooms_of(t) is not None or area_of(t) or re.search(r"commercial|house|office",
                                                                                            t, re.I))


def addr_key(a: str | None) -> str | None:
    if not a:
        return None
    street, nums = _address_parts(a)
    return " ".join(sorted(street)) + "|" + " ".join(sorted(nums)) if street and nums else None


def phones_of(t: str) -> set[str]:
    out = set()
    t = re.sub(r"\S*(?:https?://|t\.me/)\S*", " ", t or "")  # link ids are not phones
    for p in re.findall(r"\+?\d[\d \-()]{7,}\d", t):
        d = re.sub(r"\D", "", norm_phone(p))
        if len(d) >= 9:
            out.add(d[-9:])
    return out


def tags_of(t: str) -> list[str]:
    return re.findall(r"#([A-Za-zА-Яа-я][\wА-Яа-я]*)", t or "")


# ---------------------------------------------------------------- the build

class Registry:
    def __init__(self):
        self.units: dict[str, dict] = {}          # unit id → parsed post
        self.parent: dict[str, str] = {}          # union-find over unit ids
        self.ch_unit: dict[int, str] = {}         # channel message id → unit
        self.events: list[tuple] = []             # (channel id | unit id, kind, date, extra)
        self.review: list[dict] = []
        self.phones: dict[str, set] = {}          # root → owner phones known for it (guards weak merges)
        self.stats = Counter()

    # union-find
    def find(self, x):
        while self.parent.setdefault(x, x) != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b, why, weak=False):
        """weak (text / address evidence) never joins two objects whose known owner phones differ."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        pa, pb = self.phones.get(ra, set()), self.phones.get(rb, set())
        if weak and pa and pb and not pa & pb:
            self.stats["blocked_by_owner_phone:" + why] += 1
            return False
        self.parent[rb] = ra
        self.phones[ra] = pa | pb
        self.stats["merge:" + why] += 1
        return True

    def add_unit(self, uid, text, date, media, src, ch_ids=(), **extra):
        u = {"id": uid, "src": src, "date": date, "text": text, "media": media, "address": address_of(text),
             "district": district_of(text), "bedrooms": bedrooms_of(text), "area": area_of(text),
             "floor": floor_of(text), "price": price_of(text), "exclusive": bool(re.search(r"#exclusive", text, re.I)),
             "tags": tags_of(text), "body": body_key(text), "ch_ids": sorted(set(ch_ids)), **extra}
        u["akey"] = addr_key(u["address"])
        self.units[uid] = u
        self.find(uid)
        for c in u["ch_ids"]:
            if c in self.ch_unit:
                self.union(self.ch_unit[c], uid, "same_channel_post")
            else:
                self.ch_unit[c] = uid
        return u


def media_row(m: dict, where: str) -> dict:
    mi = m["media"] or {}
    return {"where": where, "msg": m["id"], "type": m["media_type"], "mime": mi.get("mime"), "size": mi.get("size"),
            "name": mi.get("name"), "tl_id": mi.get("tl_id"), "w": mi.get("width"), "h": mi.get("height"),
            "orig": m["media_type"] == "document" and (mi.get("mime") or "").startswith(("image", "video"))}


def load_group(reg: Registry, gdir: Path, name: str):
    topics = {t["id"]: t["title"] for t in json.loads((gdir / "topics.json").read_text())["topics"]}
    msgs = list(jl(gdir / "messages.jsonl"))
    by_id = {m["id"]: m for m in msgs}
    for m in msgs:
        m["_topic"] = topics.get(m["topic_id"], "")
        m["_t"] = ts(m["date"])
    gid = GROUPS[name]

    # forwarded channel albums: one unit per album (all its channel ids), events by the topic
    albums = defaultdict(list)
    for m in msgs:
        f = m["fwd"] or {}
        if f.get("from_id") == CHANNEL_ID and f.get("channel_post"):
            albums[(m["topic_id"], m["grouped_id"] or ("single", m["id"]))].append(m)
    for (_tid, _g), ms in albums.items():
        text_m = next((x for x in ms if x["text"]), None)
        role = TOPICS.get((name, ms[0]["_topic"]))
        ch_ids = [x["fwd"]["channel_post"] for x in ms]
        posted = min((x["fwd"]["date"] for x in ms if x["fwd"].get("date")), default=ms[0]["date"])
        if text_m and is_object_text(text_m["text"]):
            reg.add_unit(f"ch:{min(ch_ids)}", text_m["text"], posted,
                         [media_row(x, f"{name}/{x['id']}") for x in ms if x["media_type"]], "channel_fwd", ch_ids)
        if role and role != "object":
            for c in ch_ids[:1]:
                reg.events.append((c, role, ms[0]["date"], {"topic": ms[0]["_topic"]}))
        elif role == "object":
            reg.events.append((min(ch_ids), "posted", posted, {}))

    # own object posts: text + media of the same sender within GAP
    own = defaultdict(list)
    for m in msgs:
        if TOPICS.get((name, m["_topic"])) == "object" and not (m["fwd"] or {}).get("channel_post"):
            own[(m["topic_id"], m["sender_id"])].append(m)
    for (tid, _s), ms in own.items():
        ms.sort(key=lambda x: x["_t"])
        texts = [x for x in ms if is_object_text(x["text"])]
        if not texts:
            continue
        att = defaultdict(list)
        for x in ms:
            if not x["media_type"] or not (x["media"] or {}).get("size"):
                continue
            best = min(texts, key=lambda t: abs(t["_t"] - x["_t"]))
            if abs(best["_t"] - x["_t"]) <= GAP:
                att[best["id"]].append(media_row(x, f"{name}/{x['id']}"))
        topic = ms[0]["_topic"]
        for t in texts:
            src = {"Advertising": "advertising", "Ready For Publication": "ready"}.get(topic, "team")
            reg.add_unit(f"{name}:{t['id']}", t["text"], t["date"], att[t["id"]], src,
                         sender=t["sender_name"], topic=topic)

    # plain-link events: "#Update | t.me/rent_tbilisi_ge/N ...", "#Discount $800 ➡️ $750 | link", "Уберите | links"
    for m in msgs:
        role = TOPICS.get((name, m["_topic"]))
        if not role or role == "object" or (m["fwd"] or {}).get("channel_post") or not m["text"]:
            continue
        extra = {"topic": m["_topic"]}
        if role == "discount":
            p = re.search(r"\$?\s*(\d{2,6})\s*\$?\s*(?:➡️|->|→)\s*\$?\s*(\d{2,6})", m["text"])
            if p:
                extra.update(price_from=int(p.group(1)), price_to=int(p.group(2)))
        if role == "rented_by_us":
            extra.update(deal_text=m["text"])
        for c in CH_LINK.findall(m["text"]):
            reg.events.append((int(c), role, m["date"], extra))
    return msgs, by_id, topics


def load_channel(reg: Registry, cdir: Path):
    msgs = list(jl(cdir / "messages.jsonl"))
    groups = defaultdict(list)
    for m in msgs:
        groups[m["grouped_id"] or ("single", m["id"])].append(m)
    live = set()
    for ms in groups.values():
        live |= {x["id"] for x in ms}
        text_m = next((x for x in ms if x["text"]), None)
        if text_m and is_object_text(text_m["text"]):
            ids = [x["id"] for x in ms]
            reg.add_unit(f"ch:{min(ids)}", text_m["text"], text_m["date"],
                         [media_row(x, f"channel/{x['id']}") for x in ms if x["media_type"]], "channel_live", ids,
                         live=True)
            reg.events.append((min(ids), "posted", text_m["date"], {}))
    return live


def build(groups: Path, channel: Path, sheets: Path, out: Path, photo_merges: Path | None = None) -> dict:
    reg = Registry()
    rec_msgs, rec_by_id, _ = load_group(reg, groups / "recruiting-team-chat", "recruiting-team-chat")
    team_msgs, team_by_id, _ = load_group(reg, groups / "team-chat", "team-chat")
    live = load_channel(reg, channel)
    for u in reg.units.values():
        if u["id"].startswith("ch:") and set(u["ch_ids"]) & live:
            u["live"] = True

    # ---- people: agents by hashtag
    agents = rows(sheets / "Agents.csv") + rows(sheets / "Recruits.csv")
    agent_by_tag = {}
    for a in agents:
        for k in (a.get("") or "", a.get("name") or ""):
            k = k.strip().lstrip("#").lower()
            if k:
                agent_by_tag.setdefault(k, a["name"].strip())
    seen_tags = Counter()

    def agent_of(tag: str | None) -> str | None:
        if not tag:
            return None
        seen_tags[tag] += 1
        return agent_by_tag.get(tag.lower()) or tag  # unknown hashtag kept as is (mapping table in summary)

    owner_cards = []  # (unit ids, phone, name, address, agent tag, date)
    # ---- Owner INFORMATION (recruiting, topic 97)
    for m in rec_msgs:
        if m["_topic"] != "Owner INFORMATION" or not m["text"] or "внесен" not in m["text"]:
            continue
        t = plain(m["text"])
        uids = []
        for g, _top, mid in GROUP_LINK.findall(m["text"]):
            if int(g) == GROUPS["recruiting-team-chat"]:
                uids.append(f"recruiting-team-chat:{mid}")
        for c in CH_LINK.findall(m["text"]):
            uids.append(("ch", int(c)))
        tag = re.search(r"Агент:\s*#(\w+)", t)
        name = re.search(r"Имя собственника:\s*([^\n|]+)", t)
        adr = re.search(r"Адрес:\s*([^\n|]+)", t)
        owner_cards.append({"refs": uids, "phones": phones_of(t.split("Дата")[0]), "name": name and name.group(1).strip(),
                            "address": adr and adr.group(1).strip(), "agent": tag and tag.group(1), "date": m["date"],
                            "src": "owner_information"})
    # ---- Telefon Owner (team chat): link to a post in «Объявления» + owner
    for m in team_msgs:
        if m["_topic"] != "Telefon Owner Tbilisi" or not m["text"]:
            continue
        refs = [f"team-chat:{mid}" for g, _t, mid in GROUP_LINK.findall(m["text"]) if int(g) == GROUPS["team-chat"]]
        refs += [("ch", int(c)) for c in CH_LINK.findall(m["text"])]
        t = plain(m["text"])
        nm = re.sub(r"#\w+|https?://\S+|\+?\d[\d \-()]{7,}\d", " ", t)
        nm = re.sub(r"\s+", " ", nm).strip(" |?") or None
        owner_cards.append({"refs": refs, "phones": phones_of(t), "name": nm, "address": None, "agent": None,
                            "date": m["date"], "src": "telefon_owner"})

    def unit_of(ref):
        if isinstance(ref, tuple):
            return reg.ch_unit.get(ref[1])
        if ref in reg.units:
            return ref
        # a link to a media message of a post → the post it was attached to
        return media_owner.get(ref)

    media_owner = {}
    for u in reg.units.values():
        for x in u["media"]:
            g = x["where"].split("/")[0]
            media_owner[f"{g}:{x['msg']}"] = u["id"]

    for c in owner_cards:
        us = [unit_of(r) for r in c["refs"]]
        us = [u for u in us if u]
        c["units"] = us
        for a, b in zip(us, us[1:]):
            reg.union(a, b, "owner_card")
        if us:
            r0 = reg.find(us[0])
            reg.phones[r0] = reg.phones.get(r0, set()) | c["phones"]
        if not us:
            reg.stats["owner_card_unlinked:" + c["src"]] += 1

    # ---- Sheets: agent by Advertising id (Listings) and by channel id (Active_Listings)
    sheet_agent = {}
    for r in rows(sheets / "Listings.csv"):
        if r["message_id"].isdigit():
            sheet_agent[f"recruiting-team-chat:{r['message_id']}"] = r["agent"].strip()
    active = {}
    for r in rows(sheets / "Active_Listings.csv"):
        for c in CH_LINK.findall(r["link"]):
            active[int(c)] = r
            u = reg.ch_unit.get(int(c))
            if u:
                sheet_agent.setdefault(u, r["agent"].strip())

    # ---- same Telegram files (photo id, or document name+size) in ≥3 → same object
    by_file = defaultdict(set)
    for u in reg.units.values():
        for x in u["media"]:
            for k in (x["tl_id"], (x["name"], x["size"]) if x["name"] and x["size"] else None):
                if k:
                    by_file[k].add(u["id"])
    shared = Counter()
    for us in by_file.values():
        if 1 < len(us) <= 20:  # ponytail: a file reused in >20 posts is a logo/stock shot, not evidence
            us = sorted(us)
            for i, a in enumerate(us):
                for b in us[i + 1:]:
                    shared[a, b] += 1
    for (a, b), n in shared.items():
        if n >= 3 and reg.find(a) != reg.find(b):
            ua, ub = reg.units[a], reg.units[b]
            if reg.union(a, b, "same_files", weak=True) and ua["akey"] and ub["akey"] and ua["akey"] != ub["akey"]:
                reg.stats["same_files_diff_address"] += 1

    # ---- same body text → same object
    by_body = defaultdict(list)
    for u in reg.units.values():
        if u["body"]:
            by_body[u["body"]].append(u["id"])
    for ids in by_body.values():
        for b in ids[1:]:
            reg.union(ids[0], b, "same_text", weak=True)

    # ---- same address + bedrooms + floor + area(±5%) → same object; partial matches → review
    by_addr = defaultdict(list)
    for u in reg.units.values():
        if u["akey"] and u["bedrooms"] is not None:
            by_addr[(u["akey"], u["bedrooms"])].append(u)
    for (ak, beds), us in by_addr.items():
        for i, a in enumerate(us):
            for b in us[i + 1:]:
                if reg.find(a["id"]) == reg.find(b["id"]):
                    continue
                same_floor = a["floor"] is not None and a["floor"] == b["floor"]
                same_area = a["area"] and b["area"] and abs(a["area"] - b["area"]) <= 0.05 * max(a["area"], b["area"])
                if same_floor and same_area:
                    reg.union(a["id"], b["id"], "address_beds_floor_area", weak=True)

    # ---- photo evidence from the downloaded media (resolve_review.py / exact sha256 groups):
    # pairs of object keys (= unit ids) whose photos match; owner-phone guard still applies
    if photo_merges and photo_merges.exists():
        for a, b in json.loads(photo_merges.read_text()):
            if a in reg.units and b in reg.units:
                reg.union(a, b, "same_photos", weak=True)

    # review: still separate objects at the same address with the same bedrooms
    for (ak, beds), us in by_addr.items():
        roots = {reg.find(u["id"]) for u in us}
        if len(roots) > 1:
            reg.review.append({"why": "same address + bedrooms, floor/area differ", "address": us[0]["address"],
                               "bedrooms": beds, "objects": len(roots),
                               "units": " ".join(f"{u['id']}(fl {u['floor']}, {u['area']} m²)" for u in us[:12])})

    # ---- objects
    objs = defaultdict(list)
    for uid in reg.units:
        objs[reg.find(uid)].append(reg.units[uid])
    ch_obj = {c: reg.find(u) for c, u in reg.ch_unit.items()}

    cards_by_obj = defaultdict(list)
    for c in owner_cards:
        if c["units"]:
            cards_by_obj[reg.find(c["units"][0])].append(c)

    ev_by_obj = defaultdict(list)
    unlinked_events = Counter()
    for c, kind, date, extra in reg.events:
        root = ch_obj.get(c)
        if root is None:
            unlinked_events[kind] += 1
            continue
        ev_by_obj[root].append({"kind": kind, "date": date, "channel_post": c, **extra})

    out_objs = []
    owners = {}  # phone → owner
    for root, us in objs.items():
        us.sort(key=lambda u: u["date"])
        last = us[-1] if us else None
        cards = sorted(cards_by_obj.get(root, []), key=lambda c: c["date"])
        r = None
        # agent: owner card > sheets > hashtag at the end of a post > David
        agent, agent_src = None, None
        for c in reversed(cards):
            if c["agent"]:
                agent, agent_src = agent_of(c["agent"]), "owner_information"
                break
        if not agent:
            for u in reversed(us):
                if sheet_agent.get(u["id"]):
                    agent, agent_src = sheet_agent[u["id"]], "sheets"
                    break
        if not agent and r:
            agent, agent_src = r["agent"].strip() or None, "sheets"
        if not agent:
            for u in reversed(us):
                tg = [t for t in u["tags"][-3:] if t.lower() in agent_by_tag]
                if tg:
                    agent, agent_src = agent_of(tg[-1]), "post_hashtag"
                    break
        if not agent:
            agent, agent_src = "David", "default"
        # owner
        phones, oname = set(), None
        for c in cards:
            phones |= c["phones"]
            oname = c["name"] or oname
        # status from the last event
        evs = sorted(ev_by_obj.get(root, []), key=lambda e: e["date"])
        status, reason = "published", None
        for e in evs:
            st = EVENT_STATUS.get(e["kind"])
            if st:
                # moving a rented post to Archives afterwards keeps the more specific «rented» reason
                if not (e["kind"] == "archived" and reason in ("rented_by_us", "rented_by_owner")):
                    status, reason = st
            elif e["kind"] in ("posted", "updated", "discount"):
                status, reason = "published", None
        dates = [u["date"] for u in us] + [e["date"] for e in evs] + ([r["date"]] if r else [])
        last_seen = max(dates) if dates else None
        if status == "published" and not any(u.get("live") for u in us):
            # not in the channel any more and no event says why: archived as not actual
            status, reason = "archived", "not_actual"
        f = lambda k: next((u[k] for u in reversed(us) if u[k] is not None), None)
        o = {"key": root, "units": [u["id"] for u in us], "channel_posts": sorted({c for u in us for c in u["ch_ids"]}),
             "address": f("address") or (cards[-1]["address"] if cards else None), "district": f("district") or (r and r["district"]),
             "bedrooms": f("bedrooms"), "area": f("area"), "floor": f("floor"),
             "price": f("price") or (r and r["price"]), "exclusive": any(u["exclusive"] for u in us),
             "agent": agent, "agent_src": agent_src, "owner_phones": sorted(phones), "owner_name": oname,
             "status": status, "archive_reason": reason, "first_seen": min(dates) if dates else None,
             "last_seen": last_seen, "live": any(u.get("live") for u in us), "events": evs,
             "text": last["text"] if last else None}
        out_objs.append(o)
        for p in phones:
            owners.setdefault(p, {"phone": p, "names": set(), "objects": [], "agent": agent})
            owners[p]["objects"].append(root)
            if oname:
                owners[p]["names"].add(oname)

    # ---- owners from Owner_Base (Sheets): attach by phone, else owner without an object (agent: David)
    ob_new = 0
    for r in rows(sheets / "Owner_Base.csv"):
        for p in phones_of(r.get("Telephone")):
            if p not in owners:
                owners[p] = {"phone": p, "names": set(), "objects": [], "agent": "David", "address": r.get("Address")}
                ob_new += 1
            if (r.get("Name of Owner") or "").strip():
                owners[p]["names"].add(r["Name of Owner"].strip())

    # ---- deals: Rented/Sold By Agent posts (link → object) + Sheets Rent_out_Apartment (cadastral/phone/address)
    deals = []
    for o in out_objs:
        for e in o["events"]:
            if e.get("deal_text"):
                t = plain(e["deal_text"])
                cad = re.search(r"Cadastral[^:]*:\s*([\d.]+)", t)
                deals.append({"object": o["key"], "date": e["date"], "cadastral": cad and cad.group(1).strip("."),
                              "price": (re.search(r"Price:\s*(\d+)", t) or [None, None])[1],
                              "owner_phones": sorted(phones_of(t.split("Owner")[-1][:60])) if "Owner" in t else [],
                              "src": "rented_by_agent"})
    obj_by_phone = defaultdict(set)
    for o in out_objs:
        for p in o["owner_phones"]:
            obj_by_phone[p].add(o["key"])
    obj_by_cad = {d["cadastral"]: d["object"] for d in deals if d["cadastral"]}
    obj_by_akey = defaultdict(set)
    for o in out_objs:
        if addr_key(o["address"]):
            obj_by_akey[addr_key(o["address"])].add(o["key"])
    with open(sheets / "Rent_out_Apartment.csv", encoding="utf-8-sig", newline="") as f:
        ro = list(csv.reader(f))[1:]
    linked = Counter()
    for r in ro:
        if len(r) < 16 or not r[1].strip():
            continue
        cad, ph, adr = r[1].strip().strip("."), phones_of(r[7]), r[12]
        target, how = None, None
        if cad in obj_by_cad:
            target, how = obj_by_cad[cad], "cadastral"
        if not target:
            cand = set().union(*[obj_by_phone[p] for p in ph]) if ph else set()
            ak = addr_key(adr)
            both = cand & obj_by_akey.get(ak, set()) if ak else set()
            if len(both) == 1:
                target, how = both.pop(), "phone+address"
            elif len(cand) == 1:
                target, how = next(iter(cand)), "phone"
            elif ak and len(obj_by_akey.get(ak, ())) == 1:
                target, how = next(iter(obj_by_akey[ak])), "address"
        linked[how or "none"] += 1
        deals.append({"object": target, "date": r[0], "cadastral": cad, "price": r[2], "owner_phones": sorted(ph),
                      "owner_name": r[6], "agent": r[15], "rent_from": r[4], "rent_to": r[5], "address": adr,
                      "src": "sheets_rent_out", "linked_by": how})
    cad_of = {}
    for d in deals:
        if d["object"] and d["cadastral"]:
            cad_of.setdefault(d["object"], d["cadastral"])
    for o in out_objs:
        o["cadastral"] = cad_of.get(o["key"])

    # ---- media to download, no file twice:
    # photos = originals of the newest Advertising post, else photos of the newest post that has photos;
    # video = the largest video of the newest post with one (optional unless the object is on the market).
    vid = lambda x: x["type"] == "video" or (x["mime"] or "").startswith("video")
    seen_files, plan = set(), []
    for o in out_objs:
        units = sorted((reg.units[u] for u in o["units"]), key=lambda u: u["date"], reverse=True)
        photos = next(([x for x in u["media"] if x["orig"] and not vid(x)] for u in units
                       if any(x["orig"] and not vid(x) for x in u["media"])), None) or \
            next(([x for x in u["media"] if x["type"] == "photo"] for u in units
                  if any(x["type"] == "photo" for x in u["media"])), [])
        video = next((max((x for x in u["media"] if vid(x)), key=lambda x: x["size"] or 0) for u in units
                      if any(vid(x) for x in u["media"])), None)
        files = []
        for x in photos + ([dict(video, optional=o["status"] != "published")] if video else []):
            keys = {k for k in (x["tl_id"], (x["name"], x["size"]) if x["name"] else None) if k}
            if keys & seen_files:
                continue
            seen_files |= keys
            files.append(x)
        o["media"] = files
        plan += [{"object": o["key"], **x} for x in files]

    for o in out_objs:  # one object, several owners or very many posts: possibly two flats glued together
        if len(o["owner_phones"]) > 1 or len(o["units"]) >= 15:
            reg.review.append({"why": f"one object: {len(o['owner_phones'])} owner phones, {len(o['units'])} posts",
                               "address": o["address"], "bedrooms": o["bedrooms"], "objects": 1,
                               "units": " ".join(o["units"][:12])})
    out.mkdir(parents=True, exist_ok=True)
    for o in out_objs:
        o["owner_phones"] = sorted(o["owner_phones"])
    (out / "registry.json").write_text(json.dumps(
        {"objects": out_objs, "owners": [{**v, "names": sorted(v["names"])} for v in owners.values()], "deals": deals},
        ensure_ascii=False, default=str))
    with open(out / "review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["why", "address", "bedrooms", "objects", "units"])
        w.writeheader()
        w.writerows(reg.review)
    G = 1024 ** 3
    summary = {
        "units": Counter(u["src"] for u in reg.units.values()),
        "objects": len(out_objs),
        "active_listings_rows_without_text": sum(1 for c in active if c not in ch_obj),
        "status": Counter(f"{o['status']}/{o['archive_reason']}" for o in out_objs),
        "live_in_channel": sum(o["live"] for o in out_objs),
        "agent_source": Counter(o["agent_src"] for o in out_objs),
        "agents": Counter(o["agent"] for o in out_objs).most_common(60),
        "unknown_agent_tags": sorted(t for t in seen_tags if t.lower() not in agent_by_tag),
        "with_owner_phone": sum(bool(o["owner_phones"]) for o in out_objs),
        "with_cadastral": sum(bool(o["cadastral"]) for o in out_objs),
        "owners": len(owners), "owners_without_object": sum(not v["objects"] for v in owners.values()),
        "owners_added_from_owner_base": ob_new,
        "deals": Counter(d["src"] for d in deals), "rent_out_linked_by": linked,
        "merges": {k: v for k, v in reg.stats.items()},
        "events_unlinked": unlinked_events, "review_rows": len(reg.review),
        "media_files": len(plan), "media_photo_gb": round(sum(x["size"] or 0 for x in plan if not vid(x)) / G, 1),
        "media_video_gb": round(sum(x["size"] or 0 for x in plan if vid(x)) / G, 1),
        "media_video_required_gb": round(sum(x["size"] or 0 for x in plan if vid(x) and not x.get("optional")) / G, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    with open(out / "media_plan.jsonl", "w") as f:
        for x in plan:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    return summary


# ---------------------------------------------------------------- load into the CRM (after sheets_csv)

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")


def media_by_object(data: dict, done: Path) -> dict[str, list[dict]]:
    """The downloaded files (legacy_media done.jsonl) per registry object: every file of every post (unit) the object
    holds, one per sha256, photos before videos, newest post first. done.jsonl names an older build's object key, which
    is a unit id, so files follow their unit into whatever object it is merged into now."""
    unit_obj = {u: o["key"] for o in data["objects"] for u in o["units"]}
    rows = {}
    for line in open(done):
        d = json.loads(line)
        if "key" in d:
            rows[d["where"]] = d  # the last success wins
    out = defaultdict(list)
    seen = defaultdict(set)
    vid = lambda d: d["key"].lower().endswith(VIDEO_EXT)
    for d in sorted(rows.values(), key=lambda d: (vid(d), -int(d["where"].split("/")[1]))):
        o = unit_obj.get(d["object"])
        if o and d["sha256"] not in seen[o]:
            seen[o].add(d["sha256"])
            out[o].append(d)
    return out


def existing_match(db, o: dict, cache: dict):
    """A CRM object (not from this import) that is this legacy object: the same agency-channel post, else the same
    cadastral code. Owner phone + address is left out: one owner often has several flats in one building."""
    from sqlalchemy import select

    from app.models import ChannelPost, Property
    if not cache:
        cache["post"], cache["cad"] = {}, {}
        for p in db.scalars(select(Property).where(Property.source_type != "legacy")):
            for m in (p.post_message_id, (p.source_external_id or "")[2:] if p.source_type == "channel" else None):
                if m and str(m).isdigit():
                    cache["post"].setdefault(int(m), p)
            if p.cadastral_code:
                cache["cad"].setdefault(p.cadastral_code.strip(), p)
        for cp in db.scalars(select(ChannelPost).where(ChannelPost.property_id.is_not(None))):
            if str(cp.message_id).isdigit() and cp.property_id:
                p = db.get(Property, cp.property_id)
                if p is not None and p.source_type != "legacy":
                    cache["post"].setdefault(int(cp.message_id), p)
    for c in o["channel_posts"]:
        if c in cache["post"]:
            return cache["post"][c], "channel_post"
    if o.get("cadastral") and o["cadastral"] in cache["cad"]:
        return cache["cad"][o["cadastral"]], "cadastral"
    return None, None


def load(registry: Path, db, media: Path | None = None, media_prefix: str = "legacy/") -> Counter:
    """registry.json → users (unknown agent hashtags become inactive agents), owners, properties, their event history,
    publications (old channel posts), media (files in the bucket under media_prefix, from legacy_media done.jsonl) and
    deals linked by cadastral. A legacy object that already is a CRM object (same channel post or cadastral code) is
    merged into it instead of created. Idempotent: an object already loaded (ai_flags.legacy.key) is skipped."""
    from sqlalchemy import func, select

    from app.models import ChannelPost, Deal, Event, Owner, Property, PropertyMedia, PropertyPublication, User
    data = json.loads(registry.read_text())
    files = media_by_object(data, media) if media else {}
    n = Counter()
    users = list(db.scalars(select(User)))
    by_key = {}
    for u in users:
        for k in (u.agent_tag, u.name):
            if k:
                by_key.setdefault(k.strip().lstrip("#").lower(), u)
    david = by_key.get("david")

    def agent(name):
        if not name:
            return david
        k = name.strip().lstrip("#").lower()
        if k not in by_key:
            u = User(name=name, role="agent", agent_tag=name, is_active=False)
            db.add(u)
            db.flush()
            by_key[k] = u
            n["agents_created_inactive"] += 1
        return by_key[k]

    owner_by_phone = {}
    for o in db.scalars(select(Owner)):
        d = re.sub(r"\D", "", o.phone_norm or "")
        if len(d) >= 9:
            owner_by_phone.setdefault(d[-9:], o)
    obj_agent = {o["key"]: o["agent"] for o in data["objects"]}
    for r in data["owners"]:
        o = owner_by_phone.get(r["phone"])
        if not o:
            o = Owner(name=(r["names"] or [None])[0], phone="+995" + r["phone"], phone_norm=r["phone"],
                      address=r.get("address"))
            db.add(o)
            owner_by_phone[r["phone"]] = o
            n["owners_created"] += 1
        if not o.agent_id:
            newest = r["objects"][-1] if r["objects"] else None
            o.agent_id = agent(obj_agent.get(newest) if newest else None).id
            n["owners_agent_set"] += 1
    db.flush()

    done = set(db.scalars(select(Property.ai_flags["legacy"]["key"].astext).where(Property.ai_flags.has_key("legacy"))))
    done |= set(db.scalars(select(Property.source_external_id).where(Property.source_type == "legacy")))
    when = lambda s: datetime.fromisoformat(s) if s and "T" in s else None
    match_cache: dict = {}
    channel_post = {}
    for cp in db.scalars(select(ChannelPost)):
        if str(cp.message_id).isdigit():
            channel_post[int(cp.message_id)] = cp
    for o in data["objects"]:
        if o["key"] in done:
            n["objects_skipped"] += 1
            continue
        a = agent(o["agent"])
        # one channel post = one album; a unit "ch:<first message id>" is one post, channel_posts holds every message id
        posts = sorted({int(u[3:]) for u in o["units"] if u.startswith("ch:")}) or sorted(o["channel_posts"])[:1]
        last_post = posts[-1] if posts else None
        legacy = {"key": o["key"], "units": o["units"], "agent_src": o["agent_src"], "status": o["status"],
                  "archive_reason": o["archive_reason"]}
        p, why = existing_match(db, o, match_cache)
        if p is not None:
            # the CRM object stays as it is; the legacy side only fills what it lacks and adds its history
            p.cadastral_code = p.cadastral_code or o.get("cadastral")
            p.owner_id = p.owner_id or (owner_by_phone[o["owner_phones"][0]].id if o["owner_phones"] else None)
            p.agent_id = p.agent_id or a.id
            p.ai_flags = {**(p.ai_flags or {}), "legacy": {**legacy, "merged_by": why}}
            n["objects_merged:" + why] += 1
        else:
            p = Property(source_type="legacy", source_external_id=o["key"], status=o["status"],
                     archive_reason=o["archive_reason"],
                     archived_at=when(o["last_seen"]) if o["status"] in ("archived", "rented") else None,
                     address=o["address"], district=o["district"], cadastral_code=o.get("cadastral"),
                     price=o["price"] if str(o["price"] or "").isdigit() else None, bedrooms=o["bedrooms"],
                     area=o["area"], floor=o["floor"], description=o["text"], is_exclusive=o["exclusive"],
                     total_floors=total_floors_of(o["text"] or ""), post_fields=post_fields_of(o["text"] or ""),
                     agent_id=a.id, owner_id=owner_by_phone[o["owner_phones"][0]].id if o["owner_phones"] else None,
                     post_url=f"https://t.me/rent_tbilisi_ge/{last_post}" if last_post else None,
                     post_message_id=str(last_post) if last_post else None,
                     published_at=when(o["first_seen"]), verified_at=when(o["first_seen"]),
                     ai_flags={"legacy": legacy})
            db.add(p)
            n["objects_created"] += 1
        db.flush()
        posted = set(db.scalars(select(PropertyPublication.message_id).where(PropertyPublication.property_id == p.id)))
        for c in posts:
            if str(c) not in posted:
                db.add(PropertyPublication(property_id=p.id, channel="tg", url=f"https://t.me/rent_tbilisi_ge/{c}",
                                           message_id=str(c),
                                           status="published" if o["live"] and c == last_post else "removed"))
                n["publications"] += 1
        for c in o["channel_posts"]:
            if c in channel_post and channel_post[c].property_id is None:
                channel_post[c].property_id = p.id
                n["channel_posts_linked"] += 1
        have = set(db.scalars(select(PropertyMedia.path).where(PropertyMedia.property_id == p.id)))
        pos = db.scalar(select(func.max(PropertyMedia.position)).where(PropertyMedia.property_id == p.id)) or 0
        for f in files.get(o["key"], []):
            path = media_prefix + f["key"]
            if path in have:
                continue
            pos += 1
            db.add(PropertyMedia(property_id=p.id, kind="video" if path.lower().endswith(VIDEO_EXT) else "photo",
                                 path=path, position=pos))
            n["media"] += 1
        for e in o["events"]:
            db.add(Event(entity="property", entity_id=str(p.id), action="legacy_" + e["kind"], actor_label="import",
                         data={k: v for k, v in e.items() if k not in ("kind", "date", "deal_text")}, at=when(e["date"])))
            n["events"] += 1
        if p.cadastral_code:
            n["deals_linked"] += db.query(Deal).filter(Deal.cadastral == p.cadastral_code, Deal.property_id.is_(None)) \
                .update({Deal.property_id: p.id}, synchronize_session=False)
    # objects without an owner card: the Owner_Base owner at the same address, when exactly one has it
    by_addr = defaultdict(list)
    for ow in db.scalars(select(Owner).where(Owner.address.is_not(None))):
        k = addr_key(ow.address)
        if k:
            by_addr[k].append(ow)
    for p in db.scalars(select(Property).where(Property.source_type == "legacy", Property.owner_id.is_(None))):
        cand = by_addr.get(addr_key(p.address)) or []
        if len(cand) == 1:
            p.owner_id = cand[0].id
            n["owner_by_address"] += 1
    # owners nobody brought in: David's (stage 6.5)
    n["owners_to_david"] = db.query(Owner).filter(Owner.agent_id.is_(None)).update({Owner.agent_id: david.id},
                                                                                     synchronize_session=False)
    db.commit()
    n["owners_total"] = db.scalar(select(func.count()).select_from(Owner))
    return n


def post_texts(registry: Path, groups: Path, channel: Path) -> dict[str, list[str]]:
    """object key -> the texts of ALL its posts, newest first (registry.json keeps only the newest)."""
    reg = Registry()
    for name in GROUPS:
        load_group(reg, groups / name, name)
    load_channel(reg, channel)
    out = {}
    for o in json.loads(registry.read_text())["objects"]:
        us = sorted((reg.units[u] for u in o["units"] if u in reg.units), key=lambda u: u["date"], reverse=True)
        out[o["key"]] = list(dict.fromkeys([o["text"] or ""] + [u["text"] for u in us if u["text"]]))
    return out


CARD_PARSERS = {"address": address_of, "district": district_of, "price": price_of, "bedrooms": bedrooms_of,
                "area": area_of, "floor": floor_of, "total_floors": total_floors_of}


def prefill(db, texts: dict[str, list[str]] | None = None) -> Counter:
    """Old-base objects: fill their EMPTY card fields and generator fields from their posts, newest post first (texts:
    every post of the object from post_texts; without it the stored newest post). An agent's own values stay."""
    from sqlalchemy import or_, select
    from app.models import Property
    n = Counter()
    for p in db.scalars(select(Property).where(or_(Property.source_type == "legacy",
                                                   Property.ai_flags["legacy"].isnot(None)))):
        key = (p.ai_flags or {}).get("legacy", {}).get("key") or p.source_external_id
        posts = (texts or {}).get(key) or [p.description or ""]
        if p.source_type != "legacy":
            posts = posts[1:]  # a CRM object merged with the old base: its own description is not a post
        n["objects"] += 1
        for t in posts:
            for f, parse in CARD_PARSERS.items():
                if getattr(p, f) in (None, "") and (v := parse(t)) not in (None, ""):
                    setattr(p, f, v)
                    n[f] += 1
            cur, new = dict(p.post_fields or {}), post_fields_of(t)
            add = {k: v for k, v in new.items() if cur.get(k) in (None, "", [])}
            if add:
                p.post_fields = {**cur, **add}
                for k in add:
                    n["post_fields." + k] += 1
    db.commit()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "load", "texts", "prefill"])
    ap.add_argument("--groups", type=Path)
    ap.add_argument("--channel", type=Path)
    ap.add_argument("--sheets", type=Path)
    ap.add_argument("--media", type=Path, help="load: legacy_media done.jsonl (files already in the bucket)")
    ap.add_argument("--media-prefix", default="legacy/", help="load: bucket key prefix of those files")
    ap.add_argument("--texts", type=Path, help="prefill: post_texts.json from `texts`")
    ap.add_argument("--photo-merges", type=Path, help="JSON [[key_a, key_b], ...] from photo matching")
    ap.add_argument("--out", type=Path, help="build: output dir; load: the dir with registry.json")
    a = ap.parse_args(argv)
    if a.cmd == "texts":  # local, next to the raw exports: --out DIR (registry.json in, post_texts.json out)
        (a.out / "post_texts.json").write_text(json.dumps(post_texts(a.out / "registry.json", a.groups, a.channel),
                                                          ensure_ascii=False))
        return
    if a.cmd == "prefill":
        from app.db import SessionLocal
        texts = json.loads(a.texts.read_text()) if a.texts else None
        with SessionLocal() as db:
            print(json.dumps(prefill(db, texts), ensure_ascii=False, indent=1))
        return
    if a.out is None:
        ap.error("--out is required for build / load")
    if a.cmd == "load":
        from app.db import SessionLocal
        with SessionLocal() as db:
            print(json.dumps(load(a.out / "registry.json", db, a.media, a.media_prefix), ensure_ascii=False, indent=1))
        return
    print(json.dumps(build(a.groups, a.channel, a.sheets, a.out, a.photo_merges), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
