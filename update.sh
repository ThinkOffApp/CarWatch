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

echo "restarting services..."
sudo systemctl restart carwatch-agent carwatch-chat carwatch-presence
echo "DONE - @gle now reads its location from the network, not a stale note"
