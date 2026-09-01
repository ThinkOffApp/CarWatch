"""Configuration loading - ONE resolver for every module.

Where the car's config.json lives, in order of precedence:

  1. $CARWATCH_CONFIG                       explicit file, wins always,
                                           even when it does not exist yet
  2. $CARWATCH_STATE/config.json           the systemd units set
                                           CARWATCH_STATE=~/.carwatch
  3. ~/.carwatch/config.json               the reference car's location
  4. /etc/carwatch/config.json             legacy; old installs read here

The first existing file wins. When none exists, the path reported (and
written by save_raw) is the highest-precedence candidate, so install.sh,
the dashboard's wifi editor and the agent all agree on ONE file. Before
this resolver existed install.sh seeded /etc/carwatch/config.json while
the agent, listener, dashboard and presence read ~/.carwatch/config.json,
so a fresh clone could never start (issue #23, item 1).

Credentials never live in the repo. See config.example.json for the shape.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field


LEGACY_PATH = "/etc/carwatch/config.json"
DEFAULT_PATH = LEGACY_PATH  # kept for callers that imported the old name


def state_dir() -> str:
    """Runtime state directory (tokens, caches, the config itself)."""
    return os.environ.get("CARWATCH_STATE") or os.path.expanduser("~/.carwatch")


def config_candidates() -> list[str]:
    cands = []
    explicit = os.environ.get("CARWATCH_CONFIG")
    if explicit:
        cands.append(explicit)
    cands.append(os.path.join(state_dir(), "config.json"))
    home = os.path.expanduser("~/.carwatch/config.json")
    if home not in cands:
        cands.append(home)
    cands.append(LEGACY_PATH)
    return cands


def config_path() -> str:
    """The config file every module should use. An explicit $CARWATCH_CONFIG
    is authoritative whether or not it exists (a typo must fail visibly, not
    silently fall back to another car's credentials; codex review on #24).
    Otherwise the first existing candidate, else the highest-precedence one
    (where a new config should be written)."""
    explicit = os.environ.get("CARWATCH_CONFIG")
    if explicit:
        return explicit
    cands = config_candidates()
    for p in cands:
        if os.path.isfile(p):
            return p
    return cands[0]


def load_raw(path: str | None = None) -> dict:
    """The config as a plain dict; {} when missing or unreadable. Daemons
    that need hard failure on a missing key check for it themselves so the
    error names the key AND the file (see agent.run)."""
    try:
        with open(path or config_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_strict(path: str | None = None) -> dict:
    """The config as a dict, or an exception when the file is missing,
    unreadable or not a JSON object. For code that is about to WRITE the
    config back: a transient read error must never turn into an overwrite
    that leaves only the field being edited (codexmb, PR #24 review)."""
    path = path or config_path()
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: config is not a JSON object")
    return data


def update_config(mutate, path: str | None = None) -> dict:
    """Read strictly, apply mutate(cfg) in place, write atomically. Raises
    and leaves the file untouched if the current config cannot be read."""
    path = path or config_path()
    cfg = load_strict(path)
    mutate(cfg)
    save_raw(cfg, path)
    return cfg


def save_raw(cfg: dict, path: str | None = None) -> str:
    """Atomic write of the whole config, 0600, directory created. Returns
    the path written."""
    path = path or config_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def owner_handle(cfg: dict | None = None) -> str:
    """The human the car belongs to, as a bare lowercase handle ("petrus"),
    or "" when the config does not name one (then any human counts)."""
    cfg = cfg if cfg is not None else load_raw()
    return str(cfg.get("owner") or "").strip().lstrip("@").lower()


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
    owner: str = ""
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
        path = path or config_path()
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


def main(argv: list[str] | None = None) -> int:
    """Shell helper so scripts stop hardcoding the path:
         python3 -m carwatch.config path          -> the resolved file
         python3 -m carwatch.config get handle    -> one value (empty if unset)
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "path":
        print(config_path())
        return 0
    if argv[0] == "get" and len(argv) == 2:
        val = load_raw().get(argv[1], "")
        print(val if isinstance(val, str) else json.dumps(val))
        return 0
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
