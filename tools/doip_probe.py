#!/usr/bin/env python3
"""Reach the GLE's DoIP gateway over the yellow ENET cable - honestly.

v2, after a live test (Aug 12) exposed two bugs in v1:
  1. It counted ANY UDP reply as success, so the Pi hearing its own
     broadcast echo on the phone hotspot read as "DoIP link ALIVE". False.
  2. It broadcast on the default route (the hotspot), not down eth0 to the
     car, and eth0 had no address on the car's diagnostic subnet.

This version:
  - assigns a link-local IPv4 to eth0 (the ENET/DoIP convention) and brings
    it up, so the Pi is actually ON the car's diagnostic network;
  - sends the vehicle-identification request FROM eth0's address to the
    link-local broadcast, so it goes down the cable, not the hotspot;
  - accepts a reply ONLY if it is a real identification RESPONSE
    (payload type 0x0004) from an address that is NOT one of ours - no
    self-echo can fool it;
  - also listens for the car's UNSOLICITED announcement, which a DoIP
    gateway sends to the tester when the ENET link comes up.

Read-only. It sends identification requests and listens. It changes nothing
in the car. Still UNPROVEN until a real GLE answers with a 0x0004.
"""

from __future__ import annotations

import socket
import struct
import subprocess
import sys
import time

DOIP_PORT = 13400
DOIP_VERSION = 0x02
PT_IDENT_REQ = 0x0001
PT_IDENT_RES = 0x0004          # the ONLY payload that proves a car answered
PT_ANNOUNCE = 0x0004           # gateways announce with the same type
ETH_IF = "eth0"
TESTER_IP = "169.254.100.100"  # self-assigned link-local (ENET convention)
LL_BCAST = "169.254.255.255"

IDENT_REQUEST = struct.pack(">BBHI", DOIP_VERSION, 0xFF & ~DOIP_VERSION,
                            PT_IDENT_REQ, 0)


def _run(cmd: list[str], timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"({e})"


def own_ipv4s() -> set[str]:
    out = _run(["ip", "-4", "-o", "addr"])
    ips = set()
    for line in out.splitlines():
        parts = line.split()
        for i, p in enumerate(parts):
            if p == "inet" and i + 1 < len(parts):
                ips.add(parts[i + 1].split("/")[0])
    ips.add(TESTER_IP)
    return ips


def show_interfaces() -> None:
    print("=== network interfaces ===", flush=True)
    print(_run(["ip", "-brief", "addr"]) or "(none)", flush=True)
    print(flush=True)


def bring_up_eth0() -> bool:
    print(f"=== bringing up {ETH_IF} on the car's diagnostic subnet ===",
          flush=True)
    _run(["sudo", "ip", "link", "set", ETH_IF, "up"])
    # Add our link-local tester address (harmless if already present).
    _run(["sudo", "ip", "addr", "add", f"{TESTER_IP}/16", "dev", ETH_IF])
    state = _run(["cat", f"/sys/class/net/{ETH_IF}/operstate"])
    carrier = _run(["cat", f"/sys/class/net/{ETH_IF}/carrier"])
    print(f"{ETH_IF} operstate={state} carrier={carrier} "
          f"(carrier=1 means the cable has a live link to the car)", flush=True)
    print(f"tester address: {TESTER_IP}/16 on {ETH_IF}\n", flush=True)
    return carrier == "1"


def parse_response(data: bytes) -> tuple[int, str] | None:
    if len(data) < 8:
        return None
    _v, _i, ptype, plen = struct.unpack(">BBHI", data[:8])
    if ptype != PT_IDENT_RES:
        return ptype, ""
    body = data[8:]
    vin = body[:17].decode("ascii", "replace") if len(body) >= 17 else "?"
    logical = struct.unpack(">H", body[17:19])[0] if len(body) >= 19 else 0
    return ptype, f"VIN={vin} logical=0x{logical:04x}"


def probe(ours: set[str]) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((TESTER_IP, DOIP_PORT))  # send/receive ON eth0's subnet
    except Exception as e:
        print(f"could not bind {TESTER_IP}:{DOIP_PORT} - is eth0 up with that "
              f"address? ({e})", flush=True)
        try:
            s.bind(("", DOIP_PORT))
        except Exception:
            pass
    print("=== identification request down the cable ===", flush=True)
    for target in (LL_BCAST, "255.255.255.255"):
        try:
            s.sendto(IDENT_REQUEST, (target, DOIP_PORT))
            print(f"sent to {target}", flush=True)
        except Exception as e:
            print(f"send to {target} failed: {e}", flush=True)
    print("listening 8s for a REAL car response (ignoring our own echo)...",
          flush=True)
    s.settimeout(8)
    end = time.time() + 8
    while time.time() < end:
        try:
            data, addr = s.recvfrom(1024)
        except socket.timeout:
            break
        except Exception:
            break
        src = addr[0]
        if src in ours:
            continue  # our own broadcast bouncing back - not the car
        parsed = parse_response(data)
        if not parsed:
            continue
        ptype, info = parsed
        if ptype == PT_IDENT_RES:
            print(f"\nCAR ANSWERED from {src}: {info}", flush=True)
            print("This is a genuine DoIP response (0x0004) from a non-self "
                  "address. The link to the car is REAL.", flush=True)
            s.close()
            return True
        print(f"non-response packet from {src} (type 0x{ptype:04x}), ignoring",
              flush=True)
    s.close()
    return False


if __name__ == "__main__":
    print("CarWatch DoIP probe v2 - read-only, honest\n", flush=True)
    show_interfaces()
    ours = own_ipv4s()
    has_carrier = bring_up_eth0()
    ok = probe(ours)
    if ok:
        print("\nRESULT: DoIP link to the car CONFIRMED. Next: routing "
              "activation + a UDS read (VIN/voltage).", flush=True)
        sys.exit(0)
    print("\nRESULT: no genuine car response yet.", flush=True)
    if not has_carrier:
        print(" - eth0 has NO carrier: the ENET cable is not making a live "
              "link. Check it is seated in the Pi's ethernet port and in the "
              "car's OBD/ENET side, and that ignition is ON.", flush=True)
    else:
        print(" - eth0 link is live but the gateway did not answer. Likely "
              "the car needs ignition ON (not just accessory), or the DoIP "
              "activation line on this cable behaves differently and we need "
              "to target the gateway address directly. This is the real "
              "bring-up work, and it is progress: the cable link is up.",
              flush=True)
    sys.exit(1)
