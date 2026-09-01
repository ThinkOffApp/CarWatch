"""Is the car quiet enough to restart its services right now?

update.sh used to restart chat, agent, presence and obd every hour, no
matter what (issue #23, item 6): mid-drive the dash went dark for a few
seconds, mid-answer the brain's client died and the answer was lost.
This module is the one place that answers "busy or quiet", from the same
files the daemons already write:

  * driving   - ~/.carwatch/obd-all.json says speed > 0 and is fresh, OR
                the car moved within the last MOTION_GRACE_S (obdwatch
                stamps ~/.carwatch/last-motion whenever speed > 0), so a
                red light is not "parked" (codexmb review of #25), OR the
                12 V system reads charging voltage (ignition on / ready)
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
MOTION_GRACE_S = 600     # moved within 10 min = still a trip (red light, drop-off)
IGNITION_VOLTS = 13.2    # 12 V system above this = charging = car is on
VOICE_FRESH_S = 600      # a stale "answering" is a crashed listener, not a talk
BUSY_VOICE_STATES = ("listening", "heard", "answering", "speaking")


def _obd_all_path() -> str:
    return os.path.join(state_dir(), "obd-all.json")


def motion_stamp_path() -> str:
    return os.path.join(state_dir(), "last-motion")


def stamp_motion(now: float | None = None) -> None:
    """obdwatch calls this whenever a reading shows speed > 0."""
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(motion_stamp_path(), "w") as f:
            f.write(str(time.time() if now is None else now))
    except Exception:
        pass


def _voice_state_path() -> str:
    return os.path.join(state_dir(), "voice-state.json")


def _brain_lock_path() -> str:
    from carwatch import voicestate
    return voicestate.BRAIN_LOCK


def _reading(payload: dict, group: str, key: str):
    """A value from either cache shape: grouped dash payload or flat
    readings. None when absent."""
    for getter in (lambda: payload["groups"][group][key]["value"],
                   lambda: payload["readings"][key],
                   lambda: payload[key]):
        try:
            return float(getter())
        except Exception:
            continue
    return None


def _speed_kmh(payload: dict):
    return _reading(payload, "driving", "speed_kmh")


def _module_volts(payload: dict):
    return _reading(payload, "electrical", "module_voltage")


def busy_reasons(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    reasons: list[str] = []
    try:
        with open(_obd_all_path()) as f:
            obd = json.load(f)
        speed = _speed_kmh(obd)
        volts = _module_volts(obd)
        age = now - float(obd.get("ts") or 0)
        if speed is not None and speed > 0 and age < SPEED_FRESH_S:
            reasons.append(f"driving at {speed:g} km/h ({int(age)}s ago)")
        elif volts is not None and volts >= IGNITION_VOLTS and age < SPEED_FRESH_S:
            reasons.append(f"ignition on, 12 V system at {volts:g} V ({int(age)}s ago)")
    except Exception:
        pass
    try:
        with open(motion_stamp_path()) as f:
            moved_age = now - float(f.read().strip() or 0)
        if 0 <= moved_age < MOTION_GRACE_S:
            reasons.append(f"moved {int(moved_age)}s ago (trip grace {MOTION_GRACE_S}s)")
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
