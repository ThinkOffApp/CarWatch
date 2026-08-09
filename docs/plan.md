# CarWatch build plan

The car becomes another agent in your GroupMind: `@gle` posts to a room like
any teammate, and CodeWatch on the phone/watch is its remote control.

## Reference hardware

Pi 5 8GB (active cooling mandatory - the BCM2712 throttles), WOLFBOX G900
3-channel dashcam on its own hardwire kit, high-endurance microSD for the
camera. The Pi needs its own 5V/5A USB-C feed; the Pi 5's USB-C gadget mode
shares the power port, so MBUX gadget experiments need separate powering.

## Phases

**0 - Bench day.** Flash Pi OS 64, cooling on, install llama.cpp, run
`python3 -m carwatch.wolfbox --probe` against the camera AP, measure model
tok/s. Outcomes: real WOLFBOX endpoints wired into `wolfbox.py`, a model
choice backed by measurements.

**1 - Room agent (code in this repo).** Presence + trips from network context
(home SSIDs vs none), boot announcement, departure/arrival/trip-summary posts.
Cadence rule inherited from the human room: logbook, not firehose.

**2 - Dashcam events (probe-gated).** Poll the camera for event/manual clips,
pull over the camera's wifi, upload via `/api/v1/upload`, post into the room
with the clip attached. Parked-impact messages are the flagship demo: the car
messages you when something hits it and you answer from your wrist.

**3 - Offline voice.** Local multimodal model (Gemma 4 E2B class: ~1.5 GB in
4-bit, native audio input, multimodal; measured ~7-8 tok/s on Pi 5 in
published tests - bench day decides). Push-to-talk mic, piper TTS out.
Owner's-manual RAG so "what does this warning light mean" works in an
underground garage with zero signal. Fine-tuning happens OFF the Pi
(QLoRA, export GGUF) and only if a narrow task underperforms RAG + prompts.

**4 - OBD health.** A Mercedes-compatible OBD dongle (NOT a BMW ENET cable),
daily health posts, fault codes explained in plain language by the local model.

## RAM budget (8 GB)

| What | ~RAM |
|------|------|
| OS + agent | 1.0 GB |
| Gemma 4 E2B (4-bit) | 1.5 GB |
| Async/vision model when loaded | 3-5 GB |
| piper TTS | 0.2 GB |
| headroom / page cache for clips | rest |

## Security rules

- The car gets ITS OWN GroupMind API key; never reuse another agent's.
- No keys in the repo, ever (public repo; config lives in /etc/carwatch).
- Clips upload to the room's media store; nothing else leaves the car.
