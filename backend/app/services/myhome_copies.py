"""Копии объекта на myhome (David 11-22, Stanislav: «по адресу, метражу»): the parser keeps only owners' fresh ads, so
agents' copies of an exclusive (the owner's photos re-posted 30 times) never reached «Похожие». For one object a
narrow myhome search is made — its district, the rooms, the area ±12 %, agents included — then every rental on its
street with the same rooms and area; the ads found are kept as listings with status «copy» (never shown to clients,
not marked gone by the parser's sync) with their photos' dHash, so «Похожие» scores them like any other ad
(shared photos, the house, floor, area). Stanislav 11-23: run on publishing, on «Сравнить по ссылке» and by the card's
«Найти копии» only (no daily pass); the link's photos drive a wide search (link_copies)."""
import json
import logging
import re
import time

from sqlalchemy.orm import Session

from app import districts
from app.models import Listing, Property
from app.services.common import now
from app.services.dedup import _same_street, street_house, street_words

log = logging.getLogger(__name__)
SEARCH = "https://www.myhome.ge/en/real-estate/?deal_types=2&real_estate_types=1&cities=1&currency_id=2&{}"
PAGES = 5           # myhome renders at most 5 pages (120 ads) of one search
AREA_TOL = 0.12
PHOTOS = 6          # photos per ad to hash: enough to catch the copied ones, few downloads
STATUS = "copy"


def _page(query: str) -> list[dict] | None:
    from curl_cffi import requests
    html = requests.get(SEARCH.format(query), impersonate="chrome", timeout=30).text
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return None
    qs = json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
    q = next((q for q in qs if q["queryKey"][:2] == ["statements", "list"]), None)
    return q["state"]["data"]["data"]["data"] if q else None


def search(query: str, pause: float = 1.0) -> list[dict]:
    out = []
    for n in range(1, PAGES + 1):
        items = _page(f"{query}&page={n}")
        if not items:
            break
        out += items
        if len(items) < 24:
            break
        time.sleep(pause)
    return out


def _street(addr) -> frozenset:
    """The street words of an address with or without a house number (myhome: «რამიშვილი ნ. ქ. » often has none)."""
    return street_house(addr)[0] or street_words(addr)


def _filters(p: Property) -> str:
    q = []
    rooms = p.rooms or (p.bedrooms + 1 if p.bedrooms is not None else None)  # myhome «2 room» = 1 bedroom + living
    if rooms:
        q.append(f"room_types={min(int(rooms), 6)}")
    if p.area:
        a = float(p.area)
        q.append(f"area_from={int(a * (1 - AREA_TOL))}&area_to={int(a * (1 + AREA_TOL)) + 1}")
    return "&".join(q)


def candidates(p: Property, pause: float = 1.0) -> list[dict]:
    """myhome ads that could be the object: the city narrowed by rooms and area tells myhome's ids of the object's
    district and street; then the district and the street, each with the rooms and area (≤ 120 ads a search)."""
    base = _filters(p)
    tag, street = districts.tag(p.district), _street(p.address)
    found = {i["id"]: i for i in search(base, pause)}

    def ours(i):
        return (tag and districts.tag(i.get("urban_name")) == tag) or \
            (street and _same_street(street, _street(i.get("address"))))
    urbans = {i["urban_id"] for i in found.values() if tag and i.get("urban_id") and districts.tag(i.get("urban_name")) == tag}
    for uid in list(urbans)[:2]:
        found.update({i["id"]: i for i in search(f"urbans={uid}&{base}", pause)})
    streets = {i["street_id"] for i in found.values() if street and i.get("street_id")
               and _same_street(street, _street(i.get("address")))}
    for sid in list(streets)[:3]:  # the object's street, whatever district myhome files it under
        found.update({i["id"]: i for i in search(f"streets={sid}&{base}", pause)})
    def floor_fits(i):  # agents copy the floor with the photos; ±1: «8» / «8-9» / a slip
        return p.floor is None or i.get("floor") is None or abs(int(i["floor"]) - int(p.floor)) <= 1
    return [i for i in found.values() if ours(i) and floor_fits(i)]


