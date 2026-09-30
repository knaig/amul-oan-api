#!/usr/bin/env bash
# Start everything the planner lab needs on a laptop: Redis, the Beckn/PashuGPT
# stand-in (:3100) and the app (:8000). Logs in .lab/*.log. `scripts/lab_down.sh` stops them.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .lab
PY=${PY:-.venv/bin/python}
set -a; [ -f .env ] && . ./.env; set +a
redis-cli -p "${REDIS_PORT:-6379}" ping >/dev/null 2>&1 || redis-server --daemonize yes --port "${REDIS_PORT:-6379}" >/dev/null
$PY -m uvicorn scripts.beckn_standin:app --host 127.0.0.1 --port 3100 --log-level warning > .lab/standin.log 2>&1 &
echo $! > .lab/standin.pid
$PY -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info > .lab/app.log 2>&1 &
echo $! > .lab/app.pid
for i in $(seq 1 40); do curl -sf http://127.0.0.1:8000/api/lab/config >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf http://127.0.0.1:3100/standin/config >/dev/null && echo "stand-in : http://127.0.0.1:3100/standin/config"
curl -sf http://127.0.0.1:8000/api/lab/config >/dev/null && echo "lab      : http://127.0.0.1:8000/api/lab/" || { echo "app failed to start; see .lab/app.log"; tail -20 .lab/app.log; exit 1; }
