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

# Deep-probe bookkeeping. The stamp is a PERSISTED once-per-day marker on
# disk, not an in-memory flag: the old in-memory `deep_done` reset on every
# wireless rfcomm0 flap, so the "one-time" scan re-ran mid-drive (claudemm,
# Aug 19). Identity DIDs do not change intra-day, so once a day is plenty.
# Raw results are written to RESULTS_DIR so the bytes survive the drive.
DEEP_STAMP = os.path.expanduser("~/.carwatch/deep-probe.stamp")
RESULTS_DIR = os.path.expanduser("~/.carwatch/probe-results")


def carrier_up() -> bool:
    try:
        with open(CARRIER) as f:
            return f.read().strip() == "1"
    except Exception:
        return False


def deep_probe_done_today() -> bool:
    """True if a COMPLETE (non-degraded) deep probe already ran today. Survives
    adapter/link flaps because it is read from disk, not memory."""
    try:
        with open(DEEP_STAMP) as f:
            return f.read().strip() == time.strftime("%Y-%m-%d")
    except Exception:
        return False


def mark_deep_probe_done() -> None:
    try:
        os.makedirs(os.path.dirname(DEEP_STAMP), exist_ok=True)
        with open(DEEP_STAMP, "w") as f:
            f.write(time.strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"deep stamp write failed: {e}", flush=True)