def _usd(i: dict) -> int | None:
    pr = (i.get("price") or {}).get("2") or {}
    return int(pr["price_total"]) if pr.get("price_total") else None


def _hashes(urls: list[str]) -> list[int]:
    import httpx

    from app.services.matching import photo_hash
    out = []
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        for u in urls[:PHOTOS]:
            try:
                h = photo_hash(c.get(u).raise_for_status().content)
            except httpx.HTTPError:
                h = None
            out.append((h & ((1 << 64) - 1)) if h is not None else 0)
    return out


def _mh_row(i: dict) -> dict:
    return {"id": f"mh{i['id']}", "source": "myhome.ge",
            "url": f"https://www.myhome.ge/pr/{i['id']}/{i.get('dynamic_slug') or ''}",
            "photos": [x.get("large") for x in i.get("images") or [] if x.get("large")],
            "thumbs": [x.get("thumb") or x.get("large") for x in i.get("images") or [] if x.get("thumb") or x.get("large")],
            "user_type": (i.get("user_type") or {}).get("type"),  # physical | agent | broker
            "address": i.get("address"), "area": i.get("area"), "floor": i.get("floor"),
            "bedrooms": int(i["bedroom"]) if str(i.get("bedroom") or "").isdigit() else None,
            "district": districts.label(i.get("urban_name")) or i.get("urban_name"), "price_usd": _usd(i),
            "text": re.sub(r"<br\s*/?>", "\n", i.get("comment") or i.get("dynamic_title") or "")}


def save(db: Session, items: list[dict]) -> int:
    """Upsert the ads (myhome statements, or rows already shaped like _mh_row) as listings (an ad the parser already
    has keeps its status, a gone one becomes a «copy»)."""
    n, t = 0, now().isoformat(timespec="seconds")
    for i in items:
        r = i if "source" in i else _mh_row(i)
        l = db.get(Listing, r["id"])
        if l is None:
            l = Listing(id=r["id"], source=r["source"], status=STATUS, first_seen=t)
            db.add(l)
        elif l.status not in ("gone", STATUS):
            continue  # a live ad of the parser: «Похожие» sees it already
        l.status, l.urls = STATUS, json.dumps([r["url"]])
        l.address, l.area, l.floor, l.bedrooms = r["address"], r["area"], r["floor"], r["bedrooms"]
        l.district, l.price_usd, l.text = r["district"], r["price_usd"], r["text"]
        if r.get("user_type"):  # who posted it: «Где ещё выложен» shows собственник / агент
            l.details = json.dumps({**(json.loads(l.details or "{}") or {}), "user_type": r["user_type"]})
        if l.photos != json.dumps(r["photos"]):
            l.photos, l.photo_hashes = json.dumps(r["photos"]), json.dumps(_hashes(r["photos"]))
        l.last_seen = l.updated_at = t
        n += 1
    db.commit()
    return n


