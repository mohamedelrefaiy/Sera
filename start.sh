#!/usr/bin/env bash
#
# start.sh — run Sera (frontend + backend).
#
# There is ONE server. The FastAPI backend serves BOTH the JSON API (/api/*) and
# the static frontend (sera/frontend/, mounted at /) from the same origin,
# so a single uvicorn process is the whole app — no separate frontend dev server.
# Serving them together on one port is what lets the frontend's fetch("/api/...")
# calls reach the API without CORS or a proxy.
#
# Usage:
#   ./start.sh                 # http://127.0.0.1:8010
#   PORT=9000 ./start.sh       # pick a port
#   HOST=0.0.0.0 ./start.sh    # expose on the LAN
#
# The deterministic shortlist works with no API key; the chat panel needs
# ANTHROPIC_API_KEY (or a logged-in `claude` CLI).

set -euo pipefail

# Always operate from the repo root (this script's own directory), regardless of cwd.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8010}"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

# --- 1. Ensure the virtualenv + package are ready ----------------------------
if [ ! -x "$PY" ]; then
  echo "→ No .venv found — creating one and installing the app (first run)…"
  python3 -m venv "$VENV"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -e .
elif ! "$PY" -c "import sera" >/dev/null 2>&1; then
  echo "→ Installing the app into the existing .venv…"
  "$PY" -m pip install --quiet -e .
fi

# --- 2. Make sure the port is free -------------------------------------------
EXISTING_PIDS="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$EXISTING_PIDS" ]; then
  echo "→ Port $PORT is in use (pid(s): $EXISTING_PIDS) — killing and retrying…"
  echo "$EXISTING_PIDS" | xargs kill
  for _ in $(seq 1 20); do
    lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.25
  done
  if lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  …still held, force killing…"
    lsof -ti "tcp:$PORT" -sTCP:LISTEN | xargs kill -9
    sleep 0.5
  fi
fi

# --- 3. Report whether the live agent chat will be enabled -------------------
if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -f "$HOME/.claude/.credentials.json" ] || command -v claude >/dev/null 2>&1; then
  CHAT="enabled (live Claude agent chat available)"
else
  CHAT="disabled (set ANTHROPIC_API_KEY to enable the chat panel; the shortlist still works)"
fi

echo "──────────────────────────────────────────────────────────"
echo "  Sera — frontend + backend (single server)"
echo "  URL:   http://$HOST:$PORT"
echo "  Chat:  $CHAT"
echo "  Stop:  Ctrl-C"
echo "──────────────────────────────────────────────────────────"

# --- 4. Run. exec replaces the shell so Ctrl-C goes straight to uvicorn. ------
exec "$PY" sera/api/serve.py --host "$HOST" --port "$PORT"
