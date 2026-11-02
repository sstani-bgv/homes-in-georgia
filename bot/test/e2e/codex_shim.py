"""Stand-in for the `codex` binary on PATH during e2e runs: logs every model call (prompt, images, schema) to
$E2E_CODEX_LOG, then runs the real Codex (E2E_CODEX=real) or answers deterministically (E2E_CODEX=fake, for harness debugging)."""
import json
import os
import re
import subprocess
import sys
import time

REAL = os.environ.get("E2E_REAL_CODEX", "/opt/homebrew/bin/codex")
args = sys.argv[1:]
prompt = sys.stdin.read()
schema_path = args[args.index("--output-schema") + 1]
out_path = args[args.index("-o") + 1]
images = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
props = json.load(open(schema_path))["properties"]
mode = os.environ.get("E2E_CODEX", "real")
t0 = time.time()
if mode == "real":
    r = subprocess.run([REAL] + args, input=prompt, text=True)
    code = r.returncode
else:
    if "top" in props:
        cat = prompt.split("Каталог", 1)[1]
        budget = re.search(r"(?:до|up to) \$(\d+)", prompt)
        cap = int(budget[1]) if budget else 10 ** 9
        ids = [m[1] for m in re.finditer(r"^(\S+) \| (?:[\d.]+ км \| )?[^|]*\| \$(\d+)", cat, re.M) if int(m[2]) <= cap][:5]
        en = "Язык ответа для клиента: English" in prompt
        res = {"understood": "test match" if en else "тестовый подбор", "matches_total": len(ids), "max_price_usd": None,
               "top": [{"id": i, "reason": "fits the request" if en else "подходит по запросу"} for i in ids]}
    elif "place" in props:
        res = {"place": None, "label": None, "radius_km": None}
    else:
        ids = [json.loads(x)["id"] for x in prompt.splitlines() if x.startswith("{")]
        res = {"items": [{"id": i, "district": "Tbilisi", "description": f"Apartment {i} in Tbilisi."} for i in ids]}
    json.dump(res, open(out_path, "w"), ensure_ascii=False)
    code = 0
out = json.load(open(out_path)) if os.path.exists(out_path) else None
with open(os.environ.get("E2E_CODEX_LOG", "/tmp/e2e/codex_calls.jsonl"), "a") as f:
    f.write(json.dumps({"t": t0, "secs": round(time.time() - t0, 1), "mode": mode, "kind": next(iter(props)), "images": images,
                        "prompt": prompt, "out": out, "code": code}, ensure_ascii=False) + "\n")
sys.exit(code)
