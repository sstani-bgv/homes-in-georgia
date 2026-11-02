"""Search quality eval: search_mode=llm (whole catalog to Codex) vs hybrid (final_llm on) vs hybrid (final_llm off).

Run from the project root:  .venv/bin/python test/eval_search.py [--only q01,q07]
Works on a /tmp copy (DB from listings.json, its own vectors.npz and geo cache); never touches data/app.db or .env.
Real Codex calls (~4 per query) are cached in /tmp/eval_search/ask_cache.jsonl, so a rerun is free.
Writes proof/eval/<query>.png (5 photos + price per mode, side by side), proof/eval/results.json and the tables of
docs/eval-search.md (between the eval:begin/end markers; the text around them is written by hand).
--report: only re-render the sheets and tables from proof/eval/results.json."""
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W = Path("/tmp/eval_search")
W.mkdir(parents=True, exist_ok=True)
os.environ.update(ENV_FILE=os.devnull, APP_DB=str(W / "app.db"), EMB_PATH=str(W / "vectors.npz"),
                  PATH=f"/opt/homebrew/bin:{os.environ['PATH']}")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import geo  # noqa: E402
geo.CACHE_PATH = str(W / "geo_cache.json")  # the live cache stays untouched
if not os.path.exists(geo.CACHE_PATH) and os.path.exists(ROOT / "raw/geo_cache.json"):
    shutil.copy(ROOT / "raw/geo_cache.json", geo.CACHE_PATH)
geo._cache = json.load(open(geo.CACHE_PATH)) if os.path.exists(geo.CACHE_PATH) else {}

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import core  # noqa: E402
import db  # noqa: E402
import embed  # noqa: E402
import llm  # noqa: E402

PROOF = ROOT / "proof" / "eval"
CACHE = W / "ask_cache.jsonl"
MODES = [("llm", "llm", "on"), ("hybrid", "hybrid", "on"), ("hybrid_off", "hybrid", "off")]

# ---------- Codex with a disk cache and accounting ----------

_cache, _secs = {}, {}
if CACHE.exists():
    for ln in CACHE.read_text().splitlines():
        r = json.loads(ln)
        _cache[r["k"]] = r["out"]
        _secs[r["k"]] = r.get("secs")
LOG = []  # per call: kind, chars, images, secs, cached


def ask(prompt, schema, images=(), timeout=300, **kw):
    k = hashlib.sha1(json.dumps([prompt, schema, [hashlib.sha1(open(i, "rb").read()).hexdigest() for i in images]],
                                ensure_ascii=False).encode()).hexdigest()
    t0, cached = time.time(), k in _cache
    if not cached:
        out = llm.ask(prompt, schema, images=images, timeout=timeout, **kw)
        _cache[k], _secs[k] = out, round(time.time() - t0, 1)
        with open(CACHE, "a") as f:
            f.write(json.dumps({"k": k, "out": out, "secs": _secs[k]}, ensure_ascii=False) + "\n")
    secs = time.time() - t0
    kind = "pick" if "top" in schema["properties"] else "query" if "bedrooms_min" in schema["properties"] else "near"
    LOG.append({"kind": kind, "chars": len(prompt), "images": len(images), "secs": round(secs, 1), "cached": cached,
                "live_secs": _secs.get(k),  # how long the real Codex call took (recorded when it was made)
                "ts": time.time()})
    return _cache[k]


core.ask = ask
core.translate = lambda listings: None  # same in every mode, not what we measure

# ---------- setup ----------


def setup():
    db.PATH = os.environ["APP_DB"]
    if not os.path.exists(db.PATH):
        db.init()
        print("sync:", db.sync(str(ROOT / "listings.json"), str(ROOT / "raw/dropped_agents.json")))
    embed.main()  # incremental: the first run embeds the base (~2 min), reruns are instant


