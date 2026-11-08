"""Threads posts found by keywords (sources.json -> threads_defaults.queries) -> raw/threads.json (+ photos in raw/img/).

Official Threads Graph API keyword search (the same call as sstani-bgv/threads-engine ThreadsApi.search_posts): the app
needs the `threads_keyword_search` permission and a long-lived user token. Free; Meta's limit is per 24 h of queries,
so it runs every TH_EVERY_MIN (refresh.sh). Incremental: each run asks only for posts since the previous run
(raw/threads_state.json, +15 min overlap); kept posts are merged into raw/threads_api.json for `days_back` days.
Token: raw/threads_token.json (the persistent raw volume), seeded once from $THREADS_ACCESS_TOKEN (.env); a long-lived
token lasts 60 days, so it is refreshed here every REFRESH_DAYS and saved back. No token -> skipped, exit 0.
Rentals only: the same filter as Facebook (no photos, seekers, sales, daily, other cities are dropped).

Usage: python fetch_threads.py [--days 7] [--full | --reuse]
"""
import argparse
import json
import os
import time
from datetime import datetime

import httpx

import db
from fetch_facebook import download, skip_reason

API = "https://graph.threads.net/v1.0"
RAW, STATE, TOKEN = "raw/threads_api.json", "raw/threads_state.json", "raw/threads_token.json"
REFRESH_DAYS = 7
OVERLAP_S = 15 * 60
FIELDS = "id,text,permalink,username,timestamp,media_type,media_url,is_quote_post"


def token():
    """The saved token (refreshed weekly), or the bootstrap one from .env on the first run."""
    db.load_env()
    saved = json.load(open(TOKEN)) if os.path.exists(TOKEN) else {}
    tok = saved.get("access_token") or os.environ.get("THREADS_ACCESS_TOKEN", "").strip() or None
    if tok and time.time() - saved.get("refreshed_at", 0) > REFRESH_DAYS * 86400:
        r = httpx.get("https://graph.threads.net/refresh_access_token",
                      params={"grant_type": "th_refresh_token", "access_token": tok}, timeout=30)
        if r.status_code < 400 and r.json().get("access_token"):
            tok = r.json()["access_token"]
            json.dump({"access_token": tok, "refreshed_at": time.time(), "expires_in": r.json().get("expires_in")}, open(TOKEN, "w"))
            os.chmod(TOKEN, 0o600)
            print("threads: token refreshed")
        else:  # keep using it until it expires; the error shows up in refresh.log
            print(f"threads: token refresh failed: HTTP {r.status_code}")
    return tok


def search(c, q, since, limit):
    """One keyword query, newest first -> posts (dicts as the API gives them)."""
    params = {"q": q, "search_type": "RECENT", "limit": str(max(1, min(limit, 100))), "fields": FIELDS}
    if since:
        params["since"] = str(int(since))
    r = c.get(f"{API}/keyword_search", params=params)
    if r.status_code >= 400:
        raise RuntimeError(f"threads keyword_search {q!r}: HTTP {r.status_code} {r.text[:300]}")
    return r.json().get("data") or []


def photos_of(c, p):
    """Image URLs of a post: the single image, or the images of a carousel (one extra call)."""
    if p.get("media_type") == "IMAGE" and p.get("media_url"):
        return [p["media_url"]]
    if p.get("media_type") == "CAROUSEL_ALBUM":
        r = c.get(f"{API}/{p['id']}/children", params={"fields": "media_type,media_url"})
        if r.status_code < 400:
            return [x["media_url"] for x in r.json().get("data") or [] if x.get("media_type") == "IMAGE" and x.get("media_url")]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float)
    ap.add_argument("--reuse", action="store_true", help=f"rebuild from {RAW}, no API calls")
    ap.add_argument("--full", action="store_true", help="ignore the last run, refetch the whole days_back window")
    a = ap.parse_args()
    d = json.load(open("sources.json")).get("threads_defaults", {})
    queries, per_query, photos = d.get("queries", []), d.get("per_query", 50), d.get("photos", 5)
    days = a.days or d.get("days_back", 7)
    kept = json.load(open(RAW)) if os.path.exists(RAW) else []
    if a.reuse:
        data = kept
    else:
        tok = token()
        if not tok:
            print("threads: no token (THREADS_ACCESS_TOKEN in .env), skipping")
            return
        started = time.time()
        last = None if a.full or not os.path.exists(STATE) else json.load(open(STATE)).get("last_run")
        since = max(started - days * 86400, (last - OVERLAP_S) if last else 0)
        new = []
        with httpx.Client(headers={"Authorization": f"Bearer {tok}"}, timeout=60) as c:
            for q in queries:
                for p in search(c, q, since, per_query):
                    if p.get("is_quote_post"):
                        continue
                    p["query"], p["photos_src"] = q, photos_of(c, p)
                    new.append(p)
        cut = started - days * 86400
        by_id = {x["id"]: x for x in kept + new}  # the same post found by two queries counts once
        data = [x for x in by_id.values() if _ts(x) >= cut]
        json.dump(data, open(RAW, "w"), ensure_ascii=False)
        json.dump({"last_run": started}, open(STATE, "w"))
        print(f"threads: {len(new)} posts for {len(queries)} queries, {len(data)} kept for {days} days")

    out, skipped = [], {}
    for p in data:
        post = {"text": p.get("text") or "", "photos_src": p.get("photos_src") or []}
        if why := skip_reason(post):
            skipped[why] = skipped.get(why, 0) + 1
            continue
        user = p.get("username") or ""
        out.append({"id": p["id"], "group": user, "groups": [user], "post_urls": [p.get("permalink")],
                    "author": user or None, "author_name": user, "author_type": None, "date": p.get("timestamp"),
                    "text": post["text"], "photos": download(p["id"], post["photos_src"][:photos], prefix="th")})
    out = [o for o in out if o["photos"]]
    json.dump(out, open("raw/threads.json", "w"), ensure_ascii=False, indent=1)
    print(f"threads: {len(data)} posts -> rentals saved {len(out)} | skipped: {skipped}")


def _ts(p):
    """API timestamp "2026-11-08T12:00:00+0000" -> unix seconds (unknown -> now, so the post is kept)."""
    try:
        return datetime.strptime(p["timestamp"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (KeyError, ValueError, TypeError):
        return time.time()


if __name__ == "__main__":
    main()
