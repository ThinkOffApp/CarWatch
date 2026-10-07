"""Radiation readings for the /radiation page - a RadiaCode 10x, read from
whichever of three sources the config names. CarWatch only READS here: the
statistics (dose conversion, the watch alarm's sigma) belong to radwatch and
are shown as radwatch computed them, never recomputed.

Config block (config.json, see config.example.json):

    "radiation": {
      "source": "json" | "radwatch" | "ha" | "",   empty or absent = off
      "stale_seconds": 60,
      "json":     {"url": ""},
      "radwatch": {"python": "python3", "script": "~/radwatch/radwatch.py",
                   "db": "~/radwatch/radwatch.sqlite"},
      "ha":       {"url": "", "token": "", "entity_prefix": "sensor.radiacode_"}
    }

  json      a bridge on another machine serves one JSON object over plain GET
            (no auth) every few seconds. The recommended source: it needs no
            token and no radwatch on this box.
  radwatch  radwatch runs on THIS box; `radwatch.py status` reads its SQLite
            read-only and prints the latest reading plus the watch verdict.
  ha        Home Assistant REST, GET /api/states/<entity>, with a long-lived
            token. $CARWATCH_RADIATION_HA_URL / $CARWATCH_RADIATION_HA_TOKEN
            beat the config. The token is only ever sent to a private / LAN /
            Tailscale host (the same rule as mercedesme, issue #14).

Five states, and the page must never let two of them look alike (an empty
page for an exception is the bug this module exists to not have):

  unconfigured  no source chosen
  error         source chosen but misconfigured, unreachable or refusing;
                `error` says which
  empty         source answered, holds no reading yet
  stale         a reading exists but is older than stale_seconds, or its age
                cannot be known (an unknown age is never shown as live)
  live          a reading younger than stale_seconds
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

from carwatch.config import load_raw

DEFAULT_STALE_S = 60
_HTTP_TIMEOUT = 4.0   # fail fast: the page polls, a hung source must not hang it
_RADWATCH_TIMEOUT = 10.0

# key, label, unit for the sources that do not report their own units
FIELDS = [
    ("dose_rate", "Dose rate", "µSv/h"),
    ("count_rate", "Count rate", "CPS"),
    ("accumulated_dose", "Accumulated dose", "µSv"),
    ("battery", "Battery", "%"),
    ("temperature", "Temperature", "°C"),
]


NOT_YET = "not yet reported"
UNAVAILABLE = "unavailable"


class SourceError(Exception):
    """The source could not be read. The message is shown on the page."""


def _num(v):
    """A finite float, or None. HA states are strings; "unavailable" and
    "unknown" are not numbers and must not become 0."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _age_from_ts(ts, now: float) -> float | None:
    """Seconds since ts, which may be unix seconds or ISO 8601. A naive ISO
    stamp is read as this box's local time (radwatch's convention); give an
    offset to be unambiguous. None when it does not parse."""
    n = _num(ts)                      # unix seconds, as a number or a numeric string
    if n is not None:
        return round(now - n, 1)
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        t = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return round(now - t.timestamp(), 1)


def _expand(p: str) -> str:
    return os.path.expanduser(str(p or ""))


# ── sources ─────────────────────────────────────────────────────────────
# Each returns a dict: values {key: number|None}, units {key: str}, age_s,
# age_basis, and optionally notes {key: why the value is None} / extra /
# alarm / alarm_note / empty_note. Each raises SourceError for anything the
# owner has to fix or wait out. A field the source has simply not sent yet
# is a note ("not yet reported"), never an error.

def _get_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
        return r.read()


def read_json(c: dict, now: float) -> dict:
    url = str((c.get("json") or {}).get("url") or "").strip()
    if not url:
        raise SourceError("radiation.json.url is not set")
    if not url.startswith(("http://", "https://")):
        raise SourceError("radiation.json.url must start with http:// or https://")
    try:
        raw = _get_json(url)
    except urllib.error.HTTPError as e:
        raise SourceError(f"bridge answered HTTP {e.code}")
    except Exception as e:  # URLError, timeout, refused, DNS
        raise SourceError(f"bridge unreachable: {getattr(e, 'reason', e)}")
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as e:
        raise SourceError(f"bridge returned invalid JSON: {e}")
    if not isinstance(d, dict):
        raise SourceError(f"bridge returned JSON {type(d).__name__}, expected an object")
    known = [k for k, _, _ in FIELDS] + ["ts"]
    if not any(k in d for k in known):
        raise SourceError("bridge JSON has none of the expected fields "
                          "(dose_rate, count_rate, accumulated_dose, battery, temperature, ts)")
    # accumulated_dose / battery / temperature / hardness arrive about once a
    # minute and are ABSENT for the first minute after a bridge restart.
    extra = {k: d[k] for k in ("hardness", "serial", "firmware") if d.get(k) not in (None, "")}
    if "served_at" in d:
        extra["bridge_lag_s"] = _num(_age_from_ts(d.get("ts"), _num(d.get("served_at")) or now))
    notes = {k: NOT_YET if k not in d else UNAVAILABLE
             for k, _, _ in FIELDS if _num(d.get(k)) is None}
    age = _age_from_ts(d.get("ts"), now)
    return {"values": {k: _num(d.get(k)) for k, _, _ in FIELDS}, "notes": notes,
            "units": {}, "extra": extra, "age_s": age,
            "alarm_note": "the bridge does not serve the radwatch watch alarm",
            "age_basis": "bridge ts" if age is not None else
                         ("bridge sent no ts" if d.get("ts") in (None, "") else "bridge ts did not parse")}


