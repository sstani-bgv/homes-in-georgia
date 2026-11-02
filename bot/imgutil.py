"""Media files: image -> jpeg with the long side capped (macOS `sips` when present, it reads HEIC; Pillow elsewhere),
listing videos downloaded under a size cap."""
import os
import shutil
import subprocess


def to_jpeg(src, out, side):
    if shutil.which("sips"):
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(side), src, "--out", out], capture_output=True)
        return
    try:
        from PIL import Image
        im = Image.open(src).convert("RGB")
        im.thumbnail((side, side))
        im.save(out, "JPEG", quality=88)
    except Exception:  # broken or unsupported file: no output, callers check os.path.exists like with sips
        pass


VIDEO_DIR = "raw/video"


def video_max():
    """Size cap for listing videos, bytes (env VIDEO_MAX_MB, default 20; Telegram bots may upload up to 50 MB)."""
    return int(float(os.environ.get("VIDEO_MAX_MB", "20")) * 1024 * 1024)


def download_video(url, path, client=None, cap=None):
    """mp4 URL -> path, streamed and abandoned past the cap. -> path or None. An existing file is reused."""
    import httpx
    if os.path.exists(path):
        return path
    cap = cap or video_max()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    c = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Chrome/141.0"})
    tmp, size = path + ".part", 0
    try:
        with c.stream("GET", url) as r:
            if r.status_code != 200 or int(r.headers.get("content-length") or 0) > cap:
                return None
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    size += len(chunk)
                    if size > cap:
                        return None
                    f.write(chunk)
        os.replace(tmp, path)
        return path
    except httpx.HTTPError:
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
        if client is None:
            c.close()


def local_video(path):
    """A downloaded listing video + its sidecar meta (<name>.json: w/h/duration) -> {"path", ...} or None."""
    if not os.path.exists(path):
        return None
    side = path[:-4] + ".json"
    meta = {}
    if os.path.exists(side):
        import json
        try:
            meta = {k: v for k, v in json.load(open(side)).items() if k in ("w", "h", "duration")}
        except ValueError:
            pass
    return {"path": path, **meta}
