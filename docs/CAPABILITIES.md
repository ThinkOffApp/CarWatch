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

> The live supported-PID list for this specific car is filled in by running
> `POST /api/obd/scan` (or the plug-in probe) and pasting the result here.
> Pending: capture from a drive where the tunnel stays up long enough.

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
