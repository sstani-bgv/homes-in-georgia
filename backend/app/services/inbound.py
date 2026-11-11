"""Inbound Telegram updates from the agency work group / channel (port of the Apps Script pollUpdates handlers:
handleNewsMessage, handleListingsTopic, handleChannelPost, handleChannelDeal, handlePhotoQueueMessage, handleDealTopicMessage,
handleForwardedDeal, handleTelegramMessage).

Everything here is pure domain logic over a parsed message dict (see `from_telegram`), so the bot (bot/inbound.py), tests
and a future webhook can feed it the same way. `process(db, msg)` routes one message to every matching handler and returns
{"handled": [...], "replies": [{chat_id, text, thread_id, reply_to}]} - the replies are sent by the caller.

Where things land:
  agency channel post / work-group TOPIC_LISTINGS message → channel_posts (Active_Listings / Listings sheets)
  #Rented/#Сдано/#Sold in those                          → referenced channel_posts.status = rented
  TOPIC_NEWS message                                     → news
  TOPIC_PHOTO_QUEUE template                             → owners (dedup by phone/address) + reports kind=makefoto
  #Rented in TOPIC_DEAL(+EXTRA) / deals channel / forward → deals (source=telegram, status=pending, tg_ref idempotent)
Chats ids / topics come from env (app.config): WORK_GROUP_CHAT_ID, TOPIC_*, AGENCY_CHANNEL(_ID), DEALS_CHANNEL_ID,
NEWS_CHAT_ID. Messages from any other chat are ignored."""
import html
import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ChannelPost, Deal, News, Owner, PropertyPublication, Report, User
from app.services import settings as settings_svc
from app.services import users as users_svc
from app.services.common import TZ, jload, log_event, now
from app import districts
from app.services.dedup import address_match, norm_address
from app.telegram.client import conf

log = logging.getLogger(__name__)

# the channel's district tags (app/districts.py) + older post tags
DISTRICTS = [d[0] for d in districts.DISTRICTS] + ["Marjanishvili", "Navtlughi", "Rustaveli", "Didi Dighomi", "Old Tbilisi"]
DEAL_TAGS = ("#Rented", "#Sold", "#Sale")
RENTED_TAGS = ("#Rented", "#Сдано", "#сдано", "#Sold")
# hashtags of a listing post that are never an agent
NON_AGENT_TAGS = {d.replace(" ", "").lower() for d in DISTRICTS} | {
    "studio", "newbuilding", "oldbuilding", "exclusive", "rented", "sold", "sale", "rent", "сдано", "tbilisi", "georgia"}


# ---------------------------------------------------------------- config

def _c(key: str) -> str:
    return conf(key, "").strip()


def _ids(*keys: str) -> set[str]:
    out = set()
    for k in keys:
        out |= {x.strip() for x in _c(k).split(",") if x.strip()}
    return out


def work_group() -> str:
    return _c("WORK_GROUP_CHAT_ID")


def news_chat() -> str:
    return _c("NEWS_CHAT_ID") or work_group()


def channel_username() -> str:
    return _c("AGENCY_CHANNEL").lstrip("@").lower()


def is_agency_channel(msg: dict) -> bool:
    cid = _c("AGENCY_CHANNEL_ID")
    if cid and str(msg.get("chat_id")) == cid:
        return True
    return bool(channel_username()) and (msg.get("chat_username") or "").lower() == channel_username()


def is_deals_channel(msg: dict) -> bool:
    cid = _c("DEALS_CHANNEL_ID")
    if cid:
        return str(msg.get("chat_id")) == cid
    return msg.get("chat_type") == "channel" and is_agency_channel(msg)


def in_work_group(msg: dict) -> bool:
    return bool(work_group()) and str(msg.get("chat_id")) == work_group()


def topic_is(msg: dict, *keys: str) -> bool:
    t = msg.get("topic_id")
    return t is not None and str(t) in _ids(*keys)


def relevant(msg: dict) -> bool:
    """Cheap pre-filter (no DB): is this message from a chat we listen to?"""
    if msg.get("chat_type") == "private":
        return bool(msg.get("forward")) and has_deal_tag(text_of(msg))
    cid = str(msg.get("chat_id"))
    return cid in {work_group(), news_chat()} - {""} or is_agency_channel(msg) or is_deals_channel(msg)


