"""WOLFBOX dashcam client + bench-day probe.

The G900's HTTP API is undocumented. Most dashcam firmwares descend from a
handful of SDKs with well-known endpoint shapes, so instead of guessing in
production code, `--probe` walks the known patterns against the camera and
prints what answers. Bench day turns those findings into the constants below.

Usage on the Pi (connected to the camera's wifi AP):

    python3 -m carwatch.wolfbox --probe
    python3 -m carwatch.wolfbox --probe --host 192.168.42.1
"""

from __future__ import annotations

import argparse
import sys
import urllib.request

# Filled in on bench day from probe results. Until then, event polling
# no-ops and the agent runs phase 1 only.
EVENT_LIST_PATH: str | None = None
FILE_DOWNLOAD_PREFIX: str | None = None

# Endpoint families seen across common dashcam firmware SDKs (Novatek,
# Hisilicon/Sunplus descendants, and vendor cgi styles). The probe tries
# each with a short timeout and reports HTTP status + first bytes.
PROBE_PATHS = [
    # Novatek-style
    "/?custom=1&cmd=3001&par=1",   # mode switch
    "/?custom=1&cmd=3015",         # file list (XML)
    "/?custom=1&cmd=2019",         # movie status
    "/DCIM/",                      # direct index listing
    "/DCIM/Movie/",
    "/DCIM/Event/",
    "/DCIM/Photo/",
    # cgi-bin styles
    "/cgi-bin/Config.cgi?action=get",
    "/cgi-bin/FileList.cgi",
    "/cgi-bin/hi3510/param.cgi?cmd=getserverinfo",
    # app-api styles
    "/app/getfilelist?type=event",
    "/api/v1/files",
    "/livestream/12",              # RTSP-over-HTTP hint endpoints
    "/blackvue_vod.cgi",           # BlackVue-style, cheap to check
]


def probe(host: str) -> None:
    base = f"http://{host}"
    print(f"Probing {base} (short timeouts; connect to the camera AP first)\n")
    hits = 0
    for path in PROBE_PATHS:
        url = base + path
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CarWatch-probe"})
            with urllib.request.urlopen(req, timeout=4) as r:
                head = r.read(120)
                print(f"  HIT  {r.status}  {path}  ->  {head[:80]!r}")
                hits += 1
        except Exception as e:
            code = getattr(e, "code", None)
            if code:  # an HTTP error is still a live endpoint family
                print(f"  http {code}  {path}")
            else:
                print(f"  ...   dead  {path}")
    print(f"\n{hits} live endpoints. Paste this output into a CarWatch issue "
          "and wire EVENT_LIST_PATH / FILE_DOWNLOAD_PREFIX accordingly.")


class Wolfbox:
    """Event-clip poller. Inert until bench day fills in the endpoints."""

    def __init__(self, host: str):
        self.host = host

    def ready(self) -> bool:
        return EVENT_LIST_PATH is not None

    def new_event_clips(self, since: float) -> list[str]:
        """Return URLs of event clips newer than `since`. No-op until wired."""
        if not self.ready():
            return []
        raise NotImplementedError("wire parsing for your camera's list format")


def main() -> None:
    ap = argparse.ArgumentParser(description="WOLFBOX probe")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--host", default="192.168.1.254")
    args = ap.parse_args()
    if args.probe:
        probe(args.host)
    else:
        print("Nothing to do without --probe (agent uses this module as a library).")
        sys.exit(1)


if __name__ == "__main__":
    main()
