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
from carwatch import voicestate

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
# PHRASES, not bare nouns (petrus 28 Aug: "I want it to listen with button
# or wake word, not randomly"): the old bare "car" fired word-boundary on
# ordinary sentences, which felt exactly like always-on. The Speak button
# always works regardless of these.
# Spelling variants included: whisper in Finnish mode writes "Kar"/"Helo"
# for accented English ("Hello, Kar, how are you today?" was a real wake
# MISS in petrus's live test, 28 Aug).
WAKE_WORDS = ("hello car", "helo car", "hello kar", "helo kar", "hey car",
              "hei auto", "moi auto", "hyvä auto", "hello gle", "vadelma")
# After the car answers, the conversation stays open this long: a reply
# like "Thanks! And what about..." needs no new wake phrase.
FOLLOWUP_S = 30.0
_followup_until = 0.0

# The car's own last answer, for echo suppression (petrus mid-shoot 28 Aug:
# the mic reopened while the cabin speakers were still playing the answer's
# tail, the tail transcribed, and the FOLLOW-UP window - any utterance counts
# as addressed - made the car answer itself in a loop). Two guards: _speak
# holds the mic closed until the wav's computed duration has really elapsed
# in the cabin, and transcripts that are token-copies of the last answer are
# dropped before the follow-up/wake logic ever sees them.
_last_spoken = {"tokens": frozenset(), "start": 0.0, "end": 0.0}
ECHO_TAIL_SEC = 3.0           # BT + MBUX keep playing after aplay exits;
                              # 3.0 measured good live (claudemm, 28 Aug)
ECHO_MATCH_WINDOW_SEC = 30.0  # straggling buffered echo can arrive this late


def _echo_tail_sec() -> float:
    try:
        from carwatch.config import load_raw
        return float((load_raw().get("voice") or {}).get("echo_tail_sec"))
    except Exception:
        return ECHO_TAIL_SEC


def _tokens(text: str) -> list:
    return [t for t in re.sub(r"[^\wäöå]+", " ", text.lower()).split() if t]


def _is_self_echo(text: str) -> bool:
    """True when a transcript is (a fragment of) the answer the car just
    spoke. Token containment, not equality: the mic hears the tail of the
    answer mid-sentence and whisper is not verbatim. Needs >=4 tokens so a
    short real follow-up ("yes", "thanks") is never swallowed - the
    mic-closed hold in _speak covers the short tails physically."""
    heard = _tokens(text)
    if len(heard) < 4 or not _last_spoken["tokens"]:
        return False
    if time.time() > _last_spoken["end"] + ECHO_MATCH_WINDOW_SEC:
        return False
    hit = sum(1 for t in heard if t in _last_spoken["tokens"])
    return hit / len(heard) >= 0.8


def _wake_words():
    try:
        from carwatch.config import load_raw
        w = (load_raw().get("voice") or {}).get("wake_words")
        if w == []:
            return None          # explicit opt-out: always-on
        if w:
            return tuple(str(x).lower() for x in w)
    except Exception:
        pass
    return WAKE_WORDS


def _addressed(text: str, wake) -> bool:
    """Word-boundary wake match on NORMALIZED text. Substring matching let
    'car' hide inside 'caramelli' (20.8.) so words stay whole - but the raw
    transcript carries punctuation ("Hello, Kar, ...") that broke phrase
    matching, so punctuation collapses to spaces before the search."""
    return _wake_cut(text, wake) is not None


def _wake_cut(text: str, wake):
    """Find the wake phrase and return only what comes AFTER it - that is
    the question. Everything spoken before the wake phrase is preamble
    (petrus 28 Aug: rehearsing the pitch put the whole intro into one VAD
    utterance and the full ramble went to the brain as the question).
    Returns None when no wake phrase is present."""
    norm = re.sub(r"[^\wäöå]+", " ", text.lower()).strip()
    pat = re.compile(r"\b(" + "|".join(re.escape(w) for w in wake) + r")\b")
    m = pat.search(norm)
    if not m:
        return None
    return norm[m.end():].strip()


