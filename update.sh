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
git fetch -q origin main
git reset --hard -q origin/main
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

echo "restarting services..."
sudo systemctl restart carwatch-agent carwatch-chat carwatch-presence carwatch-brain 2>/dev/null || sudo systemctl restart carwatch-agent carwatch-chat carwatch-presence
# Do NOT restart carwatch-reach here: it is already kept up by systemd, and
# restarting it every hourly update needlessly churns the tunnel URL (brief
# reachability gap + a new cloudflared process each cycle). enable --now above
# starts it if it is somehow down; leave a healthy tunnel alone.
echo "DONE - car pulls its own updates AND dials out so it is reachable anywhere"
