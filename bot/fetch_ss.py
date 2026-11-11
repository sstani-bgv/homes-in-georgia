"""ss.ge: owner-only Tbilisi flat rentals -> raw/ss.json (+ detail fields in raw/ss_details.json).
Detail pages (__NEXT_DATA__ applicationData) add the exact pin, amenities, building status, renovation and videos;
they are cached by id, so a refresh only opens pages of new listings. Phones from the page are not stored.
Cloudflare blocks the server IP (403 challenge since Oct 2026): then the same requests run inside Apify (cheerio-scraper,
residential proxy in Georgia; ~$0.002 a run + ~$0.003 a new detail page), the spend goes to db.record_run("ss")."""
import json
import os
import re
import sys
import time

import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
PAGES = 4
DETAILS = "raw/ss_details.json"
KEEP = ("locationLatitude", "locationLongitude", "isManualPin", "nearbySubwayStations", "isPetFriendly", "realEstateStatus",
        "state", "toilet", "applicationVideos", "applicationVideoLink", "uploadVideoLink", "airConditioning", "balcony",
        "elevator", "fridge", "furniture", "internet", "wiFi", "tv", "cableTelevision", "washingMachine", "withPool",
        "storage", "garage", "heating", "floors", "project")


LIST_PF = r"""async function pageFunction({ response, body, sendRequest }) {
  let tok = (body.toString().match(/"credentialsToken":"([^"]+)"/) || [])[1];
  const c = [].concat(response.headers['set-cookie'] || []).find(x => x.startsWith('ss-session-token='));
  if (!tok && c) tok = decodeURIComponent(c.split(';')[0].split('=').slice(1).join('='));
  if (!tok) throw new Error('ss.ge: no token');
  const items = [];
  for (let page = 1; page <= PAGES; page++) {
    const r = await sendRequest({ url: 'https://api-gateway.ss.ge/v1/RealEstate/LegendSearch', method: 'POST', responseType: 'text',
      headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json', Origin: 'https://home.ss.ge', Referer: 'https://home.ss.ge/' },
      body: JSON.stringify(SEARCH(page)) });
    items.push(...(JSON.parse(r.body).realStateItemModel || []));
  }
  return { items };
}"""
DETAIL_PF = r"""async function pageFunction({ $, request, customData }) {
  const d = JSON.parse($('#__NEXT_DATA__').text()).props.pageProps.applicationData;
  return { id: request.userData.id, data: Object.fromEntries(customData.keep.map(k => [k, d[k] ?? null])) };
}"""
PROXY = {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"], "apifyProxyCountry": "GE"}


def search(page):
    return {"realEstateType": 5, "realEstateDealType": 1, "cityIdList": [95], "currencyId": 1, "page": page, "pageSize": 30,
            "order": 1, "advancedSearch": {"individualEntityOnly": True}}


def apify(inp):
    """cheerio-scraper run -> (items, usd). Keys and usage limits as the Facebook fetcher."""
    import fetch_facebook as fb
    out = fb.run_actor("apify~cheerio-scraper", {"proxyConfiguration": PROXY, "maxRequestRetries": 2, **inp})
    return out, fb.LAST_USD[0]


def detail(c, item):
    r = c.get(f"https://home.ss.ge/en/real-estate/{item['detailUrl']}")
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    d = json.loads(m.group(1))["props"]["pageProps"]["applicationData"]
    return {k: d.get(k) for k in KEEP}


def main(details_only=False):
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        if details_only:  # --details-only: detail pages for the listings already in raw/ss.json
            return details(c, json.load(open("raw/ss.json")))
        try:
            items = direct(c)
        except (AssertionError, httpx.HTTPError) as e:  # Cloudflare: the same calls from Apify's Georgian residential IPs
            print("ss.ge direct failed, via Apify:", repr(e)[:150])
            return via_apify()
        save(items)
        details(c, items)


def direct(c):
    html = c.get("https://home.ss.ge/en").raise_for_status().text
    # the bearer token moved from the page HTML to the ss-session-token cookie (Nov 2026); keep both
    m = re.search(r'"credentialsToken":"([^"]+)"', html)
    token = m.group(1) if m else c.cookies.get("ss-session-token")
    assert token, "ss.ge: no credentialsToken in the page and no ss-session-token cookie"
    items = []
    for page in range(1, PAGES + 1):
        r = c.post("https://api-gateway.ss.ge/v1/RealEstate/LegendSearch", headers={"Authorization": f"Bearer {token}"},
                   json=search(page))
        r.raise_for_status()
        data = r.json()
        batch = data.get("realStateItemModel") or data.get("items") or next(v for v in data.values() if isinstance(v, list))
        items += batch
        print("page", page, len(batch))
    return items


def save(items):
    # the gateway serves a fake 2024 dataset to non-browser clients: make sure we got fresh rentals
    assert items and sum(i["createDate"] >= "2026" for i in items[:10]) >= 6, items[:1]  # a few old promoted ads sit on top
    json.dump(items, open("raw/ss.json", "w"), ensure_ascii=False)
    print("saved", len(items))


def via_apify():
    import db
    started = time.time()
    pf = LIST_PF.replace("PAGES", str(PAGES)).replace("SEARCH(page)", "Object.assign(" + json.dumps(search(0)) + ", { page })")
    out, usd = apify({"startUrls": [{"url": "https://home.ss.ge/en"}], "pageFunction": pf})
    items = out[0]["items"]
    save(items)
    cache = json.load(open(DETAILS)) if os.path.exists(DETAILS) else {}
    new = [i for i in items if str(i["applicationId"]) not in cache]
    if new:
        got, u = apify({"startUrls": [{"url": f"https://home.ss.ge/en/real-estate/{i['detailUrl']}", "userData": {"id": str(i["applicationId"])}}
                                      for i in new], "pageFunction": DETAIL_PF, "customData": {"keep": list(KEEP)}, "maxConcurrency": 5})
        usd += u
        cache.update({x["id"]: x["data"] for x in got if x.get("id")})
    live = {str(i["applicationId"]) for i in items}
    json.dump({k: v for k, v in cache.items() if k in live}, open(DETAILS, "w"), ensure_ascii=False)
    print(f"details: {len(new)} new pages via Apify, {len(live & set(cache))}/{len(items)} have details, ${usd:.4f}")
    runs = json.loads(db.get_setting("source_runs") or "{}").get("ss") or []
    db.record_run("ss", usd, len(new), (started - runs[-1][0]) / 3600 if runs else 0)


def details(c, items):
    cache = json.load(open(DETAILS)) if os.path.exists(DETAILS) else {}
    new = [i for i in items if str(i["applicationId"]) not in cache]
    for n, i in enumerate(new, 1):
        try:
            cache[str(i["applicationId"])] = detail(c, i)
        except Exception as e:  # a missing detail only means fewer facts for that listing
            print("detail failed", i["applicationId"], repr(e)[:120])
        if n % 20 == 0 or n == len(new):
            json.dump(cache, open(DETAILS, "w"), ensure_ascii=False)
        time.sleep(0.5)
    live = {str(i["applicationId"]) for i in items}
    json.dump({k: v for k, v in cache.items() if k in live}, open(DETAILS, "w"), ensure_ascii=False)
    print(f"details: {len(new)} new pages, {len(live & set(cache))}/{len(items)} have details")


if __name__ == "__main__":
    main("--details-only" in sys.argv)
