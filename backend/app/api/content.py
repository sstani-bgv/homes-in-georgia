"""Ticker, news, settings, sources, addresses, catalog moderation, media files, public ICS."""
import json
import mimetypes
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app import keys, storage
from app.api.deps import current_user, paginate, require_level
from app.config import REPO_ROOT, settings as env
from app.db import get_db
from app.models import Address, ChannelPost, Job, Lead, Listing, News, Property, Setting, Source, TickerMessage, User, at_least
from app.services import ics, settings
from app.services import properties as P
from app.services.common import get_or_404, jload, log_event, now_text
from app.services.errors import DomainError
from app.services.properties import listing_out, media_root

router = APIRouter(tags=["content"])
public = APIRouter(tags=["public"])


# ---------------------------------------------------------------- ticker / news / addresses

class TickerIn(BaseModel):
    text: str


def ticker_out(t: TickerMessage) -> dict:
    return {"id": t.id, "text": t.text, "status": t.status, "created_at": t.created_at.isoformat()}


@router.get("/ticker")
def ticker_list(all: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):  # noqa: A002
    stmt = select(TickerMessage)
    if not all:
        stmt = stmt.where(TickerMessage.status == "active")
    return [ticker_out(t) for t in db.scalars(stmt.order_by(TickerMessage.created_at.desc()))]