# ------------------------------------------------------------------ ss.ge (David 11-22: «и SS тоже»)
# Cloudflare blocks the server: the search runs inside Apify (cheerio-scraper, a Georgian residential IP), as the
# parser's bot/fetch_ss.py. The gateway's own filters: areaFrom/areaTo, subdistrictIds, streetIds (agents included).
SS_PF = r"""async function pageFunction({ response, body, sendRequest, customData }) {
  let tok = (body.toString().match(/"credentialsToken":"([^"]+)"/) || [])[1];
  const c = [].concat(response.headers['set-cookie'] || []).find(x => x.startsWith('ss-session-token='));
  if (!tok && c) tok = decodeURIComponent(c.split(';')[0].split('=').slice(1).join('='));
  if (!tok) throw new Error('ss.ge: no token');
  const items = [];
  for (const q of customData.queries) {
    for (let page = 1; page <= customData.pages; page++) {
      const r = await sendRequest({ url: 'https://api-gateway.ss.ge/v1/RealEstate/LegendSearch', method: 'POST', responseType: 'text',
        headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json', Origin: 'https://home.ss.ge', Referer: 'https://home.ss.ge/' },
        body: JSON.stringify(Object.assign({ realEstateType: 5, realEstateDealType: 1, cityIdList: [95], currencyId: 1, pageSize: 30, order: 1, page }, q)) });
      const got = JSON.parse(r.body).realStateItemModel || [];
      items.push(...got);
      if (got.length < 30) break;
    }
  }
  return { items };
}"""
SS_PROXY = {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"], "apifyProxyCountry": "GE"}


def _apify_tokens() -> list[str]:
    import os
    out = [t.strip() for t in os.environ.get("APIFY_TOKENS", "").replace(";", ",").split(",")] + [os.environ.get("APIFY_TOKEN", "").strip()]
    return list(dict.fromkeys(t for t in out if t))


def ss_search(queries: list[dict], pages: int = 3) -> list[dict]:
    """ss.ge LegendSearch for each query (extra filters), up to `pages` × 30 ads each, run in Apify -> the ads."""
    import httpx
    inp = {"startUrls": [{"url": "https://home.ss.ge/en"}], "pageFunction": SS_PF, "proxyConfiguration": SS_PROXY,
           "maxRequestRetries": 2, "customData": {"queries": queries, "pages": pages}}
    for tok in _apify_tokens():
        with httpx.Client(headers={"Authorization": f"Bearer {tok}"}, timeout=60) as c:
            r = c.post("https://api.apify.com/v2/acts/apify~cheerio-scraper/runs",
                       params={"memory": 1024, "forcePermissionLevel": "LIMITED_PERMISSIONS"}, json=inp)
            if r.status_code in (402, 403):
                continue  # out of usage / not allowed on this key: the next one
            run = r.raise_for_status().json()["data"]
            while run["status"] in ("READY", "RUNNING"):
                time.sleep(5)
                run = c.get(f"https://api.apify.com/v2/actor-runs/{run['id']}").raise_for_status().json()["data"]
            out = c.get(f"https://api.apify.com/v2/datasets/{run['defaultDatasetId']}/items", params={"clean": "true"}).json()
            return [i for x in out for i in x.get("items") or []]
    raise RuntimeError("no Apify key could run the ss.ge search")


def _ss_row(i: dict) -> dict:
    a = i.get("address") or {}
    return {"id": f"ss{i['applicationId']}", "source": "ss.ge", "url": f"https://home.ss.ge/ka/udzravi-qoneba/{i['detailUrl']}",
            "photos": [x["fileName"].replace("_Thumb", "") for x in i.get("appImages") or [] if x.get("fileName")],
            "thumbs": [x["fileName"] for x in i.get("appImages") or [] if x.get("fileName")],
            "address": " ".join(str(x) for x in (a.get("streetTitle"), a.get("streetNumber")) if x) or None,
            "area": i.get("totalArea"), "floor": int(i["floorNumber"]) if str(i.get("floorNumber") or "").isdigit() else None,
            "bedrooms": i.get("numberOfBedrooms"), "district": districts.label(a.get("subdistrictTitle")) or a.get("subdistrictTitle"),
            "price_usd": (i.get("price") or {}).get("priceUsd"), "text": f"{i.get('title') or ''}\n{i.get('description') or ''}".strip()}


def ss_candidates(p: Property) -> list[dict]:
    """ss.ge ads that could be the object: the city by the area finds ss.ge's ids of its district and street, then the
    district and the street with the area; kept: in the district or on the street, floor ±1, the same bedrooms."""
    if not p.area:
        return []
    a = float(p.area)
    area = {"areaFrom": int(a * (1 - AREA_TOL)), "areaTo": int(a * (1 + AREA_TOL)) + 1}
    tag, street = districts.tag(p.district), _street(p.address)

    def addr(i):
        return i.get("address") or {}

    def on_street(i):
        return bool(street) and _same_street(street, _street(addr(i).get("streetTitle")))

    def in_district(i):
        return bool(tag) and districts.tag(addr(i).get("subdistrictTitle")) == tag
    first = ss_search([area])
    subs = list({addr(i)["subdistrictId"] for i in first if in_district(i) and addr(i).get("subdistrictId")})[:2]
    streets = list({addr(i)["streetId"] for i in first if on_street(i) and addr(i).get("streetId")})[:3]
    more = ss_search([{**area, "subdistrictIds": subs}] * bool(subs) + [{**area, "streetIds": streets}] * bool(streets)) \
        if subs or streets else []
    found = {i["applicationId"]: i for i in first + more}

    def fits(i):
        fl = i.get("floorNumber")
        floor_ok = p.floor is None or not str(fl or "").isdigit() or abs(int(fl) - int(p.floor)) <= 1
        bed_ok = p.bedrooms is None or i.get("numberOfBedrooms") is None or int(i["numberOfBedrooms"]) == int(p.bedrooms)
        return floor_ok and bed_ok
    return [_ss_row(i) for i in found.values() if (in_district(i) or on_street(i)) and fits(i)]


SS_DETAIL_PF = r"""async function pageFunction({ $ }) {
  const d = JSON.parse($('#__NEXT_DATA__').text()).props.pageProps.applicationData;
  return { d };
}"""


def _ss_detail(url: str) -> dict:
    """One ss.ge ad page (Apify, as ss_search) -> a row like _ss_row."""
    import httpx
    inp = {"startUrls": [{"url": url}], "pageFunction": SS_DETAIL_PF, "proxyConfiguration": SS_PROXY, "maxRequestRetries": 2}
    for tok in _apify_tokens():
        with httpx.Client(headers={"Authorization": f"Bearer {tok}"}, timeout=90) as c:
            r = c.post("https://api.apify.com/v2/acts/apify~cheerio-scraper/run-sync-get-dataset-items",
                       params={"memory": 1024, "forcePermissionLevel": "LIMITED_PERMISSIONS", "timeout": 80}, json=inp)
            if r.status_code in (402, 403):
                continue
            d = r.raise_for_status().json()[0]["d"]
            desc = d.get("description")
            desc = " ".join(v for v in desc.values() if v) if isinstance(desc, dict) else desc
            return _ss_row({"applicationId": d["applicationId"], "detailUrl": url.rsplit("/", 1)[-1], "address": d.get("address"),
                            "totalArea": d.get("totalArea"), "floorNumber": str(d.get("floor") or ""), "numberOfBedrooms": d.get("bedrooms"),
                            "price": d.get("price"), "appImages": d.get("appImages"), "title": d.get("title"), "description": desc})
    raise RuntimeError("no Apify key could open the ss.ge ad")


def _mh_detail(mid: str) -> dict:
    from curl_cffi import requests
    html = requests.get(f"https://www.myhome.ge/en/pr/{mid}/", impersonate="chrome", timeout=30).text
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    qs = json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"] if m else []
    st = next((q["state"]["data"]["data"]["statement"] for q in qs if q["queryKey"][1:2] == ["details"]), None)
    if not st:
        raise ValueError("myhome: the ad page has no data (removed?)")
    return _mh_row(st)


# ------------------------------------------------------------------ korter.ge (David 11-23: «MyHome, SS, Livo, Korter»)
# Livo is a front of myhome's own base (same statements, same ids: bot/fetch_livo.py), so the myhome search finds its
# agents' ads already. Korter is its own base (18k rentals, agents included): its search page filters by district
# (the path), area and floor; the app state (window.INITIAL_STATE) carries the ads, the ad page all its photos.
KR = "https://korter.ge"
KR_PAGES = 5        # 20 ads a page
KR_DETAILS = 15     # ad pages opened per card (the closest area first): their photos are what gets compared
GEL_PER_USD = 2.7   # = bot/ingest.py
KR_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


def _kr_state(html: str) -> dict:
    i = html.find("window.INITIAL_STATE")
    if i < 0:
        raise ValueError("korter: no INITIAL_STATE on the page")
    return json.JSONDecoder().raw_decode(html, html.index("{", i))[0]


def _kr_get(path: str) -> str | None:
    import httpx
    r = httpx.get(KR + path, headers={"User-Agent": KR_UA}, timeout=30, follow_redirects=True)
    return None if r.status_code == 404 else r.raise_for_status().text


def kr_search(slug: str | None, query: str, pause: float = 0.5) -> list[dict]:
    """Korter rentals of a district path (None: the city), up to KR_PAGES pages; an unknown district -> the city."""
    out, n = [], 0
    while n < KR_PAGES:
        n += 1
        html = _kr_get(f"/en/apartments-for-rent-tbilisi{'-' + slug if slug else ''}?{query}&page={n}")
        if html is None:
            if not slug:
                break
            slug, n = None, 0  # Korter has no page for this district: the city
            continue
        items = [i for i in _kr_state(html)["apartmentListingStore"]["apartments"] if i.get("section") == "rent"]
        out += items
        if len(items) < 20:
            break
        time.sleep(pause)
    return out


def _kr_row(obj_id, d: dict, link: str) -> dict:
    """An ad page's layout (+ the search item for the district) -> a row like _mh_row."""
    lay = d["layout"]
    price = lay.get("price")
    if price and lay.get("currency") == "GEL":
        price = round(price / GEL_PER_USD)
    fl = ((lay.get("floorsByHouse") or [{}])[0].get("floorNumbers") or [None])[0]
    rooms = lay.get("roomCount")
    return {"id": f"kr{obj_id}", "source": "korter.ge", "url": KR + link,
            "photos": [im["mediaSrc"]["default"]["x1"] for im in lay.get("images") or [] if im.get("mediaSrc")],
            "address": lay.get("address"), "area": lay.get("area"), "floor": int(fl) if str(fl or "").isdigit() else None,
            "bedrooms": lay.get("bedroomCount") if lay.get("bedroomCount") is not None else (max(rooms - 1, 0) if rooms else None),
            "district": districts.label(d.get("district")) or d.get("district"), "price_usd": price,
            "text": lay.get("description") or ""}


def _kr_detail(link: str, district: str | None = None) -> dict:
    s = _kr_state(_kr_get(link) or "")["layoutLandingStore"]
    return _kr_row(s["objectId"], {"layout": s["layout"], "district": district}, link)


def kr_candidates(p, pause: float = 0.5) -> list[dict]:
    """Korter ads that could be the object: its district (Korter's path, the city when it has none), area ±12 %,
    floor ±1; kept: in the district or on the street, the same bedrooms; the closest areas' ad pages opened."""
    if not p.area:
        return []
    a = float(p.area)
    q = f"min_area={int(a * (1 - AREA_TOL))}&max_area={int(a * (1 + AREA_TOL)) + 1}"
    if p.floor is not None:
        q += f"&min_floor_number={max(int(p.floor) - 1, 1)}&max_floor_number={int(p.floor) + 1}"
    tag, street = districts.tag(p.district), _street(p.address)
    slug = re.sub(r"[^a-z]+", "-", districts.BY_TAG[tag][2].lower()).strip("-") if tag else None

    def fits(i):
        ours = (tag and districts.tag(i.get("subLocalityNominative")) == tag) or \
            (street and _same_street(street, _street(re.sub(r"\s*\(.*\)", "", i.get("address") or ""))))
        rooms = i.get("roomCount")
        return ours and (p.bedrooms is None or not rooms or max(rooms - 1, 0) == int(p.bedrooms))
    found = {i["objectId"]: i for i in kr_search(slug, q, pause) if fits(i)}
    out = []
    for i in sorted(found.values(), key=lambda i: abs(float(i.get("area") or 0) - a))[:KR_DETAILS]:
        try:
            out.append(_kr_detail(i["link"], i.get("subLocalityNominative")))
        except Exception as e:  # noqa: BLE001 — one ad page failed: the others stand
            log.warning("korter ad %s: %r", i.get("objectId"), e)
        time.sleep(pause)
    return out


# ------------------------------------------------------------------ copies by the photos of a link (Stanislav 11-23)
# The owner posts on myhome / ss.ge, agents re-post his photos everywhere with their own area / floor / rooms, and our
# shoot shares none of them. So the ad the agent links («Сравнить по ссылке», or the object's own source ad) is what is
# searched for: every rental in its district and on its street, rooms ±1, no area / floor filter; each one's first
# THUMBS thumbnails are dHashed against the link's photos; then a second round with the found copies' own photos (a
# copy may lead with a shot the owner's ad does not have). A copy re-cut / watermarked past dHash is caught later by the
# photo vectors (embeddings: the narrow candidates are saved with their photos too).
THUMBS = 3
WIDE_STREETS, WIDE_URBANS = 2, 1  # searches of 120 ads (myhome) / 90 (ss.ge) each


def _bits():
    from app.services.similar import PHOTO_BITS
    return PHOTO_BITS


def _hit(hs: list[int], ref: set[int]) -> bool:
    b = _bits()
    return any(bin(x ^ y).count("1") <= b for x in hs if x for y in ref)


def by_photos(rows: list[dict], ref: list[int]) -> list[dict]:
    """The rows (shaped like _mh_row / _ss_row) whose first THUMBS thumbnails share a photo with ref (dHash), then a
    second round with the found ones' own photos -> the copies."""
    ref_s = {h for h in ref if h}
    if not ref_s:
        return []
    thumbs = {r["id"]: _hashes((r.get("thumbs") or r["photos"])[:THUMBS]) for r in rows}
    got = [r for r in rows if _hit(thumbs[r["id"]], ref_s)]
    more = ref_s | {h for r in got for h in _hashes(r["photos"]) if h}
    ids = {r["id"] for r in got}
    return got + [r for r in rows if r["id"] not in ids and _hit(thumbs[r["id"]], more)]


def _rooms(c) -> str:
    rooms = c.rooms or (c.bedrooms + 1 if c.bedrooms is not None else None)
    return f"room_types={','.join(str(x) for x in range(max(int(rooms) - 1, 1), min(int(rooms) + 1, 6) + 1))}" if rooms else ""


def wide_mh(c, pause: float = 1.0) -> list[dict]:
    """myhome rentals in the district and on the street of c (the link's ad), rooms ±1, any area / floor -> rows."""
    tag, street = districts.tag(c.district), _street(c.address)
    seed = search(_filters(c), pause)  # tells myhome's ids of the district and the street
    urbans = list({i["urban_id"] for i in seed if tag and i.get("urban_id") and districts.tag(i.get("urban_name")) == tag})
    streets = list({i["street_id"] for i in seed if street and i.get("street_id") and _same_street(street, _street(i.get("address")))})
    found, rq = {i["id"]: i for i in seed}, _rooms(c)
    for q in [f"streets={x}" for x in streets[:WIDE_STREETS]] + [f"urbans={x}" for x in urbans[:WIDE_URBANS]]:
        found.update({i["id"]: i for i in search(f"{q}&{rq}" if rq else q, pause)})
    return [_mh_row(i) for i in found.values()]


def wide_ss(c) -> list[dict]:
    """ss.ge rentals in the district and on the street of c, any area / floor / rooms -> rows (2 Apify runs)."""
    tag, street = districts.tag(c.district), _street(c.address)
    a = float(c.area) if c.area else None
    first = ss_search([{"areaFrom": int(a * (1 - AREA_TOL)), "areaTo": int(a * (1 + AREA_TOL)) + 1}] if a else [{}])
    addr = lambda i: i.get("address") or {}  # noqa: E731
    subs = list({addr(i)["subdistrictId"] for i in first if tag and addr(i).get("subdistrictId")
                 and districts.tag(addr(i).get("subdistrictTitle")) == tag})[:WIDE_URBANS]
    streets = list({addr(i)["streetId"] for i in first if street and addr(i).get("streetId")
                    and _same_street(street, _street(addr(i).get("streetTitle")))})[:WIDE_STREETS]
    qs = [{"streetIds": streets}] * bool(streets) + [{"subdistrictIds": subs}] * bool(subs)
    found = {i["applicationId"]: i for i in first + (ss_search(qs) if qs else [])}
    return [_ss_row(i) for i in found.values()]


def link_copies(db: Session, p: Property, pause: float = 1.0) -> list[dict]:
    """The copies of the object's linked site ads (the link, its own source ad; LINK_CARDS of them) by their photos."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.models import PropertyListing
    out = {}
    for l in db.scalars(select(Listing).join(PropertyListing, PropertyListing.listing_id == Listing.id)
                        .where(PropertyListing.property_id == p.id, Listing.photo_hashes != "[]",
                               Listing.source.not_in(("object", "agency")))
                        .order_by(PropertyListing.id.desc()).limit(LINK_CARDS)):
        ref = [h for h in json.loads(l.photo_hashes or "[]") if h]
        if not ref or not (l.id[:2] in ("mh", "ss", "lv", "kr")):
            continue
        c = SimpleNamespace(address=l.address or p.address, district=l.district or p.district, area=l.area or p.area,
                            floor=l.floor, rooms=None, bedrooms=l.bedrooms if l.bedrooms is not None else p.bedrooms)
        rows = wide_mh(c, pause)
        try:
            rows += wide_ss(c)
        except Exception as e:  # noqa: BLE001 — ss.ge / Apify down: myhome stands
            log.warning("ss.ge wide search of #%s failed: %r", p.id, e)
        out.update({r["id"]: r for r in by_photos([r for r in rows if r["id"] != l.id and r["id"] not in out], ref)})
    return list(out.values())


# /ka/pr/26740808/… and the new slug form /ka/udzravi-qoneba/qiravdeba-…-vakeshi-26740808/ (David 11-23)
MH_LINK = re.compile(r"myhome\.ge/(?:[a-z]{2}/)?(?:pr/(\d+)|[^?#]*?-(\d{6,})/?(?:[?#]|$))")
SS_LINK = re.compile(r"ss\.ge/.*?(\d{6,})(?:[/?#]|$)")
# livo.ge/en/udzravi-qoneba/…/…-26740808 (myhome's own ad: the same id) and korter.ge/en/apartments-for-rent-tbilisi/…/1074541
LIVO_LINK = re.compile(r"livo\.ge/(?:[a-z]{2}/)?(?:pr/(\d+)|[^?#]*?-(\d{6,})/?(?:[?#]|$))")
KR_LINK = re.compile(r"korter\.ge(/[^?#]*?/\d{5,})/?(?:[?#]|$)")


def by_link(url: str) -> dict:
    """A myhome / ss.ge / livo / korter ad link -> its row (_mh_row / _ss_row shape); ValueError for another link."""
    url = url.strip()
    if m := MH_LINK.search(url):
        return _mh_detail(m[1] or m[2])
    if SS_LINK.search(url):
        return _ss_detail(url.split("?")[0])
    if m := LIVO_LINK.search(url):
        return _mh_detail(m[1] or m[2])
    if m := KR_LINK.search(url):
        return _kr_detail(m[1])
    raise ValueError("Нужна ссылка на объявление myhome.ge, ss.ge, livo.ge или korter.ge")


def compare_link(db: Session, pid: int, url: str, actor=None) -> dict:
    """«Сравнить по ссылке» (David 11-22): the ad of a competitor / an owner is saved as a copy and scored against the
    object as «Похожие» does. David 11-23: the link names the object's ad — it is linked to the object (stays on the
    card, «Объявления на других сайтах»), so the owner's photos in it become the object's and the duplicate search
    (dup_scan, right after) finds every ad that re-posts them, though our own shoot shares none."""
    from sqlalchemy import select

    from app.models import PropertyListing
    from app.services import matching, similar
    from app.services import properties as P
    from app.services.common import log_event
    row = by_link(url)
    save(db, [row])
    r = similar.score_pair(db, f"p:{pid}", f"l:{row['id']}") or {}
    p, l = db.get(Property, pid), db.get(Listing, row["id"])
    owner = db.scalar(select(PropertyListing.property_id).where(PropertyListing.listing_id == l.id)) \
        or db.scalar(select(Property.id).where(Property.listing_id == l.id, Property.id != pid))
    if owner is None:
        matching.attach(db, p, l, actor, ["добавлено по ссылке"], "compare_link")  # no exclusive alert: he linked it
        P.schedule_dup_scan(db, p, "compare_link")
    log_event(db, "property", pid, "compare_link", actor, label="сравнение по ссылке", url=row["url"], listing_id=row["id"],
              pct=r.get("pct"), linked=owner is None)
    db.commit()
    return {"listing_id": row["id"], "url": row["url"], "address": row["address"], "area": row["area"], "floor": row["floor"],
            "bedrooms": row["bedrooms"], "price_usd": row["price_usd"], "photos": row["photos"][:4],
            "linked": owner in (None, pid), "other_object": owner if owner not in (None, pid) else None,
            **{k: r.get(k) for k in ("pct", "level", "why", "matched", "differs")}}


LINK_CARDS = 2  # linked ads searched by their own address / area besides the object


def cards(db: Session, p: Property) -> list:
    """What the copies are searched by: the object, then (David 11-23, «по ссылке составляет карточку объекта и по ней
    находит все матчи») each linked outside ad (the owner's ad from «Сравнить по ссылке») whose street / area / floor
    differ from the object's — the agents copied the owner's ad, not our card."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.models import PropertyListing
    out, seen = [p], {(_street(p.address), float(p.area or 0), p.floor)}
    for l in db.scalars(select(Listing).join(PropertyListing, PropertyListing.listing_id == Listing.id)
                        .where(PropertyListing.property_id == p.id, Listing.source.not_in(("object", "agency")))
                        .order_by(PropertyListing.id.desc())):
        c = SimpleNamespace(address=l.address or p.address, district=l.district or p.district, area=l.area or p.area,
                            floor=l.floor if l.floor is not None else p.floor, rooms=None,
                            bedrooms=l.bedrooms if l.bedrooms is not None else p.bedrooms)
        sig = (_street(c.address), float(c.area or 0), c.floor)
        if sig not in seen and len(out) <= LINK_CARDS:
            seen.add(sig)
            out.append(c)
    return out


def run(db: Session, pid: int, pause: float = 1.0) -> int:
    """Job myhome_copies: search (the object and its linked ads, cards()), save -> how many ads were saved (an event
    on the object says when)."""
    from app.services.common import log_event
    p = db.get(Property, pid)
    if p is None or not (p.area or p.bedrooms is not None or p.rooms):
        return 0
    cs = cards(db, p)
    items = list({i["id"]: i for c in cs for i in candidates(c, pause)}.values())
    n = save(db, items)
    try:
        ss = list({i["id"]: i for c in cs for i in ss_candidates(c)}.values())
        n_ss = save(db, ss)
    except Exception as e:  # noqa: BLE001 — ss.ge / Apify down: the myhome copies stand
        log.warning("ss.ge copies of #%s failed: %r", pid, e)
        ss, n_ss = [], 0
    try:
        kr = list({i["id"]: i for c in cs for i in kr_candidates(c, pause / 2)}.values())
        n_kr = save(db, kr)
    except Exception as e:  # noqa: BLE001 — korter down / its page changed: the others stand
        log.warning("korter copies of #%s failed: %r", pid, e)
        kr, n_kr = [], 0
    try:
        by_link_ = link_copies(db, p, pause)
        n_link = save(db, by_link_)
    except Exception as e:  # noqa: BLE001 — the wide search failed: the narrow one stands
        log.warning("link copies of #%s failed: %r", pid, e)
        by_link_, n_link = [], 0
    log_event(db, "property", pid, "myhome_copies", None, label="поиск копий на myhome, ss.ge и korter", candidates=len(items),
              saved=n, ss_candidates=len(ss), ss_saved=n_ss, kr_candidates=len(kr), kr_saved=n_kr, cards=len(cs),
              by_photos=len(by_link_), by_photos_saved=n_link)
    db.commit()
    log.info("copies of #%s: myhome %s/%s, ss.ge %s/%s, korter %s/%s, by the link's photos %s/%s", pid, len(items), n,
             len(ss), n_ss, len(kr), n_kr, len(by_link_), n_link)
    return n + n_ss + n_kr + n_link
