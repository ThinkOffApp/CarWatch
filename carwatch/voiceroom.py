"""Room voice notes, both directions (petrus 19.8.: "Voice viestit ja
vastaus voicella").

A human posts a voice note in the room -> the car downloads it, transcribes
it on board (whisper.cpp), thinks with the same one brain path every other
channel uses, and replies WITH VOICE: piper TTS audio uploaded to the room
plus the text so the exchange stays readable and searchable.

Every human voice note is treated as addressed to the car: a speaker cannot
type "@eclass" into audio, the notes are rare, and on a drive the whole
point is to just talk. Notes from agents (and the car itself) are ignored.

stdlib only, like the rest of the package. The heavy lifting is subprocess
calls to ffmpeg / whisper-cli / piper that already live on the Pi.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
import uuid

STACK = os.path.expanduser("~/carwatch-stack")
WHISPER_BIN = os.path.join(STACK, "whisper.cpp/build/bin/whisper-cli")
# Multilingual first (petrus speaks Finnish); the English-only model stays
# as the fallback so the feature degrades instead of dying if the
# multilingual download is missing.
WHISPER_MODELS = [
    os.path.join(STACK, "whisper.cpp/models/ggml-base.bin"),
    os.path.join(STACK, "whisper.cpp/models/ggml-base.en.bin"),
]
PIPER_BIN = os.path.expanduser("~/.local/bin/piper")
VOICE_EN = os.path.join(STACK, "models/en_US-lessac-medium.onnx")
VOICE_FI = os.path.join(STACK, "models/fi_FI-harri-medium.onnx")
WORK_DIR = "/tmp/carwatch-voice"

# Finnish detection for picking the reply voice: distinctive letters or a
# few very common words. Crude on purpose - a wrong pick still produces
# intelligible audio, and the text rides along in the same message.
_FI_HINT = re.compile(
    r"[äöÄÖ]|\b(ja|on|ei|se|että|mitä|olen|sinä|auto|moottori|akku)\b",
    re.IGNORECASE)


def is_voice_note(msg: dict) -> bool:
    return bool(msg.get("audio_url"))


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def transcribe(audio_url: str) -> str:
    """Voice note URL -> text, empty string on any failure."""
    os.makedirs(WORK_DIR, exist_ok=True)
    stem = os.path.join(WORK_DIR, uuid.uuid4().hex[:12])
    raw = stem + ".src"
    wav = stem + ".wav"
    try:
        with urllib.request.urlopen(audio_url, timeout=30) as r:
            with open(raw, "wb") as f:
                f.write(r.read())
        _run(["ffmpeg", "-y", "-i", raw, "-ar", "16000", "-ac", "1", wav], 60)
        model = next((m for m in WHISPER_MODELS if os.path.exists(m)), None)
        if not model or not os.path.exists(wav):
            return ""
        # -l auto lets the multilingual model hear Finnish as Finnish; the
        # .en fallback ignores the flag's effect and stays English-only.
        p = _run([WHISPER_BIN, "-m", model, "-l", "auto", "-nt",
                  "--no-prints", "-f", wav], 180)
        return p.stdout.strip()
    except Exception:
        return ""
    finally:
        for path in (raw, wav):
            try:
                os.remove(path)
            except OSError:
                pass


def tts_wav(text: str) -> str:
    """Text -> spoken WAV path, empty string on failure.

    Voice follows the reply's language so Finnish answers do not come out
    as an English voice mangling Finnish words. Callers own the file.
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    wav = os.path.join(WORK_DIR, uuid.uuid4().hex[:12] + ".wav")
    voice = VOICE_FI if (_FI_HINT.search(text) and os.path.exists(VOICE_FI)) \
        else VOICE_EN
    if not (os.path.exists(PIPER_BIN) and os.path.exists(voice)):
        return ""
    try:
        p = subprocess.run([PIPER_BIN, "-m", voice, "-f", wav],
                           input=text, capture_output=True, text=True,
                           timeout=120)
        if p.returncode != 0 or not os.path.exists(wav):
            return ""
        return wav
    except Exception:
        return ""


def synthesize(text: str) -> str:
    """Text -> spoken audio file (m4a) path, empty string on failure."""
    wav = tts_wav(text)
    if not wav:
        return ""
    m4a = wav[:-4] + ".m4a"
    try:
        # m4a/AAC because that is what phones themselves record and play.
        c = _run(["ffmpeg", "-y", "-i", wav, "-c:a", "aac", "-b:a", "64k",
                  m4a], 60)
        return m4a if c.returncode == 0 and os.path.exists(m4a) else ""
    except Exception:
        return ""
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def _upload(config: dict, path: str) -> str:
    """Multipart upload to the room media store -> public URL ('' on fail)."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: audio/mp4\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        config["api_base"].rstrip("/") + "/upload",
        data=body, method="POST",
        headers={
            "X-API-Key": config["api_key"],
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode()).get("url", "")
    except Exception:
        return ""


def post_voice_reply(config: dict, text: str, spoken: str | None = None) -> bool:
    """Speak the reply into the room: audio when possible, text always.

    spoken overrides what gets voiced (e.g. the answer alone, without the
    (kuulin: ...) transcript prefix that belongs only in the text body)."""
    audio_url = ""
    m4a = synthesize(spoken if spoken is not None else text)
    if m4a:
        audio_url = _upload(config, m4a)
        try:
            os.remove(m4a)
        except OSError:
            pass
    payload = {"room": config["room"], "body": text}
    if audio_url:
        payload["audio_url"] = audio_url
    req = urllib.request.Request(
        config["api_base"].rstrip("/") + "/messages",
        data=json.dumps(payload).encode(), method="POST",
        headers={"X-API-Key": config["api_key"],
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 201)
    except Exception:
        return False
