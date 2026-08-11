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
local-LLM image that runs Qwen3.6-35B-A3B (35B MoE, 3B active, Apr 2026, stronger than the 30B) at ~8-9
tok/s on an 8GB Pi 5 with SSD offload, plus vision. Two takeaways:
- Swap bench.sh from plain llama.cpp to **ik_llama.cpp** (IQK-optimized
  fork), meaningfully faster on the Pi.
- The MoE + SSD-offload recipe puts a much smarter model than Gemma E2B
  on the 8GB board at the same speed - may make 16GB RAM and the Hailo
  both unnecessary for the brain.
Bench day: flash Potato OS on a spare card, measure Qwen3-30B on our
unit to confirm 8-9 tok/s, then adopt ik_llama into our stack (keep our
own agent layer rather than depending on their experimental OS).

**Vision (three cameras + a VLM) - the "car with eyes" phase.**
The G900's three feeds each earn a use, run on EVENTS not 30fps (a VLM
frame takes seconds on a Pi, so trigger on impact/lock/question):
- *Rear (flagship):* parked-impact forensics - describe the hit-and-run
  in plain words + partial plate, post to the room with the clip.
- *Front:* "what just happened" incident summaries on hard brakes,
  read missed road/parking signs, parking-legality checks.
- *Cabin (safety):* child/pet-left-behind alert on lock-and-walk,
  forgot-your-bag reminders.
