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
    """Truthful-to-a-human memory line.

    MemAvailable counts page cache as free, and on this machine the cache
    IS the memory-mapped model - petrus read "11.5GB free" on the dash and
    reasonably concluded Qwen was not loaded. Technically correct, humanly
    wrong. Report what actually matters: the model lives in cache, and the
    truly free figure is MemFree.
    """
    try:
        total = free = avail = None
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) / 1048576
                elif line.startswith("MemFree:"):
                    free = int(line.split()[1]) / 1048576
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) / 1048576
        if total and free is not None:
            if serving_model():
                return (f"{total:.0f}GB total, model held in memory, "
                        f"{free:.1f}GB truly free")
            return f"{total:.0f}GB total, {avail or free:.1f}GB free"
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


def manual_status() -> str | None:
    """Whether the owner's manual is indexed on this machine, and which one.

    petrus asked @gle "do you have the manual now indexed" and it said no
    while 1549 chunks of the European manual sat on its own disk - the
    index existed but was not part of the car's self-knowledge, so the
    grounding rules made it (correctly, given its facts) deny it. Facts
    about what the car has must come from looking, like everything else.
    """
    try:
        import json as _json

        from carwatch.manual import INDEX_PATH
        with open(INDEX_PATH) as f:
            idx = _json.load(f)
        n = len(idx.get("chunks", []))
        src = idx.get("source", "owner's manual")
        if n:
            return (f"{src} indexed on your disk, {n} sections; relevant "
                    "excerpts are searched and given to you per question")
    except Exception:
        pass
    return None


def live_facts() -> dict[str, str]:
    """Sensor readings the car may legitimately assert about itself."""
    facts: dict[str, str] = {}
    t = cpu_temp_c()
    if t is not None:
        facts["your temperature"] = f"{t} C"
    try:
        facts["load"] = f"{os.getloadavg()[0]:.2f}"
    except Exception:
        pass
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
        ("your manual", manual_status()),
    ):
        if val:
            facts[key] = val
    return facts


if __name__ == "__main__":
    for k, v in live_facts().items():
        print(f"{k}: {v}")


# The car's own engine, when the DoIP link is up. Gated behind a config
# flag + a fast connect so it costs nothing when the cable is not present
# or the reader is not proven yet. Being unable to read the car is the
# normal state until OBD is verified on the real GLE - it simply omits
# the facts, exactly like every other unsensable thing.
def car_facts() -> dict:
    """Live engine readings over DoIP, or {} if no link. Never blocks long."""
    import json as _json
    try:
        cfg_path = os.path.expanduser("~/.carwatch/config.json")
        with open(cfg_path) as f:
            cfg = _json.load(f)
    except Exception:
        return {}
    obd_cfg = (cfg.get("obd") or {})
    if not obd_cfg.get("enabled"):
        return {}
    gateway = obd_cfg.get("gateway_ip")
    if not gateway:
        return {}
    try:
        from carwatch import obd
        link = obd.connect(gateway, timeout=2.0)
        if not link:
            return {}
        readings = obd.read_all(link)
        try:
            link.sock.close()
        except Exception:
            pass
    except Exception:
        return {}
    facts = {}
    if "engine_rpm" in readings:
        rpm = readings["engine_rpm"]
        facts["engine"] = (f"running at {rpm:.0f} rpm" if rpm > 0
                           else "off (0 rpm)")
    if "coolant_c" in readings:
        facts["coolant"] = f"{readings['coolant_c']} C"
    if "speed_kmh" in readings:
        facts["speed"] = f"{readings['speed_kmh']} km/h"
    if "module_voltage" in readings:
        facts["car 12V battery"] = f"{readings['module_voltage']:.1f} V"
    return facts
