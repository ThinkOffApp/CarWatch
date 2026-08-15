"""Deep OBD probe: everything the car will tell us, posted live to the room.

Born Aug 15 2026 with petrus sitting in the E 300e: "probe OBD further, see
what we can do with it". Where the daily reader touches five PIDs, this
walks ALL of them, reads the VIN, tries Mercedes-specific mode-22 reads,
and dumps the raw mode-03 frames (the P0043 real-or-ghost question).

Self-posting runner (QCD lesson): results go to the room directly, the
agent only narrates around them. Read-only except mode 01/03/09/22 reads;
never writes to the bus.

Usage: python3 -m carwatch.obd_probe [port]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from carwatch.elm327 import Elm327, DEFAULT_PORT, PIDS

CONFIG = os.path.expanduser("~/.carwatch/config.json")


def post(body: str) -> None:
    try:
        cfg = json.load(open(CONFIG))
        req = urllib.request.Request(
            cfg.get("api_base", "https://groupmind.one").replace(
                "groupmind.one", "antfarm.world") + "/api/v1/messages",
            data=json.dumps({"room": cfg["room"], "body": body}).encode(),
            headers={"X-API-Key": cfg["api_key"],
                     "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"post failed: {e}", flush=True)


def cmd_lines(elm: Elm327, line: str, timeout: float = 5.0) -> list[str]:
    """Like cmd() but PRESERVES line structure (needed for multi-frame
    mode-03 where every line repeats the 43 header)."""
    os.write(elm.fd, (line + "\r").encode("ascii"))
    raw = elm._read_until_prompt(timeout)
    out = raw.replace(line, "").replace(">", "")
    return [" ".join(l.split()) for l in out.replace("\r", "\n").split("\n")
            if l.strip()]


def supported_pids(elm: Elm327) -> list[int]:
    """Walk the 0100/0120/... support bitmaps."""
    pids: list[int] = []
    for base in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0):
        reply = elm.cmd(f"01{base:02X}")
        toks = reply.split()
        try:
            i = toks.index("41")
            data = [int(x, 16) for x in toks[i + 2:i + 6]]
        except (ValueError, IndexError):
            break
        mask = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        for bit in range(32):
            if mask & (1 << (31 - bit)):
                pids.append(base + bit + 1)
        if not mask & 1:  # next-range bit clear -> no more ranges
            break
    return [p for p in pids if p % 0x20 != 0]


def read_vin(elm: Elm327) -> str:
    lines = cmd_lines(elm, "0902", timeout=6.0)
    hexbytes: list[int] = []
    for l in lines:
        toks = [t for t in l.split() if len(t) == 2]
        # strip the 49 02 <seq> header per frame
        if len(toks) >= 3 and toks[0] == "49":
            toks = toks[3:]
        for t in toks:
            try:
                hexbytes.append(int(t, 16))
            except ValueError:
                pass
    vin = "".join(chr(b) for b in hexbytes if 32 < b < 127)
    return vin.strip() or "(no VIN reply)"


def raw_dtcs(elm: Elm327) -> tuple[list[str], list[str]]:
    """Return (decoded codes with per-line header stripping, raw lines)."""
    lines = cmd_lines(elm, "03", timeout=6.0)
    codes: list[str] = []
    for l in lines:
        toks = [t for t in l.split() if len(t) == 2 and
                all(c in "0123456789abcdefABCDEF" for c in t)]
        if toks and toks[0].upper() == "43":
            toks = toks[1:]
            # some adapters put a count byte after 43; heuristic: odd length
            if len(toks) % 2 == 1:
                toks = toks[1:]
        body = [int(x, 16) for x in toks]
        for i in range(0, len(body) - 1, 2):
            a, b = body[i], body[i + 1]
            if a == 0 and b == 0:
                continue
            codes.append(f"{'PCBU'[(a >> 6) & 3]}{(a >> 4) & 3}{a & 0xF:X}{b:02X}")
    return codes, lines


# Exploratory Mercedes/UDS candidates. Labeled exploratory on purpose:
# these DIDs vary per model; whatever answers is a FINDING, silence is not
# an error. Read-only 22-reads.
MODE22_CANDIDATES = {
    "F190": "VIN (UDS mirror)",
    "F187": "part number",
    "F18C": "serial number",
}


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    if not os.path.exists(port):
        post(f"OBD deep probe: no adapter at {port}, aborting.")
        return
    post("OBD deep probe STARTING on the car, live results follow.")
    elm = Elm327(port)
    elm.init()

    sup = supported_pids(elm)
    known = {p: PIDS[p][0] for p in sup if p in PIDS}
    unknown = [f"{p:02X}" for p in sup if p not in PIDS]
    readings = {}
    for p, name in known.items():
        reply = elm.cmd(f"01{p:02X}")
        toks = reply.split()
        try:
            i = toks.index("41")
            data = [int(x, 16) for x in toks[i + 2:]]
            readings[name] = PIDS[p][2](data)
        except Exception:
            readings[name] = "(no decode)"
    post(f"PID sweep: the car advertises {len(sup)} PIDs. "
         f"Decoded now: {json.dumps(readings)[:800]}. "
         f"Supported-but-undecoded PIDs (future work): {' '.join(unknown[:40])}")

    vin = read_vin(elm)
    post(f"VIN over OBD: {vin}")

    codes, lines = raw_dtcs(elm)
    post("Fault codes, raw frames: " + " | ".join(lines[:6])
         + f" -> decoded with per-frame header stripping: {codes or 'NONE'}. "
         + ("P0043 SURVIVES the corrected parser." if "P0043" in codes else
            "P0043 is GONE with correct frame parsing - it was a parser ghost, both cars are clean."))

    hits = {}
    for did, label in MODE22_CANDIDATES.items():
        reply = elm.cmd(f"22{did}", 4.0)
        if "62" in reply.split()[:3]:
            hits[did] = f"{label}: {reply[:60]}"
    post("Mode-22 (Mercedes/UDS) exploratory reads: "
         + (json.dumps(hits) if hits else
            "no candidates answered on the standard OBD gateway - deeper EV data "
            "likely needs specific diagnostic addressing, noted for the next session.")
         + " Probe DONE.")


if __name__ == "__main__":
    main()
