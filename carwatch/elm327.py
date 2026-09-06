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


def _first_present_port() -> str:
    """USB adapters first, then the Bluetooth rfcomm binding.

    A bare "/dev/ttyUSB0" default sent every no-argument invocation to a
    device that does not exist when the adapter is the Bluetooth ELM327
    (grok, Aug 24: probe looked only at ttyUSB0 while the car was live on
    rfcomm0). Falls back to ttyUSB0 so error messages still name a path.
    """
    for p in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0"):
        if os.path.exists(p):
            return p
    return "/dev/ttyUSB0"


DEFAULT_PORT = _first_present_port()
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


class AdapterUnreachable(OSError):
    """The adapter node exists but the link behind it is dead: the Bluetooth
    dongle sleeps a few minutes after ignition off and /dev/rfcomm0 then
    answers every write with EIO. Sep 6 2026: that EIO escaped as a raw
    traceback into the dashboard and killed carwatch-obd.service."""


class Elm327:
    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD):
        self.port = port
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
        try:
            os.write(self.fd, (line + "\r").encode("ascii"))
        except OSError as e:
            raise AdapterUnreachable(
                f"adapter not answering on {getattr(self, 'port', '?')}: "
                f"{e.strerror or e} (car off or Bluetooth link closed)") from e
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


# ── FULL standard decode table for the nerd dashboard (petrus, Aug 19:
# "purkakaa kaikki saadut tiedot"). Every formula is standard SAE J1979.
# pid -> (key, label, unit, group, nbytes, decoder). Groups drive the
# dashboard layout: related values render together (temperatures with
# temperatures, etc). This is a superset of PIDS; PIDS stays untouched so
# the proven telemetry path cannot regress.
def _pct(b):        return round(b[0] * 100 / 255, 1)
def _trim(b):       return round((b[0] - 128) * 100 / 128, 1)
def _minus40(b):    return b[0] - 40
def _u16(b):        return (b[0] << 8) | b[1]

_FUEL_TYPES = {1: "gasoline", 2: "methanol", 3: "ethanol", 4: "diesel",
               5: "LPG", 6: "CNG", 8: "electric", 17: "plug-in hybrid gasoline",
               19: "plug-in hybrid diesel", 21: "hybrid gasoline",
               23: "plug-in hybrid diesel", 24: "hybrid electric"}

EXT_PIDS = {
    0x04: ("engine_load_pct", "engine load", "%", "engine", 1, _pct),
    0x05: ("coolant_c", "coolant temp", "°C", "temperatures", 1, _minus40),
    0x06: ("short_fuel_trim_pct", "short-term fuel trim", "%", "fuel", 1, _trim),
    0x07: ("long_fuel_trim_pct", "long-term fuel trim", "%", "fuel", 1, _trim),
    0x0A: ("fuel_pressure_kpa", "fuel pressure", "kPa", "pressures", 1,
           lambda b: b[0] * 3),
    0x0B: ("intake_map_kpa", "intake manifold pressure", "kPa", "pressures", 1,
           lambda b: b[0]),
    0x0C: ("engine_rpm", "engine speed", "rpm", "engine", 2,
           lambda b: _u16(b) / 4.0),
    0x0D: ("speed_kmh", "vehicle speed", "km/h", "driving", 1, lambda b: b[0]),
    0x0E: ("timing_advance_deg", "timing advance", "°", "engine", 1,
           lambda b: b[0] / 2.0 - 64),
    0x0F: ("intake_air_c", "intake air temp", "°C", "temperatures", 1, _minus40),
    0x10: ("maf_gps", "MAF air flow", "g/s", "engine", 2,
           lambda b: round(_u16(b) / 100.0, 2)),
    0x11: ("throttle_pct", "throttle position", "%", "driving", 1, _pct),
    0x1F: ("runtime_s", "run time since start", "s", "engine", 2, _u16),
    0x21: ("distance_mil_km", "distance with MIL on", "km", "diagnostics", 2, _u16),
    0x22: ("fuel_rail_kpa", "fuel rail pressure (rel)", "kPa", "pressures", 2,
           lambda b: round(_u16(b) * 0.079, 1)),
    0x23: ("fuel_rail_gauge_kpa", "fuel rail gauge pressure", "kPa", "pressures", 2,
           lambda b: _u16(b) * 10),
    0x2C: ("egr_cmd_pct", "commanded EGR", "%", "engine", 1, _pct),
    0x2D: ("egr_error_pct", "EGR error", "%", "engine", 1, _trim),
    0x2F: ("fuel_level_pct", "fuel tank level", "%", "fuel", 1, _pct),
    0x31: ("distance_clear_km", "distance since codes cleared", "km",
           "diagnostics", 2, _u16),
    0x33: ("baro_kpa", "barometric pressure", "kPa", "pressures", 1,
           lambda b: b[0]),
    0x42: ("module_voltage", "control module voltage", "V", "electrical", 2,
           lambda b: round(_u16(b) / 1000.0, 2)),
    0x43: ("abs_load_pct", "absolute load", "%", "engine", 2,
           lambda b: round(_u16(b) * 100 / 255, 1)),
    0x46: ("ambient_air_c", "ambient air temp", "°C", "temperatures", 1, _minus40),
    0x47: ("throttle_b_pct", "abs throttle B", "%", "driving", 1, _pct),
    0x49: ("pedal_d_pct", "accelerator pedal D", "%", "driving", 1, _pct),
    0x4C: ("throttle_cmd_pct", "commanded throttle", "%", "driving", 1, _pct),
    0x51: ("fuel_type", "fuel type", "", "fuel", 1,
           lambda b: _FUEL_TYPES.get(b[0], f"code {b[0]}")),
    0x5A: ("pedal_rel_pct", "relative accelerator pedal", "%", "driving", 1, _pct),
    0x5B: ("hybrid_battery_pct", "hybrid battery", "%", "hybrid", 1, _pct),
    0x5C: ("oil_c", "engine oil temp", "°C", "temperatures", 1, _minus40),
    0x5E: ("fuel_rate_lph", "engine fuel rate", "L/h", "fuel", 2,
           lambda b: round(_u16(b) / 20.0, 1)),
}


