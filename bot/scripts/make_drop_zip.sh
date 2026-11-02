#!/bin/bash
# Build context for a Dokploy "drop" deploy -> /tmp/homes-drop.zip (Dockerfile at the zip root).
# Files: git-tracked + untracked-not-ignored, minus .dockerignore patterns.
# SEED=1 also puts /tmp/seed.tgz (./seed.sh) into seed/seed.tgz, so the first start skips re-indexing.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${OUT:-/tmp/homes-drop.zip}
SEED_FILE=${SEED_FILE:-/tmp/seed.tgz}
[ "${SEED:-0}" = 1 ] && [ ! -f "$SEED_FILE" ] && { echo "no $SEED_FILE, run ./seed.sh first"; exit 1; }
git ls-files -z --cached --others --exclude-standard | python3 -c '
import fnmatch, os, sys, zipfile
out, seed = sys.argv[1], sys.argv[2]
pats = [l.strip() for l in open(".dockerignore") if l.strip() and not l.startswith("#")]
def ignored(p):
    parts = p.split("/")
    return any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(p, pat + "/*") or
               any(fnmatch.fnmatch("/".join(parts[:i]), pat) for i in range(1, len(parts))) for pat in pats)
files = sorted(p for p in sys.stdin.read().split("\0") if p and os.path.isfile(p) and not ignored(p))
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p)
    if seed:
        z.write(seed, "seed/seed.tgz", compress_type=zipfile.ZIP_STORED)
print(f"{out}: {len(files)} files" + (" + seed/seed.tgz" if seed else ""))
' "$OUT" "$([ "${SEED:-0}" = 1 ] && echo "$SEED_FILE" || true)"
echo "size: $(du -h "$OUT" | cut -f1)"
