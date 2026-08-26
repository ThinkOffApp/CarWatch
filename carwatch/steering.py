"""Live steering-angle sampling from the CAN broadcast.

The steering-wheel angle rides on CAN id 0x0500, data byte 0, centre 128
(claudeMB's decode from a real drive capture, Aug 26 2026: of 0x0500's eight
data bytes only byte 0 tracks the wheel). Unlike the OBD PID reads (mode-01
request/response), this value exists ONLY on the passive broadcast, so it is
read with a short ATMA (monitor-all) burst - the same passive capture deepscan
uses, just long enough for a fresh sample.

Single-reader rule: only ONE thing may hold the ELM327 at a time. `sample()`
is called from inside the obdwatch loop (the sole serial owner) on an ELM it
owns, never from the web process, so it never races the PID reads. Everything
here is READ ONLY; ATMA transmits nothing to the bus.

The web dashboard reads the cache file this writes; it never opens the port.
"""
from __future__ import annotations

import os
import time

# Frames for this car come off ATMA (headers on, auto-format off) as
# "05 00 00 00 <d0> <d1> .. <d7>" - the id as two bytes, two pad bytes, then
# eight data bytes. Empirically validated against the real rec-*.log capture:
# token[0:2] == 05,00 identifies the frame and token[4] is the steering byte.
CAN_ID_HI = "05"
CAN_ID_LO = "00"
CENTRE = 128


def parse_latest_d0(text: str) -> dict:
    """Pure parser: given raw ATMA text, return the LAST 0x0500 byte-0 seen.

    Kept separate from the serial I/O so it can be unit-tested against real
    capture lines. Tolerates a leading wall-time stamp (as record_bus writes)
    or none (as a live ATMA read produces). Returns {ok, value, n}."""
    last = None
    n = 0
    for line in text.replace("\r", "\n").split("\n"):
        t = line.split()
        if not t:
            continue
        # A record_bus line is "<ts> 05 00 00 00 <d0>..."; a live ATMA line is
        # "05 00 00 00 <d0>...". Drop a leading float timestamp if present.
        if "." in t[0] and t[0].replace(".", "").isdigit():
            t = t[1:]
        if len(t) >= 5 and t[0].upper() == CAN_ID_HI and t[1].upper() == CAN_ID_LO:
            try:
                last = int(t[4], 16)
                n += 1
            except ValueError:
                pass
    if last is None:
        return {"ok": False, "error": "no 0x0500 frames", "n": 0}
    return {"ok": True, "value": last, "centre": CENTRE, "n": n}


def configure(elm) -> None:
    """Settle the adapter for passive monitoring ONCE, so repeated bursts on a
    warm connection can skip this (the slow part: 0100 + ATDPN wake). Mirrors
    deepscan.record_bus so the framing matches the decode. No ATZ: on the
    wireless adapter a reset tears down the RFCOMM link (deepscan's note)."""
    elm.cmd("ATE0", 1.0)
    elm.cmd("ATL0", 1.0)
    elm.cmd("ATH1", 1.0)
    elm.cmd("ATSP0", 1.0)
    elm.cmd("0100", 4.0)                       # wake/settle the protocol
    proto = elm.cmd("ATDPN", 1.0).strip().replace(">", "").strip()
    pn = proto[-1] if proto else "0"
    if pn in ("6", "7", "8", "9", "A", "B", "C"):
        elm.cmd("ATSP" + pn, 1.0)              # pin it so ATMA frames cleanly
    elm.cmd("ATCAF0", 1.0)                     # raw frames, no auto-formatting


def sample(elm, seconds: float = 2.5, settle: bool = True) -> dict:
    """Run a short ATMA burst on an ALREADY-OPEN Elm327 and return the latest
    steering byte. The caller owns the ELM's lifecycle and must not let any
    other reader touch the port during the burst.

    settle=True runs the full protocol wake first (use on a fresh connection);
    settle=False skips it for fast repeat bursts on a connection already
    configure()'d - the responsive path. Restores nothing on its own; call
    restore() when you are done monitoring and want PID reads back."""
    fd = elm.fd
    if settle:
        configure(elm)
    os.write(fd, b"ATMA\r")
    buf = bytearray()
    end = time.time() + seconds
    while time.time() < end:
        try:
            chunk = os.read(fd, 1024)
        except BlockingIOError:
            chunk = b""
        except OSError:
            break
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.01)
    try:
        os.write(fd, b"\r")                    # any char stops ATMA
    except OSError:
        pass
    time.sleep(0.1)
    try:
        buf.extend(os.read(fd, 4096))          # drain the stop-ack + prompt
    except Exception:
        pass
    # Restore for the caller's normal PID reads.
    elm.cmd("ATCAF1", 0.5)
    elm.cmd("ATH0", 0.5)
    return parse_latest_d0(buf.decode("ascii", "replace"))


def restore(elm) -> None:
    """Undo the monitor config so normal PID reads work on the SAME open
    connection. Only needed if a caller reuses one connection for both; the
    fresh-connection path (sample_once) does not, since the next PID session
    opens its own ELM and re-inits."""
    try:
        elm.cmd("ATCAF1", 0.5)
        elm.cmd("ATH0", 0.5)
    except Exception:
        pass


def sample_once(port: str, seconds: float = 2.0) -> dict:
    """Open a fresh connection, take one steering sample, close it. The simple,
    self-contained path for a caller that owns the serial port between calls
    (obdwatch): no persistent state to leak, and it is fully isolated so a
    steering failure can never affect the PID reads. Returns the sample dict."""
    from carwatch import elm327 as _elm
    elm = None
    try:
        elm = _elm.Elm327(port)
        return sample(elm, seconds=seconds, settle=True)
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    finally:
        if elm is not None:
            try:
                elm.close()
            except Exception:
                pass


STEER_CACHE = os.path.expanduser(
    os.environ.get("CARWATCH_STATE", "~/.carwatch")) + "/steering.json"


def write_cache(res: dict) -> None:
    """Persist the latest sample for the dashboard to read. Atomic replace so
    a reader never sees a half-written file. Adds a wall-time stamp so the UI
    can show staleness and refuse to display an old value as live."""
    import json
    out = dict(res)
    out["ts"] = time.time()
    tmp = STEER_CACHE + ".tmp"
    try:
        os.makedirs(os.path.dirname(STEER_CACHE), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(out, f)
        os.replace(tmp, STEER_CACHE)
    except Exception:
        pass


def read_cache(max_age_s: float = 6.0) -> dict:
    """Read the latest sample, marking it stale past max_age_s so the dash can
    grey it out instead of showing a frozen number as if it were live."""
    import json
    try:
        with open(STEER_CACHE) as f:
            d = json.load(f)
        age = time.time() - float(d.get("ts", 0))
        d["age_s"] = round(age, 1)
        d["stale"] = age > max_age_s
        return d
    except Exception:
        return {"ok": False, "error": "no sample yet"}