def save_deep_result(dp: dict) -> str:
    """Persist the full raw probe result (every DID's raw bytes) so the
    wrong-address-vs-locked question can be answered off-car later."""
    try:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        path = os.path.join(RESULTS_DIR,
                            "deep-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        with open(path, "w") as f:
            json.dump(dp, f, indent=2)
        return path
    except Exception as e:
        print(f"deep result save failed: {e}", flush=True)
        return ""


def post(text: str) -> None:
    try:
        with open(GLE_TXT, "w") as f:
            f.write(text)
        subprocess.run(["python3", POSTER], timeout=30, capture_output=True)
    except Exception as e:
        print(f"post failed: {e}", flush=True)


# The PIDs a healthy W213 read always answers. A post carrying fewer than
# these must SAY so: on 19.8. a mid-drive read decoded only rpm and posted
# a short line indistinguishable from a full one (claudemm's catch) - a
# partial read that does not announce itself reads as fact.
CORE_KEYS = ("engine_rpm", "coolant_c", "speed_kmh",
             "hybrid_battery_pct", "module_voltage")


def fmt_readings(r: dict) -> str:
    parts = []
    if "engine_rpm" in r:
        parts.append(f"engine {r['engine_rpm']:.0f} rpm")
    if "coolant_c" in r:
        parts.append(f"coolant {r['coolant_c']} C")
    if "speed_kmh" in r:
        parts.append(f"speed {r['speed_kmh']} km/h")
    if "fuel_level_pct" in r:
        parts.append(f"fuel {r['fuel_level_pct']}%")
    if "hybrid_battery_pct" in r:
        parts.append(f"hybrid battery {r['hybrid_battery_pct']}%")
    if "module_voltage" in r:
        parts.append(f"12V system {r['module_voltage']:.1f} V")
    if "intake_air_c" in r:
        parts.append(f"outside-ish air {r['intake_air_c']} C")
    missing = [k for k in CORE_KEYS if k not in r]
    if parts and missing:
        parts.append(f"(partial read: {len(missing)} of {len(CORE_KEYS)} "
                     "core values missing this cycle)")
    return ", ".join(parts) or "no readings"


def fmt_deep(dp: dict) -> str:
    """One room-readable summary for the per-ECU deep probe. Reports, per ECU
    that answered, what identity it gave or which NRC it returned - so 'no
    data' is never ambiguous - and flags a degraded/partial pass explicitly."""
    if not dp.get("ok"):
        why = "; ".join(dp.get("trace") or []) or "car did not answer the deep read"
        return f"Deep scan: nothing usable this time ({why})."
    parts = [f"Deep scan {dp.get('elapsed_s', '?')}s"]
    n = dp.get("supported_pid_count")
    if n:
        parts.append(f"{n} standard PIDs")
    if dp.get("vin"):
        parts.append("VIN readable")
    answered = [e for e in (dp.get("ecus") or []) if e.get("answered")]
    if answered:
        seg = []
        for e in answered:
            got = []
            for did, d in (e.get("dids") or {}).items():
                if d.get("status") == "ok" and d.get("text"):
                    got.append(f"{did}={d['text'][:24]}")
                elif d.get("status") == "nrc":
                    got.append(f"{did}:{d['nrc']} {d.get('nrc_name', '')}")
            if got:
                seg.append(f"{e['req']} ({e['label']}): " + "; ".join(got[:6]))
        if seg:
            parts.append(" || ".join(seg))
    else:
        parts.append("no ECU answered a mode-22 identity read on any addressed "
                     "unit (7E0-7E5) - that points to gateway-guarded or 29-bit "
                     "addressing, NOT proof the data is absent; raw bytes saved")
    if dp.get("degraded"):
        parts.append("(DEGRADED/partial pass - will retry, not a full scan)")
    return ". ".join(parts) + "."


def failure_hint(result: dict) -> str:
    stages = {t.get("stage"): t for t in result.get("trace", [])}
    if not stages.get("discover", {}).get("ok", False):
        return ("cable link is up but the car's diagnostic gateway did not "
                "answer identification - this is where we learn whether the "
                "ENET cable truly speaks to the GLE")
    if not stages.get("connect", {}).get("ok", False):
        return "gateway found but refused the session (routing activation)"
    return "session up but no PID data returned"


# ELM327 serial device paths, checked in order. /dev/ttyUSB* is the USB
# cable; /dev/rfcomm0 is the bound Bluetooth dongle (Vgate iCar Pro 2S).
ELM_PORTS = ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0")


def elm_port_present() -> str | None:
    for p in ELM_PORTS:
        if os.path.exists(p):
            return p
    return None


def run() -> None:
    # Two paths, one daemon. The Aug 13 real-GLE verdict: the ENET/DoIP path
    # has no gateway to talk to on a Mercedes, so the PRIMARY path is now a
    # standard ELM327 adapter (USB or bound Bluetooth) on the CAN bus. The
    # eth0/DoIP watch stays as a harmless secondary - if a car that speaks
    # DoIP ever appears on the cable, it still gets read.
    from carwatch import elm327
    print("obdwatch: watching for an ELM327 adapter "
          f"({', '.join(ELM_PORTS)}) and eth0 carrier", flush=True)
    was_present = ""
    was_up = False
    last_post_readings = ""
    next_try = 0.0
    # The deep (per-ECU mode-22 Mercedes) probe runs at most ONCE PER DAY and
    # only while the car is STATIONARY. petrus's Pi is mobile and only in the
    # car for a short window, so it self-triggers - no button, no remote. Two
    # guards, both flap-proof: a persisted on-disk day stamp (survives process
    # restarts) and this in-memory flag (survives wireless rfcomm0 flaps within
    # a run). The old code reset an in-memory flag on every flap, so the
    # "one-time" scan re-ran mid-drive at 28 km/h (claudemm, Aug 19).
    deep_ran_this_process = False
    while True:
        port = elm_port_present()
        up = carrier_up()
        if port and port != was_present:
            print(f"ELM327 adapter appeared at {port}", flush=True)
            time.sleep(SETTLE_S)
            next_try = 0.0
        if not port and was_present:
            print("ELM327 adapter gone", flush=True)
            last_post_readings = ""
        if up and not was_up:
            print("eth0 link UP", flush=True)
            time.sleep(SETTLE_S)
            next_try = 0.0
        if not up and not port and was_up:
            last_post_readings = ""
        was_present = port or ""
        was_up = up
        if (port or up) and time.time() >= next_try:
            if port:
                result = elm327.run_session(port)
            else:
                result = run_session()  # legacy DoIP path via eth0
            print(json.dumps(result), flush=True)
            if result["ok"]:
                text = fmt_readings(result["readings"])
                dtcs = result.get("dtcs") or []
                if dtcs:
                    text += f" | stored fault codes: {', '.join(dtcs)}"
                if text != last_post_readings:
                    post(f"Engine read (live from my OBD port): {text}")
                    last_post_readings = text
                # Deep probe: only when the car is STOPPED and not already done
                # today. A mid-drive diagnostic sweep both competes with live
                # telemetry and returns fewer PIDs while looking just as
                # confident (claudemm's 44-vs-12 catch) - so we wait for a
                # genuine stationary read. speed unknown -> treat as moving and
                # defer (conservative). Inline: one serial port = one reader, so
                # it briefly pauses normal reads, fine for a once-a-day scan.
                speed = result["readings"].get("speed_kmh")
                stationary = (speed == 0)
                if (port and stationary and not deep_ran_this_process
                        and not deep_probe_done_today()):
                    try:
                        post("Stopped - running my once-a-day deep scan now "
                             "(per-ECU Mercedes identity reads), up to ~1 min...")
                        dp = elm327.deep_probe(port)
                        path = save_deep_result(dp)
                        if path:
                            print(f"deep result saved: {path}", flush=True)
                        post(fmt_deep(dp))
                        # Only lock the day on a COMPLETE pass; a degraded/empty
                        # scan stays retryable at the next stop.
                        if dp.get("ok") and not dp.get("degraded"):
                            deep_ran_this_process = True
                            mark_deep_probe_done()
                    except Exception as e:  # never let the probe kill the loop
                        print(f"deep probe failed: {e}", flush=True)
                next_try = time.time() + 60
            else:
                if not last_post_readings:
                    hint = (result.get("summary", "no data")
                            if port else failure_hint(result))
                    post(f"OBD: adapter/link present but no engine data yet - {hint}")
                    last_post_readings = "(failed)"
                next_try = time.time() + RETRY_COOLDOWN_S
        time.sleep(POLL_S)


if __name__ == "__main__":
    run()
