"""Owner's-manual RAG, fully local (petrus: "load your cars PDF to
raspberry"). Drop the car's PDF manual on the Pi, ingest once, and the
voice assistant answers warning-light and how-do-I questions offline,
citing pages.

No embeddings, no dependencies: the manual is chunked (with page numbers)
and searched with plain lexical scoring, which is a strong fit for manual
lookups where the user's words ("coolant", "AdBlue", "tyre pressure")
literally appear in the text. PDF extraction shells out to `pdftotext`
(poppler-utils, installed by bench.sh); .txt files ingest directly.

Usage:
    python3 -m carwatch.manual --ingest /home/pi/gle-manual.pdf
    python3 -m carwatch.manual --ask "what does the yellow coolant light mean"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

def _index_dir() -> str:
    """CARWATCH_STATE wins; else /var/lib/carwatch when writable (the
    systemd service's StateDirectory); else a user-writable fallback so
    a plain `python3 -m carwatch.manual --ingest` just works (codexmb:
    /var/lib/carwatch belongs to the carwatch system user)."""
    env = os.environ.get("CARWATCH_STATE")
    if env:
        return env
    system = "/var/lib/carwatch"
    if os.path.isdir(system) and os.access(system, os.W_OK):
        return system
    return os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
        "carwatch",
    )


INDEX_DIR = _index_dir()
INDEX_PATH = os.path.join(INDEX_DIR, "manual-index.json")

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
STOPWORDS = frozenset(
    "the a an and or of to in on for with is are be it its this that you your "
    "if as at by from can may will when what which how do does "
    # German manuals are common in this house
    "der die das und oder von zu im in auf mit ist sind sie ihr ihre wenn "
    "als bei aus kann wird wann was wie".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zäöüß0-9]+", text.lower()) if t not in STOPWORDS]


def _pdf_pages(path: str) -> list[str]:
    """Page texts via pdftotext (poppler). Page breaks come out as \\f."""
    out = subprocess.run(
        ["pdftotext", "-layout", path, "-"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.split("\f")


def ingest(path: str) -> dict:
    if path.lower().endswith(".pdf"):
        pages = _pdf_pages(path)
    else:
        pages = [open(path, encoding="utf-8", errors="ignore").read()]
    chunks = []
    for pageno, page in enumerate(pages, start=1):
        text = re.sub(r"\s+", " ", page).strip()
        i = 0
        while i < len(text):
            piece = text[i:i + CHUNK_CHARS]
            if len(piece) > 50:
                chunks.append({"page": pageno, "text": piece})
            i += CHUNK_CHARS - CHUNK_OVERLAP
    index = {"source": os.path.basename(path), "chunks": chunks}
    os.makedirs(INDEX_DIR, exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f)
    os.replace(tmp, INDEX_PATH)
    return index


_cache: dict = {"mtime": None, "index": None, "chunk_tokens": None, "df": None}


def _load_index():
    """Index + per-chunk token sets, cached by file mtime so repeated
    voice queries do not re-tokenize a big manual every time (kimi3)."""
    try:
        mtime = os.path.getmtime(INDEX_PATH)
    except Exception:
        return None
    if _cache["mtime"] != mtime:
        with open(INDEX_PATH) as f:
            index = json.load(f)
        chunk_tokens = [set(_tokens(c["text"])) for c in index["chunks"]]
        df: dict[str, int] = {}
        for toks in chunk_tokens:
            for t in toks:
                df[t] = df.get(t, 0) + 1
        _cache.update(mtime=mtime, index=index, chunk_tokens=chunk_tokens, df=df)
    return _cache


# Bridging what people SAY to what the manual PRINTS.
#
# petrus asked @gle about the "230V power source"; the search returned
# nothing because his GLE has a 115V socket and the string "230V" appears
# nowhere in the book. The lookup was literal, so a question phrased in
# ordinary words missed a section that was right there on page 121.
#
# Each key maps a term a person might use to the words the manual actually
# prints. Expansion only ADDS search terms, so a query that already matched
# still matches - it can only widen recall, never narrow it.
ALIASES: dict[str, tuple[str, ...]] = {
    # mains sockets: people say any voltage, Mercedes prints one of them
    "230v": ("115", "socket", "power"),
    "240v": ("115", "socket", "power"),
    "220v": ("115", "socket", "power"),
    "115v": ("115", "socket"),
    "mains": ("115", "socket", "power"),
    "plug": ("socket",),
    "outlet": ("socket",),
    "inverter": ("115", "socket"),
    # everyday words vs manual words
    "boot": ("cargo", "compartment"),
    "trunk": ("cargo", "compartment"),
    "bonnet": ("engine", "hood"),
    "hood": ("engine", "hood"),
    "petrol": ("fuel",),
    "gas": ("fuel",),
    "diesel": ("fuel",),
    "windscreen": ("windshield",),
    "tyre": ("tire",),
    "tyres": ("tire",),
    "aircon": ("air", "conditioning", "climate"),
    "ac": ("air", "conditioning", "climate"),
    "sat": ("navigation",),
    "satnav": ("navigation",),
    "cruise": ("cruise", "control"),
    "handbrake": ("parking", "brake"),
    "wipers": ("windshield", "wiper"),
    "indicator": ("turn", "signal"),
    "blinker": ("turn", "signal"),
    "reversing": ("reverse", "rear", "view"),
    "battery": ("battery", "starter"),
    "charging": ("charge", "charging"),
    "warning": ("warning", "indicator", "lamp"),
    "light": ("lamp", "light"),
}


def _expand(tokens: list[str]) -> list[str]:
    """Add the manual's own vocabulary to the user's words."""
    out = list(tokens)
    for t in tokens:
        for extra in ALIASES.get(t, ()):
            if extra not in out:
                out.append(extra)
    return out


def search(query: str, top: int = 3) -> list[dict]:
    """Top chunks by lexical score: presence of query terms weighted by
    rarity across chunks (set membership, no term frequency - chunks are
    uniform-sized so tf adds little)."""
    cache = _load_index()
    if cache is None:
        return []
    q = _expand(_tokens(query))
    if not q:
        return []
    index, chunk_tokens, df = cache["index"], cache["chunk_tokens"], cache["df"]
    n = max(len(chunk_tokens), 1)
    scored = []
    for c, toks in zip(index["chunks"], chunk_tokens):
        score = sum((1.0 / (1 + df.get(t, 0) / n)) for t in q if t in toks)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top]]


# Default sized for the Pi: ~300 tokens of context. 2400 chars cost
# 12-30s of extra prefill on a Pi-5-class 2B model (kimi3 measured
# estimate); faster hosts can pass a bigger budget.
def context_for(query: str, budget_chars: int = 1200) -> str:
    """Prompt context block for the voice loop, with page citations."""
    hits = search(query)
    if not hits:
        return ""
    parts, used = [], 0
    for h in hits:
        piece = f"[manual p.{h['page']}] {h['text']}"
        if used + len(piece) > budget_chars:
            piece = piece[: budget_chars - used]
        parts.append(piece)
        used += len(piece)
        if used >= budget_chars:
            break
    return (
        "Relevant excerpts from this car's owner's manual (cite the page "
        "when you use one):\n" + "\n---\n".join(parts)
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Owner's-manual RAG")
    ap.add_argument("--ingest", metavar="PDF_OR_TXT")
    ap.add_argument("--ask", metavar="QUESTION")
    args = ap.parse_args()
    if args.ingest:
        idx = ingest(args.ingest)
        print(f"Indexed {idx['source']}: {len(idx['chunks'])} chunks -> {INDEX_PATH}")
    elif args.ask:
        hits = search(args.ask)
        if not hits:
            print("No manual indexed yet, or no match. Ingest first.")
            sys.exit(1)
        for h in hits:
            print(f"— p.{h['page']}: {h['text'][:240]}…\n")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
