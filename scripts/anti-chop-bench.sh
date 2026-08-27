#!/bin/bash
# anti-chop-bench.sh - prove long car-speaker playback is gap-free BEFORE
# any human demo: the car plays a long clip through its own speakers, the
# cabin mic records it, and we measure the holes. petrus's method (27 Aug:
# "don't you record what it says and listen to it?"). Rule: 3 clean cycles
# in a row before anyone films anything.
#
# Run from the Mac:  scripts/anti-chop-bench.sh <clip.wav> [cycles]
# Needs: car awake, MBUX media source = vadelma, SF-558 plugged into the Pi,
# ssh host alias "vadelma".
set -u
CLIP="${1:?usage: anti-chop-bench.sh <clip.wav> [cycles]}"
CYCLES="${2:-3}"
PI=vadelma

DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$CLIP")
DUR=${DUR%.*}
REC=$((DUR + 14))
echo "clip: $CLIP (${DUR}s), recording ${REC}s per cycle, $CYCLES cycle(s)"

# One-time preflight: is the car's A2DP sink there at all?
if ! ssh "$PI" 'bluealsa-cli list-pcms 2>/dev/null | grep -q a2dpsrc/sink'; then
  echo "ABORT: no A2DP sink on the Pi - car asleep or vadelma not selected as MBUX media source."
  exit 1
fi
scp -q "$CLIP" "$PI:/tmp/anti-chop-clip.wav"

# The listener owns the mic; park it for the bench, always bring it back.
ssh "$PI" 'sudo systemctl stop carwatch-listen'
trap 'ssh '"$PI"' "sudo systemctl start carwatch-listen" 2>/dev/null' EXIT

PASS=0
for i in $(seq 1 "$CYCLES"); do
  echo "=== cycle $i/$CYCLES ==="
  ssh "$PI" 'rm -f /tmp/carwatch-bench.wav; curl -sS -m5 -X POST http://localhost:8088/api/audio-bench -H "Content-Type: application/json" -d "{\"seconds\": '"$REC"'}" >/dev/null'
  sleep 2
  ssh "$PI" 'curl -sS -m10 -X POST http://localhost:8088/api/play --data-binary @/tmp/anti-chop-clip.wav -H "Content-Type: audio/wav" >/dev/null'
  sleep $((DUR + 14))
  OUT="/tmp/anti-chop-cycle$i.wav"
  scp -q "$PI:/tmp/carwatch-bench.wav" "$OUT" || { echo "cycle $i: NO RECORDING (bench failed)"; continue; }
  if python3 "$(dirname "$0")/chop-gaps.py" "$OUT"; then
    PASS=$((PASS + 1))
  fi
done

echo "=== verdict: $PASS/$CYCLES clean ==="
[ "$PASS" -eq "$CYCLES" ] && echo "GREEN LIGHT: filming can start." || echo "NOT green - do not film yet."
[ "$PASS" -eq "$CYCLES" ]
