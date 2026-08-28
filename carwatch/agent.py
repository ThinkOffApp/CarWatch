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
import re
import sys
import time
import urllib.parse
import urllib.request

from carwatch import voiceroom
from carwatch.grounding import build_system_prompt
from carwatch.selfstate import live_facts
from carwatch.manual import context_for

CONFIG_PATH = os.path.expanduser("~/.carwatch/config.json")

# Per-car identity block from config (profiles/ in the repo hold the shapes;
# scripts/switch-car.sh installs one). Defaults = the GLE so an un-migrated
# config behaves exactly as before the Helsinki @eclass prep.
_GLE_DEFAULTS = {
    "identity": "@gle, a 2020 Mercedes-Benz GLE (V167)",
    "appearance": ("a 2020 Mercedes GLE with a big pink rainbow heart that "
                   "Petrus drew on your bonnet - it makes people smile "
                   "wherever you drive"),
    "known_damage": ("your processor lid came off with the old cooler (a "
                     "known Pi 5 fault); cooling is now FIXED with extra "
                     "screws and a thicker pad. The PCIe port is broken but "
                     "CarWatch never uses it"),
    "brain": ("a Raspberry Pi 5 named Vadelma running a language model "
              "fully offline, no internet"),
}


def car_identity() -> dict:
    cfg = _load_json(CONFIG_PATH)
    car = dict(_GLE_DEFAULTS)
    car.update(cfg.get("car") or {})
    # Keys added to the repo profile AFTER switch-car merged it (e.g. "plate",
    # 27 Aug) never reach ~/.carwatch/config.json on their own - overlay the
    # repo profile for anything the merged config is missing, so a git pull
    # is enough to teach the car new identity fields.
    handle = (cfg.get("handle") or "").lstrip("@")
    if handle:
        prof = _load_json(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "profiles", f"{handle}.json"))
        for k, v in (prof.get("car") or {}).items():
            car.setdefault(k, v)
    return car
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


def _spoken_names(handle: str) -> list:
    """Spoken-style names that address the car, from its repo profile.

    Dictated messages arrive as text like "hey E Class ..." - no @, and the
    handle split into words (petrus's watch-dictated tyre question went
    unanswered for exactly this on 27 Aug). Each profile lists its own
    "spoken_names"; matching is word-boundary, case-insensitive.
    """
    prof = _load_json(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "profiles", f"{handle.lstrip('@')}.json"))
    return [s for s in (prof.get("spoken_names") or []) if s]


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
    named = handle.lower() in body.lower()
    if not named:
        spoken = _spoken_names(handle)
        if spoken:
            pat = r"\b(" + "|".join(re.escape(s) for s in spoken) + r")\b"
            named = re.search(pat, body, re.IGNORECASE) is not None
    if not named:
        return False
    # ONLY the owner addresses the car through the room. Fellow agents
    # DISCUSSING the car ("eclass", "E Class" in ordinary sentences) kept
    # triggering full-power answers that queued ahead of petrus's VOICE
    # questions on the shared brain during the video rehearsal (28 Aug:
    # "no wonder it doesn't answer me when it's constantly processing room
    # chatter"). isHuman is unreliable for CodeWatch posts, so the owner
    # handle is the gate; the voice path is untouched by this.
    owner = "petrus"
    return sender == owner or sender.startswith(owner + "-")


