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

from carwatch.selfstate import cpu_temp_c, live_facts, serving_model

CONFIG_PATH = os.path.expanduser("~/.carwatch/config.json")
INTERVAL_S = 60
USER_ID = "@petrus"          # the human whose dashboard this feeds
DEVICE_ID = "vadelma"
AGENT_NAME = "gle"


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
    doc_ids = [USER_ID]
    uuid = _room_human_user_id(config)
    if uuid:
        doc_ids.append(uuid)
    print(f"publishing to intent docs: {doc_ids}", flush=True)
    while True:
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
        ok_dev = ok_agent = False
        for doc in doc_ids:
            ok_dev = _patch(config, f"/intent/{doc}/{DEVICE_ID}", {
                "kind": "car-pi",
                "model": model,
                "temp_c": temp,
                "load1": load1,
                "load_pct": load_pct,
                "cpu_count": os.cpu_count(),
                "watts_w": watts,
                "memory": facts.get("memory"),
                "network": facts.get("network"),
                "reach_url": reach_url,
                "heartbeat": True,
            }) or ok_dev
            ok_agent = _patch(config, f"/intent/{doc}/agents/{AGENT_NAME}", {
                "status": "active" if model != "none" else "brainless",
                "last_task": f"serving {model} at {temp} C",
            }) or ok_agent
        print(f"published device={ok_dev} agent={ok_agent} "
              f"({model}, {temp} C)", flush=True)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    run()
