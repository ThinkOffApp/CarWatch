"""A fake WLED endpoint: proves carwatch.lights end to end before the real
ESP32 strip exists (the fake-gateway discipline from tests/fake_gateway.py).

Stands up a tiny HTTP server that accepts POST /json/state like WLED does,
records the last state it received, and a self-test that drives every mood
through carwatch.lights.Lights and asserts the payload WLED would have got.

    python3 tests/fake_wled.py --self-test
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_last = {"state": None}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        try:
            _last["state"] = json.loads(body)
            code = 200
        except Exception:
            code = 400
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"success":true}')

    def do_GET(self):
        # WLED identity endpoint, used by carwatch.lights.discover()
        if self.path == "/json/info":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"ver": "0.15.0", "brand": "WLED",
                 "leds": {"count": 30}}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass  # quiet


def start() -> str:
    """Start the fake WLED; returns host:port."""
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"127.0.0.1:{srv.server_address[1]}"


def self_test() -> None:
    from carwatch import lights as L

    host = start()
    dev = L.Lights(host, led_count=30, brightness=128)
    ok = True

    for mood, (col, fx, sx, ix, bri) in L.MOODS.items():
        assert dev.mood(mood), f"post failed for {mood}"
        st = _last["state"]
        seg = st["seg"][0]
        exp_col = list(col)
        problems = []
        if st.get("on") is not True:
            problems.append("on!=True")
        if seg.get("fx") != fx:
            problems.append(f"fx {seg.get('fx')}!={fx}")
        if seg.get("sx") != sx:
            problems.append(f"sx {seg.get('sx')}!={sx}")
        if seg.get("col") != [exp_col]:
            problems.append(f"col {seg.get('col')}!={[exp_col]}")
        if problems:
            ok = False
            print(f"MOOD {mood}: {', '.join(problems)}")
        else:
            print(f"mood {mood}: fx={fx} sx={sx} col={exp_col} bri={st.get('bri')} OK")

    # off
    assert dev.off()
    assert _last["state"].get("on") is False, "off did not set on=False"
    print("off: on=False OK")

    # disabled instance is a safe no-op
    noop = L.Lights("")
    assert noop.mood("thinking") is False and noop.enabled is False
    print("disabled (empty host): no-op OK")

    # discovery: the fake answers /json/info like a real WLED
    assert L._is_wled(host), "_is_wled did not recognize the fake"
    print("_is_wled recognizes the fake WLED OK")
    import tempfile
    cache = os.path.join(tempfile.mkdtemp(), "wled-host")
    with open(cache, "w") as f:
        f.write(host)                 # pre-seed cache -> discover returns it
    assert L.discover(cache_path=cache) == host, "discover cache path failed"
    print("discover (cached) returns the fake host OK")

    print("SELF-TEST", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        print(start())
        threading.Event().wait()
