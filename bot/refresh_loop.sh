#!/bin/bash
# The `refresh` process (docs/ARCHITECTURE.md §1): refresh.sh now (REFRESH_ON_START=1, default) and every REFRESH_HOURS
# (6; 0 = run once and exit). Env as refresh.sh (PY, TG_FETCH, SKIP_FACEBOOK ...) + DATABASE_URL.
cd "$(dirname "$0")" || exit 1
rmdir data/refresh.lock 2>/dev/null   # a lock left by a killed run would block every future refresh
h=${REFRESH_HOURS:-6}
[ "${REFRESH_ON_START:-1}" = 1 ] && ./refresh.sh
[ "$h" = 0 ] && exit 0
while true; do
  sleep "$(awk -v h="$h" 'BEGIN { print int(h * 3600) }')"
  ./refresh.sh
done
