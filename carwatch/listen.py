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
import re
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


# Wake-word gate (claudemm's Helsinki observation, Aug 14 night): the mic
# transcribes FINNISH bystander speech as garbled English and the car
# politely answers each fragment, posting overheard speech into the room.
# The car now only engages when addressed. Overridable per config:
# voice.wake_words = [] restores always-on; a custom list replaces these.
WAKE_WORDS = ("gle", "glee", "e-class", "e class", "eclass", "car", "vadelma")


def _wake_words():
    try:
        import json
        cfg = json.load(open(os.path.expanduser("~/.carwatch/config.json")))
        w = (cfg.get("voice") or {}).get("wake_words")
        if w == []:
            return None          # explicit opt-out: always-on
        if w:
            return tuple(str(x).lower() for x in w)
    except Exception:
        pass
    return WAKE_WORDS


def handle_utterance(frames: bytes, on_text):
    """Transcribe one utterance and hand it to on_text. Returns whatever
    on_text returns (the voice handler returns the answer TEXT so listen()
    can speak it after releasing the mic)."""
    from carwatch import lights  # local import: keeps lights fully optional
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    _write_wav(path, frames)
    lights.signal("thinking")   # transcribing + answering
    text = transcribe(path)
    os.unlink(path)
    result = None
    if text:
        wake = _wake_words()
        if wake and not any(w in text.lower() for w in wake):
            # Not addressed to the car: log a stub locally, never post.
            print(f"(ignored, no wake word): {text[:60]}", flush=True)
            lights.signal("idle")
            return None
        print(f"HEARD: {text}", flush=True)
        result = on_text(text)
    else:
        # Duration + level make the journal diagnosable: a 0.8s spike at rms
        # 4000 is a slammed door, 6s at 1300 is someone talking too far from
        # the mic - without these the two are the same mute line.
        secs = len(frames) / (RATE * 2)
        print(f"(captured sound but no clear speech: {secs:.1f}s, rms {rms(frames):.0f})",
              flush=True)
    lights.signal("idle")       # back to calm when done
    return result


def _bt_pcm_mac(suffix: str):
    """MAC of the connected BT device offering the given bluealsa PCM
    (e.g. 'hfpag/source' = headset mic, 'a2dpsrc/sink' = playback)."""
    try:
        out = subprocess.run(["bluealsa-cli", "list-pcms"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if line.strip().endswith(suffix):
            m = re.search(r"dev_([0-9A-F_]+)/", line)
            if m:
                return m.group(1).replace("_", ":")
    return None


def _usb_audio_device(kind: str):
    """ALSA device string for an attached USB audio device, else None.

    kind: 'capture' (arecord -l) or 'playback' (aplay -l). Returns
    'plughw:N,M' - plughw so ALSA resamples to our 16 kHz. A wired USB
    speakerphone (the Jabra Speak2 40, ordered 20.8.) beats Bluetooth:
    hardware echo cancel, full duplex, and none of the A2DP/HFP mode
    switching that mutes one direction while the other is live.
    """
    cmd = ["arecord", "-l"] if kind == "capture" else ["aplay", "-l"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        m = re.match(r"card (\d+): [^\[]*\[([^\]]*)\], device (\d+):", line)
        if m and re.search(r"jabra|speak|usb", m.group(2), re.IGNORECASE):
            return f"plughw:{m.group(1)},{m.group(3)}"
    return None


def _open_mic():
    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS),
           "-t", "raw"]
    # Priority: USB speakerphone, then BT headset SCO (the headset is both
    # directions since 20.8., petrus removed the old USB mic), then default.
    usb = _usb_audio_device("capture")
    mac = None if usb else _bt_pcm_mac("hfpag/source")
    if usb:
        cmd[1:1] = ["-D", usb]
        print(f"mic: USB audio {usb}", flush=True)
    elif mac:
        cmd[1:1] = ["-D", f"bluealsa:DEV={mac},PROFILE=sco"]
        print(f"mic: bluetooth HFP {mac}", flush=True)
    else:
        print("mic: default ALSA device", flush=True)
    # stderr silenced: with no capture device present arecord prints a
    # multi-line ALSA error block, and the reopen loop would flood the
    # journal with it (20.8., headset off).
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)


