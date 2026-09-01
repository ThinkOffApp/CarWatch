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
    try:
        box.flush(client)
    except Exception as e:  # noqa: BLE001 - flush stops at first failure itself
        print(f"outbox flush error: {e}", flush=True)
    if len(box):
        box.enqueue(body)
        print(f"offline: queued post ({len(box)} waiting)", flush=True)
        return False
    try:
        client.post(body)
        return True
    except Exception as e:  # noqa: BLE001 - a daemon must not die on a post
        box.enqueue(body)
        print(f"post failed ({e}); queued for later", flush=True)
        return False


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
