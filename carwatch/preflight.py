"""Pre-flight: is the car actually ready for a drive, tile by tile.

14 Sep 2026: VTA went to the car after three agents called it ready, each
having looked at a different half. The driver got a dash with no brain, a
Mercedes tile that was never wired, and an OBD dongle left at home. Nothing
on the box could have said so. This can. One line per tile, each READY or
naming exactly what is missing, and a summary that is READY only when
every tile is. The word "ready" should come from here, not from a person.

Every probe is a small module-level function so tests can replace it; the
checks themselves are plain reads of what the other modules already know.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from carwatch.config import state_dir

OBD_FRESH_S = 300.0      # webchat treats an OBD snapshot older than this as stale
CLOUD_FRESH_S = 900.0    # HA polls Mercedes every few minutes; 15 min = something is off
INTERNET_PROBE = "http://connectivitycheck.gstatic.com/generate_204"


# ---- probes (replaced in tests) ------------------------------------------

def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return (r.stdout or "").strip() if r.returncode == 0 else None


def _http_status(url: str, timeout: float = 4.0) -> int | None:
    """HTTP status of a GET, or None if nothing answered. 401/403 count as
    "reachable" for services that want a token; callers decide."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


FUTURE_SLACK_S = 60.0


def _snapshot_age_s(path: str, ts_key: str, valid) -> float | None:
    """Age of a snapshot from ITS OWN timestamp, and only if `valid(d)` says
    the payload is a real reading. A file that exists is not a reading:
    malformed, empty, error payloads and future timestamps all return None
    (codexmb's review of #48). Never falls back to mtime."""
    try:
        with open(path) as f:
            d = json.load(f)
        if not isinstance(d, dict) or not valid(d):
            return None
        ts = float(d.get(ts_key) or 0)
        now = time.time()
        if ts <= 0 or ts > now + FUTURE_SLACK_S:
            return None
        return now - ts
    except Exception:
        return None


def _usable_fields(car: dict) -> int:
    """Fields the dash could show. MercedesMeHA creates a car entry before it
    discards unknown/unavailable values, so a fully unavailable vehicle is
    {"label", "slug"} and nothing else (codexmb's review of #48)."""
    return sum(1 for k, v in car.items()
               if k not in ("label", "slug") and v not in (None, "", {}, []))


def _cloud_valid(d: dict) -> bool:
    if d.get("ok") is not True or d.get("stale") is True:
        return False
    cars = d.get("cars")
    return isinstance(cars, dict) and any(
        isinstance(c, dict) and _usable_fields(c) > 0 for c in cars.values())


def _obd_valid(d: dict) -> bool:
    r = d.get("readings")
    return isinstance(r, dict) and any(isinstance(v, (int, float)) for v in r.values())


def _ha_auth(token: str) -> str:
    """"ok" if HA accepts the Bearer on GET /api/, "rejected" on 401/403,
    "refused" if the URL is not private (mercedesme will not send the token
    there), "unreachable" otherwise. Token existence proves nothing."""
    from carwatch import mercedesme
    try:
        mercedesme._get("/api/", token, timeout=4.0)
        return "ok"
    except urllib.error.HTTPError as e:
        return "rejected" if e.code in (401, 403) else "unreachable"
    except ValueError:
        return "refused"
    except Exception:
        return "unreachable"


# ---- tiles ----------------------------------------------------------------

def _tile(name: str, ok: bool, detail: str, status: str | None = None) -> dict:
    """status: "ready" | "not ready" | "unverified". `ok` is True only for
    ready; "unverified" (a check that cannot be completed before ignition)
    never counts toward global READY but is reported apart from failures."""
    if status is None:
        status = "ready" if ok else "not ready"
    return {"tile": name, "ok": status == "ready", "status": status, "detail": detail}


def check_network() -> dict:
    from carwatch.trips import current_ssid
    ssid = None
    try:
        ssid = current_ssid()
    except Exception:
        pass
    route = _run(["ip", "route", "show", "default"]) or ""
    net = _internet()
    parts = []
    parts.append(f"wifi {ssid}" if ssid else "no wifi association")
    parts.append("default route" if route else "no default route")
    parts.append("internet" if net else "no internet (OBD and the local brain do not need it)")
    # A car with no hotspot still works offline: OBD and the local brain
    # need no internet (claudemm's review of #48). Only the route gates
    # here; the internet answer feeds the mercedes line.
    return _tile("network", bool(route), ", ".join(parts))


def _internet() -> bool:
    return _http_status(INTERNET_PROBE) == 204


def _local_state() -> str:
    """State of carwatch-brain itself. models.local_brain_state exists once
    #46 lands; until then brain_state() on main IS the local probe."""
    from carwatch import models
    fn = getattr(models, "local_brain_state", None) or models.brain_state
    return fn()


def check_brain() -> dict:
    from carwatch import brain
    url = brain.model_url()
    up = brain._healthy(url)
    local = _local_state()
    enabled = _run(["systemctl", "is-enabled", "carwatch-brain"]) or "unknown"
    side = "local" if url == brain.LOCAL_URL else "remote"
    if up:
        detail = f"answering via {side} {url.split('/v1/')[0]}"
        if side == "remote" and local != "ready":
            detail += f"; local unit {local}, {enabled}"
        return _tile("brain", True, detail)
    return _tile("brain", False,
                 f"no brain answers: {side} {url.split('/v1/')[0]} down; "
                 f"local unit {local}, is-enabled {enabled}")


