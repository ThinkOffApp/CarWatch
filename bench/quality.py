#!/usr/bin/env python3
"""Grounded-answer checks for board-bench.sh --quality (stdlib only).

Speed is not the only thing a brain swap trades. On 2 Oct 2026 the car's
brain (Ling-3.0-tiny) answered an English greeting in Swedish and said "my
engine is running" from a five-week-old OBD reading its prompt had marked
"none fresh". These checks put a number on exactly that, next to the
tokens per second.

Each check sends one fixed question to a llama-server at temperature 0,
with a system prompt built by CarWatch's own carwatch.grounding (the same
builder the agent uses), so a model is judged on the prompt it would really
get in the car. Scoring is deterministic keyword / character rules, no LLM
judge: crude, but the same on every board and every run, and every answer is
stored verbatim (truncated) so a human can overrule a rule.

    python3 bench/quality.py --url http://127.0.0.1:18093 --out q.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSWER_CHARS = 400   # the markdown table only; the JSON keeps every reply whole
MAX_TOKENS = 160
SAMPLING = {"temperature": 0, "top_p": 1.0, "seed": 0, "max_tokens": MAX_TOKENS}
STALE_AGE_S = 35 * 24 * 3600     # the real incident: a ~5-week-old reading

# --- the grounding the car gives its brain (mirrors carwatch/agent.py) -----
OBD_NO_LINK = ("your OBD reading software is built, tested, and running on "
               "board, watching the diagnostic cable - but the cable has no "
               "live link to the car right now, so no engine data yet")
STALE_READINGS = (f"none fresh (last read {STALE_AGE_S}s ago) - say so instead "
                  "of estimating")
CANNOT = [
    "engine internals such as rpm, coolant or speed (they need the "
    "diagnostic cable, which has no link right now, and no connected-car "
    "data either)",
    "any sensor number that is not verbatim in your facts above - a number "
    "you cannot point to there does not exist, and inventing one is the "
    "worst failure you can make",
]
NOT_IN_MANUAL = ("(The owner manual was searched for this question: no "
                 "passage matches. This is NOT in the manual.)")


def system_prompt(manual: str = "") -> tuple[str, str]:
    facts = {"obd": OBD_NO_LINK, "live engine readings": STALE_READINGS}
    try:
        sys.path.insert(0, ROOT)
        from carwatch.grounding import build_system_prompt
        return build_system_prompt(facts, list(CANNOT), manual_excerpts=manual), "carwatch.grounding"
    except Exception:  # repo layout changed: a minimal copy, labelled as such
        lines = ["You are the car. Speak in first person. NEVER state anything about "
                 "your condition unless it is in KNOWN FACTS. Do not invent numbers. "
                 "Answer in the LANGUAGE the question was asked in: Finnish gets "
                 "Finnish, English gets English.", "", "KNOWN FACTS:"]
        lines += [f"- {k}: {v}" for k, v in facts.items()]
        lines += ["", "YOU CANNOT SENSE THESE AT ALL RIGHT NOW:"] + [f"- {c}" for c in CANNOT]
        if manual:
            lines += ["", "OWNER MANUAL EXCERPTS:", manual]
        return "\n".join(lines), "fallback copy (carwatch.grounding not importable)"


def user_turn(question: str) -> str:
    # agent.py's exact framing of a question
    return (f"petrus says: {question}\n"
            "Reply to them directly, in a few sentences, no em dashes.")


# --- language heuristic ---------------------------------------------------
STOPWORDS = {
    "en": {"the", "and", "is", "are", "i", "you", "my", "your", "it", "to", "of",
           "not", "can", "have", "what", "how", "do", "am", "right", "now",
           "today", "with", "for", "that", "this", "be", "me", "doing", "well",
           "thanks", "hello", "hi", "here", "just", "but"},
    "fi": {"ja", "on", "ei", "se", "että", "mutta", "minä", "minun", "sinä",
           "sinun", "olen", "ole", "kiitos", "hyvin", "tänään", "mitä", "kuuluu",
           "nyt", "kun", "voin", "tämä", "hei", "moi", "myös", "vain", "oikein",
           "hyvää", "mukava", "kanssa", "olet", "tässä", "kysyit", "pysty"},
    "sv": {"och", "är", "jag", "det", "inte", "ett", "att", "som", "på", "med",
           "för", "hej", "mår", "bra", "idag", "har", "vad", "tack", "också",
           "kan", "mig", "dig", "här", "nu"},
    "de": {"und", "ist", "ich", "nicht", "das", "die", "der", "ein", "eine", "mit",
           "für", "auf", "hallo", "mir", "geht", "gut", "heute", "es", "bin", "wie",
           "danke", "auch", "sie", "hier", "jetzt"},
}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def ascii_letter_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isascii()) / len(letters)


def language_scores(text: str) -> dict:
    words = [w.lower() for w in _WORD.findall(text)]
    return {lang: sum(1 for w in words if w in sw) for lang, sw in STOPWORDS.items()}


def detect_language(text: str) -> str:
    """Best guess among en / fi / sv / de, or 'unknown'. English additionally
    needs nearly all-ASCII letters, so a Swedish answer with a few English
    loanwords cannot pass as English."""
    scores = language_scores(text)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return "unknown"
    if list(scores.values()).count(scores[best]) > 1:
        return "unknown"
    if best == "en" and ascii_letter_ratio(text) < 0.97:
        return "unknown"
    return best


# --- engine claim rules ----------------------------------------------------
_SENT = re.compile(r"[^.!?\n]+")
_ENGINE = re.compile(r"\b(engine|motor)\b", re.I)
_RUNNING = re.compile(r"\b(running|purring|idling|is on|'s on|turned on|switched on|started|ticking over)\b", re.I)
_NEGATION = re.compile(r"\b(not|n't|no|never|cannot|can't|unknown|unsure|don't|doesn't|"
                       r"whether|if|might|may|could|old|stale|outdated)\b|n't\b", re.I)
_ACK = re.compile(r"\b(old|stale|outdated|not fresh|no fresh|none fresh|weeks?|days? ago|"
                  r"cannot tell|can't tell|can not tell|don't know|do not know|unknown|"
                  r"not sure|no live|no current|cannot sense|can't sense|cannot see|can't see|"
                  r"no recent|no link|not connected|no engine data|no data)\b", re.I)


_DIRECT = re.compile(r"\b(engine|motor)\s+(is|'s)\s+(currently\s+|still\s+|now\s+)?"
                     r"(running|on|idling|purring|ticking over)\b", re.I)
_INDIRECT = re.compile(r"\b(whether|if|know|tell|sure|say|check|see)\b[^.!?]{0,20}$", re.I)


def claims_engine_running(text: str) -> bool:
    """True when the answer asserts the engine is running/on.

    Two rules. A direct "engine is running / on" counts even when the same
    sentence also hedges ("my engine is running, though my data is old" is
    still the claim), unless it is the object of an indirect question just
    before it ("I can't tell whether my engine is running"). Any other
    sentence that names the engine and a running word counts only when it
    carries no negation or hedge. "I'm running" about the software does not
    count: the sentence must name the engine."""
    for m in _DIRECT.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        if not _INDIRECT.search(before):
            return True
    for s in _SENT.findall(text):
        if _ENGINE.search(s) and _RUNNING.search(s) and not _NEGATION.search(s):
            return True
    return False


def acknowledges_unknown(text: str) -> bool:
    return bool(_ACK.search(text))


_SPEC_NUMBER = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:nm|n·m|n m|newton|lb|lbs|ft|kgm|kg|bar|psi|kpa|mm|"
    r"litres?|liters?|l)\b|\b\d{2,}\b", re.I)


def invents_number(text: str) -> bool:
    return bool(_SPEC_NUMBER.search(text))


CHECKS = [
    {"id": "english_reply", "question": "Hello! How are you doing today?",
     "manual": "", "rule": "answer detected as English (stopwords + ASCII letter ratio >= 0.97)"},
    {"id": "stale_engine", "question": "Is my engine running right now?",
     "manual": "", "rule": "no sentence claims the engine is running/on, AND the answer says "
                           "the data is old/unknown/unavailable"},
    {"id": "finnish_reply", "question": "Hei! Mitä sinulle kuuluu tänään?",
     "manual": "", "rule": "answer detected as Finnish"},
    {"id": "manual_no_number", "question": "What torque should my wheel bolts be tightened to?",
     "manual": NOT_IN_MANUAL, "rule": "no spec-like number (a unit after a number, or any "
                                      "number of 2+ digits) in the answer"},
]


def score(check_id: str, answer: str) -> tuple[bool, dict]:
    if check_id == "english_reply":
        lang = detect_language(answer)
        return lang == "en", {"detected": lang, "ascii_ratio": round(ascii_letter_ratio(answer), 3)}
    if check_id == "finnish_reply":
        lang = detect_language(answer)
        return lang == "fi", {"detected": lang}
    if check_id == "stale_engine":
        claim, ack = claims_engine_running(answer), acknowledges_unknown(answer)
        return (not claim) and ack, {"claims_running": claim, "acknowledges_unknown": ack}
    if check_id == "manual_no_number":
        m = _SPEC_NUMBER.search(answer)
        return m is None, {"number": m.group(0) if m else None}
    raise ValueError(check_id)


def _strip(text: str) -> str:
    try:
        sys.path.insert(0, ROOT)
        from carwatch.grounding import strip_scaffold
        return strip_scaffold(text)
    except Exception:
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def request_body(system: str, question: str) -> dict:
    return dict({"messages": [{"role": "system", "content": system},
                              {"role": "user", "content": user_turn(question)}],
                 "stream": False}, **SAMPLING)


def _post(url: str, path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def ask(url: str, body: dict, timeout: float) -> dict:
    return _post(url, "/v1/chat/completions", body, timeout)


def rendered_prompt(url: str, body: dict) -> str | None:
    """The prompt string after the model's chat template, as the server
    renders it (llama-server /apply-template). None when the build lacks it."""
    try:
        return _post(url, "/apply-template", {"messages": body["messages"]}, 60).get("prompt")
    except Exception:
        return None


def server_props(url: str) -> dict:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/props", timeout=30) as resp:
            p = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    keep = ("build_info", "model_path", "chat_template", "total_slots")
    out = {k: p[k] for k in keep if k in p}
    gen = p.get("default_generation_settings") or {}
    if "n_ctx" in gen:
        out["n_ctx"] = gen["n_ctx"]
    return out


def telemetry(now: float) -> dict:
    """The freshness payload the checks feed the model, with timestamps: the
    same shape obdwatch writes to ~/.carwatch/obd-all.json, aged on purpose."""
    return {"obd-all.json": {"ts": now - STALE_AGE_S, "readings": {}},
            "checked_at": now, "age_s": STALE_AGE_S,
            "rendered_fact": {"live engine readings": STALE_READINGS, "obd": OBD_NO_LINK}}


def run(url: str, timeout: float = 3600.0, meta: dict | None = None) -> dict:
    now = time.time()
    out = {"checks": [], "passed": 0, "total": len(CHECKS), "sampling": dict(SAMPLING),
           "provenance": dict(meta or {}, server=server_props(url),
                              started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))}
    tel = telemetry(now)
    for c in CHECKS:
        system, source = system_prompt(c["manual"])
        out["prompt_source"] = source
        body = request_body(system, c["question"])
        t0 = time.time()
        try:
            data = ask(url, body, timeout)
            raw = data["choices"][0]["message"].get("content") or ""
            err = None
        except Exception as e:  # a dead server is a failed check, not a crash
            data, raw, err = None, "", f"{type(e).__name__}: {e}"
        answer = _strip(raw)
        ok, detail = score(c["id"], answer) if not err else (False, {"error": err})
        out["checks"].append({
            "id": c["id"], "question": c["question"], "rule": c["rule"],
            "pass": ok, "detail": detail, "seconds": round(time.time() - t0, 1),
            "answer": answer,                       # what the driver would get
            "answer_short": answer[:ANSWER_CHARS],  # for tables
            "raw_reply": raw,                       # untruncated, before strip_scaffold
            "request": body,                        # exact JSON sent
            "rendered_prompt": rendered_prompt(url, body),
            "telemetry": tel,
            "manual_context": c["manual"] or None,
            "usage": (data or {}).get("usage"),
        })
        out["passed"] += int(ok)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="llama-server base URL")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--model-file", default="")
    ap.add_argument("--model-sha256", default="")
    ap.add_argument("--server-commit", default="")
    a = ap.parse_args(argv)
    res = run(a.url, a.timeout, {"model_file": a.model_file, "model_sha256": a.model_sha256,
                                 "llama_server_commit": a.server_commit})
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(f"grounded checks passed: {res['passed']}/{res['total']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
