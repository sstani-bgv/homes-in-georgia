"""Download the legacy media plan (legacy_objects → media_plan.jsonl) from Telegram, resumable.

    python legacy_media.py PLAN OUTDIR [--workers 4] [--min-free-gb 15]
    env: TG_API_ID, TG_API_HASH, TG_SESSION

Files land in OUTDIR/files/<group>/<msg>.<ext>; each finished file is appended to OUTDIR/done.jsonl as
{where, object, key, bytes, sha256} (or {where, error}) — that line is the object ↔ file link, and the
resume point. Live objects first, then photos, then optional videos. Waits while the disk is nearly full
(an `rclone move` of OUTDIR/files to S3 frees it). Standalone: needs only telethon (+ cryptg for speed).
"""
import argparse
import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

from telethon import TelegramClient, errors
import telethon.client.downloads as _tl_downloads
import telethon.utils as _tl_utils

# Telegram rate-limits GetFile requests per account (~1 s flood waits), so bytes/s = requests/s × chunk size:
# take the largest chunk the API allows (1 MiB) instead of Telethon's 128 KiB default.
CHUNK_KB = int(os.environ.get("TG_CHUNK_KB", "1024"))
_tl_downloads.MAX_CHUNK_SIZE = CHUNK_KB * 1024
_tl_utils.get_appropriated_part_size = lambda _size: CHUNK_KB

FLOODS = {"premium": 0, "plain": 0}  # which limit Telegram applies (FLOOD_PREMIUM_WAIT = treated as non-premium)
for _cls, _k in ((errors.FloodPremiumWaitError, "premium"), (errors.FloodWaitError, "plain")):
    def _counting(self, *args, _orig=_cls.__init__, _k=_k, **kw):
        FLOODS[_k] += 1
        _orig(self, *args, **kw)
    _cls.__init__ = _counting

PEERS = {"recruiting-team-chat": -1002252468572, "team-chat": -1001933642420, "channel": "rent_tbilisi_ge"}  # public; may be absent from dialogs


def ext_of(r: dict) -> str:
    if r["name"] and Path(r["name"]).suffix:
        return Path(r["name"]).suffix.lower()
    if r["type"] == "photo":
        return ".jpg"
    return mimetypes.guess_extension(r["mime"] or "") or ".bin"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--min-free-gb", type=float, default=15)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")  # shows flood waits
    out = Path(a.out)
    (out / "files").mkdir(parents=True, exist_ok=True)
    done_f = out / "done.jsonl"
    # resume: skip finished files and deleted posts; other failures are retried on the next start
    done, oldest = set(), {}
    if done_f.exists():
        for line in open(done_f):
            d = json.loads(line)
            if "key" in d or d.get("error") == "missing":
                done.add(d["where"])
            if "key" in d:
                g, m = d["where"].split("/")
                if g != "channel" and int(m) < oldest.get(g, 1 << 62):
                    oldest[g] = int(m)  # oldest message known to exist (probe for history access)

    plan = [json.loads(line) for line in open(a.plan)]
    vid = lambda r: r["type"] == "video" or (r["mime"] or "").startswith("video")
    plan.sort(key=lambda r: (bool(r.get("optional")), vid(r)))  # stable: keeps per-object order
    todo = [r for r in plan if r["where"] not in done]
    total_gb = sum(r["size"] or 0 for r in todo) / 1e9
    print(f"plan {len(plan)}, done {len(done)}, todo {len(todo)} ({total_gb:.1f} GB)", flush=True)

    # Telegram caps download speed per account (~4 MB/s), so each logged-in account takes its own share
    accounts = []
    for path in os.environ.get("TG_SESSIONS", os.environ.get("TG_SESSION", "")).split(","):
        cl = TelegramClient(path, int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"], flood_sleep_threshold=3600)
        await cl.connect()
        if not await cl.is_user_authorized():
            print(f"skip {path}: not logged in", flush=True)
            continue
        await cl.get_dialogs()  # fills the entity cache for the peers
        try:
            peers = {g: await cl.get_entity(pid) for g, pid in PEERS.items()}
        except ValueError:
            print(f"skip {path}: not a member of both groups", flush=True)
            continue
        # a fresh member may not see old history; such an account would mark files "missing" by mistake
        probes = [await cl.get_messages(peers[g], ids=m) for g, m in oldest.items()]
        if not all(probes):
            print(f"skip {path}: cannot see old group history", flush=True)
            continue
        accounts.append((cl, peers))
        print(f"account {(await cl.get_me()).username}", flush=True)

    lock = asyncio.Lock()
    stat = {"n": 0, "bytes": 0, "t0": time.time(), "err": 0}

    async def record(row: dict):
        async with lock:
            with open(done_f, "a") as f:
                f.write(json.dumps(row) + "\n")

    async def fetch(r: dict, msg, cl, peers):
        g = r["where"].split("/")[0]
        rel = Path(g) / f"{r['msg']}{ext_of(r)}"
        dst = out / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        while shutil.disk_usage(out).free < a.min_free_gb * 1e9:
            await asyncio.sleep(60)
        if msg is None or not msg.media:
            stat["err"] += 1
            return await record({"where": r["where"], "object": r["object"], "error": "missing"})
        part = dst.with_name(dst.name + ".part")
        for attempt in range(3):
            try:
                await cl.download_media(msg, file=str(part))
                break
            except errors.FileReferenceExpiredError:
                msg = await cl.get_messages(peers[g], ids=r["msg"])
            except (OSError, errors.RPCError) as e:
                if attempt == 2:
                    stat["err"] += 1
                    return await record({"where": r["where"], "object": r["object"], "error": repr(e)[:200]})
                await asyncio.sleep(10)
        n = part.stat().st_size
        digest = sha256(part)
        part.rename(dst)
        stat["n"] += 1
        stat["bytes"] += n
        await record({"where": r["where"], "object": r["object"], "key": str(rel), "bytes": n, "sha256": digest})

    async def report():
        while True:
            await asyncio.sleep(60)
            dt = time.time() - stat["t0"]
            gb = stat["bytes"] / 1e9
            print(f"{time.strftime('%H:%M')} files {stat['n']}/{len(todo)} err {stat['err']} "
                  f"{gb:.1f}/{total_gb:.1f} GB {stat['bytes'] / dt / 1e6:.1f} MB/s floods {FLOODS}", flush=True)

    rep = asyncio.create_task(report())

    async def producer(q, cl, peers, todo):
        # messages are fetched in batches of 100, shortly before their files download
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            by_g = defaultdict(list)
            for r in batch:
                by_g[r["where"].split("/")[0]].append(r)
            for g, rs in by_g.items():
                got = await cl.get_messages(peers[g], ids=[r["msg"] for r in rs])
                for r, m in zip(rs, got):
                    await q.put((r, m))
        for _ in range(a.workers):
            await q.put(None)

    async def worker(q, cl, peers):
        while (item := await q.get()) is not None:
            try:
                await fetch(*item, cl, peers)
            except Exception as e:  # one bad file must not stop the run (and must not loop on restart)
                stat["err"] += 1
                await record({"where": item[0]["where"], "object": item[0]["object"], "error": repr(e)[:200]})

    jobs = []
    for k, (cl, peers) in enumerate(accounts):
        q: asyncio.Queue = asyncio.Queue(maxsize=400)
        jobs += [producer(q, cl, peers, todo[k::len(accounts)])] + [worker(q, cl, peers) for _ in range(a.workers)]
    await asyncio.gather(*jobs)
    rep.cancel()
    print(f"finished: files {stat['n']} err {stat['err']} {stat['bytes'] / 1e9:.1f} GB", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