@router.post("/ticker")
def ticker_add(body: TickerIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    t = TickerMessage(text=body.text.strip(), status="active")
    db.add(t)
    db.commit()
    return ticker_out(t)


@router.delete("/ticker/{tid}")
def ticker_delete(tid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    t = get_or_404(db, TickerMessage, tid, "ticker message")
    t.status = "deleted"
    db.commit()
    return ticker_out(t)


@router.get("/news")
def news_list(limit: int = Query(30, ge=0, le=500), db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    rows = db.scalars(select(News).order_by(News.date.desc().nullslast(), News.id.desc()).limit(limit))
    return [{"id": n.id, "date": n.date.isoformat() if n.date else None, "text": n.text, "message_id": n.message_id}
            for n in rows]


def channel_post_out(p: ChannelPost) -> dict:
    return {"id": p.id, "message_id": p.message_id, "source": "topic" if "t.me/c/" in (p.link or "") else "channel",
            "date": p.date.isoformat() if p.date else None, "agent_name": p.agent_name, "agent_id": p.agent_id,
            "district": p.district, "price": float(p.price) if p.price is not None else None, "rooms": p.rooms,
            "building": p.building, "is_exclusive": p.is_exclusive, "link": p.link, "status": p.status, "raw": p.raw}


@router.get("/channel-posts")
def channel_posts(hashtag: str | None = None, district: str | None = None, status: str | None = None, q: str | None = None,
                  agent_id: int | None = None, source: str | None = None, rooms: str | None = None,
                  exclusive: bool | None = None, price_min: float | None = None, price_max: float | None = None,
                  limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Agency listings parsed from the channel / work-group Listings topic (services/inbound.py). Filters: hashtag
    (#Vake or Vake, matched in the post text), district, status (active|rented|archived, comma list), q (text, agent,
    district), agent_id, source (channel|topic), rooms (2Bed|Studio), exclusive, price range."""
    stmt = select(ChannelPost)
    if hashtag:
        for tag in [t for t in hashtag.replace(",", " ").split() if t.strip("#")]:
            stmt = stmt.where(ChannelPost.raw.ilike(f"%#{tag.lstrip('#')}%"))
    if district:
        stmt = stmt.where(ChannelPost.district.ilike(district.strip()))
    if status:
        stmt = stmt.where(ChannelPost.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ChannelPost.raw.ilike(like), ChannelPost.agent_name.ilike(like), ChannelPost.district.ilike(like)))
    if agent_id:
        stmt = stmt.where(ChannelPost.agent_id == agent_id)
    if source == "topic":
        stmt = stmt.where(ChannelPost.link.like("%t.me/c/%"))
    elif source == "channel":
        stmt = stmt.where(or_(ChannelPost.link.is_(None), ChannelPost.link.not_like("%t.me/c/%")))
    if rooms:
        stmt = stmt.where(ChannelPost.rooms == rooms)
    if exclusive is not None:
        stmt = stmt.where(ChannelPost.is_exclusive.is_(exclusive))
    if price_min is not None:
        stmt = stmt.where(ChannelPost.price >= price_min)
    if price_max is not None:
        stmt = stmt.where(ChannelPost.price <= price_max)
    stmt = stmt.order_by(ChannelPost.date.desc().nullslast(), ChannelPost.id.desc())
    return paginate(db, stmt, channel_post_out, limit, offset)


@router.get("/addresses")
def addresses_list(active: int = 1, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    stmt = select(Address)
    if active:
        stmt = stmt.where(Address.is_active.is_(True))
    return [{"id": a.id, "district": a.district, "address": a.address, "ad_link": a.ad_link, "is_active": a.is_active}
            for a in db.scalars(stmt.order_by(Address.district, Address.address))]


# ---------------------------------------------------------------- integration keys (superadmin, app/keys.py)

class KeyIn(BaseModel):
    value: str


def _key_name(name: str) -> str:
    name = name.strip().upper()
    if not keys.valid(name):
        raise DomainError("bad_input", "Имя ключа: латиница A-Z, цифры и _, например THREADS_TOKEN (системные ключи меняются только в .env)")
    return name


def _key_changes(db: Session) -> dict[str, tuple]:
    """NAME -> (when, who) of the latest «key_set» / «key_removed» event."""
    from app.models import Event
    out: dict[str, tuple] = {}
    for ev in db.scalars(select(Event).where(Event.entity == "setting", Event.action.in_(("key_set", "key_removed")))
                         .order_by(Event.at.desc()).limit(500)):
        out.setdefault((ev.data or {}).get("name"), (ev.at, ev.actor_label))
    return out


# «Права» (services/perms.py): the sections and the roles' standard rights for the checkboxes in a person's card;
# a person's own rights are PATCH /users/{id} «perms», their history GET /users/{id}/perms-history
@router.get("/admin/permissions")
def permissions_get(user: User = Depends(require_level("superadmin"))):
    from app.services import perms
    return perms.info()


@router.get("/keys")
def keys_list(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    """Known + added keys: where the value comes from (crm / env / none), settings in full, secrets masked, last change."""
    crm = keys.read(db)
    changes = _key_changes(db)
    out = []
    for name in dict.fromkeys([*keys.KNOWN, *sorted(crm)]):
        v = crm.get(name) or os.environ.get(name)
        src = "crm" if name in crm else ("env" if v else None)
        title, note = keys.KNOWN.get(name, (None, None))
        at, by = changes.get(name, (None, None))
        sec = keys.secret(name)
        out.append({"name": name, "title": title, "note": note, "source": src, "secret": sec,
                    "value": (keys.mask(v) if sec else v) if v else None,
                    "changed_at": at.isoformat() if at else None, "changed_by": by})
    return {"items": out}


@router.get("/keys/{name}/value")
def keys_reveal(name: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    """The full value on an admin's «Показать», logged with who and when."""
    name = _key_name(name)
    keys.refresh()
    v = keys.read(db).get(name) or os.environ.get(name)
    if not v:
        raise DomainError("not_found", "Ключ не задан")
    log_event(db, "setting", 0, "key_revealed", user, name=name)
    db.commit()
    return {"name": name, "value": v}


@router.put("/keys/{name}")
def keys_put(name: str, body: KeyIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    name, value = _key_name(name), body.value.strip()
    if not value or len(value) > 4000 or "\n" in value:
        raise DomainError("bad_input", "Пустое или слишком длинное значение")
    settings.set(db, keys.PREFIX + name, value, commit=False)
    log_event(db, "setting", 0, "key_set", user, name=name)  # the name only, never the value
    db.commit()
    keys.refresh(force=True)
    return keys_list(db, user)


@router.delete("/keys/{name}")
def keys_delete(name: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    name = _key_name(name)
    db.execute(delete(Setting).where(Setting.key == keys.PREFIX + name))
    log_event(db, "setting", 0, "key_removed", user, name=name)
    db.commit()
    keys.refresh(force=True)
    return keys_list(db, user)


def _scrub(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, keys.mask(s))
    return text[:200]


def _check_ai() -> list[dict]:
    """A 1-token chat call with the current key, URL and model: proves all three work."""
    import httpx
    key, base = os.environ.get("OPENAI_API_KEY") or "", (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "gpt-4.1-mini"
    res = {"label": model, "ok": False, "ms": None, "error": None}
    if not key:
        return [res | {"error": "Ключ не задан"}]
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=30,
                       json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5})
        res["ms"] = round((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            res["ok"] = True
        else:
            try:
                msg = (r.json().get("error") or {}).get("message") or r.text
            except Exception:  # noqa: BLE001
                msg = r.text
            res["error"] = _scrub(f"HTTP {r.status_code}: {msg}", key)
    except Exception as e:  # noqa: BLE001
        res["error"] = _scrub(type(e).__name__ + ": " + str(e), key)
    return [res]


def _check_apify(value: str) -> list[dict]:
    import httpx
    out = []
    for tok in dict.fromkeys(t.strip() for t in value.replace(";", ",").split(",") if t.strip()):
        res = {"label": keys.mask(tok), "ok": False, "ms": None, "error": None}
        t0 = time.monotonic()
        try:
            r = httpx.get("https://api.apify.com/v2/users/me", headers={"Authorization": f"Bearer {tok}"}, timeout=15)
            res["ms"] = round((time.monotonic() - t0) * 1000)
            res["ok"] = r.status_code == 200
            if not res["ok"]:
                res["error"] = "Ключ не работает" if r.status_code in (401, 403) else f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            res["error"] = _scrub(type(e).__name__, tok)
        out.append(res)
    return out


@router.post("/keys/{name}/check")
def keys_check(name: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    """«Проверить»: a cheap live call with the key. AI: key + URL + model together; Apify: each token of the list."""
    name = _key_name(name)
    keys.refresh(force=True)
    if name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL"):
        return {"results": _check_ai()}
    if name in ("APIFY_TOKENS", "APIFY_TOKEN"):
        return {"results": _check_apify(os.environ.get(name) or "")}
    raise DomainError("bad_input", "Этот ключ проверить нельзя")


# ---------------------------------------------------------------- spend: Apify + AI (Управление → Ключи, admin)

# $ per 1M tokens [prompt, completion] for bare OpenAI names; OpenRouter models are priced from its /models list.
# Only tokens without a cost reported by the provider are priced (calls before 16.11 and non-OpenRouter providers).
LLM_PRICES = {"gpt-4.1-mini": (0.4, 1.6), "gpt-4.1": (2.0, 8.0), "gpt-4.1-nano": (0.1, 0.4), "gpt-4o-mini": (0.15, 0.6),
              "gpt-4o": (2.5, 10.0), "gpt-5-mini": (0.25, 2.0), "gpt-5": (1.25, 10.0)}
_CACHE: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (time.monotonic(), val)
    return val


def _openrouter_prices() -> dict[str, tuple[float, float]]:
    """OpenRouter model id -> ($ per prompt token, $ per completion token), public list, cached for a day."""
    import httpx

    def fetch():
        try:
            r = httpx.get("https://openrouter.ai/api/v1/models", timeout=15)
            return {m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])) for m in r.json()["data"]
                    if (m.get("pricing") or {}).get("prompt") is not None}
        except Exception:  # noqa: BLE001
            return None
    prices = _cached("or_prices", 86400, fetch)
    if prices is None:
        _CACHE.pop("or_prices", None)  # retry on the next load
    return prices or {}


def _price(model: str) -> tuple[float, float] | None:
    if model in LLM_PRICES:
        return LLM_PRICES[model][0] / 1e6, LLM_PRICES[model][1] / 1e6
    return _openrouter_prices().get(model) or _openrouter_prices().get(f"openai/{model}")


def _llm_sum(days: dict) -> dict:
    """days: {day: {model: [calls, prompt, completion, cost_usd, unpriced_prompt, unpriced_completion]}} (old rows: the first 3)."""
    out = {"calls": 0, "prompt": 0, "completion": 0, "usd": 0.0, "estimated": False, "unpriced": [], "models": {}}
    for models in days.values():
        for model, row in models.items():
            calls, pin, pout, cost, upin, upout = (list(row) + [0.0, row[1], row[2]])[:6]
            if upin or upout:
                price = _price(model)
                if price:
                    cost += upin * price[0] + upout * price[1]
                    out["estimated"] = True
                elif model not in out["unpriced"]:
                    out["unpriced"].append(model)
            m = out["models"].setdefault(model, {"model": model, "calls": 0, "tokens": 0, "usd": 0.0})
            m["calls"] += calls
            m["tokens"] += pin + pout
            m["usd"] += cost
            out["calls"] += calls
            out["prompt"] += pin
            out["completion"] += pout
            out["usd"] += cost
    out["usd"] = round(out["usd"], 4)
    out["models"] = sorted(({**m, "usd": round(m["usd"], 4)} for m in out["models"].values()), key=lambda m: -m["usd"])
    return out


def _purposes(days: dict) -> dict:
    """days: {day: {purpose: {model: row}}} (llm_purpose) -> spend per purpose, the costliest first, and $ per client search
    (search_* $ / search_pick calls: every search ends with one pick)."""
    names = {p for ps in days.values() for p in ps}
    rows = []
    for p in names:
        x = _llm_sum({d: ps[p] for d, ps in days.items() if p in ps})
        rows.append({"purpose": p, "calls": x["calls"], "tokens": x["prompt"] + x["completion"], "completion": x["completion"], "usd": x["usd"]})
    rows.sort(key=lambda r: -r["usd"])
    picks = sum(r["calls"] for r in rows if r["purpose"] == "search_pick")
    search = sum(r["usd"] for r in rows if r["purpose"].startswith("search_"))
    return {"rows": rows, "searches": picks, "per_search_usd": round(search / picks, 5) if picks else None}


APIFY_SOURCES = {"dami_studio/facebook-groups-scraper": "facebook", "dacoder/threads-search-post-scraper": "threads",
                 "apify/cheerio-scraper": "ss", "apify/instagram-post-scraper": "instagram"}


def _apify_account(tok: str) -> dict:
    """One key, read-only from the Apify API: plan, usage cycle, $ per day of the cycle, limit, $ per actor (= source)."""
    import httpx
    a = {"tail": tok[-4:], "masked": keys.mask(tok), "plan": None, "created_at": None, "cycle_start": None, "cycle_end": None,
         "used_usd": None, "limit_usd": None, "days": [], "by_source": [], "exhausted": False, "exhausted_since": None, "error": None}
    api = "https://api.apify.com/v2"
    try:
        with httpx.Client(headers={"Authorization": f"Bearer {tok}"}, timeout=15) as c:
            r = c.get(f"{api}/users/me")
            if r.status_code != 200:
                a["error"] = "invalid_key" if r.status_code in (401, 403) else f"http_{r.status_code}"
                return a
            me = r.json()["data"]
            a |= {"plan": (me.get("plan") or {}).get("id"), "created_at": (me.get("createdAt") or "")[:10] or None}
            u = c.get(f"{api}/users/me/usage/monthly").raise_for_status().json()["data"]
            cyc = u.get("usageCycle") or {}
            a |= {"cycle_start": (cyc.get("startAt") or "")[:10] or None, "cycle_end": (cyc.get("endAt") or "")[:10] or None,
                  "days": [{"date": d["date"][:10], "usd": round(d.get("totalUsageCreditsUsd") or 0, 4)} for d in u.get("dailyServiceUsages") or []]}
            lim = c.get(f"{api}/users/me/limits").raise_for_status().json()["data"]
            a |= {"used_usd": round((lim.get("current") or {}).get("monthlyUsageUsd") or 0, 3),
                  "limit_usd": (lim.get("limits") or {}).get("maxMonthlyUsageUsd")}
            # runs of this cycle, newest first (ponytail: one page of 1000 runs; enough at our ~100 runs a month)
            runs = c.get(f"{api}/actor-runs", params={"desc": 1, "limit": 1000}).raise_for_status().json()["data"]["items"]
            by_act: dict[str, list] = {}
            for x in runs:
                if (x.get("startedAt") or "")[:10] >= (a["cycle_start"] or ""):
                    s = by_act.setdefault(x.get("actId") or "?", [0, 0.0])
                    s[0] += 1
                    s[1] += x.get("usageTotalUsd") or 0
            for act, (n, usd) in by_act.items():
                d = c.get(f"{api}/acts/{act}")
                name = f"{d.json()['data']['username']}/{d.json()['data']['name']}" if d.status_code == 200 else act
                a["by_source"].append({"source": APIFY_SOURCES.get(name, name), "runs": n, "usd": round(usd, 4)})
            a["by_source"].sort(key=lambda x: -x["usd"])
    except Exception:  # noqa: BLE001 - never the exception text: it may carry the request (and so the key)
        a["error"] = a["error"] or "unreachable"
        return a
    if a["limit_usd"] and a["used_usd"] >= a["limit_usd"] - 0.05:
        a["exhausted"] = True
        total = 0.0
        for d in sorted(a["days"], key=lambda d: d["date"]):
            total += d["usd"]
            if total >= a["limit_usd"] - 0.05:
                a["exhausted_since"] = d["date"]
                break
    return a


def _apify_accounts() -> list[dict]:
    """Each Apify key (masked, never the key) from the Apify API, cached 5 minutes."""
    import hashlib
    toks = [t.strip() for t in (os.environ.get("APIFY_TOKENS") or "").replace(";", ",").split(",")]
    toks = list(dict.fromkeys(t for t in [*toks, (os.environ.get("APIFY_TOKEN") or "").strip()] if t))
    out = []
    for tok in toks:
        k = "apify:" + hashlib.sha256(tok.encode()).hexdigest()
        a = _cached(k, 300, lambda: _apify_account(tok))
        if a["error"]:
            _CACHE.pop(k, None)  # retry on the next load
        out.append(a)
    return out


def ai_provider(base: str) -> str:
    base = base.lower()
    return ("OpenRouter" if "openrouter.ai" in base else "OpenAI" if "api.openai.com" in base or not base else re.sub(r"^https?://([^/]+).*$", r"\1", base))


def _ai_balance() -> dict | None:
    """OpenRouter: credits left / used (key's own limit if it has one).
    Cached 5 minutes. None when the provider shows no balance."""
    import hashlib
    import httpx
    key, base = os.environ.get("OPENAI_API_KEY") or "", os.environ.get("OPENAI_BASE_URL") or ""
    prov = ai_provider(base)
    if not key or prov != "OpenRouter":
        return None
    h = {"Authorization": f"Bearer {key}"}

    def fetch():
        try:
            out = {}
            r = httpx.get("https://openrouter.ai/api/v1/credits", headers=h, timeout=10)
            if r.status_code == 200:
                d = r.json()["data"]
                out |= {"balance_usd": round(d["total_credits"] - d["total_usage"], 4), "used_usd": round(d["total_usage"], 4)}
            r = httpx.get("https://openrouter.ai/api/v1/key", headers=h, timeout=10)
            if r.status_code == 200:
                d = r.json()["data"]
                out |= {"key_used_usd": d.get("usage"), "key_limit_usd": d.get("limit"), "key_remaining_usd": d.get("limit_remaining"),
                        "key_used_today_usd": d.get("usage_daily"), "key_used_month_usd": d.get("usage_monthly")}
            elif not out:
                out["error"] = "invalid_key" if r.status_code in (401, 403) else f"http_{r.status_code}"
            return out
        except Exception:  # noqa: BLE001
            return {"error": "unreachable"}
    return _cached("bal:" + hashlib.sha256(f"{base}|{key}".encode()).hexdigest(), 300, fetch)


@router.get("/spend")
def spend(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    """Apify: per key from the Apify API (plan, cycle, $ per day and per source, limit); today / 7 days = Apify's daily
    usage of all keys; source_runs (the parser's log) only as run counts.
    AI: calls, tokens and $ per day and model recorded by bot/llm.py (llm_usage; OpenRouter reports each call's cost),
    the same per purpose (llm_purpose, from 16.11), the provider's balance."""
    from datetime import datetime, timedelta, timezone
    keys.refresh()
    now = datetime.now(timezone.utc)
    today, month, week0 = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m"), (now - timedelta(days=6)).strftime("%Y-%m-%d")
    runs = jload(settings.get(db, "source_runs"), {})
    accs = _apify_accounts()
    usage = jload(settings.get(db, "llm_usage"), {})
    purpose = jload(settings.get(db, "llm_purpose"), {})
    base = os.environ.get("OPENAI_BASE_URL") or ""
    return {
        "apify": {"today_usd": round(sum(d["usd"] for a in accs for d in a["days"] if d["date"] == today), 4),
                  "week_usd": round(sum(d["usd"] for a in accs for d in a["days"] if d["date"] >= week0), 4),
                  "runs_week": {k: len(rr) for k, rr in runs.items() if rr},  # our own log: run attempts, not money
                  "accounts": accs},
        "ai": {"mode": (os.environ.get("LLM_PROVIDER") or "openai").lower(), "provider": ai_provider(base),
               "base_url": base or "https://api.openai.com/v1", "model": os.environ.get("LLM_MODEL") or "gpt-4.1-mini",
               "today": _llm_sum({d: m for d, m in usage.items() if d == today}),
               "week": _llm_sum({d: m for d, m in usage.items() if d >= week0}),
               "month": _llm_sum({d: m for d, m in usage.items() if d.startswith(month)}),
               "purposes": {"today": _purposes({d: p for d, p in purpose.items() if d == today}),
                            "week": _purposes({d: p for d, p in purpose.items() if d >= week0}),
                            "month": _purposes({d: p for d, p in purpose.items() if d.startswith(month)})},
               "balance": _ai_balance()},
    }


# ---------------------------------------------------------------- settings / sources

@router.get("/settings")
def settings_get(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    return settings.all(db)


@router.put("/settings")
def settings_put(body: dict, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    settings.validate(body)
    if not at_least(user, "superadmin") and any(k in settings.PROTECTED and str(v) != settings.get(db, k) for k, v in body.items()):
        raise DomainError("forbidden", "Эти настройки (интеграция с Telegram) меняет только суперадмин")
    for k, v in body.items():
        if k in settings.HIDDEN or k.startswith("key:"):  # the parser's / its own page's / Управление → Ключи
            continue
        settings.set(db, k, json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v, commit=False)
    if str(body.get("sla_enabled", "")) == "0":  # SLA off: drop the running timers and their pending reminders
        db.execute(update(Lead).where(Lead.sla_due_at.isnot(None)).values(sla_stage=None, sla_due_at=None))
        db.execute(delete(Job).where(Job.kind == "lead_sla", Job.done_at.is_(None)))
    log_event(db, "settings", "-", "updated", user, keys=sorted(body))
    db.commit()
    return settings.all(db)


class SourceIn(BaseModel):
    kind: str | None = None
    key: str | None = None
    name: str | None = None
    url: str | None = None
    lang: str | None = None
    options: dict | None = None
    enabled: bool | None = None
    position: int | None = None


def source_out(s: Source) -> dict:
    return {"id": s.id, "kind": s.kind, "key": s.key, "name": s.name, "url": s.url, "lang": s.lang,
            "options": jload(s.options, {}), "enabled": bool(s.enabled), "position": s.position}


@router.get("/sources")
def sources_list(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    return [source_out(s) for s in db.scalars(select(Source).order_by(Source.position, Source.id))]


def _apply_source(s: Source, data: dict):
    for k, v in data.items():
        if k == "options":
            v = json.dumps(v or {}, ensure_ascii=False)
        elif k == "enabled":
            v = int(bool(v))
        setattr(s, k, v)


@router.post("/sources")
def sources_create(body: SourceIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    data = body.model_dump(exclude_none=True)
    if data.get("kind") not in ("tg_agency", "tg_competitor", "fb_group", "ig_account") or not data.get("key"):
        raise DomainError("bad_source", "kind (tg_agency|tg_competitor|fb_group|ig_account) and key are required")
    if db.scalar(select(Source).where(Source.key == data["key"])):
        raise DomainError("conflict", "source key exists")
    s = Source(options="{}")
    _apply_source(s, data)
    db.add(s)
    db.commit()
    return source_out(s)


@router.patch("/sources/{sid}")
def sources_patch(sid: int, body: SourceIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    s = get_or_404(db, Source, sid, "source")
    _apply_source(s, body.model_dump(exclude_unset=True))
    db.commit()
    return source_out(s)


# How often the parser runs each source (bot/refresh.sh reads source_every_min through db.py export-sources) and what the
# paid ones cost: per run (Apify start / per query) and per hour of new posts, measured from the runs the parser
# recorded (source_runs, 7 days). The page multiplies them by the chosen frequency: $/month.
SCHEDULE = [("ss", "ss.ge (Apify)", 0.002), ("myhome", "myhome.ge", None), ("livo", "livo.ge", None), ("korter", "korter.ge", None),
            ("telegram", "Telegram-каналы", None), ("facebook", "Facebook-группы (Apify)", 0.002),
            ("threads", "Threads (Apify)", None)]
SCHEDULE_DEFAULTS = {"ss": 360, "myhome": 360, "livo": 360, "korter": 360, "telegram": 360, "facebook": 360, "threads": 480}
THREADS_PER_QUERY = 0.03


def _schedule(db: Session) -> list[dict]:
    every = {**SCHEDULE_DEFAULTS, **jload(settings.get(db, "source_every_min"), {})}
    runs = jload(settings.get(db, "source_runs"), {})
    queries = len((jload(settings.get(db, "threads_defaults"), {}) or {}).get("queries") or []) or 4
    out = []
    for key, label, start in SCHEDULE:
        paid = None
        if key in ("ss", "facebook", "threads"):
            rr = runs.get(key) or []
            spent = sum(r[1] for r in rr)
            if key == "threads":  # a flat price per query and run, new posts are free. Not the recorded spend: Apify
                # reports a pay-per-event run's usage late, the fetcher read $0 / $0.03 for runs that cost $0.12
                paid = {"per_run": round(THREADS_PER_QUERY * queries, 4), "per_hour": 0}
            else:  # a start (+ ss.ge: the list page) + each new post / detail page, which come at their own pace
                hours = sum(r[3] for r in rr if r[3])
                var = sum(max(0, r[1] - start) for r in rr if r[3])
                paid = {"per_run": start, "per_hour": round(var / hours, 5) if hours else None}
            paid |= {"spent_7d": round(spent, 3), "runs_7d": len(rr), "posts_7d": sum(r[2] for r in rr)}
        out.append({"key": key, "label": label, "every_min": int(every[key]), "paid": paid})
    return out


@router.get("/sources/schedule")
def schedule_get(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    return _schedule(db)


@router.put("/sources/schedule")
def schedule_put(body: dict[str, int], db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("admin"))):
    if any(k not in SCHEDULE_DEFAULTS or not isinstance(v, int) or not 15 <= v <= 7 * 1440 for k, v in body.items()):
        raise DomainError("bad_setting", "every_min: a known source and 15..10080 minutes")
    every = {**jload(settings.get(db, "source_every_min"), {}), **body}
    settings.set(db, "source_every_min", json.dumps(every), commit=False)
    log_event(db, "settings", "-", "updated", user, keys=["source_every_min"])
    db.commit()
    return _schedule(db)


# ---------------------------------------------------------------- operator account (userbot QR login)

def reader_url() -> str:
    """The channel reader (compose service «reader», userbot.reader()): READER_URL, else next to the userbot."""
    return (env.READER_URL or ("http://reader:8092" if env.USERBOT_URL else "")).strip().rstrip("/")


def _userbot(method: str, path: str, body: dict | None = None, url: str | None = None) -> dict:
    """The userbot process (USERBOT_URL) owns the operator's Telegram session; the CRM only asks it. The QR url is
    rendered here as SVG, the 2FA password passes through and is never stored. url: the reader's instead."""
    import httpx
    import segno
    url = (env.USERBOT_URL if url is None else url or "").strip().rstrip("/")
    if not url:
        return {"configured": False}
    try:
        r = httpx.request(method, f"{url}{path}", json=body, headers={"X-Userbot-Token": env.SECRET_KEY}, timeout=35)
        st = r.json()
    except Exception:  # noqa: BLE001 — the service is down or restarting
        return {"configured": True, "reachable": False}
    if r.status_code != 200:
        return {"configured": True, "reachable": False}
    login = st.get("login") or {}
    if login.get("url"):
        login["qr_svg"] = segno.make(login["url"], error="m").svg_inline(scale=5, border=2)
    return {"configured": True, "reachable": True, **st}


@router.get("/userbot")
def userbot_status(user: User = Depends(require_level("admin"))):
    return _userbot("GET", "/status")


class UserbotPassword(BaseModel):
    password: str


@router.post("/userbot/login")
def userbot_login(user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/login", {})


@router.post("/userbot/password")
def userbot_password(body: UserbotPassword, user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/password", {"password": body.password})


class TelegramPhone(BaseModel):
    phone: str


class TelegramCode(BaseModel):
    code: str


@router.post("/userbot/phone")
def userbot_phone(body: TelegramPhone, user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/phone", {"phone": body.phone})


@router.post("/userbot/code")
def userbot_code(body: TelegramCode, user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/code", {"code": body.code})


@router.post("/reader/phone")
def reader_phone(body: TelegramPhone, user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/phone", {"phone": body.phone}, url=reader_url())


@router.post("/reader/code")
def reader_code(body: TelegramCode, user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/code", {"code": body.code}, url=reader_url())


@router.post("/userbot/logout")
def userbot_logout(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("superadmin"))):
    out = _userbot("POST", "/logout", {})
    log_event(db, "settings", "-", "userbot_logout", user)
    db.commit()
    return out


@router.get("/reader")
def reader_status(user: User = Depends(require_level("admin"))):
    return _userbot("GET", "/status", url=reader_url())


@router.post("/reader/login")
def reader_login(user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/login", {}, url=reader_url())


@router.post("/reader/password")
def reader_password(body: UserbotPassword, user: User = Depends(require_level("superadmin"))):
    return _userbot("POST", "/password", {"password": body.password}, url=reader_url())


@router.post("/reader/logout")
def reader_logout(db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("superadmin"))):
    out = _userbot("POST", "/logout", {}, url=reader_url())
    log_event(db, "settings", "-", "reader_logout", user)
    db.commit()
    return out


# ---------------------------------------------------------------- moderation (bot "Проверка")

class ModerationIn(BaseModel):
    approve: bool


@router.get("/moderation")
def moderation_list(limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"),
                    user: User = Depends(require_level("operator"))):
    stmt = select(Listing).where(Listing.status == "pending_review").order_by(Listing.first_seen.desc())
    return paginate(db, stmt, lambda x: listing_out(x, full=True), limit, offset)


@router.post("/moderation/{listing_id}")
def moderation_decide(listing_id: str, body: ModerationIn, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(require_level("operator"))):
    x = get_or_404(db, Listing, listing_id, "listing")
    x.decision = "approved" if body.approve else "rejected"
    x.status = "published" if body.approve else "rejected"
    x.status_reason = None if body.approve else "отклонено агентом"
    x.decided_by, x.decided_at = user.id, now_text()
    x.updated_at = now_text()
    log_event(db, "listing", x.id, x.decision, user)
    db.commit()
    return listing_out(x, full=True)


# ---------------------------------------------------------------- files

def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise DomainError("not_found", "file not found")
    return p


@router.get("/media/{path:path}")
def media_file(path: str, preview: bool = Query(False, alias="p"), user: User = Depends(current_user),
               db: Session = Depends(get_db, scope="function")):
    """p=1: the ~640px preview (storage.preview_key), the original while there is none.
    private/properties/<id>/…: the owner's passport / registry extract — staff and the object's agent only; other
    private/ files (contracts, NDAs, extracts, sign drafts) have their own endpoints, never this one."""
    parts = path.split("/")
    # the access decision reads the path as given: nothing that resolves elsewhere (x/../private/…, ./, //, \)
    if path.startswith("/") or "\\" in path or any(s in ("", ".", "..") for s in parts):
        raise DomainError("not_found", "file not found")
    base = parts[1:] if parts[0] == "previews" else parts  # a preview is as private as its original (storage previews)
    private = bool(base) and base[0] + "/" == P.PRIVATE_PREFIX
    if private:
        prop = db.get(Property, int(base[2])) if len(base) > 3 and base[1] == "properties" and base[2].isdigit() else None
        if not prop or not P.can_see_owner(prop, user):
            raise DomainError("not_found", "file not found")
        preview = False
    elif not at_least(user, "operator") and (m := re.match(r"(?:previews/)?properties/(\d+)/", path)):
        # an object's photos / video: for those who may open its card. A photo's ~640 px preview is for every agent
        # (Stanislav 11-17: «Похожие объекты» compare a new ad with colleagues' objects by photo); the original, the
        # video and the card itself stay closed (audit 11-17)
        photo_preview = (preview or parts[0] == "previews") and base[-1].lower().endswith(storage.PREVIEW_OF)
        prop = db.get(Property, int(m[1]))
        if prop is None or not ((photo_preview and at_least(user, "recruit")) or P.can_open(db, prop, user)):
            raise DomainError("not_found", "file not found")
        if storage.is_s3() and parts[0] != "previews" and not P.can_open(db, prop, user):
            path, preview = storage.preview_key(path), False  # the bucket's preview, never the original
    if storage.is_s3():
        if preview and (f := storage.cached(storage.preview_key(path))):  # from the server's disk, cached by the browser
            return FileResponse(f, media_type="image/webp", headers=PREVIEW_HEADERS)
        return RedirectResponse(storage.url(path, presign=True) if private else storage.url(path), status_code=307)
    src = _safe(media_root(), path)
    if not src.is_file():
        raise DomainError("not_found", "file not found")
    p = (storage.local_preview(src, path) if preview and not path.startswith("previews/") else None) or src
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    safe = mt.startswith(("image/", "video/")) and mt != "image/svg+xml"
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
    if not safe:
        mt, headers["Content-Disposition"] = "application/octet-stream", "attachment"
    return FileResponse(p, media_type=mt, headers=headers)


PHOTO_ROOTS = [REPO_ROOT / "bot", REPO_ROOT, Path(env.MEDIA_DIR)]
# a preview never changes under its key (a new file is a new key): the browser keeps it a month
PREVIEW_HEADERS = {"Cache-Control": "private, max-age=2592000, immutable", "X-Content-Type-Options": "nosniff"}


@public.get("/config")
def public_config():
    keys.refresh()  # a MapTiler key entered in Управление → Ключи
    return {"bot_username": env.LOGIN_BOT_USERNAME or env.BOT_USERNAME, "public_url": env.PUBLIC_URL, "bot_id": (env.LOGIN_BOT_TOKEN or env.BOT_TOKEN).split(":")[0], "maptiler_key": env.MAPTILER_KEY}


@public.get("/listing-photos/{listing_id}/{n}")
def listing_photo(listing_id: str, n: int, preview: bool = Query(False, alias="p"), db: Session = Depends(get_db, scope="function")):
    """Photos of catalog listings (paths written by the bot's ingest, relative to bot/). Public like the channel.
    p=1: the ~640px preview, made on first request (remote http photos have none)."""
    x = get_or_404(db, Listing, listing_id, "listing")
    photos = jload(x.photos, []) or []
    if not 0 <= n < len(photos):
        raise DomainError("not_found", "photo not found")
    ref = str(photos[n])
    if ref.startswith("s3:"):  # a CRM object's catalog row (services/catalog.py): the object's own file in the storage
        key = ref[3:]
        if storage.is_s3():
            if preview and (f := storage.cached(storage.preview_key(key))):
                return FileResponse(f, media_type="image/webp", headers=PREVIEW_HEADERS)
            return RedirectResponse(storage.url(key), status_code=307)
        f = storage.local_path(key)
        if not f.is_file():
            raise DomainError("not_found", "photo file missing")
        f = (storage.local_preview(f, key) if preview else None) or f
        return FileResponse(f, media_type=mimetypes.guess_type(f.name)[0] or "image/jpeg")
    if ref.startswith(("http://", "https://")):
        return RedirectResponse(ref)
    for root in PHOTO_ROOTS:
        try:
            p = Path(ref) if os.path.isabs(ref) else _safe(root, ref)
        except DomainError:
            continue
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"):
            if preview:  # key as in `storage previews`: the path under the repo (bot/raw/img/<file>)
                rel = p.relative_to(REPO_ROOT.resolve()).as_posix() if p.is_relative_to(REPO_ROOT.resolve()) else f"abs/{p.name}"
                p = storage.local_preview(p, rel) or p
            return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "image/jpeg",
                                headers={"Cache-Control": "public, max-age=86400"})
    raise DomainError("not_found", "photo file missing")


@public.get("/public/ics/{token}")
def public_ics(token: str, db: Session = Depends(get_db, scope="function")):
    lead = get_or_404(db, Lead, ics.lead_from_token(token), "lead")
    return Response(ics.build(db, lead), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="viewing-{lead.id}.ics"'})
