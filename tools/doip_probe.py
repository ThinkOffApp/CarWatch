#!/usr/bin/env python3
"""First real step of OBD-over-ENET: does the car's DoIP gateway answer?

The GLE (W167) speaks DoIP - diagnostics over IP - through the yellow
ENET cable into the Pi's ethernet port. Before any engine reading we must
know three things, in order, and this script answers them without any pip
install (raw sockets only, so nothing to fail in a car):

  1. Is eth0 up with a link to the car at all?
  2. Does the car's DoIP gateway answer a vehicle-identification broadcast?
     (payload type 0x0001 -> 0x0004) - this alone proves the cable works
     and hands us the car's logical address.
  3. (printed for the next step) what address to route-activate against.

Run on the Pi (petrus's hands, car powered):
    curl -sSL https://raw.githubusercontent.com/ThinkOffApp/CarWatch/main/tools/doip_probe.py | python3 -

Read-only: it sends one identification broadcast and listens. It cannot
change anything in the car.
"""

from __future__ import annotations

import socket
import struct
import subprocess
import sys
import time

DOIP_PORT = 13400
# DoIP header: protocol version 0x02, inverse 0xFD, payload type, length.
IDENT_REQUEST = struct.pack(">BBHI", 0x02, 0xFD, 0x0001, 0)


def show_interfaces() -> None:
    print("=== network interfaces ===", flush=True)
    try:
        out = subprocess.run(["ip", "-brief", "addr"], capture_output=True,
                             text=True, timeout=8).stdout
        print(out.strip() or "(none)", flush=True)
    except Exception as e:
        print(f"could not list interfaces: {e}", flush=True)
    print(flush=True)


def parse_ident_response(data: bytes) -> str:
    if len(data) < 8:
        return f"short reply ({len(data)} bytes)"
    ver, inv, ptype, plen = struct.unpack(">BBHI", data[:8])
    if ptype != 0x0004:
        return f"payload type 0x{ptype:04x} (not an identification response)"
    body = data[8:]
    vin = body[:17].decode("ascii", "replace") if len(body) >= 17 else "?"
    logical = struct.unpack(">H", body[17:19])[0] if len(body) >= 19 else 0
    return f"CAR ANSWERED. VIN={vin} logical_address=0x{logical:04x}"


def broadcast_identify() -> None:
    print("=== DoIP vehicle identification broadcast ===", flush=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(5)
    try:
        s.bind(("", DOIP_PORT))
    except Exception as e:
        print(f"could not bind udp {DOIP_PORT}: {e}", flush=True)
    for target in ("255.255.255.255", "169.254.255.255"):
        try:
            s.sendto(IDENT_REQUEST, (target, DOIP_PORT))
            print(f"sent identification request to {target}", flush=True)
        except Exception as e:
            print(f"send to {target} failed: {e}", flush=True)
    print("listening 5s for a reply...", flush=True)
    end = time.time() + 5
    answered = False
    while time.time() < end:
        try:
            data, addr = s.recvfrom(1024)
        except socket.timeout:
            break
        except Exception as e:
            print(f"recv error: {e}", flush=True)
            break
        print(f"reply from {addr[0]}: {parse_ident_response(data)}", flush=True)
        answered = True
    s.close()
    if not answered:
        print("\nNO REPLY. Likely causes, in order of odds:", flush=True)
        print(" - eth0 has no IP on the car's diagnostic subnet yet "
              "(next step: assign a link-local address)", flush=True)
        print(" - the ENET cable is not in the Pi's ethernet port", flush=True)
        print(" - the car's DoIP gateway needs ignition ON, not just "
              "accessory", flush=True)
    else:
        print("\nDoIP link is ALIVE. Next step is routing activation + a "
              "UDS read. Relay the VIN/address line above to claudeMB.",
              flush=True)


if __name__ == "__main__":
    print("CarWatch DoIP probe - read-only, sends one broadcast\n", flush=True)
    show_interfaces()
    broadcast_identify()
    sys.exit(0)
