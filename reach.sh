#!/usr/bin/env bash
# reach.sh - make the Pi reachable from anywhere, over whatever internet it has.
#
# The park-and-wait trap (petrus, Aug 12): the car sits behind the phone
# hotspot's NAT, so claudeMB at home cannot reach it to fix anything, and the
# fix he found is stuck on his Mac. This dials OUT instead: a cloudflared quick
# tunnel exposes the car's local dashboard (:8088) at a public https URL, and
# the URL is published to the room as @gle so claudeMB can reach the car from
# anywhere and finish the job (provision home wifi, etc.) with no one typing
# creds into the car and no physical trip.
#
# Self-contained: downloads cloudflared if missing, needs no account, no secret.
# The tunnel is a control surface, so it is meant to be short-lived - claudeMB
# uses it, provisions permanent reachability, then this can be stopped.
set -u

DASH_PORT="${CARWATCH_DASH_PORT:-8088}"
BIN_DIR="$HOME/carwatch-stack/bin"
CF="$BIN_DIR/cloudflared"
CF_LOG="/tmp/carwatch-reach-cf.log"
URL_FILE="$HOME/.carwatch/reach-url.txt"
POSTER="$HOME/post-as-gle.py"
GLE_TXT="/tmp/gle_text.txt"

mkdir -p "$BIN_DIR" "$(dirname "$URL_FILE")"

# Resolve the CPU arch to the right cloudflared asset (Pi 5 is arm64).
arch="$(uname -m)"
case "$arch" in
  aarch64|arm64) asset="cloudflared-linux-arm64" ;;
  armv7l|armhf)  asset="cloudflared-linux-arm" ;;
  x86_64|amd64)  asset="cloudflared-linux-amd64" ;;
  *)             asset="cloudflared-linux-arm64" ;;
esac

if [ ! -x "$CF" ]; then
  echo "reach: fetching cloudflared ($asset)..."
  if ! curl -fsSL -o "$CF" \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/$asset"; then
    echo "reach: cloudflared download failed; cannot dial out" >&2
    exit 2
  fi
  chmod +x "$CF"
fi

# One tunnel at a time: kill a previous reach tunnel before starting a new one.
pkill -f "cloudflared tunnel --url http://localhost:$DASH_PORT" 2>/dev/null || true
sleep 1
: > "$CF_LOG"
"$CF" tunnel --url "http://localhost:$DASH_PORT" --no-autoupdate >>"$CF_LOG" 2>&1 &

# Wait for the public URL to appear in the log (up to ~40s).
url=""
for _ in $(seq 1 20); do
  # Quick-tunnel hostnames are multi-word (word-word-word-word); a bare
  # [a-z0-9-]+ also matched "api.trycloudflare.com" from cloudflared's own
  # error lines and we published a dead URL (seen Aug 13). Require a dash.
  url="$(grep -oE 'https://[a-z0-9]+(-[a-z0-9]+)+\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1)"
  [ -n "$url" ] && break
  sleep 2
done

if [ -z "$url" ]; then
  echo "reach: no tunnel URL yet; will retry on next service start" >&2
  exit 1
fi

echo "$url" > "$URL_FILE"
echo "reach: car dashboard reachable at $url"

# The URL is delivered SILENTLY via the presence heartbeat (presence.py reads
# this same file and publishes reach_url), which claudeMB reads on demand. We
# deliberately do NOT post it to the room: the hourly self-update restarts this
# service and would spam the room with a fresh URL every cycle (petrus is
# noise-sensitive), and the car is already provisioned so "provision me" is stale.

# Keep this script alive so the systemd service keeps the tunnel up.
wait
