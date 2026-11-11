"""Public Facebook groups (sources.json -> facebook_groups) -> raw/facebook.json (+ photos in raw/img/).

Scraped through the Apify actor dami_studio~facebook-groups-scraper (no cookies, $0.0007 per post + $0.002 per run;
2026-11-08: 2x cheaper per post than thedoor's, and posts dropped by the keyword filter are free). Only posts with a
rent word (KEYWORDS, ka/ru/en) are delivered: seekers, ads and chatter cost nothing; our skip_reason still filters the rest.
Incremental: each run asks only for posts newer than the previous successful run (raw/facebook_state.json, +15 min
overlap), so a run pays for new posts only; they are merged into raw/facebook_apify.json, which keeps the last
`days_back` days. --full refetches the whole window.
Keys: several Apify accounts, tried in order; one out of usage (HTTP 402, free $5 a month) hands over to the next:
$APIFY_TOKENS (comma separated), $APIFY_TOKEN (.env), ~/.config/apify/token.
`--reuse` rebuilds raw/facebook.json from the kept dataset without paying.

Usage: python fetch_facebook.py [--posts 30] [--days 7] [--only <key>,<key>] [--full | --reuse]
"""
import argparse
import json
import os
import re
import time

import httpx

import db
from imgutil import VIDEO_DIR, download_video, to_jpeg

ACTOR = "dami_studio~facebook-groups-scraper"
# the rent words the actor keeps (case-insensitive substring); posts without one are not delivered and not charged
KEYWORDS = ["rent", "сдаю", "сдам", "сдается", "сдаётся", "сдаем", "сдаём", "аренд", "ქირავდება", "გაქირავ", "ქირით"]
API = "https://api.apify.com/v2"
RAW, IMG, STATE = "raw/facebook_apify.json", "raw/img", "raw/facebook_state.json"
OVERLAP_H = 0.25  # re-ask the last 15 min: a post published while the previous run was going is not lost
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


class NoUsage(Exception):
    """Every Apify key is out of usage this month."""


def tokens():
    """Apify keys in order, without duplicates: $APIFY_TOKENS, $APIFY_TOKEN, ~/.config/apify/token."""
    db.load_env()
    out = [t.strip() for t in os.environ.get("APIFY_TOKENS", "").replace(";", ",").split(",")]
    out.append(os.environ.get("APIFY_TOKEN", "").strip())
    f = os.path.expanduser("~/.config/apify/token")
    if os.path.exists(f):
        out.append(open(f).read().strip())
    return list(dict.fromkeys(t for t in out if t))


def _out_of_usage(r):
    if r.status_code == 402:
        return True
    try:
        kind = (r.json().get("error") or {}).get("type") or ""
    except ValueError:
        return False
    return "usage" in kind or "limit" in kind


LAST_USD = [0.0]  # what the last run_actor cost: the fetcher records it with db.record_run


def run_actor(actor, inp):
    """One Apify run, waited for -> dataset items. The first key with usage left runs it (logged as key #n, never the key)."""
    keys = tokens()
    if not keys:
        raise NoUsage("no Apify key: set APIFY_TOKENS or APIFY_TOKEN")
    for n, tok in enumerate(keys, 1):
        with httpx.Client(headers={"Authorization": f"Bearer {tok}"}, timeout=60) as c:
            r = c.post(f"{API}/acts/{actor}/runs", params={"memory": 1024}, json=inp)
            if _out_of_usage(r):
                print(f"apify: key #{n} is out of usage this month, trying the next one")
                continue
            if r.status_code == 403:  # e.g. cheerio-scraper not approved on that account (full-permission-actor-not-approved)
                print(f"apify: key #{n} can't run {actor} ({r.text[:120]}), trying the next one")
                continue
            run = r.raise_for_status().json()["data"]
            while run["status"] in ("READY", "RUNNING"):
                time.sleep(5)
                run = c.get(f"{API}/actor-runs/{run['id']}").raise_for_status().json()["data"]
            print(f"apify: key #{n}", actor, run["status"], f"${run.get('usageTotalUsd') or 0:.3f}", run.get("chargedEventCounts"))
            LAST_USD[0] = run.get("usageTotalUsd") or 0
            return c.get(f"{API}/datasets/{run['defaultDatasetId']}/items", params={"clean": "true"}).raise_for_status().json()
    raise NoUsage(f"all {len(keys)} Apify keys are out of usage this month")


