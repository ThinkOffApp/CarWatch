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
#   CARWATCH_DRM_DIR        where connector status lives (default /sys/class/drm;
#                           tests point it at a fake tree)
#   CARWATCH_PANEL_POLL     seconds between panel checks while the browser runs (5;
#                           also bounds how fast a crashed browser is noticed)
#
# No panel, no browser (petrus, 15 Sep 2026: the VTA's display had moved to
# the Pi, and the kiosk kept drawing the dash for nobody: one full core, 80 C,
# fans, for four hours). If no DRM connector reports "connected" the script
# exits 78 (EX_CONFIG), which the unit lists in RestartPreventExitStatus, so
# systemd leaves it alone; the udev rule installed next to the unit starts it
# again the moment a panel is plugged in. While the browser runs, the panel is
# re-checked every CARWATCH_PANEL_POLL seconds and the browser is stopped when
# it goes away.
set -u
NO_PANEL_EXIT=78
DRM_DIR="${CARWATCH_DRM_DIR:-/sys/class/drm}"
PANEL_POLL="${CARWATCH_PANEL_POLL:-5}"
panel_connected() {
  local f
  for f in "$DRM_DIR"/card*-*/status; do
    [ -r "$f" ] || continue
    [ "$(cat "$f" 2>/dev/null)" = "connected" ] && return 0
  done
  return 1
}
if ! panel_connected; then
  echo "carwatch-kiosk: no display connected, not starting the browser" >&2
  exit "$NO_PANEL_EXIT"
fi
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
"$CAGE" -d -- "$CHROMIUM" \
  --kiosk --ozone-platform=wayland --no-first-run --noerrdialogs \
  --disable-infobars --disable-session-crashed-bubble --disable-pinch \
  --overscroll-history-navigation=0 --check-for-update-interval=31536000 \
  --password-store=basic --user-data-dir="$PROFILE" \
  "$URL" &
BROWSER=$!
trap 'kill "$BROWSER" 2>/dev/null' TERM INT
# Watch the panel while the browser runs: unplugged panel = stop drawing.
while kill -0 "$BROWSER" 2>/dev/null; do
  if ! panel_connected; then
    echo "carwatch-kiosk: display disconnected, stopping the browser" >&2
    kill "$BROWSER" 2>/dev/null
    wait "$BROWSER" 2>/dev/null
    exit "$NO_PANEL_EXIT"
  fi
  sleep "$PANEL_POLL" &
  wait $! 2>/dev/null
done
wait "$BROWSER"
