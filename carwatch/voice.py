"""Speak to the car: capture from the USB mic, transcribe with whisper,
answer through the same grounded @gle pipeline.

This is the CAR voice path (the Pi's mic), distinct from the CodeWatch
Android app voice path. Proven pieces before this module (Aug 12):
  - arecord captures the SF-558 USB mic (verified)
  - whisper-cli transcribes offline with ggml-base.en (verified on a sample)

Everything runs on the Pi, offline. No cloud in the path.

Usage:
    python3 -m carwatch.voice --seconds 5     # record, transcribe, answer
    python3 -m carwatch.voice --transcribe-only /tmp/clip.wav
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

WHISPER_DIR = os.path.expanduser("~/carwatch-stack/whisper.cpp")
WHISPER_CLI = os.path.join(WHISPER_DIR, "build/bin/whisper-cli")
# Prefer the MULTILINGUAL base model when present: petrus speaks Finnish, and
# the .en model transliterates Finnish into garbled English (20.8. first mic
# take). base.en stays as the fallback so a Pi without the extra model still
# works in English.
_MODEL_MULTI = os.path.join(WHISPER_DIR, "models/ggml-base.bin")
_MODEL_EN = os.path.join(WHISPER_DIR, "models/ggml-base.en.bin")
WHISPER_MODEL = _MODEL_MULTI if os.path.exists(_MODEL_MULTI) else _MODEL_EN


def record(seconds: int, out_path: str) -> bool:
    """Capture mono 16 kHz PCM from the default USB mic. True on success."""
    try:
        subprocess.run(
            ["arecord", "-d", str(seconds), "-f", "S16_LE", "-r", "16000",
             "-c", "1", out_path],
            check=True, capture_output=True, timeout=seconds + 10)
        return os.path.getsize(out_path) > 1024
    except Exception as e:
        print(f"record failed: {e}", file=sys.stderr)
        return False


def transcribe(wav_path: str) -> str:
    """Offline speech-to-text via whisper.cpp. Empty string on failure."""
    if not (os.path.exists(WHISPER_CLI) and os.path.exists(WHISPER_MODEL)):
        print("whisper cli/model missing", file=sys.stderr)
        return ""
    try:
        cmd = [WHISPER_CLI, "-m", WHISPER_MODEL, "-f", wav_path, "-nt",
               # Domain vocabulary bias: without it "E10" transcribes as
               # "eating" (petrus's fuel question, on camera, 28 Aug).
               "--prompt",
               "In-car voice assistant, car talk: E10 petrol, E5, gasoline, "
               "fuel tank, tyre pressure, kPa, hybrid battery, OBD, coolant, "
               "rpm, AdBlue, Mercedes E-Class, hello car."]
        if WHISPER_MODEL == _MODEL_MULTI:
            # Language is a CONFIG decision, not a constant. The 20.8 pin to
            # fi (auto once flipped a clip to Italian) Finnishes accented
            # ENGLISH into wake misses and garble ("Helo, Kar" - petrus's
            # live test, 28 Aug). CARWATCH_STT_LANG picks the mode: en for
            # the English video script, fi for Finnish-first days, auto to
            # let whisper guess per clip. Default stays fi.
            cmd += ["-l", os.environ.get("CARWATCH_STT_LANG", "fi")]
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120).stdout
        # -nt strips timestamps; join the spoken lines, drop blanks.
        text = " ".join(line.strip() for line in out.splitlines() if line.strip())
        # whisper emits bracketed markers for non-speech: [BLANK_AUDIO],
        # [MUSIC PLAYING], (silence), etc. Drop anything that is ONLY such a
        # marker, and drop tiny fragments ("So...", "Hmm") that are ambient
        # noise, not a real question - they were false-triggering Qwen.
        import re as _re
        stripped = _re.sub(r"[\[(].*?[\])]", "", text).strip()
        stripped = stripped.strip(" .,-").strip()
        words = [w for w in _re.split(r"\s+", stripped) if w]
        # 3+ words: "on thix" (a 5am ambient-noise fragment) passed the old
        # 2-word bar and @gle answered the furniture. Real questions to a
        # car are practically never under three words.
        if len(words) < 3 or len(stripped) < 10:
            return ""
        # Deterministic fixes for known mishears the prompt bias alone
        # does not always catch.
        stripped = _re.sub(r"\be[- ]?ten\b", "E10", stripped, flags=_re.I)
        stripped = _re.sub(r"\beating\b(?=\s+(gas|gasoline|petrol|fuel))",
                           "E10", stripped, flags=_re.I)
        return stripped
    except Exception as e:
        print(f"transcribe failed: {e}", file=sys.stderr)
        return ""


def ask_gle(question: str) -> str:
    """Route the transcribed question through the SAME grounded pipeline the
    room agent uses, so voice answers are as honest as text ones.

    In-repo module, NOT the old loose ~/gle-ask.py: that script hardcoded a
    stale location/OBD briefing and sat outside self-update's reach, so voice
    answers contradicted the (fixed) room agent. carwatch.ask -> agent._think
    is the one shared brain path."""
    try:
        r = subprocess.run(
            ["python3", "-m", "carwatch.ask", question],
            capture_output=True, text=True, timeout=420,
            cwd=os.path.expanduser("~/CarWatch"),
            env={**os.environ, "CARWATCH_STATE": os.path.expanduser("~/.carwatch")})
        # The subprocess streams "  ... chunk" progress lines for the journal
        # and THEN prints the final answer - keeping both duplicated every
        # answer in the room echo (petrus 28 Aug: "we only want the non
        # breaks complete version"). Drop the stream lines.
        lines = [l for l in r.stdout.splitlines()
                 if not l.lstrip().startswith("...")]
        return "\n".join(lines).strip()
    except Exception as e:
        return f"(could not reach the brain: {e})"


def main() -> None:
    args = sys.argv[1:]
    if "--transcribe-only" in args:
        wav = args[args.index("--transcribe-only") + 1]
        print(transcribe(wav))
        return
    seconds = 5
    if "--seconds" in args:
        seconds = int(args[args.index("--seconds") + 1])

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = f.name
    print(f"listening for {seconds}s ...", flush=True)
    if not record(seconds, wav):
        print("no audio captured", file=sys.stderr)
        return
    heard = transcribe(wav)
    os.unlink(wav)
    if not heard:
        print("HEARD: (nothing / silence)")
        return
    print(f"HEARD: {heard}", flush=True)
    print("--- car answering ---", flush=True)
    print(ask_gle(heard))


if __name__ == "__main__":
    main()
