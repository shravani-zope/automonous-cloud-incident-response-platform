#!/usr/bin/env bash
# Run the core demo stack locally (no Docker required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q -r backend/requirements.txt -r demo/chaos-target/requirements.txt

mkdir -p "$ROOT/.run"

# Chaos target
(cd "$ROOT/demo/chaos-target" && uvicorn app:app --host 127.0.0.1 --port 8081) >"$ROOT/.run/chaos.log" 2>&1 &
echo $! >"$ROOT/.run/chaos.pid"

# Backend
(
  cd "$ROOT/backend"
  export DATABASE_URL="sqlite+aiosqlite:///./acirp.db"
  export KAFKA_ENABLED=false
  export CHAOS_TARGET_URL="http://127.0.0.1:8081"
  export PROMETHEUS_URL="http://127.0.0.1:9090"
  export CORS_ORIGINS="http://localhost:5173,http://localhost:3000"
  export AUTO_REMEDIATE=true
  uvicorn app.main:app --host 127.0.0.1 --port 8000
) >"$ROOT/.run/backend.log" 2>&1 &
echo $! >"$ROOT/.run/backend.pid"

# Frontend
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi
(cd frontend && npm run dev -- --host 127.0.0.1 --port 5173) >"$ROOT/.run/frontend.log" 2>&1 &
echo $! >"$ROOT/.run/frontend.pid"

sleep 3
echo "ACIRP local stack starting..."
echo "  Dashboard: http://127.0.0.1:5173"
echo "  API:       http://127.0.0.1:8000/docs"
echo "  Chaos:     http://127.0.0.1:8081/health"
echo "Logs in .run/*.log — stop with: scripts/stop-local.sh"

# Wait until API is up
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null; then
    echo "Backend healthy."
    curl -s -X POST http://127.0.0.1:8000/api/simulate \
      -H 'Content-Type: application/json' \
      -d '{"scenario":"oom"}' | python3 -m json.tool | head -n 40
    exit 0
  fi
  sleep 1
done

echo "Backend failed to start — see .run/backend.log"
tail -n 50 "$ROOT/.run/backend.log"
exit 1
