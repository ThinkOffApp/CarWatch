"""GroupMind room I/O: post messages and upload media.

Deliberately dependency-free (urllib only) so the Pi needs nothing beyond
the Python standard library.
"""

from __future__ import annotations

import json
import mimetypes
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

    def upload(self, path: str) -> str:
        """Upload a media file, returning its public URL."""
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        name = path.rsplit("/", 1)[-1]
        boundary = f"----carwatch{uuid.uuid4().hex[:12]}"
        with open(path, "rb") as f:
            data = f.read()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.api_base}/api/v1/upload",
            data=body,
            headers={
                "X-API-Key": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)["url"]
