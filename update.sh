#!/usr/bin/env bash
# Pull the latest CarWatch code onto the Pi and restart the services.
#
# The car runs on Petrus's phone hotspot when it is out in the GLE, which
# puts it behind the phone's NAT - claudeMB at home cannot reach it to
# deploy. But the Pi HAS internet through the hotspot, so it can update
# itself from the public repo. One command from a phone terminal (Termux):
#
#   ssh petrus@<pi-ip-from-hotspot-device-list>
#   curl -sSL https://raw.githubusercontent.com/ThinkOffApp/CarWatch/main/update.sh | bash
#
# Safe to run repeatedly. Config in ~/.carwatch is never touched.
set -e

REPO="https://github.com/ThinkOffApp/CarWatch.git"
DIR="$HOME/CarWatch"

cd "$DIR"
if [ ! -d .git ]; then
  git init -q
  git remote add origin "$REPO" 2>/dev/null || git remote set-url origin "$REPO"
fi
echo "fetching latest..."
# Self-heal a wedged git state (Aug 14: an interrupted fetch left
# refs/remotes/origin/main inconsistent -> "cannot lock ref" and every
# update died at this line). Clear stale locks + the remote ref; the
# following fetch recreates it cleanly. Worktree is reset right after,
# so deleting the remote-tracking ref loses nothing.
find .git -name '*.lock' -delete 2>/dev/null || true
git update-ref -d refs/remotes/origin/main 2>/dev/null || true
git fetch -q origin main || { git remote prune origin 2>/dev/null || true; git fetch -q origin main; }
git reset --hard -q FETCH_HEAD
echo "code updated to $(git rev-parse --short HEAD)"

# Install/refresh systemd unit files from the repo. The old updater only
# restarted EXISTING services, so a newly added unit (like carwatch-reach)
# never got picked up - part of why fixes did not land. Copy every unit,
# reload, and enable the always-on ones.
if [ -d "$DIR/systemd" ]; then
  echo "installing service units..."
  sudo cp "$DIR"/systemd/*.service "$DIR"/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
  sudo systemctl daemon-reload
  # Dial-out reachability: makes the car reachable from anywhere so no future
  # fix needs a physical trip or anyone typing creds into the car.
  sudo systemctl enable --now carwatch-reach.service 2>/dev/null || true
  # Zero-touch OBD: watches the ENET cable, reads the engine, posts to the
  # room - petrus does nothing in the car.
  sudo systemctl enable --now carwatch-obd.service 2>/dev/null || true
  sudo systemctl enable --now carwatch-update.timer 2>/dev/null || true
fi

# One-shot identity switch, repo-triggered. MUST run BEFORE the service
# restarts below: update.sh executes inside carwatch-chat via /api/update,
# so the restart line kills this very script - anything after it never
# runs (why the first two switch attempts silently did nothing).
# (Aug 15: the E 300e switch must
# run over the hotspot where only the HTTP update channel reaches the Pi).
# Idempotent: fires only while the marker exists AND the car is still @gle
# AND a staged key is present; switch-car.sh consumes the key.
if [ -f "$DIR/profiles/SWITCH-TO-eclass" ] && [ -f "$HOME/.carwatch/staged-api-key" ]; then
  CUR=$(python3 -c "import json;print(json.load(open('$HOME/.carwatch/config.json')).get('handle',''))" 2>/dev/null || echo "")
  if [ "$CUR" = "@gle" ]; then
    echo "running one-shot @eclass switch..."
    SWITCH_NO_RESTART=1 bash "$DIR/scripts/switch-car.sh" eclass || echo "switch failed"
    if [ -f "$HOME/eclass-w213-2021-owners-manual.pdf" ]; then
      CARWATCH_STATE="$HOME/.carwatch" python3 -m carwatch.manual --ingest "$HOME/eclass-w213-2021-owners-manual.pdf" || echo "manual ingest failed"
    fi
    # Policy (petrus, Aug 15): the car never rides the house wifi.
    sudo nmcli con delete "wifi router" 2>/dev/null || true
    echo "switch block done"
  fi
fi

# One-shot deep probe, repo-triggered (petrus in the car, Aug 15). Runs
# BEFORE the restart line (same self-kill reason as the switch block) and
# in the BACKGROUND so the update completes while the probe posts live.
mkdir -p "$HOME/.carwatch"
if [ -f "$DIR/profiles/PROBE-NOW" ]; then
  STAMP=$(cat "$DIR/profiles/PROBE-NOW")
  LAST=$(cat "$HOME/.carwatch/last-probe-stamp" 2>/dev/null || echo "")
  ELM_PRESENT=""
  for p in /dev/ttyUSB0 /dev/ttyUSB1 /dev/rfcomm0; do [ -e "$p" ] && ELM_PRESENT=1; done
  if [ "$STAMP" != "$LAST" ] && [ -n "$ELM_PRESENT" ]; then
    echo "$STAMP" > "$HOME/.carwatch/last-probe-stamp"
    echo "launching deep OBD probe (transient unit, survives service restarts)"
    # setsid was NOT enough: the probe stayed in carwatch-chat's cgroup and
    # the restart below killed it ~2s after launch (Aug 15, petrus in the
    # car, burned the one-shot silently). systemd-run escapes the cgroup.
    sudo systemd-run --collect --unit="carwatch-probe-$(date +%s)" \
      --working-directory="$DIR" -E PYTHONPATH="$DIR" \
      bash -c "python3 -m carwatch.obd_probe >>$HOME/.carwatch/obd-probe.log 2>&1" || \
      (cd "$DIR" && nohup python3 -m carwatch.obd_probe >>$HOME/.carwatch/obd-probe.log 2>&1 &)
  fi
fi

# Surface the deep-probe log THROUGH the update response (Aug 15: the car's
# room key started 403ing after the identity switch, so the probe's own posts
# are lost; /api/update returns this script's output, so tailing the log here
# makes the results readable over the tunnel with no working room key).
if [ -f $HOME/.carwatch/obd-probe.log ]; then
  echo "=== PROBE LOG (tail) ==="
  tail -c 1200 $HOME/.carwatch/obd-probe.log
  echo "=== END PROBE LOG ==="
fi

echo "restarting services..."
# carwatch-obd MUST be in this list: enable --now is a no-op when the unit is
# already running, so without a restart the OBD watcher keeps executing
# pre-update code forever (Aug 14: exactly why the first USB adapter plug-in
# stayed silent while everything else updated around it).
# The restart must NOT run in this script's own cgroup: update.sh executes
# inside carwatch-chat (/api/update), so stopping carwatch-chat killed the
# restart command itself and the remaining units sometimes never came back -
# code on disk updated while the RUNNING page stayed old (Aug 16, the
# wifi-scan evening: three updates restarted the agent but never the chat).
# systemd-run escapes the cgroup, same trick the deep-probe block uses.
sudo systemd-run --collect --unit="carwatch-svc-restart-$(date +%s)" \
  sh -c 'systemctl restart carwatch-agent carwatch-chat carwatch-presence carwatch-brain carwatch-obd 2>/dev/null || systemctl restart carwatch-agent carwatch-chat carwatch-presence carwatch-obd' \
  || sudo systemctl restart carwatch-agent carwatch-chat carwatch-presence carwatch-obd
# Do NOT restart carwatch-reach here: it is already kept up by systemd, and
# restarting it every hourly update needlessly churns the tunnel URL (brief
# reachability gap + a new cloudflared process each cycle). enable --now above
# starts it if it is somehow down; leave a healthy tunnel alone.
echo "DONE - car pulls its own updates AND dials out so it is reachable anywhere"
