"""Listing video links (ss.ge HLS m3u8, YouTube, ...) -> local mp4 for the album, with yt-dlp. A refresh.sh step after
ingest.py and before `db.py sync`: every listings.json item with details.video_url and no local video gets
raw/video/<listing id>.mp4 (+ <id>.json with w/h/duration, the same sidecar as the Telegram videos) in details.videos.

Limits: at most 720p, at most VIDEO_MAX_MB (20): formats known to be under the cap first, unknown sizes are tried and a
file over the cap is thrown away. One video per listing. A file already there is reused (ingest.py picks it up too).
A link that fails (YouTube "sign in to confirm you're not a bot" on server IPs, geo blocks, too big, gone) goes to
raw/video/failed.json and is retried only after VIDEO_RETRY_DAYS (3); the client keeps the "🎥 Видео" button for it.
HLS (ss.ge) needs ffmpeg to end up as mp4; without ffmpeg only ready mp4 files are taken.
Env: VIDEO_MAX_MB (20), VIDEO_RETRY_DAYS (3), VIDEO_MAX_PER_RUN (30 downloads per run).
Usage: python fetch_videos.py [listings.json]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from imgutil import VIDEO_DIR, local_video, video_max

FAILED = f"{VIDEO_DIR}/failed.json"


def fmt(cap):
    """Single-file formats (video+audio, no merge) <= 720p, sizes under the cap or unknown; mp4 first."""
    size = f"[filesize<?{cap}][filesize_approx<?{cap}]"
    # YouTube has no single-file 720p any more: with ffmpeg merge h264 video + m4a audio (h264 plays everywhere, AV1 not)
    merge = (f"/bv*[ext=mp4][vcodec^=avc1][height<=720]{size}+ba[ext=m4a]/bv*[ext=mp4][height<=720]{size}+ba[ext=m4a]"
             if shutil.which("ffmpeg") else "")
    return f"best[ext=mp4][vcodec^=avc1][height<=?720]{size}/best[ext=mp4][height<=?720]{size}/best[height<=?720]{size}" + merge


def ytdlp(url, out_dir, cap):
    """Download one video into out_dir -> (file path, info). Raises on any failure."""
    import yt_dlp
    opts = {"format": fmt(cap), "outtmpl": f"{out_dir}/v.%(ext)s", "max_filesize": cap, "noplaylist": True,
            "quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30, "retries": 2,
            "merge_output_format": "mp4"}
    if shutil.which("ffmpeg"):  # HLS / webm -> mp4 container (no re-encode)
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    files = [os.path.join(out_dir, f) for f in os.listdir(out_dir) if not f.endswith((".part", ".ytdl"))]
    if not files:
        raise RuntimeError("nothing downloaded (over the size cap?)")
    return max(files, key=os.path.getsize), info


def probe(path):
    """w/h of the first video stream via ffprobe -> {} when ffprobe is missing or fails."""
    if not shutil.which("ffprobe"):
        return {}
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
    try:
        w, h = map(int, r.stdout.strip().split(",")[:2])
        return {"w": w, "h": h}
    except ValueError:
        return {}


def fetch(url, path, cap, downloader=ytdlp):
    """url -> path (mp4 under cap) + sidecar meta. -> local_video dict. Raises with a short reason on failure."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(path)) as d:
        f, info = downloader(url, d, cap)
        if not f.endswith(".mp4"):
            raise RuntimeError(f"not an mp4: {os.path.basename(f)}")
        if os.path.getsize(f) > cap:
            raise RuntimeError(f"too big: {os.path.getsize(f) >> 20} MB")
        os.replace(f, path)
    meta = {"w": info.get("width"), "h": info.get("height"), "duration": int(info["duration"]) if info.get("duration") else None}
    if not (meta["w"] and meta["h"]):  # HLS (ss.ge) has no size in the playlist: read the file
        meta.update(probe(path))
    json.dump({k: v for k, v in meta.items() if v}, open(path[:-4] + ".json", "w"))
    return local_video(path)


def run(listings="listings.json", downloader=ytdlp, now=None, failed_path=FAILED):
    now = now or time.time()
    listings = os.path.realpath(listings)  # the container's listings.json is a symlink into data/: write its target
    os.makedirs(os.path.dirname(failed_path) or ".", exist_ok=True)
    cap = video_max()
    retry = float(os.environ.get("VIDEO_RETRY_DAYS", "3")) * 86400
    budget = int(os.environ.get("VIDEO_MAX_PER_RUN", "30"))
    items = json.load(open(listings))
    failed = json.load(open(failed_path)) if os.path.exists(failed_path) else {}
    stats = {"reused": 0, "ok": 0, "failed": 0, "skipped": 0}
    for it in items:
        d = it.get("details") or {}
        url = d.get("video_url")
        if not url or any(os.path.exists(v.get("path") or "") for v in d.get("videos") or []):
            continue
        path = f"{VIDEO_DIR}/{it['id']}.mp4"
        if os.path.exists(path):
            v, stats["reused"] = local_video(path), stats["reused"] + 1
        elif url in failed and now - failed[url]["at"] < retry or budget <= 0:
            stats["skipped"] += 1
            continue
        else:
            budget -= 1
            try:
                v = fetch(url, path, cap, downloader)
                failed.pop(url, None)
                stats["ok"] += 1
                print(f"video {it['id']}: {os.path.getsize(path) / 2**20:.1f} MB {v.get('w')}x{v.get('h')} <- {url}")
            except Exception as e:  # yt-dlp DownloadError (bot check, geo, 404), too big, no mp4
                failed[url] = {"at": now, "id": it["id"], "error": str(e).strip().splitlines()[-1][:300] if str(e).strip() else repr(e)}
                stats["failed"] += 1
                print(f"video {it['id']} failed: {failed[url]['error']}")
                continue
        it["details"] = {**d, "videos": [v]}
    json.dump(failed, open(failed_path + ".tmp", "w"), ensure_ascii=False, indent=1)
    os.replace(failed_path + ".tmp", failed_path)
    if stats["ok"] or stats["reused"]:
        json.dump(items, open(listings + ".tmp", "w"), ensure_ascii=False, indent=1)
        os.replace(listings + ".tmp", listings)
    print("videos:", stats)
    return stats


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run(*sys.argv[1:2])
