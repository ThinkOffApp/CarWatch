"""Read-only deep probe of the car's CAN bus, triggered from the room.

petrus, Aug 22 2026, from a hospital room while the car was running:
"NOW is the time to do all the odb probing that you can!!!" - and nobody
could act, because the Pi sits behind the phone hotspot's NAT and no agent
has a shell on it. The Pi is not reachable INWARD, but it does two things
outward already: it polls the room, and it self-updates hourly. So the probe
ships as code and is triggered by a mention, needing no inbound access at
all.

Everything here is READ ONLY. Nothing is ever written to the vehicle bus:

  - ATMA is the ELM327's "monitor all" mode. It is passive; it transmits
    nothing and simply reports frames as they pass. This is the ELM327
    equivalent of candump, which is what we actually want, because the
    adapter speaks serial and there is no socketcan interface to run
    candump against.
  - The mode-22 attempts use standard diagnostic READ requests. We try the
    Mercedes-range headers because the earlier silence came from asking on
    7E0-7E5, the generic OBD addresses; silence there is not proof that the
    gateway is closed, only that we knocked on doors this car may not use.

The distinction matters and is the whole point of the exercise: a passive
capture tells us what IS on the bus regardless of whether anything answers
our questions.
"""

from __future__ import annotations

import time


def monitor_bus(elm, seconds: float = 12.0) -> dict:
    """Passively watch the bus and report which CAN IDs actually appear."""
    # Headers ON so each frame carries its arbitration ID, otherwise every
    # line looks alike and the capture tells us nothing about who is talking.
    # No ATZ here. On the wireless adapter a reset tears down the Bluetooth
    # RFCOMM link, the file descriptor dies with it, and the very next command
    # fails with EIO - which is exactly how this failed twice. The live poller
    # has already initialised the adapter, so configure it in place instead.
    elm.cmd("ATE0", 1.0)
    elm.cmd("ATL0", 1.0)
    elm.cmd("ATH1", 1.0)
    elm.cmd("ATSP0", 1.0)
    # Wake the protocol before monitoring: on a cold link ATMA can return
    # nothing simply because no protocol has been negotiated yet.
    elm.cmd("0100", 4.0)
    # ATMA never returns a prompt on its own: it streams until interrupted,
    # so read for a fixed window and then send a bare CR to stop it.
    import os
    os.write(elm.fd, b"ATMA\r")
    buf = bytearray()
    end = time.time() + seconds
    while time.time() < end:
        try:
            chunk = os.read(elm.fd, 512)
        except BlockingIOError:
            chunk = b""
        except OSError:
            break
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.02)
    try:
        os.write(elm.fd, b"\r")          # stop monitoring
        elm._read_until_prompt(2.0)
    except Exception:
        pass

    text = buf.decode("ascii", "replace")
    ids: dict[str, int] = {}
    lines = 0
    for line in text.replace("\r", "\n").split("\n"):
        toks = line.split()
        if not toks:
            continue
        head = toks[0].strip().upper()
        # An arbitration ID is 3 hex chars (11-bit) or 8 (29-bit extended).
        if len(head) in (3, 8) and all(c in "0123456789ABCDEF" for c in head):
            ids[head] = ids.get(head, 0) + 1
            lines += 1
    return {"seconds": seconds, "frames": lines, "unique_ids": len(ids),
            "top": sorted(ids.items(), key=lambda kv: -kv[1])[:12],
            "raw_chars": len(text)}


# Mercedes commonly answers on lower headers than the generic OBD range, and
# on 29-bit addressing. Both are tried because the earlier scan only used
# 7E0-7E5 and concluded "closed" from that silence alone.
MB_HEADERS_11 = ["0x300", "0x301", "0x302", "0x310", "0x320", "0x330"]
MB_HEADERS_29 = ["18DA10F1", "18DA18F1", "18DA28F1", "18DA40F1"]