def read_radwatch(c: dict, now: float) -> dict:
    rc = c.get("radwatch") or {}
    script = _expand(rc.get("script") or "~/radwatch/radwatch.py")
    db = _expand(rc.get("db") or "~/radwatch/radwatch.sqlite")
    py = _expand(rc.get("python") or "python3")
    if not os.path.isfile(script):
        raise SourceError(f"radwatch not found at {script} (radiation.radwatch.script)")
    try:
        p = subprocess.run([py, script, "status", "--db", db], capture_output=True,
                           text=True, timeout=_RADWATCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SourceError(f"radwatch status took over {_RADWATCH_TIMEOUT:.0f}s")
    except OSError as e:
        raise SourceError(f"cannot run radwatch: {e}")
    try:
        d = json.loads(p.stdout)
    except ValueError:
        # An older radwatch has no `status` subcommand: argparse exits 2 and
        # says so on stderr. Show that, it names the fix.
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise SourceError(f"radwatch status exited {p.returncode}: {tail[0][:200]}")
    if not isinstance(d, dict) or not d.get("ok"):
        raise SourceError(str((d or {}).get("error") or f"radwatch status exited {p.returncode}"))
    r = d.get("reading") or {}
    dev = d.get("device") or {}
    out = {"values": {"dose_rate": _num(r.get("dose_rate_usv_h")),
                      "count_rate": _num(r.get("count_rate_cps")),
                      "accumulated_dose": _num(dev.get("accumulated_dose_usv")),
                      "battery": _num(dev.get("battery_pct")),
                      "temperature": _num(dev.get("temperature_c"))},
           "units": {}, "extra": {}, "age_s": _num(r.get("age_s")),
           "age_basis": "radwatch reading ts",
           "alarm": d.get("watch"), "alarm_note": d.get("watch_note")}
    out["notes"] = {k: (NOT_YET if (k in ("accumulated_dose", "battery", "temperature")
                                    and not dev) or not r else UNAVAILABLE)
                    for k, v in out["values"].items() if v is None}
    if dev:
        out["extra"]["device_age_s"] = _num(dev.get("age_s"))
    elif r:
        out["extra"]["device_note"] = "radwatch has logged no battery/temperature record yet"
    if not r:
        out["empty_note"] = f"radwatch database has no readings yet ({d.get('readings', 0)} rows)"
    return out


def _ha_settings(c: dict) -> tuple[str, str, str]:
    h = c.get("ha") or {}
    url = (os.environ.get("CARWATCH_RADIATION_HA_URL") or h.get("url") or "").strip().rstrip("/")
    token = (os.environ.get("CARWATCH_RADIATION_HA_TOKEN") or h.get("token") or "").strip()
    prefix = (h.get("entity_prefix") or "sensor.radiacode_").strip()
    return url, token, prefix


def read_ha(c: dict, now: float) -> dict:
    from carwatch.mercedesme import _is_private_ha
    url, token, prefix = _ha_settings(c)
    if not url:
        raise SourceError("radiation.ha.url is not set")
    if not token:
        raise SourceError("no Home Assistant token: set radiation.ha.token "
                          "or $CARWATCH_RADIATION_HA_TOKEN")
    if not _is_private_ha(url):
        raise SourceError("refusing to send the HA token to a non-private host "
                          "(LAN, .local or Tailscale only)")
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    states, absent = {}, []
    for key in [k for k, _, _ in FIELDS] + ["last_reading"]:
        eid = prefix + key
        try:
            states[key] = json.loads(_get_json(f"{url}/api/states/{eid}", hdr).decode())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SourceError(f"Home Assistant rejected the token (HTTP {e.code})")
            if e.code == 404:
                absent.append(eid)
                continue
            raise SourceError(f"Home Assistant answered HTTP {e.code} for {eid}")
        except ValueError as e:
            raise SourceError(f"Home Assistant returned invalid JSON for {eid}: {e}")
        except Exception as e:  # first unreachable call ends the poll, no 6x timeout
            raise SourceError(f"Home Assistant unreachable at {url}: {getattr(e, 'reason', e)}")
    if not states:
        raise SourceError(f"none of the {prefix}* entities exist in Home Assistant")
    values = {k: _num((states.get(k) or {}).get("state")) for k, _, _ in FIELDS}
    notes = {k: ("no such entity" if k not in states else
                 str(states[k].get("state") or UNAVAILABLE))
             for k, v in values.items() if v is None}
    units = {k: (states[k].get("attributes") or {}).get("unit_of_measurement")
             for k, _, _ in FIELDS if k in states}
    age, basis = None, "no last_reading sensor and no dose rate timestamp"
    lr = (states.get("last_reading") or {}).get("state")
    if lr not in (None, "", "unknown", "unavailable"):
        age = _age_from_ts(lr, now)
        basis = "last_reading sensor" if age is not None else "last_reading sensor did not parse"
    if age is None and "dose_rate" in states:
        s = states["dose_rate"]
        stamp = s.get("last_reported") or s.get("last_updated")
        age = _age_from_ts(stamp, now) if stamp else None
        if age is not None:
            basis = "HA last_reported of the dose rate entity"
    out = {"values": values, "notes": notes, "units": {k: u for k, u in units.items() if u},
           "extra": {"missing_entities": absent} if absent else {},
           "age_s": age, "age_basis": basis,
           "alarm_note": "the radwatch watch alarm is not available through Home Assistant"}
    if all(v is None for v in values.values()):
        out["empty_note"] = "Home Assistant reports every radiacode sensor unavailable or unknown"
    return out


SOURCES = {"json": read_json, "radwatch": read_radwatch, "ha": read_ha}


# ── classification ──────────────────────────────────────────────────────

def classify(reading: dict, stale_s: float) -> tuple[str, str]:
    """(state, message) for a reading a source returned without error."""
    if all(v is None for v in (reading.get("values") or {}).values()):
        return "empty", reading.get("empty_note") or "source answered, no reading yet"
    age = reading.get("age_s")
    if age is None:
        return "stale", f"reading age unknown ({reading.get('age_basis')}), not claimed live"
    if age < -stale_s:
        return "stale", f"reading is {-age:.0f}s in the future: clock skew between boxes"
    if age > stale_s:
        return "stale", f"last reading {age:.0f}s ago, older than {stale_s:.0f}s"
    return "live", f"last reading {max(age, 0):.0f}s ago"


def _stale_s(c: dict) -> float:
    v = _num(c.get("stale_seconds"))
    if not v or v <= 0:
        return DEFAULT_STALE_S
    return int(v) if v.is_integer() else v


def status(cfg: dict | None = None, now: float | None = None) -> dict:
    """The page's whole payload. Never raises: any exception becomes an
    `error` state carrying the exception text."""
    now = time.time() if now is None else now
    out = {"state": "error", "source": None, "message": "", "error": None,
           "stale_seconds": DEFAULT_STALE_S, "age_s": None, "age_basis": None,
           "values": {}, "extra": {}, "alarm": None, "alarm_note": None,
           "checked_at": round(now, 1)}
    try:
        c = ((load_raw() if cfg is None else cfg).get("radiation") or {})
        if not isinstance(c, dict):
            raise SourceError("radiation in config.json must be an object")
        out["stale_seconds"] = stale_s = _stale_s(c)
        src = str(c.get("source") or "").strip().lower()
        if not src:
            out.update(state="unconfigured",
                       message='no radiation source configured: set radiation.source to '
                               '"json", "radwatch" or "ha" in config.json')
            return out
        out["source"] = src
        reader = SOURCES.get(src)
        if reader is None:
            raise SourceError(f'unknown radiation.source "{src}" (json, radwatch or ha)')
        r = reader(c, now)
        units, notes = r.get("units") or {}, r.get("notes") or {}
        out["values"] = {k: {"label": label, "value": r["values"].get(k),
                             "unit": units.get(k) or unit,
                             "note": None if r["values"].get(k) is not None
                             else notes.get(k, UNAVAILABLE)}
                         for k, label, unit in FIELDS}
        out.update(age_s=r.get("age_s"), age_basis=r.get("age_basis"),
                   extra=r.get("extra") or {}, alarm=r.get("alarm"),
                   alarm_note=r.get("alarm_note"))
        out["state"], out["message"] = classify(r, stale_s)
    except SourceError as e:
        out.update(state="error", error=str(e), message="source error")
    except Exception as e:  # a bug, shown as one, never as an empty page
        out.update(state="error", error=f"{type(e).__name__}: {e}", message="internal error")
    return out
