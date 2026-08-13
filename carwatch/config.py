"""Configuration loading.

Credentials live in /etc/carwatch/config.json (or $CARWATCH_CONFIG), never in
the repo. See config.example.json at the repo root for the shape.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


DEFAULT_PATH = "/etc/carwatch/config.json"


@dataclass
class WolfboxConfig:
    ssid: str = ""
    password: str = ""
    # The camera's wifi AP gateway; probe mode discovers the real API paths.
    host: str = "192.168.1.254"
    poll_seconds: int = 60


@dataclass
class WledConfig:
    # WLED LED controller (ESP32) on the car network. Empty host disables the
    # lights entirely; carwatch.lights is a no-op until this is set.
    host: str = ""            # e.g. "192.168.1.50" or "wled-gle.local"
    led_count: int = 30       # strip length, so effects can scale to it
    brightness: int = 128     # 0-255 default brightness


@dataclass
class Config:
    api_base: str = "https://groupmind.one"
    api_key: str = ""
    room: str = ""
    handle: str = "@car"
    home_ssids: list[str] = field(default_factory=list)
    # Seconds without network/motion signals before a trip is considered over.
    trip_idle_seconds: int = 300
    wolfbox: WolfboxConfig = field(default_factory=WolfboxConfig)
    wled: WledConfig = field(default_factory=WledConfig)
    # Free-form overrides for carwatch.voice (paths, prompt, seconds).
    # Kept as a dict on purpose: voice.py owns the defaults and this was
    # silently dropped by the field filter before (codexmb P2).
    voice: dict = field(default_factory=dict)
    state_dir: str = "/var/lib/carwatch"

    @staticmethod
    def load(path: str | None = None) -> "Config":
        path = path or os.environ.get("CARWATCH_CONFIG", DEFAULT_PATH)
        with open(path) as f:
            raw = json.load(f)
        wolf = WolfboxConfig(**raw.pop("wolfbox", {}))
        wled = WledConfig(**raw.pop("wled", {}))
        cfg = Config(**{k: v for k, v in raw.items()
                        if k in Config.__dataclass_fields__ and k not in ("wolfbox", "wled")})
        cfg.wolfbox = wolf
        cfg.wled = wled
        if not cfg.api_key:
            raise SystemExit(f"api_key missing in {path}")
        if not cfg.room:
            raise SystemExit(f"room missing in {path}")
        return cfg
