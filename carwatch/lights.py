"""@gle's expression lights: drive a WLED (ESP32) strip to show mood/status.

WLED exposes a JSON HTTP API (POST /json/state). This module maps @gle's
moods to WLED segment states (colour + effect + speed) so the car can glow
its state: thinking, listening, speaking, happy, alert, offline.

Design notes:
- Stdlib only (urllib), same as the rest of CarWatch.
- Fail-soft: an empty wled.host disables it, and any network error is
  swallowed with a log line - lights must NEVER break the agent or a trip.
- Proven before it touches real hardware: tests/fake_wled.py stands up a
  fake WLED endpoint and self-checks that every mood produces the right
  payload (the fake-gateway discipline from carwatch.obd).

Usage:
    from carwatch.lights import Lights
    lights = Lights.from_config(cfg)     # no-op if wled.host is empty
    lights.mood("thinking")
    lights.off()
"""

from __future__ import annotations

import json
import urllib.request

# WLED built-in effect IDs (stable across WLED versions).
FX_SOLID = 0
FX_BREATHE = 2
FX_WIPE = 3
FX_RAINBOW = 9
FX_BLINK = 1
FX_CHASE = 28

# mood -> (r,g,b), effect, speed(0-255), intensity(0-255), brightness(0-255)
MOODS = {
    "idle":      ((40, 30, 60),   FX_BREATHE, 40,  128, 60),   # calm dim purple
    "thinking":  ((120, 60, 200), FX_BREATHE, 120, 160, 140),  # pulsing violet
    "listening": ((0, 160, 200),  FX_BREATHE, 180, 200, 160),  # breathing cyan
    "speaking":  ((0, 200, 120),  FX_WIPE,    200, 200, 180),   # green wipe
    "happy":     ((0, 0, 0),      FX_RAINBOW, 150, 200, 200),   # rainbow (col ignored)
    "alert":     ((255, 40, 0),   FX_BLINK,   220, 255, 220),   # red blink = fault/warning
    "offline":   ((80, 20, 20),   FX_SOLID,   0,   0,   40),    # dim red steady
}
DEFAULT_MOOD = "idle"


def _state_payload(mood: str, brightness_override: int | None = None) -> dict:
    col, fx, sx, ix, bri = MOODS.get(mood, MOODS[DEFAULT_MOOD])
    if brightness_override is not None:
        bri = brightness_override
    return {
        "on": True,
        "bri": bri,
        "seg": [{"col": [list(col)], "fx": fx, "sx": sx, "ix": ix}],
    }


class Lights:
    """Thin, fail-soft WLED driver keyed by @gle mood."""

    def __init__(self, host: str, led_count: int = 30, brightness: int = 128,
                 timeout: int = 4):
        self.host = (host or "").strip()
        self.led_count = led_count
        self.brightness = brightness
        self.timeout = timeout
        self.last_mood: str | None = None

    @classmethod
    def from_config(cls, cfg) -> "Lights":
        w = getattr(cfg, "wled", None)
        if w is None:
            return cls("")
        return cls(w.host, w.led_count, w.brightness)

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    def _post(self, payload: dict) -> bool:
        if not self.enabled:
            return False
        url = f"http://{self.host}/json/state"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return 200 <= r.status < 300
        except Exception as e:
            print(f"lights: WLED post failed ({self.host}): {e}", flush=True)
            return False

    def mood(self, mood: str) -> bool:
        """Set the strip to a mood. Unknown moods fall back to idle."""
        if mood not in MOODS:
            mood = DEFAULT_MOOD
        ok = self._post(_state_payload(mood, self.brightness
                                       if mood != "offline" else None))
        if ok:
            self.last_mood = mood
        return ok

    def off(self) -> bool:
        ok = self._post({"on": False})
        if ok:
            self.last_mood = None
        return ok


def _is_wled(host: str, timeout: float = 0.8) -> bool:
    """True if host answers like a WLED device (GET /json/info)."""
    try:
        with urllib.request.urlopen(f"http://{host}/json/info",
                                    timeout=timeout) as r:
            info = json.load(r)
        return "leds" in info and ("ver" in info or "brand" in info)
    except Exception:
        return False


def _local_subnet_hosts() -> list[str]:
    """Addresses on our /24, our own IP excluded. Empty if undetermined."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))       # no traffic sent; just picks the iface
        me = s.getsockname()[0]
        s.close()
    except Exception:
        return []
    base = ".".join(me.split(".")[:3])
    return [f"{base}.{i}" for i in range(1, 255)
            if f"{base}.{i}" != me]


def discover(timeout_per_host: float = 0.5, cache_path: str | None = None) -> str | None:
    """Find a WLED on the local network: cached IP -> mDNS names -> /24 sweep.

    Returns a host string or None. The sweep runs ~32 hosts at a time and the
    result is cached so later startups are instant.
    """
    import concurrent.futures
    import os

    cache_path = cache_path or "/var/lib/carwatch/wled-host"
    # 0) cache
    try:
        with open(cache_path) as f:
            cached = f.read().strip()
        if cached and _is_wled(cached):
            return cached
    except Exception:
        pass
    # 1) mDNS default hostnames (the resolver handles .local via avahi)
    for name in ("wled.local", "wled-gle.local"):
        if _is_wled(name, timeout=1.5):
            host = name
            break
    else:
        # 2) parallel /24 sweep for the WLED signature
        host = None
        hosts = _local_subnet_hosts()
        if hosts:
            with concurrent.futures.ThreadPoolExecutor(max_workers=32) as ex:
                futs = {ex.submit(_is_wled, h, timeout_per_host): h for h in hosts}
                for fut in concurrent.futures.as_completed(futs):
                    if fut.result():
                        host = futs[fut]
                        for f in futs:
                            f.cancel()
                        break
    if host:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w") as f:
                f.write(host)
        except Exception:
            pass
    return host


_shared: "Lights | None" = None


def signal(mood: str) -> None:
    """Fire-and-forget mood set for any caller, loading config lazily.

    Fully fail-soft: if config or WLED is missing, this does nothing. Lets
    listen/agent code say carwatch.lights.signal("thinking") without threading
    a Lights object through every call site. Never raises.
    """
    global _shared
    try:
        if _shared is None:
            from carwatch.config import Config
            _shared = Lights.from_config(Config.load())
            if not _shared.enabled:
                found = discover()          # empty host = find it yourself
                if found:
                    _shared.host = found
        if _shared.enabled:
            _shared.mood(mood)
    except Exception:
        pass  # lights must never break the caller


if __name__ == "__main__":
    # Manual smoke test against a real or fake WLED: cycle the moods.
    import sys
    import time
    host = sys.argv[1] if len(sys.argv) > 1 else ""
    lights = Lights(host)
    if not lights.enabled:
        print("no WLED host given; pass one as argv[1] to demo")
        raise SystemExit(0)
    for m in list(MOODS) + ["off"]:
        print("->", m)
        (lights.off() if m == "off" else lights.mood(m))
        time.sleep(1.5)
