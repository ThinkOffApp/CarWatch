#!/bin/bash
# CarWatch dash kiosk: full-screen Chromium under the cage compositor on the
# local HDMI panel, pointed at the local dash with the owner token. Started by
# carwatch-kiosk@.service on tty1.
#
# Waits for the dash before launching. If the dash never answers within the
# readiness window the script exits nonzero WITHOUT starting Chromium, so
# systemd's Restart=always keeps retrying instead of leaving a stale
# connection-error page on the car screen.
#
# Env (set by the unit or a drop-in):
#   CARWATCH_CHROMIUM       browser binary (default /usr/bin/chromium; the
#                           Ubuntu installer sets /snap/bin/chromium)
#   CARWATCH_KIOSK_PROFILE  Chromium profile dir; defaults depend on the binary:
#                           snap Chromium can only write under ~/snap/chromium,
#                           so the snap gets ~/snap/chromium/common/carwatch-kiosk
#   CARWATCH_KIOSK_TRIES / CARWATCH_KIOSK_SLEEP  readiness attempts (120) and
#                           seconds between them (2)
#   CARWATCH_DASH_TOKEN_FILE, CARWATCH_DASH_URL
set -u
TOKEN_FILE="${CARWATCH_DASH_TOKEN_FILE:-$HOME/.carwatch/dash-token}"
DASH_URL="${CARWATCH_DASH_URL:-http://127.0.0.1:8088/dash}"
HEALTH_URL="${DASH_URL%/dash}/"
CHROMIUM="${CARWATCH_CHROMIUM:-/usr/bin/chromium}"
TRIES="${CARWATCH_KIOSK_TRIES:-120}"
SLEEP="${CARWATCH_KIOSK_SLEEP:-2}"
CAGE="${CARWATCH_CAGE:-/usr/bin/cage}"

case "$CHROMIUM" in
  /snap/*) DEFAULT_PROFILE="$HOME/snap/chromium/common/carwatch-kiosk" ;;
  *)       DEFAULT_PROFILE="$HOME/.carwatch/kiosk-profile" ;;
esac
PROFILE="${CARWATCH_KIOSK_PROFILE:-$DEFAULT_PROFILE}"

ready=0
for _ in $(seq 1 "$TRIES"); do
  if curl -sf -m 2 -o /dev/null "$HEALTH_URL"; then ready=1; break; fi
  sleep "$SLEEP"
done
if [ "$ready" != 1 ]; then
  echo "carwatch-kiosk: dash not reachable at $HEALTH_URL after $TRIES attempts, not starting the browser" >&2
  exit 1
fi

TOKEN=""
[ -r "$TOKEN_FILE" ] && TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
URL="$DASH_URL"
[ -n "$TOKEN" ] && URL="$DASH_URL?t=$TOKEN"
mkdir -p "$PROFILE" 2>/dev/null || true
export XDG_SESSION_TYPE=wayland
export WLR_LIBINPUT_NO_DEVICES=1
exec "$CAGE" -d -- "$CHROMIUM" \
  --kiosk --ozone-platform=wayland --no-first-run --noerrdialogs \
  --disable-infobars --disable-session-crashed-bubble --disable-pinch \
  --overscroll-history-navigation=0 --check-for-update-interval=31536000 \
  --password-store=basic --user-data-dir="$PROFILE" \
  "$URL"
