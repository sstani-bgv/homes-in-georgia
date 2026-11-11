"""Local multimodal embeddings for the hybrid search (core.py, setting search_mode=hybrid). Free, runs on this Mac.

Model: multilingual SigLIP (google/siglip-base-patch16-256-multilingual): photos and multilingual text (ru/en) in one vector space.
Per listing: every photo, the mean of its photos, and its profile text (style, renovation, light, looks...).
Vectors live next to the DB in vectors.npz (float32, L2-normalized), keyed by listing id; a fingerprint
(model + photo paths + profile text) means only new or changed listings get embedded, and gone ones drop out.

Usage: python embed.py           # incremental, after `db.py sync` (refresh.sh does it)
       python embed.py --full    # re-embed everything
"""
import hashlib
import json
import os
import sys
import threading
import time

import numpy as np

import db

MODEL = os.environ.get("EMB_MODEL", "google/siglip-base-patch16-256-multilingual")
_lock, _run = threading.Lock(), threading.Lock()
_model = None
_store = {"mtime": None, "path": None, "data": None}
_img_cache = {}


def path():
    """vectors.npz in the bot's data dir (tests and the eval point db.DATA_DIR / EMB_PATH at /tmp copies)."""
    return os.environ.get("EMB_PATH") or os.path.join(db.DATA_DIR, "vectors.npz")


# ---------- model ----------

def _load():
    global _model
    with _lock:
        if _model is None:
            import torch
            from transformers import AutoModel, AutoProcessor
            dev = "mps" if torch.backends.mps.is_available() else "cpu"
            _model = (AutoModel.from_pretrained(MODEL).to(dev).eval(), AutoProcessor.from_pretrained(MODEL), dev)
    return _model


def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def _small(p):
    s = p.replace(".jpg", "_640.jpg")  # ingest's 640px copy decodes 4x faster; the model sees 224px anyway
    return s if os.path.exists(s) else p


def encode_images(paths, batch=32):
    import torch
    from PIL import Image
    try:  # HEIC originals from the old base (pillow-heif is a backend dependency)
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    def opened(p):
        try:
            return Image.open(_small(p)).convert("RGB")
        except Exception as e:  # a broken file must not kill the whole run
            print(f"embed: skip unreadable {os.path.basename(p)}: {e}")

    m, pr, dev = _load()
    out = []
    for k in range(0, len(paths), batch):
        ims = [opened(p) for p in paths[k:k + batch]]
        ok = [i for i in ims if i is not None]
        f = np.zeros((len(ims), m.config.vision_config.hidden_size), np.float32)  # unreadable -> zero row, order kept
        if ok:
            with _run, torch.no_grad():  # one model call at a time: the bot searches in several threads
                v = m.get_image_features(**pr(images=ok, return_tensors="pt").to(dev))
            f[[n for n, i in enumerate(ims) if i is not None]] = getattr(v, "pooler_output", v).float().cpu().numpy()
        out.append(f)
    return _norm(np.concatenate(out)) if out else np.zeros((0, 1), np.float32)


def encode_texts(texts, batch=64):
    import torch
    m, pr, dev = _load()
    out = []
    for k in range(0, len(texts), batch):  # SigLIP was trained on lowercased text padded to 64 tokens
        x = pr(text=[t.lower() for t in texts[k:k + batch]], padding="max_length", max_length=64,
               truncation=True, return_tensors="pt").to(dev)
        with _run, torch.no_grad():
            f = m.get_text_features(**x)
        out.append(getattr(f, "pooler_output", f).float().cpu().numpy())
    return _norm(np.concatenate(out)) if out else np.zeros((0, 1), np.float32)


def client_images(paths):
    """Client reference photos -> (k, d); cached in memory (the same photo is reused on every 'more')."""
    todo = [p for p in dict.fromkeys(paths) if p not in _img_cache and os.path.exists(p)]
    if todo:
        for p, v in zip(todo, encode_images(todo)):
            _img_cache[p] = v
    vs = [_img_cache[p] for p in paths if p in _img_cache]
    return np.stack(vs) if vs else None


def text(t):
    return encode_texts([t])[0] if t and t.strip() else None


