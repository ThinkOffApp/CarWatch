"""GroupMind room I/O: post messages and upload media.

Deliberately dependency-free (urllib only) so the Pi needs nothing beyond
the Python standard library.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
import urllib.request
import uuid


class RoomClient:
    def __init__(self, api_base: str, api_key: str, room: str):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.room = room

    def post(
        self,
        body: str,
        image_url: str | None = None,
        file_url: str | None = None,
        file_name: str | None = None,
        file_size: int | None = None,
    ) -> dict:
        """Post a message to the room. Returns the created message JSON."""
        payload: dict = {"room": self.room, "body": body}
        if image_url:
            payload["image_url"] = image_url
        if file_url:
            payload["file_url"] = file_url
            if file_name:
                payload["file_name"] = file_name
            if file_size is not None:
                payload["file_size"] = file_size
        req = urllib.request.Request(
            f"{self.api_base}/api/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    def fetch(self, limit: int = 20) -> list[dict]:
        """Newest room messages (server returns newest-first)."""
        req = urllib.request.Request(
            f"{self.api_base}/api/v1/rooms/{self.room}/messages?limit={limit}",
            headers={"X-API-Key": self.api_key},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r).get("messages", [])

    # Refuse absurd uploads outright; 4K event clips are minutes long and
    # a small Pi must not slurp them into RAM (kimi3 review) - the
    # multipart body is spooled to disk and streamed, never held twice.
    MAX_UPLOAD_BYTES = 100 * 1024 * 1024

    def upload(self, path: str) -> str:
        """Upload a media file, returning its public URL."""
        size = os.path.getsize(path)
        if size > self.MAX_UPLOAD_BYTES:
            raise ValueError(f"{path} is {size} bytes, over the upload cap")
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        name = path.rsplit("/", 1)[-1]
        boundary = f"----carwatch{uuid.uuid4().hex[:12]}"
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        with tempfile.TemporaryFile() as body:
            body.write(head)
            with open(path, "rb") as f:
                shutil.copyfileobj(f, body, 1 << 16)
            body.write(tail)
            length = body.tell()
            body.seek(0)
            req = urllib.request.Request(
                f"{self.api_base}/api/v1/upload",
                data=body,
                headers={
                    "X-API-Key": self.api_key,
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(length),
                },
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["url"]


def post_queued(client: "RoomClient", state_dir: str, body: str) -> bool:
    """Post through the persistent outbox: drain anything queued from an
    offline stretch first (oldest first, order preserved), then this body;
    whatever cannot be delivered now is queued and delivered late rather
    than lost. Returns True when THIS body went out now. This is the
    README's offline promise made real (issue #23, item 5); before, the
    Outbox class existed and no daemon used it.
    """
    from carwatch.outbox import Outbox
    box = Outbox(state_dir)
    # Enqueue FIRST, then one locked drain: the queue is the single order of
    # record, so a concurrent producer cannot slip in between a "queue is
    # empty" check and a direct send and overtake an older item (codexmb,
    # #25 round 2). flush() holds the lock across the whole drain.
    box.enqueue(body)
    try:
        box.flush(client)
    except Exception as e:  # noqa: BLE001 - flush stops at first failure itself
        print(f"outbox flush error: {e}", flush=True)
    left = len(box)
    if left:
        print(f"offline: post queued ({left} waiting)", flush=True)
        return False
    return True


def client_from_config(cfg: dict | None = None) -> "RoomClient | None":
    """RoomClient for the car's own room, or None when the config lacks a
    key or room (the caller prints why)."""
    from carwatch.config import load_raw
    cfg = cfg if cfg is not None else load_raw()
    if not cfg.get("api_key") or not cfg.get("room"):
        return None
    base = (cfg.get("api_base") or "https://groupmind.one").rstrip("/")
    if base.endswith("/api/v1"):
        base = base[: -len("/api/v1")]
    return RoomClient(base, cfg["api_key"], cfg["room"])


def flush_outbox() -> int:
    """Drain whatever is queued, from any daemon's loop. Without a periodic
    drain a single failed post could sit in the outbox until the NEXT event
    happened to call post_queued (codexmb review of #25); presence calls
    this every heartbeat, the agent every poll. Returns how many went out;
    0 when nothing queued, nothing configured, or still offline."""
    from carwatch.config import state_dir
    from carwatch.outbox import Outbox
    client = client_from_config()
    if client is None:
        return 0
    box = Outbox(state_dir())
    try:
        if not len(box):
            return 0
        sent = box.flush(client)
        if sent:
            print(f"outbox: delivered {sent} queued post(s), {len(box)} left", flush=True)
        return sent
    except Exception as e:  # noqa: BLE001 - a drain must never take a daemon down
        print(f"outbox drain error: {e}", flush=True)
        return 0


def post_as_car(text: str) -> bool:
    """Post one message to the car's room as the car, from the config every
    other module reads. Returns True on success, False on any failure, never
    raises: the OBD daemon and the voice listener call this from their loops.

    This replaces ~/post-as-gle.py, a loose script that lived only on the
    reference Pi. On any other machine the subprocess failed, printed
    "post failed" and every engine reading and voice transcript silently
    never reached the room (issue #23, item 2).
    """
    from carwatch.config import config_path, load_raw, state_dir
    cfg = load_raw()
    missing = [k for k in ("api_key", "room") if not cfg.get(k)]
    if missing:
        print(f"post skipped: {', '.join(missing)} missing in {config_path()}",
              flush=True)
        return False
    client = RoomClient(cfg.get("api_base") or "https://groupmind.one",
                        cfg["api_key"], cfg["room"])
    return post_queued(client, state_dir(), text)


def main(argv: list[str] | None = None) -> int:
    """Shell entry point for the same poster:
         python3 -m carwatch.room --file /tmp/text.txt
         python3 -m carwatch.room "one line"
         echo text | python3 -m carwatch.room -
    Bodies travel via files, never as shell arguments, whenever they are
    more than a word or two."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(main.__doc__, file=sys.stderr)
        return 2
    if argv[0] == "--file" and len(argv) == 2:
        with open(argv[1], encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    elif argv[0] == "-":
        text = sys.stdin.read()
    else:
        text = " ".join(argv)
    text = text.strip()
    if not text:
        print("post skipped: empty body", file=sys.stderr)
        return 1
    return 0 if post_as_car(text) else 1


if __name__ == "__main__":
    raise SystemExit(main())
