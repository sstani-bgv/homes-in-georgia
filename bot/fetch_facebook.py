"""Public Facebook groups (sources.json -> facebook_groups) -> raw/facebook.json (+ photos in raw/img/).

Scraped through the Apify actor thedoor~facebook-group-post-scraper (no cookies, ~$0.0015 per post + $0.005 per run).
Token: $APIFY_TOKEN or ~/.config/apify/token. The raw dataset is kept in raw/facebook_apify.json,
so `--reuse` rebuilds raw/facebook.json without paying again.

Usage: python fetch_facebook.py [--posts 30] [--days 7] [--only <key>,<key>] [--reuse]
"""
import argparse
import json
import os
import re
import time

import httpx

from imgutil import VIDEO_DIR, download_video, to_jpeg

ACTOR = "thedoor~facebook-group-post-scraper"
API = "https://api.apify.com/v2"
RAW, IMG = "raw/facebook_apify.json", "raw/img"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"

# Facebook groups mix rentals with seekers, sales, daily flats and ads: keep only long-term rental offers
RENT = re.compile(r"for rent|for lease|\brent\b|rental|сда[её]тся|сдаю|сдам|сдаем|в аренду|аренд|ქირავდება|გაქირავ|ქირით", re.I)
WANTED = re.compile(r"looking for (an? |the )?(apartment|flat|room|house|place)|ищу|ищем(?! (жильц|арендатор|квартирант))|сниму|"
                    r"ვეძებ|ვიქირავებ|ვიქირაობ", re.I)
SALE = re.compile(r"for sale|прода[её]тся|продам|продажа|იყიდება|გაიყიდება", re.I)
DAILY = re.compile(r"daily|per night|nightly|посуточн|почасов|დღიურად|საათობრივ|booking\.com|airbnb", re.I)
OTHER_CITY = re.compile(r"batumi|батуми|ბათუმ|kobuleti|кобулети|ქობულეთ", re.I)
COMMERCIAL = re.compile(r"commercial|office space|офисн|коммерческ|საოფისე|კომერციულ", re.I)
TBILISI = re.compile(r"tbilisi|тбилиси|თბილის", re.I)


def skip_reason(post):
    t = post.get("text") or ""
    if not post["photos_src"]:
        return "нет фото"
    if len(t) < 40:
        return "нет текста"
    if WANTED.search(t):
        return "ищут квартиру"
    if DAILY.search(t):
        return "посуточно"
    rent, sale = RENT.search(t), SALE.search(t)
    if not rent:
        return "продажа" if sale else "не аренда"
    if sale and sale.start() < rent.start():  # "for sale ... can also rent" is a sale post
        return "продажа"
    if COMMERCIAL.search(t):
        return "коммерция"
    if OTHER_CITY.search(t) and not TBILISI.search(t):
        return "не Тбилиси"
    return None


def author_key(p):
    u = p.get("user") or {}
    name = p.get("user_name") or u.get("name") or ""
    # anonymous posts ("Anonymous participant" or a random alias like ArticulateBee7804) can't be tied to a person
    if u.get("type") == "GroupAnonAuthorProfile" or not name or name.lower().startswith(("anonymous", "анонимн")):
        return None  # anonymous posts: one author per post, author signals can't apply
    return u.get("profileUrl") or p.get("user_url") or name


def apify_run(groups, posts, days):
    tok = os.environ.get("APIFY_TOKEN") or open(os.path.expanduser("~/.config/apify/token")).read().strip()
    h = {"Authorization": f"Bearer {tok}"}
    inp = {"url": [g["url"] for g in groups], "maxPosts": posts, "postsNewerThanDays": days,
           "sortingOrder": "newest_posts", "includeTopComment": False}
    with httpx.Client(headers=h, timeout=60) as c:
        run = c.post(f"{API}/acts/{ACTOR}/runs", params={"memory": 1024}, json=inp).raise_for_status().json()["data"]
        while run["status"] in ("READY", "RUNNING"):
            time.sleep(5)
            run = c.get(f"{API}/actor-runs/{run['id']}").raise_for_status().json()["data"]
        print("apify:", run["status"], f"${run.get('usageTotalUsd') or 0:.3f}", run.get("chargedEventCounts"))
        return c.get(f"{API}/datasets/{run['defaultDatasetId']}/items", params={"clean": "true"}).raise_for_status().json()


