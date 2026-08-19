# What CarWatch can read from the car

**Vehicle:** Mercedes-Benz E 300e (W213, 2021 plug-in hybrid) — `@eclass`.
Also applies to the GLE 350 de (W167) except where noted.
**Interface:** USB ELM327 v1.5 adapter on the OBD-II port, CAN (ISO 15765-4).
**Posture:** READ-ONLY. The module never writes to the car — no clearing of
codes (mode 04), no actuator or ECU writes (UDS 0x2E/0x2F/0x31), no coding.
Everything below is a *read*. Changing anything in the car (climate, locks,
charging) is out of scope for OBD and belongs to the Mercedes me path
(separate investigation).

Source of truth for this list: `carwatch/elm327.py` (live decode + capability
scan) and `carwatch/obd_probe.py` (deep read-only probe). This document is
generated from that code; when the code's PID table changes, update this file.

---

## 1. Live values we decode today

Read every cycle by `elm327.read_all()` and posted to the room by
`@eclass`. These are the numbers you see streaming during a drive.

| PID | Value | Unit | Notes |
|-----|-------|------|-------|
| 0x04 | engine load | % | calculated |
| 0x05 | coolant temperature | °C | |
| 0x0C | engine RPM | rpm | 0 while driving on electric |
| 0x0D | vehicle speed | km/h | |
| 0x0F | intake air temperature | °C | rough proxy for outside air |
| 0x2F | fuel tank level | % | |
| 0x42 | control module voltage | V | ~14.x in ready mode |
| 0x5B | hybrid/EV battery remaining | % | the traction-battery SoC |

**Verified live on the E 300e** (Aug 15 + Aug 19 drives): all eight answer.
On the **GLE 350 de**, PID 0x5B (hybrid SoC) returned nothing on first read —
its battery SoC likely needs a Mercedes-specific DID (see §4).

## 2. Stored diagnostic trouble codes (DTCs)

- `elm327.read_dtcs()` — mode 03, stored codes, CAN-multi-ECU aware (each
  module answers `43 <count> <pairs>`; decoded to Pxxxx/Cxxxx/Bxxxx/Uxxxx).
- `obd_probe.py` also dumps the **raw** mode-03 frames, so a suspicious code
  can be re-decoded by hand (this is how the recurring **P0043** was proven a
  multi-ECU parser ghost, not a real fault — both cars read clean).
- Mode 07 (pending codes) is available via the same path if needed.

## 3. Supported-PID scan — the full "what this car advertises"

`elm327.scan_capabilities()` / `python3 -m carwatch.elm327 scan` reads the
mode-01 **supported-PID bitmasks** (`0100`, `0120`, `0140`, `0160`, `0180`,
`01A0`). Each reply is a 32-bit map of which PIDs in the next block the car
supports. This is the authoritative, per-car answer to "what more could we
read". Anything it lists but §1 does not decode is a one-line addition to the
PID table.

Also reads:
- **VIN** — mode 09 PID 02 (`elm327.read_vin()`), and its UDS mirror DID
  0xF190.
- **Protocol** — `ATDPN` (which OBD protocol the adapter negotiated).

### Live scan result — E 300e, 2026-08-19

Captured with `POST /api/obd/scan` during a drive.

- **Protocol:** A0 (ISO 15765-4 CAN, auto).
- **VIN:** readable (mode 09 answered a full 17-char VIN). *Not printed here —
  this repo is public and a VIN is a vehicle identifier; it lives only in the
  live JSON, masked as `W1K…666` which confirms a Mercedes W213 E-Class.*
- **Stored DTCs:** none — car is clean.
- **PIDs advertised:** 47. We decode 8 today (marked ✅). The rest are
  readable and one line each to add.

