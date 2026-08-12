"""A fake GLE diagnostic gateway: speaks enough real DoIP/UDS to prove the
whole OBD client works BEFORE the Pi ever goes near the car.

This is the missing piece of Aug 12 (petrus drove out three times for
software that had never run end-to-end anywhere). Run this on localhost and
point carwatch.obd_session at it:

    python3 tests/fake_gateway.py --port 13400 &
    python3 -m carwatch.obd_session --gateway 127.0.0.1 --port 13400 --no-eth0

It serves:
  - TCP: routing activation (0x0005 -> 0x0006 success code 0x10), then
    UDS mode-01 reads (0x01 <pid>) answered with realistic engine values
    (idle RPM 800, coolant 92C, speed 0, 14.2V), each preceded by a DoIP
    ACK frame (0x8002) exactly like a real gateway.
  - UDP (same port): vehicle identification requests (0x0001) answered
    with a 0x0004 response carrying a fake VIN + logical address 0x07E0,
    so discovery can be tested on a machine where broadcast reaches self.

Values are intentionally distinctive so a passing test proves DECODING,
not just transport: rpm=800.0, coolant=92, speed=0, volts=14.2.
"""

from __future__ import annotations

import socket
import struct
import sys
import threading

DOIP_VERSION = 0x02
PT_IDENT_REQ = 0x0001
PT_IDENT_RES = 0x0004
PT_RA_REQ = 0x0005
PT_RA_RES = 0x0006
PT_DIAG = 0x8001
PT_DIAG_ACK = 0x8002

GATEWAY_LOGICAL = 0x07E0
VIN = b"FAKEGLE0000000001"

# Mode-01 answers: pid -> data bytes (after the 0x41 <pid> echo).
PID_DATA = {
    0x0C: bytes([0x0C, 0x80]),  # rpm: 0x0C80/4 = 800.0
    0x05: bytes([0x84]),        # coolant: 0x84-40 = 92 C
    0x0D: bytes([0x00]),        # speed: 0 km/h
    0x42: bytes([0x37, 0x78]),  # volts: 0x3778/1000 = 14.2 V
}


def frame(ptype: int, payload: bytes) -> bytes:
    return struct.pack(">BBHI", DOIP_VERSION, 0xFF & ~DOIP_VERSION,
                       ptype, len(payload)) + payload


def parse(data: bytes):
    if len(data) < 8:
        return None
    _v, _i, ptype, plen = struct.unpack(">BBHI", data[:8])
    return ptype, data[8:8 + plen]


def serve_udp(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    while True:
        data, addr = s.recvfrom(1024)
        p = parse(data)
        if p and p[0] == PT_IDENT_REQ:
            body = VIN + struct.pack(">H", GATEWAY_LOGICAL) + b"\x00" * 6
            s.sendto(frame(PT_IDENT_RES, body), addr)


def handle_tcp(conn: socket.socket) -> None:
    with conn:
        while True:
            try:
                data = conn.recv(1024)
            except OSError:
                return
            if not data:
                return
            p = parse(data)
            if not p:
                continue
            ptype, body = p
            if ptype == PT_RA_REQ:
                # source addr echo + our addr + success 0x10 + reserved
                src = body[:2] if len(body) >= 2 else b"\x0e\x00"
                res = src + struct.pack(">H", GATEWAY_LOGICAL) + b"\x10" \
                    + b"\x00" * 4
                conn.sendall(frame(PT_RA_RES, res))
            elif ptype == PT_DIAG and len(body) >= 4:
                src, tgt = struct.unpack(">HH", body[:4])
                uds = body[4:]
                # ACK first, like a real gateway.
                conn.sendall(frame(PT_DIAG_ACK, body[:4] + b"\x00"))
                if len(uds) >= 2 and uds[0] == 0x01 and uds[1] in PID_DATA:
                    answer = bytes([0x41, uds[1]]) + PID_DATA[uds[1]]
                    back = struct.pack(">HH", tgt, src) + answer
                    conn.sendall(frame(PT_DIAG, back))


def main() -> None:
    port = 13400
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    threading.Thread(target=serve_udp, args=(port,), daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(2)
    print(f"fake GLE gateway on 127.0.0.1:{port} (tcp+udp)", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle_tcp, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
