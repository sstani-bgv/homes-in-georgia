"""Public Registry (NAPR) applications by the owner's personal ID (or a cadastral code): the owner's sign page and the
CRM contract generator.

naprweb.reestri.gov.ge (the form my.gov.ge service 176 embeds) searches applications without a CAPTCHA; opening one
(its documents, the extract ამონაწერი) asks for reCAPTCHA. So we only list them: the owner taps one, NAPR's own page
opens on his phone, he ticks the CAPTCHA himself, downloads the extract and uploads it back (properties.sign_contract).
The search has no CORS headers, so the browser can't call it: this proxy, scoped to a signing token, cached."""
import io
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from app.services.common import CADASTRAL_RE, clean_cadastral
from app.services.errors import DomainError

log = logging.getLogger(__name__)
SEARCH_URL = "https://naprweb.reestri.gov.ge/api/search"
VIEW_URL = "https://naprweb.reestri.gov.ge/_dea/#/view/{}"
SEARCH_PAGE_URL = "https://naprweb.reestri.gov.ge/_dea/#/search"
CACHE_TTL = 3600
PER_TOKEN_HOUR = 30  # lookups per signing link per hour (a cache hit doesn't count)
_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}
_hits: dict[str, list[float]] = {}


def _item(a: dict) -> dict:
    ts = str(a.get("appRegDate") or "")
    return {"app_id": str(a.get("appID") or ""), "reg_number": a.get("regNumber") or "",
            "date": datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts.isdigit() else None,
            "type": a.get("webTransact") or "", "address": a.get("address") or "", "status": a.get("status") or "",
            "done": str(a.get("statusId")) == "10", "applicants": [str(x) for x in (a.get("applicants") or [])][:4],
            "url": VIEW_URL.format(a.get("appID"))}


def search(page: int, key: str, cadastral: str | None = None, personal_id: str | None = None,
           limit: int = PER_TOKEN_HOUR) -> dict:
    """By the personal ID (11 digits; 9 for a company) or the cadastral code -> {total, page, lastpage, items,
    search_url}; DomainError registry_unavailable when NAPR doesn't answer. key: whose lookups the rate limit counts."""
    pid = re.sub(r"\D", "", personal_id or "")
    if personal_id is not None:
        if len(pid) not in (9, 11):
            raise DomainError("bad_personal_id", "Личный номер: 11 цифр (код компании: 9)", 422)
        field, value = "person", pid
    else:
        value = clean_cadastral(cadastral)
        if not value:
            raise DomainError("bad_cadastral", "Введите кадастровый код", 422)
        field = "cadcode"
    page = max(1, min(int(page or 1), 50))
    now = time.time()
    if (hit := _cache.get((field, value, page))) and now - hit[0] < CACHE_TTL:
        return hit[1]
    recent = [t for t in _hits.get(key, []) if now - t < 3600]
    if len(recent) >= limit:
        raise DomainError("rate_limited", "Слишком много запросов, попробуйте позже", 429)
    _hits[key] = recent + [now]
    body = {"page": page, "search": "", "regno": "", "datefrom": None, "dateto": None, "person": "", "address": "",
            "cadcode": "", field: value}
    try:
        r = httpx.post(SEARCH_URL, json=body, timeout=httpx.Timeout(12, connect=6),
                       headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("napr search by %s: %s", field, e)  # not the value: a personal ID stays out of the logs
        raise DomainError("registry_unavailable", "Реестр сейчас не отвечает", 502)
    out = {"total": int(d.get("total") or 0), "page": int(d.get("page") or page), "lastpage": int(d.get("lastpage") or 1),
           "items": [_item(a) for a in d.get("applist") or []], "search_url": SEARCH_PAGE_URL}
    if len(_cache) > 2000:  # ponytail: crude bound for the in-process cache; one api process, so enough
        _cache.clear()
    _cache[(field, value, page)] = (now, out)
    return out


# «01.10.13.015.029.01.123», optionally a letter and «/2»; the extract also names the land plot's shorter code
_CODE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,6}){3,8}[^\W\d_]?(?:/\d{1,4}[^\W\d_]?)?(?![\d])")


def extract_code(pdf: bytes) -> str | None:
    """The cadastral code from the registry extract's text: the longest valid one (the flat's, not the plot's).
    None for a scan / an unreadable PDF (the owner's page then lets him type it)."""
    try:
        from pypdf import PdfReader
        text = " ".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(pdf)).pages[:6])
    except Exception as e:  # pypdf raises many kinds on broken files
        log.info("extract text: %s", e)
        return None
    codes = [c.rstrip(".") for c in _CODE.findall(text)]
    codes = [c for c in codes if CADASTRAL_RE.match(c)]
    return max(codes, key=len) if codes else None
