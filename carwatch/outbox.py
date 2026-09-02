"""Tiny persistent outbox: room posts survive offline stretches.

The car is offline constantly (garages, country roads), so a post that
fails is not an error, it is Tuesday. Every outgoing message lands here
first; flush() drains in order and stops at the first failure. The queue
is a JSON file in the state dir, so events survive restarts and power
cuts, and are delivered late rather than lost.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager


class Outbox:
    # A car parked for a month in a garage must not fill the SD card with
    # heartbeat lines: keep the newest MAX_ITEMS, drop the oldest beyond.
    MAX_ITEMS = 200

    def __init__(self, state_dir: str):
        self.path = os.path.join(state_dir, "outbox.json")
        self.lock_path = self.path + ".lock"

    @contextmanager
    def _locked(self):
        """One queue transaction at a time across PROCESSES: the agent, the
        voice listener, the OBD daemon and presence all touch this file
        (codexmb review of #25). flock on a side file, held for the whole
        read-modify-write (and for a flush, across the posts)."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fh = open(self.lock_path, "a")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()

    def __len__(self) -> int:
        with self._locked():
            return len(self._load())

    def _load(self) -> list[dict]:
        try:
            with open(self.path) as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, items: list[dict]) -> None:
        if len(items) > self.MAX_ITEMS:
            del items[: len(items) - self.MAX_ITEMS]
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items, f)
        os.replace(tmp, self.path)

    def enqueue(self, body: str, **media) -> None:
        with self._locked():
            items = self._load()
            items.append({"body": body, **{k: v for k, v in media.items() if v is not None}})
            self._save(items)

    def flush(self, room) -> int:
        """Send queued posts oldest-first; stop at the first failure.
        Returns how many went out."""
        sent = 0
        with self._locked():
            items = self._load()
            while items:
                m = items[0]
                try:
                    room.post(m["body"], **{k: v for k, v in m.items() if k != "body"})
                except Exception:
                    break  # still offline; keep the rest queued
                items.pop(0)
                self._save(items)
                sent += 1
        return sent
