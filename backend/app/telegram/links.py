"""Public URLs used in Telegram buttons: Mini App pages (/m/...), the listing map (/map), the viewing calendar file."""
from urllib.parse import urlencode

from app.telegram.client import conf


def public_url():
    return conf("PUBLIC_URL").strip().rstrip("/")


def miniapp(path):
    """PUBLIC_URL + /m/<path> ('' when PUBLIC_URL is not set)."""
    base = public_url()
    return base and f"{base}/m/{path.lstrip('/')}"


def lead_app(lead_id):
    return miniapp(f"leads/{lead_id}")


def property_app(pid):
    return miniapp(f"properties/{pid}")


def deal_app(deal_id=None, lead_id=None):
    return miniapp(f"deals/{deal_id}") if deal_id else miniapp(f"deals/new?lead={lead_id}")


def map_pin(listing, lang="ru"):
    """Our OSM map (bot/webapp/index.html served at /map) with the listing's point, or ''."""
    base = public_url()
    if not base or not listing or listing.get("lat") is None or listing.get("lng") is None:
        return ""
    return f"{base}/map?" + urlencode({"pin": f"{listing['lat']:.6f},{listing['lng']:.6f}", "prec": listing.get("geo_precision") or "",
                                       "title": f"{listing.get('district') or ''} · ${listing.get('price_usd')}", "lang": lang})


def google_maps(listing):
    """Google Maps link to the listing's point, or None without coordinates."""
    if not listing or listing.get("lat") is None or listing.get("lng") is None:
        return None
    return f"https://www.google.com/maps/search/?api=1&query={listing['lat']:.6f},{listing['lng']:.6f}"


def ics_url(lead_id):
    """GET /api/public/ics/<token> (backend app.services.ics)."""
    try:
        from app.services.ics import ics_url as url
        return url(lead_id) if public_url() else ""
    except Exception:
        return ""


def tg_user(username=None, tg_id=None):
    if username:
        return f"https://t.me/{username.lstrip('@')}"
    return f"tg://user?id={tg_id}" if tg_id else ""
