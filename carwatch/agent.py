"""The CarWatch daemon: phase 1 (presence + trips) and phase 2 hooks (dashcam).

Run: python3 -m carwatch.agent            (config from /etc/carwatch/config.json)
     CARWATCH_CONFIG=./dev.json python3 -m carwatch.agent
"""

from __future__ import annotations

import time
import traceback

from .config import Config
from .room import RoomClient
from .trips import TripTracker
from .wolfbox import Wolfbox


TICK_SECONDS = 20


def main() -> None:
    cfg = Config.load()
    room = RoomClient(cfg.api_base, cfg.api_key, cfg.room)
    trips = TripTracker(cfg.home_ssids, cfg.trip_idle_seconds)
    cam = Wolfbox(cfg.wolfbox.host)
    last_cam_poll = 0.0

    # One boot announcement, then event-driven posts only - the room is a
    # logbook, not a firehose (same cadence rule the human agents follow).
    try:
        room.post(f"{cfg.handle} online")
    except Exception:
        pass  # offline boot is normal in a garage; first event will retry

    while True:
        try:
            for ev in trips.tick():
                text = {
                    "departure": "Departing",
                    "arrival_home": "Home, parked",
                    "parked_away": "Parked away from home",
                    "trip_summary": ev.detail,
                }.get(ev.kind, ev.detail)
                room.post(f"{cfg.handle}: {text}")

            now = time.time()
            if cam.ready() and now - last_cam_poll > cfg.wolfbox.poll_seconds:
                last_cam_poll = now
                for clip_url in cam.new_event_clips(since=now - 3600):
                    # Phase 2: pull the clip locally, upload, post with media.
                    room.post(f"{cfg.handle}: dashcam event", image_url=clip_url)
        except Exception:
            # A daemon in a car must never die on a network blip. Print for
            # journalctl, keep ticking; unsent events are re-derived from
            # state next tick where possible.
            traceback.print_exc()
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