def normalize(x):
    """A dami_studio row -> the post shape the rest of this file reads (post_id, text, media, user, group_id, ...)."""
    media = [{"type": "Photo" if a.get("type") == "photo" else "Video" if a.get("type") == "video" else a.get("type"),
              "uri": a.get("thumbnail") if a.get("type") == "photo" else a.get("videoUrl") or a.get("url")}
             for a in x.get("attachments") or []]
    u = x.get("user") or {}
    return {"post_id": x.get("id"), "post_url": x.get("url"), "text": x.get("text") or x.get("sharedPostText") or "",
            "media": media, "user": {"name": u.get("name"), "profileUrl": u.get("profileUrl") or (u.get("id") and f"fb-user:{u['id']}"),
                                     "type": u.get("type")},
            "user_name": u.get("name"), "group_id": x.get("groupId"), "creation_timestamp": x.get("timestamp"),
            "creation_time": x.get("time")}


def apify_run(groups, posts, hours):
    rows = run_actor(ACTOR, {"startUrls": [{"url": g["url"]} for g in groups], "maxPostsPerGroup": posts,
                             "resultsLimit": min(5000, posts * len(groups)), "viewOption": "CHRONOLOGICAL",
                             "onlyPostsNewerThan": f"{max(1, round(hours * 60))} minutes", "keywords": KEYWORDS})
    return [normalize(x) for x in rows if x.get("recordType", "post") == "post" and x.get("id")]


def download(post_id, urls, prefix="fb"):
    paths = []
    for n, url in enumerate(urls):
        path = f"{IMG}/{prefix}{post_id}_{n}.jpg"  # same name ingest.download_photos would use for id fb<post_id>
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


def video(post_id, urls, prefix="fb"):
    """At most one video per post under VIDEO_MAX_MB -> {"video": {"path"}} or {}."""
    for url in urls:
        if path := download_video(url, f"{VIDEO_DIR}/{prefix}{post_id}.mp4"):
            return {"video": {"path": path}}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", type=int, help="max posts per group")
    ap.add_argument("--days", type=float, help="only posts newer than N days")
    ap.add_argument("--only", help="comma-separated group keys from sources.json")
    ap.add_argument("--reuse", action="store_true", help=f"rebuild from {RAW}, no Apify call")
    ap.add_argument("--full", action="store_true", help="ignore the last run, refetch the whole days_back window")
    a = ap.parse_args()
    cfg = json.load(open("sources.json"))
    d = cfg.get("facebook_defaults", {})
    groups = [g for g in cfg["facebook_groups"] if not a.only or g["key"] in a.only.split(",")]
    photos = d.get("photos", 3)

    days = a.days or d.get("days_back", 7)
    kept = json.load(open(RAW)) if os.path.exists(RAW) else []
    if a.reuse:
        data = kept
    else:
        started = time.time()
        last = None if a.full or not os.path.exists(STATE) else json.load(open(STATE)).get("last_run")
        hours = days * 24 if last is None else min(days * 24, (started - last) / 3600 + OVERLAP_H)
        new = apify_run(groups, a.posts or d.get("posts_per_group", 30), hours)
        cut = started - days * 86400  # merge by post id, keep the rolling window
        by_id = {x.get("post_id"): x for x in kept + new if x.get("post_id")}
        data = [x for x in by_id.values() if (x.get("creation_timestamp") or started) >= cut]
        json.dump(data, open(RAW, "w"), ensure_ascii=False)
        json.dump({"last_run": started}, open(STATE, "w"))
        print(f"facebook: {len(new)} posts from the last {hours:.2f} h, {len(data)} kept for {days} days")
        db.record_run("facebook", LAST_USD[0], len(new), hours)
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
