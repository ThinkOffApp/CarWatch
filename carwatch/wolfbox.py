"""WOLFBOX G900 TriPro dashcam client + probe.

The G900 TriPro does NOT speak the Novatek `?custom=1&cmd=3015` protocol
(it answers 403). Firmware analysis of the shipped image
(`upgrade_HC901_*.appsw`, Aug 10 2026) settled the real API: the camera
runs ONE ARM64 binary, `/app/bin/main_app`, which embeds both thttpd/2.29
and a HiSilicon RTSP server, and exposes the HiSilicon **hisnet** CGI
family - not a docroot of scripts:

    http://192.168.1.1/cgi-bin/hisnet/<name>.cgi?<params>

Session is IP-registration, not a password: call `checkconnect.cgi`
first (the camera records the caller's IP as the active client) and keep
pinging it, or later calls fail with "IP has been cleared!".

Live video is a single RTSP stream whose SOURCE CAMERA is switched
server-side (`voswitch.cgi?CamID=n`), rather than one URL per lens:

    rtsp://192.168.1.1:554/livestream      trackID=0 video, trackID=1 audio

Usage on the Pi (joined to the camera's wifi AP):

    python3 -m carwatch.wolfbox --probe
    python3 -m carwatch.wolfbox --list          # clip list, all DirTypes
    python3 -m carwatch.wolfbox --pull-latest    # download newest clip
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request

HOST_DEFAULT = "192.168.1.1"
CGI_BASE = "/cgi-bin/hisnet"

# Single RTSP stream; the *camera* is chosen with voswitch.cgi.
RTSP_PORT = 554          # inferred (runtime-configurable); verify with a live DESCRIBE
RTSP_PATH = "/livestream"

# DirType is an integer folder selector. The firmware confirms the param
# but not the int->folder mapping, so we sweep a small range and let the
# camera tell us which ones exist (getdircapability.cgi lists them).
DIRTYPE_CANDIDATES = (0, 1, 2, 3, 4)

# Event/locked clips are what CarWatch posts (parked impacts, manual
# saves). On disk they carry these markers; `_00/_01/_02` are the
# front/rear/cabin channels and `_b` is the locked/second stream.
EVENT_MARKERS = ("event", "emr", "_ro", "lock", "_b.")

PROBE_PATHS = [
    f"{CGI_BASE}/checkconnect.cgi",
    f"{CGI_BASE}/getdeviceattr.cgi",
    f"{CGI_BASE}/getcamnum.cgi",
    f"{CGI_BASE}/getpreviewcamid.cgi",
    f"{CGI_BASE}/getworkmode.cgi",
    f"{CGI_BASE}/getdircapability.cgi",
    f"{CGI_BASE}/getsdstatus.cgi",
    f"{CGI_BASE}/getfilecount.cgi?DirType=0",
    f"{CGI_BASE}/getdirfilelist.cgi?DirType=0&Index=0&Count=5",
]


def probe(host: str) -> None:
    base = f"http://{host}"
    print(f"Probing {base}{CGI_BASE} (join the camera AP first)\n")
    hits = 0
    for path in PROBE_PATHS:
        try:
            req = urllib.request.Request(base + path, headers={"User-Agent": "CarWatch-probe"})
            with urllib.request.urlopen(req, timeout=6) as r:
                head = r.read(200)
                print(f"  HIT  {r.status}  {path}\n        -> {head[:160]!r}")
                hits += 1
        except Exception as e:
            code = getattr(e, "code", None)
            print(f"  {'http ' + str(code) if code else '...  dead'}  {path}")
    print(f"\n{hits} live endpoints.")


def _parse_paths(body: str) -> list[str]:
    """Pull file paths out of a hisnet listing.

    The firmware's JSON keys are known (result/payload/info) but the exact
    list schema is not reconstructable from strings alone, and some builds
    answer XML. So: try JSON, then XML tags, then fall back to scraping
    anything that looks like a media path. Whichever the camera speaks,
    we get the filenames.
    """
    paths: list[str] = []

    try:
        data = json.loads(body)

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, str) and re.search(r"\.(mp4|mov|jpg)$", v, re.I):
                        paths.append(v)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
    except Exception:
        pass

    if not paths:  # XML-ish
        for tag in ("FPATH", "NAME", "path", "name", "file"):
            paths += re.findall(rf"<{tag}>([^<]+)</{tag}>", body, re.I)

    if not paths:  # last resort: scrape bare paths/filenames
        paths += re.findall(r"[A-Za-z0-9_:\\/.\-]+\.(?:MP4|MOV|JPG)", body, re.I)

    seen, out = set(), []
    for p in paths:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


class Wolfbox:
    """Event-clip poller for the G900 TriPro (hisnet API)."""

    REPROBE_SECONDS = 120        # camera boots slower than the Pi; never latch "absent"
    SESSION_REFRESH_SECONDS = 5  # checkconnect is the heartbeat that keeps our IP registered

    def __init__(self, host: str = HOST_DEFAULT):
        self.host = host
        self._connected = False
        self._next_probe = 0.0
        self._last_session = 0.0
        self._dirtypes: list[int] = []

    # --- transport -----------------------------------------------------
    def _get(self, path: str, timeout: int = 8) -> bytes:
        req = urllib.request.Request(
            f"http://{self.host}{path}", headers={"User-Agent": "CarWatch"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def _cgi(self, name: str, timeout: int = 8) -> str:
        return self._get(f"{CGI_BASE}/{name}", timeout).decode("utf-8", "ignore")

    # --- session -------------------------------------------------------
    def _touch_session(self) -> None:
        """Re-register our IP. Cheap, and the camera drops idle clients."""
        now = time.time()
        if now - self._last_session < self.SESSION_REFRESH_SECONDS:
            return
        try:
            self._cgi("checkconnect.cgi", timeout=5)
            self._last_session = now
        except Exception:
            pass

    def ready(self) -> bool:
        if self._connected:
            self._touch_session()
            return True
        now = time.time()
        if now < self._next_probe:
            return False
        self._next_probe = now + self.REPROBE_SECONDS
        try:
            self._cgi("checkconnect.cgi", timeout=5)
            self._connected = True
            self._last_session = now
            self._discover_dirtypes()
        except Exception:
            pass  # camera asleep/out of range; retried later
        return self._connected

    def _discover_dirtypes(self) -> None:
        """Keep only DirTypes that actually return a listing."""
        found = []
        for dt in DIRTYPE_CANDIDATES:
            try:
                body = self._cgi(f"getdirfilelist.cgi?DirType={dt}&Index=0&Count=1", timeout=6)
                if _parse_paths(body) or "result" in body.lower():
                    found.append(dt)
            except Exception:
                continue
        self._dirtypes = found or list(DIRTYPE_CANDIDATES)

    # --- files ---------------------------------------------------------
    def _file_url(self, path: str) -> str:
        """Camera paths are DOS-ish (A:\\DCIM\\...); thttpd serves them off /."""
        web = path.replace("A:\\", "/").replace("\\", "/")
        if not web.startswith("/"):
            web = "/" + web
        return f"http://{self.host}{web}"

    def list_clips(self, dirtype: int, count: int = 50) -> list[str]:
        if not self.ready():
            return []
        try:
            body = self._cgi(
                f"getdirfilelist.cgi?DirType={dirtype}&Index=0&Count={count}", timeout=20
            )
        except Exception:
            return []
        return [self._file_url(p) for p in _parse_paths(body)]

    def new_event_clips(self) -> list[str]:
        """ALL event-clip URLs, oldest first. Empty when unreachable.

        No filtering/capping here: the agent owns dedup (posted markers)
        and flood control, so an offline burst still drains fully."""
        if not self.ready():
            return []
        clips: list[str] = []
        for dt in self._dirtypes:
            for url in self.list_clips(dt):
                if any(m in url.lower() for m in EVENT_MARKERS):
                    clips.append(url)
        # filenames embed timestamps, so lexical order is chronological
        return sorted(set(clips))

    def all_clips(self) -> list[str]:
        """Every clip the camera lists, oldest first (for --list/testing)."""
        if not self.ready():
            return []
        out: list[str] = []
        for dt in self._dirtypes:
            out += self.list_clips(dt)
        return sorted(set(out))

    def download(self, url: str, dest: str) -> str:
        """Pull one clip to local storage; returns dest."""
        req = urllib.request.Request(url, headers={"User-Agent": "CarWatch"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        return dest

    # --- live video ----------------------------------------------------
    def rtsp_url(self) -> str:
        return f"rtsp://{self.host}:{RTSP_PORT}{RTSP_PATH}"

    def select_camera(self, cam_id: int) -> bool:
        """Point the single RTSP stream at front/rear/cabin."""
        if not self.ready():
            return False
        try:
            self._cgi(f"voswitch.cgi?CamID={cam_id}", timeout=6)
            return True
        except Exception:
            return False

    def camera_count(self) -> int:
        if not self.ready():
            return 0
        try:
            m = re.search(r"\d+", self._cgi("getcamnum.cgi", timeout=6))
            return int(m.group()) if m else 0
        except Exception:
            return 0

    def trigger_event_clip(self) -> bool:
        """Ask the camera to save an emergency/locked clip right now."""
        if not self.ready():
            return False
        try:
            self._cgi("publishsimulationevent.cgi?EmrRecord=1", timeout=6)
            return True
        except Exception:
            return False


def main() -> None:
    ap = argparse.ArgumentParser(description="WOLFBOX G900 TriPro client")
    ap.add_argument("--probe", action="store_true", help="hit each known endpoint")
    ap.add_argument("--list", action="store_true", help="list all clips")
    ap.add_argument("--pull-latest", action="store_true", help="download newest clip")
    ap.add_argument("--dest", default="/tmp/carwatch-clip.mp4")
    ap.add_argument("--host", default=HOST_DEFAULT)
    args = ap.parse_args()

    if args.probe:
        probe(args.host)
        return

    cam = Wolfbox(args.host)
    if not cam.ready():
        print(f"Camera not reachable at {args.host} (join its wifi AP first).")
        sys.exit(1)

    if args.list or args.pull_latest:
        print(f"cameras: {cam.camera_count()}   rtsp: {cam.rtsp_url()}")
        print(f"dirtypes with content: {cam._dirtypes}")
        clips = cam.all_clips()
        print(f"{len(clips)} clips")
        for c in clips[-20:]:
            print("  " + c)
        events = cam.new_event_clips()
        print(f"{len(events)} event/locked clips")

        if args.pull_latest and clips:
            newest = clips[-1]
            print(f"\ndownloading {newest} -> {args.dest}")
            cam.download(newest, args.dest)
            print("done")
        return

    print("Nothing to do; use --probe, --list or --pull-latest.")
    sys.exit(1)


if __name__ == "__main__":
    main()
