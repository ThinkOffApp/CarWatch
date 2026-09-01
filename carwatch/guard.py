"""Is the car quiet enough to restart its services right now?

update.sh used to restart chat, agent, presence and obd every hour, no
matter what (issue #23, item 6): mid-drive the dash went dark for a few
seconds, mid-answer the brain's client died and the answer was lost.
This module is the one place that answers "busy or quiet", from the same
files the daemons already write:

  * driving   - ~/.carwatch/obd-all.json says speed > 0 and is fresh
  * voice     - ~/.carwatch/voice-state.json is listening/answering/speaking
  * brain     - /tmp/carwatch-brain.lock is held (an answer is generating)

Shell use (scripts/restart-when-quiet.sh):
    python3 -m carwatch.guard        # exit 0 quiet, exit 1 busy (+reasons)
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time

from carwatch.config import state_dir

SPEED_FRESH_S = 120      # an OBD sample older than this says nothing
VOICE_FRESH_S = 600      # a stale "answering" is a crashed listener, not a talk
BUSY_VOICE_STATES = ("listening", "heard", "answering", "speaking")


def _obd_all_path() -> str:
    return os.path.join(state_dir(), "obd-all.json")


def _voice_state_path() -> str:
    return os.path.join(state_dir(), "voice-state.json")


def _brain_lock_path() -> str:
    from carwatch import voicestate
    return voicestate.BRAIN_LOCK


def _speed_kmh(payload: dict):
    """speed_kmh from either cache shape: grouped dash payload or flat
    readings. None when absent."""
    try:
        v = payload["groups"]["driving"]["speed_kmh"]["value"]
        return float(v)
    except Exception:
        pass
    try:
        return float(payload["readings"]["speed_kmh"])
    except Exception:
        pass
    try:
        return float(payload["speed_kmh"])
    except Exception:
        return None


def busy_reasons(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    reasons: list[str] = []
    try:
        with open(_obd_all_path()) as f:
            obd = json.load(f)
        speed = _speed_kmh(obd)
        age = now - float(obd.get("ts") or 0)
        if speed is not None and speed > 0 and age < SPEED_FRESH_S:
            reasons.append(f"driving at {speed:g} km/h ({int(age)}s ago)")
    except Exception:
        pass
    try:
        with open(_voice_state_path()) as f:
            vs = json.load(f)
        state = str(vs.get("state") or "idle")
        age = now - float(vs.get("ts") or 0)
        if state in BUSY_VOICE_STATES and age < VOICE_FRESH_S:
            reasons.append(f"voice {state} ({int(age)}s ago)")
    except Exception:
        pass
    lock_path = _brain_lock_path()
    if os.path.exists(lock_path):
        try:
            fh = open(lock_path, "a")
            try:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh, fcntl.LOCK_UN)
                except OSError:
                    reasons.append("brain answering (lock held)")
            finally:
                fh.close()
        except Exception:
            pass
    return reasons


def is_quiet() -> bool:
    return not busy_reasons()


def main() -> int:
    reasons = busy_reasons()
    if reasons:
        print("busy: " + "; ".join(reasons))
        return 1
    print("quiet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