def check_mercedes() -> dict:
    """READY only when the token is ACCEPTED by HA and a real, fresh cloud
    snapshot exists. Reachability plus a token file is not a connection
    (claudemm reproduced READY with token "garbage" + 401 + no data)."""
    from carwatch import mercedesme
    missing = []
    ts_ip = _run(["tailscale", "ip", "-4"]) or ""
    on_tailnet = ts_ip.startswith("100.")
    url = mercedesme._ha_url()
    token = ""
    try:
        with open(mercedesme._TOKEN_FILE) as f:
            token = f.read().strip()
    except OSError:
        pass
    if not token:
        missing.append("no HA token (ha-token)")
        auth = "no token"
    else:
        auth = _ha_auth(token)
        if auth == "rejected":
            missing.append(f"HA {url} rejected the token (401/403)")
        elif auth == "refused":
            missing.append(f"HA URL {url} is not private, token not sent")
        elif auth != "ok":
            why = "" if on_tailnet else " and not on the tailnet"
            if not _internet():
                why += ", no internet"
            missing.append(f"HA {url} unreachable" + why)
    age = _snapshot_age_s(os.path.join(state_dir(), "cloud-last.json"), "fetched_at", _cloud_valid)
    if age is None:
        missing.append("no valid cloud data (cloud-last.json missing, malformed, ok:false, no cars, or bad timestamp)")
        fresh = "no valid cloud data"
    else:
        fresh = f"fetched from HA {int(age)}s ago (retrieval time; the snapshot carries no per-value provider age)"
        if age > CLOUD_FRESH_S:
            missing.append(f"last fetch {int(age)}s ago (stale)")
    ok = not missing
    detail = ", ".join(missing) if missing else f"HA {url} accepted the token, usable vehicle data, {fresh}"
    return _tile("mercedes", ok, detail)


ADAPTER_PATHS = ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0")  # elm327 probes these


def _adapter_present() -> str | None:
    for path in ADAPTER_PATHS:
        if os.path.exists(path):
            return path
    return None


def _obd_mac() -> str:
    try:
        from carwatch.config import load_raw
        return str(load_raw().get("obd_mac") or "").strip()
    except Exception:
        return ""


def _paired(mac: str) -> bool:
    out = _run(["bluetoothctl", "info", mac], timeout=8.0) or ""
    return "Paired: yes" in out


def check_obd() -> dict:
    """Before ignition there is no fresh snapshot and cannot be: the dongle
    sleeps with the car. The pre-drive question is whether a dongle is
    configured and paired/bound at all (claudemm's review of #48); the
    snapshot age is reported, never used to fail the tile."""
    age = _snapshot_age_s(os.path.join(state_dir(), "obd-all.json"), "ts", _obd_valid)
    fresh = ("no valid reading yet" if age is None
             else f"last reading {int(age)}s ago" + ("" if age <= OBD_FRESH_S else " (car off?)"))
    path = _adapter_present()
    if path and path.startswith("/dev/ttyUSB"):
        # A USB adapter node exists only while the adapter is plugged in.
        return _tile("obd", True, f"USB adapter {path} present, {fresh}")
    if path:
        # /dev/rfcomm0 exists once bound, whether or not the dongle is
        # anywhere near the car. Pairing is the same: a promise, not presence
        # (claudemm: paired last week, on the kitchen table today).
        return _tile("obd", False, f"rfcomm bound at {path}; dongle presence unverified until ignition, {fresh}",
                     status="unverified")
    mac = _obd_mac()
    if not mac:
        return _tile("obd", False,
                     f"no OBD dongle configured (obd_mac) and no adapter path; pair with scripts/pair-bt-obd.sh; {fresh}")
    if _paired(mac):
        return _tile("obd", False, f"dongle {mac} paired; presence unverified until ignition, {fresh}",
                     status="unverified")
    return _tile("obd", False, f"dongle {mac} configured but not paired/bound, {fresh}")


def check_presence() -> dict:
    st = _run(["systemctl", "is-active", "carwatch-presence"])
    active = st == "active"
    return _tile("presence", active, "carwatch-presence active" if active
                 else f"carwatch-presence {st or 'unknown'}")


def check_chat() -> dict:
    st = _run(["systemctl", "is-active", "carwatch-chat"])
    active = st == "active"
    return _tile("dash", active, "carwatch-chat active" if active
                 else f"carwatch-chat {st or 'unknown'}")


CHECKS = [check_network, check_chat, check_brain, check_mercedes, check_obd, check_presence]


def run() -> dict:
    tiles = []
    for fn in CHECKS:
        try:
            tiles.append(fn())
        except Exception as e:  # a broken check must never hide the others
            tiles.append(_tile(fn.__name__.replace("check_", ""), False, f"check failed: {e}"))
    bad = [t["tile"] for t in tiles if t["status"] == "not ready"]
    unverified = [t["tile"] for t in tiles if t["status"] == "unverified"]
    if not bad and not unverified:
        summary = "READY"
    else:
        parts = []
        if bad:
            parts.append("READY EXCEPT: " + ", ".join(bad))
        if unverified:
            parts.append(("" if bad else "READY except ") + "unverified until ignition: " + ", ".join(unverified))
        summary = "; ".join(parts)
    return {
        "ready": not bad and not unverified,
        "summary": summary,
        "tiles": tiles,
        "ts": time.time(),
    }


def format_text(result: dict) -> str:
    width = max(len(t["tile"]) for t in result["tiles"]) if result["tiles"] else 8
    lines = [f"{t['tile']:<{width}}  {t['status'].upper():<10} {t['detail']}"
             for t in result["tiles"]]
    return "\n".join(lines + [result["summary"]])


def main(argv: list[str] | None = None) -> int:
    import sys
    argv = sys.argv[1:] if argv is None else argv
    res = run()
    print(json.dumps(res, indent=1) if "--json" in argv else format_text(res))
    return 0 if res["ready"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
