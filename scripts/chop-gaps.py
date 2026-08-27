#!/usr/bin/env python3
"""Measure playback holes in a cabin recording of the car speaking.

Reads a 16 kHz mono S16 wav (the audio-bench capture), finds the speech
span, and reports every internal quiet hole. TTS sentence pauses run well
under 0.8 s; the choppiness petrus heard was 1-2 s dropouts, so >= 0.8 s
inside the span counts as a CHOP and fails the cycle (exit 1).

Usage: chop-gaps.py <bench.wav>
"""
import math
import struct
import sys
import wave

WIN_MS = 50
HOLE_S = 0.8          # a quiet stretch this long inside speech = chop
SPEECH_X = 4.0        # speech is this many times the noise floor


def rms_windows(path):
    w = wave.open(path)
    if w.getnchannels() != 1 or w.getsampwidth() != 2:
        sys.exit(f"unexpected format in {path} (want 16-bit mono)")
    rate = w.getframerate()
    per = int(rate * WIN_MS / 1000)
    out = []
    while True:
        raw = w.readframes(per)
        if len(raw) < per * 2:
            break
        s = struct.unpack(f"<{per}h", raw)
        out.append(math.sqrt(sum(x * x for x in s) / per))
    return out


def main():
    wins = rms_windows(sys.argv[1])
    if not wins:
        sys.exit("empty recording")
    floor = sorted(wins)[len(wins) // 5]          # 20th percentile
    thr = max(floor * SPEECH_X, floor + 200)
    loud = [r > thr for r in wins]
    if not any(loud):
        print(f"FAIL: no speech found (floor {floor:.0f}, thr {thr:.0f}) - "
              "was the car actually playing?")
        sys.exit(1)
    first, last = loud.index(True), len(loud) - 1 - loud[::-1].index(True)
    span_s = (last - first + 1) * WIN_MS / 1000
    holes, run = [], 0
    for i in range(first, last + 1):
        if loud[i]:
            if run * WIN_MS / 1000 >= HOLE_S:
                holes.append(((i - run) * WIN_MS / 1000, run * WIN_MS / 1000))
            run = 0
        else:
            run += 1
    natural = sum(1 for i in range(first, last + 1) if not loud[i])
    print(f"speech span {span_s:.1f}s (starts {first*WIN_MS/1000:.1f}s), "
          f"floor {floor:.0f}, thr {thr:.0f}, "
          f"quiet windows {natural*WIN_MS/1000:.1f}s total")
    if holes:
        for at, dur in holes:
            print(f"  CHOP: {dur:.2f}s hole at {at:.1f}s")
        print(f"FAIL: {len(holes)} hole(s) >= {HOLE_S}s")
        sys.exit(1)
    print("CLEAN: no holes >= %.1fs inside the speech span" % HOLE_S)


if __name__ == "__main__":
    main()
