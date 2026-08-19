"""Pluggable cloud vehicle-data providers - the "mokkula" (petrus, Aug 19:
"Tän pitäis olla mokkula johon voidaan liittää muiden valmistajien vastaavat
jatkossa").

ONE normalized vehicle state, manufacturer adapters plug in behind it.
Mercedes me is the first provider; a Tesla/BMW/VW equivalent implements the
same three methods and registers itself - nothing else in CarWatch changes.

Read-only BY DESIGN: no provider in this module may expose remote commands
(lock, unlock, climate start). petrus's phone app does those; Vadelma reads.

The normalized state uses ONE vocabulary regardless of vendor (claudemm's
plug-in principle: same names no matter who supplies them):

    {
      "provider":   "mercedes-me",
      "fetched_at": <unix ts>,
      "ok":         bool,
      "error":      "",            # honest failure text when ok is False
      "lock":       {"locked": true},
      "doors":      {"front_left": "closed", "front_right": "closed",
                     "rear_left": "closed", "rear_right": "closed",
                     "trunk": "closed", "hood": "closed"},
      "windows":    {"front_left": "closed", ...},
      "sunroof":    "closed",
      "tires_kpa":  {"front_left": 250, "front_right": 250,
                     "rear_left": 260, "rear_right": 260},
      "ev":         {"soc_pct": 76.5, "range_km": 41,
                     "charging": false, "plugged_in": false},
      "fuel":       {"level_pct": 58, "range_km": 540},
      "odometer_km": 48211,
      "location":   {"lat": ..., "lon": ...},   # only if petrus enables it
      "climate":    {"setpoint_c": 21.0}        # when the vendor exposes it
    }

Providers OMIT keys they cannot read (missing != zero, the same honesty rule
as the OBD dashboard). Every value is a READ from the vendor cloud; nothing
here talks to the car directly.

Auth contract (credential-safe): begin_login(email) makes the vendor send a
one-time code to the OWNER's email; complete_login(code) exchanges it for
tokens the provider persists itself (mode 0600, under ~/.carwatch/). The
owner's password is never entered anywhere in CarWatch.
"""

from __future__ import annotations

import time


class CloudCarProvider:
    """Interface every manufacturer adapter implements. All methods return
    plain dicts (stdlib-JSON-friendly) and never raise to callers - report
    failures inside the dict ({"ok": False, "error": ...})."""

    name = "abstract"
    label = "Abstract provider"

    def auth_state(self) -> dict:
        """{"authenticated": bool, "step": "need_email"|"need_code"|"ready",
            "hint": <one line for the setup UI>}"""
        raise NotImplementedError

    def begin_login(self, email: str) -> dict:
        """Ask the vendor to email a one-time code to `email` (the owner's
        vendor-account address). Returns {"ok": bool, "error": ...}."""
        raise NotImplementedError

    def complete_login(self, code: str) -> dict:
        """Exchange the emailed code for tokens; persist them (0600).
        Returns {"ok": bool, "error": ...}."""
        raise NotImplementedError

    def status(self) -> dict:
        """The normalized vehicle state documented above. Providers should
        serve a short-lived cache and poll the vendor at a respectful
        interval - callers may hit this every few seconds."""
        raise NotImplementedError


_REGISTRY: dict[str, CloudCarProvider] = {}


def register(provider: CloudCarProvider) -> None:
    _REGISTRY[provider.name] = provider


def providers() -> dict[str, CloudCarProvider]:
    return dict(_REGISTRY)


def get(name: str = "") -> CloudCarProvider | None:
    """The named provider, or the only registered one when unnamed."""
    if name:
        return _REGISTRY.get(name)
    if len(_REGISTRY) == 1:
        return next(iter(_REGISTRY.values()))
    return None


def empty_state(provider_name: str, error: str) -> dict:
    """The honest nothing: shape-stable failure state for UIs."""
    return {"provider": provider_name, "fetched_at": time.time(),
            "ok": False, "error": error}
