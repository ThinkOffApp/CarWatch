"""Mercedes me cloud provider - reads the Home Assistant instance that runs
the mbapi2020 integration (the Mini, always on at home) and normalizes both
cars into the mokkula shape from carwatch.cloudcar.

Why via HA and not a direct Mercedes client on the Pi (locked Aug 19 2026):
one cloud client per account or the app-protocol sessions fight, and the
Mercedes password must stay OFF the mobile Pi. The Mini's HA is the single
cloud client; this module is a thin read-only consumer of its REST API.

Auth: an HA *long-lived access token* - a revocable HA key petrus creates in
his HA profile, NOT any Mercedes credential. It arrives through the /cloudcar
setup page's existing code field (complete_login) and persists 0600 under
~/.carwatch/. Read-only stays true: this module calls only GET /api/states,
never a service call, so no command can exist here even by accident.

Entity mapping is BEST-EFFORT by suffix until proven against the live HA:
mbapi2020 names entities by car, and the exact ids differ per install (the
GLE's are VIN-based until it gets a friendly name - never log or commit
those). Unmapped-but-recognized entities are counted, not hidden, so the
dashboard can say honestly how much of the cloud it understood.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from carwatch import cloudcar

_CONFIG = "/etc/carwatch/config.json"
_TOKEN_FILE = os.path.expanduser("~/.carwatch/ha-token")
_DEFAULT_URL = "http://192.168.50.241:8123"  # the Mini's HA, home LAN
_CACHE_S = 25.0  # HA itself polls Mercedes; hitting it harder adds nothing


def _ha_url() -> str:
    env = os.environ.get("CARWATCH_HA_URL")  # bench override (tests/fake_ha.py)
    if env:
        return env.rstrip("/")
    try:
        with open(_CONFIG) as f:
            return (json.load(f).get("ha", {}).get("url") or _DEFAULT_URL).rstrip("/")
    except Exception:
        return _DEFAULT_URL


def _token() -> str:
    try:
        with open(_TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _get(path: str, token: str, timeout: float = 8.0):
    req = urllib.request.Request(
        _ha_url() + path,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# mbapi2020 entity-id suffix -> (group, normalized key). Suffixes are matched
# against the part after the car prefix, longest first, so "tire_pressure_
# front_left" wins over "front_left".
_SUFFIX_MAP = [
    ("lock", ("lock", "locked")),
    ("tirepressure_front_left", ("tires_kpa", "front_left")),
    ("tirepressure_front_right", ("tires_kpa", "front_right")),
    ("tirepressure_rear_left", ("tires_kpa", "rear_left")),
    ("tirepressure_rear_right", ("tires_kpa", "rear_right")),
    ("tire_pressure_front_left", ("tires_kpa", "front_left")),
    ("tire_pressure_front_right", ("tires_kpa", "front_right")),
    ("tire_pressure_rear_left", ("tires_kpa", "rear_left")),
    ("tire_pressure_rear_right", ("tires_kpa", "rear_right")),
    ("window_front_left", ("windows", "front_left")),
    ("window_front_right", ("windows", "front_right")),
    ("window_rear_left", ("windows", "rear_left")),
    ("window_rear_right", ("windows", "rear_right")),
    ("windows_closed", ("windows", "all_closed")),
    ("door_front_left", ("doors", "front_left")),
    ("door_front_right", ("doors", "front_right")),
    ("door_rear_left", ("doors", "rear_left")),
    ("door_rear_right", ("doors", "rear_right")),
    ("doors_closed", ("doors", "all_closed")),
    ("decklidstatus", ("doors", "trunk")),
    ("engine_hood_status", ("doors", "hood")),
    ("sunroof", ("_flat", "sunroof")),
    ("state_of_charge", ("ev", "soc_pct")),
    ("soc", ("ev", "soc_pct")),
    ("range_electric", ("ev", "range_km")),
    ("charging_active", ("ev", "charging")),
    ("charging_power", ("ev", "charging_kw")),
    ("charging", ("ev", "charging")),
    ("range_liquid", ("fuel", "range_km")),
    ("tank_level", ("fuel", "level_pct")),
    ("fuel_level", ("fuel", "level_pct")),
    ("adblue_level", ("fuel", "adblue_pct")),
    ("odometer", ("_flat", "odometer_km")),
    ("ignition_state", ("_flat", "ignition")),
    ("park_brake", ("_flat", "park_brake")),
    ("pre_entry_climate_control", ("_flat", "preclimate")),
]
_SUFFIX_MAP.sort(key=lambda kv: -len(kv[0]))


def _numeric(state: str):
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def _norm_value(entity_id: str, state: str):
    """HA state string -> honest normalized value."""
    if state in ("unknown", "unavailable", "", None):
        return None
    if entity_id.startswith(("binary_sensor.", "lock.")):
        # locked/unlocked, on/off, open/closed pass through as-is - the
        # dashboard renders words, inventing booleans loses "jammed" etc.
        return state
    n = _numeric(state)
    return state if n is None else n


class MercedesMeHA(cloudcar.CloudCarProvider):
    name = "mercedes-me"
    label = "Mercedes me (via Home Assistant)"

    def __init__(self):
        self._cache: dict = {}
        self._cache_at = 0.0

    # -- auth -------------------------------------------------------------
    def auth_state(self) -> dict:
        if not _token():
            return {"authenticated": False, "step": "need_code",
                    "hint": "Home Assistant token"}
        return {"authenticated": True, "step": "ready",
                "hint": f"reading {_ha_url()}"}

    def begin_login(self, email: str) -> dict:
        # No email leg exists on the HA path; the token IS the credential.
        return {"ok": False,
                "error": "this provider wants the HA token in the code field"}

    def complete_login(self, code: str) -> dict:
        token = code.strip()
        if len(token) < 20:
            return {"ok": False, "error": "that does not look like an HA token"}
        try:
            _get("/api/", token)  # probe before persisting
        except Exception as e:
            return {"ok": False, "error": f"HA rejected the token: {e}"}
        os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
        fd = os.open(_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        self._cache_at = 0.0
        return {"ok": True}

    # -- data -------------------------------------------------------------
    def status(self) -> dict:
        now = time.time()
        if self._cache and now - self._cache_at < _CACHE_S:
            return self._cache
        token = _token()
        if not token:
            return cloudcar.empty_state(self.name, "not connected yet")
        try:
            states = _get("/api/states", token)
        except Exception as e:
            return cloudcar.empty_state(self.name, f"HA unreachable: {e}")

        # The suffix map does the recognizing. The first version required an
        # attribution attribute ("mbapi2020 stamps attribution") - that
        # assumption was FALSE: mbapi2020 sets no attribution at all, so all
        # 108 real entities were rejected and the dash said "0 candidates"
        # (claudemm's diagnosis, Aug 26 2026 - he grepped the integration's
        # whole source for the word). A real car produces many matching
        # entities, so a slug with fewer than 3 suffix hits is dropped below
        # rather than letting some other integration's lone lock.front_door
        # masquerade as a vehicle.
        cars: dict[str, dict] = {}
        hits_per_slug: dict[str, int] = {}
        recognized = 0
        for ent in states:
            eid = ent.get("entity_id", "")
            attrs = ent.get("attributes", {}) or {}
            body = eid.split(".", 1)[-1]
            hit = next(((grp, key) for suf, (grp, key) in _SUFFIX_MAP
                        if body.endswith(suf)), None)
            if hit is None:
                continue
            recognized += 1
            # Car slug = what precedes the matched suffix; falls back to the
            # friendly name's first word so both named + VIN cars group.
            grp, key = hit
            suf = next(s for s, gk in _SUFFIX_MAP if gk == hit and body.endswith(s))
            slug = body[: len(body) - len(suf)].strip("_") or \
                str(attrs.get("friendly_name", "car")).split()[0].lower()
            car = cars.setdefault(slug, {"label": slug})
            hits_per_slug[slug] = hits_per_slug.get(slug, 0) + 1
            val = _norm_value(eid, ent.get("state"))
            if val is None:
                continue
            if grp == "_flat":
                car[key] = val
            elif grp == "lock":
                car.setdefault("lock", {})[key] = val
            else:
                car.setdefault(grp, {})[key] = val
            # Label from the slug itself: "isk_579" -> "ISK-579". Deriving it
            # from a friendly name picked whichever entity came last ("ISK-579
            # Charging" as a car title, live, Aug 26) - the slug is stable.
            if car["label"] == slug:
                car["label"] = slug.replace("_", "-").upper()

        # A vehicle shows up as many entities; a lone suffix hit is another
        # integration's coincidence, not a car.
        cars = {slug: car for slug, car in cars.items()
                if hits_per_slug.get(slug, 0) >= 3}

        if not cars:
            self._cache = cloudcar.empty_state(
                self.name,
                f"HA answered but no vehicle matched the entity map "
                f"({recognized} suffix hits) - send me the entity list")
            self._cache_at = now
            return self._cache

        self._cache = {"provider": self.name, "fetched_at": now, "ok": True,
                       "error": "", "cars": cars,
                       "entities_recognized": recognized}
        self._cache_at = now
        return self._cache


cloudcar.register(MercedesMeHA())
