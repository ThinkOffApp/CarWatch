"""Ask the car one question from the command line, with LIVE grounding.

This replaces the loose ~/gle-ask.py script on the Pi, which hardcoded
"on Petrus desk, not installed in the car yet" and could never be fixed by
self-update because it lived outside the repo. Three copies of the car's
self-knowledge (agent.py, gle-ask.py, grounding defaults) drifted apart on
Aug 12 and @gle told petrus its OBD software was "not built" hours after it
was built. ONE source now: carwatch.agent._think, the same path the room
agent uses, so voice, CLI and mentions can never disagree again.

Usage:
    python3 -m carwatch.ask "how hot are you?"
"""

from __future__ import annotations

import sys

from carwatch.agent import _think


def main() -> None:
    question = " ".join(sys.argv[1:]).strip() or "How are you?"
    print(_think(question, "Petrus"))


if __name__ == "__main__":
    main()
