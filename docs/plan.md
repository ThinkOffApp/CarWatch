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

**5 - MBUX integration (use the car's own computer).** Easiest first:

1. *Cabin audio via Bluetooth - car mic AND speakers, no USB hardware
   (petrus: "all android auto like chatgpt use car inbuilt").* Correct:
   the Pi registers as a Bluetooth hands-free phone (HFP), the car streams
   its mic to the Pi at 16kHz wideband (fine for whisper) and plays the
   assistant through the car speakers (A2DP). Same mechanism AA/CarPlay
   use. Footnotes: AA's projection mic needs a Google-signed app we cannot
   be, so we use plain HFP (slightly lower-fi, still fine); HFP mic is
   call-state, so listening is push-to-talk (steering-wheel button or wake
   tap), which is better for privacy than open-mic anyway. Zero added
   audio hardware; a bench-day bluez pairing task.
2. *MBUX screen as the DASHBOARD (petrus's design).* The Pi renders a
   dashboard image on a cycle - car state, pending count, latest room
   messages, newest event thumbnails - the same render-not-capture
   pattern as the CodeWatch e-ink screensaver, and exposes it (plus an
   `Events/` clip folder) through a USB mass-storage gadget on the car's
   data port. MBUX's own media viewer becomes the dashboard display: no
   app needed on the head unit, it is just showing pictures the Pi keeps
   fresh. Cabling note: the Pi 5's gadget-capable USB-C port is also its
   power input, so this needs a power-injector arrangement (power the Pi
   separately, data lines to MBUX).
3. *Android Auto phone emulation - explicitly out of scope.* Phone-side AA
   requires Google-signed certificates; do not plan on it.

**6 - CodeWatch overlay on the rearview mirror (probe-gated, LIKELY NOT
POSSIBLE).** Correction (Aug 9): the G900 most likely runs a CLOSED
Novatek-family RTOS, not Android (petrus's vendor-support source; my
earlier "basic Android" was one thin hit). Bench day still runs a 30s
ADB probe to settle it for our exact unit, but expect nothing. If RTOS:
the mirror keeps its stock job, and the icon strip moves to the MBUX
dashboard render or a small dedicated display. Silver lining: a
Novatek-family RTOS is exactly what speaks the cmd=3015 HTTP protocol
already implemented in wolfbox.py, so this raises confidence the CLIP
pipeline works day one.

*Safety-first design (petrus: "it is important for safety"): the mirror's
LIVE REAR FEED is never replaced or blocked.* The overlay is ICONS ONLY
(petrus's design - mockup in docs/mirror-overlay-mockup.png): weather now
/ +3h / +24h as icon + degrees (online-fed, cached, hides when stale),
battery as a drawn meter + percent and remaining range (OBD/phase 4
sourced), pending-approvals badge, message badge, camp-mode indicator,
recording dot, connectivity. No text to
read at a glance-critical surface; anything that needs reading lives on
the MBUX dashboard or the phone. Rendered via Android
`TYPE_APPLICATION_OVERLAY`, hides while reversing if the unit exposes
that state.

Known risks: many units ship locked down (no ADB, no launcher), and the
mirror's Android version may predate CodeWatch's minSdk 30 - fallback is a
purpose-built slim overlay APK with a low minSdk. Worst case, the mirror
still plays clips through its own UI.

*Confirmed architecture (petrus): overlay on the mirror + the Pi taps a
COPY of the camera stream in parallel.* The copy never sits in the
mirror's display path; the Pi uses it for (a) local-model frame analysis
(impact/scene understanding feeding room posts), and (b) a relayed remote
live view of the parked car in CodeWatch. Probe maps the stream endpoint
on bench day.


**Camp mode (petrus).** A parked mode that keeps the car ALIVE instead of
quiet: camera stays recording, voice assistant stays listening, the strip
and MBUX dashboard stay rendered, presence posts "camping" once. Toggle by
mention ("@gle camp on/off") or a strip long-press; exits automatically on
departure. The power budget note: camp mode is for hardwired installs with
battery-protection cutoff (the WOLFBOX kit has one), and the Pi should
watch voltage once OBD lands and self-shutdown below threshold.

**7 - Hybrid brain routing.** Three tiers, best reachable wins, all through
one ordered `brain_endpoints` list of OpenAI-compatible URLs (the voice
loop already speaks that protocol, so no code changes to add a tier):

1. *Pi local* - Gemma on the Pi, always on, fully offline.
2. *Car LAN* - when the MacBook rides along, its bigger local model (e.g.
   DeepSeek V4 Flash) serves over the car LAN: still offline, still zero
   API spend, much smarter.
3. *Cloud* - online + explicit opt-in ("think hard"), a model like Opus 5
   on the car's OWN budget-capped key; the car can never quietly burn
   credits. The room shows which brain answered.

**Glass-surface render rules (petrus: "very simple... no blinking!").**
Anything projected onto glass (HUD) or overlaid on the mirror follows hard
rules: static glyphs and numbers only, high contrast, generous size, NO
blinking, NO animation, NO transitions - state changes simply appear on
the next refresh. Nothing on a driving surface may pull the eye. Alerts
that genuinely need attention go to the phone/watch, not to the glass.

**HUD content rule (petrus): the HUD is ALL about driving - no CodeWatch
notifications there, ever.** HUD shows driving data only: speed, range,
weather ahead, navigation-adjacent info. Approvals, messages and badges
live on the mirror strip (parked-relevant) and the phone/watch. The
surfaces have roles: HUD = driving, mirror = car status at a glance,
MBUX = the full dashboard, phone/watch = interaction.

**HUD hardware (route 2 reference).** Windscreen reflective film (PET HUD
film, ~10-15 euro on amazon.de) + a high-brightness panel: standard Pi
displays (300-400 nits) prototype fine and work at night; DAYLIGHT needs
a 1000-nit-class panel (Waveshare/Newhaven direct, ~110 euro). A salvaged
OEM Mercedes HUD unit is the stretch path: teardown, read the TFT's model
number, panelook it, and if LVDS/RGB a cheap HDMI driver board makes the
Pi render through OEM optics.

**8 - Cars talking to each other (petrus).** Rooms are already the fabric,
so cars can share any room today. Ship first: CONVOY MODE - a private
shared room for a road trip; member cars post position updates rendered
on each MBUX dashboard, propose fuel/charging stops, relay hazard
callouts down the line; humans and cars in one thread. Public car rooms
come after, with hard privacy defaults: a car NEVER posts precise
location or clips to a public room; only opt-in coarse events (hazard
warnings). Location trails are a stalking vector; private convoy rooms
get full detail, public rooms get almost none.

**9 - Adoption: the kit ladder (petrus: hardware friction).**
1. *Free, ships with launch:* a pre-built SD image (`carwatch.img`) -
   flash, enter wifi + key on a first-boot config page, done - plus an
   Amazon affiliate IDEA LIST: the whole kit in one add-to-basket click,
   zero stock, small commission. Printed QR codes point at
   carwatch.app/build (our instructions page carrying the affiliate
   links) - Amazon's ToS forbids affiliate links in offline material,
   a QR to our own page is fine.
2. *If demand shows:* sell pre-flashed SD cards (trivial logistics).
3. *On real demand:* a DIY kit sold as a curated UNASSEMBLED bundle of
   individually CE-marked off-the-shelf parts + open-source software.
   That keeps us a distributor, not a manufacturer. EU note (not legal
   advice, get 30 min of it before selling): the 2-year statutory
   consumer warranty cannot be disclaimed with a DIY label; assembling
   or branding the hardware as one new device would make us the
   manufacturer with full CE and liability - do not cross that line
   casually.

**Cameras that can join.** The dashcam's three cameras (ours), any USB
camera on the Pi (a ~20 euro interior cam gives camp mode eyes), and
possibly official Mercedes me API data (location, lock, fuel) as a
cleaner alternative to OBD for some fields (check at phase 4). The
factory surround cameras are OFF LIMITS: they live on Mercedes's
internal bus with no public access, and prying is warranty and safety
territory.

## Modular tiers (petrus: minimum setup = Pi + local AI + car integration)

The code is already modular - the dashcam, OBD and displays are optional
plugins around a core agent. The kit tiers mirror that:

| Tier | Hardware | What you get | ~Cost |
|------|----------|--------------|-------|
| CORE | Pi 5 8GB kit + USB mic/speaker | @car room agent: presence, trips, mention commands, offline voice with local AI, brain routing | ~150 euro |
| +VISION | any Novatek-family dashcam (WOLFBOX ref) | clips into the room, impact events, mirror icon strip, camp-mode eyes | +100-320 euro |
| +OBD | Mercedes-compatible BT dongle | battery, range, fault codes on strip and in chat | +30 euro |
| +HUD | high-brightness display + film | driving-only windshield projection | +60-120 euro |

CORE alone is a complete product: the car talks, listens, and messages.
Every tier above it hot-plugs; nothing requires reflashing.

**Bench-day firmware catalog (safe, read-only).** Download all G900 firmware
branches and run binwalk - reading only, NEVER flashing the car's mirror.
Records SoC, RTOS family, and whether the UI is reflashable. If crackable,
open a "G900 custom UI" issue with the findings as a real starting point.
Custom-UI hacking is a someday/community track (buy a SECOND G900 as the
mule; never brick the daily driver); RE notes go in the public repo to
attract the car-hacker crowd. Never on the product's critical path.

**Compute options (bench-day head-to-head, petrus flagged the Hailo).**
Two levers, decided by measured tok/s, not bought blind:
- *RAM (Pi 5 8GB vs 16GB)*: 16GB lets Gemma 4 E4B be the always-on brain
  vs E2B on 8GB. ~110 euro more at scalped German retail (~40 at MSRP).
- *Hailo-10H NPU (Raspberry Pi AI HAT+ 2, Jan 2026, sub-100 euro, 40
  TOPS INT4, own 8GB LPDDR4X)*: runs LLMs, ~10x CPU (a 1B at 30-50 tok/s
  vs 2-5 on CPU), OpenAI-compatible hailo-llm-server drops into our
  stack. Caveat: supported models are a converted set (Llama 3.2 1B,
  Qwen 2.5 1.5B, DeepSeek R1 1.5B) - Gemma 4 is NOT supported unless
  compiled for it. So it is faster-but-different-model vs our chosen
  model on CPU. The Hailo-8 (older AI HAT) is vision-only; only the 10H
  runs LLMs.
Bench day runs Gemma 4 E2B on CPU against a supported 1.5B on the Hailo
and we pick by what feels smarter in the car.

**Runtime to beat: ik_llama.cpp (via Potato OS, petrus's find).**
Potato OS (github.com/potato-os/core, Apache 2.0) is a Pi-optimized
local-LLM image that runs Qwen3-30B-A3B (30B MoE, 3B active) at ~8-9
tok/s on an 8GB Pi 5 with SSD offload, plus vision. Two takeaways:
- Swap bench.sh from plain llama.cpp to **ik_llama.cpp** (IQK-optimized
  fork), meaningfully faster on the Pi.
- The MoE + SSD-offload recipe puts a much smarter model than Gemma E2B
  on the 8GB board at the same speed - may make 16GB RAM and the Hailo
  both unnecessary for the brain.
Bench day: flash Potato OS on a spare card, measure Qwen3-30B on our
unit to confirm 8-9 tok/s, then adopt ik_llama into our stack (keep our
own agent layer rather than depending on their experimental OS).

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
