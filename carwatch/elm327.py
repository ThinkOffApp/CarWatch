"""Read the GLE's engine over a standard ELM327 OBD-II adapter (CAN bus).

The Aug 13 real-car test proved the BMW-style ENET/DoIP cable has no gateway
to talk to on the Mercedes. The right hardware for the GLE is the boring,
universal one: a plain ELM327 adapter on the OBD-II port, speaking the
ISO 15765 (CAN) standard PIDs that every post-2008 car exposes. This is the
reader for it.

Deliberately stdlib-only for the serial chat (ELM327 is line-based ASCII: you
write "010C\\r" and it answers "41 0C 1A F8\\r\\r>"). Works on the Pi with a
USB ELM327 at /dev/ttyUSB0. No pyserial needed - raw termios sets the baud.

Proven BEFORE the adapter exists: tests/fake_elm327.py is a PTY that speaks
the real ELM327 protocol, and the whole read runs against it. Only the final
step (a real GLE answering on real CAN) is unverified until the adapter is in
the port - and the honest status stays "built + tested, not car-verified"
until it does.

Read-only: mode 01 (current data) and mode 03 (stored DTCs). Never writes.
"""

from __future__ import annotations

import os
import time

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 38400


# ── mode-01 PIDs: pid -> (name, nbytes, decoder(data_ints)) ──────────────
def _rpm(b):        return ((b[0] << 8) | b[1]) / 4.0
def _coolant(b):    return b[0] - 40
def _speed(b):      return b[0]
def _volts(b):      return ((b[0] << 8) | b[1]) / 1000.0
def _fuel_pct(b):   return round(b[0] * 100 / 255, 1)
def _load_pct(b):   return round(b[0] * 100 / 255, 1)
def _intake(b):     return b[0] - 40
def _hybrid_soc(b): return round(b[0] * 100 / 255, 1)

PIDS = {
    0x04: ("engine_load_pct", 1, _load_pct),
    0x05: ("coolant_c", 1, _coolant),
    0x0C: ("engine_rpm", 2, _rpm),
    0x0D: ("speed_kmh", 1, _speed),
    0x0F: ("intake_air_c", 1, _intake),
    0x2F: ("fuel_level_pct", 1, _fuel_pct),
    0x42: ("module_voltage", 2, _volts),
    0x5B: ("hybrid_battery_pct", 1, _hybrid_soc),
}


def _open_serial(port: str, baud: int):
    """Open the ELM327 serial port raw, at `baud`, via termios. Returns an
    os-level fd. Kept dependency-free so it runs on a bare Pi."""
    import termios
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    # cfmakeraw equivalent.
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
    baud_const = getattr(termios, f"B{baud}", termios.B38400)
    iflag = 0
    oflag = 0
    cflag = (cflag | termios.CLOCAL | termios.CREAD) & ~termios.CSIZE \
        | termios.CS8
    cflag &= ~(termios.PARENB | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
    lflag = 0
    termios.tcsetattr(fd, termios.TCSANOW,
                      [iflag, oflag, cflag, lflag, baud_const, baud_const, cc])
    return fd


class Elm327:
    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD):
        self.fd = _open_serial(port, baud)

    def close(self):
        try:
            os.close(self.fd)
        except Exception:
            pass

    def _read_until_prompt(self, timeout: float = 5.0) -> str:
        """ELM327 ends every response with '>'. Read until it, or timeout."""
        buf = bytearray()
        end = time.time() + timeout
        while time.time() < end:
            try:
                chunk = os.read(self.fd, 256)
            except BlockingIOError:
                chunk = b""
            except OSError:
                break
            if chunk:
                buf.extend(chunk)
                if b">" in buf:
                    break
            else:
                time.sleep(0.02)
        return buf.decode("ascii", "replace")

    def cmd(self, line: str, timeout: float = 5.0) -> str:
        os.write(self.fd, (line + "\r").encode("ascii"))
        raw = self._read_until_prompt(timeout)
        # Strip echo, prompt, whitespace; keep the meaningful lines.
        out = raw.replace(line, "").replace(">", "")
        return " ".join(out.split())

    def init(self) -> None:
        # Reset, echo off, linefeeds off, headers off, auto-protocol.
        for c, t in (("ATZ", 2.0), ("ATE0", 1.0), ("ATL0", 1.0),
                     ("ATH0", 1.0), ("ATSP0", 1.0)):
            self.cmd(c, t)