def download(post_id, urls):
    paths = []
    for n, url in enumerate(urls):
        path = f"{IMG}/fb{post_id}_{n}.jpg"  # same name ingest.download_photos would use for id fb<post_id>
        if not os.path.exists(path):
            try:
                r = httpx.get(url, headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            open(path + ".src", "wb").write(r.content)
            to_jpeg(path + ".src", path, 1280)
            os.remove(path + ".src")
        if os.path.exists(path):
            paths.append(path)
    return paths


def video(post_id, urls):
    """At most one video per post under VIDEO_MAX_MB -> {"video": {"path"}} or {}."""
    for url in urls:
        if path := download_video(url, f"{VIDEO_DIR}/fb{post_id}.mp4"):
            return {"video": {"path": path}}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", type=int, help="max posts per group")
    ap.add_argument("--days", type=float, help="only posts newer than N days")
    ap.add_argument("--only", help="comma-separated group keys from sources.json")
    ap.add_argument("--reuse", action="store_true", help=f"rebuild from {RAW}, no Apify call")
    a = ap.parse_args()
    cfg = json.load(open("sources.json"))
    d = cfg.get("facebook_defaults", {})
    groups = [g for g in cfg["facebook_groups"] if not a.only or g["key"] in a.only.split(",")]
    photos = d.get("photos", 3)

    if a.reuse:
        data = json.load(open(RAW))
    else:
        data = apify_run(groups, a.posts or d.get("posts_per_group", 30), a.days or d.get("days_back", 7))
        json.dump(data, open(RAW, "w"), ensure_ascii=False)
    key_by_id = {}
    for g in cfg["facebook_groups"]:
        key_by_id[g.get("id") or g["url"].rstrip("/").split("/")[-1]] = g["key"]

    # one owner often posts the same flat into several groups: collapse to one record with all post links
    posts, seen = [], {}
    for p in data:
        if not p.get("post_id"):
            continue
        shared = p.get("sharedPost") or {}  # a shared post keeps its text and photos inside sharedPost
        p["text"] = p.get("text") or shared.get("text") or ""
        media = p.get("media") or shared.get("media") or []
        p["photos_src"] = [m["uri"] for m in media if m.get("type") == "Photo" and m.get("uri")]
        # videos: the actor gives a signed mp4 (it expires within days, so download now) or only a thumbnail
        p["video_src"] = [m["uri"] for m in media if m.get("type") in ("Video", "Reel") and ".mp4" in (m.get("uri") or "")][:1]
        p["group"] = key_by_id.get(p.get("group_id"), p.get("group_id"))
        norm = re.sub(r"\W", "", p["text"].lower())[:200]
        k = (author_key(p) or ("anon" if len(norm) > 60 else p["post_id"]), norm)
        if k in seen:
            first = seen[k]
            first["post_urls"].append(p["post_url"])
            first["groups"].append(p["group"])
            continue
        p["post_urls"], p["groups"] = [p["post_url"]], [p["group"]]
        seen[k] = p
        posts.append(p)

    os.makedirs(IMG, exist_ok=True)
    out, skipped = [], {}
    for p in posts:
        why = skip_reason(p)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        u = p.get("user") or {}
        out.append({"id": p["post_id"], "group": p["group"], "groups": p["groups"], "post_urls": p["post_urls"],
                    "author": author_key(p), "author_name": p.get("user_name"), "author_type": u.get("type"),
                    "date": p.get("creation_time"), "text": p["text"],
                    "photos": download(p["post_id"], p["photos_src"][:photos]),
                    **video(p["post_id"], p["video_src"])})
    out = [o for o in out if o["photos"]]
    json.dump(out, open("raw/facebook.json", "w"), ensure_ascii=False, indent=1)
    print(f"posts {len(data)} -> unique {len(posts)} -> rentals saved {len(out)}, videos {sum('video' in o for o in out)} | skipped: {skipped}")


if __name__ == "__main__":
    main()