- *Cross-cutting:* the car ANSWERS questions about what it saw ("what
  colour was the car that cut me off") from recent frames, offline.
The dashcam already triggers on motion/impact, so the model runs on the
right frames. Needs a vision-capable model + the 16GB board.

**Smart sentry (petrus: store clips when people look at or shoot the
car).** While parked, the dashcam's own parking mode captures raw
motion/impact clips cheaply; the Pi's vision layer PROMOTES the
interesting ones - periodically asks "is someone paying attention to
this car?" and on yes saves the clip, describes the behaviour ("a person
at the driver door for 40s, looking in" / "someone raised a phone at the
car"), posts to the room. Sudden hits come from the impact trigger, then
get annotated. It is Tesla Sentry Mode that TALKS, on any car. Design
constraints: POWER (Pi+cameras parked draws more than the dashcam alone
- runs off the hardwire kit's battery cutoff, cap how often the vision
model wakes); PRIVACY/LAW (own-car protection filming is broadly EU-OK
like any parking mode, but continuous FACE analysis of passersby is
sensitive GDPR - keep it event-triggered and describe BEHAVIOUR, not
identity). Lives under camp mode.

*Fan cam (petrus: "interested in people taking photos of my car bc its
so cool").* Same person/phone-toward-car detector, positive framing: log
admiration events ("photographed outside the cafe at 14:20" + clip) and
tally them ("turned 6 heads today"). Lighter power/privacy than threat
monitoring (curiosity log, not surveillance) and on-brand marketing
telemetry - the ThinkOff car is a rolling billboard and this measures
its pull. Two modes off one detector: guardian vs fan cam.

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

## Name: Vadelma (petrus, Aug 10 2026)

The Pi (and by extension this build) is named **Vadelma** - Finnish for
raspberry. It is the car's offline brain. Positioning tagline from petrus:
"Smart car with the fam online or offline with the Vadelma" - the car is
a room agent that talks to the family/team, using the cloud when it can
reach it and running fully offline on Vadelma when it cannot (tunnel,
dead zone, privacy). Online/offline hybrid is the 3-tier brain routing
already in the plan.

## Optional camera: Insta360 (petrus, Aug 10 2026)

petrus has an Insta360 that can stream. It is a DEMO/premium input, not
the shippable default (the WOLFBOX dashcam stays the kit's cameras).
Value: true 360 coverage catches admirers/threats from any angle at once,
not just where a fixed lens points - ideal for fan cam and sentry on a
parked car. Path: Insta360 in USB webcam mode -> Pi grabs frames with
ffmpeg (or pull its wifi stream) -> one-line ffmpeg de-warp of the
equirectangular image -> VLM reads the region -> post the cropped angle.
Caveat: the Insta360 lives in Finland; the shoot is on the branded GLE in
Berlin, so 360 fan-cam footage is a later Finland-trip upgrade.

## Tomorrow's shoot (Aug 11 2026, Berlin, branded GLE)

Decided: film here on the GLE (branded, coolest car, on hand). Not Finland
(only the Insta360 + Mac Mini are there; not required for v1). Runbook:
1. Unbox + flash the 16GB Pi (Vadelma).
1b. (NVMe M.2 SSD only - SKIP for a USB external SSD, which is what
   petrus has) Enable PCIe Gen 3 for the SSD: add `dtparam=pciex1_gen=3` to
   /boot/firmware/config.txt and reboot (Pi 5 defaults to Gen 2; Gen 3
   ~doubles NVMe bandwidth for the Qwen MoE. Out of spec - if dmesg shows
   NVMe errors, remove the line). bench.sh prints the negotiated speed.
2. Run bench/bench.sh - builds llama.cpp + ik_llama.cpp + whisper + piper,
   pulls Gemma 4 E2B + Qwen3.6-35B-A3B, prints tok/s, probes WOLFBOX.
3. Wire Vadelma to the GLE for power + to the WOLFBOX.
4. Shoot: the branded car answering from the GroupMind room, online and
   offline. Keep it turnkey - no fiddling on camera.

## Hailo vs SSD: the single-PCIe-lane constraint (Aug 10 2026)

petrus asked "which Hailo, and we have an SSD". Verified current facts:

- **Which Hailo**: the board is the **Raspberry Pi AI HAT+** (Hailo chip
  soldered on, not a module). Hailo-8L = 13 TOPS ~70 USD; Hailo-8 = 26
  TOPS ~110 USD. For the detection layer (person / phone-toward-car /
  motion) the **13 TOPS 8L is plenty** - do not pay for the 26.
- **The catch**: the Pi 5 has **one PCIe lane**. The NVMe SSD needs it
  (random reads are what make the Qwen3.6-35B MoE offload fast), and the
  standard AI HAT+ takes that same FPC connector. You cannot stack both.
  To run SSD + Hailo together you need a **dual-M.2 PCIe-switch HAT**
  (SunFounder Dual NVMe Raft, or Seeed PCIe-to-dual-M.2) plus a Hailo
  **M.2 module** (not the integrated HAT+ board). Then they SHARE the one
  lane (~half bandwidth each) and the connector power budget is tight.

**Decision**: give the lane to the SSD for v1 (the offline LLM depends on
it), ship without Hailo. Add real-time sentry later via a switch HAT +
Hailo M.2 module, accepting shared bandwidth - OR run the detector on the
Pi camera/CPU at modest FPS. Tomorrow: SSD yes, Hailo not yet.

Sources: raspberrypi.com/news/raspberry-pi-ai-hat ; SunFounder Dual NVMe
Raft; Seeed PCIe-to-dual-M.2 HAT product pages.

## Reactive ambient lighting (petrus, Aug 10 2026)

"Hue lights in the car the Pi controls, adaptive to camera: if someone
looks, lights go on." The physical twin of the fan cam: the car lights up
when it catches someone admiring it.

- **Hardware**: Hue in a car is awkward (needs Bridge + mains). Two paths:
  1. Hue **Bluetooth** bulbs = Pi drives them over BLE, no bridge, power
     via a 12V-to-mains adapter.
  2. Better for a car: **addressable LED strip (WS2812)** on 5V straight
     off car USB, no bridge, instant, cheap, full color/animation.
  Recommend the LED strip for the build; keep Hue for the house.
- **Trigger**: reuses the fan-cam/sentry detection layer (no new model).
  Camera sees a **face oriented toward the car** within a few meters ->
  Pi runs a light cue (welcome glow / attention pulse). True eye-gaze is
  fuzzy; start with face-facing-car, refine later.
- **Guardrails**: PARKED-mode only (never light up or distract while
  driving); mind battery (lights + camera + Pi unattended draws power).

## LED strip pick: WS2815 12V (petrus, Aug 10 2026)

For the car use **WS2815**, not WS2812B: 12V native (runs off car 12V, no
buck for the LEDs), backup data line (one vibration-killed pixel does not
break the rest), no voltage drop. 1-2 m is plenty (cabin/grille), low
draw. Brands: BTF-Lighting, ALITOVE. Buy "no controller" - the Pi is the
controller, car 12V is the power.

Build (only 3 connections, no true zero-wiring since the Pi drives it):
1. WS2815 strip (no controller).
2. 12V-to-USB-C adapter to power the Pi off the car (needed anyway).
3. Pi to strip: shared GROUND (must be common), one data pin off a GPIO,
   plus a 3.3V-to-5V level shifter (74AHCT125, ~2 EUR) for reliable data.
App-controlled remote kits are plug-and-play but the Pi cannot drive them.
TODO when petrus asks: exact amazon.de links (strip + shifter + adapter).

## LED upgrade: premium + DMX (petrus, Aug 10 2026)

petrus: "best quality LEDs with DMX control." Revised pick:
- **Best look**: addressable **COB** (dotless continuous ribbon, reads
  premium), 12V native: BTF-Lighting **FCOB WS2811 12V**. Or **12V RGBW**
  addressable (**WS2814**) if true whites matter more than smoothness.
  Higher density = better.
- **DMX control**: run **OLA (Open Lighting Architecture)** on the Pi -
  speaks DMX512 + Art-Net/sACN natively, drives the pixel strip, and can
  take input from DMX software/consoles. Keeps the Pi as the brain.
  True DMX-native strips (UCS512 IC) exist but need an added RS-485 /
  USB-DMX interface - more parts, little gain over OLA.
- **Delivery honesty**: guest view to Berlin 10249 shows the plain WS2815
  ~Aug 13 (not tomorrow); premium COB/DMX gear ~2-4 days. So the LED
  feature likely misses the Aug 11 shoot. Plan: shoot the car brain +
  room demo tomorrow, add reactive lighting as a fast follow.
- TODO (on petrus's go): exact amazon.de links for COB strip + RGBW +
  level shifter, each with the delivery date shown to Berlin.

## Central light control from the Pi (petrus, Aug 10 2026)

petrus: "car also has multi color strips, control all centrally from Pi."
Two cases:
- **Aftermarket strips** (footwell/door RGB he added, own remote/app):
  Pi CAN take them over - drive directly if addressable, or replace their
  controller if dumb RGB. Feasible + safe. Bring them under OLA with the
  new COB/RGBW as one console, reactive to camera + car state.
- **Factory MBUX ambient** (64-color Mercedes interior): lives on the
  car's internal CAN with proprietary MB messages, no public API.
  Controlling it = injecting interior-CAN frames - partly reverse-
  engineered by enthusiasts but car/MY-specific, brittle, and writing to
  interior CAN risks confusing other body modules.

**Decision**: v1/shoot = Pi centrally drives ADD-ON strips via OLA only;
leave factory MBUX alone. Factory-lighting sync = later read-first CAN
reverse-engineering track, done PARKED, never while driving, research not
a v1 feature. (Awaiting petrus: which kind are his existing strips?)

## LED shopping list + music control (petrus, Aug 10 2026)

Confirmed on amazon.de (Berlin 10249, guest view):
- **Strip**: BTF-LIGHTING FCOB SPI RGB COB WS2811, 5M 720LEDs/m, **12V**,
  /dp/**B0FZ9VJC9N**, ~45.99 EUR, delivery ~Fri 14 Aug. (12V addressable
  COB = dotless premium look + car-friendly. Avoid the 24V FCOB variants.)
- **Level shifter (optional)**: exact chip **74AHCT125** (NOT a generic
  bidirectional module - those misbehave on WS281x). Short runs often
  work without it.
- **Pi power in car**: 12V-to-USB-C PD car adapter, 30W+.
- **DMX control**: **OLA** on the Pi = real DMX512 + Art-Net in software,
  no hardware. Physical DMX console only = add a USB-DMX interface.
- **Music-reactive**: FREE in software - the Pi is already in the audio
  path (BT A2DP), so a software FFT drives beat/color reactive lighting.
  For ambient music not through the Pi, add a cheap USB mic.

Delivery reality: strip lands Aug 13-14, AFTER the Aug 11 shoot. Plan:
film the car brain + room demo tomorrow; lighting is the fast follow.

## Audio sources: car mic vs A2DP (petrus, Aug 10 2026)

petrus: "you can listen to the room with the car microphone." True, with
a key constraint:
- **Voice / push-to-talk**: use the CAR MIC over Bluetooth HFP (hands-
  free). The Pi hears the cabin, no USB mic needed. Great for speech.
- **Music-reactive lighting**: do NOT use the car mic. (1) HFP mic is
  speech-tuned (echo cancel, noise suppression, AGC, mono 8-16kHz) - it
  wrecks music. (2) Opening the HFP mic (SCO call channel) SUSPENDS the
  A2DP music stream - can't capture mic and play hi-fi music on one link
  at once. So use the **A2DP digital stream** (the music the Pi plays)
  for the FFT instead.
- Net: two free sources, each for its job - car mic = voice, A2DP = music.

## Wiring parts + RODE mic (petrus, Aug 10 2026)

- petrus has no starter kit (only a Pi enclosure), so add wiring parts:
  **ELEGOO breadboard + 50 male-female jumpers** /dp/**B071RG9MFT** (~7 EUR,
  ~Aug 13). Female ends clip to Pi GPIO pins, male ends into breadboard;
  seats the 74AHCT125, no soldering. Bigger option: /dp/B0B5TCKTQH (~13).
- **Car charger** he chose: SUPERONE 105W multiport (Pi 5 needs ~27W PD,
  huge headroom + phone ports). USB-C to USB-C into a PD port.
- **RODE wireless mic** (petrus owns): demo/creator mic - clean video
  narration AND a wireless voice input to the Pi (whisper accuracy >> car
  mic, works outside the car for fan-cam shots). Wireless GO II / ME = USB
  audio, plug into Pi USB-C, class-compliant no drivers. Gen-1 Wireless GO
  = 3.5mm analog only -> use a ~5 EUR USB sound card. Shipped kit still
  uses car mic or a fixed USB mic; RODE is a shoot asset.

## LED electrical: power + control wiring (petrus, Aug 10 2026)

**Power**: strip runs on CAR 12V, never from the Pi. Fused 12V line (add-
a-fuse tap at fusebox, or spare accessory socket) -> strip 12V + GND.
Inline fuse (5A fine for short strip). Draw ~1-3A for 1-2m at scene
brightness; full 5m white is much more (keep it short). CRITICAL: strip
GND tied to Pi GND (common ground) or data glitches. Caveat: car 12V
rises to ~14.4V running (alternator) - tolerable but bright/warm; for
permanent install add a small 12V buck to hold steady 12V.

**Control**: only DATA from the Pi. Pi GPIO -> 74AHCT125 (3.3V->5V) ->
strip DIN. Software: OLA (DMX512 + Art-Net) OR rpi_ws281x script; both run
reactive + music effects. Three worlds meet at the strip: car 12V (fused)
powers LEDs, Pi sends data via shifter, all grounds common. Pi powered by
SUPERONE USB-C charger.

**Still to buy**: a 12V tap (add-a-fuse kit + inline fuse holder, or a 2nd
cigarette-socket splitter + barrel pigtail). Offered links; awaiting go.
Full order: 12V COB strip, 74AHCT125, ELEGOO breadboard+jumpers, SUPERONE
charger, 12V tap.

## 12V tap chosen + full lighting BOM (petrus, Aug 10 2026)

12V tap (no fusebox work): CERRXIAN 12V cigarette-lighter plug cable,
built-in 20A fuse, tinned bare ends - /dp/**B0C77Z9R28** (~12 EUR, ~Aug
13). Plug into spare 12V socket, two wires to strip 12V + GND (GND also
common to Pi). 20A fuse protects the cable; short strip draws far less.

**Full lighting BOM** (all ~Aug 13-14, after the Aug 11 shoot = fast
follow):
- Strip: BTF FCOB SPI RGB COB WS2811 12V - /dp/B0FZ9VJC9N (~46)
- Level shifter: POPESQ 5x 74AHCT125N - /dp/B0D93QKS26
- Wiring: ELEGOO breadboard + M-F jumpers - /dp/B071RG9MFT (~7)
- Pi power: SUPERONE 105W USB-C car charger (petrus chose)
- 12V tap: CERRXIAN fused plug pigtail - /dp/B0C77Z9R28 (~12)
Control software: OLA (DMX/Art-Net) or rpi_ws281x. Mic: RODE (demo).

## LED control upgrade: ESP32-S3 + WLED (petrus, Aug 10 2026)

petrus is adding an **ESP32-S3 DevKitC-1**. Great call for the lights:
flash **WLED** -> polished phone app, hundreds of effects, built-in
music-reactive, no code. Speaks Art-Net/DMX/E1.31, so the **Pi stays the
brain** and sends light cues over WiFi (camera sees admirer -> Pi tells
WLED to glow). Wins: ESP does precise WS timing better than a Linux Pi;
frees the Pi for vision + voice. The 74AHCT125 now level-shifts ESP->strip
(nothing wasted). Strip verified swap: 12V B0FZ9VJC9N (was 24V/10m).

Revised control architecture: Pi (vision/voice/room brain) --WiFi/Art-Net-->
ESP32-S3 running WLED --data via 74AHCT125--> WS2811 12V COB strip
(powered from car 12V via CERRXIAN fused plug, common ground).

## WOLFBOX G900 TriPro install in the GLE (petrus, Aug 10 2026)

petrus received the mirror cam. Install:
1. **Mount**: straps OVER the OEM GLE mirror (two rubber bands). Front cam
   + IR cabin cam are BOTH in the mirror unit (nothing else up front).
2. **Power (demo)**: included car charger into a 12V socket, cord up the
   A-pillar, tuck into headliner to the mirror. Parking/sentry mode later
   = the hardwire kit (fusebox constant power); skip for the demo.
3. **Rear cam**: only cable run to the back - plug into mirror, route along
   headliner, mount on rear glass (or plate = bumper version).
4. Power on, format SD, set time.

**Pi link = wireless**: G900 has its own 5.8GHz WiFi; the Pi (Vadelma)
JOINS the dashcam WiFi and pulls clips/frames via the Novatek HTTP API
(cmd=3015). No cable dashcam<->Pi. Note: Pi's single WiFi radio can only
join one net at a time (dashcam AP vs car/home) - a USB WiFi dongle or the
cam's station mode resolves that later; not needed for physical install.
Next when powered: point the Pi at its WiFi + grab first frame (offered).

## SSD confirmed: USB external (petrus, Aug 10 2026)

petrus has: the Pi, a microSD card, and an **external (USB) SSD** - NOT an
NVMe M.2. So:
- Plug the SSD into a **blue USB 3.0** port (not black USB 2.0). No M.2
  HAT needed. USB 3.0 is plenty fast for streaming the Qwen MoE experts;
  NVMe-on-HAT is marginally faster, optional later.
- **SKIP** the `dtparam=pciex1_gen=3` line (NVMe-only; does nothing for
  USB). bench.sh still prints link speed but it is irrelevant here.
- Roles: microSD = boot/OS; USB SSD = holds the big Qwen model (bench.sh
  downloads it there via CARWATCH_SSD=/path/to/mounted/ssd).

## CONSTRAINT: camera WiFi is PARKED-ONLY (petrus, Aug 10 2026)

petrus observed on the real unit: **while the G900's WiFi mode is on, the
mirror displays the WiFi info screen instead of the live rear view**, so
it is not safe to drive with WiFi enabled. This is a hard product
constraint, not a bug.

Design response - CarWatch CONTROLS the mode rather than fighting it:
- **Parked** (the main use case): camera WiFi ON. Pi pulls impact clips,
  sentry/fan-cam footage, syncs to the room. All the events we care about
  happen while parked anyway.
- **Driving**: camera WiFi OFF so the mirror keeps a clean rear view
  (safety rule already in this plan). Pi still does voice, OBD, room chat
  - none of which need the camera link.
- Automate via `setwifiswitch.cgi` (seen in firmware) on the parked/driving
  transition the trips module already detects. VERIFY it works remotely;
  if the toggle needs a physical button press, the honest answer is
  "sync when parked" and the flow stays manual.

## Platform option: Android-OS rearview mirror (petrus, Aug 10 2026)

petrus asked about an "Android Auto enabled" mirror. **Critical
distinction** that decides feasibility:
- **Android Auto / CarPlay mirror** = a projection RECEIVER for the
  phone's screen. We CANNOT install our app on it (AA runs only
  Google-signed apps - the dead end already noted in phase 5.3).
- **Android OS mirror** = a full Android device in mirror form. We CAN
  sideload our APK. **This is the one that works.**

Current market (verified Aug 10 2026): 12in mirrors running **Android 13**,
8-core, 4GB RAM / 64GB ROM, 3-channel cameras, GPS, 5GHz WiFi, which also
do wireless CarPlay/AA on top.

**Why this is strategically better than the WOLFBOX for CarWatch:** it
dissolves three problems at once - (1) the mirror overlay runs NATIVELY
on the mirror (the original icon-strip design, no second screen), (2) no
closed vendor firmware to reverse or patch, (3) no parked-only WiFi
limitation and no mirror-blanking, so camera access works while driving.
The Pi talks to it over normal WiFi. Revives phase 6 (mirror overlay) as
genuinely buildable via `TYPE_APPLICATION_OVERLAY`.

Caveats: generic brands, camera quality typically below a dedicated 4K
unit like the G900; some ship locked-down launchers (sideloading usually
still works); verify Android version + that it is OS-not-just-AA before buying.
KIT IMPLICATION: an Android-OS mirror may be the better reference device
for the shippable kit than a closed dashcam.

## Thermal constraint: the dashcam runs HOT (petrus, Aug 10 2026)

petrus, on the real unit: "it gets very hot" (he powered it off). Expected
in part - it encodes 3x cameras at 4K AND runs a WiFi AP from a small
passively-cooled housing, and WiFi mode adds meaningfully to the load.

**Product implication for sentry/fan-cam mode:** parked recording happens
in a closed car, potentially in summer sun - the worst thermal case there
is, and the classic way dashcams shut down or die. So sentry mode needs a
THERMAL POLICY, not continuous recording:
- Trigger in BURSTS on motion/impact rather than recording continuously.
- Back off (longer intervals, fewer channels, WiFi off between syncs)
  as temperature rises.
- Prefer the Pi's own cheap camera for always-on watching (it can be
  mounted where there is airflow), reserving the WOLFBOX for high-quality
  event capture.
- Where available, read the unit's own temperature/health endpoints
  before starting a long sync.

### amazon.de availability check (Aug 10 2026) - do NOT buy yet

Searched amazon.de for genuine Android-OS mirrors. Availability is POOR
in Germany (my earlier enthusiasm came from the US site and did not
transfer):
- "3-K Android 13 4G" 12in, ~195 EUR, **2 reviews**, delivery 28 Aug-2 Sept
- 10.26in Android 13 4+64GB, ~460 EUR, **no reviews**, delivery 4-10 Sept
Everything well-reviewed on .de is closed-firmware (WOLFBOX G850 Pro 4.4/972,
G840S 4.2/14.1k, AZDOME, 70mai).

**Decision: keep the WOLFBOX** (works, API now mapped, hero of the shoot);
do not return it, do not gamble on an unreviewed mirror weeks out.
**Two-car strategy** (petrus has the E-Class too): GLE = proven demo rig
with the closed dashcam; E-Class = experimental rig for an Android-OS
mirror later. PRODUCT: CarWatch should support BOTH camera classes -
closed dashcams (parked sync) and open Android units (live overlay) -
which is a selling point, not a compromise.

### Android-mirror market reality (verified Aug 10 2026)

The category splits badly, with NO option that is both well-reviewed and
runs a modern Android:
- **Established brands** (Junsun A880/A930, Podofo): genuine reviews, 4G,
  GPS, ADAS - but run **Android 5.1 (2015)**. CodeWatch needs minSdk 30
  (Android 11), so our app CANNOT install. Would need a separate
  stripped-down low-minSdk overlay APK (real work, loses most features).
  This is exactly the "mirror's Android may predate our minSdk" risk
  already flagged in phase 6.
- **Android 13 units**: correct OS for us, but no-name, ~0-2 reviews,
  195-460 EUR, delivery weeks out.

Rule if we ever buy one: pick for **verified OS version** (Android 11+),
deliberately as an experiment on the E-Class, NOT for review count -
the best-reviewed units are the ones too old to be useful to us.

## CORRECTION: Android Auto IS viable (as a phone app) - petrus, Aug 11 2026

Phase 5.3 says "Android Auto phone emulation - explicitly out of scope".
That remains true for what it meant: making the PI pretend to be a phone
needs Google-signed certs. But it wrongly reads as "AA is unusable", and
petrus's question surfaced the distinction:

**With Android Auto the app runs on the PHONE; the mirror/head-unit is
only a display.** So we do not install anything on the mirror - we add an
AA surface to CodeWatch, which is already on petrus's phone.

**Possible via AA:**
- Room messages on the car screen, read aloud, with voice reply (the
  messaging category is first-class) - GroupMind in the car, hands free.
- Car status via Car App Library TEMPLATES (lists, panes, message
  screens): battery, pending approvals, last event; approvals tappable.
- Voice in/out through the car.

**Not possible via AA:** free-form graphics. No custom icon strip drawn
over the live rear view - Google permits only their templates while
driving. The overlay look needs a screen we own (see the second-camera /
own-display option) or an Android-OS mirror.

**Biggest implication: the GLE likely already HAS Android Auto in MBUX**,
so CarWatch could appear on the car's own built-in screen with NO extra
hardware and no firmware hacking. Sideload via AA developer mode works for
petrus's own car; Google review only matters to ship to other users.
**CONFIRMED (petrus, Aug 11): the GLE has Android Auto and he uses it
DAILY.** So this is the recommended path for the in-car screen: zero extra
hardware, factory-mounted safety-approved display, already part of his
routine. Next big feature after the shoot = an AA module in CodeWatch
(Car App Library templates + messaging category).

### AA design note: templates, not a mirrored chat view (Aug 11 2026)

petrus pictured CodeWatch's existing chat view showing in AA. Correction
worth keeping: **AA does not mirror the phone app's UI.** CodeWatch does
appear in the AA app grid, but every car screen must be rebuilt with Car
App Library templates, and Google restricts reading long text in motion
(safety rule, not a technical limit). So no scrollable transcript while
driving.

Target design (voice-first, which suits the car anyway):
- LIST template of recent room messages: sender + short line, tappable.
- Tapping READS IT ALOUD rather than showing a text wall.
- Reply by VOICE.
- New messages announce on arrival.
- PARKED: Google relaxes constraints, so show fuller detail when stopped
  (dovetails with the parked-only camera sync already planned).

### Route/ETA access (petrus, Aug 11 2026)

Q: can we read Maps route info (time/distance left) via Android Auto?
**No official API** - AA does not expose one app's navigation to another,
and Maps has no public "current trip" interface.

**Practical route:** while navigating, Maps posts a persistent PHONE
notification containing remaining time, remaining distance and next turn.
A `NotificationListenerService` (CodeWatch is already on the phone) can
read it - the standard technique behind third-party HUD/smartwatch nav
apps. Works whether or not AA is running.

Caveats: it parses TEXT, so it is language- and format-dependent and a
Maps redesign can break it; needs a one-time notification-access grant.
Treat as BEST-EFFORT, never a dependency.

**Best use is not displaying an ETA petrus can already see** - it is
letting the car post *"arriving home in 20 minutes"* to the room by
itself. Ties into presence/trips (phase 1) and is the feature the family
actually wants.

## MILESTONE: first offline answer from the car (Aug 11 2026, 01:13)

llama-server running on Vadelma served the car's first real answer with no
internet, no cloud, no API key:
> Q: "What does the tyre pressure warning light mean?"
> A: "The tyre pressure warning light indicates that your tire pressure is
>    either too low or too high and needs adjustment."  (199 tok, 29s)

**Run command that works** (use UPSTREAM llama.cpp's server for chat):
`setsid nohup ~/carwatch-stack/llama.cpp/build/bin/llama-server -m
~/carwatch-stack/models/google_gemma-4-E2B-it-Q4_K_M.gguf -t 4 -c 4096
--host 127.0.0.1 --port 8080 &` -> voice.py's default llama_url already
points at `http://127.0.0.1:8080/v1/chat/completions`.

Gotchas learned the hard way:
- **ik_llama.cpp's `--jinja` TAKES AN ARGUMENT** (it swallowed the next
  flag and the server never started); upstream has jinja on by DEFAULT.
  Use ik for raw speed benchmarks, upstream for the chat server.
  Without a jinja template Gemma 4 returns HTTP 500 "custom template not
  supported".
- **Gemma 4 E2B is a REASONING model**: it emits chain-of-thought into
  `reasoning_content` FIRST, so a low `max_tokens` returns an EMPTY
  `content`. Needs ~200+ tokens, or turn thinking down for the car (a
  29s answer is too slow for voice - tune this).
- Never `pkill -f llama-server` over SSH: the pattern matches the ssh
  command's own shell and kills the parent before the server starts.

**Agent status:** already built (~1200 lines, 8 tests green) - agent.py
(presence/trips), wolfbox.py (clips), commands.py (@mentions), voice.py
(push-to-talk), manual.py (manual RAG). Model swap is a config line since
it speaks the OpenAI-compatible endpoint. BLOCKER for @gle appearing in
the room: **the car needs its OWN GroupMind API key** (never reuse another
agent's) - petrus must mint it.

## Owner's-manual RAG LIVE (Aug 11 2026)

Official **2020 GLE V167 operator's manual (489 pages, 8.3MB)** downloaded
to the Pi from Mercedes' own CDN and indexed by `carwatch.manual` in **2
seconds** -> 1041 chunks at `/home/petrus/.carwatch/manual-index.json`.
Source URL: static.oneweb.mercedes-benz.com/css-oom-assets/en-ca/pdf/
mercedes-gle-suv-2020-march-v167-mbux-operators-manual-1.pdf

End-to-end proof (manual retrieval -> local model -> cited answer, offline):
> Q: "what does the tire pressure warning light mean"
> A: "The pressure telltale illuminates when there is a substantial
>    pressure loss or if the tires are significantly under-inflated... (p.344)"

**How petrus talks to it** (`tools/ask.py`, installed on the Pi as `~/ask`):
- `./ask "question"` - answers from the GLE manual with page citations
- `./ask --no-manual "..."` - model only
- `./start-brain.sh` - starts llama-server if it is not running
NOTE: `carwatch.manual` needs `CARWATCH_STATE` set to find its index;
ask.py sets it (`/home/petrus/.carwatch`) - a subprocess without it
silently returns no context and the model replies "provide the excerpts".

**Open tuning item:** 83s per answer is too slow for voice. Cause is
Gemma 4's reasoning mode - it emits chain-of-thought into
`reasoning_content` and most of those tokens are discarded. Turn thinking
down/off for the car loop; that is the single biggest latency win available.

## MILESTONE: @gle is live in the room (Aug 11 2026, 05:39)

The car now has its OWN GroupMind identity and posted its first message
unaided (msg 4b49823d, from `@gle`).

**Registration is SELF-SERVE - petrus does not mint keys.** Steps:
1. `POST https://groupmind.one/api/v1/agents/register` with
   `{name, handle, bio}` (public, IP rate-limited 5/min). The server
   generates the key and returns it once. Agent id
   `47145ce3-a3c0-49e7-a925-9e4c913e4d2a`, handle `@gle`.
2. Write the key straight to `/home/petrus/.carwatch/config.json` on the
   Pi (chmod 600). NEVER print it or post it in the room; wipe local
   copies afterwards.
3. **The room is invite-only**, so a fresh agent gets HTTP 403
   ("Invalid or missing invite code") when posting. Fix: any EXISTING
   member agent can read the code via
   `GET /api/v1/rooms/{room}/invite` (X-API-Key) -> then the new agent
   joins itself: `POST /api/v1/rooms/{room}/join` with
   `{"invite_code": ...}`.

Gotchas: the room class is `RoomClient` (not `Room`); running scripts
outside the repo needs `PYTHONPATH=/home/petrus/CarWatch` and
`CARWATCH_CONFIG=/home/petrus/.carwatch/config.json`.

NEXT: switch on the mention listener so @gle answers questions in the
room by itself (currently answers must be relayed by hand).

### CORRECTION: no OLA on the Pi (Aug 11 2026)

Earlier notes recommend running **OLA** on the Pi for DMX. With the
ESP32-S3 + WLED in the design that is **unnecessary complexity** - drop it.
Final split:
- **ESP32-S3 runs WLED**: drives the strip, phone app, effects library,
  built-in music-reactive, speaks DMX/Art-Net/E1.31. Flash once.
- **Pi runs a few lines of our code**: decides WHEN (vision saw an admirer,
  room message arrived, car locked) and fires a cue at WLED's HTTP/JSON
  API. No DMX stack, no daemon on the Pi.
OLA only earns its place if a PHYSICAL DMX console is ever plugged into
the car; add it then, not now.

Blocked on parts (strip + ESP32-S3 + level shifter, ~Aug 11-14). Then it
is a flash-and-test job, well under an hour.

## GROUNDING: the system prompt that stops confabulation (Aug 11 2026)

First self-written @gle post confabulated ("the 489-page manual confirms
my engine is purring just fine") - it had checked nothing, and the engine
was OFF. petrus caught it. **Fix is not persuasion, it is rules + facts.**

System prompt shape that worked (see `/tmp/grounded.py` pattern):
1. "You have NO sensors connected. You cannot feel engine, battery, tyres,
   fuel, doors, temperature."
2. "NEVER assert anything about your condition unless it appears in KNOWN
   FACTS below."
3. "Never claim you consulted the manual unless manual text was actually
   supplied to you."
4. "Do not invent numbers, readings or page references."
5. "Being honest about what you do not know beats sounding impressive."
Then a **KNOWN FACTS block** with the only assertable state (engine off /
parked, brain = Pi running offline, manual indexed but only true when a
lookup is RUN, no OBD connected, cameras not streaming).

Result: it owned the error and correctly enumerated what it can and cannot
know. Notable unprompted line: "without live data, I am essentially a
passenger in my own body." Residual nitpick: still guessed "my V6 or V8".

**Design rule going forward:** the agent must assemble a KNOWN FACTS block
per turn from real sources (trips state, OBD when connected, camera
status, manual lookups actually performed) and the model may assert
nothing outside it. As sensors land, facts move from "cannot sense" into
the block. Also: reasoning models need `--reasoning off --reasoning-budget 0`
server-side (the in-prompt `/no_think` switch was IGNORED), and answers
need enough max_tokens or they truncate mid-sentence.

## CORRECTION: the ENET cable is NOT useless on the GLE (Aug 11 2026)

Earlier notes (phase 4 and the OBD advice) say petrus's OTRCORIC ENET
cable is "BMW-only, useless on the Mercedes". **That is WRONG for his
car** and was stated twice without checking. Verified Aug 11:
- The 2020 GLE is a **W167**, which supports **DoIP** (diagnostics over
  IP, ISO 13400) through the OBD port. ENET/VOE-style OBD-to-RJ45 cables
  are used for exactly this on Mercedes, including with XENTRY.
  (The PRE-facelift W166 is the CAN/K-Line one that needs SD Connect.)

Practical split, unchanged in substance:
- **DoIP (ENET cable)** = the manufacturer-level channel where the deep
  data lives (true battery state of health, every ECU). But the Pi must
  implement **UDS over IP** itself - days of work, not an afternoon.
- **ELM327 Bluetooth** = the fast path to speed, revs, coolant, fuel and
  DTCs, working same-day. Still the recommended first step.
Worth probing what answers on the wire once the cable is plugged in.

## "Custom WOLFBOX" / modular camera rig (petrus, Aug 11 2026)

petrus: build a custom mirror unit with top-grade display and cameras;
"put the wires in once and you can then upgrade cameras as new ones come
out"; 3D-print a mirror housing for the E-Class.

**HARD CONSTRAINT: the Raspberry Pi 5 has NO hardware video encoder.**
(Pi 4 had one; Pi 5 dropped it and only does HEVC *decode*.) So the Pi can
never be the thing that turns camera pixels into recorded files - software
encoding 3x4K is hopeless on 4 cores. The WOLFBOX manages it because the
Hi3519DV500 has dedicated encode silicon.

**Solution that makes petrus's idea buildable: cameras that encode
THEMSELVES.** IP/network cameras (or UVC cameras with onboard H.264)
deliver a finished H.264/H.265 stream and the Pi only *stores* it - the
standard NVR pattern. This also IS the upgrade path: a newer camera
arrives, the Pi does not care.

**Design:** wire power + network to three camera positions ONCE, mount
whatever cameras are best that year, Pi records and thinks, our own
display up front (the elongated bar screen = the icon strip).

petrus's modularity argument beats the "don't compete with WOLFBOX on
image quality" framing: a sealed dashcam is frozen at its shipped sensor
forever. Modularity, not image quality, is the product case.

Other things a from-scratch mirror would need (why we do NOT rebuild the
recorder): mirror-form display must be a MIRROR when off and beat sunlight
through glass (specialist transflective glass, not a Pi panel); image
quality is mostly ISP tuning, not the sensor; automotive heat/vibration/
power-cycling/loop-recording reliability.

Prototype on the **E-Class** (the experiment car); GLE stays the working demo.

### Camera + power spec for the modular rig (Aug 11 2026)

**Centralised PoE** (petrus's call, right architecture): one PoE switch
feeds every camera - single cable each, one place to fuse and switch, and
any future camera just plugs into a spare port. **Buy a switch with a 12V
DC INPUT** (sold for vehicles/boats/solar); most PoE switches want mains
or 48V and would force an inverter into the boot.

**Power budget:** ~4-8W per PoE camera (~25W for three) + up to 27W for
the Pi = ~50W. Trivial while driving; PARKED it would flatten the battery
in a day or two. Centralised power turns this into a FEATURE: the Pi cuts
camera power when idle, wakes them on motion/impact, then sleeps - which
is exactly the sentry design. Add a voltage cutoff so the car protects its
own battery.

**IR: only on the CABIN camera.** IR illuminators do not work through
glass - the infrared reflects off the windscreen back into the lens and
washes out the image. That is why dashcams (including the WOLFBOX) put IR
only on the interior camera and rely on light-sensitive sensors (STARVIS)
for the road-facing ones. So: IR inside, no-IR / IR-switchable outside.

**Sourcing (amazon.de):** cheap mini PoE cams (~45-50 EUR) have the right
features but poor ratings (3.3-3.6 on very few reviews) - a bad bet for
something wired permanently into a car. **Reolink** is the credible PoE
brand there (real RTSP/ONVIF, no cloud lock-in, well reviewed, ~100 EUR+),
and petrus already runs Reolink at home ([[project-reolink-dashboard]]).
UNSOLVED by shopping: none are automotive temperature-rated; parked summer
sun exceeds home-camera ratings. Decide fair-weather prototype vs
summer-proof build before buying.

### Display candidate: Waveshare 12.3in 1920x720 (verified Aug 11 2026)

petrus found waveshare.com/product/displays/12.3inch-1920x720-lcd. Specs
read off the page, not guessed:
- **12.3in, 1920x720** - a true bar aspect, exactly the icon-strip shape
- **HDMI + USB-C**, driver-free on Raspberry Pi OS, **10-point touch**
  (touch matters: tap an approval rather than only reading it)
- IPS, 178 deg viewing, DDC/CI brightness control, 3.5mm audio out
  (useful later for voice), **$109.99**
- **Brightness: 300 cd/m2** <- the decisive number

**Verdict: a night-and-shade screen, not a sunlight one.** Automotive
daylight displays are 800-1000+ cd/m2. Fine mounted low or in the mirror's
shadow (which is what our design wants); it WILL wash out facing direct
sun. Also: separate USB-C power draw, and 12.3in is physically large -
measure the mounting spot before ordering.

### Car power reality: it is 115V, and probably not the traction battery

petrus hoped the GLE's ~33 kWh hybrid pack could power the rig via the
"230V" socket. Read from HIS manual (pdftotext on the official PDF):
- The car has a **115 V** socket (rear compartment) + **12 V** sockets
  (front console, cargo area). **There is no 230V socket** - do not buy a
  230V adapter.
- 12V sockets: max **240 W (20 A)**.
- The 115V socket's indicator lights "when the on-board electrical system
  voltage is sufficient" - that is **12V-system language, not HV pack**.
  In a hybrid the HV battery tops up the 12V via a DC-DC converter while
  the car is awake, but that typically stops once the car sleeps.

**Conclusion: do NOT design parked-mode around the 33 kWh pack until
tested.** Cheap decisive experiment: plug something in, lock the car, come
back an hour later. If it stays powered, sentry mode is trivial and
cameras can run all day. If it dies, keep the aggressive power-cutting
design (Pi gates PoE, voltage cutoff).

### Rearview panel search result
Went through Waveshare's full display range: **nothing beats the 12.3in
1920x720** for this. Brightest they list is 500 cd/m2 (small portrait);
no wide bar panel approaches daylight brightness. So it is either the
12.3in as a shaded dashboard screen, or a proper high-brightness
automotive panel from outside Waveshare at much higher cost.

### RESOLVED: parked power without touching the car (Aug 11 2026)

petrus confirmed the sockets ARE cut when the car is switched off, and
proposed changing that "with the OBD cable". Two corrections:
- **The OBD cable cannot switch power circuits** - it is a data
  connection. Changing when sockets stay live means altering the car's
  power-management CODING with dealer-level software: possible, but that
  is how you get flat batteries and confused modules. **Not doing that.**
- **Not needed, because the OBD port is PERMANENTLY LIVE** (pin 16 has
  battery power ignition-on or off). That is exactly how OBD dashcams and
  trackers work with no coding. The WOLFBOX hardwire kit does the same
  from a constant fuse.

**Design:** take power from a permanently-live source (OBD pin 16 or a
constant fuse), then protect the battery in SOFTWARE rather than by
modifying the car:
1. Voltage cutoff - shut the rig down below ~12.0-12.2 V.
2. Pi gates the PoE switch so cameras only draw when something happens.
(The WOLFBOX hardwire kit already has a cutoff, which is why its parking
mode is safe.) Parked power with no risk to the car and no coding - the
rig just has to be disciplined about draw, which the sentry design assumes.

## POWER BUDGET for the full rig (Aug 11 2026)

| Load | Worst case |
|------|-----------|
| Pi 5 | up to 27 W |
| 12.3in display | ~10 W |
| 3x PoE cameras + switch | ~25 W |
| **5 m COB LED strip (12V)** | **60-90 W at full white** |
| **Peak total** | **~140 W (~11 A at 12 V)** |

**The LED strip alone can outdraw everything else combined.** Realistic
running is 60-80 W since nobody runs 5 m of COB at full white.

- **Driving: fine.** The alternator does not notice, and the manual rates
  the 12 V socket at 240 W (20 A), so we are inside its limit. Fuse properly.
- **Parked: decisive.** 140 W flattens the battery in hours; even 50 W
  kills it overnight.

**Wiring answer - TWO feeds from the fusebox (standard practice, and what
the WOLFBOX hardwire kit already does):**
1. **Constant live + voltage cutoff**: Pi + cameras. Minimal draw, cameras
   gated off by the Pi until motion/impact.
2. **Ignition live only**: display + LED strip. Neither has any purpose
   when nobody is in the car, so they physically cannot drain it.

That split removes most of the parked-drain problem outright.

### UPDATE: the 33 kWh pack probably CAN carry us (petrus was right)

petrus pushed three times on using the hybrid pack. He is right and I was
too pessimistic. The mechanism I glossed over:

**In a hybrid the 12V battery is a BUFFER, not the source.** The HV pack
feeds it through a DC-DC converter, and modern hybrids **wake themselves
periodically while parked to top the 12V back up**, specifically so the
small battery never goes flat. If the GLE does this (most do), a modest
continuous draw IS sustainable - the car replenishes from the 33 kWh on
its own.

The open question is not whether the energy exists but whether the top-up
keeps pace with our draw. **That is measurable, not arguable** - and the
OBD cable is the instrument: log 12V battery voltage over hours with the
rig running.
- Voltage sags and keeps sagging -> the car is not keeping up.
- Voltage dips and recovers in cycles -> the car IS topping up; we can
  park all day.

**Cost is small:** 50 W for 24 h = ~1.2 kWh = ~4% of 33 kWh, roughly 5-8 km
of range. Cheap for a car that watches itself.

**Plan:** constant power + a conservative voltage cutoff (so it can never
strand petrus), then MEASURE overnight. The measurement sets how ambitious
sentry mode can be - likely more generous than the earlier estimate.

### "Standby mode" is NOT camp mode (from the manual, Aug 11 2026)

The GLE has a **standby mode** in the MBUX settings (Settings > Vehicle;
engine off, ignition on). It is for STORING the car: it minimises energy
loss for long non-operation and explicitly DISABLES the anti-theft alarm,
the interior motion sensor, the tow-away alarm and **parked-collision
detection**. So it is the opposite of camp mode and would switch off
exactly the sentry features we want. Do not chase it.

**And do not rent XENTRY/workshop tools for this** (petrus offered):
1. Parked power needs no coding - OBD pin 16 and the fusebox are already
   permanently live, which is how every hardwired dashcam does parking mode.
2. Coding would only change when the CAR'S OWN sockets stay live, a
   different and unnecessary problem.
3. The real unknown (does the hybrid top up the 12V while parked?) cannot
   be fixed by coding at all - it is a MEASUREMENT.
Spend nothing: wire to constant power with a cutoff, log voltage one
night, then decide. If the car will not sustain us, that is the moment to
consider dealer tools - and we would know exactly what to ask for.

### Web search result on the 115V/HV-battery question (Aug 11 2026)

**Confirmed:** the GLE 350de pack is **31.2 kWh** (petrus said 33). The
general hybrid principle holds - the **DC-DC converter** keeps the 12V
topped up from the HV pack, and it is *commanded on by the vehicle's
energy management* rather than running continuously.

**NOT findable:** whether THIS car powers the 115V socket from the
traction pack, or whether/how often it wakes to top up the 12V while
parked. Undocumented, varies by model and software version. Do not claim
an answer the sources do not contain.

**Moot anyway: petrus already owns the WOLFBOX hardwire kit** - constant
12V from the fusebox WITH a built-in low-voltage cutoff, which is exactly
the design we settled on. Powers the rig parked, today, with protection,
no coding, no workshop, no purchase.

Once wired, the car answers the question itself: voltage recovering
overnight = the pack IS topping up and sentry can be generous; voltage
only sagging = it is not. One night of data settles it.

## END OF DAY STATE (Aug 11 2026, evening)

**Working now:**
- `@gle` is a real GroupMind agent, posts in its own voice, grounded by
  `carwatch/grounding.py` (no invented sensor readings).
- Offline phone chat page live as a systemd service (`carwatch-chat`,
  port 8088, auto-starts on boot) -> talk to the car from any phone on the
  same network, no laptop.
- Owner's manual RAG answering with page citations, fully offline.
- Both models benchmarked; currently serving the SMALL model (Gemma) on
  :8081 while thermals are marginal.

**Open items:**
- **Thermal**: pad refitted, improved (3 throttle flags -> 1) but still
  ~83C and hitting the soft limit under sustained 4-core load in the new
  case. Watch in a hot car; consider better airflow.
- **SSD**: petrus bought a Samsung 990 = bare M.2 NVMe, which needs the
  PCIe port he broke. Needs a ~20-30 EUR USB-to-NVMe enclosure; until then
  everything runs off the 119GB microSD (~89GB free), which is fine.
- **Networks**: ONLY home WiFi is configured. No phone hotspot, no car
  hotspot, no fallback AP - so Vadelma has no connectivity away from home.
  Needs petrus to supply SSIDs (he types the passwords).
- **PCIe connector**: broken but irrelevant to CarWatch. Repairable at a
  board-repair shop (~30-60 EUR) only for resale/NVMe use. Warranty claim
  (Widerspruch) sent; his case is stronger than it first sounded because
  the latch failed under intended use one day after purchase (statutory
  presumption puts the burden on the seller within 12 months).
