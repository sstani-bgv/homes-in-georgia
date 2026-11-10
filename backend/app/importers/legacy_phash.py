"""Perceptual hashes for a list of bucket keys, fetched in batches; resumable (phash.jsonl)."""
import json, os, shutil, subprocess, sys
from multiprocessing import Pool
from pathlib import Path
import imagehash, pillow_heif
from PIL import Image
pillow_heif.register_heif_opener()
LIST, OUT, TMP = sys.argv[1], Path("/opt/media/phash.jsonl"), Path("/opt/media/ph")

def h(key):
    try:
        with Image.open(TMP / key) as im:
            im.draft("RGB", (512, 512))  # fast JPEG decode at reduced size
            return {"key": key, "phash": str(imagehash.phash(im.convert("RGB")))}
    except Exception as e:
        return {"key": key, "error": repr(e)[:120]}

done = {json.loads(l)["key"] for l in open(OUT)} if OUT.exists() else set()
keys = [k for k in open(LIST).read().split() if k not in done]
print("todo", len(keys), flush=True)
with Pool(4) as pool:
    for i in range(0, len(keys), 1500):
        batch = keys[i:i + 1500]
        (TMP).mkdir(exist_ok=True)
        Path("/opt/media/ph_batch.txt").write_text("\n".join(batch) + "\n")
        subprocess.run(["rclone", "copy", "hz:homes-in-georgia-media/legacy", str(TMP), "--files-from",
                        "/opt/media/ph_batch.txt", "--transfers", "32", "--disable-http2", "-q"], check=False)
        with open(OUT, "a") as f:
            for r in pool.imap_unordered(h, batch, chunksize=20):
                f.write(json.dumps(r) + "\n")
        shutil.rmtree(TMP)
        print(i + len(batch), "/", len(keys), flush=True)
