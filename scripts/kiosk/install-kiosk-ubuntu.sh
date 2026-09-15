#!/bin/bash
# Ubuntu variant of install-kiosk.sh (VTA-439): Chromium comes from snap on
# Ubuntu (apt has only a shim), and a box that already runs a text console on
# tty1 (vta-console.service) keeps it on tty2 so both survive.
# Idempotent. Usage: sudo scripts/kiosk/install-kiosk-ubuntu.sh [user]
set -euo pipefail
KUSER="${1:-${SUDO_USER:-$USER}}"
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends cage seatd grim fonts-noto-color-emoji curl
snap list chromium >/dev/null 2>&1 || snap install chromium
install -m 755 "$HERE/carwatch-kiosk.sh" /usr/local/bin/carwatch-kiosk.sh
sed -i 's#/usr/bin/chromium #/snap/bin/chromium #' /usr/local/bin/carwatch-kiosk.sh
install -m 644 "$HERE/carwatch-kiosk.service" /etc/systemd/system/carwatch-kiosk@.service
usermod -aG video,input,render "$KUSER" 2>/dev/null || true
# keep an existing tty1 text console, moved to tty2 (Ctrl+Alt+F2)
if systemctl cat vta-console.service >/dev/null 2>&1; then
  mkdir -p /etc/systemd/system/vta-console.service.d
  cat > /etc/systemd/system/vta-console.service.d/tty2.conf <<'CONF'
[Unit]
Conflicts=
Conflicts=getty@tty2.service
ConditionPathExists=/dev/tty2
[Service]
ExecStartPre=
ExecStartPre=/bin/sh -c 'setterm -blank 0 -powerdown 0 > /dev/tty2 2>/dev/null || true; printf "\033[H\033[2J" > /dev/tty2'
TTYPath=/dev/tty2
CONF
fi
systemctl daemon-reload
systemctl disable --now getty@tty1.service getty@tty2.service 2>/dev/null || true
systemctl restart vta-console.service 2>/dev/null || true
systemctl set-default graphical.target
systemctl enable --now "carwatch-kiosk@${KUSER}.service"
sleep 6
systemctl --no-pager --lines=6 status "carwatch-kiosk@${KUSER}.service" || true
systemctl --no-pager --lines=3 status vta-console.service 2>/dev/null | head -8 || true
