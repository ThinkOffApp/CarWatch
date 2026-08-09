"""Push-to-talk offline voice loop.

Pipeline: record (sox) -> whisper.cpp STT -> llama-server chat -> piper TTS
-> aplay. Every stage is a subprocess against the bench-day stack, so this
file has zero Python dependencies and each stage can be swapped (Gemma 4
E2B's native audio input can later replace the STT stage entirely).

Run modes:
    python3 -m carwatch.voice --once      # one question, then exit
    python3 -m carwatch.voice             # loop: press Enter, speak, answer

Paths default to the bench.sh layout under ~/carwatch-stack; override in
config.json under "voice" if you moved things.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import urllib.request

STACK = os.environ.get("CARWATCH_STACK", os.path.expanduser("~/carwatch-stack"))

DEFAULTS = {
    "whisper_bin": f"{STACK}/whisper.cpp/build/bin/whisper-cli",
    "whisper_model": f"{STACK}/whisper.cpp/models/ggml-base.en.bin",
    "llama_url": "http://127.0.0.1:8080/v1/chat/completions",
    "piper_voice": f"{STACK}/models/en_US-lessac-medium.onnx",
    "record_seconds": 6,
    "system_prompt": (
        "You are the car's onboard assistant. Answer in one or two short "
        "sentences; you are heard, not read. If asked about a warning light "
        "or car feature, be practical and calm."
    ),
}


def record(seconds: int, path: str) -> None:
    subprocess.run(
        ["sox", "-d", "-r", "16000", "-c", "1", "-b", "16", path, "trim", "0", str(seconds)],
        check=True,
    )


def transcribe(cfg: dict, wav: str) -> str:
    out = subprocess.run(
        [cfg["whisper_bin"], "-m", cfg["whisper_model"], "-f", wav, "-nt", "-np"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def ask(cfg: dict, text: str) -> str:
    # Owner's-manual RAG: if a manual is ingested, relevant excerpts ride
    # in as context so warning-light questions get real answers offline.
    system = cfg["system_prompt"]
    try:
        from .manual import context_for
        extra = context_for(text)
        if extra:
            system = f"{system}\n\n{extra}"
    except Exception:
        pass
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "max_tokens": 160,
    }).encode()
    req = urllib.request.Request(
        cfg["llama_url"], data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def speak(cfg: dict, text: str) -> None:
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    subprocess.run(
        ["piper", "--model", cfg["piper_voice"], "--output_file", wav],
        input=text, text=True, check=True,
    )
    subprocess.run(["aplay", "-q", wav], check=True)
    os.unlink(wav)


def one_round(cfg: dict) -> None:
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    print(f"Listening for {cfg['record_seconds']}s…")
    record(cfg["record_seconds"], wav)
    heard = transcribe(cfg, wav)
    os.unlink(wav)
    print(f"You: {heard}")
    if not heard:
        return
    answer = ask(cfg, heard)
    print(f"Car: {answer}")
    speak(cfg, answer)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    cfg = dict(DEFAULTS)
    try:
        from .config import Config
        user = Config.load()
        cfg.update(getattr(user, "voice", None) or {})
    except Exception:
        pass  # bench mode: defaults are fine without /etc/carwatch
    if args.once:
        one_round(cfg)
        return
    print("Push-to-talk: press Enter to speak, Ctrl-C to quit.")
    while True:
        input()
        one_round(cfg)


if __name__ == "__main__":
    main()
