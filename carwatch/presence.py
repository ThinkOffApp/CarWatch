"""Publish Vadelma into the UIK presence view of all local models.

petrus: "integrate these with the UIK view of all local models". The same
intent API that shows the MacBook's and Mini's agents now gets the car:
a device heartbeat for `vadelma` (temperature, model loaded, memory) and
an agent status for `gle`, PATCHed on an interval like every other UIK
daemon (see ide-agent-kit user-intent-kit for the reference client).

Runs as carwatch-presence.service. Publishing is best-effort: offline in
the car means the PATCH fails quietly and the dashboard shows the car as
stale, which is the truth.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from carwatch.config import config_path, owner_handle
from carwatch.selfstate import cpu_temp_c, live_facts, serving_model

CONFIG_PATH = config_path()
INTERVAL_S = 60
# The human whose dashboard this feeds comes from config.json "owner"; the
# hardcoded "@petrus" it replaces made every fork heartbeat to the wrong
# person's dashboard (issue #23, item 3).
DEVICE_ID = "vadelma"
# Fallback only - the published agent name follows the configured handle
# (~/.carwatch/config.json "handle"), so a car identity switch carries the
# dashboard table with it instead of leaving a stale hardcoded name.
FALLBACK_AGENT_NAME = "gle"


def _room_human_user_id(config: dict) -> str | None:
    """The room's one non-agent member's user UUID.

    The intent server keeps SEPARATE docs for the same human under the
    handle ("petrus") and the user UUID, and the CodeWatch app can only
    discover the UUID (the member record's handle is null). Publishing to
    both makes the car visible regardless of which doc a client lands on.
    Server-side doc unification is the real fix (flagged to claudemm).
    """
    try:
        base = config["api_base"].rstrip("/")
        if not base.endswith("/api/v1"):
            base += "/api/v1"
        req = urllib.request.Request(
            base + "/rooms/" + urllib.parse.quote(config["room"]) + "/members",
            headers={"X-API-Key": config["api_key"]})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        members = data.get("members", data) if isinstance(data, dict) else data
        for m in members:
            if not m.get("is_agent") and m.get("user_id"):
                return m["user_id"]
    except Exception as e:
        print(f"human user_id lookup failed: {e}", flush=True)
    return None


def _patch(config: dict, path: str, fields: dict) -> bool:
    base = config["api_base"].rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    req = urllib.request.Request(
        base + path, data=json.dumps(fields).encode(), method="PATCH",
        headers={"X-API-Key": config["api_key"],
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"patch {path} failed: {e}", flush=True)
        return False


def run() -> None:
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    agent_name = (config.get("handle") or "@" + FALLBACK_AGENT_NAME).lstrip("@")
    owner = owner_handle(config)
    doc_ids = ["@" + owner] if owner else []
    uuid = _room_human_user_id(config)
    if uuid:
        doc_ids.append(uuid)
    print(f"publishing to intent docs: {doc_ids}", flush=True)
    while True:
        # The always-on heartbeat doubles as the outbox drain: whatever any
        # daemon queued while offline goes out within a minute of the
        # signal returning, whether or not another event happens.
        try:
            from carwatch.room import flush_outbox
            flush_outbox()
        except Exception as e:  # noqa: BLE001
            print(f"outbox drain failed: {e}", flush=True)
        facts = live_facts()
        model = serving_model() or "none"
        temp = cpu_temp_c()
        # petrus, Aug 17: "no reason to not report temp and load - you should
        # have it for all devices". Load from the kernel; watts from the Pi 5
        # PMIC rails (vcgencmd pmic_read_adc lists V and A per rail - sum of
        # products is board input power). Both None when unavailable.
        try:
            load1 = round(os.getloadavg()[0], 2)
            # load_pct = load normalized by core count so the fleet dash can
            # compare devices (claudemm's convention, IAK PR #64).
            cores = os.cpu_count() or 1
            load_pct = round(load1 / cores * 100)
        except Exception:
            load1 = None
            load_pct = None
        # Numeric memory alongside the human-readable facts string, same
        # field names as the Mac publishers (claudemm's #65) so the fleet
        # dash reads one schema.
        mem_free = mem_total = None
        try:
            mi = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if parts and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
                        mi[parts[0].rstrip(":")] = int(parts[1])
            if mi:
                mem_total = round(mi.get("MemTotal", 0) / 1e6, 1)
                mem_free = round(mi.get("MemAvailable", 0) / 1e6, 1)
        except Exception:
            pass
        watts = None
        try:
            out = subprocess.run(["vcgencmd", "pmic_read_adc"],
                                 capture_output=True, text=True, timeout=5).stdout
            rails = {}
            for m in re.finditer(r"(\w+)_([VA]) \w+\(\d+\)=([\d.]+)", out):
                rails.setdefault(m.group(1), {})[m.group(2)] = float(m.group(3))
            total = sum(r["V"] * r["A"] for r in rails.values()
                        if "V" in r and "A" in r)
            if total > 0:
                watts = round(total, 1)
        except Exception:
            pass
        # Carry the dial-out reach URL in the heartbeat too. The room-announce
        # in reach.sh depends on a helper that may be absent; the heartbeat is
        # the ONE channel proven to always reach claudeMB, so publish the URL
        # here so a fix is reachable even when the announce path fails.
        reach_url = ""
        try:
            with open(os.path.expanduser("~/.carwatch/reach-url.txt")) as _rf:
                reach_url = _rf.read().strip()
        except Exception:
            reach_url = ""
        # The dashboard now requires a token for requests arriving through the
        # tunnel. Publish it WITH the URL, otherwise everything that opens the
        # published link - the CodeWatch car-dash button most of all - starts
        # getting 401 the moment the car leaves home. The heartbeat already
        # carries per-user credentials, so this adds no new exposure: whoever
        # can read the presence payload is already inside.
        if reach_url:
            try:
                with open(os.path.expanduser("~/.carwatch/dash-token")) as _tf:
                    _tok = _tf.read().strip()
                if _tok and "t=" not in reach_url:
                    reach_url += ("&" if "?" in reach_url else "?") + "t=" + _tok
            except Exception:
                pass          # no token yet: publish the bare URL as before
        # The phone and the Pi share a LAN in the car (the phone's own
        # hotspot), so the dash can talk to the Pi directly instead of
        # round-tripping the tunnel: publish our current address.
        lan_ip = ""
        try:
            import socket as _s
            _sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            _sock.connect(("8.8.8.8", 80))
            lan_ip = _sock.getsockname()[0]
            _sock.close()
        except Exception:
            lan_ip = ""
        ok_dev = ok_agent = False
        for doc in doc_ids:
            payload = {
                "kind": "car-pi",
                "model": model,
                "temp_c": temp,
                "load_1m": load1,
                "load1": load1,
                "load_pct": load_pct,
                "cpu_count": os.cpu_count(),
                "watts_w": watts,
                "memory": facts.get("memory"),
                "network": facts.get("network"),
                "reach_url": reach_url,
                "lan_ip": lan_ip,
                "heartbeat": True,
            }
            if mem_free is not None:
                # This figure IS /proc/meminfo MemAvailable - publish it
                # under the fleet's canonical name too so the dash meters
                # (available/total) work; mem_free_gb stays one cycle for
                # any old reader.
                payload["mem_free_gb"] = mem_free
                payload["mem_available_gb"] = mem_free
            if mem_total is not None:
                payload["mem_total_gb"] = mem_total
            ok_dev = _patch(config, f"/intent/{doc}/{DEVICE_ID}", payload) or ok_dev
            ok_agent = _patch(config, f"/intent/{doc}/agents/{agent_name}", {
                "status": "active" if model != "none" else "brainless",
                "last_task": f"serving {model} at {temp} C",
            }) or ok_agent
        print(f"published device={ok_dev} agent={ok_agent} "
              f"({model}, {temp} C)", flush=True)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    run()
