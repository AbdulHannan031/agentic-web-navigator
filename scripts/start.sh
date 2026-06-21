#!/usr/bin/env bash
# Single-command launcher: starts the Electron browser (opens the CDP port) and
# the Python agent service together. Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env so BOTH the browser (Electron) and the agent see the same config
# (e.g. NAV_CDP_PORT, NAV_MODEL). Without this, Electron wouldn't pick up the port.
if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi

# Electron must NOT run in "node" mode (set by some IDE-hosted shells), or the
# `app` API is undefined and the browser fails to start.
unset ELECTRON_RUN_AS_NODE

# Prefer the project venv's Python; fall back to python3.
PY="$ROOT/agent/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "▶ Launching NavGo browser…"
( cd "$ROOT/browser" && exec ./node_modules/.bin/electron . ) &
ELECTRON_PID=$!

# Stop the browser (and its children) when this script exits.
cleanup() { kill "$ELECTRON_PID" 2>/dev/null || true; pkill -P "$ELECTRON_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "▶ Starting agent backend…  (Ctrl-C to stop both)"
cd "$ROOT/agent"
exec "$PY" -m navigator.server
