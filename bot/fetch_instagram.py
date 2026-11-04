"""Instagram accounts (sources.json -> instagram_accounts) -> raw/instagram.json (+ photos in raw/img/).

Scraped through the official Apify actor apify~instagram-post-scraper: $0.0027 per post with all carousel photos and
the video ($0.0017 + $0.001 "post-details"), no fee per run, so a run with no new posts costs nothing. Incremental like
fetch_facebook.py: each run asks only for posts newer than the previous successful run (raw/instagram_state.json,
+15 min overlap), merged into raw/instagram_apify.json, which keeps the last `days_back` days.
Rental posts in Instagram come from agencies, so these accounts are competitors like the tg: channels.

Usage: python fetch_instagram.py [--posts 20] [--days 7] [--only <user>,<user>] [--full | --reuse]
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

from fetch_facebook import IMG, OVERLAP_H, download, run_actor, skip_reason, video

ACTOR = "apify~instagram-post-scraper"
RAW, STATE = "raw/instagram_apify.json", "raw/instagram_state.json"


def apify_run(accounts, posts, since):
    return run_actor(ACTOR, {"username": accounts, "resultsLimit": posts, "skipPinnedPosts": True,
                             "onlyPostsNewerThan": datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})


def ts(p):
    try:
        return datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", type=int, help="max posts per account")
    ap.add_argument("--days", type=float, help="only posts newer than N days")
    ap.add_argument("--only", help="comma-separated usernames from sources.json")
    ap.add_argument("--reuse", action="store_true", help=f"rebuild from {RAW}, no Apify call")
    ap.add_argument("--full", action="store_true", help="ignore the last run, refetch the whole days_back window")
    a = ap.parse_args()
    cfg = json.load(open("sources.json"))
    d = cfg.get("instagram_defaults", {})
    accounts = [x["key"] for x in cfg.get("instagram_accounts", []) if not a.only or x["key"] in a.only.split(",")]
    days = a.days or d.get("days_back", 7)
    kept = json.load(open(RAW)) if os.path.exists(RAW) else []
    if a.reuse:
        data = kept
    elif not accounts:
        print("instagram: no accounts in sources.json")
        return
    else:
        started = time.time()
        last = None if a.full or not os.path.exists(STATE) else json.load(open(STATE)).get("last_run")
        since = max(started - days * 86400, (last or 0) - OVERLAP_H * 3600)
        new = apify_run(accounts, a.posts or d.get("posts_per_account", 20), since)
        new = [p for p in new if p.get("id") and not p.get("error")]
        by_id = {p["id"]: p for p in kept + new}
        data = [p for p in by_id.values() if (ts(p) or started) >= started - days * 86400]
        json.dump(data, open(RAW, "w"), ensure_ascii=False)
        json.dump({"last_run": started}, open(STATE, "w"))
        print(f"instagram: {len(new)} posts since {datetime.fromtimestamp(since):%d.%m %H:%M}, {len(data)} kept for {days} days")

    os.makedirs(IMG, exist_ok=True)
    out, skipped = [], {}
    for p in data:
        kids = p.get("childPosts") or []
        post = {"text": p.get("caption") or "",
                "photos_src": p.get("images") or [k["displayUrl"] for k in kids if k.get("displayUrl")] or
                              [p["displayUrl"]] * bool(p.get("displayUrl"))}
        why = skip_reason(post)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        vids = [x["videoUrl"] for x in [p, *kids] if x.get("videoUrl")][:1]
        user = p.get("ownerUsername")
        out.append({"id": p["id"], "group": user, "groups": [user], "post_urls": [p.get("url")], "author": user,
                    "author_name": p.get("ownerFullName"), "author_type": "instagram", "date": p.get("timestamp"),
                    "text": post["text"], "photos": download(p["id"], post["photos_src"][:d.get("photos", 5)], "ig"),
                    **video(p["id"], vids, "ig")})
    out = [o for o in out if o["photos"]]
    json.dump(out, open("raw/instagram.json", "w"), ensure_ascii=False, indent=1)
    print(f"posts {len(data)} -> rentals saved {len(out)}, videos {sum('video' in o for o in out)} | skipped: {skipped}")


if __name__ == "__main__":
    main()