def _parse_ext_reply(text: str, pid: int):
    """Like _parse_pid_reply but against the EXT_PIDS table."""
    toks = text.replace("\r", " ").split()
    hexes = [t for t in toks if len(t) == 2 and
             all(ch in "0123456789ABCDEFabcdef" for ch in t)]
    for i in range(len(hexes) - 1):
        if hexes[i].upper() == "41" and int(hexes[i + 1], 16) == pid:
            key, label, unit, group, nbytes, dec = EXT_PIDS[pid]
            data = [int(x, 16) for x in hexes[i + 2:i + 2 + nbytes]]
            if len(data) < nbytes:
                return None
            try:
                return {"key": key, "label": label, "unit": unit,
                        "group": group, "pid": f"0x{pid:02X}",
                        "value": dec(data)}
            except Exception:
                return None
    return None


def read_all_extended(elm: Elm327, pids=None) -> dict:
    """Read EVERY decodable PID for the nerd dashboard, grouped. By default
    reads the intersection of what the car advertises and EXT_PIDS; an
    explicit `pids` list overrides (used by the bench to exercise all
    decoders against the fake without bitmask surgery)."""
    if pids is None:
        try:
            supported = set(scan_supported_quiet(elm))
        except Exception:
            supported = set()
        pids = [p for p in EXT_PIDS if p in supported] or list(PIDS)
    groups: dict = {}
    read_count = 0
    for pid in sorted(pids):
        if pid not in EXT_PIDS:
            continue
        parsed = _parse_ext_reply(elm.cmd(f"01{pid:02X}"), pid)
        if parsed:
            read_count += 1
            groups.setdefault(parsed.pop("group"), {})[parsed["key"]] = parsed
    return {"groups": groups, "read_count": read_count,
            "attempted": len(list(pids))}


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


# UDS negative-response codes (ISO 14229). We classify these so a DID that is
# SILENT (wrong address / not present) never looks the same as one that is
# LOCKED (0x33 - the data exists, it just needs a security unlock we do not
# do) or genuinely NOT SUPPORTED (0x11/0x31). That distinction is the whole
# reason for the per-ECU probe: "no Mercedes data" was a premature conclusion
# when we only ever asked the broadcast address 0x7DF (claudemm, Aug 19).
_NRC = {
    0x10: "general reject",
    0x11: "service not supported",
    0x12: "sub-function not supported",
    0x13: "incorrect message length",
    0x14: "response too long",
    0x22: "conditions not correct",
    0x31: "request out of range (DID not on this ECU)",
    0x33: "security access denied (data exists, locked)",
    0x35: "invalid key",
    0x36: "exceeded attempts",
    0x37: "required time delay not expired",
    0x78: "response pending",
    0x7E: "service not supported in active session",
    0x7F: "service not supported in active session",
}

