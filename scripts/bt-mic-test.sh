#!/bin/bash
# bt-mic-test.sh - prove the car's cabin mic reaches the Pi over Bluetooth HFP.
#
# Run on the Pi while MBUX is awake and vadelma is the selected media source.
# The Pi runs bluealsa with -p hfp-ag (the phone side); if MBUX connects the
# Handsfree profile, a <MAC>/hfpag/source PCM appears and we can record the
# cabin mic over the SCO call channel, then play the recording back through
# the car speakers over A2DP.
#
# Usage: bt-mic-test.sh [seconds]   (default 5)
set -u
SECS="${1:-5}"
OUT=/tmp/carwatch-btmic.wav

echo "=== 1. Bluetooth device ==="
bluetoothctl info | grep -E "Name|Connected|UUID.*(Handsfree|Advanced Audio)" || {
  echo "NO BT DEVICE CONNECTED - wake MBUX and select vadelma as media source"; exit 1; }

echo "=== 2. bluealsa PCMs ==="
PCMS=$(bluealsa-cli list-pcms 2>/dev/null || bluealsactl list-pcms 2>/dev/null)
echo "$PCMS"
MAC=$(echo "$PCMS" | grep -oiE '([0-9A-F]{2}_){5}[0-9A-F]{2}(?=/hfpag/source)' -P | head -1)
if [ -z "$MAC" ]; then
  MAC=$(echo "$PCMS" | grep -i hfpag/source | grep -oiE '([0-9A-F]{2}_){5}[0-9A-F]{2}' | head -1)
fi
if [ -z "$MAC" ]; then
  echo "RESULT: no hfpag/source PCM - MBUX did not connect the Handsfree profile."
  echo "Try: remove pairing on MBUX, re-pair (so HFP is negotiated), or check"
  echo "     bluetoothctl 'info' UUIDs above for Handsfree."
  exit 2
fi
MACC=${MAC//_/:}
echo "cabin mic PCM found: $MAC"

echo "=== 3. record ${SECS}s from cabin mic (say something!) ==="
timeout $((SECS+10)) arecord -q -D "bluealsa:DEV=$MACC,PROFILE=sco" \
  -f S16_LE -r 16000 -c 1 -d "$SECS" "$OUT"
RC=$?
SZ=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
echo "arecord rc=$RC size=${SZ}B"
[ "$SZ" -lt 8000 ] && { echo "RESULT: capture empty/tiny - SCO did not open."; exit 3; }

echo "=== 4. play it back through the car (A2DP) ==="
sleep 2  # let the link fall back from HFP to A2DP
timeout 30 aplay --buffer-time=1000000 -D "bluealsa:DEV=$MACC,PROFILE=a2dp" "$OUT"
echo "RESULT: OK - recorded ${SZ}B from the car mic and played it back."
echo "File kept at $OUT (fetch for analysis)."
