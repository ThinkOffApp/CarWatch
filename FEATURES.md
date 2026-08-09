# CarWatch features

## Implemented (in this repo, hardware arrives to meet it)

**The car is a chat-room agent**
- Joins any GroupMind room as its own agent (`@gle` by default) with its own API key
- Boot announcement, then logbook cadence: posts only on real events, never spams
- Appears automatically in CodeWatch's agent grid with presence, like any teammate

**Trips and presence**
- Wifi-context trip detection: departure, arrival home, parked away, trip duration summaries
- Home detection via your own SSID list; no GPS or cloud tracking involved

**Talk to the car from any device**
- Mention commands from phone, watch, or e-ink: `@gle status`, `@gle battery`, `@gle help`
- Persistent answered-message tracking: survives restarts, never double-answers, never answers itself
- Honest capability reporting: asks it cannot serve yet say so instead of guessing

**Dashcam events into the room**
- WOLFBOX G900 clip pipeline: poll camera over its wifi AP, download event/manual-save clips, upload to the room's media store, post with the file attached
- Novatek dashcam protocol implemented with runtime auto-detect (most mirror cams speak it); probe tool maps any other firmware family in one command
- Posted-clip dedup markers: a clip is never posted twice

**Offline voice**
- Push-to-talk pipeline: sox mic capture, whisper.cpp transcription, local LLM answer, piper voice out through the speakers
- Every stage is a swappable subprocess; zero Python dependencies beyond the standard library

**Turnkey bench day**
- `bench/bench.sh`: one command on a fresh Pi 5 builds llama.cpp and whisper.cpp, downloads a quantized Gemma 4 E2B, benchmarks real tokens/sec, probes the dashcam API and the mirror's ADB
- systemd service + idempotent installer; config lives in `/etc/carwatch`, never in the repo

## Probe-gated (wired the day the hardware answers)

- Parked-impact clip posts (needs the camera's event flags from the probe)
- CodeWatch overlay on the rearview mirror: translucent strip over the live rear feed, which is never blocked or replaced (safety first); the G900 runs a basic Android
- Pi taps a parallel copy of the camera stream: local-model frame analysis + remote live view of the parked car in CodeWatch

## Planned

- MBUX integration: assistant voice through the car speakers via Bluetooth; dashcam clips on the MBUX screen via USB mass-storage gadget
- OBD health (Mercedes-compatible dongle): battery voltage, fault codes explained in plain language, daily health posts
- Owner's-manual RAG: "what does this warning light mean" answered offline in a garage
- Hybrid brain routing: local model first, opt-in escalation to a cloud model on the car's own budget-capped key
- Home camera hub: Nest/Reolink events into rooms via the Home Assistant bridge (YAML included)
