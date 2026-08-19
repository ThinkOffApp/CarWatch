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
    ensure_bluealsa >/dev/null 2>&1 || true
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

ensure_bluealsa() {
    # The Pi must OFFER the A2DP-source profile or the car's connect fails
    # ("connecting... failed" on the MBUX screen; measured Aug 19 right after
    # the bond finally succeeded). bluealsa registers that profile with BlueZ,
    # but only when started with -p a2dp-source - the packaged default is not
    # guaranteed to include it. Converge: drop-in override when the packaged
    # unit exists (survives reboots), transient unit otherwise. Idempotent and
    # cheap when the daemon is already right.
    local BIN PID
    BIN="$(command -v bluealsa || command -v bluealsad || true)"
    [ -n "$BIN" ] || { echo "bluealsa not installed - car audio cannot work"; return 1; }
    PID="$(pgrep -x "$(basename "$BIN")" | head -1 || true)"
    if [ -n "$PID" ] && tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null | grep -q "a2dp-source"; then
        return 0
    fi
    echo "(re)starting bluealsa with the A2DP source profile..."
    if systemctl cat bluealsa.service >/dev/null 2>&1; then
        sudo mkdir -p /etc/systemd/system/bluealsa.service.d
        printf '[Service]\nExecStart=\nExecStart=%s -p a2dp-source -p a2dp-sink\n' "$BIN" \
            | sudo tee /etc/systemd/system/bluealsa.service.d/carwatch-a2dp.conf >/dev/null
        sudo systemctl daemon-reload
        sudo systemctl restart bluealsa || true
    else
        sudo pkill -x "$(basename "$BIN")" 2>/dev/null || true
        sleep 1
        sudo systemd-run --collect --unit="carwatch-bluealsa" \
            "$BIN" -p a2dp-source -p a2dp-sink 2>/dev/null || true
    fi
    sleep 1
}

# Pair + connect a MAC as the car's A2DP sink, remember it, greet. This is a
# FUNCTION so the auto-scan 'pair' path can reuse it: the old code called
# `pairmac "$MAC"` as if it were a command, and bash answered "pairmac:
# command not found" - so a scan that DID find the car (e.g. "MBUX 57313")
# still never paired (petrus, Aug 19). The greeting is non-fatal (|| true):
# the BT bond is what matters; if piper cannot speak (e.g. RAM tight) the pair
# still counts as done.
do_pairmac() {
    local MAC="${1:?usage: do_pairmac <MAC>}"
    # The A2DP-source profile must be registered BEFORE bonding: the car
    # auto-connects right after the bond and fails ("connecting... failed" on
    # MBUX) when the Pi offers no audio profile.
    ensure_bluealsa || true
    # Clear any half-bond left by earlier failed attempts - a stale bond makes
    # every retry fail identically (claudemm, Aug 19). The car side needs the
    # same hygiene: forget "vadelma" on the CAR's Bluetooth list if shown.
    bluetoothctl remove "$MAC" >/dev/null 2>&1 || true
    # A Mercedes A2DP pair uses numeric-comparison SSP: the car shows a 6-digit
    # code and wants a YES on BOTH sides. NoInputNoOutput ("Just Works") cannot
    # answer that, so the old code failed "org.bluez.Error.AuthenticationFailed"
    # (measured on MBUX 57313, Aug 19). Register a KeyboardDisplay agent and
    # auto-confirm the code from the Pi side inside ONE bluetoothctl session
    # (separate --timeout invocations each spawned a NEW agent that died before
    # the confirmation arrived). petrus taps YES on the car screen for the same
    # code; the Pi says yes automatically here.
    bluetoothctl power on >/dev/null 2>&1 || true
    local out
    out=$({
        echo "agent KeyboardDisplay"; echo "default-agent"; echo "pairable on"
        echo "pair $MAC";  sleep 12   # car shows the code; BlueZ raises confirm
        echo "yes";        sleep 3    # auto-confirm the numeric comparison
        echo "trust $MAC"
        echo "connect $MAC"; sleep 4
        echo "quit"
    } | bluetoothctl 2>&1 || true)
    echo "$out" | tail -25
    if echo "$out" | grep -qiE "Pairing successful|Connection successful|Paired: yes"; then
        mkdir -p "$(dirname "$CAR_MAC_FILE")"; echo "$MAC" > "$CAR_MAC_FILE"
        echo "paired car BT = $MAC (saved)"
        speak "CarWatch connected. You should hear me through your car speakers." || true
    else
        echo "pairing did not complete - when the car shows a code, tap YES on the CAR screen, then press pair again"
        return 1
    fi
}

case "$CMD" in
    pair)
        systemctl start bluetooth || true
        ensure_bluealsa || true
        bluetoothctl power on >/dev/null 2>&1 || true
        echo "== scanning 20 s for the car's Bluetooth audio =="
        bluetoothctl --timeout 20 scan on >/dev/null 2>&1 || true
        echo "Devices bluetoothd sees (your Mercedes shows as 'MBUX <number>', e.g. MBUX 57313 - NOT the word 'Mercedes'):"
        bluetoothctl devices || true
        # Auto-pick an obvious Mercedes name if present.
        LINE=$(bluetoothctl devices | grep -iE "mercedes|benz|mbux|e-class|eclass|gle|glc|w213" | head -1 || true)
        MAC=$(echo "$LINE" | awk '{print $2}' || true)
        if [ -z "$MAC" ]; then
            echo "No obvious car name. Re-run: sudo bash car-speak.sh pairmac <MAC-from-list>"
            exit 1
        fi
        do_pairmac "$MAC"
        ;;
    pairmac)
        do_pairmac "${2:?usage: car-speak.sh pairmac <MAC>}"
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
