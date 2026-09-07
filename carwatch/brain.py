"""Which brain answers.

A faster box can ride along (the VTA-439 at 18 tok/s next to the Pi's 3.5) or
sit at home on the same LAN, but it is not always there. The endpoint is
therefore resolved per call: a remote brain from CARWATCH_MODEL_URL (or
"brain": {"url": ...} in the config) is used only while its /health answers;
otherwise the Pi's own llama-server takes over, so the car never goes mute
because the fast box stayed in the flat. The decision is cached for a minute
so a health probe is not paid on every question.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

try:
    from carwatch.config import CONFIG_PATH
except Exception:  # pragma: no cover - older checkouts without the resolver
    CONFIG_PATH = os.path.expanduser("~/.carwatch/config.json")

LOCAL_URL = "http://127.0.0.1:8081/v1/chat/completions"
CHECK_EVERY = 60.0
_cache = {"url": None, "until": 0.0}


def remote_url() -> str | None:
    """The configured remote brain, or None. Environment wins over config."""
    env = os.environ.get("CARWATCH_MODEL_URL", "").strip()
    if env:
        return env
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        return None
    brain = cfg.get("brain")
    if isinstance(brain, dict) and brain.get("url"):
        return str(brain["url"]).strip()
    if cfg.get("brain_url"):
        return str(cfg["brain_url"]).strip()
    return None


def health_url(chat_url: str) -> str:
    """http://host:port/v1/chat/completions -> http://host:port/health"""
    base = chat_url.split("/v1/", 1)[0]
    return base.rstrip("/") + "/health"


def _healthy(chat_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(health_url(chat_url), timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def model_url(now: float | None = None) -> str:
    """The chat-completions URL to use right now: the remote brain while it is
    up, the local one otherwise. Re-evaluated every CHECK_EVERY seconds."""
    now = time.time() if now is None else now
    if _cache["url"] and now < _cache["until"]:
        return _cache["url"]
    remote = remote_url()
    if remote and remote != LOCAL_URL and _healthy(remote):
        url = remote
    else:
        url = LOCAL_URL
    _cache["url"], _cache["until"] = url, now + CHECK_EVERY
    return url


def model_ready() -> bool:
    """Is the brain that would answer right now actually up?"""
    return _healthy(model_url(), timeout=4.0)


def reset_cache() -> None:
    _cache["url"], _cache["until"] = None, 0.0
