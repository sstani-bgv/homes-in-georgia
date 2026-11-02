"""Public Telegram channels without a user session: reads the web preview https://t.me/s/<username>
(older pages via ?before=<id>) and writes the same raw/channel_<username>.json (+ photos in raw/img/) as
fetch_channel.py, so ingest.py works unchanged. Only public channels with the web preview enabled.

A listing = one post with text and photos (an album shows up on the page as one post with all its photos).
Per post also "links" (URLs behind the text: the agency's Google Maps link is the exact pin) and at most one "video":
the page embeds an mp4 only for small videos (<video src>), downloaded under VIDEO_MAX_MB (20) to raw/video/; for
"Media is too big" (every agency video so far: 50-400 MB) a file already there (seed / Telethon fetch, same name) is reused.
"video_msg" = the video's own message id (the album item's t.me/<channel>/<id> link), whatever its size: the bot (admin of
the agency channel) forwards that message once to get a file_id and sends the original video with no download (bot.py).
Env for quick tests: FETCH_ONLY=user1,user2  FETCH_WANT=5  FETCH_PHOTOS=1  FETCH_VIDEOS=0
Usage: python fetch_channel_web.py
"""
import html
import json
import os
import re
import time

import httpx

from imgutil import download_video

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = "raw"  # relative paths: the same base works in a checkout on the Mac and in the container (/app)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
PAUSE = float(os.environ.get("FETCH_PAUSE", "1"))  # seconds between page requests, be polite to t.me


def get(c, url, tries=4):
    for n in range(tries):
        try:
            r = c.get(url)
            if r.status_code == 200:
                return r
            err = r.status_code
        except httpx.HTTPError as e:
            err = repr(e)
        time.sleep(5 * (n + 1))
    raise RuntimeError(f"{url}: {err}")


def text_of(fragment):
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse(page):
    """Posts of one t.me/s page, oldest first: {id, date, text, links, photos: [(message id, url)],
    videos: [(message id, mp4 url or None when "too big")]}."""
    posts = []
    for chunk in page.split('<div class="tgme_widget_message_wrap')[1:]:
        m = re.search(r'data-post="[^"/]+/(\d+)"', chunk)
        if not m:
            continue
        t = re.search(r'class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', chunk, re.S)
        d = re.search(r'<time datetime="([^"]+)"', chunk)
        photos = []
        for tag in re.findall(r'<a class="tgme_widget_message_photo_wrap[^>]*>', chunk):
            url = re.search(r"background-image:url\('([^']+)'\)", tag)
            pid = re.search(r'href="https://t\.me/[^/]+/(\d+)', tag)
            if url and pid:
                photos.append((int(pid.group(1)), url.group(1)))
        videos = []
        for v in re.split(r'(?=<a class="tgme_widget_message_video_player)', chunk)[1:]:
            vid = re.search(r'href="https://t\.me/[^/]+/(\d+)', v[:2000])
            src = re.search(r'<video[^>]*\ssrc="([^"]+)"', v.split("</a>", 1)[0])
            if vid:
                videos.append((int(vid.group(1)), html.unescape(src.group(1)) if src else None))
        links = [html.unescape(html.unescape(u)) for u in re.findall(r'href="([^"]+)"', t.group(1))] if t else []
        posts.append({"id": int(m.group(1)), "date": d.group(1) if d else None, "text": text_of(t.group(1)) if t else "",
                      "links": [u for u in links if u.startswith("http")], "photos": photos, "videos": videos})
    return posts


def download(c, url, path):
    if os.path.exists(path):
        return True
    try:
        r = c.get(url)
        r.raise_for_status()
    except httpx.HTTPError:
        return False
    open(path, "wb").write(r.content)
    return True


def video_of(c, user, kind, p, old):
    """At most one video per post -> {path[, w, h, duration]} or None."""
    for vid, src in p["videos"][:1] if os.environ.get("FETCH_VIDEOS", "1") != "0" else []:
        name = f"tg_{vid}" if kind == "agency" else f"tg_{user}_{vid}"
        path = f"{OUT}/video/{name}.mp4"
        if src and download_video(src, path, c) or os.path.exists(path):
            side = path[:-4] + ".json"  # w/h/duration written by fetch_channel.py (Telethon) or by an earlier run
            meta = json.load(open(side)) if os.path.exists(side) else (old.get(p["id"]) or {}).get("video") or {}
            return {**{k: v for k, v in meta.items() if k in ("w", "h", "duration")}, "path": path}
    return None


def fetch(c, user, kind, want, photos, scan, marker=None, old=None):
    """Newest-first walk over the web preview until `want` posts with text and photos or `scan` messages."""
    items, before, newest = [], None, None
    while len(items) < want:
        page = get(c, f"https://t.me/s/{user}" + (f"?before={before}" if before else "")).text
        posts = parse(page)
        if not posts:
            break
        newest = newest or posts[-1]["id"]
        for p in reversed(posts):
            if len(items) >= want:
                break
            if not (p["text"] and p["photos"]) or (marker and marker not in p["text"]):
                continue
            paths = []
            for pid, url in sorted(p["photos"])[:photos]:
                # same file names as fetch_channel.py, so the photo/index caches stay valid
                path = f"{OUT}/img/tg_{pid}.jpg" if kind == "agency" else f"{OUT}/img/tg_{user}_{pid}.jpg"
                if download(c, url, path):
                    paths.append(path)
            if paths:
                video = video_of(c, user, kind, p, old or {})
                items.append({"id": p["id"], "channel": user, "kind": kind, "date": p["date"], "text": p["text"],
                              "photos": paths, "url": f"https://t.me/{user}/{p['id']}", "links": p["links"],
                              **({"video": video} if video else {}),
                              # the video's own message id (any size): the bot forwards it once to get a file_id
                              **({"video_msg": p["videos"][0][0]} if p["videos"] else {})})
        oldest = posts[0]["id"]
        if before is not None and oldest >= before or newest - oldest >= scan:
            break
        before = oldest
        time.sleep(PAUSE)
    return items


def main():
    os.chdir(ROOT)
    os.makedirs(f"{OUT}/img", exist_ok=True)
    os.makedirs(f"{OUT}/video", exist_ok=True)
    cfg = json.load(open("sources.json"))
    only = {u.strip().lower() for u in os.environ.get("FETCH_ONLY", "").split(",") if u.strip()}
    failed = 0
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        for ch in cfg["telegram_channels"]:
            user = ch["username"]
            if only and user.lower() not in only:
                continue
            o = {**cfg.get("telegram_defaults", {}), **ch}
            want = int(os.environ.get("FETCH_WANT") or o["want"])
            photos = int(os.environ.get("FETCH_PHOTOS") or o["photos"])
            path = f"{OUT}/channel_{user}.json"
            old = {i["id"]: i for i in json.load(open(path))} if os.path.exists(path) else {}
            try:
                items = fetch(c, user, ch["kind"], want, photos, int(o["scan"]), o.get("caption_marker"), old)
            except Exception as e:  # one broken channel keeps its previous file, the others still update
                print(f"{user}: failed, keeping old file: {e}")
                failed += 1
                continue
            if not items:
                print(f"{user}: nothing found (private channel or web preview off?), keeping old file")
                continue
            json.dump(items, open(f"{OUT}/channel_{user}.json", "w"), ensure_ascii=False)
            ds = sorted(i["date"] for i in items if i["date"])
            print(f"{user} ({ch['kind']}): saved {len(items)} posts, {ds[0][:10] if ds else '?'} .. {ds[-1][:10] if ds else '?'}, "
                  f"videos {sum('video' in i for i in items)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
