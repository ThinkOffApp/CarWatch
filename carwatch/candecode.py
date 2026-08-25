#!/usr/bin/env python3
"""Post-drive CAN correlation logger / analyzer for CarWatch.

There is no public W213/E300e DBC (codexmb web-hunt, Aug 25 2026), so every
broadcast ID on this car is proprietary and CANNOT be named from ID + rate
alone. The only honest way to map a byte to a meaning is a *correlation drive*:
capture the raw bus while a known thing happens (turn the wheel, brake, indicate,
switch ignition) and see which byte moves with it. This tool does the "see which
byte moves" half against the raw capture files that carwatch already records.

It reads the record-mode log format written by the ATMA capture:

    {"started": <unix>, "seconds": N, "protocol": "A7"}      # JSON header line
    <unix_ts> <ID_hi> <ID_lo> 00 00 <d0> <d1> ... <d7>       # one frame per line

i.e. a 2-byte ID, a fixed `00 00` pad, then the 8 data bytes D0..D7. (My first
pass mis-read the ID width; codexmb flagged it, the 00 00 pad is what gives it
away - every frame in a clean capture has it.)

Pure standard library on purpose: it runs the same on a laptop and on the Pi, so
the car can analyze its own capture over the tunnel with no extra install.

Modes
-----
  summary <log>                per-ID rate + per-byte range/spread; flags a byte
                               as LIVE (moves) or static (config/keepalive)
  corr    <log> <ID:byte>      rank every other byte channel by how tightly it
                               tracks the reference byte (Pearson r over time),
                               so bytes belonging to the same physical signal
                               surface together
  diff    <baseline> <active>  compare a parked capture against a driving one;
                               bytes that are flat parked but move while driving
                               are motion-signal candidates (speed/rpm/steering)
  events  <log> <marks.csv>    marks.csv holds `unix_ts,label` lines (one per
                               known moment - "wheel left", "brake", ...); prints
                               each live byte just before/after each mark

Nothing here writes to the bus or touches a running service - it only reads log
files. Steering/speed/etc. stay CANDIDATES until a correlation drive confirms
them; this tool reports evidence, it never labels a stream "proven".
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict


def parse_log(path):
    """Return (header, frames). frames = list of (ts, id_str, [d0..d7])."""
    header = {}
    frames = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                try:
                    header = json.loads(line)
                except ValueError:
                    pass
                continue
            parts = line.split()
            # <ts> <hi> <lo> <00> <00> <8 data>  -> at least 13 tokens
            if len(parts) < 13:
                continue
            try:
                ts = float(parts[0])
            except ValueError:
                continue
            hexes = parts[1:]
            if not all(len(t) == 2 and all(c in "0123456789ABCDEFabcdef" for c in t) for t in hexes[:12]):
                continue
            cid = (hexes[0] + hexes[1]).upper()
            data = [int(t, 16) for t in hexes[4:12]]
            frames.append((ts, cid, data))
    return header, frames


def by_id(frames):
    d = defaultdict(list)
    for ts, cid, data in frames:
        d[cid].append((ts, data))
    return d


def _rate(rows):
    if len(rows) < 2:
        return 0.0
    span = rows[-1][0] - rows[0][0]
    return (len(rows) - 1) / span if span > 0 else 0.0


def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def cmd_summary(path):
    header, frames = parse_log(path)
    if not frames:
        print("no frames parsed - is this a record-mode capture?")
        return
    groups = by_id(frames)
    dur = frames[-1][0] - frames[0][0]
    print(f"# {path}")
    print(f"# protocol {header.get('protocol','?')}  frames {len(frames)}  "
          f"span {dur:.1f}s  ids {len(groups)}")
    print(f"# {'ID':>6} {'Hz':>5} {'n':>5}  D0..D7 spread (std; '.'=static, digits=distinct when live)")
    for cid, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        cols = list(zip(*[d for _, d in rows]))  # 8 columns of byte values
        cells = []
        live_bytes = 0
        for col in cols:
            distinct = len(set(col))
            if distinct <= 1:
                cells.append("   .")
            else:
                live_bytes += 1
                cells.append(f"{_std(col):4.0f}")
        tag = "" if live_bytes else "  <- STATIC (config/keepalive)"
        print(f"  0x{cid:>4} {_rate(rows):5.1f} {len(rows):>5}  [{' '.join(cells)}]{tag}")
    print("# spread is per-byte std over the drive; a big number = a byte that "
          "swings = a live signal worth correlating.")


def _series(groups, ref):
    cid, bi = ref.split(":")
    cid = cid.replace("0x", "").replace("0X", "").upper()
    bi = int(bi)
    rows = groups.get(cid)
    if not rows:
        raise SystemExit(f"ID 0x{cid} not in capture")
    return [(ts, d[bi]) for ts, d in rows], cid, bi


def _resample(ref_ts, other):
    """Last-value-hold: for each ref timestamp, the other channel's most recent
    sample. other is a list of (ts, val) assumed time-sorted."""
    out = []
    j = 0
    cur = None
    for t in ref_ts:
        while j < len(other) and other[j][0] <= t:
            cur = other[j][1]
            j += 1
        out.append(cur)
    return out


def _pearson(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return 0.0
    ax = [p[0] for p in pairs]
    bx = [p[1] for p in pairs]
    ma, mb = sum(ax) / n, sum(bx) / n
    num = sum((x - ma) * (y - mb) for x, y in pairs)
    da = math.sqrt(sum((x - ma) ** 2 for x in ax))
    db = math.sqrt(sum((y - mb) ** 2 for y in bx))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def cmd_corr(path, ref):
    _, frames = parse_log(path)
    groups = by_id(frames)
    ref_series, rcid, rbi = _series(groups, ref)
    ref_ts = [t for t, _ in ref_series]
    ref_val = [v for _, v in ref_series]
    scored = []
    for cid, rows in groups.items():
        cols = list(zip(*[d for _, d in rows]))
        other_ts = [t for t, _ in rows]
        for bi, col in enumerate(cols):
            if cid == rcid and bi == rbi:
                continue
            if len(set(col)) <= 1:
                continue  # static, cannot correlate
            resampled = _resample(ref_ts, list(zip(other_ts, col)))
            r = _pearson(ref_val, resampled)
            scored.append((abs(r), r, cid, bi))
    scored.sort(reverse=True)
    print(f"# reference 0x{rcid} D{rbi} on {path}")
    print(f"# {'ID':>6} {'byte':>4} {'r':>7}  (bytes that move WITH the reference "
          "over time = likely the same physical signal)")
    for _, r, cid, bi in scored[:15]:
        print(f"  0x{cid:>4}   D{bi} {r:+7.2f}")


def cmd_diff(baseline, active):
    _, fb = parse_log(baseline)
    _, fa = parse_log(active)
    gb, ga = by_id(fb), by_id(fa)
    print(f"# baseline {baseline}  vs  active {active}")
    print(f"# bytes that are flat in baseline but move in active = motion-signal "
          "candidates")
    print(f"# {'ID':>6} {'byte':>4} {'base_std':>9} {'act_std':>9}  note")
    hits = []
    for cid in sorted(set(gb) | set(ga)):
        rb = gb.get(cid, [])
        ra = ga.get(cid, [])
        cb = list(zip(*[d for _, d in rb])) if rb else [()] * 8
        ca = list(zip(*[d for _, d in ra])) if ra else [()] * 8
        for bi in range(8):
            sb = _std(cb[bi]) if bi < len(cb) and cb[bi] else 0.0
            sa = _std(ca[bi]) if bi < len(ca) and ca[bi] else 0.0
            if sa > 2.0 and sa > sb * 3 + 1:
                hits.append((sa - sb, cid, bi, sb, sa))
    hits.sort(reverse=True)
    for _, cid, bi, sb, sa in hits:
        print(f"  0x{cid:>4}   D{bi} {sb:9.1f} {sa:9.1f}  woke up while driving")
    if not hits:
        print("# (no byte clearly woke up - captures may be too similar or too short)")


def cmd_events(path, marks_csv):
    _, frames = parse_log(path)
    groups = by_id(frames)
    marks = []
    with open(marks_csv) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ts, _, label = line.partition(",")
            try:
                marks.append((float(ts), label.strip()))
            except ValueError:
                pass
    if not marks:
        raise SystemExit("no marks parsed (expected `unix_ts,label` lines)")
    live = []  # (cid, bi)
    for cid, rows in groups.items():
        cols = list(zip(*[d for _, d in rows]))
        for bi, col in enumerate(cols):
            if len(set(col)) > 1:
                live.append((cid, bi))
    for mts, label in marks:
        print(f"\n# mark {mts:.2f}  {label}")
        for cid, bi in live:
            rows = groups[cid]
            before = [d[bi] for t, d in rows if t <= mts][-3:]
            after = [d[bi] for t, d in rows if t > mts][:3]
            print(f"  0x{cid:>4} D{bi}  before {before}  after {after}")


USAGE = """candecode - CarWatch CAN correlation logger
  python3 -m carwatch.candecode summary <log>
  python3 -m carwatch.candecode corr    <log> <ID:byte>   e.g. 0x0500:0
  python3 -m carwatch.candecode diff     <parked.log> <driving.log>
  python3 -m carwatch.candecode events   <log> <marks.csv>
"""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 1
    mode = argv[1]
    try:
        if mode == "summary" and len(argv) == 3:
            cmd_summary(argv[2])
        elif mode == "corr" and len(argv) == 4:
            cmd_corr(argv[2], argv[3])
        elif mode == "diff" and len(argv) == 4:
            cmd_diff(argv[2], argv[3])
        elif mode == "events" and len(argv) == 4:
            cmd_events(argv[2], argv[3])
        else:
            print(USAGE)
            return 1
    except FileNotFoundError as e:
        print(f"file not found: {e.filename}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
