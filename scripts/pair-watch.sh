#!/usr/bin/env bash
# Continuous Bluetooth audio-device pairing watch. Removes the timing race
# that burned two windows on 20.8.: petrus holds the pairing button while an
# agent tries to happen to scan at the same moment over a session-bound SSH
# loop. This runs ON the Pi as a systemd service (survives agent sessions,
# reboots, everything): scan continuously, and the moment a known audio
# device advertises in pairing mode, pair -> trust -> connect -> speak a
# greeting through it, and post the result to the room DIRECTLY from here
# (the demo-runner lesson: results self-post, they do not ride an agent's
# attention loop).
#
# Never touches ~/.carwatch/car-bt-mac - the car's identity stays intact.
# Greets once per device per boot (state in /tmp) so a connected headset is
# not spammed every scan cycle.
set -u

PATTERN="sony|WH-|1000X|CH[0-9]|LE_WH|LinkBuds|headphone|earbud|JBL|Bose|buds"
VOICE="$HOME/carwatch-stack/models/en_US-lessac-medium.onnx"
PIPER="$HOME/.local/bin/piper"
POSTER="$HOME/post-as-gle.py"
GLE_TXT="/tmp/gle_text.txt"

post() {
    # Best-effort room post as the car agent; never fatal.
    { printf '%s' "$1" > "$GLE_TXT" && python3 "$POSTER" >/dev/null 2>&1; } || true
}

greet_through() {
    local mac="$1"
    local wav; wav="$(mktemp --suffix=.wav)"
    echo "Good morning Petrus. This is Vadelma. You are hearing me through your Sony headphones. The whole audio chain works." \
        | "$PIPER" -m "$VOICE" -f "$wav" 2>/dev/null
    aplay -D "bluealsa:DEV=$mac,PROFILE=a2dp" "$wav" >/dev/null 2>&1
    local rc=$?
    rm -f "$wav"
    return $rc
}

echo "pair-watch: scanning continuously for audio devices ($PATTERN)"
while true; do
    bluetoothctl power on >/dev/null 2>&1
    bluetoothctl --timeout 5 scan on >/dev/null 2>&1
    LINE=$(bluetoothctl devices | grep -iE "$PATTERN" | head -1 || true)
    MAC=$(echo "$LINE" | awk '{print $2}')
    if [ -z "$MAC" ]; then
        continue
    fi
    STAMP="/tmp/pair-watch-greeted-${MAC//:/}"
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Paired: yes"; then
        # Known device: keep it connected, greet only once per boot.
        if ! bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
            bluetoothctl connect "$MAC" >/dev/null 2>&1
            sleep 3
        fi
        if [ ! -f "$STAMP" ] && bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
            touch "$STAMP"
            if greet_through "$MAC"; then
                post "Audio test PASSED: I just spoke through $(echo "$LINE" | cut -d' ' -f3-) over Bluetooth. The piper -> bluealsa -> A2DP chain is proven."
            fi
        fi
        sleep 20
        continue
    fi
    echo "pair-watch: found unpaired $LINE - pairing"
    { echo "agent KeyboardDisplay"; echo "default-agent"; echo "pair $MAC"; sleep 8; echo "yes"; sleep 2; echo "trust $MAC"; echo "connect $MAC"; sleep 6; echo "quit"; } \
        | bluetoothctl >/dev/null 2>&1
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; then
        touch "$STAMP"
        if greet_through "$MAC"; then
            post "Audio test PASSED: paired $(echo "$LINE" | cut -d' ' -f3-) and spoke through it. The piper -> bluealsa -> A2DP chain is proven."
        else
            post "Paired $(echo "$LINE" | cut -d' ' -f3-) (bond + connect ok) but playback returned an error - checking."
        fi
    fi
done
