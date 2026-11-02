# Run via: tg --account main fetch_channel.py   (read-only; `client` is injected by the tg runtime)
# Reads every channel from sources.json -> raw/channel_<username>.json (+ photos in raw/img/).
# A listing = an album (photos share grouped_id) or a single photo post with a caption.
# Also kept per post: "links" (URLs behind the text, e.g. the agency's Google Maps link = exact pin) and at most one
# "video" {path, w, h, duration}: the largest mp4 rendition (the original or one of Telegram's lower qualities) under
# VIDEO_MAX_MB (20), saved as raw/video/<photo-style name>.mp4; bigger ones are skipped.
# Env for quick tests: FETCH_ONLY=user1,user2  FETCH_WANT=5  FETCH_PHOTOS=1  FETCH_VIDEOS=0 (no video downloads)
import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
OUT = os.path.join(ROOT, "raw")
os.makedirs(f"{OUT}/img", exist_ok=True)
os.makedirs(f"{OUT}/video", exist_ok=True)
CAP = int(float(os.environ.get("VIDEO_MAX_MB", "20")) * 1024 * 1024)
VIDEOS = os.environ.get("FETCH_VIDEOS", "1") != "0"


def links_of(m):
    """URLs behind the text: text links (entity.url) and plain URLs."""
    out = [e.url for e in (m.entities or []) if getattr(e, "url", None)]
    return out + re.findall(r"https?://\S+", m.message or "")


def best_video(m):
    """Largest mp4 (the document itself or a lower-quality alt document) under the cap -> (document, attr) or None."""
    docs = [m.document] + list(getattr(m.media, "alt_documents", None) or [])
    ok = []
    for d in docs:
        attr = next((a for a in d.attributes if a.__class__.__name__ == "DocumentAttributeVideo"), None)
        if attr and d.mime_type == "video/mp4" and d.size <= CAP:
            ok.append((d.size, d, attr))
    return max(ok, key=lambda x: x[0])[1:] if ok else None


cfg = json.load(open(os.path.join(ROOT, "sources.json")))
only = {u.strip().lower() for u in os.environ.get("FETCH_ONLY", "").split(",") if u.strip()}

for ch in cfg["telegram_channels"]:
    user, kind = ch["username"], ch["kind"]
    if only and user.lower() not in only:
        continue
    o = {**cfg.get("telegram_defaults", {}), **ch}
    want = int(os.environ.get("FETCH_WANT") or o["want"])
    photos = int(os.environ.get("FETCH_PHOTOS") or o["photos"])
    marker = o.get("caption_marker")  # agency: the caption is the message with 💰

    albums = {}
    async for m in client.iter_messages(user, limit=o["scan"]):  # iter_messages does not mark read
        key = m.grouped_id or m.id
        a = albums.setdefault(key, {"text": "", "photos": [], "videos": [], "links": [], "id": m.id, "date": m.date})
        if m.message and (marker in m.message if marker else len(m.message) > len(a["text"])):
            a["text"], a["id"], a["date"], a["links"] = m.message, m.id, m.date, links_of(m)
        if m.photo:
            a["photos"].append(m)
        elif m.video:
            a["videos"].append(m)
        if sum(1 for x in albums.values() if x["text"] and x["photos"]) >= want:
            break

    items = []
    for a in albums.values():
        if not (a["text"] and a["photos"]):
            continue
        paths = []
        for p in sorted(a["photos"], key=lambda x: x.id)[:photos]:
            # agency keeps the old file names so the photo/index caches stay valid
            path = f"{OUT}/img/tg_{p.id}.jpg" if kind == "agency" else f"{OUT}/img/tg_{user}_{p.id}.jpg"
            if not os.path.exists(path):
                await client.download_media(p, file=path)
            paths.append(path)
        video = None
        for v in sorted(a["videos"], key=lambda x: x.id)[:1] if VIDEOS else []:
            name = f"tg_{v.id}" if kind == "agency" else f"tg_{user}_{v.id}"
            path, pick = f"{OUT}/video/{name}.mp4", best_video(v)
            if pick:
                if not os.path.exists(path):
                    await client.download_media(pick[0], file=path + ".part")
                    os.replace(path + ".part", path)
                video = {"path": f"raw/video/{name}.mp4", "w": pick[1].w, "h": pick[1].h, "duration": int(pick[1].duration)}
                json.dump(video, open(f"{OUT}/video/{name}.json", "w"))  # sidecar: fetch_channel_web.py reuses the meta
        items.append({"id": a["id"], "channel": user, "kind": kind, "date": a["date"].isoformat(),
                      "text": a["text"], "photos": paths, "url": f"https://t.me/{user}/{a['id']}", "links": a["links"],
                      **({"video": video} if video else {}),
                      **({"video_msg": min(v.id for v in a["videos"])} if a["videos"] else {})})

    json.dump(items, open(f"{OUT}/channel_{user}.json", "w"), ensure_ascii=False)
    a_dates = sorted((a["date"] for a in albums.values() if a["text"] and a["photos"]), reverse=True)
    span = (a_dates[0] - a_dates[-1]).total_seconds() / 86400 if a_dates else 0
    print(f"{user} ({kind}): saved {len(items)} posts over {span:.1f} days, videos {sum('video' in i for i in items)}")
