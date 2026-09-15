#!/bin/bash
# Install the CarWatch dash kiosk on a box with an HDMI panel (Pi 5 / VTA).
# Idempotent. Usage: sudo scripts/kiosk/install-kiosk.sh [user]   (default: the invoking user)
set -euo pipefail
KUSER="${1:-${SUDO_USER:-$USER}}"
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends cage chromium seatd grim fonts-noto-color-emoji
install -m 755 "$HERE/carwatch-kiosk.sh" /usr/local/bin/carwatch-kiosk.sh
install -m 644 "$HERE/carwatch-kiosk.service" /etc/systemd/system/carwatch-kiosk@.service
usermod -aG video,input,render "$KUSER" 2>/dev/null || true
systemctl daemon-reload
systemctl disable --now getty@tty1.service || true
systemctl set-default graphical.target
systemctl enable --now "carwatch-kiosk@${KUSER}.service"
sleep 6
systemctl --no-pager --lines=8 status "carwatch-kiosk@${KUSER}.service" || true