# Powertrain ECUs physically addressable on the OBD-II 11-bit CAN bus. In
# ISO 15765-4 the response ID is always request+8. Broadcast 0x7DF reaches
# whoever answers first (usually just the engine); addressing each ECU
# directly with ATSH is how a module's OWN identity/manufacturer DIDs are
# actually reached. Labels are best-effort roles - the probe reports the raw
# address too, so a wrong guess never hides a real answer.
_ECUS = [
    (0x7E0, 0x7E8, "engine / powertrain"),
    (0x7E1, 0x7E9, "ECU @7E1"),
    (0x7E2, 0x7EA, "transmission / @7E2"),
    (0x7E3, 0x7EB, "ECU @7E3"),
    (0x7E4, 0x7EC, "ECU @7E4"),
    (0x7E5, 0x7ED, "ECU @7E5"),
]

# Standard ISO 14229 identification DIDs (mode 22). Each control unit answers
# with ITS OWN values, so probing them per-ECU yields real per-module identity
# (part number, software version, serial). Mercedes proprietary EV/hybrid DIDs
# are not publicly mapped, so we probe the DEFINED identity block and record
# every NRC - which teaches us, per ECU, whether deeper reads are unsupported
# (0x11/0x31) or merely locked behind security access (0x33).
_ID_DIDS = {
    "F190": "VIN",
    "F187": "spare part number",
    "F18C": "ECU serial number",
    "F191": "hardware version",
    "F195": "software version",
    "F197": "system name",
    "F18A": "supplier identifier",
    "F1A0": "vehicle config (MB-specific)",
}


def _parse_did_reply(reply: str, did: str) -> dict:
    """Classify a mode-22 reply. Returns {status, nrc, nrc_name, raw, text}:
      'ok'   - positive '62 <did> <data>', ASCII captured in text
      'nrc'  - negative '7F 22 <code>', code + name recorded
      'none' - no / garbled response (wrong address, quiet bus, adapter error)
    With headers off and a receive-address filter the ELM327 reassembles the
    ISO-TP payload, so a clean 62/7F is what we parse."""
    raw = reply.strip()
    up = raw.upper()
    toks = [t for t in up.replace("\r", " ").split()
            if len(t) == 2 and all(c in "0123456789ABCDEF" for c in t)]
    vals = [int(t, 16) for t in toks]
    did_hi, did_lo = int(did[:2], 16), int(did[2:], 16)
    # Negative response: 7F 22 <nrc>.
    for i in range(len(vals) - 2):
        if vals[i] == 0x7F and vals[i + 1] == 0x22:
            code = vals[i + 2]
            return {"status": "nrc", "nrc": f"0x{code:02X}",
                    "nrc_name": _NRC.get(code, "unknown NRC"),
                    "raw": raw[:120], "text": ""}
    # Positive response: 62 <did_hi> <did_lo> <data...>.
    for i in range(len(vals) - 2):
        if vals[i] == 0x62 and vals[i + 1] == did_hi and vals[i + 2] == did_lo:
            data = vals[i + 3:]
            text = "".join(chr(b) for b in data if 0x20 <= b <= 0x7E).strip()
            return {"status": "ok", "nrc": "", "nrc_name": "",
                    "raw": raw[:120], "text": text}
    return {"status": "none", "nrc": "", "nrc_name": "",
            "raw": raw[:120], "text": ""}


