#!/usr/bin/env bash
# Pair the Pi to a Bluetooth OBD dongle (Vgate iCar Pro 2S) and bind it to
# /dev/rfcomm0, which carwatch.elm327 already reads. This removes the USB
# cable running into the footwell (petrus, Aug 19: cable near the pedals).
#
# ONE-TIME setup, run PARKED. The dongle uses classic Bluetooth SPP; the
# iCar Pro 2S PIN is 1234 when asked. After binding, obdwatch reads it with
# no code change (it already probes /dev/rfcomm0).
#
# Usage:
#   sudo bash pair-bt-obd.sh scan          # find the dongle's MAC
#   sudo bash pair-bt-obd.sh pair <MAC>    # pair + trust + bind rfcomm0
#   sudo bash pair-bt-obd.sh persist <MAC> # install a boot-time rebind unit
#   sudo bash pair-bt-obd.sh test          # read via rfcomm0
set -euo pipefail

CMD="${1:-scan}"
MAC="${2:-}"

case "$CMD" in
  auto)
    # One-shot: scan, pick the first OBD-looking dongle, pair, bind, persist,
    # and test - so the whole wireless swap is a single remote call. Prints a
    # labelled log; exits non-zero only if no dongle is found or the test read
    # fails.
    systemctl start bluetooth || true
    rfkill unblock bluetooth || true
    bluetoothctl power on >/dev/null 2>&1 || true
    echo "== scan (25 s) =="
    bluetoothctl --timeout 25 scan on >/dev/null 2>&1 || true
    # NOTE: '|| true' is load-bearing - under pipefail a no-match grep would
    # kill the whole script BEFORE the diagnosis prints (burned 19.8.).
    # The Vgate iCar Pro 2S advertises TWO devices: "Android-Vlink" (classic
    # BT/SPP - the one rfcomm needs) and "IOS-Vlink" (BLE). Prefer the
    # classic side explicitly; the generic pattern is the fallback.
    LINE=$(bluetoothctl devices | grep -i "android-vlink" | head -1 || true)
    [ -n "$LINE" ] || LINE=$(bluetoothctl devices | grep -iE "obd|vgate|icar|vlink|v-link" | grep -iv "ios-" | head -1 || true)
    MAC=$(echo "$LINE" | awk '{print $2}' || true)
    echo "picked: ${LINE:-<none>}"
    if [ -z "$MAC" ]; then
        echo "NO_DONGLE_FOUND - all devices bluetoothd can see right now:"
        bluetoothctl devices || true
        echo "(if the list is empty: the dongle is asleep - OBD port has no"
        echo " power without ignition - or it is out of range/already paired"
        echo " to a phone. Wake it with ignition ON and close any phone OBD app.)"
        exit 1
    fi
    echo "== pair =="
    bluetoothctl --timeout 6 agent NoInputNoOutput >/dev/null 2>&1 || true
    bluetoothctl --timeout 15 pair "$MAC" 2>&1 | tail -3 || true
    bluetoothctl trust "$MAC" >/dev/null 2>&1 || true
    echo "== bind rfcomm0 =="
    rfcomm release 0 2>/dev/null || true
    rfcomm bind 0 "$MAC" 1 && echo "bound /dev/rfcomm0 -> $MAC"
    echo "== persist =="
    cat > /etc/systemd/system/carwatch-rfcomm.service <<UNIT
[Unit]
Description=CarWatch: bind BT OBD dongle to /dev/rfcomm0
After=bluetooth.service
Requires=bluetooth.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sh -c 'rfcomm release 0 2>/dev/null || true'
ExecStart=/usr/bin/rfcomm bind 0 $MAC 1

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload && systemctl enable carwatch-rfcomm.service >/dev/null 2>&1
    echo "persist unit installed"
    echo "== test read via rfcomm0 =="
    cd "$(dirname "$0")/.." 2>/dev/null || cd /home/petrus/CarWatch
    python3 -m carwatch.elm327 /dev/rfcomm0 2>&1 | tail -20
    ;;
  scan)
    systemctl start bluetooth || true
    rfkill unblock bluetooth || true
    echo "Scanning 15 s for OBD dongles (look for OBDII / Vgate / iCar)..."
    bluetoothctl --timeout 15 scan on | grep -iE "obd|vgate|icar|v-link" || true
    echo "Full device list:"
    bluetoothctl devices | grep -iE "obd|vgate|icar|v-link" || \
      echo "  (none matched by name - run 'bluetoothctl devices' and pick the new MAC)"
    ;;
  pair)
    [ -n "$MAC" ] || { echo "usage: $0 pair <MAC>"; exit 1; }
    # SPP dongles want no-input-no-output agent + PIN 1234; scripted so it is
    # deterministic parked.
    bluetoothctl --timeout 5 agent NoInputNoOutput || true
    bluetoothctl pair "$MAC" || true
    bluetoothctl trust "$MAC" || true
    # channel 1 is the SPP RFCOMM channel on these dongles
    sudo rfcomm release 0 2>/dev/null || true
    sudo rfcomm bind 0 "$MAC" 1
    ls -l /dev/rfcomm0 && echo "BOUND: /dev/rfcomm0 -> $MAC"
    ;;
  persist)
    [ -n "$MAC" ] || { echo "usage: $0 persist <MAC>"; exit 1; }
    # Rebind rfcomm0 at boot so the swap survives a power cycle.
    cat <<UNIT | sudo tee /etc/systemd/system/carwatch-rfcomm.service >/dev/null
[Unit]
Description=CarWatch: bind BT OBD dongle to /dev/rfcomm0
After=bluetooth.service
Requires=bluetooth.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sh -c 'rfcomm release 0 2>/dev/null || true'
ExecStart=/usr/bin/rfcomm bind 0 $MAC 1

[Install]
WantedBy=multi-user.target
UNIT
    sudo systemctl daemon-reload
    sudo systemctl enable --now carwatch-rfcomm.service
    echo "persist unit installed + enabled"
    ;;
  test)
    if [ ! -e /dev/rfcomm0 ]; then echo "no /dev/rfcomm0 - pair first"; exit 1; fi
    cd "$(dirname "$0")/.."
    python3 -m carwatch.elm327 /dev/rfcomm0
    ;;
  *)
    echo "usage: $0 {scan|pair <MAC>|persist <MAC>|test}"; exit 1 ;;
esac
