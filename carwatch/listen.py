"""The car listens on its own: continuous voice-activity detection.

Replaces the broken chat-coordinated recording (petrus, Aug 12: three timed
windows all missed because room latency makes a sync impossible). This runs
on the Pi, streams the USB mic, and when it hears actual SPEECH it captures
that utterance, transcribes it with whisper, and routes it to the grounded
@gle pipeline. No window, no timing, no "tell me when you're recording" -
you just talk to the car.

Energy-based VAD implemented without audioop (removed in Python 3.13):
int16 RMS is computed directly from the PCM with the array module.

Tunables live in a small config so the threshold can be adjusted for the
SF-558 mic without code changes.
"""

from __future__ import annotations

import array
import math
import os
import subprocess
import sys
import tempfile
import time
import wave

from carwatch.voice import transcribe, ask_gle

RATE = 16000
CHANNELS = 1
CHUNK_MS = 200
CHUNK_BYTES = int(RATE * (CHUNK_MS / 1000)) * 2  # int16 mono
SILENCE_HANGOVER_CHUNKS = 5   # ~1s of quiet ends an utterance
MIN_SPEECH_CHUNKS = 3         # ignore blips shorter than ~0.6s
DEFAULT_THRESHOLD = 700       # int16 RMS; tune per mic/environment


def rms(chunk: bytes) -> float:
    if len(chunk) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _write_wav(path: str, frames: bytes) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


def handle_utterance(frames: bytes, on_text) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    _write_wav(path, frames)
    text = transcribe(path)
    os.unlink(path)
    if text:
        print(f"HEARD: {text}", flush=True)
        on_text(text)
    else:
        print("(captured sound but no clear speech)", flush=True)


def _open_mic():
    return subprocess.Popen(
        ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS),
         "-t", "raw"],
        stdout=subprocess.PIPE)


def listen(threshold: float, on_text) -> None:
    """Stream the mic forever; emit each detected utterance to on_text.

    Resilient to transient arecord failures (EINTR, brief device hiccups):
    the mic is reopened rather than the loop dying, so a running service
    keeps listening through interruptions. Only ONE instance may hold the
    mic - run this as the single carwatch-listen service, never alongside
    another arecord (mic contention was the Aug 12 failure).
    """
    proc = _open_mic()
    print(f"listening (threshold {threshold:.0f}) ...", flush=True)
    speech: list[bytes] = []
    quiet = 0
    in_speech = False
    try:
        while True:
            try:
                chunk = proc.stdout.read(CHUNK_BYTES)
            except Exception:
                chunk = b""
            if not chunk:
                # arecord ended or was interrupted; reopen and continue.
                time.sleep(0.3)
                try:
                    proc.terminate()
                except Exception:
                    pass
                proc = _open_mic()
                speech, quiet, in_speech = [], 0, False
                continue
            level = rms(chunk)
            if level >= threshold:
                if not in_speech:
                    in_speech = True
                    speech = []
                    quiet = 0
                speech.append(chunk)
                quiet = 0
            elif in_speech:
                speech.append(chunk)  # keep trailing audio through the pause
                quiet += 1
                if quiet >= SILENCE_HANGOVER_CHUNKS:
                    in_speech = False
                    if len(speech) - quiet >= MIN_SPEECH_CHUNKS:
                        handle_utterance(b"".join(speech), on_text)
                    speech = []
    finally:
        proc.terminate()


def _default_on_text(text: str) -> None:
    # For a bare run: answer to stdout. The service wires a poster instead.
    print("--- car answering ---", flush=True)
    print(ask_gle(text), flush=True)


def main() -> None:
    threshold = DEFAULT_THRESHOLD
    if "--threshold" in sys.argv:
        threshold = float(sys.argv[sys.argv.index("--threshold") + 1])
    if "--calibrate" in sys.argv:
        # Print rolling RMS for a few seconds so the quiet-room floor is known.
        proc = subprocess.Popen(
            ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1",
             "-t", "raw"], stdout=subprocess.PIPE)
        end = time.time() + 6
        peak = 0.0
        while time.time() < end:
            level = rms(proc.stdout.read(CHUNK_BYTES))
            peak = max(peak, level)
            print(f"rms {level:7.0f}", flush=True)
        proc.terminate()
        print(f"peak {peak:.0f} — set threshold a bit above the quiet floor",
              flush=True)
        return
    listen(threshold, _default_on_text)


if __name__ == "__main__":
    main()