# ---------------------------------------------------------------- message dict

def from_telegram(m: dict, edited: bool = False) -> dict:
    """Bot API Message JSON (python-telegram-bot `Message.to_dict()`) → the dict every function here takes."""
    chat = m.get("chat") or {}
    frm = m.get("from") or {}
    fo = m.get("forward_origin") or {}
    fwd = None
    if fo or m.get("forward_date") or m.get("forward_from_chat") or m.get("forward_from"):
        fchat = fo.get("chat") or fo.get("sender_chat") or m.get("forward_from_chat") or {}
        fwd = {"chat_id": fchat.get("id"), "chat_username": fchat.get("username"),
               "message_id": fo.get("message_id") or m.get("forward_from_message_id"),
               "date": fo.get("date") or m.get("forward_date")}
    return {"chat_id": chat.get("id"), "chat_type": chat.get("type"), "chat_username": chat.get("username"),
            "topic_id": m.get("message_thread_id"), "message_id": m.get("message_id"), "date": m.get("date"),
            "text": m.get("text"), "caption": m.get("caption"), "edited": edited,
            "from": {"id": frm.get("id"), "username": frm.get("username"), "first_name": frm.get("first_name")} if frm else None,
            "forward": fwd}


def text_of(msg: dict) -> str:
    return msg.get("text") or msg.get("caption") or ""


def msg_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)) and v > 0:
        return datetime.fromtimestamp(v, timezone.utc)
    return now()


def has_deal_tag(text: str) -> bool:
    return any(t in (text or "") for t in DEAL_TAGS)


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- agents (AGENT_TAGS / matchAgentName)

def agent_aliases(db: Session) -> dict[str, str]:
    """Setting `agent_tags` (JSON {"#Tag": "User name"}, admin-editable via PUT /settings) - the old AGENT_TAGS map.
    Not hardcoded: the names are agency data, kept in the DB. Since stage 6.5 the tag lives in users.agent_tag
    (resolve_agent looks there first); the setting is the fallback for tags not in any profile."""
    raw = jload(settings_svc.get(db, "agent_tags"), {}) or {}
    return {str(k).lstrip("#").lower(): str(v) for k, v in raw.items() if v} if isinstance(raw, dict) else {}


def match_user(db: Session, name: str) -> User | None:
    """matchAgentName: exact name/username (case-insensitive), else a unique prefix match of ≥4 chars."""
    n = (name or "").strip().lstrip("#@").lower()
    if not n:
        return None
    users = list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.id)))
    for u in users:
        if (u.name or "").strip().lower() == n or (u.username or "").lower() == n:
            return u
    if len(n) >= 4:
        hits = [u for u in users if (u.name or "").strip().lower().startswith(n)
                or (len((u.name or "").strip()) >= 4 and n.startswith((u.name or "").strip().lower()))]
        if len(hits) == 1:
            return hits[0]
    return None


def resolve_agent(db: Session, tag: str, aliases: dict | None = None) -> tuple[str, User | None]:
    raw = (tag or "").lstrip("#").replace("'", "").strip()
    if not raw:
        return "", None
    tagged = users_svc.tag_owner(db, raw) if users_svc.TAG_RE.fullmatch(raw) else None
    if tagged is not None:
        return tagged.name, tagged
    aliases = agent_aliases(db) if aliases is None else aliases
    name = aliases.get(raw.lower(), raw)
    return name, match_user(db, name) or (match_user(db, raw) if name != raw else None)


# ---------------------------------------------------------------- listings (handleChannelPost / handleListingsTopic)

