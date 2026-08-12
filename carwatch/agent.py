"""@gle's own ears: an always-on room watcher that runs on the car's Pi.

Until now the car only spoke when a human ran a script by hand - petrus
said "we should have it operate independently", and this is that. The
watcher polls the GroupMind room, notices messages mentioning the car's
handle, runs them through the SAME grounded pipeline every other surface
uses (live sensors + manual excerpts + the grounding rules), and posts
the answer with the car's own key. It lives on the Pi as a systemd
service, so it works with every other machine switched off.

State: ~/.carwatch/agent-state.json remembers the newest message already
handled, so a restart neither replays old questions nor answers its own
posts. On first run it starts from "now" - the car does not wake up and
answer last week's backlog.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

from carwatch.grounding import build_system_prompt
from carwatch.selfstate import live_facts
from carwatch.manual import context_for

CONFIG_PATH = os.path.expanduser("~/.carwatch/config.json")
STATE_PATH = os.path.expanduser("~/.carwatch/agent-state.json")
MODEL_URL = "http://127.0.0.1:8081/v1/chat/completions"
POLL_SECONDS = 20
MAX_TOKENS = 400


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def _api(config: dict, method: str, path: str, body: dict | None = None) -> dict | list:
    base = config["api_base"].rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-API-Key": config["api_key"],
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _fetch_messages(config: dict, limit: int = 20) -> list[dict]:
    room = urllib.parse.quote(config["room"])
    out = _api(config, "GET", f"/rooms/{room}/messages?limit={limit}")
    msgs = out.get("messages", out) if isinstance(out, dict) else out
    return sorted(msgs, key=lambda m: m.get("created_at", ""))


def _post(config: dict, body: str) -> None:
    _api(config, "POST", "/messages", {"room": config["room"], "body": body})


def _mentions_me(msg: dict, handle: str) -> bool:
    """Addressed to the car, not merely about it.

    Any human mentioning the handle is talking to the car. Only
    claudeMB's posts get the stricter leads-with-handle-or-question test,
    because its status reports mention "@gle" in passing and cost the car
    2.5 minutes of full-power thinking per false trigger.
    """
    sender = (msg.get("from") or "").lstrip("@").lower()
    if sender == handle.lstrip("@").lower():
        return False
    body = (msg.get("body") or "").strip()
    if handle.lower() not in body.lower():
        return False
    # petrus's "put the case on temp test time @gle" showed the strict rule
    # blocks real requests: humans do not always lead with the handle or ask
    # a question. So: any mention from a person counts as addressed. The
    # strict test stays ONLY for claudeMB, whose status posts about the car
    # were the original false trigger.
    if sender == "claudemb":
        return body.lower().startswith(handle.lower()) or "?" in body
    return True


def _think(question: str, asker: str) -> str:
    """One grounded answer: live sensors + manual excerpts, nothing invented."""
    facts = live_facts()
    facts["location"] = "on Petrus desk, not installed in the car yet"
    facts["known damage"] = (
        "your processor lid came off with the old cooler (a known Pi 5 fault); "
        "cooling is now FIXED with extra screws and a thicker pad. The PCIe "
        "port is broken but CarWatch never uses it"
    )
    cannot = [
        "anything measured from the car itself - you are not installed in it yet",
        "engine, battery, fuel, tyres",
        "your cameras",
    ]
    system = build_system_prompt(
        facts, cannot, manual_excerpts=context_for(question))
    req = urllib.request.Request(MODEL_URL, data=json.dumps({
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{asker} says: {question}\n"
             "Reply to them directly, in a few sentences, no em dashes."},
        ],
        "max_tokens": MAX_TOKENS,
        "stream": True,
    }).encode(), headers={"Content-Type": "application/json"})
    # Stream so the answer is visible AS it forms (journalctl -fu
    # carwatch-agent) and so faster surfaces (webchat, voice in the car)
    # can speak while the model is still generating, instead of everyone
    # staring at silence for two minutes.
    parts: list[str] = []
    line_buf = ""
    with urllib.request.urlopen(req, timeout=1200) as resp:
        for raw in resp:
            raw = raw.decode("utf-8", "ignore").strip()
            if not raw.startswith("data: ") or raw == "data: [DONE]":
                continue
            try:
                delta = json.loads(raw[6:])["choices"][0]["delta"].get("content") or ""
            except Exception:
                continue
            if not delta:
                continue
            parts.append(delta)
            line_buf += delta
            if len(line_buf) >= 60 or "\n" in delta:
                print(f"  ... {line_buf.strip()}", flush=True)
                line_buf = ""
    if line_buf.strip():
        print(f"  ... {line_buf.strip()}", flush=True)
    return "".join(parts).strip()


def _model_ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def run() -> None:
    config = _load_json(CONFIG_PATH)
    for key in ("api_key", "api_base", "room", "handle"):
        if not config.get(key):
            print(f"config missing {key!r} in {CONFIG_PATH}", file=sys.stderr)
            sys.exit(1)
    handle = config["handle"]

    state = _load_json(STATE_PATH)
    if not state.get("last_seen"):
        # First run: start from now, do not answer the backlog.
        msgs = _fetch_messages(config)
        state["last_seen"] = msgs[-1]["created_at"] if msgs else ""
        _save_state(state)
        print(f"starting fresh from {state['last_seen'] or 'empty room'}", flush=True)

    while True:
        try:
            for msg in _fetch_messages(config):
                if msg.get("created_at", "") <= state["last_seen"]:
                    continue
                prev_seen = state["last_seen"]
                state["last_seen"] = msg["created_at"]
                _save_state(state)
                if not _mentions_me(msg, handle):
                    continue
                asker = msg.get("from") or "someone"
                question = (msg.get("body") or "").strip()
                print(f"answering {asker}: {question[:80]}", flush=True)
                if not _model_ready():
                    # Rewind so the next poll retries this message once the
                    # brain has finished loading (boot races the model load).
                    print("model still loading, will retry this message", flush=True)
                    state["last_seen"] = prev_seen
                    _save_state(state)
                    time.sleep(POLL_SECONDS)
                    break
                started = time.time()
                answer = _think(question, asker)
                if answer:
                    _post(config, answer)
                    print(f"replied in {int(time.time() - started)}s", flush=True)
                else:
                    print("empty answer, not posting", flush=True)
        except Exception as e:  # noqa: BLE001 - a poll cycle must never kill the ears
            print(f"poll error: {e}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