def deep_probe(port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD,
               cap_s: float = 75.0) -> dict:
    """Per-ECU addressed Mercedes identity read, BOUNDED, read-only. Runs
    on-board when the Pi is connected AND the car is stationary (the daemon
    gates that). For each powertrain ECU it sets the request/response headers
    (ATSH/ATCRA), opens an extended diagnostic session (10 03, non-intrusive),
    and reads the ISO 14229 identity DIDs - CAPTURING the raw reply and, on a
    negative response, the exact NRC. Never writes (only services 10/22). The
    result distinguishes answered / negative-with-NRC / silent per ECU, and
    flags itself DEGRADED if it could not confirm the bus or hit the time cap,
    so a partial pass never reads as a confident 'no data'."""
    import time as _t
    out = {"ok": False, "vin": "", "supported_pid_count": 0,
           "bus_responsive": False, "ecus": [], "mode22": {},
           "ecus_answered": 0, "degraded": False, "trace": []}
    if not os.path.exists(port):
        out["trace"].append("no adapter")
        return out
    try:
        elm = Elm327(port, baud)
    except Exception as e:
        out["trace"].append(f"open failed: {e}")
        return out
    started = _t.time()
    try:
        elm.init()
        # Baseline on broadcast: a supported-PID count proves the bus answers
        # at all, so a later empty per-ECU pass is clearly addressing and not
        # a dead bus.
        try:
            pids = scan_supported_quiet(elm)
            out["supported_pid_count"] = len(pids)
            out["bus_responsive"] = bool(pids)
        except Exception:
            pass
        out["vin"] = read_vin(elm)
        for req, resp, label in _ECUS:
            if _t.time() - started > cap_s:
                out["degraded"] = True
                out["trace"].append("time cap hit before all ECUs scanned")
                break
            elm.cmd(f"ATSH{req:03X}", 1.0)      # request header -> this ECU
            elm.cmd(f"ATCRA{resp:03X}", 1.0)    # accept only its response
            sess = elm.cmd("1003", 2.0)         # extended diag session
            rec = {"req": f"0x{req:03X}", "resp": f"0x{resp:03X}",
                   "label": label, "answered": False,
                   "session_ok": "50" in sess.upper() and "7F" not in sess.upper(),
                   "dids": {}}
            for did, dlabel in _ID_DIDS.items():
                if _t.time() - started > cap_s:
                    out["degraded"] = True
                    break
                raw_reply = elm.cmd(f"22{did}", 2.0)
                r = _parse_did_reply(raw_reply, did)
                # Store the RAW response for EVERY DID, including "no response"
                # (claudemm, Aug 19): a summary that only says "no DIDs
                # answered" throws away the bytes that separate wrong-address
                # (silent) from not-supported (0x11/0x31) from locked (0x33).
                # Once discarded the question can never be re-answered from the
                # drive, so the raw string is kept verbatim here and on disk.
                entry = {"label": dlabel, "status": r["status"],
                         "raw": raw_reply.strip()[:160]}
                if r["status"] == "ok":
                    rec["answered"] = True
                    entry["text"] = r["text"]
                    out["mode22"][f"{req:03X}:{did}"] = {
                        "label": f"{label} {dlabel}", "text": r["text"],
                        "raw": r["raw"]}
                elif r["status"] == "nrc":
                    rec["answered"] = True  # it DID respond, just negatively
                    entry["nrc"] = r["nrc"]
                    entry["nrc_name"] = r["nrc_name"]
                rec["dids"][did] = entry
            out["ecus"].append(rec)
        # Restore broadcast addressing so normal telemetry reads are unaffected.
        try:
            elm.cmd("ATAR", 1.0)     # auto receive address
            elm.cmd("ATSH7DF", 1.0)  # back to functional broadcast
        except Exception:
            pass
    except Exception as e:
        out["trace"].append(f"probe error: {e}")
        out["degraded"] = True
    finally:
        elm.close()
    # An ECU's own F190 (VIN via mode 22, ISO-TP reassembled to a clean
    # 62 F1 90 <17 bytes>) is a more reliable VIN than the lenient mode-09
    # read, so prefer it when a module answered it.
    for key, v in out["mode22"].items():
        if key.endswith(":F190") and v.get("text"):
            out["vin"] = v["text"]
            break
    answered = [e for e in out["ecus"] if e["answered"]]
    out["ecus_answered"] = len(answered)
    out["ok"] = bool(out["vin"] or out["mode22"] or out["bus_responsive"])
    # Could not even confirm the bus and nobody answered -> degraded, so the
    # daemon retries next stationary cycle instead of marking the day done.
    if not out["bus_responsive"] and not answered:
        out["degraded"] = True
    out["elapsed_s"] = round(_t.time() - started, 1)
    return out


def scan_supported_quiet(elm: "Elm327") -> list:
    """Supported mode-01 PIDs without opening a new connection (reuses elm)."""
    supported = []
    for base in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0):
        block = _bitmask_pids(elm.cmd(f"01{base:02X}"), base)
        supported += block
        if (base + 0x20) not in block and base != 0x00:
            break
    return sorted(set(supported))


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
    except AdapterUnreachable as e:
        # Not a fault of ours and not worth a traceback: say what it is.
        trace.append({"stage": "handshake", "ok": False, "detail": str(e)})
        result["state"] = "adapter_asleep"
        result["summary"] = ("adapter asleep or car off: the ELM327 is bound "
                             "but its link is closed; starts again with the ignition")
        return result
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