def parse_listing(text: str, mode: str = "channel") -> dict | None:
    """Agency post format → {agent_tag, district, price, rooms, building, is_exclusive}. mode: channel (post in the
    agency channel: ≥100 chars and a +995 phone line required) or topic (work-group Listings topic: ≥50 chars)."""
    text = text or ""
    if mode == "channel" and (len(text) < 100 or "+995" not in text):
        return None
    if mode == "topic" and len(text) < 50:
        return None
    before = text.split("[Check all listings]")[0] or text
    # the last hashtag of the first +995 line: «+995 … #Zaza» (old posts) and «+995 … #David · Agent #Zaza» (ours)
    m = (re.search(r"\+995[^\n]*#([A-Za-zА-Яа-я']+)", before) if mode == "channel"
         else re.search(r"[+]995[^\r\n]*#([A-Za-zА-Яа-я]+)", before))
    tag = m[1].replace("'", "") if m else ""
    candidates = [] if tag else [t for t in reversed(re.findall(r"#([A-Z][a-z]+)", before)) if t.lower() not in NON_AGENT_TAGS]
    tags = set(re.findall(r"#(\w+)", text))  # whole tags: #Digomi is not #DigomiMassive
    district = next((d for d in DISTRICTS if d.replace(" ", "") in tags), "")
    if mode == "channel":
        pm = re.search(r"(\d{3,4})\$\s*\+", text) or re.search(r"💰\s*(\d{3,4})\$", text) or re.search(r"(\d{3,4})\$", text)
    else:
        pm = re.search(r"(\d{3,4})\$\s*\+", text) or re.search(r"\$\s*(\d{3,4})", text) or re.search(r"(\d{3,4})\$", text)
    rm = re.search(r"#(\d)Bed", text)
    rooms = f"{rm[1]}Bed" if rm else "Studio" if "#Studio" in text else ""
    building = "New" if "#NewBuilding" in text else "Old" if "#OldBuilding" in text else ""
    return {"agent_tag": tag, "agent_candidates": candidates, "district": district, "price": int(pm[1]) if pm else 0,
            "rooms": rooms, "building": building, "is_exclusive": "#Exclusive" in text,
            "rented": any(t in text for t in RENTED_TAGS)}


def _post_key(msg: dict, source: str) -> str:
    return str(msg["message_id"]) if source == "channel" else f"wg:{msg['message_id']}"


def _post_link(msg: dict, source: str) -> str:
    if source == "channel":
        return f"https://t.me/{msg.get('chat_username') or channel_username()}/{msg['message_id']}"
    return f"https://t.me/c/{str(msg['chat_id']).replace('-100', '', 1)}/{msg['message_id']}"


def mark_rented_refs(db: Session, text: str) -> list[str]:
    """#Rented/#Сдано message that links listing posts (t.me/<channel>/<id>, t.me/c/<chat>/<id>) → those posts rented."""
    keys = []
    ch = channel_username()
    for m in re.finditer(r"t\.me/(c/)?([\w\-]+)/(\d+)", text or ""):
        if m[1]:
            keys.append(f"wg:{m[3]}")
        elif ch and m[2].lower() == ch:
            keys.append(m[3])
    done = []
    for k in dict.fromkeys(keys):
        p = db.scalar(select(ChannelPost).where(ChannelPost.message_id == k))
        if p is not None and p.status != "rented":
            p.status = "rented"
            done.append(k)
    return done


def handle_listing(db: Session, msg: dict, source: str) -> dict | None:
    """source: channel (Active_Listings) | topic (Listings). Idempotent by message id; an edit re-parses the row."""
    text = text_of(msg)
    rented_refs = mark_rented_refs(db, text) if any(t in text for t in RENTED_TAGS) else []
    parsed = parse_listing(text, source)
    key = _post_key(msg, source)
    existing = db.scalar(select(ChannelPost).where(ChannelPost.message_id == key))
    if parsed is None or (existing is None and has_deal_tag(text)):  # a #Rented deal post is not a listing
        if existing is not None and any(t in text for t in RENTED_TAGS):  # an edit that turned the post into "#Rented"
            existing.status = "rented"
        db.commit()
        return {"rented": rented_refs} if rented_refs else None
    aliases = agent_aliases(db)
    name, user = resolve_agent(db, parsed["agent_tag"], aliases) if parsed["agent_tag"] else ("", None)
    if not name:  # topic mode: the last capitalised hashtag that is a known agent (AGENT_TAGS lookup)
        for t in parsed["agent_candidates"]:
            n, u = resolve_agent(db, t, aliases)
            if u is not None or t.lower() in aliases:
                name, user = n, u
                break
    if not name:
        db.commit()
        return {"rented": rented_refs} if rented_refs else None
    if existing is not None and not msg.get("edited"):
        db.commit()
        return {"post": existing.id, "duplicate": True, "rented": rented_refs}
    p = existing or ChannelPost(message_id=key, date=msg_dt(msg.get("date")), link=_post_link(msg, source))
    p.agent_name, p.agent_id = name, user.id if user else None
    p.district, p.price, p.rooms, p.building = parsed["district"], parsed["price"] or None, parsed["rooms"], parsed["building"]
    p.is_exclusive, p.raw = parsed["is_exclusive"], text[:2000]
    if parsed["rented"]:
        p.status = "rented"
    elif existing is None:
        p.status = "active"
    if p.property_id is None:  # our own publication of a CRM object coming back as a channel post
        p.property_id = db.scalar(select(PropertyPublication.property_id).where(
            PropertyPublication.channel == "tg", PropertyPublication.url == p.link).limit(1))
    db.add(p)
    db.commit()
    return {"post": p.id, "rented": rented_refs}


