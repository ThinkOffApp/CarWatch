# What data CarWatch can actually access

Plain language, no hex required. Verified on the real car (Mercedes E 300e
plug-in hybrid, W213) — the dates below say when each item was last proven
on the road, not assumed from a spec sheet.

## 1. Live telemetry — polled continuously while the adapter is plugged in

Proven in-car 2026-08-24. Read every ~60 s over a Bluetooth ELM327 in the
OBD port; shown live on the dashboard, posted to the room only on real
events (first read after connect, changed fault codes, hybrid-charge
milestones — not spam).

| What | Unit | Notes |
|---|---|---|
| Engine speed | rpm | 0 when driving electric — that is real, not an error |
| Vehicle speed | km/h | |
| Coolant temperature | °C | |
| Intake air temperature | °C | |
| Engine load | % | |
| Fuel level | % | |
| 12 V system voltage | V | 14.4 V = charging, ~12.x V = engine off |
| Hybrid battery charge | % | the traction battery SoC, e.g. 84.7 % |
| Stored fault codes | text | decoded to standard Pxxxx/Cxxxx codes |
| VIN | text | the car's identity number |

## 2. Extended set — the "nerd dashboard"

Standard SAE J1979 values, read when the car advertises them: fuel trims
(short/long), fuel pressures (rail and gauge), intake manifold pressure,
MAF air flow, throttle position, timing advance, runtime since start,
distance driven with the warning light on, fuel type code, and more.
The software reads the intersection of what it can decode and what this
car says it supports.

## 3. Deep scan — once a day, parked

Per-ECU identity reads on Mercedes-range addresses plus the standard
capability probe. Last real run (2026-08-24, in the car): 12 PIDs
answered, VIN readable, 12.8 s duration. Read-only — it never writes to
the bus.

## 4. Discovered, not yet decoded — the broadcast stream

Probing the Mercedes 11-bit range (0x300–0x330) on 2026-08-24 revealed
continuous broadcast frames the adapter can hear: senders 0x307, 0x328
and 0x33D repeat constantly. This is the car's internal telemetry
chatter — almost certainly richer than the polled values above (throttle,
brakes, gear, hybrid flows). Decoding it means recording during a drive
and correlating bytes with what the driver did. Passive listening only.
**Status: open project.** Until decoded, nobody can honestly say which
value lives in which byte.

## 5. Closed doors — known limits, stated plainly

- 29-bit UDS identity addresses (18DAxxF1) do not answer through this
  adapter. Tested 2026-08-24, silent.
- Tyre pressures: not available in the current reading set.
- Cameras: there is **no camera feed** of any kind. The on-board agent is
  explicitly briefed that camera views do not exist and that any sensor
  number it cannot point to in its latest real reading does not exist
  either (that rule was added after it once invented readings).
