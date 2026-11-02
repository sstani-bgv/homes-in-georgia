"""ss.ge: owner-only Tbilisi flat rentals -> raw/ss.json (+ detail fields in raw/ss_details.json).
Detail pages (__NEXT_DATA__ applicationData) add the exact pin, amenities, building status, renovation and videos;
they are cached by id, so a refresh only opens pages of new listings. Phones from the page are not stored."""
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
        html = c.get("https://home.ss.ge/en").text
        token = re.search(r'"credentialsToken":"([^"]+)"', html).group(1)
        items = []
        for page in range(1, PAGES + 1):
            r = c.post("https://api-gateway.ss.ge/v1/RealEstate/LegendSearch",
                       headers={"Authorization": f"Bearer {token}"},
                       json={"realEstateType": 5, "realEstateDealType": 1, "cityIdList": [95], "currencyId": 1,
                             "page": page, "pageSize": 30, "order": 1,
                             "advancedSearch": {"individualEntityOnly": True}})
            r.raise_for_status()
            data = r.json()
            batch = data.get("realStateItemModel") or data.get("items") or next(v for v in data.values() if isinstance(v, list))
            items += batch
            print("page", page, len(batch))

        # the gateway serves a fake 2024 dataset to non-browser clients: make sure we got fresh rentals
        assert items and sum(i["createDate"] >= "2026" for i in items[:10]) >= 6, items[:1]  # a few old promoted ads sit on top
        json.dump(items, open("raw/ss.json", "w"), ensure_ascii=False)
        print("saved", len(items))
        details(c, items)


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