# ---------------------------------------------------------------- news (handleNewsMessage)

def handle_news(db: Session, msg: dict) -> dict | None:
    text = text_of(msg)
    if not text:
        return None
    mid = str(msg["message_id"])
    n = db.scalar(select(News).where(News.message_id == mid))
    if n is not None:
        if msg.get("edited"):
            n.text = text
            db.commit()
        return {"news": n.id, "duplicate": not msg.get("edited")}
    n = News(date=msg_dt(msg.get("date")), text=text, message_id=mid)
    db.add(n)
    db.commit()
    return {"news": n.id}


# ---------------------------------------------------------------- photo queue (handlePhotoQueueMessage)

PHONE_TPL = r"(\+?995[ ]?[0-9 ]{9,}|0[0-9 ]{9,})"


def parse_template(text: str) -> dict:
    """Make-Foto template: Address / Agent / Owner (+phone) / Date / Link lines (apps-script parseTemplate)."""
    t = (text or "").replace("\r", "")
    g = lambda pat: (re.search(pat, t, re.I) or [None, None])[1]  # noqa: E731
    addr, agent, owner, dt, link = (g(r"Address[ :]+([^\n]+)"), g(r"Agent[ :]+([^\n]+)"), g(r"Owner[ :]+([^\n]+)"),
                                    g(r"Date[^:\n]*[ :]+([^\n]+)"), g(r"Link[ :]+([^ \n]+)"))
    pm = re.search(PHONE_TPL, t)
    owner_name = re.sub(PHONE_TPL, "", (owner or "").strip(), count=1).replace("__", "").strip()
    return {"addr": (addr or "").replace("__", "").strip(), "agent": (agent or "").replace("#", "").strip(),
            "owner": owner_name, "phone": pm[1].replace(" ", "").strip() if pm else "", "date": (dt or "").strip(),
            "link": (link or "").replace("__", "").strip()}


def parse_shoot_date(raw: str, ref: datetime) -> tuple[date | None, str | None]:
    """"27.05 14:00" | "27.05.2026" | "2026-05-27 9.30" → (date, "HH:MM") (apps-script getCabinetData mqDate/mqTime)."""
    s, hm = (raw or "").strip(), None
    m = re.search(r"\s+(\d{1,2}):(\d{2})", s) or re.search(r"\s+(\d{1,2})\.(\d{2})\s*$", s)
    if m and 0 <= int(m[1]) <= 23:
        hm = f"{int(m[1]):02d}:{m[2]}"
        s = s.replace(m[0], "").strip()
    year = ref.astimezone(TZ).year
    try:
        if re.fullmatch(r"\d{1,2}\.\d{1,2}", s):
            d, mo = s.split(".")
            return date(year, int(mo), int(d)), hm
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", s):
            d, mo, y = s.split(".")
            return date(int(y), int(mo), int(d)), hm
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return date.fromisoformat(s), hm
    except ValueError:
        pass
    return None, hm


