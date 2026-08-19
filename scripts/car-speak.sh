#!/usr/bin/env bash
# Speak text through the CAR's speakers over Bluetooth A2DP. The car (MBUX)
# is the Bluetooth speaker; the Pi needs no speaker of its own for this.
# piper makes the audio, bluealsa routes it to the car's A2DP sink.
#
# ONE-TIME, in the car with the car's Bluetooth in pairing mode:
#   sudo bash car-speak.sh pair          # find + pair the car's BT audio
# THEN, anytime the car is connected:
#   bash car-speak.sh say "text to speak"
#   bash car-speak.sh test               # a fixed test sentence
set -euo pipefail

CMD="${1:-test}"
VOICE="$HOME/carwatch-stack/models/en_US-lessac-medium.onnx"
PIPER="$HOME/.local/bin/piper"
# Car BT MAC is remembered here after pairing so 'say' needs no argument.
CAR_MAC_FILE="$HOME/.carwatch/car-bt-mac"

speak() {
    local text="$1"
    [ -x "$PIPER" ] && [ -f "$VOICE" ] || { echo "piper/voice missing"; exit 1; }
    local wav; wav="$(mktemp --suffix=.wav)"
    echo "$text" | "$PIPER" -m "$VOICE" -f "$wav" 2>/dev/null
    local mac=""; [ -f "$CAR_MAC_FILE" ] && mac="$(cat "$CAR_MAC_FILE")"
    if [ -n "$mac" ]; then
        # Route to the car's A2DP sink explicitly.
        bluealsa-aplay --profile-a2dp "$mac" < "$wav" 2>/dev/null || \
        aplay -D "bluealsa:DEV=$mac,PROFILE=a2dp" "$wav" 2>/dev/null || \
        echo "playback failed - is the car connected? (car-speak.sh pair)"
    else
        # No car paired yet: send to the default bluealsa sink (whatever A2DP
        # device is connected), so it still works if the car auto-connected.
        bluealsa-aplay < "$wav" 2>/dev/null || echo "no A2DP sink - pair the car first"
    fi
    rm -f "$wav"
}

case "$CMD" in
    pair)
        systemctl start bluetooth || true
        sudo systemctl restart bluealsa 2>/dev/null || true
        bluetoothctl power on >/dev/null 2>&1 || true
        echo "== scanning 20 s for the car's Bluetooth audio =="
        bluetoothctl --timeout 20 scan on >/dev/null 2>&1 || true
        echo "Devices bluetoothd sees (pick the car - e.g. 'Mercedes', 'GLE', 'E-Class', 'MB Bluetooth'):"
        bluetoothctl devices || true
        # Auto-pick an obvious Mercedes name if present.
        LINE=$(bluetoothctl devices | grep -iE "mercedes|benz|mbux|e-class|eclass|gle|glc|w213" | head -1 || true)
        MAC=$(echo "$LINE" | awk '{print $2}' || true)
        if [ -z "$MAC" ]; then
            echo "No obvious car name. Re-run: sudo bash car-speak.sh pairmac <MAC-from-list>"
            exit 1
        fi
        pairmac "$MAC"
        ;;
    pairmac)
        MAC="${2:?usage: car-speak.sh pairmac <MAC>}"
        bluetoothctl --timeout 6 agent NoInputNoOutput >/dev/null 2>&1 || true
        bluetoothctl --timeout 15 pair "$MAC" 2>&1 | tail -3 || true
        bluetoothctl trust "$MAC" >/dev/null 2>&1 || true
        bluetoothctl --timeout 10 connect "$MAC" 2>&1 | tail -3 || true
        mkdir -p "$(dirname "$CAR_MAC_FILE")"; echo "$MAC" > "$CAR_MAC_FILE"
        echo "paired car BT = $MAC (saved)"
        speak "CarWatch connected. You should hear me through your car speakers."
        ;;
    say)
        speak "${2:?usage: car-speak.sh say \"text\"}"
        ;;
    test)
        speak "Hello Petrus. This is your car speaking through its own speakers. The audio path works."
        ;;
    *)
        echo "usage: $0 {pair | pairmac <MAC> | say \"text\" | test}"; exit 1 ;;
esac