def handle_utterance(frames: bytes, on_text):
    """Transcribe one utterance and hand it to on_text. Returns whatever
    on_text returns (the voice handler returns the answer TEXT so listen()
    can speak it after releasing the mic)."""
    from carwatch import lights  # local import: keeps lights fully optional
    was_armed = voicestate.armed()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    _write_wav(path, frames)
    lights.signal("thinking")   # transcribing + answering
    text = transcribe(path)
    os.unlink(path)
    result = None
    if text and _is_self_echo(text):
        # The mic caught the car's own answer coming out of the cabin
        # speakers. This must be dropped BEFORE the armed/follow-up logic:
        # inside the follow-up window every utterance counts as addressed,
        # which is exactly how the car ended up answering itself (28 Aug).
        print(f"(ignored self-echo): {text[:60]}", flush=True)
        voicestate.set_state("idle")
        lights.signal("idle")
        return None
    if text:
        wake = _wake_words()
        # The Speak button arms the mic: an armed utterance is for the car
        # even without a wake word (petrus's dash flow, 27 Aug). A fresh
        # answer keeps the conversation open the same way (follow-up window).
        by_button = voicestate.armed()
        if by_button:
            voicestate.consume_arm()
        if not by_button and time.time() < _followup_until:
            by_button = True  # conversation continuation counts as armed
        if not by_button and wake:
            cut = _wake_cut(text, wake)
            if cut is None:
                # Not addressed to the car: journal only, dash stays QUIET.
                # (petrus 28 Aug wants unarmed hearing invisible - the strip
                # reacting to room chatter read as always-on surveillance.)
                print(f"(ignored, no wake word): {text[:60]}", flush=True)
                voicestate.set_state("idle")
                lights.signal("idle")
                return None
            if len(cut.split()) < 2:
                # Wake phrase alone, no question in the same breath.
                print(f"(wake heard, no question): {text[:60]}", flush=True)
                voicestate.set_state(
                    "idle", note="heard the wake phrase - ask the question "
                    "in the same breath")
                lights.signal("idle")
                return None
            text = cut  # the question starts AFTER the wake phrase
        print(f"HEARD: {text}", flush=True)
        voicestate.set_state("heard", text=text)
        result = on_text(text)
    else:
        # Duration + level make the journal diagnosable: a 0.8s spike at rms
        # 4000 is a slammed door, 6s at 1300 is someone talking too far from
        # the mic - without these the two are the same mute line.
        secs = len(frames) / (RATE * 2)
        print(f"(captured sound but no clear speech: {secs:.1f}s, rms {rms(frames):.0f})",
              flush=True)
        # Visible only when the button asked us to listen; ambient noise
        # otherwise ends silently (same petrus rule as above).
        if was_armed:
            voicestate.set_state("idle",
                                 note=f"caught sound but no clear speech ({secs:.1f}s)")
        else:
            voicestate.set_state("idle")
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
        # Match anywhere in the line, not just the card's bracket name: the
        # SF-558 enumerates as 'card 2: SF558 [SF-558], device 0: USB Audio'
        # - its 'USB' lives in the device half, and the card-name-only match
        # made every mic consumer skip a mic that arecord plainly listed.
        if m and re.search(r"jabra|speak|usb", line, re.IGNORECASE):
            return f"plughw:{m.group(1)},{m.group(3)}"
    return None


def _open_mic():
    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS),
           "-t", "raw"]
    # Priority: USB speakerphone, then BT headset SCO (the headset is both
    # directions since 20.8., petrus removed the old USB mic), then default.
    # CARWATCH_MIC overrides: "bt" prefers the headset SCO even with a USB
    # mic attached (video config, 28 Aug: XM5 is THE mic, the SF-558 stays
    # plugged purely as the bench recorder), "usb" forces USB, unset/auto
    # keeps the priority above.
    pref = (os.environ.get("CARWATCH_MIC") or "auto").lower()
    usb = _usb_audio_device("capture")
    mac = _bt_pcm_mac("hfpag/source")
    if pref == "bt" and mac:
        usb = None
    elif pref == "usb":
        mac = None
    elif usb:
        mac = None
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


