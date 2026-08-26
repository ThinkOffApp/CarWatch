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

import ipaddress
import json
import os
import socket
import time
import urllib.parse
import urllib.request

from carwatch import cloudcar


# Hostnames that name a private mesh/LAN rather than the public internet.
_PRIVATE_SUFFIXES = (".local", ".internal", ".ts.net", ".lan", ".home.arpa")


def _is_private_ha(url: str) -> bool:
    """True only if the HA URL points at a private / loopback / Tailscale /
    LAN target. The HA Bearer token is sent to this host, so it must never go
    to an arbitrary public server: a same-LAN attacker who repointed the
    provider (POST /api/cloudcar/ha-url) could otherwise capture the token and
    own the whole Home Assistant remotely (issue #14). Private-only also
    matches the Tailscale remote-access design (docs/remote-access.md);
    public HA exposure is not a supported CarWatch path.

    Fail-closed: anything we can't resolve to a private address is rejected."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    low = host.lower()
    if low == "localhost" or low.endswith(_PRIVATE_SUFFIXES):
        return True
    # Resolve to IP(s); every resolved address must be private/loopback.
    # Tailscale's 100.64.0.0/10 is CGNAT space -> ip_address().is_private is
    # False for it, so allow it explicitly.
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        # A bare literal IP that didn't resolve via DNS still parses below.
        try:
            infos = [(0, 0, 0, "", (host, 0))]
        except Exception:
            return False
    tailscale = ipaddress.ip_network("100.64.0.0/10")
    saw = False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        saw = True
        if not (ip.is_private or ip.is_loopback or ip.is_link_local
                or (ip.version == 4 and ip in tailscale)):
            return False
    return saw

_CONFIG = "/etc/carwatch/config.json"
_TOKEN_FILE = os.path.expanduser("~/.carwatch/ha-token")
_DEFAULT_URL = "http://192.168.50.241:8123"  # the Mini's HA, home LAN
_CACHE_S = 25.0  # HA itself polls Mercedes; hitting it harder adds nothing


_URL_OVERRIDE = os.path.expanduser("~/.carwatch/ha-url")
_LAST_GOOD = os.path.expanduser("~/.carwatch/cloud-last.json")


def _ha_url() -> str:
    # Runtime override wins: lets the HA URL be repointed remotely (POST
    # /api/cloudcar/ha-url) the moment an internet path exists - no SSH, no
    # redeploy - because the LAN URL is useless once the Pi leaves home.
    try:
        with open(_URL_OVERRIDE) as f:
            u = f.read().strip()
            if u:
                return u.rstrip("/")
    except Exception:
        pass
    env = os.environ.get("CARWATCH_HA_URL")  # bench override (tests/fake_ha.py)
    if env:
        return env.rstrip("/")
    try:
        with open(_CONFIG) as f:
            return (json.load(f).get("ha", {}).get("url") or _DEFAULT_URL).rstrip("/")
    except Exception:
        return _DEFAULT_URL


def set_ha_url(url: str) -> dict:
    """Persist a runtime HA URL override (used by the /api/cloudcar/ha-url
    endpoint). Empty string clears it, falling back to config/default. A
    non-empty URL must be a private/Tailscale/LAN target (issue #14) or it is
    refused."""
    url = url.strip()
    if url and not _is_private_ha(url):
        return {"ok": False, "error":
                "refused: HA URL must be a private / Tailscale / LAN address. "
                "Public HA exposure would leak the access token. See "
                "docs/remote-access.md (use Tailscale)."}
    os.makedirs(os.path.dirname(_URL_OVERRIDE), exist_ok=True)
    with open(_URL_OVERRIDE, "w") as f:
        f.write(url)
    return {"ok": True, "ha_url": _ha_url()}


def _token() -> str:
    try:
        with open(_TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _get(path: str, token: str, timeout: float = 8.0):
    # Defense in depth: never send the Bearer token to a non-private target,
    # even if a bad URL reached config/env some other way (issue #14).
    base = _ha_url()
    if not _is_private_ha(base):
        raise ValueError("refusing to send HA token to a non-private host")
    req = urllib.request.Request(
        base + path,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(path: str, token: str, payload: dict, timeout: float = 15.0):
    req = urllib.request.Request(
        _ha_url() + path, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


# The ONLY commands this module can ever send, chosen because they are the
# make-safe direction and need no security PIN (doors_lock verified from
# mbapi2020's own code; windows_close fails safe if Mercedes wants a PIN).
# unlock / open / engine / sunroof are deliberately absent - adding one is a
# petrus-level decision, not a code change to slip in. (Read-only-by-default
# was the Aug 19 rule; petrus reversed it for these two on Aug 26: "make the
# lock doors and windows button work".)
_COMMANDS = {
    "lock": "doors_lock",
    "windows_close": "windows_close",
}


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
        self._vins: dict[str, str] = {}  # slug -> VIN, never serialized

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

    def _unreachable(self, why: str) -> dict:
        """Honest offline state. If we have a last-good snapshot, return THAT
        with an age + stale flag so the car shows real numbers on the road
        instead of a blank, rather than pretending they are live."""
        last = self._cache if self._cache.get("ok") else None
        if last is None:
            try:
                with open(_LAST_GOOD) as f:
                    last = json.load(f)
            except Exception:
                last = None
        if last and last.get("cars"):
            age = int(time.time() - last.get("fetched_at", 0))
            return {**last, "ok": True, "stale": True, "age_s": age,
                    "note": f"last known {age // 60} min ago - {why}"}
        return cloudcar.empty_state(self.name, why)

    # -- data -------------------------------------------------------------
    def status(self) -> dict:
        now = time.time()
        if self._cache and now - self._cache_at < _CACHE_S:
            return self._cache
        token = _token()
        if not token:
            return cloudcar.empty_state(self.name, "not connected yet")
        try:
            # Fail FAST: a 4s ceiling so an unreachable HA (the Pi off its
            # home network) never hangs the dashboard - it drops straight to
            # last-known. The home LAN answers in well under a second.
            states = _get("/api/states", token, timeout=4.0)
        except Exception:
            u = _ha_url()
            homeish = "192.168." in u or "10." in u or ".local" in u
            why = ("Home Assistant not reachable from here. On the road, give "
                   "HA an internet address (Nabu Casa or a tunnel) and set it "
                   "at /cloudcar." if homeish else "Home Assistant unreachable.")
            return self._unreachable(why)

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
            fn = str(attrs.get("friendly_name", ""))
            if fn:
                car.setdefault("_fns", []).append(fn)
            # mbapi2020 stamps every entity with the car's display name and
            # VIN. The name is the best label source; the VIN stays OFF the
            # payload (instance-internal, needed only to address commands).
            if attrs.get("car"):
                car["label"] = str(attrs["car"])
            if attrs.get("vin"):
                self._vins[slug] = str(attrs["vin"])

        # A vehicle shows up as many entities; a lone suffix hit is another
        # integration's coincidence, not a car.
        cars = {slug: car for slug, car in cars.items()
                if hits_per_slug.get(slug, 0) >= 3}
        for slug, car in cars.items():
            car["slug"] = slug  # the dashboard addresses commands by slug

        # Car title = the common prefix of its entities' friendly names
        # ("ISK-579 Odometer" + "ISK-579 Lock" -> "ISK-579"). The slug is the
        # fallback ONLY when it doesn't look like a VIN - a VIN-slugged car
        # (fresh add, no friendly rename) must never put the VIN on a screen.
        import os.path as _osp  # commonprefix works on any str list
        for slug, car in cars.items():
            fns = car.pop("_fns", [])
            if car["label"] != slug:
                continue  # the entities' own "car" attribute already named it
            prefix = _osp.commonprefix(fns).strip(" -_") if len(fns) > 1 else ""
            if len(prefix) >= 3:
                car["label"] = prefix
            elif car["label"] == slug:
                looks_like_vin = len(slug) > 12 and slug.isalnum()
                car["label"] = "unnamed car" if looks_like_vin else \
                    slug.replace("_", "-").upper()

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
        # Persist the good snapshot so the car can show last-known values
        # (with an honest age) when it later can't reach HA, and across a
        # service restart.
        try:
            with open(_LAST_GOOD, "w") as f:
                json.dump(self._cache, f)
        except Exception:
            pass
        return self._cache

    # -- commands ---------------------------------------------------------
    def command(self, slug: str, action: str) -> dict:
        """Send one ALLOWLISTED make-safe command (petrus's Aug 26 decision).
        Everything else about this provider stays read-only; unlock/open can
        not be expressed here at all."""
        service = _COMMANDS.get(action)
        if service is None:
            return {"ok": False,
                    "error": f"'{action}' is not an allowed command "
                             f"(only: {', '.join(sorted(_COMMANDS))})"}
        token = _token()
        if not token:
            return {"ok": False, "error": "no HA token - open /cloudcar"}
        if slug not in self._vins:
            self.status()  # refresh the slug->VIN map
        vin = self._vins.get(slug)
        if not vin:
            return {"ok": False, "error": f"unknown car '{slug}'"}
        try:
            code = _post(f"/api/services/mbapi2020/{service}", token,
                         {"vin": vin})
        except Exception as e:
            # Mercedes-side refusals (e.g. a PIN demand) surface here - report
            # the vendor's words, never pretend the command landed.
            return {"ok": False, "error": f"HA/Mercedes refused: {e}"}
        self._cache_at = 0.0  # next poll shows the real post-command state
        return {"ok": code in (200, 201),
                "sent": action,
                "note": "Mercedes confirms asynchronously - the dashboard "
                        "shows the real state on the next cloud refresh"}


cloudcar.register(MercedesMeHA())
