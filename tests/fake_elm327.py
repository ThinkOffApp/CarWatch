"""A fake ELM327 adapter + car on a PTY: proves carwatch.elm327 end to end
before the real adapter exists (the DoIP fake-gateway discipline, applied to
the CAN path chosen after the Aug 13 real-car verdict).

Creates a pseudo-terminal; the master side plays an ELM327 v1.5 with a car
behind it answering mode-01 PIDs and mode-03 DTCs with DISTINCTIVE values,
so a passing test proves decoding, not just plumbing:
  rpm 1750.0, coolant 88, speed 42, 14.1V, fuel 63.5%, load 27.5%,
  intake 21, hybrid 80.0%, and one stored DTC: P0420.

Usage (also used by the self-test below):
    python3 tests/fake_elm327.py --self-test
prints the slave PTY path on stdout when run standalone, so:
    python3 -m carwatch.elm327 $(python3 tests/fake_elm327.py &)  # manual
"""

from __future__ import annotations

import os
import pty
import re
import threading

RESPONSES = {
    "0100": "41 00 BE 3F A8 13",
    "0104": "41 04 46",          # load 70/255 = 27.5%
    "0105": "41 05 80",          # coolant 0x80-40 = 88
    "010C": "41 0C 1B 58",       # rpm 0x1B58/4 = 1750.0
    "010D": "41 0D 2A",          # speed 42
    "010F": "41 0F 3D",          # intake 0x3D-40 = 21
    "012F": "41 2F A2",          # fuel 162/255 = 63.5%
    "0142": "41 42 37 14",       # 0x3714/1000 = 14.1V
    "015B": "41 5B CC",          # hybrid 204/255 = 80.0%
    "03":   "43 04 20 00 00",    # one DTC: P0420
}


def serve(master_fd: int) -> None:
    buf = b""
    while True:
        try:
            chunk = os.read(master_fd, 256)
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        while b"\r" in buf:
            line, buf = buf.split(b"\r", 1)
            cmd = line.decode("ascii", "replace").strip().upper()
            if not cmd:
                continue
            if cmd.startswith("AT"):
                reply = "ELM327 v1.5\r\rOK" if cmd == "ATZ" else "OK"
            else:
                reply = RESPONSES.get(cmd, "NO DATA")
            os.write(master_fd, (cmd + "\r" + reply + "\r\r>").encode())


def start() -> str:
    """Start the fake adapter; returns the slave PTY path."""
    master, slave = pty.openpty()
    threading.Thread(target=serve, args=(master,), daemon=True).start()
    return os.ttyname(slave)


def self_test() -> None:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from carwatch import elm327

    port = start()
    # Use the REAL _open_serial: its raw-termios setup (echo off) is exactly
    # what a PTY needs too - a plain os.open left slave echo on and every
    # reply arrived one command late (caught by this test's first run).
    result = elm327.run_session(port)
    expected = {
        "engine_load_pct": 27.5, "coolant_c": 88, "engine_rpm": 1750.0,
        "speed_kmh": 42, "intake_air_c": 21, "fuel_level_pct": 63.5,
        "module_voltage": 14.1, "hybrid_battery_pct": 80.0,
    }
    ok = result["ok"] and result["readings"] == expected \
        and result["dtcs"] == ["P0420"]
    print("readings:", result["readings"])
    print("dtcs:", result["dtcs"])
    print("SELF-TEST", "PASS" if ok else f"FAIL (expected {expected})")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        self_test()
    else:
        print(start())
        threading.Event().wait()
