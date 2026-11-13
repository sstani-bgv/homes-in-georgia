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
    text = text_of_pdf(pdf)
    return code_from_text(text) if text else None


# Old NAPR extracts (EMS QuickPdf) set Georgian in a legacy «Latin-mapped» font: the text layer reads «ÌÉÓÀÌÀÒÈÉ» for
# «მისამართი». The 8-bit codes À..ä map onto the Georgian alphabet in order.
_LEGACY = dict(zip("ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãä", "აბგდევზჱთიკლმნჲოპჟრსტჳუფქღყშჩცძწჭხჴჯჰ"))


def georgian(text: str) -> str:
    """Legacy-encoded Georgian (see _LEGACY) → Unicode; text that is already Unicode is returned as is."""
    legacy = sum(1 for ch in text if ch in _LEGACY)
    real = sum(1 for ch in text if "\u10d0" <= ch <= "\u10ff")
    return text.translate(str.maketrans(_LEGACY)) if legacy > 20 and legacy > real else text


def text_of_pdf(pdf: bytes) -> str | None:
    try:
        from pypdf import PdfReader
        return georgian(" \n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(pdf)).pages[:6]))
    except Exception as e:  # pypdf raises many kinds on broken files
        log.info("extract text: %s", e)
        return None


_NUM = r"(\d+(?:[.,]\d+)?)"
_SQM = r"\s*(?:კვ\.?\s*მ|მ2|მ²|m2|m²|кв\.?\s*м)"


def _area(text: str, code: str | None) -> str | None:
    """The flat's area: the last «… ფართ… N კვ.მ.» line that isn't the land plot's (the extract describes the plot
    first, the flat after it). None for a land plot's code (≤ 5 parts, e.g. 66.56.06.005): its area isn't a flat's."""
    if not code or len(code.split(".")) < 6:
        return None
    best = None
    for line in text.splitlines():
        m = re.search(_NUM + _SQM, line, re.I)
        if not m or "ფართ" not in line:
            continue
        if re.search(r"ნაკვეთ|მიწ", line):  # the land plot
            continue
        best = m.group(1).replace(",", ".")
    if best and best.endswith(".00"):
        best = best[:-3]
    return best


def _street(addr: str) -> str:
    """«ქალაქი თბილისი, ვაკის რაიონი, ჭავჭავაძის გამზირი, N 5, სართული 4, ბინა N 12» → «ჭავჭავაძის გამზირი, N 5»:
    the contract prints the city, the floor and the flat on their own («ქ. თბილისი, [[street_house]], სარ. …»)."""
    parts = [p.strip(" .") for p in addr.split(",")]
    keep = [p for p in parts if p and not re.search(r"თბილისი|რაიონი|^სართ|ბინა|^ბ\.|სადარბაზო|^კორპ", p)]
    return re.sub(r"\s+", " ", ", ".join(keep)).strip()


def parse_extract(text: str) -> dict:
    """Everything a registry extract (or the NAPR application page, pasted) says that the contract wizard has a field for:
    {cadastral_code, street_house, floor, apartment_number, apartment_area, owner_full_name, owner_personal_number,
    address} — a key only when found."""
    text = georgian(text or "")
    out: dict = {}
    if code := code_from_text(text):
        out["cadastral_code"] = code
    m = re.search(r"მისამართი\s*:?\s*(.+)", text)
    if m:
        addr = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")[:300]
        out["address"] = addr
        if street := _street(addr):
            out["street_house"] = street
        if f := re.search(r"სართ(?:ული)?\.?\s*N?\s*(-?\d+)", addr):
            out["floor"] = f.group(1)
        if b := re.search(r"ბინა\s*N?\s*([\w/-]+)", addr):
            out["apartment_number"] = b.group(1)
    if "floor" not in out and (f := re.search(r"სართ(?:ული)?\.?\s*N?\s*(-?\d+)", text)):
        out["floor"] = f.group(1)
    if "apartment_number" not in out and (b := re.search(r"ბინა\s*N\s*([\w/-]+)", text)):
        out["apartment_number"] = b.group(1)
    if area := _area(text, out.get("cadastral_code")):
        out["apartment_area"] = area
    # «მესაკუთრეები: NAME ,P/N: 01001012345» — the first owner
    o = re.search(r"მესაკუთრ\w*\s*:?\s*\n?\s*([^\n,]{3,80}?)\s*,\s*P/N\s*:?\s*(\d{9,11})", text)
    if o:
        out["owner_full_name"] = re.sub(r"\s+", " ", o.group(1)).strip()
        out["owner_personal_number"] = o.group(2)
    return out


def code_from_text(text: str) -> str | None:
    """The cadastral code in any registry text — the extract's, or the NAPR application page the agent copied after its
    CAPTCHA and pasted into the CRM: the longest valid code (the flat's, not the building's or the land plot's)."""
    codes = [c.rstrip(".") for c in _CODE.findall(text or "")]
    codes = [c for c in codes if CADASTRAL_RE.match(c)]
    return max(codes, key=len) if codes else None


# ---------------------------------------------------------------- our own records by the owner's personal ID

def known(db, personal_id: str) -> list[dict]:
    """The owner's objects the CRM already knows by his personal ID — no registry, no CAPTCHA: earlier contracts (the
    wizard's fields), the owner's electronic signatures, his exclusive confirmations. Newest first, one per cadastral code.
    Each: {cadastral, address, floor, apartment_number, area, owner_name, source, date}."""
    from sqlalchemy import func, select

    from app.models import Contract, ContractSigner, Event, Property

    pid = re.sub(r"\D", "", personal_id or "")
    if len(pid) not in (9, 11):
        raise DomainError("bad_personal_id", "Личный номер: 11 цифр (код компании: 9)", 422)
    digits = lambda col: func.regexp_replace(col, r"\D", "", "g")  # noqa: E731
    rows: list[dict] = []

    def add(code, when, source, **f):
        code = clean_cadastral(code or "") or (code or "").strip()
        if code:
            rows.append({"cadastral": code, "date": when.isoformat() if when else None, "source": source,
                         **{k: (str(v).strip() if v not in (None, "") else None) for k, v in f.items()}})

    owner_pid = Contract.data["owner_personal_number"].astext
    for c in db.scalars(select(Contract).where(digits(owner_pid) == pid).order_by(Contract.id.desc()).limit(20)):
        d = c.data or {}
        add(d.get("cadastral_code") or c.cadastral, c.created_at, f"договор #{c.id}", address=d.get("street_house") or c.address,
            floor=d.get("floor"), apartment_number=d.get("apartment_number"), area=d.get("apartment_area"),
            owner_name=d.get("owner_full_name") or c.owner_name)
    q = (select(ContractSigner, Contract).join(Contract, Contract.id == ContractSigner.contract_id)
         .where(ContractSigner.role == "owner", digits(ContractSigner.personal_id) == pid).order_by(ContractSigner.id.desc()).limit(20))
    for s, c in db.execute(q):
        d = c.data or {}
        add(d.get("cadastral_code") or c.cadastral, s.signed_at, f"подпись договора #{c.id}", address=d.get("street_house") or c.address,
            floor=d.get("floor"), apartment_number=d.get("apartment_number"), area=d.get("apartment_area"), owner_name=s.full_name)
    evs = db.scalars(select(Event).where(Event.entity == "property", Event.action == "exclusive_contract_signed",
                                         digits(Event.data["personal_id"].astext) == pid).order_by(Event.id.desc()).limit(20))
    for ev in evs:
        p = db.get(Property, int(ev.entity_id)) if str(ev.entity_id).isdigit() else None
        add((ev.data or {}).get("cadastral_code") or (p and p.cadastral_code), ev.at, f"эксклюзив, объект #{ev.entity_id}",
            address=p and p.address, floor=p and p.floor, apartment_number=None,
            area=p and p.area and f"{p.area:g}", owner_name=(ev.data or {}).get("full_name"))
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    by: dict[str, dict] = {}
    for r in rows:  # newest first; an older record of the same object fills what the newer one lacks
        if (have := by.get(r["cadastral"])) is None:
            by[r["cadastral"]] = r
        else:
            for k, v in r.items():
                if have.get(k) is None and v is not None:
                    have[k] = v
    return list(by.values())
