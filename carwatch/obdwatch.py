"""Zero-touch OBD: watch the cable, read the engine, post the numbers.

The Aug 12 lesson made executable: petrus should never sit in the car doing
steps for me. This daemon runs on the Pi permanently. When the ENET cable
gets an electrical link (Pi in the car, ignition on), it waits a beat for
the gateway to boot, runs the full OBD session (carwatch.obd_session), and
posts the result to the room as @gle - real numbers, or the honest failure
trace. Petrus does nothing but drive.

Posts are rate-limited: one on link-up, then only when readings change
meaningfully or on link-loss/regain - no hourly spam (see the room-post
cadence rule).

Runs as carwatch-obd.service.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from carwatch.obd_session import run_session

CARRIER = "/sys/class/net/eth0/carrier"
POLL_S = 5
SETTLE_S = 6          # give the gateway a moment after link-up
RETRY_COOLDOWN_S = 120  # after a failed session, retry this often while up
GLE_TXT = "/tmp/gle_text.txt"
POSTER = os.path.expanduser("~/post-as-gle.py")


def carrier_up() -> bool:
    try:
        with open(CARRIER) as f:
            return f.read().strip() == "1"
    except Exception:
        return False


def post(text: str) -> None:
    try:
        with open(GLE_TXT, "w") as f:
            f.write(text)
        subprocess.run(["python3", POSTER], timeout=30, capture_output=True)
    except Exception as e:
        print(f"post failed: {e}", flush=True)


def fmt_readings(r: dict) -> str:
    parts = []
    if "engine_rpm" in r:
        parts.append(f"engine {r['engine_rpm']:.0f} rpm")
    if "coolant_c" in r:
        parts.append(f"coolant {r['coolant_c']} C")
    if "speed_kmh" in r:
        parts.append(f"speed {r['speed_kmh']} km/h")
    if "module_voltage" in r:
        parts.append(f"battery/system {r['module_voltage']:.1f} V")
    return ", ".join(parts) or "no readings"


def failure_hint(result: dict) -> str:
    stages = {t.get("stage"): t for t in result.get("trace", [])}
    if not stages.get("discover", {}).get("ok", False):
        return ("cable link is up but the car's diagnostic gateway did not "
                "answer identification - this is where we learn whether the "
                "ENET cable truly speaks to the GLE")
    if not stages.get("connect", {}).get("ok", False):
        return "gateway found but refused the session (routing activation)"
    return "session up but no PID data returned"


def run() -> None:
    print("obdwatch: watching eth0 for the car", flush=True)
    was_up = False
    last_post_readings = ""
    next_try = 0.0
    while True:
        up = carrier_up()
        if up and not was_up:
            print("link UP - car detected, settling then reading", flush=True)
            time.sleep(SETTLE_S)
            next_try = 0.0
        if not up and was_up:
            print("link DOWN", flush=True)
            last_post_readings = ""
        was_up = up
        if up and time.time() >= next_try:
            result = run_session()
            print(json.dumps(result), flush=True)
            if result["ok"]:
                text = fmt_readings(result["readings"])
                if text != last_post_readings:
                    post(f"Engine read (live from my OBD port): {text}")
                    last_post_readings = text
                next_try = time.time() + 60
            else:
                if not last_post_readings:
                    post("OBD: cable link is up but no engine data yet - "
                         + failure_hint(result))
                    last_post_readings = "(failed)"
                next_try = time.time() + RETRY_COOLDOWN_S
        time.sleep(POLL_S)


if __name__ == "__main__":
    run()
