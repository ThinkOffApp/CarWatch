#!/usr/bin/env python3
"""Ask Vadelma (the car's local brain) a question. Fully offline.

    ask "what does the tyre pressure light mean"     # uses the GLE manual
    ask --no-manual "tell me a joke"                 # model only

Reads the owner's-manual index when available so answers cite real pages.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"
REPO = "/home/petrus/CarWatch"
os.environ.setdefault("CARWATCH_STATE", "/home/petrus/.carwatch")


def manual_context(q: str) -> str:
    try:
        r = subprocess.run(
            ["python3", "-m", "carwatch.manual", "--ask", q],
            capture_output=True, text=True, cwd=REPO, timeout=30,
        )
        return r.stdout.strip()[:1500]
    except Exception:
        return ""


def main() -> None:
    args = [a for a in sys.argv[1:]]
    use_manual = True
    if "--no-manual" in args:
        use_manual = False
        args.remove("--no-manual")
    q = " ".join(args).strip()
    if not q:
        print('usage: ask "your question"')
        sys.exit(1)

    ctx = manual_context(q) if use_manual else ""
    # One grounding path for every surface: the model may assert only what
    # carwatch.grounding puts in KNOWN FACTS.
    sys.path.insert(0, REPO)
    from carwatch.grounding import build_system_prompt, default_state
    facts, cannot = default_state(engine_on=False, parked=True)
    system = build_system_prompt(facts, cannot, manual_excerpts=ctx)
    prompt = q

    start = time.time()
    req = urllib.request.Request(
        URL,
        data=json.dumps({"messages": [{"role": "system", "content": system},
                                      {"role": "user", "content": prompt}],
                         "max_tokens": 900}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=600))
    except Exception as e:
        print(f"Could not reach the local model on :8080 ({e}).")
        print("Start it with:  ~/start-brain.sh")
        sys.exit(1)

    msg = resp["choices"][0]["message"]
    answer = (msg.get("content") or "").strip()
    if not answer:  # reasoning model spent the budget thinking
        tail = (msg.get("reasoning_content") or "").strip()
        answer = f"[thought too long] ...{tail[-300:]}" if tail else "[no answer]"

    took = int(time.time() - start)
    used = resp.get("usage", {}).get("completion_tokens", "?")
    print(f"\n{answer}\n")
    print(f"({took}s, {used} tokens, offline{', from your GLE manual' if ctx else ''})")


if __name__ == "__main__":
    main()