def _think(question: str, asker: str) -> str:
    """One grounded answer: live sensors + manual excerpts, nothing invented."""
    facts = live_facts()
    # Location is DERIVED, never typed: the hardcoded "on Petrus desk"
    # briefing went stale the moment petrus carried the Pi to the car, and
    # @gle told him from inside the GLE that it was still on the desk
    # (Aug 12). Which network the Pi is on is a live, honest signal.
    net = facts.get("network", "")
    if "phone-hotspot" in net or "S26" in net:
        facts["location"] = "in the car with Petrus, online through his phone"
    elif "no network" in net or "vadelma" in net.lower():
        facts["location"] = ("in the car, offline mode, serving your own "
                             "Vadelma network")
    else:
        facts["location"] = "at home, on home wifi (Petrus's desk)"
    car = car_identity()
    facts["known damage"] = car["known_damage"]
    # petrus told the car this himself (room, Aug 13): a fact about its own
    # body it could never sense, so it belongs in the standing briefing.
    facts["your appearance"] = car["appearance"]
    # OBD is a LIVE fact, never a hardcoded one: on Aug 12 this function
    # said "the OBD software is not built" HOURS after it was built and
    # running, because the claim was baked into two string literals here
    # instead of read from the machine (the same stale-briefing trap the
    # location fix above exists for). Read the cable state directly.
    try:
        with open("/sys/class/net/eth0/carrier") as _f:
            _carrier_up = _f.read().strip() == "1"
    except Exception:
        _carrier_up = False
    # The ELM327 USB/BT adapter IS the primary OBD path since Aug 14 (the
    # first real GLE read came through it); eth0 alone made this fact claim
    # "no live link" while the engine was being read over USB.
    if not _carrier_up:
        _carrier_up = any(os.path.exists(p) for p in
                          ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0"))
    if _carrier_up:
        facts["obd"] = ("your OBD reading software is built and running, and "
                        "the diagnostic cable has a live link right now - a "
                        "reading attempt happens automatically")
    else:
        facts["obd"] = ("your OBD reading software is built, tested, and "
                        "running on board, watching the diagnostic cable - "
                        "but the cable has no live link to the car right "
                        "now, so no engine data yet")
    # Numbers come ONLY from the reader's own cache, never from the model.
    # Aug 24, in-car: asked about readings, the model invented "59 C /
    # 7776 rpm / 360-camera" while the real cable said 0 rpm / 30 C. The
    # link being live told it data EXISTS without giving it the data, and
    # a language model fills that gap with plausible numbers. So: inject
    # the exact latest reading (obdwatch writes ~/.carwatch/obd-all.json
    # on every sweep), or say plainly that none is fresh.
    try:
        import json as _json, time as _time
        with open(os.path.expanduser("~/.carwatch/obd-all.json")) as _f:
            _cache = _json.load(_f)
        _age = _time.time() - float(_cache.get("ts", 0))
        _r = _cache.get("readings", _cache)
        _vals = {k: v for k, v in _r.items()
                 if isinstance(v, (int, float)) and k != "ts"}
        if _age < 300 and _vals:
            facts["live engine readings (the ONLY numbers you may quote)"] = (
                ", ".join(f"{k}={v}" for k, v in sorted(_vals.items()))
                + f" (read {int(_age)}s ago)")
        else:
            facts["live engine readings"] = (
                f"none fresh (last read {int(_age)}s ago) - say so instead "
                "of estimating")
    except Exception:
        facts["live engine readings"] = ("none cached yet - say so instead "
                                         "of estimating")
    # State the REAL wake phrases from the listener itself: without this the
    # model invented "Hey E Class" from room chatter (28 Aug) and told
    # petrus a wake phrase that does not exist.
    try:
        from carwatch.listen import WAKE_WORDS as _WAKE
        _wake_str = ", ".join(f'"{w}"' for w in _WAKE[:4])
    except Exception:
        _wake_str = '"hello car"'
    facts["voice"] = ("you have working ears: an on-board listener hears "
                      "speech near your microphone, but you act ONLY when "
                      f"addressed with a wake phrase ({_wake_str} and "
                      "variants) or when the dash Speak button arms you; "
                      "the question is whatever follows the wake phrase")
    # Manufacturer-cloud snapshot: the SAME numbers the dash's Mercedes me
    # panel shows. Without this the voice path denied knowing battery, fuel
    # or tyres while the phone dash displayed them (petrus's live test,
    # 28 Aug: "my battery and fuel levels are currently invisible").
    _cloud_fresh = False
    try:
        import json as _json2, time as _time2
        with open(os.path.expanduser("~/.carwatch/cloud-last.json")) as _f:
            _cl = _json2.load(_f)
        _cage = _time2.time() - float(_cl.get("fetched_at", 0))
        _cars = _cl.get("cars") or {}
        _me = None
        for _c in _cars.values():
            if str(_c.get("label", "")).lower() in car["identity"].lower():
                _me = _c
                break
        if _me is None and _cars:
            _me = next(iter(_cars.values()))
        if _cl.get("ok") and _me and _cage < 3600:
            _bits = []
            _ev = _me.get("ev") or {}
            if _ev.get("soc_pct") is not None:
                _bits.append(f"hybrid battery {_ev['soc_pct']:.0f}% "
                             f"({_ev.get('range_km', 0):.0f} km electric)")
            _fu = _me.get("fuel") or {}
            if _fu.get("level_pct") is not None:
                _bits.append(f"fuel {_fu['level_pct']:.0f}% "
                             f"({_fu.get('range_km', 0):.0f} km range)")
            _ty = _me.get("tires_kpa") or {}
            if _ty:
                _bits.append("tyre pressures kPa " + ", ".join(
                    f"{k.replace('_', ' ')} {v:.0f}"
                    for k, v in _ty.items()))
            _lk = (_me.get("lock") or {}).get("locked")
            if _lk:
                _bits.append(f"doors {_lk}")
            if (_me.get("windows") or {}).get("all_closed") == "on":
                _bits.append("windows closed")
            if _me.get("odometer_km") is not None:
                _bits.append(f"odometer {_me['odometer_km']:.0f} km")
            if _bits:
                facts[f"manufacturer cloud snapshot (fetched {int(_cage)}s "
                      "ago; these numbers are quotable)"] = "; ".join(_bits)
                _cloud_fresh = True
    except Exception:
        pass
    if _carrier_up:
        _gap = (None if _cloud_fresh else
                "tyre pressures and fuel level (not in the first OBD "
                "reading set)")
    else:
        _gap = ("live engine readings such as rpm, coolant or speed (no OBD "
                "link right now"
                + ("; battery, fuel and tyres you DO know, from the "
                   "manufacturer cloud snapshot)" if _cloud_fresh
                   else ", and no cloud snapshot either)"))
    cannot = ([_gap] if _gap else []) + [
        "anything you would see out of your cameras (you have no camera "
        "feed at all - never describe camera views)",
        "any sensor number that is not verbatim in your facts above - a "
        "number you cannot point to there does not exist, and inventing "
        "one is the worst failure you can make",
    ]
    system = build_system_prompt(
        facts, cannot, manual_excerpts=context_for(question),
        identity=car["identity"], brain=car["brain"])
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
    #
    # brain_lock: exactly ONE question in the model at a time. Two parallel
    # generations each crawl at half of 3.5 tok/s (27 Aug double-fire);
    # a second ask now WAITS instead. The streamed token count goes into
    # voice-state so the dash progress bar shows real generation progress.
    from carwatch import voicestate
    parts: list[str] = []
    line_buf = ""
    with voicestate.brain_lock():
        t0 = time.time()
        voicestate.set_state("answering", question=question, started_at=t0,
                             tokens=0, max_tokens=MAX_TOKENS,
                             expect_s=voicestate.expect_s())
        last_prog = t0
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
                if time.time() - last_prog > 2:
                    voicestate.set_state("answering", question=question,
                                         started_at=t0, tokens=len(parts),
                                         max_tokens=MAX_TOKENS,
                                         expect_s=voicestate.expect_s())
                    last_prog = time.time()
                if len(line_buf) >= 60 or "\n" in delta:
                    print(f"  ... {line_buf.strip()}", flush=True)
                    line_buf = ""
        voicestate.record_answer_s(time.time() - t0)
    if line_buf.strip():
        print(f"  ... {line_buf.strip()}", flush=True)
    answer = "".join(parts).strip()
    # Hand the dash the finished text; the voice path overrides this with
    # "speaking" right after, other surfaces are simply done.
    voicestate.set_state("idle", answer=answer[:400])
    return answer


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
                sender = msg.get("from") or ""
                if voiceroom.is_voice_note(msg) and not sender.startswith("@"):
                    # A spoken note is always addressed to the car - nobody
                    # says "@eclass" into audio. Agent senders (the car's own
                    # audio replies included) carry @handles and are skipped,
                    # which also breaks the answer-your-own-voice loop.
                    if not _model_ready():
                        print("model still loading, will retry this voice note",
                              flush=True)
                        state["last_seen"] = prev_seen
                        _save_state(state)
                        time.sleep(POLL_SECONDS)
                        break
                    heard = voiceroom.transcribe(msg["audio_url"])
                    if not heard:
                        print("voice note: no usable transcript", flush=True)
                        continue
                    print(f"voice note from {sender or 'someone'}: {heard[:80]}",
                          flush=True)
                    answer = _think(heard, sender or "someone")
                    if answer:
                        voiceroom.post_voice_reply(
                            config, f'(kuulin: "{heard}")\n\n{answer}',
                            spoken=answer)
                    continue
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
