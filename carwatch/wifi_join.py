"""Join a wifi network and write the REAL result where the dashboard can
show it. Born Helsinki Aug 15 2026: petrus typed credentials into a page
that echoed nothing and reported nothing; success and failure looked
identical. This helper makes the outcome a fact on disk.

Usage: python3 -m carwatch.wifi_join <ssid> <password>
Result: /tmp/wifi-add-result.json {state, ok, ssid, nmcli}
"""
from __future__ import annotations

import json
import subprocess
import sys

RESULT = "/tmp/wifi-add-result.json"


def write(obj) -> None:
    with open(RESULT, "w") as f:
        json.dump(obj, f)


def main() -> None:
    ssid, psk = sys.argv[1], sys.argv[2]
    write({"state": "working", "ssid": ssid})
    r = subprocess.run(
        ["sudo", "nmcli", "dev", "wifi", "connect", ssid,
         "password", psk, "ifname", "wlan0"],
        capture_output=True, text=True, timeout=90)
    ok = r.returncode == 0
    if ok:
        # Home wifi must outrank the phone hotspot (priority 20) or the Pi
        # clings to the tether parked at home - the Berlin trap.
        subprocess.run(["sudo", "nmcli", "con", "mod", ssid,
                        "connection.autoconnect-priority", "100"],
                       capture_output=True, timeout=30)
    write({"state": "done", "ok": ok, "ssid": ssid,
           "nmcli": (r.stdout + r.stderr).strip()[-300:]})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write({"state": "done", "ok": False, "ssid": sys.argv[1] if len(sys.argv) > 1 else "?",
               "nmcli": f"helper error: {e}"})