def _parse_pid_reply(text: str, pid: int):
    """A mode-01 reply looks like '41 0C 1A F8'. Return decoded (name, value)
    or None. Ignores 'NO DATA', 'SEARCHING...', errors."""
    toks = text.replace("\r", " ").split()
    hexes = [t for t in toks if len(t) == 2 and
             all(ch in "0123456789ABCDEFabcdef" for ch in t)]
    # Find the '41 <pid>' response echo and take the bytes after it.
    for i in range(len(hexes) - 1):
        if hexes[i].upper() == "41" and int(hexes[i + 1], 16) == pid:
            name, nbytes, dec = PIDS[pid]
            data = [int(x, 16) for x in hexes[i + 2:i + 2 + nbytes]]
            if len(data) < nbytes:
                return None
            try:
                return name, dec(data)
            except Exception:
                return None
    return None


def read_all(elm: Elm327) -> dict:
    out: dict = {}
    for pid, (name, _n, _d) in PIDS.items():
        reply = elm.cmd(f"01{pid:02X}")
        parsed = _parse_pid_reply(reply, pid)
        if parsed:
            out[parsed[0]] = parsed[1]
    return out


def _decode_dtc_pairs(body: list[int]) -> list[str]:
    """Decode raw 2-byte DTC pairs into Pxxxx/Cxxxx/Bxxxx/Uxxxx strings,
    skipping 00 00 padding."""
    codes = []
    for i in range(0, len(body) - 1, 2):
        a, b = body[i], body[i + 1]
        if a == 0 and b == 0:
            continue
        prefix = "PCBU"[(a >> 6) & 0x3]
        codes.append(f"{prefix}{(a >> 4) & 0x3}{a & 0xF:X}{b:02X}")
    return codes


def decode_dtc_reply(reply: str) -> list[str]:
    """Decode a mode-03 reply, CAN-aware.

    On CAN (every modern Mercedes) EACH module answers '43 <count> <pairs>',
    and with headers off (ATH0) several modules' answers arrive interleaved:
    a car with NO stored codes at all replies '43 00 43 00 43 00'. The old
    decoder assumed the K-line format (pairs directly after 43, no count
    byte), so it read that healthy stream as data pairs - and 00 43 decodes
    to exactly P0043. That phantom pair showed up identically on BOTH cars
    (2026-08-14 GLE, 2026-08-15 E 300e) and nearly sent petrus to a garage.

    Parse: walk the hex tokens; at each '43' header try the CAN form (next
    byte = count, then exactly count pairs). If the promised pairs do not
    fit before the end or the next '43' header, fall back to K-line pairs
    for that segment. '43' appearing INSIDE a counted segment stays data.
    """
    toks = [t for t in reply.split() if len(t) == 2 and
            all(ch in "0123456789ABCDEFabcdef" for ch in t)]
    vals = [int(t, 16) for t in toks]
    codes: list[str] = []
    i = 0
    while i < len(vals):
        if vals[i] != 0x43:
            i += 1
            continue
        seg_start = i + 1
        if seg_start >= len(vals):
            break
        count = vals[seg_start]
        can_end = seg_start + 1 + count * 2
        # CAN form is plausible when the counted pairs fit exactly before
        # the end of the stream or the next module's 43 header.
        can_ok = (count <= 0x7F and can_end <= len(vals) and
                  (can_end == len(vals) or vals[can_end] == 0x43 or
                   all(v == 0 for v in vals[can_end:])))
        if can_ok:
            codes.extend(_decode_dtc_pairs(vals[seg_start + 1:can_end]))
            i = can_end
        else:
            # K-line: pairs run straight after 43 to the end of the stream.
            codes.extend(_decode_dtc_pairs(vals[seg_start:]))
            break
    return codes


