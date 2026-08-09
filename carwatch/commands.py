"""Mention commands: ask the car things from any watch or phone.

petrus: "I'd like clawwatch to talk with the car find out hows the battery
status etc". ClawWatch/CodeWatch and the car already share the room fabric,
so talking to the car is just mentioning it: "@gle battery", "@gle status".
The agent polls the room, answers mentions, and remembers what it has
already answered across restarts.

Answer-tracking contract (kimi3 review): replies are computed here but the
answered-marker only advances via mark() AFTER the caller successfully
posts - a mention must never be lost to an offline stretch. On first run
(no marker yet) the newest message is marked and nothing is answered, so a
fresh install never replays days of backlog.

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
        # Token match, not substring: "@gleen" must not trigger "@gle"
        # (same false-friend class as the CodeWatch provider markers).
        self._mention = re.compile(
            rf"@{re.escape(self.handle)}(?![a-z0-9])", re.IGNORECASE
        )

    def _load_seen(self) -> str:
        try:
            return open(self._seen_path).read().strip()
        except Exception:
            return ""

    def mark(self, msg_id: str) -> None:
        """Advance the answered-marker; call only after posts succeeded."""
        if not msg_id:
            return
        self._seen = msg_id
        try:
            with open(self._seen_path, "w") as f:
                f.write(msg_id)
        except Exception:
            pass

    def pending_replies(self, messages: list[dict]) -> tuple[list[str], str]:
        """Given newest-first room messages, return (replies oldest-first,
        newest_id). The caller posts the replies, then calls mark(newest_id)
        on success."""
        newest_id = messages[0].get("id", "") if messages else ""
        if not self._seen:
            # First run: never replay historical mentions.
            self.mark(newest_id)
            return [], newest_id
        replies: list[str] = []
        pending: list[dict] = []
        for m in messages:  # newest first; stop at the last answered one
            if m.get("id") == self._seen:
                break
            pending.append(m)
        for m in reversed(pending):
            body = (m.get("body") or "")
            sender = (m.get("from") or "").lstrip("@").lower()
            if sender == self.handle:
                continue  # never answer ourselves
            if not self._mention.search(body):
                continue
            reply = self._dispatch(body.lower())
            if reply:
                replies.append(reply)
        return replies, newest_id

    def _dispatch(self, body: str) -> str | None:
        if re.search(r"\b(battery|charge|fuel)\b", body):
            return self._battery()
        if re.search(r"\b(status|where)\b|\bhow are\b", body):
            return self._status()
        if re.search(r"\b(help|commands)\b", body):
            return ("I answer: battery, status/where, help. "
                    "More arrives with the OBD dongle.")
        # Unknown ask: one short pointer, not a status dump (kimi3: chatty).
        return "Not sure what you mean - try battery, status, or help."

    def _status(self) -> str:
        state = getattr(self.trips, "state", None)
        name = getattr(state, "value", "unknown")
        return {
            "parked_home": "Parked at home",
            "parked_away": "Parked away from home",
            "driving": "On the move",
            "unknown": "Just woke up, still getting my bearings",
        }.get(name, name)

    def _battery(self) -> str | None:
        # Honest until the Mercedes-compatible OBD dongle is installed
        # (phase 4). Then this reads voltage/SoC from the dongle.
        return ("No OBD dongle installed yet, so I cannot read the battery. "
                "That arrives in phase 4 with a Mercedes-compatible dongle.")
