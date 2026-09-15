#!/bin/bash
# CarWatch dash kiosk: full-screen Chromium under the cage compositor on the
# local HDMI panel, pointed at the local dash with the owner token. Started by
# carwatch-kiosk.service on tty1. Waits for the dash before launching so a
# reboot does not leave an error page on the car screen.
set -u
TOKEN_FILE="${CARWATCH_DASH_TOKEN_FILE:-$HOME/.carwatch/dash-token}"
DASH_URL="${CARWATCH_DASH_URL:-http://127.0.0.1:8088/dash}"
for _ in $(seq 1 120); do
  curl -sf -m 2 -o /dev/null "http://127.0.0.1:8088/" && break
  sleep 2
done
TOKEN=""
[ -r "$TOKEN_FILE" ] && TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
URL="$DASH_URL"
[ -n "$TOKEN" ] && URL="$DASH_URL?t=$TOKEN"
export XDG_SESSION_TYPE=wayland
export WLR_LIBINPUT_NO_DEVICES=1
exec /usr/bin/cage -d -- /usr/bin/chromium \
  --kiosk --ozone-platform=wayland --no-first-run --noerrdialogs \
  --disable-infobars --disable-session-crashed-bubble --disable-pinch \
  --overscroll-history-navigation=0 --check-for-update-interval=31536000 \
  --password-store=basic --user-data-dir="$HOME/.carwatch/kiosk-profile" \
  "$URL"
