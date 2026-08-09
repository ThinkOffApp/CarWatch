#!/usr/bin/env bash
# CarWatch installer for Raspberry Pi OS (64-bit). Idempotent.
set -euo pipefail

DEST=/opt/carwatch

sudo useradd -r -s /usr/sbin/nologin carwatch 2>/dev/null || true
sudo mkdir -p "$DEST" /etc/carwatch
sudo cp -r carwatch "$DEST/"
sudo cp systemd/carwatch.service /etc/systemd/system/

if [ ! -f /etc/carwatch/config.json ]; then
  sudo cp config.example.json /etc/carwatch/config.json
  sudo chmod 600 /etc/carwatch/config.json
  sudo chown carwatch /etc/carwatch/config.json
  echo ">> Edit /etc/carwatch/config.json with your key, room and SSIDs."
fi

# iwgetid for SSID detection
sudo apt-get install -y wireless-tools >/dev/null

sudo systemctl daemon-reload
echo ">> Done. Start with: sudo systemctl enable --now carwatch"
