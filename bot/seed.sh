#!/bin/bash
# Packs the current local base into /tmp/seed.tgz for the server (start.sh unpacks it into empty volumes):
#   data/listings.json, raw/{index,geo}_cache.json, raw/dropped_agents.json, the raw source files
#   (ss, myhome + their detail caches, channel_*, facebook*) and every photo they reference (+ the 640px copies for the model);
#   photos over 1280px are downscaled to 1280. SEED_VIDEOS=1: also every referenced raw/video/*.mp4 (+ .json sidecars),
#   ~15 MB each (the server can't fetch agency videos itself: t.me/s says "Media is too big").
# Absolute Mac paths are rewritten to relative raw/img/... Usage: [SEED_VIDEOS=1] ./seed.sh [out.tgz]
set -euo pipefail
cd "$(dirname "$0")"
OUT=${1:-/tmp/seed.tgz}
PY=${PY:-.venv/bin/python}
STAGE=$(mktemp -d /tmp/seed-stage.XXXX)
trap 'rm -rf "$STAGE"' EXIT
"$PY" - "$PWD" "$STAGE" <<'PYEOF'
import glob, json, os, shutil, sys
from PIL import Image
root, stage = sys.argv[1], sys.argv[2]
os.makedirs(f"{stage}/data"); os.makedirs(f"{stage}/raw/img"); os.makedirs(f"{stage}/raw/video")
videos = set()
files = {"listings.json": "data/listings.json"}
for f in ["raw/index_cache.json", "raw/geo_cache.json", "raw/dropped_agents.json", "raw/ss.json", "raw/myhome.json",
          "raw/ss_details.json", "raw/myhome_details.json", "raw/facebook.json", "raw/facebook_apify.json", *glob.glob("raw/channel_*.json")]:
    if os.path.exists(f):
        files[f] = f
photos = set()
def rel(p):
    return "raw/img/" + os.path.basename(p) if "raw/img/" in p else p
def walk(x):
    if isinstance(x, dict):
        return {k: walk(v) for k, v in x.items()}
    if isinstance(x, list):
        return [walk(v) for v in x]
    if isinstance(x, str) and "raw/video/" in x and x.endswith(".mp4"):
        videos.add("raw/video/" + os.path.basename(x))
        return "raw/video/" + os.path.basename(x)
    if isinstance(x, str) and "raw/img/" in x and x.endswith(".jpg"):
        photos.add(rel(x))
        return rel(x)
    return x
for src, dst in files.items():
    data = walk(json.load(open(src)))
    json.dump(data, open(f"{stage}/{dst}", "w"), ensure_ascii=False)
n = 0
for p in sorted(photos):
    for q in (p, p.replace(".jpg", "_640.jpg")):
        if os.path.exists(q):
            im = Image.open(q)
            if max(im.size) > 1280:  # Telegram shows photos at <=1280px anyway; halves the seed
                im.convert("RGB").resize((round(im.width * 1280 / max(im.size)), round(im.height * 1280 / max(im.size)))).save(f"{stage}/{q}", "JPEG", quality=85)
            else:
                shutil.copy2(q, f"{stage}/{q}")
            n += 1
v = 0
if os.environ.get("SEED_VIDEOS") == "1":
    for p in sorted(videos):
        for q in (p, p[:-4] + ".json"):
            if os.path.exists(q):
                shutil.copy2(q, f"{stage}/{q}")
                v += q.endswith(".mp4")
print(f"seed: {len(files)} json files, {len(photos)} referenced photos, {n} image files, videos {v}/{len(videos)}")
PYEOF
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf "$OUT" -C "$STAGE" data raw   # no macOS ._ files/xattrs
echo "seed: $OUT $(du -h "$OUT" | cut -f1)"