def _speak(text: str) -> bool:
    """Voice a reply through the connected BT sink over A2DP. Call ONLY with
    the mic closed: while SCO capture is live the headset sits in HFP mode
    and the A2DP sink it would play through does not exist (the 20.8.
    aplay-exit-0-but-silence bug)."""
    from carwatch import voiceroom  # language-aware voice pick (fi/en)
    target = _usb_audio_device("playback")
    bt = target is None
    if bt:
        mac = _bt_pcm_mac("a2dpsrc/sink")
        if not mac:
            print("speak: no USB or BT audio output connected", flush=True)
            return False
        target = f"bluealsa:DEV={mac},PROFILE=a2dp"
    wav = voiceroom.tts_wav(text)
    if not wav:
        print("speak: TTS failed (piper/voice missing?)", flush=True)
        return False
    try:
        if bt:
            time.sleep(1.5)   # let the headset fall back from HFP to A2DP
        rc = subprocess.run(
            ["aplay", "-D", target, wav],
            capture_output=True, timeout=180).returncode
        return rc == 0
    except Exception as e:
        print(f"speak failed: {e}", flush=True)
        return False
    finally:
        os.unlink(wav)


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
                # No mic around at all (no USB device, headset off) means
                # every reopen fails instantly - back off so the loop idles
                # gently until something appears instead of hammering arecord.
                has_mic = _usb_audio_device("capture") or _bt_pcm_mac("hfpag/source")
                time.sleep(0.3 if has_mic else 5)
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
                        reply = handle_utterance(b"".join(speech), on_text)
                        if reply:
                            # Voice the answer with the mic CLOSED: SCO and
                            # A2DP cannot be live at once on one headset,
                            # then reopen (also discards echo of our own
                            # voice buffered during playback).
                            try:
                                proc.terminate()
                                proc.wait(timeout=5)
                            except Exception:
                                pass
                            _speak(reply)
                            proc = _open_mic()
                    speech = []
    finally:
        proc.terminate()


def _default_on_text(text: str) -> None:
    # For a bare run: answer to stdout. The service wires a poster instead.
    print("--- car answering ---", flush=True)
    print(ask_gle(text), flush=True)


def _post_on_text(text: str) -> None:
    """Full hands-free loop: heard speech -> grounded answer -> room, as @gle,
    so speaking to the car produces a visible reply, not just a log line."""
    answer = ask_gle(text)
    if not answer:
        return
    try:
        with open("/tmp/gle_text.txt", "w") as f:
            f.write(f"(heard you say: \"{text}\")\n\n{answer}")
        subprocess.run(
            ["python3", os.path.expanduser("~/post-as-gle.py")],
            timeout=30, capture_output=True)
    except Exception as e:
        print(f"post failed: {e}", flush=True)


def _voice_on_text(text: str):
    """Spoken conversation: heard speech -> grounded answer -> VOICED back
    through the headset/car speakers, plus a room transcript so the exchange
    stays visible on the phone/watch. Returns the answer text; listen() does
    the actual speaking after it has released the mic."""
    answer = ask_gle(text)
    if not answer:
        return None
    try:
        with open("/tmp/gle_text.txt", "w") as f:
            f.write(f"(heard: \"{text}\")\n\n{answer}")
        subprocess.run(
            ["python3", os.path.expanduser("~/post-as-gle.py")],
            timeout=30, capture_output=True)
    except Exception as e:
        print(f"post failed: {e}", flush=True)
    return answer


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
    if "--voice" in sys.argv:
        on_text = _voice_on_text
    elif "--post" in sys.argv:
        on_text = _post_on_text
    else:
        on_text = _default_on_text
    listen(threshold, on_text)


if __name__ == "__main__":
    main()
