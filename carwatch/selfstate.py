"""What the car can actually sense about ITSELF, read live.

Until now the KNOWN FACTS block was hand-written by whoever prompted the
model, which meant @gle only knew what a human had just told it. petrus
checking the temperature by hand and reporting "now 44.4" made the gap
obvious: the Pi knows its own temperature, so the car should answer that
itself rather than be informed of it.

Everything here is read from the running system at call time. Anything
unreadable is omitted rather than guessed, so it can only ever narrow what
the model is allowed to assert (see carwatch.grounding).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess


def _run(cmd: list[str], timeout: int = 5) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() or None
    except Exception:
        return None


def cpu_temp_c() -> float | None:
    for path in ("/sys/class/thermal/thermal_zone0/temp",):
        try:
            with open(path) as f:
                return round(int(f.read().strip()) / 1000, 1)
        except Exception:
            pass
    out = _run(["vcgencmd", "measure_temp"])  # temp=44.4'C
    if out and "=" in out:
        try:
            return float(out.split("=")[1].split("'")[0])
        except Exception:
            return None
    return None


def throttling() -> str | None:
    """Decode the live throttle bits - the ACTIVE ones, not 'has occurred'."""
    out = _run(["vcgencmd", "get_throttled"])  # throttled=0x0
    if not out or "=" not in out:
        return None
    try:
        bits = int(out.split("=")[1], 16)
    except Exception:
        return None
    now = []
    if bits & 0x1:
        now.append("under-voltage")
    if bits & 0x2:
        now.append("clock speed capped")
    if bits & 0x4:
        now.append("thermally throttled")
    if bits & 0x8:
        now.append("at the soft temperature limit")
    if now:
        return "RIGHT NOW: " + ", ".join(now)
    if bits:
        return "not throttling now, though it has happened since boot"
    return "not throttling, and has not since boot"


def fan_rpm() -> int | None:
    for path in glob.glob("/sys/class/hwmon/hwmon*/fan1_input"):
        try:
            with open(path) as f:
                return int(f.read().strip())
        except Exception:
            continue
    return None


def uptime_human() -> str | None:
    return _run(["uptime", "-p"])


def memory() -> str | None:
    try:
        total = avail = None
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) / 1048576
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) / 1048576
        if total and avail is not None:
            return f"{total:.0f}GB total, {avail:.1f}GB free"
    except Exception:
        pass
    return None


def disk_free() -> str | None:
    try:
        u = shutil.disk_usage("/")
        return f"{u.free / 1e9:.0f}GB free of {u.total / 1e9:.0f}GB"
    except Exception:
        return None


def serving_model() -> str | None:
    """Which model is ACTUALLY loaded - never assume, always look."""
    out = _run(["ps", "-eo", "args"])
    if not out:
        return None
    for line in out.splitlines():
        if "llama-server" in line and "-m " in line and "grep" not in line:
            for part in line.split():
                if part.endswith(".gguf"):
                    return os.path.basename(part)
    return None


def network() -> str | None:
    out = _run(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "dev", "status"])
    if not out:
        return None
    for line in out.splitlines():
        if line.startswith("wlan0:connected:"):
            return "on wifi " + line.split(":", 2)[2]
        if line.startswith("eth0:connected:"):
            return "on ethernet"
    return "no network"


def live_facts() -> dict[str, str]:
    """Sensor readings the car may legitimately assert about itself."""
    facts: dict[str, str] = {}
    t = cpu_temp_c()
    if t is not None:
        facts["your temperature"] = f"{t} C"
    th = throttling()
    if th:
        facts["throttling"] = th
    rpm = fan_rpm()
    if rpm is not None:
        facts["your fan"] = f"{rpm} rpm" + (" (idle - it only spins when hot)" if rpm == 0 else "")
    for key, val in (
        ("uptime", uptime_human()),
        ("memory", memory()),
        ("storage", disk_free()),
        ("brain", serving_model()),
        ("network", network()),
    ):
        if val:
            facts[key] = val
    return facts


if __name__ == "__main__":
    for k, v in live_facts().items():
        print(f"{k}: {v}")