def add_to_owner_base(db: Session, parsed: dict) -> tuple[Owner | None, str | None]:
    """addToOwnerBase: owner by phone (normP) or, without a phone, by address → (owner, 'added' | 'exists')."""
    if not parsed["addr"] and not parsed["phone"]:
        return None, None
    from app.services import properties
    if parsed["phone"]:
        known = properties.find_owner(db, parsed["phone"])
        if known is not None:
            return known, "exists"
    elif len(norm_address(parsed["addr"])) > 2:
        for o in db.scalars(select(Owner).where(Owner.address.isnot(None))):
            if address_match(parsed["addr"], o.address):
                return o, "exists"
    o, _ = properties.upsert_owner(db, parsed["owner"] or None, parsed["phone"] or None, parsed["addr"] or None,
                                   parsed["link"] or None)
    o.notes = o.notes or "Telegram: Make Foto"
    return o, "added"


def handle_photo_queue(db: Session, msg: dict) -> dict | None:
    """Template → owner in the base + a `makefoto` report (the same record funnel 1 writes at C3 «shoot scheduled»), so
    the CRM Make-Foto list and the agent's cabinet show it. No property is created: a property belongs to an agent who
    took it through C1-C3 (lock, call, owner check); a chat template carries none of that."""
    text = text_of(msg)
    if not text:
        return None
    parsed = parse_template(text)
    if not parsed["addr"] and not parsed["phone"]:
        return None
    ref = f"{msg['chat_id']}:{msg['message_id']}"
    if db.scalar(select(Report.id).where(Report.kind == "makefoto", Report.data["tg_ref"].astext == ref)):
        return {"duplicate": True}
    owner, status = add_to_owner_base(db, parsed)
    agent_name, agent = resolve_agent(db, parsed["agent"]) if parsed["agent"] else ("", None)
    if status == "added" and agent is not None:  # stage 6.5: a new owner belongs to the agent who brought him
        owner.agent_id = agent.id
    when = msg_dt(msg.get("date"))
    d, hm = parse_shoot_date(parsed["date"], when)
    day = d or when.astimezone(TZ).date()
    data = {"address": parsed["addr"], "phone": parsed["phone"], "owner": parsed["owner"], "agent": agent_name or None,
            "date": day.strftime("%d.%m.%Y") + (f" {hm}" if hm else ""), "date_raw": parsed["date"], "link": parsed["link"],
            "status": status, "owner_id": owner.id if owner else None, "source": "telegram", "tg_ref": ref,
            "raw": text[:500]}
    db.flush()
    from app.services import reports
    r = reports.save(db, "makefoto", data, agent, post=False)  # commits; no echo into the topic it came from
    line = {"added": "✅ Добавлено в базу", "exists": "⚠️ Этот номер уже есть в базе!"}.get(status)
    return {"report": r.id, "owner": owner.id if owner else None, "status": status,
            "reply": line}


# ---------------------------------------------------------------- deals (parseDealMessage + handlers)

PHONE_DEAL = re.compile(r"(\+?995[ ]?[0-9 ]{9,}|\+?[0-9][0-9 \-]{8,})")


def _rent_range(s: str) -> tuple[date | None, date | None]:
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\s*[\-–—]\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", s or "")
    if not m:
        return None, None
    try:
        return date(int(m[3]), int(m[2]), int(m[1])), date(int(m[6]), int(m[5]), int(m[4]))
    except ValueError:
        return None, None


