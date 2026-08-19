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

## 4. Mercedes-specific reads (mode 22 DIDs) — RAN 2026-08-19, NOTHING ANSWERED

> **It has now run in the car, twice** — 13:06 stationary and 13:09 moving.
> Both reported *no Mercedes-specific DIDs answered*. **That is not proof the
> car lacks them.** Both asked from the ELM327's default **broadcast** address
> (0x7DF); Mercedes DIDs normally answer only when a **specific ECU** is
> addressed (0x7E0 etc.), often only after security access. Silence from a
> broadcast is an addressing result, not a capability result.
>
> A corrected probe — **per-ECU addressing plus raw NRC capture** (d5d86c1) —
> is deployed but **has not yet run in the car**, so this section stays open.
> The raw negative response is what settles it: nothing at all = wrong address;
> **NRC 0x11** = service not supported; **NRC 0x33** = data exists but locked;
> **NRC 0x31** = mode 22 works but those DID numbers were wrong. The first runs
> stored no raw responses, which is why capture was added.

`obd_probe.py` walks the manufacturer identification block **0xF100–0xF1FF**
(mode 22, read-only). No public per-DID table exists for the Daimler PHEV
BMS, so the scan *is* the research instrument: whatever answers is a finding,
silence is not an error. Named candidates it tries explicitly:

| DID | Meaning (candidate) | Answered? |
|-----|---------------------|-----------|
| 0xF190 | VIN (UDS mirror) | no answer (broadcast) |
| 0xF187 | part number | no answer (broadcast) |
| 0xF18C | serial number | no answer (broadcast) |

This is the path expected to expose the GLE's traction-battery SoC and other
Mercedes-only telemetry that standard OBD PIDs do not carry.

## 5. What we CANNOT get over OBD (honest boundary)

- **Anything requiring a write** — clearing codes, actuator tests, coding,
  climate/lock/charge control. The module is read-only by design and by the
  fact that blind UDS writes to a modern Mercedes gateway can leave fault
  codes or worse.
- **Two-way control** (pre-entry climate / "camp mode", remote lock, charge
  scheduling) lives behind **Mercedes me**, not OBD.
  **As of 2026-08-19 that path is LIVE** — see §6.
- **Live cabin/road context** (camera, GPS) — comes from the WOLFBOX dashcam
  and the Pi, not the OBD port.

## 6. The Mercedes me cloud path — LIVE since 2026-08-19

Everything §5 lists as unreachable over OBD now arrives from the cloud instead,
so the two sources are complementary rather than competing: **OBD knows the car
only while the Pi is plugged in; the cloud knows the parked car.**

- **Route:** the community app protocol `mbapi2020` running in Home Assistant
  on the Mac mini (the always-on host — the Pi travels and cannot hold a
  24/7 poller). The official free developer products were **discontinued
  2023-08-31**; only the paid Fleet API remains, so the portal's still-live
  doc pages are not evidence of a usable product.
- **Access:** a dedicated Mercedes me account invited as **co-user** on each
  car (the app calls it "co-user"/"Manage users", never "drivers").
- **Delivered, measured:** 101 entities across both cars — door + lock state,
  all four windows, sunroof, all four tyre pressures, state of charge,
  charging power/status/end-of-charge, electric and liquid range separately,
  odometer, fuel level, trip and average-speed data, location. The GLE adds
  AdBlue level, pre-climate state, engine/ignition state and parking state.
- **The Berlin GLE is covered with no hardware there at all.**

**Deliberate limit — locking yes, unlocking no.** `lock.py` sends
`doors_lock(vin)` with no PIN, while unlock calls `doors_unlock_with_pin` and,
with no PIN configured and no code supplied, logs `"Code required but none
provided"` and aborts. **The PIN field is left empty on purpose**, so a leak of
this host yields no way to open the car. Unlocking is reserved for a separate
direct path with the PIN entered per use.

**Wider than doors.** The integration also exposes `ENGINE_START`/`ENGINE_STOP`,
preheat and preconditioning (incl. departure time and seats), battery max-SoC,
charging breaks and sunroof control — so "read-only" is a decision that has to
be made per service, not a property that comes for free. The preconditioning
services may cover camp mode's climate half **without writing anything to the
car**; check that before returning to the DoIP path.

---

*How to refresh the live sections:* plug the ELM327 in, then
`POST <reach-url>/api/obd/scan` for the supported-PID + VIN list, and let the
plug-in `obd_probe.py` run for the mode-22 DID sweep. Paste the JSON results
into §3 and §4.
