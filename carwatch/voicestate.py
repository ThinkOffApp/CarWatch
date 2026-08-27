"""Shared voice-interaction state + the one-question-at-a-time brain lock.

petrus, 27 Aug (from the car, after a day of talking to silence): "I don't
have any indication if it heard what I said at all" and "you said it was
processing two Qs at once? that should not happen." This module is both
fixes: a tiny state file every voice participant writes (listener, brain
path, webchat) so the dashboard can SHOW the loop's state live, and a file
lock so exactly one question occupies the brain at a time (two parallel
generations serialize at ~3.5 tok/s each and both crawl).

States: idle -> armed (Speak button pressed, next utterance is for the car)
-> listening (speech detected, capturing) -> heard (transcribed, addressed)
-> answering (brain busy; started_at + expect_s drive the dash progress bar)
-> speaking (answer voiced through whatever output exists) -> idle.
An unaddressed utterance goes back to idle with a note, so "it heard you
but you did not say the wake word" is visible instead of silent.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager

STATE_PATH = os.path.expanduser("~/.carwatch/voice-state.json")
STATS_PATH = os.path.expanduser("~/.carwatch/voice-stats.json")
BRAIN_LOCK = "/tmp/carwatch-brain.lock"
# The Speak button arms the mic this long: speech starting inside the window
# is addressed to the car, wake word or not.
ARM_FILE = "/tmp/carwatch-listen-now"
ARM_WINDOW_S = 25
DEFAULT_EXPECT_S = 90


def set_state(state: str, **fields) -> None:
    d = {"state": state, "ts": time.time()}
    d.update(fields)
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass  # state is a courtesy; never break the voice path over it


def get_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"state": "idle", "ts": 0}


def arm() -> None:
    """Speak button: the next utterance is for the car, no wake word needed."""
    with open(ARM_FILE, "w") as f:
        f.write(str(time.time()))
    set_state("armed")


def armed() -> bool:
    try:
        return time.time() - os.path.getmtime(ARM_FILE) < ARM_WINDOW_S
    except OSError:
        return False


def consume_arm() -> None:
    try:
        os.unlink(ARM_FILE)
    except OSError:
        pass


def expect_s() -> float:
    """Typical answer duration, from the last few real ones."""
    try:
        with open(STATS_PATH) as f:
            hist = json.load(f).get("answer_s") or []
        if hist:
            return sorted(hist)[len(hist) // 2]
    except Exception:
        pass
    return DEFAULT_EXPECT_S


def record_answer_s(seconds: float) -> None:
    try:
        hist = []
        try:
            with open(STATS_PATH) as f:
                hist = json.load(f).get("answer_s") or []
        except Exception:
            pass
        hist = (hist + [round(seconds, 1)])[-5:]
        tmp = STATS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"answer_s": hist}, f)
        os.replace(tmp, STATS_PATH)
    except Exception:
        pass


@contextmanager
def brain_lock():
    """Exactly one question in the brain. Blocking: a second ask WAITS its
    turn instead of degrading both generations (27 Aug double-fire)."""
    f = open(BRAIN_LOCK, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()
