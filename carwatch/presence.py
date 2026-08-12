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
import time
import urllib.request

from carwatch.selfstate import cpu_temp_c, live_facts, serving_model

CONFIG_PATH = os.path.expanduser("~/.carwatch/config.json")
INTERVAL_S = 60
USER_ID = "@petrus"          # the human whose dashboard this feeds
DEVICE_ID = "vadelma"
AGENT_NAME = "gle"


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
    while True:
        facts = live_facts()
        model = serving_model() or "none"
        temp = cpu_temp_c()
        ok_dev = _patch(config, f"/intent/{USER_ID}/{DEVICE_ID}", {
            "kind": "car-pi",
            "model": model,
            "temp_c": temp,
            "memory": facts.get("memory"),
            "network": facts.get("network"),
            "heartbeat": True,
        })
        ok_agent = _patch(config, f"/intent/{USER_ID}/agents/{AGENT_NAME}", {
            "status": "active" if model != "none" else "brainless",
            "last_task": f"serving {model} at {temp} C",
        })
        print(f"published device={ok_dev} agent={ok_agent} "
              f"({model}, {temp} C)", flush=True)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    run()
