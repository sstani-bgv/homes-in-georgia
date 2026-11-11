"""Address -> point for the CRM address field (Stanislav 11-11): OSM Nominatim (policy: a real User-Agent, ≤1 req/s,
cache), Georgia only, Tbilisi preferred; a pasted Google Maps link or «lat, lng» is the point itself, no lookup."""
import re
import threading
import time
from functools import lru_cache
from urllib.parse import unquote, urlparse

import httpx

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "homes-in-georgia-crm/1.0 (real-estate CRM address check)"
TBILISI = "44.60,41.85,45.05,41.60"  # viewbox lon1,lat1,lon2,lat2 (as bot/geo.py)
_lock = threading.Lock()
_last = [0.0]

# @lat,lng (a place/view URL) · !3dlat!4dlng (the pin) · q=/query=/ll=lat,lng · a bare «41.71, 44.78»
_PIN = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_AT = re.compile(r"(?:@|[?&](?:q|query|ll|center|destination)=|^)\s*(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})")
_SHORT = ("maps.app.goo.gl", "goo.gl")
# Nominatim's English district names -> the CRM's district list (frontend DISTRICTS)
_DISTRICT = {"bagebi": "Ваке-Багеби", "vake": "Ваке", "saburtalo": "Сабуртало", "mtatsminda": "Мтацминда", "vere": "Вере",
             "ortachala": "Ортачала", "didube": "Дидубе", "chughureti": "Чугурети", "isani": "Исани", "samgori": "Самгори",
             "gldani": "Глдани", "nadzaladevi": "Надзаладеви", "krtsanisi": "Крцаниси", "dighomi": "Дигоми",
             "digomi": "Дигоми", "old tbilisi": "Старый город", "sololaki": "Старый город", "abanotubani": "Старый город"}


def _in_georgia(lat: float, lng: float) -> bool:  # rough box: a swapped «lng, lat» or a point abroad is not ours
    return 41.0 <= lat <= 43.7 and 40.0 <= lng <= 46.8


def point_of(q: str) -> tuple[float, float] | None:
    """A Google Maps link (the pin !3d!4d wins over the view @) or «lat, lng»; maps.app.goo.gl short links are resolved."""
    s = q.strip()
    host = urlparse(s).hostname or ""
    if host in _SHORT:
        try:
            s = str(httpx.get(s, follow_redirects=True, timeout=10, headers={"User-Agent": UA}).url)
        except httpx.HTTPError:
            return None
    s = unquote(unquote(s))  # consent.google.com?continue=<encoded maps url>
    for rx in (_PIN, _AT):
        if m := rx.search(s):
            lat, lng = float(m[1]), float(m[2])
            if _in_georgia(lat, lng):
                return lat, lng
    return None


def _district(addr: dict) -> str | None:
    for k in ("suburb", "city_district", "quarter", "neighbourhood"):
        v = (addr.get(k) or "").lower()
        for stem, ru in _DISTRICT.items():
            if stem in v:
                return ru
    return None


def _get(url: str, params: dict):
    with _lock:
        time.sleep(max(0.0, 1.1 - (time.time() - _last[0])))  # Nominatim policy: max 1 req/s
        _last[0] = time.time()
        r = httpx.get(url, params={**params, "format": "jsonv2", "addressdetails": 1, "accept-language": "en"},
                      headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
        return r.json()


def _hit(h: dict) -> dict:
    return {"label": h["display_name"].replace(", Georgia", ""), "lat": float(h["lat"]), "lng": float(h["lon"]),
            "district": _district(h.get("address") or {})}


@lru_cache(maxsize=2000)  # ponytail: per-process cache, enough for a handful of agents; a table if it grows
def _nominatim(q: str) -> tuple:
    return tuple(_hit(h) for h in _get(NOMINATIM, {"q": q, "limit": 5, "countrycodes": "ge", "viewbox": TBILISI, "bounded": 0}))


@lru_cache(maxsize=2000)
def _reverse(lat: float, lng: float) -> dict | None:
    h = _get(NOMINATIM.replace("/search", "/reverse"), {"lat": lat, "lon": lng, "zoom": 18})
    return _hit(h) if "lat" in h else None


def search(q: str) -> dict:
    """{"items": [{label, lat, lng, district}], "point": bool}: up to 5 candidates, or the pasted point itself."""
    q = re.sub(r"\s+", " ", q or "").strip()
    if len(q) < 3:
        return {"items": [], "point": False}
    if at := point_of(q):  # the pasted point stays exact; OSM only names the address there
        try:
            near = _reverse(*at) or {}
        except (httpx.HTTPError, ValueError, KeyError):
            near = {}
        return {"items": [{"label": near.get("label") or f"{at[0]:.6f}, {at[1]:.6f}", "lat": at[0], "lng": at[1],
                           "district": near.get("district")}], "point": True}
    if q.startswith("http"):
        return {"items": [], "point": False}  # a link without a point: nothing to ask OSM
    try:
        return {"items": [dict(h) for h in _nominatim(q)], "point": False}
    except (httpx.HTTPError, ValueError, KeyError):  # errors aren't cached (lru_cache skips raised calls)
        return {"items": [], "point": False, "error": True}


def point_facts(lat: float, lng: float) -> dict:
    """What a point fills in the card (Stanislav 11-11): the CRM district (OSM's suburb / city district there) and the
    nearest metro at any distance up to METRO_RADIUS_M (a sanity cap: outside Tbilisi none), with its distance."""
    from app.services.properties import METRO_RADIUS_M, nearest_metro_m
    try:
        district = (_reverse(round(lat, 5), round(lng, 5)) or {}).get("district")  # ~1 m: a dragged marker reuses the cache
    except (httpx.HTTPError, ValueError, KeyError):
        district = None
    m, tag = nearest_metro_m(lat, lng)
    near = m <= METRO_RADIUS_M
    return {"district": district, "metro": tag if near else None, "metro_m": round(m) if near else None}