def _car_a2dp_mac():
    """The paired car head unit's BT MAC (written by car-speak.sh). Speaking
    through THIS sink is the proven music channel the brief uses. The generic
    hfpag/a2dpsrc scans stay as fallbacks only: with no call up, MBUX keeps
    the HFP call channel CLOSED and audio sent there exits 0 into silence
    (claudemm's live find, 28 Aug - answers were text-only in the cabin)."""
    try:
        mac = open(os.path.expanduser("~/.carwatch/car-bt-mac")).read().strip()
        return mac or None
    except Exception:
        return None


def _speak(text: str) -> bool:
    """Voice a reply through the car's A2DP sink (the brief's music channel),
    falling back to USB playback, then the headset channels. Call ONLY with
    the mic closed: while SCO capture is live the headset sits in HFP mode
    and the A2DP sink it would play through does not exist (the 20.8.
    aplay-exit-0-but-silence bug). Does not return until the audio has
    actually FINISHED in the cabin (wav duration + tail): the player exiting
    only means the BT/MBUX buffer was written, and reopening the mic into
    the still-playing tail is how the car answered itself (28 Aug)."""
    from carwatch import voiceroom  # language-aware voice pick (fi/en)
    wav = voiceroom.tts_wav(text)
    if not wav:
        print("speak: TTS failed (piper/voice missing?)", flush=True)
        return False
    try:
        try:
            with wave.open(wav, "rb") as w:
                dur = w.getnframes() / float(w.getframerate() or RATE)
        except Exception:
            dur = 3.0 + len(text) / 12.0   # rough speech-rate estimate
        play_timeout = max(60, int(dur * 2) + 15)  # 30s truncated a 44s answer
        car = _car_a2dp_mac()
        target = None
        bt = False
        if car:
            # Bond can exist while the A2DP link is down; connect is cheap
            # when already connected (car-speak.sh does the same).
            if _bt_pcm_mac("a2dpsrc/sink") != car:
                subprocess.run(["bluetoothctl", "connect", car],
                               capture_output=True, timeout=10)
                time.sleep(2)
            target = f"bluealsa:DEV={car},PROFILE=a2dp"
            bt = True
        if not target:
            target = _usb_audio_device("playback")
        if not target:
            # Headset-only (bench / walking): prefer the HFP call channel -
            # on a multipoint headset the A2DP slot may belong to the PHONE
            # and audio played there exits 0 into silence (28 Aug, XM5).
            bt = True
            mac = _bt_pcm_mac("hfpag/sink")
            if mac:
                target = f"bluealsa:DEV={mac},PROFILE=sco"
            else:
                mac = _bt_pcm_mac("a2dpsrc/sink")
                if not mac:
                    print("speak: no car, USB or BT audio output connected",
                          flush=True)
                    return False
                target = f"bluealsa:DEV={mac},PROFILE=a2dp"
        start = time.time()
        _last_spoken.update(tokens=frozenset(_tokens(text)),
                            start=start, end=start + dur)
        # Radio quiet window: hold OBD polling off the shared BT radio for
        # the playback (same file webchat's play path writes and obdwatch
        # honors). Voice answers never set this, so obdwatch polled the BT
        # dongle every ~20s straight through them - measured mid-answer on
        # the 28 Aug shoot (poll at 14:30:51 inside the stuttering answer);
        # the brief was smooth because ITS path sets the window.
        try:
            with open("/tmp/carwatch-audio-quiet-until", "w") as qf:
                qf.write(str(start + dur + 20))
        except Exception:
            pass
        if bt:
            time.sleep(1.5)   # let a shared headset fall back from HFP mode
        rc = subprocess.run(
            ["aplay", "--buffer-time=1000000", "-D", target, wav],
            capture_output=True, timeout=play_timeout).returncode
        if rc == 0:
            # Hold until the cabin is actually quiet before the caller
            # reopens the mic: computed end of the audio plus a tail for
            # the BT/MBUX buffers (voice.echo_tail_sec overrides).
            time.sleep(max(0.0, start + dur + _echo_tail_sec() - time.time()))
        return rc == 0
    except Exception as e:
        print(f"speak failed: {e}", flush=True)
        return False
    finally:
        os.unlink(wav)


