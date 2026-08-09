"""Mention commands: ask the car things from any watch or phone.

petrus: "I'd like clawwatch to talk with the car find out hows the battery
status etc". ClawWatch/CodeWatch and the car already share the room fabric,
so talking to the car is just mentioning it: "@gle battery", "@gle status".
The agent polls the room, answers mentions, and remembers what it has
already answered across restarts.

Handlers return None when they cannot answer yet (e.g. battery before the
OBD dongle exists) - the car says so honestly instead of guessing.
"""

from __future__ import annotations

import os
import re


class Commands:
    def __init__(self, handle: str, state_dir: str, trips):
        self.handle = handle.lstrip("@").lower()
        self.trips = trips
        self._seen_path = os.path.join(state_dir, "last-answered-id")
        self._seen = self._load_seen()

    def _load_seen(self) -> str:
        try:
            return open(self._seen_path).read().strip()
        except Exception:
            return ""

    def _mark(self, msg_id: str) -> None:
        self._seen = msg_id
        try:
            with open(self._seen_path, "w") as f:
                f.write(msg_id)
        except Exception:
            pass

    def answer_new(self, messages: list[dict]) -> list[str]:
        """Given newest-first room messages, return replies for unanswered
        mentions (oldest first) and mark them answered."""
        replies: list[str] = []
        pending: list[dict] = []
        for m in messages:  # newest first; stop at the last answered one
            if m.get("id") == self._seen:
                break
            pending.append(m)
        for m in reversed(pending):
            body = (m.get("body") or "").lower()
            sender = (m.get("from") or "").lstrip("@").lower()
            if sender == self.handle:
                continue  # never answer ourselves
            if f"@{self.handle}" not in body:
                continue
            reply = self._dispatch(body)
            if reply:
                replies.append(reply)
        if messages:
            self._mark(messages[0].get("id", ""))
        return replies

    def _dispatch(self, body: str) -> str | None:
        if re.search(r"\bbattery|\bcharge|\bfuel", body):
            return self._battery()
        if re.search(r"\bstatus|\bwhere|\bhow are", body):
            return self._status()
        if re.search(r"\bhelp|\bcommands", body):
            return ("I answer: battery, status/where, help. "
                    "More arrives with the OBD dongle.")
        return self._status()  # any other mention: say where things stand

    def _status(self) -> str:
        state = getattr(self.trips, "state", None)
        name = getattr(state, "value", "unknown")
        pretty = {
            "parked_home": "Parked at home",
            "parked_away": "Parked away from home",
            "driving": "On the move",
            "unknown": "Just woke up, still getting my bearings",
        }.get(name, name)
        return pretty

    def _battery(self) -> str | None:
        # Honest until the Mercedes-compatible OBD dongle is installed
        # (phase 4). Then this reads voltage/SoC from the dongle.
        return ("No OBD dongle installed yet, so I cannot read the battery. "
                "That arrives in phase 4 with a Mercedes-compatible dongle.")