def probe_identity(elm, headers_11=None, headers_29=None) -> dict:
    """Try a mode-22 identity read on Mercedes-style addresses."""
    hits, tried = [], []
    elm.cmd("ATZ", 2.0)
    elm.cmd("ATE0", 1.0)
    elm.cmd("ATH1", 1.0)

    for h in (headers_11 or MB_HEADERS_11):
        elm.cmd("ATSP6", 1.0)            # 11-bit, 500k
        elm.cmd(f"ATSH{h[2:] if h.startswith('0x') else h}", 1.0)
        r = elm.cmd("22F190", 3.0)       # VIN by data identifier
        tried.append(h)
        if r and "NO DATA" not in r.upper() and "ERROR" not in r.upper():
            hits.append({"header": h, "reply": r[:120]})

    for h in (headers_29 or MB_HEADERS_29):
        elm.cmd("ATSP7", 1.0)            # 29-bit, 500k
        elm.cmd(f"ATSH{h}", 1.0)
        r = elm.cmd("22F190", 3.0)
        tried.append(h)
        if r and "NO DATA" not in r.upper() and "ERROR" not in r.upper():
            hits.append({"header": h, "reply": r[:120]})

    return {"tried": tried, "hits": hits}


def summarise(mon: dict, ident: dict) -> str:
    """One room-readable paragraph. Spoken aloud, so no hex soup up front."""
    parts = []
    if mon.get("frames"):
        parts.append(
            f"Passive listen for {int(mon['seconds'])} seconds: "
            f"{mon['frames']} frames from {mon['unique_ids']} different senders. "
            "That means the bus IS readable through this port."
        )
        top = ", ".join(f"{i} ({n})" for i, n in mon["top"][:6])
        parts.append(f"Busiest senders: {top}.")
    else:
        parts.append(
            f"Passive listen for {int(mon['seconds'])} seconds heard NOTHING. "
            "Either the adapter is not on a live bus, or it needs the ignition on."
        )
    if ident.get("hits"):
        hs = ", ".join(h["header"] for h in ident["hits"])
        parts.append(f"Identity reads answered on: {hs}.")
    else:
        parts.append(
            f"No identity answer on any of the {len(ident.get('tried', []))} "
            "Mercedes-style addresses tried."
        )
    return " ".join(parts)


def record_bus(elm, seconds: float = 90.0, out_dir: str = None) -> dict:
    """Record raw ATMA traffic to disk for offline decoding.

    The passive counterpart of monitor_bus with a file instead of a
    summary: petrus, Aug 24, after the 0x300-range discovery: "selvittaa
    broadcast-virta". Chunks are stamped with monotonic-ish wall time so
    the decode can correlate bytes with what the car was doing.
    """
    import os, json, time as _t
    out_dir = out_dir or os.path.expanduser(
        os.environ.get("CARWATCH_STATE", "~/.carwatch")) + "/can-logs"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, time.strftime("rec-%Y%m%d-%H%M%S.log"))
    elm.cmd("ATE0", 1.0)
    elm.cmd("ATL0", 1.0)
    elm.cmd("ATH1", 1.0)
    elm.cmd("ATSP0", 1.0)
    elm.cmd("0100", 4.0)
    os.write(elm.fd, b"ATMA\r")
    frames = 0
    ids = set()
    end = time.time() + seconds
    with open(path, "w") as f:
        f.write(json.dumps({"started": time.time(), "seconds": seconds}) + "\n")
        buf = bytearray()
        while time.time() < end:
            try:
                chunk = os.read(elm.fd, 1024)
            except BlockingIOError:
                chunk = b""
            except OSError:
                break
            if chunk:
                buf.extend(chunk)
                while b"\r" in buf:
                    line, _, rest = bytes(buf).partition(b"\r")
                    buf = bytearray(rest)
                    text = line.decode("ascii", "replace").strip()
                    if not text or text == ">":
                        continue
                    frames += 1
                    tok = text.split()
                    if tok and all(c in "0123456789ABCDEF" for c in tok[0]):
                        ids.add(tok[0])
                    f.write(f"{time.time():.3f} {text}\n")
            else:
                time.sleep(0.02)
    os.write(elm.fd, b"\r")
    time.sleep(0.5)
    try:
        os.read(elm.fd, 4096)
    except Exception:
        pass
    return {"path": path, "frames": frames, "unique_ids": sorted(ids),
            "seconds": seconds}