def parse_deal_message(text: str) -> dict | None:
    """#Rented/#Sold/#Sale post → fields (apps-script parseDealMessage; also reads the Russian lines of the platform's own
    #Rented text: «👤 #Agent», «Цена», «Комиссия», «Платформа», «Rent: from – to», like handleTelegramMessage did)."""
    if not has_deal_tag(text):
        return None
    t = text
    g = lambda pat, fl=re.I: (re.search(pat, t, fl) or [None, None])[1]  # noqa: E731
    lm = re.search(r"Link[^\n]*?(https?://[^\s]+)", t, re.I) or re.search(r"(https?://t\.me/[^\s]+)", t, re.I)
    price = _num(g(r"Price[ :]+([0-9.]+)\$") or g(r"Цена[:\s]+([\d.]+)\s*\$"))
    comm = _num(g(r"^Commission[ :]+([0-9.]+)\$", re.I | re.M) or g(r"Комиссия[:\s]+([\d.]+)\s*\$"))
    plat = _num(g(r"Platforms? Commission[ :]+([0-9.]+)\$") or g(r"Платформа[^:\n]*[:\s]+([\d.]+)\s*\$"))
    am = re.search(r"#(\w+)[ ]+Commission[ :]+([0-9.]+)\$", t, re.I)
    agent, agent_comm = (am[1], _num(am[2])) if am else ("", 0.0)
    if not agent_comm:
        agent_comm = _num(g(r"Agents?[ ]+commission[ :]+([0-9.,]+)\$"))
    if not agent:
        agent = g(r"👤\s*#?([A-Za-zА-Яа-яёЁ]+)") or ""
    if not agent:
        pm = re.search(r"\+995[^\n#]*#([A-Za-zА-Яа-яёЁ']+)", t)
        agent = pm[1].replace("'", "") if pm else ""
    recruit = g(r"Recruit[:\s]+#?(\w+)") or g(r"Рекрут[:\s]+#(\w+)") or ""
    cad = (g(r"Cadastral(?: number)?[ :]*\n?(?:<code>)?([0-9.]+)") or "").strip(".")
    client_s, owner_s = (g(r"Clients?[ :]+([^\n]+)") or "").strip(), (g(r"Owner[ :]+([^\n]+)") or "").strip()

    def split(s):
        pm = PHONE_DEAL.search(s)
        return PHONE_DEAL.sub("", s, count=1).strip(), (pm[0].strip() if pm else "")
    client_name, client_phone = split(client_s)
    owner_name, owner_phone = split(owner_s)
    date_str = (g(r"Date of Rent[ :]+([^\n]+)") or g(r"^Rent[ :]+([^\n]+)", re.I | re.M) or "").strip()[:30]
    rent_from, rent_to = _rent_range(date_str)
    lv = re.search(r"([0-9]+)/([0-9]+)\(([0-9]+)/([0-9]+)\)", t)
    lv2 = re.search(r"Agent (\d{2})%", t)
    dn = re.search(r"Deal[ ]+([0-9]+)(?![ ]*/)", t) or re.search(r"Сделка #(\d+)", t)
    is_recruit = "Recruiting" in t
    return {"agent": agent or (recruit if is_recruit else ""), "recruit": recruit, "agent_comm": agent_comm,
            "link": lm[1].strip() if lm else "", "price": price, "commission": comm, "platform": plat, "cadastral": cad,
            "client_name": client_name, "client_phone": client_phone, "owner_name": owner_name, "owner_phone": owner_phone,
            "date_str": date_str, "rent_from": rent_from, "rent_to": rent_to,
            "level": int(lv[1]) if lv else int(lv2[1]) if lv2 else None,
            "deal_num": int(lv[3]) if lv else int(dn[1]) if dn else 0, "deal_plan": int(lv[4]) if lv else 10,
            "is_exclusive": "#Exclusive" in t or "#Эксклюзив" in t, "is_recruit": is_recruit,
            "is_sold": "#Sold" in t or "#Sale" in t, "raw": t[:800]}


def deal_ref(msg: dict) -> str:
    """Idempotency key: the original post for forwards (so a forward of an already ingested post is a no-op)."""
    f = msg.get("forward")
    if msg.get("chat_type") == "private" and f and f.get("chat_id") and f.get("message_id"):
        return f"{f['chat_id']}:{f['message_id']}"
    return f"{msg['chat_id']}:{msg['message_id']}"


