"""myhome.ge: owner-only Tbilisi flat rentals -> raw/myhome.json (+ detail fields in raw/myhome_details.json).
The JSON API needs login now, but search pages embed page data in __NEXT_DATA__;
curl_cffi's Chrome TLS fingerprint gets past Cloudflare. Detail pages add the exact pin, the parameters list
(amenities, pets), rent period, YouTube / 3D links and the rendered labels (Status, Condition, Heating, Parking,
Bathrooms); cached by id, a refresh only opens pages of new listings. Phones are not stored."""
import json
import os
import re
import sys
import time

from curl_cffi import requests

URL = "https://www.myhome.ge/en/real-estate/?deal_types=2&real_estate_types=1&cities=1&owner_type=physical&currency_id=2&page={}"
DETAILS = "raw/myhome_details.json"
KEEP = ("lat", "lng", "parameters", "balconies", "rent_period", "youtube_link", "3d_url", "condition", "total_floors")
LABELS = ("Status", "Condition", "Bathrooms", "Heating", "Parking", "Hot water", "Ceiling height")


def next_data(html):
    return json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S).group(1))


def labels(html):
    """Rendered "Label|Value" pairs of the details block (the ids behind them have no public dictionary)."""
    t = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    toks = [x.strip() for x in re.sub(r"<[^>]+>", "|", t).split("|") if x.strip()]
    return {k: toks[n + 1] for n, k in enumerate(toks[:-1]) if k in LABELS}


def detail(i):
    html = requests.get(f"https://www.myhome.ge/en/pr/{i['id']}/{i.get('dynamic_slug') or ''}", impersonate="chrome", timeout=30).text
    qs = next_data(html)["props"]["pageProps"]["dehydratedState"]["queries"]
    st = next(q["state"]["data"]["data"]["statement"] for q in qs if q["queryKey"][1:2] == ["details"])
    out = {k: st.get(k) for k in KEEP}
    out["parameters"] = [{"key": p.get("key")} for p in st.get("parameters") or []]
    out["labels"] = labels(html)
    return out


def main(details_only=False):
    if details_only:  # --details-only: detail pages for the listings already in raw/myhome.json
        return details(json.load(open("raw/myhome.json")))
    items = []
    for page in range(1, 5):
        html = requests.get(URL.format(page), impersonate="chrome", timeout=30).text
        qs = next_data(html)["props"]["pageProps"]["dehydratedState"]["queries"]
        batch = next(q["state"]["data"]["data"]["data"] for q in qs if q["queryKey"][:2] == ["statements", "list"])
        items += batch
        print("page", page, len(batch))

    assert all(i["user_type"]["type"] == "physical" for i in items)
    json.dump(items, open("raw/myhome.json", "w"), ensure_ascii=False)
    print("saved", len(items))
    details(items)


def details(items):
    cache = json.load(open(DETAILS)) if os.path.exists(DETAILS) else {}
    new = [i for i in items if str(i["id"]) not in cache]
    for n, i in enumerate(new, 1):
        try:
            cache[str(i["id"])] = detail(i)
        except Exception as e:  # a missing detail only means fewer facts for that listing
            print("detail failed", i["id"], repr(e)[:120])
        if n % 20 == 0 or n == len(new):
            json.dump(cache, open(DETAILS, "w"), ensure_ascii=False)
        time.sleep(0.5)
    live = {str(i["id"]) for i in items}
    json.dump({k: v for k, v in cache.items() if k in live}, open(DETAILS, "w"), ensure_ascii=False)
    print(f"details: {len(new)} new pages, {len(live & set(cache))}/{len(items)} have details")


if __name__ == "__main__":
    main("--details-only" in sys.argv)
