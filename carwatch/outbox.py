"""Tiny persistent outbox: room posts survive offline stretches.

The car is offline constantly (garages, country roads), so a post that
fails is not an error, it is Tuesday. Every outgoing message lands here
first; flush() drains in order and stops at the first failure. The queue
is a JSON file in the state dir, so events survive restarts and power
cuts, and are delivered late rather than lost.
"""

from __future__ import annotations

import json
import os


class Outbox:
    def __init__(self, state_dir: str):
        self.path = os.path.join(state_dir, "outbox.json")

    def _load(self) -> list[dict]:
        try:
            with open(self.path) as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, items: list[dict]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items, f)
        os.replace(tmp, self.path)

    def enqueue(self, body: str, **media) -> None:
        items = self._load()
        items.append({"body": body, **{k: v for k, v in media.items() if v is not None}})
        self._save(items)

    def flush(self, room) -> None:
        """Send queued posts oldest-first; stop at the first failure."""
        items = self._load()
        while items:
            m = items[0]
            try:
                room.post(m["body"], **{k: v for k, v in m.items() if k != "body"})
            except Exception:
                break  # still offline; keep the rest queued
            items.pop(0)
            self._save(items)
