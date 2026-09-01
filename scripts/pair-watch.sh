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

# [ -]CH[0-9], not CH[0-9]: case-insensitive CH[0-9] matched "Galaxy Watch8"
# (the 20.8. pairing loop) - Sony CH model names always follow a space or "-".
PATTERN="sony|WH-|1000X|[ -]CH[0-9]|LE_WH|LinkBuds|headphone|earbud|JBL|Bose|buds"
# Never pair these even on a pattern hit: watches/phones/wearables are BT
# audio SOURCES, not sinks - playback toward them can never work. vlink = the
# OBD dongle's two identities.
BLOCKLIST="watch|band|phone|tablet|keyboard|mouse|vlink"
MAX_ATTEMPTS=3
VOICE="$HOME/carwatch-stack/models/en_US-lessac-medium.onnx"
PIPER="$HOME/.local/bin/piper"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
GLE_TXT="/tmp/pair-watch-post.txt"

post() {
    # Best-effort room post as the car agent; never fatal. Never repeat the
    # previous line verbatim - the 20.8. loop filled the room with one line.
    [ "$1" = "$(cat /tmp/pair-watch-lastpost 2>/dev/null)" ] && return 0
    printf '%s' "$1" > /tmp/pair-watch-lastpost
    { printf '%s' "$1" > "$GLE_TXT" && PYTHONPATH="$DIR" python3 -m carwatch.room --file "$GLE_TXT" >/dev/null 2>&1; } || true
}

advertises_sink() {
    # 0 = Audio Sink UUID present, 1 = UUIDs present but NO sink (never pair),
    # 2 = no UUID data yet (EIR not seen; let the name pattern decide).
    local info; info=$(bluetoothctl info "$1" 2>/dev/null)
    echo "$info" | grep -q "UUID" || return 2
    echo "$info" | grep -qi "Audio Sink" && return 0
    return 1
}

greet_through() {
    local mac="$1" name="$2"
    local wav; wav="$(mktemp --suffix=.wav)"
    echo "Good morning Petrus. This is Vadelma. You are hearing me through $name. The whole audio chain works." \
        | "$PIPER" -m "$VOICE" -f "$wav" 2>/dev/null
    aplay -D "bluealsa:DEV=$mac,PROFILE=a2dp" "$wav" >/dev/null 2>&1
    local rc=$?
    rm -f "$wav"
    return $rc
}

echo "pair-watch: scanning continuously for audio devices ($PATTERN, minus $BLOCKLIST)"
while true; do
    bluetoothctl power on >/dev/null 2>&1
    bluetoothctl --timeout 5 scan on >/dev/null 2>&1
    LINE=$(bluetoothctl devices | grep -iE "$PATTERN" | grep -ivE "$BLOCKLIST" | head -1 || true)
    MAC=$(echo "$LINE" | awk '{print $2}')
    if [ -z "$MAC" ]; then
        continue
    fi
    NAME=$(echo "$LINE" | cut -d' ' -f3-)
    STAMP="/tmp/pair-watch-greeted-${MAC//:/}"
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Paired: yes"; then
        # Known device: keep it connected, greet only once per boot.
        if ! bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
            bluetoothctl connect "$MAC" >/dev/null 2>&1
            sleep 3
        fi
        if [ ! -f "$STAMP" ] && bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
            touch "$STAMP"
            if greet_through "$MAC" "$NAME"; then
                post "Audio test PASSED: I just spoke through $NAME over Bluetooth. The piper -> bluealsa -> A2DP chain is proven."
            fi
        fi
        sleep 20
        continue
    fi
    # Unpaired candidate: gate on what it IS, not only what it is called.
    advertises_sink "$MAC"; SINK=$?
    if [ "$SINK" -eq 1 ]; then
        SKIP="/tmp/pair-watch-skip-${MAC//:/}"
        if [ ! -f "$SKIP" ]; then
            touch "$SKIP"
            echo "pair-watch: $NAME advertises no Audio Sink (source-only device) - skipping"
        fi
        sleep 20
        continue
    fi
    ATT="/tmp/pair-watch-attempts-${MAC//:/}"
    N=$(cat "$ATT" 2>/dev/null || echo 0)
    if [ "$N" -ge "$MAX_ATTEMPTS" ]; then
        sleep 20
        continue
    fi
    echo $((N + 1)) > "$ATT"
    echo "pair-watch: found unpaired $LINE - pairing (attempt $((N + 1))/$MAX_ATTEMPTS)"
    { echo "agent KeyboardDisplay"; echo "default-agent"; echo "pair $MAC"; sleep 8; echo "yes"; sleep 2; echo "trust $MAC"; echo "connect $MAC"; sleep 6; echo "quit"; } \
        | bluetoothctl >/dev/null 2>&1
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; then
        touch "$STAMP"
        if greet_through "$MAC" "$NAME"; then
            rm -f "$ATT"
            post "Audio test PASSED: paired $NAME and spoke through it. The piper -> bluealsa -> A2DP chain is proven."
        else
            FAILPOST="/tmp/pair-watch-failpost-${MAC//:/}"
            if [ ! -f "$FAILPOST" ]; then
                touch "$FAILPOST"
                post "Paired $NAME (bond + connect ok) but playback returned an error - will retry quietly ($((MAX_ATTEMPTS - N - 1)) attempts left)."
            fi
        fi
    fi
done