def listen(threshold: float, on_text) -> None:
    # A restart mid-exchange must not leave a zombie "answering" on the
    # dash (petrus 28 Aug: 'why is it "answering"? I did not ask anything')
    voicestate.set_state("idle")
    """Stream the mic forever; emit each detected utterance to on_text.

    Resilient to transient arecord failures (EINTR, brief device hiccups):
    the mic is reopened rather than the loop dying, so a running service
    keeps listening through interruptions. Only ONE instance may hold the
    mic - run this as the single carwatch-listen service, never alongside
    another arecord (mic contention was the Aug 12 failure).
    """
    proc = _open_mic()
    opened_at = time.time()
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
                if time.time() - opened_at < 2:
                    # Died instantly although a device exists = the device is
                    # WEDGED, usually a leaked arecord still holding it (the
                    # 20.8. reopen storm after the first USB speak cycle).
                    # This service is the mic's sole legitimate owner, so
                    # clearing every arecord is safe - then breathe.
                    subprocess.run(["pkill", "-9", "-x", "arecord"],
                                   capture_output=True)
                    time.sleep(2)
                has_mic = _usb_audio_device("capture") or _bt_pcm_mac("hfpag/source")
                time.sleep(0.3 if has_mic else 5)
                try:
                    proc.terminate()
                except Exception:
                    pass
                proc = _open_mic()
                opened_at = time.time()
                speech, quiet, in_speech = [], 0, False
                continue
            level = rms(chunk)
            if level >= threshold:
                if not in_speech:
                    in_speech = True
                    # The strip shows "listening" ONLY when the button armed
                    # it (petrus 28 Aug: "why is it listening already? i
                    # didn't press or wake"). Unarmed capture stays visually
                    # silent; the wake-word check still runs underneath.
                    if voicestate.armed():
                        voicestate.set_state("listening")
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
                                # A terminate that did not stick leaves the
                                # capture device held forever and every
                                # reopen fails busy - make sure it is dead.
                                try:
                                    proc.kill()
                                    proc.wait(timeout=5)
                                except Exception:
                                    pass
                            voicestate.set_state("speaking", answer=reply)
                            _speak(reply)
                            # Conversation continues: for FOLLOWUP_S after an
                            # answer the next utterance needs NO wake phrase
                            # (the script's "Thanks! I've been wondering..."
                            # follow-ups carry none - claudemm's catch before
                            # the take, 28 Aug). Speak only to the car during
                            # this window.
                            global _followup_until
                            _followup_until = time.time() + FOLLOWUP_S
                            voicestate.set_state(
                                "idle", answer=reply,
                                note=f"follow-up window open {int(FOLLOWUP_S)}s"
                                " - just speak, no wake phrase needed")
                            proc = _open_mic()
                            opened_at = time.time()
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
    from carwatch.room import post_as_car
    post_as_car(f"(heard you say: \"{text}\")\n\n{answer}")


def _voice_on_text(text: str):
    """Spoken conversation: heard speech -> grounded answer -> VOICED back
    through the headset/car speakers, plus a room transcript so the exchange
    stays visible on the phone/watch. Returns the answer text; listen() does
    the actual speaking after it has released the mic."""
    answer = ask_gle(text)
    if not answer:
        return None
    from carwatch.room import post_as_car
    post_as_car(f"(heard: \"{text}\")\n\n{answer}")
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