def save_deal(db: Session, msg: dict, source: str, actor: User | None = None) -> dict | None:
    """#Rented message → deals row status=pending (source=telegram) for an operator to confirm (B6). Exclusive counters,
    levels, T14 and contract jobs happen on confirm, like for CRM deals. Idempotent by tg_ref; also skips a message
    for an ad link that already has a live deal from the last 30 days (the same deal entered twice)."""
    from app.services import deals
    d = parse_deal_message(text_of(msg))
    if d is None or not (d["price"] or d["commission"]):  # a bare "#Rented <link>" only marks the listing (handle_listing)
        return None
    ref = deal_ref(msg)
    old = db.scalar(select(Deal).where(Deal.tg_ref == ref))
    if old is not None:
        return {"deal": old.id, "duplicate": True}
    if d["link"]:
        same = db.scalar(select(Deal).where(Deal.ad_link == d["link"], Deal.status != "cancelled",
                                            Deal.created_at >= now() - timedelta(days=30)).order_by(Deal.id).limit(1))
        if same is not None:
            return {"deal": same.id, "duplicate": True, "by": "link"}
    agent_name, agent = resolve_agent(db, d["agent"]) if d["agent"] else ("", None)
    data = {"agent_id": agent.id if agent else None, "deal_type": "sale" if d["is_sold"] else "rent",
            "is_exclusive": d["is_exclusive"], "price": d["price"] or None,
            "commission_total": d["commission"] or None, "platform_amount": d["platform"] or None,
            "agent_level": d["level"], "cadastral": d["cadastral"] or None, "ad_link": d["link"] or None,
            "client_name": d["client_name"] or None, "client_phone": d["client_phone"] or None,
            "owner_name": d["owner_name"] or None, "owner_phone": d["owner_phone"] or None,
            "rent_from": d["rent_from"], "rent_to": d["rent_to"], "recruit": d["recruit"] or None, "source": "telegram",
            "tg_source": source, "tg_ref": ref, "tg_agent": d["agent"], "tg_agent_name": agent_name,
            "tg_agent_comm": d["agent_comm"] or None, "tg_deal_num": d["deal_num"] or None, "tg_date": d["date_str"],
            "tg_text": d["raw"]}
    if data["platform_amount"] is None and data["commission_total"] and d["agent_comm"]:
        data["platform_amount"] = max(0.0, d["commission"] - d["agent_comm"])  # agent's share given, platform = rest
    if data["rent_from"] is None:
        data["month"] = f"{msg_dt(msg.get('date')).astimezone(TZ).month}/{msg_dt(msg.get('date')).astimezone(TZ).year}"
    try:
        deal = deals.create(db, data, None, commit=False)
        deal.tg_ref = ref
        db.flush()
    except IntegrityError:  # the same message processed concurrently: the other one won
        db.rollback()
        old = db.scalar(select(Deal).where(Deal.tg_ref == ref))
        return {"deal": old.id if old else None, "duplicate": True}
    log_event(db, "deal", deal.id, "telegram_parsed", actor, label="telegram", source=source, ref=ref)
    db.commit()
    return {"deal": deal.id, "agent": agent_name, "agent_id": deal.agent_id, "price": float(deal.price or 0)}


# ---------------------------------------------------------------- work-group housekeeping (handleTelegramMessage)

TOPIC_KEYS = {"фото": "TOPIC_PHOTO", "photo": "TOPIC_PHOTO", "показ": "TOPIC_SHOWING", "showing": "TOPIC_SHOWING",
              "расклейка": "TOPIC_STICKERS", "stickers": "TOPIC_STICKERS", "сдача": "TOPIC_DEAL", "rental": "TOPIC_DEAL",
              "deal": "TOPIC_DEAL", "t14": "TOPIC_T14", "news": "TOPIC_NEWS", "новости": "TOPIC_NEWS",
              "listings": "TOPIC_LISTINGS", "daily": "TOPIC_DAILY", "general": "TOPIC_GENERAL"}


def link_tg_user(db: Session, msg: dict) -> int | None:
    """TG_Users sheet replacement: a work-group member whose @username matches a staff user without tg_id gets linked
    (then Mini App login works for them). An existing tg_id is never overwritten."""
    f = msg.get("from") or {}
    if not f.get("id") or not f.get("username"):
        return None
    if db.scalar(select(User.id).where(User.tg_id == int(f["id"]))):
        return None
    u = db.scalar(select(User).where(func.lower(User.username) == f["username"].lower().lstrip("@"), User.tg_id.is_(None)))
    if u is None:
        return None
    u.tg_id = int(f["id"])
    log_event(db, "user", u.id, "tg_linked", None, label="telegram", tg_id=u.tg_id)
    db.commit()
    return u.id


