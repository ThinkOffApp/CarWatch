"""Model registry + on-the-fly brain swap (THI-38).

petrus, 28 Aug: "Can we have model selector in CarWatch to swap models on
the fly? Sounds like a killer feature for devs." The brain unit's comment
has always said model choice is the owner's call - this turns that call
into a deliberate tap instead of an ssh session. The selected model lives
in an EnvironmentFile the unit reads at start (BRAIN_MODEL); swapping =
rewrite that file + restart carwatch-brain. Guard rails: a .gguf that
cannot fit in RAM is refused (a too-big pick would OOM-crashloop the box),
and a swap is refused while an answer is being generated or another model
is still loading - never mid-answer. The selector itself is the rollback:
if a new model misbehaves, tap the old one back.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import urllib.error
import urllib.request

MODEL_DIRS = ["~/models", "~/carwatch-stack/models"]
ENV_FILE = os.path.expanduser("~/.config/carwatch/brain.env")
# Measured llama-bench numbers keyed by .gguf basename: bench runs write it,
# the dash reads it, so the menu says what each model actually costs on THIS
# machine instead of quoting someone else's numbers.
BENCH_FILE = os.path.expanduser("~/.config/carwatch/model-bench.json")
BRAIN_HEALTH = "http://127.0.0.1:8081/health"
# The whole file must be resident to generate at full speed; leave room for
# the OS, whisper, and every other service on the box.
RAM_HEADROOM = int(2.5 * 1024 ** 3)


def _mem_total() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _bench_map() -> dict:
    try:
        with open(BENCH_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def selected_path() -> str | None:
    """What the owner last picked - the RUNNING model can differ (still
    loading, or the restart failed); the dash shows both truthfully."""
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BRAIN_MODEL="):
                    return line.split("=", 1)[1].strip().strip('"') or None
    except Exception:
        pass
    return None


def brain_state() -> str:
    """ready / loading / down - asked from the server itself, never assumed.
    llama-server answers /health 503 while the weights stream in."""
    try:
        with urllib.request.urlopen(BRAIN_HEALTH, timeout=3) as r:
            return "ready" if r.status == 200 else "loading"
    except urllib.error.HTTPError as e:
        return "loading" if e.code == 503 else "down"
    except Exception:
        return "down"


def brain_busy() -> bool:
    """True while an answer is being generated (voicestate's brain lock)."""
    from carwatch import voicestate
    try:
        with open(voicestate.BRAIN_LOCK, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except Exception:
        return False


def list_models() -> list[dict]:
    from carwatch.selfstate import serving_model
    running = serving_model()  # basename of the loaded .gguf, from ps
    selected = selected_path()
    mem = _mem_total()
    bench = _bench_map()
    seen, out = set(), []
    for d in MODEL_DIRS:
        d = os.path.expanduser(d)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            # mmproj = a vision projector, not a model: benching one produced
            # the family-bench night's silent empty section.
            if not fn.endswith(".gguf") or "mmproj" in fn.lower():
                continue
            if fn in seen:
                continue
            seen.add(fn)
            path = os.path.join(d, fn)
            try:
                size = os.stat(path).st_size
            except OSError:
                continue
            out.append({
                "name": fn[:-len(".gguf")],
                "file": fn,
                "path": path,
                "bytes": size,
                "size_gb": round(size / 1e9, 1),
                "fits": bool(mem) and size <= mem - RAM_HEADROOM,
                "bench": bench.get(fn),
                "running": fn == running,
                "selected": path == selected,
            })
    out.sort(key=lambda m: m["bytes"])
    return out


def registry() -> dict:
    models = list_models()
    running = next((m["name"] for m in models if m["running"]), None)
    return {
        "models": models,
        "running": running,
        "state": brain_state(),
        "busy": brain_busy(),
        "ram_gb": round(_mem_total() / 1e9, 1),
    }


def select_model(name: str) -> dict:
    name = (name or "").strip()
    models = list_models()
    pick = next((m for m in models if name in (m["name"], m["file"])), None)
    if not pick:
        return {"ok": False, "error": f"no such model on disk: {name!r}"}
    if pick["running"]:
        return {"ok": False, "error": f"{pick['name']} is already the running brain"}
    if not pick["fits"]:
        return {"ok": False, "error":
                f"{pick['name']} is {pick['size_gb']}GB - too big for this "
                f"machine's {round(_mem_total() / 1e9, 1)}GB RAM with headroom"}
    if brain_busy():
        return {"ok": False,
                "error": "brain is mid-answer - try again when it finishes"}
    if brain_state() == "loading":
        return {"ok": False, "error": "a model is already loading - wait for it"}
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"BRAIN_MODEL={pick['path']}\n")
    os.replace(tmp, ENV_FILE)
    # carwatch-brain is a DIFFERENT unit from the one webchat runs in, so
    # this cannot self-kill; sudo -n so a missing sudoers rule fails loud
    # instead of hanging the request on a password prompt.
    try:
        r = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "carwatch-brain"],
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        return {"ok": False, "error": f"restart failed: {e}"}
    if r.returncode != 0:
        return {"ok": False, "error":
                "restart failed: " + (r.stderr or r.stdout).strip()[:300]}
    return {"ok": True, "loading": pick["name"],
            "note": "old model unloading, new one loading - "
                    "poll /api/models until state=ready"}