def run_all(port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD) -> dict:
    """One-shot FULL decode for the nerd dashboard: every supported PID in
    EXT_PIDS, grouped, plus stored DTCs. Read-only."""
    import time as _t
    out = {"ok": False, "groups": {}, "dtcs": [], "read_count": 0,
           "attempted": 0, "error": ""}
    if not os.path.exists(port):
        out["error"] = "no adapter present (car not connected)"
        return out
    started = _t.time()
    try:
        elm = Elm327(port, baud)
    except Exception as e:
        out["error"] = f"could not open {port}: {e}"
        return out
    try:
        elm.init()
        ext = read_all_extended(elm)
        out.update(ext)
        out["dtcs"] = read_dtcs(elm)
    except Exception as e:
        out["error"] = f"read error: {e}"
    finally:
        elm.close()
    out["elapsed_s"] = round(_t.time() - started, 1)
    out["ok"] = out["read_count"] > 0
    return out


if __name__ == "__main__":
    import json
    import sys
    args = sys.argv[1:]
    if args and args[0] == "deep":
        # petrus, Aug 22, having asked four times: the PER-ECU read, not the
        # capability list. Reached over the car's own dial-out tunnel through
        # the existing /api/obd/scan style path, so it needs no inbound access.
        # Strictly read-only: passive monitoring plus mode-22 READ requests.
        from . import deepscan as _ds
        import subprocess as _sp
        port = args[1] if len(args) > 1 else DEFAULT_PORT
        # The live poller owns this serial port and polls it continuously.
        # Two readers on one ELM327 gives EIO half way through, which is
        # exactly how the first attempt died. Pause it, probe, restore.
        _paused = False
        try:
            _sp.run(["systemctl", "stop", "carwatch-obd.service"],
                    capture_output=True, timeout=20)
            _paused = True
            time.sleep(1.5)
        except Exception:
            pass
        _elm = None
        try:
            _elm = Elm327(port)
            mon = _ds.monitor_bus(_elm, seconds=float(args[2]) if len(args) > 2 else 12.0)
            ident = _ds.probe_identity(_elm)
            print(json.dumps({"ok": True, "monitor": mon, "identity": ident,
                              "summary": _ds.summarise(mon, ident)}, indent=2))
        finally:
            if _elm is not None:
                try:
                    _elm.close()
                except Exception:
                    pass
            # The car must never be left without its live read.
            if _paused:
                try:
                    _sp.run(["systemctl", "start", "carwatch-obd.service"],
                            capture_output=True, timeout=20)
                except Exception:
                    pass
    elif args and args[0] == "record":
        # Raw ATMA capture to disk for the broadcast-stream decode (petrus,
        # Aug 24). Same pause/restore discipline as "deep": one reader on
        # the serial port at a time, and the car never stays without its
        # live poll.
        from . import deepscan as _ds
        import subprocess as _sp
        port = args[1] if len(args) > 1 else DEFAULT_PORT
        secs = float(args[2]) if len(args) > 2 else 90.0
        _paused = False
        try:
            _sp.run(["systemctl", "stop", "carwatch-obd.service"],
                    capture_output=True, timeout=20)
            _paused = True
            time.sleep(1.5)
        except Exception:
            pass
        _elm = None
        try:
            # The Bluetooth ELM drops its RFCOMM link when its reader goes
            # away and needs one failed touch to re-establish: the in-car
            # deep on Aug 24 succeeded only on the second attempt (errno 5
            # first). Bake that observed pattern in instead of failing.
            _last = None
            for _attempt in range(3):
                try:
                    _elm = Elm327(port)
                    print(json.dumps({"ok": True, "attempt": _attempt + 1,
                                      **_ds.record_bus(_elm, secs)}))
                    _last = None
                    break
                except OSError as _e:
                    _last = _e
                    try:
                        if _elm is not None:
                            _elm.close()
                    except Exception:
                        pass
                    _elm = None
                    time.sleep(3.0)
            if _last is not None:
                print(json.dumps({"ok": False,
                                  "error": f"record failed after retries: {_last}"}))
        finally:
            if _elm is not None:
                try:
                    _elm.close()
                except Exception:
                    pass
            if _paused:
                try:
                    _sp.run(["systemctl", "start", "carwatch-obd.service"],
                            capture_output=True, timeout=20)
                except Exception:
                    pass
    elif args and args[0] == "scan":
        port = args[1] if len(args) > 1 else DEFAULT_PORT
        print(json.dumps(scan_capabilities(port), indent=2))
    elif args and args[0] == "all":
        port = args[1] if len(args) > 1 else DEFAULT_PORT
        print(json.dumps(run_all(port), indent=2))
    else:
        port = args[0] if args else DEFAULT_PORT
        try:
            print(json.dumps(run_session(port), indent=2))
        except Exception as e:  # the dashboard shows stdout verbatim: never a traceback
            print(json.dumps({"ok": False, "state": "error",
                              "summary": f"OBD probe failed: {type(e).__name__}: {e}"},
                             indent=2))
            sys.exit(2)
