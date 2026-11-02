"""Geocoding via OpenStreetMap Nominatim (free, 1 req/s), bounded to Tbilisi, cached on disk."""
import json
import math
import os
import re
import threading
import time

import httpx

CACHE_PATH = "raw/geo_cache.json"
_cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
_lock = threading.Lock()
_last = [0.0]
TBILISI = "44.60,41.85,45.05,41.60"  # viewbox: lon1,lat1,lon2,lat2


def clean(q):
    q = re.sub(r"\bქ\.", "ქუჩა", q or "")
    q = re.sub(r"\bგამზ\.", "გამზირი", q)
    return re.sub(r"\s+", " ", q).strip(" ,")


def geocode(q):
    """'Vake Park' / 'ფალიაშვილის ქ. 39' -> (lat, lng) or None."""
    q = clean(q)
    if not q:
        return None
    with _lock:
        if q in _cache:
            return tuple(_cache[q]) if _cache[q] else None
        time.sleep(max(0, 1.1 - (time.time() - _last[0])))  # Nominatim policy: max 1 req/s
        _last[0] = time.time()
        try:
            r = httpx.get("https://nominatim.openstreetmap.org/search",
                          params={"q": q, "format": "json", "limit": 1, "viewbox": TBILISI, "bounded": 1},
                          headers={"User-Agent": "tbilisi-rent-ai-demo/0.1"}, timeout=20)
            hits = r.json()
        except (httpx.HTTPError, ValueError):
            return None  # not cached: retry next time
        _cache[q] = [float(hits[0]["lat"]), float(hits[0]["lon"])] if hits else None
        json.dump(_cache, open(CACHE_PATH, "w"), ensure_ascii=False)
        return tuple(_cache[q]) if _cache[q] else None


def first_hit(queries):
    """Most precise query first (street+number), then broader (street, district)."""
    for n, q in enumerate(queries):
        p = geocode(q)
        if p:
            return p, ("точный" if n == 0 else "примерный")
    return None, None


def km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


if __name__ == "__main__":
    assert abs(km((41.7151, 44.8271), (41.6938, 44.8015)) - 3.2) < 0.3  # Rustaveli -> Liberty Sq, ~3.2 km
    print(geocode("Vake Park"), clean("ფალიაშვილის ქ. 39"))
