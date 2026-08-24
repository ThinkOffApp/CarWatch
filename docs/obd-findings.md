# OBD findings — real cars, real data

The record petrus asked for: what the OBD work has actually produced,
updated after each real-car session. Raw claims live in the room; this file
is the distilled truth.

## Mercedes GLE 350 de (V167, diesel PHEV, Berlin) — Aug 14 2026
First real read ever, via USB ELM327 v1.5 (HS position), remotely through
the dashboard while the car sat on petrus's hotspot:

- engine 766 rpm (idle), coolant 48 C, speed 0
- module voltage 14.398 V (engine running, charging)
- engine load 45.9%
- **hybrid battery SoC: NOT exposed** on standard PID 0x5B despite the car
  being a PHEV → GLE needs Mercedes-specific PIDs (open work item)
- fuel level: not returned either

## Mercedes E 300e (W213, petrol PHEV, Helsinki) — Aug 15 2026
Zero-touch: the watcher read the car by itself the moment the adapter went
in. Nine+ reads over the session:

- **hybrid battery SoC ANSWERS: 88.6–89.4%** on standard PID 0x5B — the
  GLE silence is car-specific, not Mercedes-wide
- coolant 18–19 C (cold parked car), speed 0, engine 0 rpm
- 12V rail 12.8 V parked → **14.3 V the moment the car entered ready mode**
  (DC-DC converter live; the reader caught the transition in real time)

## P0043 — parser ghost, NOT a real fault (both cars)
"P0043" appeared stored on BOTH cars, twice each — implausible, and false:
multi-frame mode-03 replies repeat the `43` response header per frame; the
old parser stripped only the first and decoded later headers as codes.
Confirmed two ways on Aug 15: the probe's per-frame parser decodes the
fake adapter's genuine P0420 correctly with zero phantoms, and the
CAN-aware rewrite of the daily reader reaches the same verdict.
**Both cars are clean. No garage visit.**

## Deep probe — first in-car run, E 300e, Aug 24 2026
BT ELM327 on `/dev/rfcomm0` (not USB). Room-triggered; Petrus did not type
`deepscan`. First attempt died with errno 5; second run 12.8 s.

- **Standard OBD:** 12 PIDs, VIN readable (mode 09). Live parked: 0 rpm,
  coolant 31–34 C, 0 km/h, hybrid SoC 84.7→84.3 % on 0x5B, 12 V rail 14.4 V
  (ready / DC-DC).
- **Move (Petrus shifting the car):** 3 km/h, rpm still 0 (EV), SoC 83.9 %,
  12 V dropped to 12.7 V.
- **Mode-22 on generic 7E0–7E5:** silent. Not proof the data is absent.
- **29-bit UDS 18DAxx:** silent on this adapter.
- **Mercedes 11-bit 0x300–0x330:** broadcast traffic, not UDS replies.
  Repeat transmitters: 0x307 (continuous 8-byte frame), 0x328, 0x33D.
- **Dashboard "probe car connection"** only looked at `/dev/ttyUSB0` while
  the live path was rfcomm0. Fixed in `69064eb` (first present of ttyUSB0/1,
  then rfcomm0). Pi pulled that over `/api/update` the same session.

The Aug 15 in-car attempt was killed 2 s in by a cgroup sweep. This session
is the first that finished.

## Open items
- GLE hybrid SoC via Mercedes-specific PIDs (Berlin, needs the GLE)
- ATMA 60–120 s on 0x307/0x328/0x33D during a real drive, then decode
- Meaningful-change post threshold + daily digest + plain-language fault
  explanations (phase-4 product layer)
