"""Read the GLE's engine over DoIP (diagnostics over IP), the yellow ENET cable.

This is the thing that should have existed before petrus drove to the car
on Aug 12 and did not. Built here properly, to be TESTED against the real
car before it is ever called ready.

The W167 GLE speaks DoIP: the ENET cable is plain ethernet into the Pi's
eth0, the car's diagnostic gateway is a DoIP server on TCP 13400. The flow
is always the same three steps:

  1. Get an IP on the car's diagnostic subnet (link-local; the tester side
     is usually 169.254.x or a DHCP lease from the gateway).
  2. Vehicle identification (UDP 13400) to learn the car's logical address.
  3. TCP 13400: routing activation, then UDS requests wrapped in DoIP
     diagnostic messages (payload type 0x8001). Standard OBD-II lives under
     UDS service 0x22 (ReadDataByIdentifier) and the classic mode-01 PIDs.

Only ReadDataByIdentifier / mode-01 reads are implemented. Nothing here
writes to the car - CarWatch observes, it never tunes (see the ECU-write
refusal in the room, Aug 11).

Status: message construction and parsing are unit-tested; the live path
(steps 1-3 against the real gateway) is UNVERIFIED until run in the car.
Do not report OBD as working until probe + a real PID read succeed on the
actual GLE.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass

DOIP_PORT = 13400
DOIP_VERSION = 0x02

# DoIP payload types (ISO 13400)
PT_VEHICLE_IDENT_REQ = 0x0001
PT_VEHICLE_IDENT_RES = 0x0004
PT_ROUTING_ACTIVATION_REQ = 0x0005
PT_ROUTING_ACTIVATION_RES = 0x0006
PT_DIAG_MESSAGE = 0x8001
PT_DIAG_MESSAGE_ACK = 0x8002

# Standard addresses: external tester -> vehicle gateway.
SOURCE_ADDR = 0x0E00           # tester logical address (conventional)
DEFAULT_TARGET_ADDR = 0x1000   # gateway; discovered value overrides this


def _doip_frame(payload_type: int, payload: bytes) -> bytes:
    header = struct.pack(">BBHI", DOIP_VERSION, 0xFF & ~DOIP_VERSION,
                         payload_type, len(payload))
    return header + payload


def _parse_doip(data: bytes) -> tuple[int, bytes] | None:
    if len(data) < 8:
        return None
    _ver, _inv, ptype, plen = struct.unpack(">BBHI", data[:8])
    return ptype, data[8:8 + plen]


# ── UDS / OBD-II mode 01 ─────────────────────────────────────────────────
# Mode 01 PID -> (name, decoder). Decoders take the data bytes AFTER the
# echoed [0x41, pid] and return a human value. Only the readings that
# matter for a car agent: is it running, how hot, what voltage, moving?

def _rpm(b: bytes) -> float:
    return ((b[0] << 8) | b[1]) / 4.0


def _coolant(b: bytes) -> int:
    return b[0] - 40


def _speed(b: bytes) -> int:
    return b[0]


def _volts(b: bytes) -> float:
    # Control-module voltage, PID 0x42, in mV pairs.
    return ((b[0] << 8) | b[1]) / 1000.0


PIDS: dict[int, tuple[str, object]] = {
    0x0C: ("engine_rpm", _rpm),
    0x05: ("coolant_c", _coolant),
    0x0D: ("speed_kmh", _speed),
    0x42: ("module_voltage", _volts),
}


def _uds_read_pid(pid: int) -> bytes:
    # UDS mode 01 (OBD-II current data) request: [0x01, pid].
    return bytes([0x01, pid])


def _diag_message(target: int, uds: bytes) -> bytes:
    payload = struct.pack(">HH", SOURCE_ADDR, target) + uds
    return _doip_frame(PT_DIAG_MESSAGE, payload)


def _parse_pid_response(uds: bytes, pid: int):
    # Expect [0x41, pid, data...]. 0x41 = positive response to mode 01.
    if len(uds) < 3 or uds[0] != 0x41 or uds[1] != pid:
        return None
    name, decoder = PIDS[pid]
    try:
        return name, decoder(uds[2:])
    except Exception:
        return None


@dataclass
class DoipLink:
    sock: socket.socket
    target_addr: int


def routing_activation(sock: socket.socket) -> bool:
    # Activation type 0x00 (default). Payload: source addr, type, reserved.
    payload = struct.pack(">HBI", SOURCE_ADDR, 0x00, 0x00000000)
    sock.sendall(_doip_frame(PT_ROUTING_ACTIVATION_REQ, payload))
    sock.settimeout(3)
    parsed = _parse_doip(sock.recv(64))
    if not parsed:
        return False
    ptype, body = parsed
    if ptype != PT_ROUTING_ACTIVATION_RES or len(body) < 5:
        return False
    response_code = body[4]
    return response_code == 0x10  # 0x10 = success


def connect(gateway_ip: str, target_addr: int = DEFAULT_TARGET_ADDR,
            timeout: float = 4.0) -> DoipLink | None:
    """TCP-connect and routing-activate. None on any failure."""
    try:
        s = socket.create_connection((gateway_ip, DOIP_PORT), timeout=timeout)
    except Exception:
        return None
    if not routing_activation(s):
        s.close()
        return None
    return DoipLink(s, target_addr)


def read_pid(link: DoipLink, pid: int):
    link.sock.sendall(_diag_message(link.target_addr, _uds_read_pid(pid)))
    link.sock.settimeout(3)
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            parsed = _parse_doip(link.sock.recv(256))
        except socket.timeout:
            return None
        if not parsed:
            return None
        ptype, body = parsed
        if ptype == PT_DIAG_MESSAGE_ACK:
            continue  # ack precedes the real answer
        if ptype == PT_DIAG_MESSAGE and len(body) >= 4:
            return _parse_pid_response(body[4:], pid)
    return None


def read_all(link: DoipLink) -> dict[str, object]:
    out: dict[str, object] = {}
    for pid in PIDS:
        r = read_pid(link, pid)
        if r:
            out[r[0]] = r[1]
    return out


if __name__ == "__main__":
    import sys
    gw = sys.argv[1] if len(sys.argv) > 1 else "169.254.100.100"
    print(f"connecting to DoIP gateway {gw} ...")
    link = connect(gw)
    if not link:
        print("no DoIP link (gateway unreachable or routing activation "
              "refused). This is expected until run in the car with eth0 up.")
        raise SystemExit(1)
    print("routing activated. reading PIDs...")
    for k, v in read_all(link).items():
        print(f"  {k}: {v}")
