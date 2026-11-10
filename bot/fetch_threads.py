"""Threads posts found by keywords (sources.json -> threads_defaults.queries) -> raw/threads.json (+ photos in raw/img/).

Scraped through the Apify actor dacoder~threads-search-post-scraper (no login, sort "recent"): $0.03 per query per
run, however many posts it returns (2026-11-10: 2 queries -> 40 posts, newest 2 h old). The official Graph API
keyword_search finds only the token owner's own posts until Meta App Review, so it is not used.
monitorMode: the actor remembers what it gave per query (per Apify account) and returns only new posts, so runs
by the admin schedule (refresh.sh) see fresh ads only; they are merged into raw/threads_apify.json for `days_back` days.
Keys: the same Apify keys as Facebook (fetch_facebook.tokens). Rentals only: the same filter as Facebook.

Usage: python fetch_threads.py [--days 7] [--reuse]
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

import db
from fetch_facebook import LAST_USD, download, run_actor, skip_reason

ACTOR = "dacoder~threads-search-post-scraper"
RAW, STATE = "raw/threads_apify.json", "raw/threads_state.json"


def photos_of(p):
    """Image URLs of a post: every carousel image, or the single image (a video gives its cover)."""
    media = p.get("carousel_media") or [p]
    return [m["image_versions2"]["candidates"][0]["url"] for m in media
            if ((m.get("image_versions2") or {}).get("candidates") or [None])[0]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float)
    ap.add_argument("--reuse", action="store_true", help=f"rebuild from {RAW}, no Apify run")
    a = ap.parse_args()
    d = json.load(open("sources.json")).get("threads_defaults", {})
    queries, photos = d.get("queries", []), d.get("photos", 5)
    days = a.days or d.get("days_back", 7)
    kept = json.load(open(RAW)) if os.path.exists(RAW) else []
    if a.reuse:
        data = kept
    else:
        started = time.time()
        new = run_actor(ACTOR, {"searchQueries": queries, "sort": "recent", "monitorMode": True, "lastDays": int(days)})
        cut = started - days * 86400
        by_id = {str(x["pk"]): x for x in kept + new if x.get("pk")}  # the same post found by two queries counts once
        data = [x for x in by_id.values() if (x.get("taken_at") or started) >= cut]
        json.dump(data, open(RAW, "w"), ensure_ascii=False)
        print(f"threads: {len(new)} new posts for {len(queries)} queries, {len(data)} kept for {days} days")
        last = json.load(open(STATE)).get("last_run") if os.path.exists(STATE) else None
        db.record_run("threads", LAST_USD[0], len(new), (started - last) / 3600 if last else 0)
        json.dump({"last_run": started}, open(STATE, "w"))

    out, skipped = [], {}
    for p in data:
        post = {"text": (p.get("caption") or {}).get("text") or "", "photos_src": photos_of(p)}
        if why := skip_reason(post):
            skipped[why] = skipped.get(why, 0) + 1
            continue
        pid, user = str(p["pk"]), (p.get("user") or {}).get("username") or ""
        date = datetime.fromtimestamp(p["taken_at"], timezone.utc).isoformat() if p.get("taken_at") else None
        out.append({"id": pid, "group": user, "groups": [user], "post_urls": [p.get("permalink") or p.get("post_url")],
                    "author": user or None, "author_name": user, "author_type": None, "date": date,
                    "text": post["text"], "photos": download(pid, post["photos_src"][:photos], prefix="th")})
    out = [o for o in out if o["photos"]]
    json.dump(out, open("raw/threads.json", "w"), ensure_ascii=False, indent=1)
    print(f"threads: {len(data)} posts -> rentals saved {len(out)} | skipped: {skipped}")


if __name__ == "__main__":
    main()
