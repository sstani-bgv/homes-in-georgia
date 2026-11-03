"""korter.ge: owner-only Tbilisi flat rentals -> raw/korter.json (+ detail fields in raw/korter_details.json).
Pages are server-rendered with the app state in window.INITIAL_STATE: the listing (?seller_type=owner) gives price, area,
rooms, floor, district and the building pin; the detail page adds the description, all photos, bedrooms, bathrooms,
balcony and the seller type. Details are cached by object id, so a refresh only opens pages of new listings.
Phones from the page are not stored."""
import json
import os
import sys
import time

import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
BASE = "https://korter.ge"
URL = BASE + "/en/apartments-for-rent-tbilisi?seller_type=owner&page={}"
PAGES = 4  # 20 per page
DETAILS = "raw/korter_details.json"
KEEP = ("description", "bedroomCount", "bathroomCount", "hasBalcony", "hasTerrace", "parking", "videoLink", "livingArea",
        "kitchenArea", "ceilingHeight", "builtYear", "publishTime")


def state(html):
    i = html.find("window.INITIAL_STATE")
    if i < 0:
        raise ValueError("no INITIAL_STATE on the page")
    return json.JSONDecoder().raw_decode(html, html.index("{", i))[0]


def listing(html):
    return state(html)["apartmentListingStore"]["apartments"]


def detail_fields(html):
    s = state(html)["layoutLandingStore"]
    lay, seller = s["layout"], s.get("seller") or {}
    d = {k: lay.get(k) for k in KEEP}
    d["images"] = [im["mediaSrc"]["default"]["x1"] for im in lay.get("images") or [] if im.get("mediaSrc")]
    d["seller_type"] = seller.get("sellerType")
    return d


def main(details_only=False):
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        if details_only:  # --details-only: detail pages for the listings already in raw/korter.json
            return details(c, json.load(open("raw/korter.json")))
        items, seen = [], set()
        for page in range(1, PAGES + 1):
            r = c.get(URL.format(page))
            r.raise_for_status()
            batch = [i for i in listing(r.text) if i.get("section") == "rent" and i["objectId"] not in seen]
            seen.update(i["objectId"] for i in batch)
            items += batch
            print("page", page, len(batch))
            time.sleep(0.5)
        assert items, "no rentals parsed: the page layout changed"
        json.dump(items, open("raw/korter.json", "w"), ensure_ascii=False)
        print("saved", len(items))
        details(c, items)


def details(c, items):
    cache = json.load(open(DETAILS)) if os.path.exists(DETAILS) else {}
    new = [i for i in items if str(i["objectId"]) not in cache]
    for n, i in enumerate(new, 1):
        try:
            r = c.get(BASE + i["link"])
            r.raise_for_status()
            cache[str(i["objectId"])] = detail_fields(r.text)
        except Exception as e:  # a missing detail only means fewer facts for that listing
            print("detail failed", i["objectId"], repr(e)[:120])
        if n % 20 == 0 or n == len(new):
            json.dump(cache, open(DETAILS, "w"), ensure_ascii=False)
        time.sleep(0.5)
    live = {str(i["objectId"]) for i in items}
    json.dump({k: v for k, v in cache.items() if k in live}, open(DETAILS, "w"), ensure_ascii=False)
    print(f"details: {len(new)} new pages, {len(live & set(cache))}/{len(items)} have details")


if __name__ == "__main__":
    main("--details-only" in sys.argv)