def read_dtcs(elm: Elm327) -> list[str]:
    """Stored trouble codes via mode 03. See decode_dtc_reply for the
    CAN/K-line handling."""
    return decode_dtc_reply(elm.cmd("03"))


# Standard OBD-II mode-01 PID names (SAE J1979), for reporting which PIDs
# the car ADVERTISES as supported even when we do not yet decode them. Not
# exhaustive - covers the commonly-present ones so the capability report is
# readable rather than a wall of hex.
STD_PID_NAMES = {
    0x01: "monitor status since DTCs cleared", 0x03: "fuel system status",
    0x04: "calculated engine load", 0x05: "engine coolant temp",
    0x06: "short term fuel trim b1", 0x07: "long term fuel trim b1",
    0x0A: "fuel pressure", 0x0B: "intake manifold pressure",
    0x0C: "engine RPM", 0x0D: "vehicle speed", 0x0E: "timing advance",
    0x0F: "intake air temp", 0x10: "MAF air flow", 0x11: "throttle position",
    0x1F: "run time since engine start", 0x21: "distance with MIL on",
    0x22: "fuel rail pressure", 0x23: "fuel rail gauge pressure",
    0x2C: "commanded EGR", 0x2D: "EGR error", 0x2F: "fuel tank level",
    0x31: "distance since codes cleared", 0x33: "barometric pressure",
    0x42: "control module voltage", 0x43: "absolute load value",
    0x46: "ambient air temp", 0x47: "abs throttle position B",
    0x49: "accelerator pedal position D", 0x4C: "commanded throttle actuator",
    0x51: "fuel type", 0x5A: "relative accelerator pedal position",
    0x5B: "hybrid/EV battery remaining life", 0x5C: "engine oil temp",
    0x5E: "engine fuel rate",
}


def _bitmask_pids(reply: str, base: int) -> list[int]:
    """A '0100'/'0120'/... reply carries a 4-byte bitmask of which PIDs in
    the next block of 32 are supported. Return their PID numbers."""
    toks = [t for t in reply.replace("\r", " ").split()
            if len(t) == 2 and all(c in "0123456789ABCDEFabcdef" for c in t)]
    up = [t.upper() for t in toks]
    # Find '41 <base>' then take the following 4 data bytes.
    for i in range(len(up) - 1):
        if up[i] == "41" and int(up[i + 1], 16) == base:
            data = [int(x, 16) for x in toks[i + 2:i + 6]]
            if len(data) < 4:
                return []
            bits = int.from_bytes(bytes(data), "big")
            return [base + 1 + n for n in range(32)
                    if bits & (1 << (31 - n))]
    return []


def read_vin(elm: Elm327) -> str:
    """Vehicle Identification Number via mode 09 PID 02. Read-only."""
    reply = elm.cmd("0902", timeout=6.0)
    toks = [t for t in reply.replace("\r", " ").split()
            if len(t) == 2 and all(c in "0123456789ABCDEFabcdef" for c in t)]
    up = [t.upper() for t in toks]
    chars = []
    # ISO-TP multiframe: pull ASCII bytes that fall in the printable VIN range
    # after the '49 02' service echo. Kept lenient - a partial VIN is still
    # useful and we never write anything.
    started = False
    for i in range(len(up) - 1):
        if up[i] == "49" and up[i + 1] == "02":
            started = True
            j = i + 3  # skip service, pid, frame-index byte
            for b in toks[j:]:
                v = int(b, 16)
                if 0x20 <= v <= 0x7E:
                    chars.append(chr(v))
            break
    vin = "".join(chars).strip()
    return vin if started and len(vin) >= 11 else ""


