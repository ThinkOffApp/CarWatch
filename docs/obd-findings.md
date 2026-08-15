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

## Deep probe (carwatch/obd_probe.py) — armed, verified, not yet run on a car
Sweeps every advertised PID, reads the VIN (mode 09), dumps raw DTC frames,
tries UDS mode-22 candidates. Proven complete against the fake adapter
(all five result posts, correct decodes). Its first in-car attempt on
Aug 15 was killed 2s in by the service restart's cgroup sweep — launcher
now a systemd transient unit that survives restarts. Fires automatically
at the next plug-in and posts everything to the room.

## Open items
- GLE hybrid SoC via Mercedes-specific PIDs (Berlin, needs the GLE)
- First real deep-probe run (next plug-in, any car)
- Meaningful-change post threshold + daily digest + plain-language fault
  explanations (phase-4 product layer)