| PID | Meaning | Decoded? |
|-----|---------|----------|
| 0x04 | calculated engine load | ✅ |
| 0x05 | engine coolant temp | ✅ |
| 0x0C | engine RPM | ✅ |
| 0x0D | vehicle speed | ✅ |
| 0x0F | intake air temp | ✅ |
| 0x2F | fuel tank level | ✅ |
| 0x42 | control module voltage | ✅ |
| 0x5B | hybrid battery remaining | ✅ |
| 0x0E | timing advance | readable, not decoded |
| 0x11 | throttle position | readable, not decoded |
| 0x1F | run time since engine start | readable, not decoded |
| 0x21 | distance with MIL on | readable, not decoded |
| 0x31 | distance since codes cleared | readable, not decoded |
| 0x33 | barometric pressure | readable, not decoded |
| 0x46 | **ambient air temp** | readable, not decoded — true outside temp, better than 0x0F |
| 0x47 | absolute throttle position B | readable, not decoded |
| 0x49 | accelerator pedal position D | readable, not decoded |
| 0x51 | fuel type | readable, not decoded |
| 0x5C | **engine oil temp** | readable, not decoded |
| 0x5E | engine fuel rate | readable, not decoded |
| 0x06/0x07 | fuel trim b1 (short/long) | readable, not decoded |
| 0x0B | intake manifold pressure | readable, not decoded |
| 0x23 | fuel rail gauge pressure | readable, not decoded |

Plus a set of **standard-range mode-01 PIDs our lookup table just does not
name** (0x13, 0x15, 0x1C, 0x20, 0x30, 0x34, 0x40, 0x41, 0x45, 0x4A, 0x56,
0x60, 0x62, 0x63, 0x65, 0x68, 0x7A, 0x7C, 0x80, 0x8B, 0x8E, 0x9D, 0x9E, 0xA0,
0xA4). **These are NOT Mercedes-specific data** (claudemm's correction,
19.8.) — they are ordinary J1979 PIDs we simply have no name for yet, and
several are structural, not values: **0x20/0x40/0x60/0x80/0xA0 are the
"is there another block of PIDs" support masks**, not readable data, and
e.g. 0x13 = O2-sensor locations, 0x1C = OBD compliance standard. So this
list is "standard PIDs to name in our table", not hidden telemetry. The
genuinely **Mercedes-only** data lives behind the mode-22 DIDs (§4), which
have not been run yet.

**Easy wins to add to §1 next:** 0x46 ambient air temp and 0x5C engine oil
temp are the two most useful undecoded PIDs (a real outside-temperature and
oil temp), each a one-line addition to the `PIDS` table.

## 4. Mercedes-specific reads (mode 22 DIDs) — NOT YET RUN IN THE CAR

> **Pending, like §3.** The rows below are DIDs the code *tries*, not things
> this car has answered. `obd_probe.py` has never completed a run in the car
> (see `docs/obd-findings.md`). Whatever answers becomes a finding; treat this
> table as the probe's target list, not proven capability.

`obd_probe.py` walks the manufacturer identification block **0xF100–0xF1FF**
(mode 22, read-only). No public per-DID table exists for the Daimler PHEV
BMS, so the scan *is* the research instrument: whatever answers is a finding,
silence is not an error. Named candidates it tries explicitly:

| DID | Meaning (candidate) | Answered? |
|-----|---------------------|-----------|
| 0xF190 | VIN (UDS mirror) | not yet run |
| 0xF187 | part number | not yet run |
| 0xF18C | serial number | not yet run |

This is the path expected to expose the GLE's traction-battery SoC and other
Mercedes-only telemetry that standard OBD PIDs do not carry.

## 5. What we CANNOT get over OBD (honest boundary)

- **Anything requiring a write** — clearing codes, actuator tests, coding,
  climate/lock/charge control. The module is read-only by design and by the
  fact that blind UDS writes to a modern Mercedes gateway can leave fault
  codes or worse.
- **Two-way control** (pre-entry climate / "camp mode", remote lock, charge
  scheduling) lives behind **Mercedes me**, not OBD — tracked separately.
- **Live cabin/road context** (camera, GPS) — comes from the WOLFBOX dashcam
  and the Pi, not the OBD port.

---

*How to refresh the live sections:* plug the ELM327 in, then
`POST <reach-url>/api/obd/scan` for the supported-PID + VIN list, and let the
plug-in `obd_probe.py` run for the mode-22 DID sweep. Paste the JSON results
into §3 and §4.
