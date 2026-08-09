"""Trip detection state machine.

Signals are kept deliberately abstract because installs differ: an
ignition-switched Pi sees boot = trip start; a constantly-powered Pi
uses network context (home wifi vs none) and, later, OBD speed.

v1 signal: the current wifi SSID (home SSID = parked at home; the
dashcam's own AP is park-neutral). Ignition-switched installs get
boot = trip start for free because the daemon starts at boot.

The state machine emits events; the agent decides what to post.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from enum import Enum


class State(Enum):
    UNKNOWN = "unknown"
    PARKED_HOME = "parked_home"
    DRIVING = "driving"
    PARKED_AWAY = "parked_away"


@dataclass
class Event:
    kind: str  # "departure" | "arrival_home" | "parked_away" | "trip_summary"
    detail: str
    at: float


def current_ssid() -> str | None:
    """SSID the Pi is associated with, or None. Uses iwgetid (wireless-tools)."""
    try:
        out = subprocess.run(
            ["iwgetid", "-r"], capture_output=True, text=True, timeout=5
        )
        ssid = out.stdout.strip()
        return ssid or None
    except Exception:
        return None


class TripTracker:
    def __init__(
        self,
        home_ssids: list[str],
        idle_seconds: int = 300,
        neutral_ssids: list[str] | None = None,
    ):
        self.home_ssids = set(home_ssids)
        # Park-neutral networks (the dashcam's own AP): being on them says
        # nothing about location - the normal clip-polling topology is
        # "parked at home, joined to the camera" and must not read as
        # DRIVING (kimi3 review).
        self.neutral_ssids = set(neutral_ssids or [])
        self.idle_seconds = idle_seconds
        self.state = State.UNKNOWN
        self.trip_started_at: float | None = None
        self._last_transition = time.time()

    def _classify(self) -> State:
        ssid = current_ssid()
        if ssid and ssid in self.home_ssids:
            return State.PARKED_HOME
        if ssid and ssid in self.neutral_ssids:
            return self.state  # no signal either way: hold current state
        # No home network. PARKED_AWAY on continued no-signal HOLDS -
        # classifying it back to DRIVING made the pair oscillate and post
        # "Parked away" every idle interval forever (codexmb reproduced
        # at 301s/622s/943s). Only a real signal change leaves PARKED_AWAY.
        if self.state == State.PARKED_AWAY:
            return State.PARKED_AWAY
        # With only wifi as a v1 signal we cannot separate "driving" from
        # "parked away" instantly; DRIVING decays into PARKED_AWAY after
        # idle_seconds without a state change.
        return State.DRIVING

    def tick(self) -> list[Event]:
        """Call periodically; returns events to publish (possibly empty)."""
        now = time.time()
        seen = self._classify()
        events: list[Event] = []

        if seen == self.state:
            # Long stretch of "driving" with no change = actually parked away.
            if (
                self.state == State.DRIVING
                and now - self._last_transition > self.idle_seconds
            ):
                self.state = State.PARKED_AWAY
                events.append(Event("parked_away", "Parked away from home", now))
                self._last_transition = now
            return events

        prev, self.state = self.state, seen
        self._last_transition = now

        if seen == State.DRIVING and prev == State.PARKED_HOME:
            self.trip_started_at = now
            events.append(Event("departure", "Departed", now))
        elif seen == State.DRIVING and prev == State.UNKNOWN:
            # Boot away from home wifi (or before wifi associates) is not a
            # departure - announcing one was a false positive (codexmb).
            # Track the trip silently; arrival still reports correctly.
            self.trip_started_at = now
        elif seen == State.PARKED_HOME and prev != State.PARKED_HOME:
            if self.trip_started_at:
                mins = int((now - self.trip_started_at) / 60)
                events.append(
                    Event("trip_summary", f"Trip ended, {mins} min door to door", now)
                )
                self.trip_started_at = None
            events.append(Event("arrival_home", "Home", now))
        return events