def ref_photo(lid, n, crop=False):
    """A listing photo as a client would send it; crop=True: 75% center crop, 512px (a screenshot, not the file)."""
    src = core.listing(lid)["photos"][n]
    if not crop:
        return src
    out = W / f"ref_{lid}_{n}.jpg"
    if not out.exists():
        im = Image.open(src).convert("RGB")
        w, h = im.size
        im.crop((w // 8, h // 8, w - w // 8, h - h // 8)).resize((512, int(512 * h / w))).save(out, quality=80)
    return str(out)


def pick_listing(style):
    with db.tx() as c:
        r = c.execute("SELECT id FROM listings WHERE status = 'published' AND style = ? AND json_array_length(photos) = 3 "
                      "ORDER BY impression DESC, id LIMIT 1", (style,)).fetchone()
    return r[0]


def queries():
    loft, classic = pick_listing("лофт"), pick_listing("классика")
    return [
        {"id": "q01", "lang": "ru", "msgs": ["2 спальни в Ваке до $1000"], "budget": 1000, "beds": (2, 2), "districts": ["Ваке"],
         "followup": "more"},
        {"id": "q02", "lang": "en", "msgs": ["1 bedroom in Saburtalo under $600, with a balcony"], "budget": 600, "beds": (1, 1),
         "districts": ["Сабуртало"]},
        {"id": "q03", "lang": "ru", "msgs": ["светлая, скандинавский стиль, много растений"], "followup": "react"},
        {"id": "q04", "lang": "en", "msgs": ["modern loft with brick walls and big windows"]},
        {"id": "q05", "lang": "ru", "msgs": ["до 500$, любой район, у меня собака"], "budget": 500, "pets": True},
        {"id": "q06", "lang": "ru", "msgs": ["старый тбилисский дом в Сололаки или Мтацминде, высокие потолки, до 1200$"],
         "budget": 1200, "districts": ["Сололаки", "Мтацминда"]},
        {"id": "q07", "lang": "ru", "msgs": ["рядом с Ваке-парком, 2-3 спальни, до 1500$"], "budget": 1500, "beds": (2, 3)},
        {"id": "q08", "lang": "en", "msgs": ["near Rustaveli metro, up to $900, 1-2 bedrooms"], "budget": 900, "beds": (1, 2)},
        {"id": "q09", "lang": "ru", "msgs": ["до 800$"], "budget": 800, "pin": [41.7363, 44.7720], "pin_label": "метро Делиси",
         "radius": 1.5},
        {"id": "q10", "lang": "ru", "msgs": [], "images": [ref_photo(loft, 0)], "source": loft},
        {"id": "q11", "lang": "ru", "msgs": ["хочу похожую, до 3000$"], "images": [ref_photo(classic, 1, crop=True)],
         "source": classic, "budget": 3000},
        {"id": "q12", "lang": "ru", "msgs": [], "images": [str(ROOT / "test/ref.jpg")]},
        {"id": "q13", "lang": "ru", "msgs": ["3 спальни, дизайнерский ремонт, вид на город, Ваке или Вера, бюджет до 3000$"],
         "budget": 3000, "beds": (3, 3), "districts": ["Ваке", "Вера"]},
    ]


# ---------- running ----------

def violations(i, q):
    v = []
    if q.get("budget") and (i.get("price_usd") or 0) > q["budget"] * 1.1:
        v.append("price")
    b, beds = i.get("bedrooms"), q.get("beds")
    if beds and b is not None and not beds[0] <= b <= beds[1]:
        v.append("beds")
    if q.get("districts") and not core._district_ok(i, q["districts"]):
        v.append("district")
    if q.get("pets") and i.get("pets") == "нет":
        v.append("pets")
    return v


def run_search(cl):
    n0, t0 = len(LOG), time.time()
    res = core.search(cl)
    calls = LOG[n0:]
    live = sum(c["secs"] for c in calls if not c["cached"])
    return res, {"secs": round(time.time() - t0, 1), "llm_calls": len(calls), "chars": sum(c["chars"] for c in calls),
                 "cached": all(c["cached"] for c in calls), "llm_secs_live": round(live, 1),
                 "calls": [{k: c[k] for k in ("kind", "chars", "images", "secs", "cached", "live_secs")} for c in calls]}


def run_query(q, n):
    out = {}
    share = None  # hybrid off reuses hybrid on's extraction (same call, same cache key)
    for name, mode, final in MODES:
        db.set_setting("search_mode", mode)
        db.set_setting("final_llm", final)
        cl = core.client("eval", f"{q['id']}-{name}-{n}", name=name, lang_hint=q["lang"])
        core.reset(cl)
        with db.tx() as c:  # reruns reuse the client: forget earlier likes/dislikes (they change the prompt)
            c.execute("DELETE FROM lead_events WHERE lead_id IN (SELECT id FROM leads WHERE client_id = ?)", (cl["id"],))
            c.execute("DELETE FROM leads WHERE client_id = ?", (cl["id"],))
            c.execute("DELETE FROM reactions WHERE client_id = ?", (cl["id"],))
        cl["lang"] = q["lang"]
        core.update(cl, lambda s, r: r.update(lang=q["lang"]))
        for m in q["msgs"]:
            core.add_input(cl, text=m)
        for im in q.get("images", []):
            core.add_input(cl, image=im)
        if q.get("pin"):
            core.add_input(cl, pin=q["pin"], pin_label=q["pin_label"], radius=q["radius"])
        res, stat = run_search(cl)
        rows = [{"id": x["listing"]["id"], "reason": x["reason"], "near": x["near"], "violations": violations(x["listing"], q)}
                for x in res["items"]]
        r = {"understood": res["understood"], "total": res["total"], "items": rows, **stat}
        if q.get("followup") == "more":
            res2, stat2 = run_search(core.client("eval", cl["external_id"]))
            r["more"] = {"items": [{"id": x["listing"]["id"], "violations": violations(x["listing"], q)} for x in res2["items"]],
                         "repeats": len({x["id"] for x in rows} & {x["listing"]["id"] for x in res2["items"]}), **stat2}
        if q.get("followup") == "react" and len(rows) >= 2:
            core.react(cl, rows[0]["id"], "like")
            core.react(cl, rows[1]["id"], "dislike")
            res2, stat2 = run_search(core.client("eval", cl["external_id"]))
            r["after_react"] = {"items": [{"id": x["listing"]["id"]} for x in res2["items"]],
                                "liked": rows[0]["id"], "disliked": rows[1]["id"], **stat2}
        out[name] = r
    return out


def img_sim(a, b):
    v = embed.load()
    ia, ib = v["index"].get(a), v["index"].get(b)
    return None if ia is None or ib is None else float(v["img"][ia] @ v["img"][ib])


def photo_check(q, res):
    """Where the source listing of a client photo (or its near-duplicates by photo vector) ended up."""
    v = embed.load()
    ci = embed.client_images(q["images"])
    best = np.full(len(v["ids"]), -1.0, np.float32)
    np.maximum.at(best, v["owner"], (ci @ v["photo"].T).max(0))
    dups = {str(v["ids"][n]) for n in np.where(best >= 0.92)[0]} | {q["source"]}
    return {"source": q["source"], "near_dups": sorted(dups - {q["source"]}),
            **{m: next((n + 1 for n, x in enumerate(res[m]["items"]) if x["id"] in dups), None) for m, _, _ in MODES}}


# ---------- contact sheets ----------

def font(size):
    for p in ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def sheet(q, res, path):
    tw, th, pad, head, label = 220, 165, 8, 70, 150
    rows = [(m, res[m]["items"]) for m, _, _ in MODES]
    if "more" in res["llm"]:
        rows += [(m + " +more", res[m]["more"]["items"]) for m, _, _ in MODES]
    if "after_react" in res["llm"]:
        rows += [(m + " +like/dislike", res[m]["after_react"]["items"]) for m, _, _ in MODES]
    W_ = label + 6 * (tw + pad) + pad
    im = Image.new("RGB", (W_, head + len(rows) * (th + 44)), "white")
    d = ImageDraw.Draw(im)
    f, fs = font(18), font(13)
    title = f"{q['id']}: " + (" / ".join(q["msgs"]) or "(только фото)") + (f"  [пин {q['pin_label']} {q['radius']} км]" if q.get("pin") else "")
    d.text((pad, 10), title[:150], fill="black", font=f)
    cons = ", ".join(f"{k}={q[k]}" for k in ("budget", "beds", "districts", "pets") if q.get(k))
    d.text((pad, 38), "условия: " + (cons or "нет") + "   (красная рамка = нарушение)", fill="#555", font=fs)
    for r, (name, items) in enumerate(rows):
        y = head + r * (th + 44)
        d.text((pad, y + th // 2 - 10), name, fill="black", font=f)
        if q.get("images") and r == 0:  # the client's photo under the first label
            ref = Image.open(q["images"][0]).convert("RGB")
            ref.thumbnail((label - 2 * pad, th // 2 + 20))
            im.paste(ref, (pad, y + th // 2 + 16))
            d.text((pad, y + th + 20), "фото клиента", fill="#777", font=fs)
        for k, x in enumerate(items):
            i = core.listing(x["id"])
            xx = label + pad + k * (tw + pad)
            try:
                p = Image.open(i["photos"][0]).convert("RGB")
                p = p.resize((tw, th)) if p.size[0] / p.size[1] > 1 else p.crop((0, 0, p.size[0], int(p.size[0] * th / tw))).resize((tw, th))
                im.paste(p, (xx, y))
            except (IndexError, OSError):
                d.rectangle((xx, y, xx + tw, y + th), fill="#ddd")
            bad = violations(i, q)
            if bad:
                d.rectangle((xx, y, xx + tw - 1, y + th - 1), outline="red", width=5)
            cap = f"${i.get('price_usd')} · {i.get('bedrooms')}сп · {i.get('district')}"
            d.text((xx, y + th + 2), cap[:34], fill="red" if bad else "black", font=fs)
            d.text((xx, y + th + 20), f"{x['id']} {i.get('style')}"[:34], fill="#777", font=fs)
    im.save(path)


# ---------- report ----------

DOC = ROOT / "docs" / "eval-search.md"
TOK = 3.0  # ~chars per token for this ru/en mix (o200k-like tokenizers)


def report(results, qs):
    names = {"llm": "llm", "hybrid": "hybrid", "hybrid_off": "hybrid, final_llm=off"}
    rows, agg = [], {m: {"items": 0, "viol": 0, "chars": [], "calls": []} for m, _, _ in MODES}
    for q in qs:
        res = results.get(q["id"])
        if not res:
            continue
        ids = {m: [x["id"] for x in res[m]["items"]] for m, _, _ in MODES}
        vio = {m: sum(bool(x["violations"]) for x in res[m]["items"]) for m, _, _ in MODES}
        for m, _, _ in MODES:
            a = agg[m]
            a["items"] += len(ids[m])
            a["viol"] += vio[m]
            a["chars"].append(res[m]["chars"])
            a["calls"].append(max(res[m]["llm_calls"], 1 if m == "hybrid_off" and q["msgs"] else 0))
        ov = lambda a, b: len(set(ids[a]) & set(ids[b]))
        ph = res.get("photo")
        text = (" / ".join(q["msgs"]) or "(только фото)") + (" + фото" if q.get("images") and q["msgs"] else "") + \
            (f" + пин {q['pin_label']}" if q.get("pin") else "")
        cell = lambda m: f"{len(ids[m])} / {vio[m]} / {res[m]['chars'] / 1000:.1f}k"
        rows.append(f"| [{q['id']}](../proof/eval/{q['id']}.png) | {text} | {cell('llm')} | {cell('hybrid')} | {cell('hybrid_off')} | "
                    f"{ov('llm', 'hybrid')} · {ov('llm', 'hybrid_off')} · {ov('hybrid', 'hybrid_off')} | "
                    + (f"{ph['llm'] or '—'} · {ph['hybrid'] or '—'} · {ph['hybrid_off'] or '—'}" if ph else "") + " |")
    out = ["| запрос | текст | llm: шт / наруш. / промпт, симв. | hybrid | hybrid, off | пересечение llm·hyb, llm·off, hyb·off | "
           "фото: место источника llm·hyb·off |", "|---|---|---|---|---|---|---|"] + rows
    mean = lambda xs: sum(xs) / max(len(xs), 1)
    out += ["", "| режим | показано | нарушений жёстких условий | промпт, символов (≈токенов) на поиск | вызовов Codex на поиск |",
            "|---|---|---|---|---|"]
    for m, _, _ in MODES:
        a = agg[m]
        out.append(f"| {names[m]} | {a['items']} | {a['viol']} | {mean(a['chars']):,.0f} (≈{mean(a['chars']) / TOK:,.0f}) | "
                   f"{mean(a['calls']):.1f} |")
    fu = []
    for q in qs:
        res = results.get(q["id"]) or {}
        for m, _, _ in MODES:
            if "more" in res.get(m, {}):
                r = res[m]["more"]
                fu.append(f"| {q['id']} «ещё» | {names[m]} | {len(r['items'])} новых, повторов {r['repeats']}, "
                          f"нарушений {sum(bool(x['violations']) for x in r['items'])} |")
            if "after_react" in res.get(m, {}):
                r = res[m]["after_react"]
                fu.append(f"| {q['id']} ❤️/👎 + «ещё» | {names[m]} | {len(r['items'])} шт; похожесть фото на ❤️ {r.get('sim_liked')}, "
                          f"на 👎 {r.get('sim_disliked')} |")
    if fu:
        out += ["", "| продолжение | режим | результат |", "|---|---|---|"] + fu
    table = "\n".join(out)
    doc = DOC.read_text() if DOC.exists() else "# Оценка поиска: llm vs hybrid\n\n<!-- eval:begin -->\n<!-- eval:end -->\n"
    a, b = doc.split("<!-- eval:begin -->")[0], doc.split("<!-- eval:end -->")[1]
    DOC.write_text(f"{a}<!-- eval:begin -->\n{table}\n<!-- eval:end -->{b}")
    print(table)


def main():
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    setup()
    if "--report" in sys.argv:  # tables from proof/eval/results.json only, no searches
        results = json.loads((PROOF / "results.json").read_text())
        for q in queries():
            if q["id"] in results:
                sheet(q, results[q["id"]], PROOF / f"{q['id']}.png")
        return report(results, queries())
    PROOF.mkdir(parents=True, exist_ok=True)
    qs = [q for q in queries() if not only or q["id"] in only]
    rpath = PROOF / "results.json"
    results = json.loads(rpath.read_text()) if rpath.exists() and only else {}
    for n, q in enumerate(qs):
        print(f"{q['id']}: {q['msgs'] or q.get('images')}", flush=True)
        res = run_query(q, 0)
        if q.get("source"):
            res["photo"] = photo_check(q, res)
        if q.get("followup") == "react":
            for m, _, _ in MODES:
                ar = res[m].get("after_react")
                if ar:
                    sims = [(img_sim(x["id"], ar["liked"]), img_sim(x["id"], ar["disliked"])) for x in ar["items"]]
                    sims = [s for s in sims if None not in s]
                    ar["sim_liked"] = round(float(np.mean([s[0] for s in sims])), 3) if sims else None
                    ar["sim_disliked"] = round(float(np.mean([s[1] for s in sims])), 3) if sims else None
        results[q["id"]] = res
        sheet(q, res, PROOF / f"{q['id']}.png")
        for m, _, _ in MODES:
            r = res[m]
            print(f"   {m:10s} {[x['id'] for x in r['items']]} viol={sum(bool(x['violations']) for x in r['items'])} "
                  f"calls={r['llm_calls']} chars={r['chars']} secs={r['secs']}{' (cached)' if r['cached'] else ''}", flush=True)
        rpath.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("llm calls made live this run:", sum(not c["cached"] for c in LOG))
    report(results, queries())


if __name__ == "__main__":
    main()
