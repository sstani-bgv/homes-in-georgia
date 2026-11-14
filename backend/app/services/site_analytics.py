"""«Аналитика» (superadmins only): the report of hge.ge's own visitor counter.

The site sends anonymous events to a Google Apps Script (apps-script/site-analytics.gs in PlatformDGE/-rentintbilisi-crm),
which keeps them in the «HGE Analytics» sheet and builds the report. The CRM asks that script for the report with
REPORT_KEY (SITE_ANALYTICS_KEY here). The key stays on the server: the browser only talks to /api/analytics/site.
"""
import threading
import time

import httpx

from app.config import settings
from app.services.errors import DomainError

PERIODS = (1, 7, 30, 90)
CACHE_SEC = 120          # the sheet is read in full on every report, so repeated clicks are served from memory
TIMEOUT_SEC = 40         # Apps Script reads the whole sheet; a cold start takes a while

_cache: dict[int, tuple[float, dict]] = {}
_lock = threading.Lock()


def _fetch(days: int) -> dict:
    try:
        # Apps Script answers with a redirect to script.googleusercontent.com.
        r = httpx.get(settings.SITE_ANALYTICS_URL, params={"action": "report", "key": settings.SITE_ANALYTICS_KEY, "days": days},
                      follow_redirects=True, timeout=TIMEOUT_SEC)
        data = r.json()
    except (httpx.HTTPError, ValueError):
        raise DomainError("site_analytics_unavailable", "site analytics did not answer", 502)
    if not isinstance(data, dict):
        raise DomainError("site_analytics_unavailable", "site analytics answered with something else", 502)
    if not data.get("ok"):
        if data.get("error") == "bad_key":
            raise DomainError("site_analytics_bad_key", "SITE_ANALYTICS_KEY does not match REPORT_KEY", 502)
        if data.get("error") in ("bad_session", "not_admin"):
            # a script deployment older than the key entrance treats every report request as a CRM-session one
            raise DomainError("site_analytics_outdated", "the deployed analytics script has no key entrance: deploy a new version", 502)
        raise DomainError("site_analytics_unavailable", f"site analytics error: {data.get('error') or 'unknown'}", 502)
    report = data.get("report")
    if not isinstance(report, dict):
        raise DomainError("site_analytics_unavailable", "site analytics answered without a report", 502)
    return report


def report(days: int = 7, fresh: bool = False) -> dict:
    if days not in PERIODS:
        days = 7
    if not settings.SITE_ANALYTICS_URL or not settings.SITE_ANALYTICS_KEY:
        raise DomainError("site_analytics_off", "SITE_ANALYTICS_KEY is not set", 503)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(days)
        if hit and not fresh and now - hit[0] < CACHE_SEC:
            return hit[1]
    out = _fetch(days)
    with _lock:
        _cache[days] = (time.monotonic(), out)
    return out


def clear_cache():
    with _lock:
        _cache.clear()
