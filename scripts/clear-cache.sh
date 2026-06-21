#!/usr/bin/env bash
# Clear the NavGo browser cache so Cloudflare/Turnstile starts fresh (it won't be
# fighting a session it already flagged). Run this with the app STOPPED.
#
#   bash scripts/clear-cache.sh          # cache only — KEEPS your logins (Google etc.)
#   bash scripts/clear-cache.sh --all    # full reset — also clears cookies & storage
#                                        # (you'll be logged OUT of everything)
set -euo pipefail

PROFILE="$HOME/Library/Application Support/agentic-web-navigator-shell"
[ -d "$PROFILE" ] || { echo "No profile found at: $PROFILE"; exit 0; }

# Refuse to run while the browser is open (files are locked → won't take effect).
if lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -qE ':(9222|9333) '; then
  echo "⚠  NavGo looks like it's still running. Stop it (Ctrl-C in start.sh) first, then re-run."
  exit 1
fi

echo "Clearing browser cache in: $PROFILE"
# HTTP/code/GPU caches + service-worker cache storage — safe, keeps logins.
rm -rf \
  "$PROFILE/Cache" \
  "$PROFILE/Code Cache" \
  "$PROFILE/GPUCache" \
  "$PROFILE/DawnCache" \
  "$PROFILE/DawnGraphiteCache" \
  "$PROFILE/Service Worker/CacheStorage" \
  "$PROFILE/Service Worker/ScriptCache" \
  2>/dev/null || true
echo "✓ cache cleared"

if [ "${1:-}" = "--all" ]; then
  echo "Full reset: clearing cookies, local storage, and challenge state…"
  rm -rf \
    "$PROFILE/Cookies" "$PROFILE/Cookies-journal" \
    "$PROFILE/Local Storage" \
    "$PROFILE/Session Storage" \
    "$PROFILE/IndexedDB" \
    "$PROFILE/Service Worker" \
    "$PROFILE/Network" \
    2>/dev/null || true
  echo "✓ full reset done — you'll be logged out of sites (sign in again once)"
fi

echo "Done. Start NavGo again:  bash scripts/start.sh"
