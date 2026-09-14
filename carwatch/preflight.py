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


def _file_age_s(path: str) -> float | None:
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def _json_ts_age_s(path: str) -> float | None:
    """Age of the `ts` field in a JSON snapshot, falling back to mtime."""
    try:
        with open(path) as f:
            d = json.load(f)
        ts = float(d.get("ts", 0)) if isinstance(d, dict) else 0.0
        if ts:
            return time.time() - ts
    except Exception:
        pass
    return _file_age_s(path)


# ---- tiles ----------------------------------------------------------------

def _tile(name: str, ok: bool, detail: str) -> dict:
    return {"tile": name, "ok": bool(ok), "detail": detail}


def check_network() -> dict:
    from carwatch.trips import current_ssid
    ssid = None
    try:
        ssid = current_ssid()
    except Exception:
        pass
    route = _run(["ip", "route", "show", "default"]) or ""
    net = _http_status(INTERNET_PROBE) == 204
    parts = []
    parts.append(f"wifi {ssid}" if ssid else "no wifi association")
    parts.append("default route" if route else "no default route")
    parts.append("internet" if net else "no internet")
    return _tile("network", bool(route) and net, ", ".join(parts))


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
    from carwatch import mercedesme
    missing = []
    ts_ip = _run(["tailscale", "ip", "-4"]) or ""
    on_tailnet = ts_ip.startswith("100.")
    url = mercedesme._ha_url()
    have_token = os.path.exists(mercedesme._TOKEN_FILE)
    if not have_token:
        missing.append("no HA token (ha-token)")
    code = _http_status(url.rstrip("/") + "/api/")
    reachable = code in (200, 401, 403)
    if not reachable:
        missing.append(f"HA {url} unreachable"
                       + ("" if on_tailnet else " and not on the tailnet"))
    age = _json_ts_age_s(os.path.join(state_dir(), "cloud-last.json"))
    if age is None:
        fresh = "no cloud data yet"
    else:
        fresh = f"last cloud read {int(age)}s ago"
        if age > CLOUD_FRESH_S:
            missing.append(fresh + " (stale)")
    ok = not missing
    detail = ", ".join(missing) if missing else f"HA {url} reachable, token present, {fresh}"
    return _tile("mercedes", ok, detail)


def check_obd() -> dict:
    age = _json_ts_age_s(os.path.join(state_dir(), "obd-all.json"))
    if age is None:
        return _tile("obd", False, "no OBD snapshot ever (dongle paired and in the car?)")
    if age > OBD_FRESH_S:
        return _tile("obd", False, f"last OBD snapshot {int(age)}s ago (stale; dongle present?)")
    return _tile("obd", True, f"OBD snapshot {int(age)}s ago")


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
    bad = [t["tile"] for t in tiles if not t["ok"]]
    return {
        "ready": not bad,
        "summary": "READY" if not bad else "READY EXCEPT: " + ", ".join(bad),
        "tiles": tiles,
        "ts": time.time(),
    }


def format_text(result: dict) -> str:
    width = max(len(t["tile"]) for t in result["tiles"]) if result["tiles"] else 8
    lines = [f"{t['tile']:<{width}}  {'READY' if t['ok'] else 'NOT READY'}  {t['detail']}"
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
