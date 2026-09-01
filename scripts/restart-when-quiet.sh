#!/usr/bin/env bash
# Restart CarWatch services, but only when the car is quiet: not driving,
# no voice exchange in flight, no answer generating (carwatch.guard).
# Called by update.sh through systemd-run (own cgroup, see there), and by
# update.sh --rollback. Waits up to MAX seconds for a quiet moment, then
# gives up loudly rather than yanking the dash mid-drive.
#
#   restart-when-quiet.sh <max_wait_seconds> <unit> [unit...]
#
# Needs CARWATCH_STATE pointing at the car user's ~/.carwatch when run as
# root (update.sh passes it), otherwise the guard would read /root/.carwatch.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
MAX="${1:-3600}"; shift || true
UNITS="$*"
[ -n "$UNITS" ] || { echo "restart-when-quiet: no units given"; exit 2; }
start=$(date +%s)
while :; do
  if REASON="$(cd "$DIR" && PYTHONPATH="$DIR" python3 -m carwatch.guard 2>&1)"; then
    break
  fi
  now=$(date +%s)
  if [ $((now - start)) -ge "$MAX" ]; then
    echo "restart-when-quiet: still busy after ${MAX}s ($REASON) - services NOT restarted; code on disk is new, next update or reboot picks it up"
    exit 1
  fi
  echo "restart-when-quiet: $REASON - waiting"
  sleep 30
done
# Same sequence the old inline block used: kill stragglers, reload, start the
# always-on pair, restart the requested set.
pkill -f "carwatch[.]webchat" || true
pkill -f "carwatch[.]presence" || true
sleep 1
systemctl daemon-reload
systemctl enable --now carwatch-chat carwatch-presence
systemctl restart $UNITS
echo "restart-when-quiet: restarted $UNITS after $(( $(date +%s) - start ))s"
