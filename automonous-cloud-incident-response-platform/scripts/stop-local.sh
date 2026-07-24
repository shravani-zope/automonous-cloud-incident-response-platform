#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for name in chaos backend frontend; do
  pidfile="$ROOT/.run/${name}.pid"
  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile")"
    kill "$pid" 2>/dev/null || true
    rm -f "$pidfile"
    echo "stopped $name ($pid)"
  fi
done
# Also kill anything still bound to our ports
for port in 8000 8081 5173; do
  pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  if [[ -n "${pids}" ]]; then
    kill $pids 2>/dev/null || true
  fi
done