def scan_capabilities(port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD) -> dict:
    """READ-ONLY capability probe of the whole OBD surface: which mode-01
    PIDs the car advertises, the VIN (mode 09), and stored DTCs (mode 03).
    Never writes - no mode 04 (clear), no UDS write services. This is the
    authoritative answer to 'what can our module get from this car'."""
    out = {"ok": False, "supported_pids": [], "decoded_now": [],
           "vin": "", "dtcs": [], "protocol": "", "trace": []}
    if not os.path.exists(port):
        out["trace"].append({"stage": "port", "ok": False,
                              "detail": f"no adapter at {port}"})
        return out
    try:
        elm = Elm327(port, baud)
    except Exception as e:
        out["trace"].append({"stage": "open", "ok": False, "detail": str(e)})
        return out
    try:
        elm.init()
        out["protocol"] = elm.cmd("ATDPN").strip()  # describe protocol number
        supported: list[int] = []
        for base in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0):
            reply = elm.cmd(f"01{base:02X}")
            block = _bitmask_pids(reply, base)
            supported += [p for p in block if (p & 0x1F) or True]
            out["trace"].append({"stage": f"supported_{base:02X}",
                                  "ok": bool(block), "count": len(block)})
            # PID 0x00 of each block signals whether the NEXT block exists.
            if (base + 0x20) not in block and base != 0x00:
                break
        # Annotate with names where known.
        out["supported_pids"] = [
            {"pid": f"0x{p:02X}", "name": STD_PID_NAMES.get(p, "manufacturer/unknown"),
             "we_decode": p in PIDS}
            for p in sorted(set(supported))
        ]
        out["decoded_now"] = [PIDS[p][0] for p in sorted(PIDS)]
        out["vin"] = read_vin(elm)
        out["dtcs"] = read_dtcs(elm)
    finally:
        elm.close()
    out["ok"] = bool(out["supported_pids"])
    out["summary"] = (f"{len(out['supported_pids'])} PIDs advertised, "
                      f"{len(out['decoded_now'])} decoded today"
                      + (f", VIN {out['vin']}" if out["vin"] else "")
                      + (f", {len(out['dtcs'])} DTC(s)" if out["dtcs"] else ""))
    return out


def run_session(port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD) -> dict:
    """Whole flow, honest trace at every step."""
    trace: list[dict] = []
    result = {"ok": False, "readings": {}, "dtcs": [], "trace": trace}
    if not os.path.exists(port):
        trace.append({"stage": "port", "ok": False,
                      "detail": f"no adapter at {port} - plug in the USB "
                      "ELM327 (it appears as /dev/ttyUSB0)"})
        result["summary"] = "no ELM327 adapter present"
        return result
    try:
        elm = Elm327(port, baud)
    except Exception as e:
        trace.append({"stage": "open", "ok": False, "detail": str(e)})
        result["summary"] = f"could not open {port}"
        return result
    try:
        elm.init()
        ident = elm.cmd("0100")  # supported PIDs - proves the car answers CAN
        car_ok = "41 00" in ident.upper()
        trace.append({"stage": "handshake", "ok": car_ok,
                      "detail": "car answered mode-01 on CAN" if car_ok else
                      f"adapter up but car did not answer 0100 ({ident!r})"})
        readings = read_all(elm)
        dtcs = read_dtcs(elm)
    finally:
        elm.close()
    result["readings"] = readings
    result["dtcs"] = dtcs
    result["ok"] = bool(readings)
    trace.append({"stage": "read", "ok": bool(readings), "count": len(readings)})
    result["summary"] = (f"read {len(readings)} live values"
                         + (f", {len(dtcs)} stored fault code(s)" if dtcs else "")
                         if readings else "adapter present but no PID data")
    return result


if __name__ == "__main__":
    import json
    import sys
    args = sys.argv[1:]
    if args and args[0] == "scan":
        port = args[1] if len(args) > 1 else DEFAULT_PORT
        print(json.dumps(scan_capabilities(port), indent=2))
    else:
        port = args[0] if args else DEFAULT_PORT
        print(json.dumps(run_session(port), indent=2))
