"""The CarWatch daemon: phase 1 (presence + trips) and phase 2 hooks (dashcam).

Run: python3 -m carwatch.agent            (config from /etc/carwatch/config.json)
     CARWATCH_CONFIG=./dev.json python3 -m carwatch.agent
"""

from __future__ import annotations

import os
import time
import traceback

from .commands import Commands
from .config import Config
from .outbox import Outbox
from .room import RoomClient
from .trips import TripTracker
from .wolfbox import Wolfbox


TICK_SECONDS = 20

# Flood control: at most this many unposted clips per camera poll, newest
# first; the rest stay unposted and drain on later polls, so a big offline
# burst arrives bounded but complete (codexmb P1: the old newest-3 cap in
# Wolfbox permanently lost anything older).
MAX_CLIP_POSTS_PER_POLL = 3


def main() -> None:
    cfg = Config.load()
    os.makedirs(cfg.state_dir, exist_ok=True)
    room = RoomClient(cfg.api_base, cfg.api_key, cfg.room)
    trips = TripTracker(
        cfg.home_ssids,
        cfg.trip_idle_seconds,
        neutral_ssids=[cfg.wolfbox.ssid] if cfg.wolfbox.ssid else [],
    )
    cam = Wolfbox(cfg.wolfbox.host)
    commands = Commands(cfg.handle, cfg.state_dir, trips)
    outbox = Outbox(cfg.state_dir)
    last_cam_poll = 0.0

    # One boot announcement, then event-driven posts only - the room is a
    # logbook, not a firehose (same cadence rule the human agents follow).
    # Everything goes through the outbox: offline events deliver late,
    # never get lost (garages and country roads are normal, not errors).
    outbox.enqueue(f"{cfg.handle} online")

    while True:
        try:
            for ev in trips.tick():
                text = {
                    "departure": "Departing",
                    "arrival_home": "Home, parked",
                    "parked_away": "Parked away from home",
                    "trip_summary": ev.detail,
                }.get(ev.kind, ev.detail)
                outbox.enqueue(f"{cfg.handle}: {text}")
            outbox.flush(room)

            # Mentions: "@gle battery", "@gle status" from any watch/phone.
            # Replies go through the persistent outbox: enqueue is local
            # and cannot fail, so the marker advances exactly once per
            # batch - no duplicate answers when one post of several fails
            # (codexmb P1), and delivery is the outbox's problem. A gap
            # longer than the fetch window can still skip mentions; the
            # window is generous for a car.
            try:
                replies, newest_id = commands.pending_replies(room.fetch(limit=50))
                for reply in replies:
                    outbox.enqueue(f"{cfg.handle}: {reply}")
                commands.mark(newest_id)
                outbox.flush(room)
            except Exception:
                pass  # room unreachable mid-drive is routine

            now = time.time()
            if cam.ready() and now - last_cam_poll > cfg.wolfbox.poll_seconds:
                last_cam_poll = now
                unposted = [
                    u for u in cam.new_event_clips()
                    if not os.path.exists(
                        os.path.join(cfg.state_dir, "posted-" + u.rsplit("/", 1)[-1])
                    )
                ]
                # Newest first, bounded per poll, backlog drains over time.
                for clip_url in list(reversed(unposted))[:MAX_CLIP_POSTS_PER_POLL]:
                    name = clip_url.rsplit("/", 1)[-1]
                    marker = os.path.join(cfg.state_dir, f"posted-{name}")
                    local = os.path.join(cfg.state_dir, name)
                    # Camera URLs only resolve on the camera's own AP, so
                    # the clip is pulled locally, then uploaded to the
                    # room's media store once real internet is back.
                    cam.download(clip_url, local)
                    public = room.upload(local)
                    outbox.enqueue(
                        f"{cfg.handle}: dashcam event, clip attached",
                        file_url=public,
                        file_name=name,
                        file_size=os.path.getsize(local),
                    )
                    outbox.flush(room)
                    open(marker, "w").close()
                    os.unlink(local)
        except Exception:
            # A daemon in a car must never die on a network blip. Print for
            # journalctl, keep ticking; unsent events are re-derived from
            # state next tick where possible.
            traceback.print_exc()
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
