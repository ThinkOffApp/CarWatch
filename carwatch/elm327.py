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


def read_dtcs(elm: Elm327) -> list[str]:
    """Stored trouble codes via mode 03. Best-effort decode of the standard
    2-byte-per-code format into Pxxxx/Cxxxx/Bxxxx/Uxxxx strings."""
    reply = elm.cmd("03")
    toks = [t for t in reply.split() if len(t) == 2 and
            all(ch in "0123456789ABCDEFabcdef" for ch in t)]
    if not toks or toks[0].upper() != "43":
        return []
    body = [int(x, 16) for x in toks[1:]]
    codes = []
    for i in range(0, len(body) - 1, 2):
        a, b = body[i], body[i + 1]
        if a == 0 and b == 0:
            continue
        prefix = "PCBU"[(a >> 6) & 0x3]
        codes.append(f"{prefix}{(a >> 4) & 0x3}{a & 0xF:X}{b:02X}")
    return codes


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
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    print(json.dumps(run_session(port), indent=2))