def command_reply(msg: dict) -> str | None:
    """/setup <topic> → the ids to put into .env (topics live in env now, not in a Settings sheet); /status → current."""
    text = text_of(msg).strip()
    if text.startswith("/setup"):
        parts = text.split()
        key = TOPIC_KEYS.get(parts[1].lower()) if len(parts) > 1 else None
        if not key:
            return "ℹ️ /setup фото|показ|расклейка|сдача|t14|news|listings|daily|general"
        return (f"ℹ️ Эта тема: <code>{msg['chat_id']}|{msg.get('topic_id') or ''}</code>\n"
                f"Пропишите в .env: <code>WORK_GROUP_CHAT_ID={msg['chat_id']}</code> и "
                f"<code>{key}={msg.get('topic_id') or ''}</code>, затем перезапустите bot/api/worker.")
    if text.split("@")[0] == "/status":
        rows = [("📷 Фото", "TOPIC_PHOTO"), ("🏠 Показ", "TOPIC_SHOWING"), ("📌 Расклейка", "TOPIC_STICKERS"),
                ("💰 Сдача", "TOPIC_DEAL"), ("🔍 T14", "TOPIC_T14"), ("📰 Новости", "TOPIC_NEWS"),
                ("🏘 Listings", "TOPIC_LISTINGS"), ("📸 Make Foto", "TOPIC_PHOTO_QUEUE")]
        return "📋 <b>Настройки тем:</b>\n\n" + "\n".join(f"{label}: {_c(k) or '❌'}" for label, k in rows)
    return None


# ---------------------------------------------------------------- router

def process(db: Session, msg: dict) -> dict:
    """Run every matching handler (as pollUpdates did); one failing handler doesn't stop the others."""
    res = {"handled": [], "replies": []}
    if not relevant(msg):
        return res
    text = text_of(msg)
    reply_base = {"chat_id": msg["chat_id"], "thread_id": msg.get("topic_id"), "reply_to": msg.get("message_id")}

    def run(name, fn, *a):
        try:
            out = fn(db, *a)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("inbound %s failed for %s:%s", name, msg.get("chat_id"), msg.get("message_id"))
            return None
        if out:
            res["handled"].append({"kind": name, **{k: v for k, v in out.items() if k != "reply"}})
            if out.get("reply"):
                res["replies"].append({**reply_base, "text": out["reply"]})
        return out

    private = msg.get("chat_type") == "private"
    work = in_work_group(msg)
    if not private and str(msg.get("chat_id")) == news_chat() and topic_is(msg, "TOPIC_NEWS"):
        run("news", handle_news, msg)
    if work and topic_is(msg, "TOPIC_LISTINGS"):
        run("listing", handle_listing, msg, "topic")
    if work and topic_is(msg, "TOPIC_PHOTO_QUEUE"):
        run("makefoto", handle_photo_queue, msg)
    if work and topic_is(msg, "TOPIC_DEAL", "TOPIC_DEAL_EXTRA") and has_deal_tag(text) and not msg.get("edited"):
        run("deal", save_deal, msg, "topic")
    if not private and is_agency_channel(msg):
        run("listing", handle_listing, msg, "channel")
    if not private and is_deals_channel(msg) and has_deal_tag(text) and not msg.get("edited"):
        run("deal", save_deal, msg, "channel")
    if private and msg.get("forward") and has_deal_tag(text) and not msg.get("edited"):
        f = msg.get("from") or {}
        sender = db.scalar(select(User).where(User.tg_id == int(f["id"]), User.is_active.is_(True))) if f.get("id") else None
        if sender is None:  # only staff / agents may file deals by forwarding
            return res
        out = run("deal", save_deal, msg, "forward", sender)
        if out:
            res["replies"].append({"chat_id": msg["chat_id"], "thread_id": None, "reply_to": msg.get("message_id"),
                                   "text": ("⚠️ Уже есть в базе" if out.get("duplicate") else
                                            f"🔑 Сохранена! Сделка ждёт подтверждения оператора.\nАгент: {html.escape(out.get('agent') or '—')}"
                                            f"\nPrice: ${out.get('price', 0):g}")})
    if work:
        run("tg_user", lambda s, m: {"user": uid} if (uid := link_tg_user(s, m)) else None, msg)
        if text.startswith("/"):
            if r := command_reply(msg):
                res["replies"].append({**reply_base, "text": r})
    return res
