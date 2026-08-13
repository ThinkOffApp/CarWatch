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