# ---------- index ----------

def profile_text(i):
    return (f"{i.get('looks') or ''} {i.get('style') or ''}, {i.get('renovation') or ''}, {i.get('light') or ''}, "
            f"мебель {i.get('furniture') or ''}. {', '.join((i.get('features') or [])[:8])}").strip()


def fingerprint(i):
    return hashlib.sha1(json.dumps([MODEL, i.get("photos") or [], profile_text(i)], ensure_ascii=False).encode()).hexdigest()[:16]


def build(listings, old=None, enc_img=None, enc_txt=None):
    """listings -> store dict {ids, keys, img, txt, photo, owner}; rows of `old` with the same fingerprint are reused.
    enc_img / enc_txt are injectable for tests."""
    enc_img, enc_txt = enc_img or encode_images, enc_txt or encode_texts
    prev = {}
    if old is not None:
        for n, lid in enumerate(old["ids"]):
            prev[str(lid)] = (old["keys"][n], old["img"][n], old["txt"][n], old["photo"][old["owner"] == n])
    rows, todo = {}, []
    for i in listings:
        key = fingerprint(i)
        if i["id"] in prev and prev[i["id"]][0] == key:
            rows[i["id"]] = prev[i["id"]]
        else:
            todo.append((i, key))
    if todo:
        photos = [[p for p in (i.get("photos") or []) if os.path.exists(p)] for i, _ in todo]
        flat = enc_img([p for ps in photos for p in ps])
        txt = enc_txt([profile_text(i) for i, _ in todo])
        k = 0
        for (i, key), ps, t in zip(todo, photos, txt):
            ph = flat[k:k + len(ps)].reshape(-1, len(t))
            k += len(ps)
            mean = _norm(ph.mean(0)) if len(ph) else np.zeros_like(t)
            rows[i["id"]] = (key, mean, t, ph)
    ids = [i["id"] for i in listings if i["id"] in rows]
    d = len(next(iter(rows.values()))[2]) if rows else 1
    return {"ids": np.array(ids, dtype=str), "keys": np.array([rows[x][0] for x in ids], dtype=str),
            "img": np.array([rows[x][1] for x in ids], np.float32).reshape(-1, d),
            "txt": np.array([rows[x][2] for x in ids], np.float32).reshape(-1, d),
            "photo": np.concatenate([rows[x][3] for x in ids] or [np.zeros((0, d), np.float32)]).astype(np.float32),
            "owner": np.concatenate([np.full(len(rows[x][3]), n) for n, x in enumerate(ids)] or [np.zeros(0)]).astype(np.int32),
            "embedded": len(todo)}


def save(store, p=None):
    p = p or path()
    tmp = p + ".tmp.npz"
    np.savez(tmp, **{k: v for k, v in store.items() if k != "embedded"})
    os.replace(tmp, p)  # atomic: the bot may be reading it


def read(p=None):
    p = p or path()
    if not os.path.exists(p):
        return None
    with np.load(p) as z:
        return {k: z[k] for k in z.files}


def load():
    """Current vectors for search, reloaded when the file changes -> {"index": {id: n}, img, txt, photo, owner} or None."""
    p = path()
    mtime = os.path.getmtime(p) if os.path.exists(p) else None
    if (_store["path"], _store["mtime"]) != (p, mtime):
        s = read(p)
        if s is not None:
            s["index"] = {str(x): n for n, x in enumerate(s["ids"])}
        _store.update(path=p, mtime=mtime, data=s)
    return _store["data"]


def listings_to_embed():
    with db.tx() as c:  # pending/rejected too: an agent may publish them later without a new sync
        return [db.row(r) for r in c.execute("SELECT * FROM listings WHERE status != 'gone' ORDER BY id")]


def main(full=False):
    t0 = time.time()
    items = listings_to_embed()
    old = None if full else read()
    store = build(items, old)
    save(store)
    print(f"embed: {len(store['ids'])} listings, {store['embedded']} embedded now, {len(store['photo'])} photos, "
          f"{time.time() - t0:.0f}s -> {path()}")


if __name__ == "__main__":
    db.load_env()
    main(full="--full" in sys.argv)
