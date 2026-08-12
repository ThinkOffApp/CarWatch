"""The complete OBD read, end to end: cable -> gateway -> engine numbers.

This is the piece petrus asked for on Aug 12 ("MAKE THE OBD SW NOW") after
driving to the car three times for software that was never finished. One
entrypoint does the WHOLE job and reports honestly at every step:

  1. bring up eth0 with a link-local tester address (ENET/DoIP convention)
  2. discover the car's DoIP gateway (UDP vehicle identification broadcast,
     learning its IP and logical address from the response)
  3. TCP connect + routing activation
  4. read the engine PIDs (RPM, coolant, speed, module voltage)

Output is one JSON object on stdout, always, with a stage-by-stage trace so
a failure says exactly HOW FAR it got and why - no more guessing from the
passenger seat.

Testability without the car: --gateway <ip> [--port N] skips discovery and
targets a given gateway, so the entire flow (connect, activation, UDS reads,
decoding) runs against tests/fake_gateway.py locally. That local round-trip
is the pre-car proof; only step 2 (real discovery) needs the real car.

Read-only. Mode-01 reads; nothing here writes to the car.

Usage:
    sudo python3 -m carwatch.obd_session              # the real thing, in car
    python3 -m carwatch.obd_session --gateway 127.0.0.1 --port 13400 --no-eth0
"""

from __future__ import annotations

import json
import socket
import struct
import subprocess
import sys
import time

from carwatch.obd import (
    DOIP_PORT, PT_VEHICLE_IDENT_REQ, PT_VEHICLE_IDENT_RES,
    _doip_frame, _parse_doip, connect, read_all,
)

ETH_IF = "eth0"
TESTER_IP = "169.254.100.100"
LL_BCAST = "169.254.255.255"


def _sh(cmd: list[str], timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"({e})"


def stage_eth0(trace: list[dict]) -> bool:
    """Bring eth0 up on the diagnostic subnet; report carrier honestly."""
    _sh(["sudo", "ip", "link", "set", ETH_IF, "up"])
    _sh(["sudo", "ip", "addr", "add", f"{TESTER_IP}/16", "dev", ETH_IF])
    carrier = _sh(["cat", f"/sys/class/net/{ETH_IF}/carrier"])
    ok = carrier == "1"
    trace.append({"stage": "eth0", "ok": ok, "carrier": carrier,
                  "detail": "cable has a live electrical link to the car"
                  if ok else "NO electrical link: cable unplugged, ignition "
                  "off, or this cable does not connect on this car"})
    return ok


def stage_discover(trace: list[dict], own_ips: set[str],
                   wait_s: float = 8.0) -> tuple[str, int] | None:
    """UDP vehicle identification: learn the gateway's IP + logical address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((TESTER_IP, DOIP_PORT))
    except Exception:
        try:
            s.bind(("", DOIP_PORT))
        except Exception as e:
            trace.append({"stage": "discover", "ok": False,
                          "detail": f"could not bind UDP {DOIP_PORT}: {e}"})
            return None
    req = _doip_frame(PT_VEHICLE_IDENT_REQ, b"")
    sent_to = []
    for target in (LL_BCAST, "255.255.255.255"):
        try:
            s.sendto(req, (target, DOIP_PORT))
            sent_to.append(target)
        except Exception:
            pass
    s.settimeout(wait_s)
    end = time.time() + wait_s
    while time.time() < end:
        try:
            data, addr = s.recvfrom(1024)
        except (socket.timeout, OSError):
            break
        if addr[0] in own_ips:
            continue  # our own broadcast echo - never the car
        parsed = _parse_doip(data)
        if not parsed or parsed[0] != PT_VEHICLE_IDENT_RES:
            continue
        body = parsed[1]
        vin = body[:17].decode("ascii", "replace") if len(body) >= 17 else "?"
        logical = struct.unpack(">H", body[17:19])[0] if len(body) >= 19 else 0
        s.close()
        trace.append({"stage": "discover", "ok": True, "gateway_ip": addr[0],
                      "vin": vin, "logical_addr": f"0x{logical:04x}",
                      "detail": "car answered vehicle identification"})
        return addr[0], logical
    s.close()
    trace.append({"stage": "discover", "ok": False, "sent_to": sent_to,
                  "detail": "no identification response from the car "
                  f"within {wait_s:.0f}s"})
    return None


def own_ipv4s() -> set[str]:
    ips = {TESTER_IP, "127.0.0.1"}
    out = _sh(["ip", "-4", "-o", "addr"])
    for line in out.splitlines():
        parts = line.split()
        for i, p in enumerate(parts):
            if p == "inet" and i + 1 < len(parts):
                ips.add(parts[i + 1].split("/")[0])
    return ips


def run_session(gateway: str | None = None, port: int = DOIP_PORT,
                bring_eth0: bool = True) -> dict:
    """The whole flow. Returns the result dict (also printed by main)."""
    trace: list[dict] = []
    result: dict = {"ok": False, "readings": {}, "trace": trace}

    if bring_eth0:
        stage_eth0(trace)  # informative; discovery below is the hard gate

    target_logical = None
    if gateway is None:
        found = stage_discover(trace, own_ipv4s())
        if not found:
            result["summary"] = ("no car found on the cable - see trace; "
                                 "OBD read did NOT happen")
            return result
        gateway, target_logical = found

    link = connect(gateway, port=port,
                   **({"target_addr": target_logical}
                      if target_logical else {}))
    if not link:
        trace.append({"stage": "connect", "ok": False,
                      "detail": f"TCP {gateway}:{port} unreachable or "
                      "routing activation refused"})
        result["summary"] = "gateway found but session refused - see trace"
        return result
    trace.append({"stage": "connect", "ok": True,
                  "detail": f"routing activated with {gateway}:{port}"})

    readings = read_all(link)
    link.sock.close()
    trace.append({"stage": "read", "ok": bool(readings),
                  "count": len(readings)})
    result["readings"] = readings
    result["ok"] = bool(readings)
    result["summary"] = (f"read {len(readings)} live values from the engine"
                         if readings else
                         "session up but the car returned no PID data")
    return result


def main() -> None:
    args = sys.argv[1:]
    gateway = None
    port = DOIP_PORT
    bring = "--no-eth0" not in args
    if "--gateway" in args:
        gateway = args[args.index("--gateway") + 1]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    result = run_session(gateway, port, bring)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
