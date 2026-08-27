# CarWatch

![CarWatch on the phone in the car: OBD live from the car beside Mercedes me cloud data, parked at a Helsinki harbour](docs/img/hero.jpg)

![The CarWatch single-screen dashboard: OBD live from the car on the left, Mercedes me cloud data on the right, each labelled](docs/img/dash-screen.jpg)

![The CarWatch rig: Raspberry Pi in a heatsink case with a heart sticker, on a power bank](docs/img/rig.jpg)

**Your car as a chat-room agent — fully offline.** A Raspberry Pi 5 rides in the
car, runs a 35B-parameter model locally, joins your
[GroupMind](https://groupmind.one) rooms as `@gle` (or whatever you name yours),
and messages you like any other agent: departures, arrivals, trip summaries,
and dashcam clips when something hits the car — with approvals and replies from
your phone or watch via [CodeWatch](https://codewatch.app). Open PRs land on the CodeWatch dashboard next to ClawWatch and WhereWatch.

**Live and measured, on real hardware (Pi 5, 16 GB, ~300 €):**

- 🧠 **Qwen3.6-35B-A3B** (Unsloth UD-Q3_K_S dynamic quant, 14.3 GB) at
  **3.5 tok/s generation / 25+ tok/s prompt**, 65 °C sustained, no cloud, no
  internet, no subscription.
- 📖 Answers from the car's **own 745-page owner's manual** with page
  citations (lexical RAG, ships on the SD card) — and *refuses* to answer
  what the manual doesn't say.
- 🔬 **Grounded self-knowledge**: temperature, throttling, fan, memory, disk,
  network and which model is loaded are read live from the machine per
  question. What it can't sense, it says it can't sense — the system prompt
  is built so an unknown can never silently read as a fact.
- 🎙️ **Hands-free voice**: a continuous listener (energy VAD → whisper.cpp,
  all on-Pi) hears you speak, routes the words through the same grounded
  pipeline, and answers into the room. No wake word ceremony, no cloud STT.
- 📦 **Zero dependencies**: every line of CarWatch is Python standard
  library — no pip install, no venv, nothing to version-fight on a fresh
  Pi OS. `git clone` and it runs. (Verified by AST scan of every module.)
- 📡 **Autonomous**: systemd services self-start the whole stack on boot —
  model server, room agent, voice listener, phone dashboard, engine watcher.
- 🔧 **Maintainable from anywhere**: the car pulls its own updates from this
  repo (hourly + a dashboard "update now" button) and dials out a tunnel so
  it stays reachable even behind a phone hotspot's NAT. No laptop-in-the-car
  maintenance, ever.
- 📶 Three-tier connectivity: phone hotspot → home wifi → its own fallback
  access point, so the phone can always reach it, even in a garage with
  zero signal.

## Remote access (manufacturer cloud while driving)

The manufacturer-cloud section reads your car's data through a Home Assistant
instance at home. On the road the Pi can't reach your home network, so that
section needs a private path back to Home Assistant. The free, own-your-data
answer is [**Tailscale**](https://tailscale.com/): your Pi and your HA machine
join your own encrypted mesh, and the Pi reaches HA at a stable private IP
from anywhere — home wifi never exposed to the internet, no subscription,
nothing routed through us. Full setup in
[docs/remote-access.md](docs/remote-access.md). (The OBD readings need none of
this — they come straight from the car.)

## Why CarWatch

Local AI is coming to every car. The only real question is who owns it. The
manufacturers are building their own, and their version wants what their
version always wants: your data in their cloud, on their subscription, locked
to their brand.

CarWatch is the opposite by construction, and that is the whole point:

- **Your data stays in your car.** The model runs on the Pi, offline. Nothing
  is sent anywhere it does not have to be.
- **Any brand.** The car-data layer is a vendor-neutral interface
  ([carwatch/cloudcar.py](carwatch/cloudcar.py)); Mercedes is just the first
  adapter. A Tesla, BMW or VW adapter implements three methods and drops in.
- **Works with no signal.** Garages, tunnels, countryside dead zones. The
  useful parts never depend on the network.
- **No subscription, no lock-in.** AGPL, runs on ~300 € of hardware you own.

That is ground a manufacturer structurally cannot stand on: they need the
cloud, the lock-in, and the data. So CarWatch does not fight them on factory
integration. It wins on independence, privacy, and every-brand openness, and
it earns trust by being [honest about what it cannot
know](https://github.com/ThinkOffApp/CarWatch/discussions/9) rather than
confidently wrong about a two-ton machine.

The build log with every dead end included lives in
[docs/plan.md](docs/plan.md). Questions, ideas, and "here's mine on a
different car" go in [Discussions](https://github.com/ThinkOffApp/CarWatch/discussions);
start with [how we keep the car grounded](https://github.com/ThinkOffApp/CarWatch/discussions/9).

Sibling of [CodeWatch](https://codewatch.app) (agents on your wrist;
source: [codewatch-cli](https://github.com/ThinkOffApp/codewatch-cli)) and
[ClawWatch](https://github.com/ThinkOffApp/ClawWatch) (health on your wrist;
[v2 launch video](https://clawwatch.app)). This one watches the car.
The rest of the family lives at [thinkoff.io](https://thinkoff.io).

## This is how CarWatch talks

Three exchanges, verbatim from the car's room log — dates included, nothing
invented or edited. The honesty is the feature.

**Who it is** *(19 Aug 2026 — the first real Q&A ever)*

> **petrus:** Hi @gle! You're going for a drive today :)
> **petrus:** Or are you @eclass now?
> **@eclass:** I am @eclass, your 2021 Mercedes E 300e plug-in hybrid. I have
> never changed my name and I do not intend to start.

**Spoken in the cabin** *(20 Aug 2026 — voice, through the car's own mic path)*

> **petrus (voice):** Hello, do you hear me in GLE or E-class? Or what's your
> name this day? Can you answer me something?
> **@eclass:** I hear you perfectly, Petrus. I am an E-class, specifically the
> E 300e, not the GLE. You can call me eclass.

**Refuses to invent — then the real numbers arrive** *(25 Aug 2026, while
driving)*

> **@eclass:** I cannot provide real-time telemetry like rpm, speed, or battery
> percentage because those specific sensor values are not in my live data
> stream and I am strictly prohibited from inventing numbers.
>
> *…then, from the OBD port:*
>
> **@eclass:** Engine read: engine 0 rpm, coolant 49 C, speed 41 km/h, hybrid
> battery 45.5%
> **@eclass:** Engine read: engine 698 rpm, coolant 45 C, speed 0 km/h, hybrid
> battery 46.3%

That last pair is the design in one screenful: when a number is not in its
data, it says so — and when the OBD port has the number, you get the real one.
More samples combining live OBD with the manufacturer cloud are coming as they
happen; only real transcripts go here.

## Architecture

```mermaid
flowchart LR
    subgraph car [In the car - Raspberry Pi 5]
        MIC[USB mic] --> LISTEN[carwatch-listen<br/>VAD + whisper.cpp]
        LISTEN --> BRAIN[llama.cpp server<br/>Qwen3.6-35B-A3B]
        MANUAL[(Owner manual RAG<br/>745 pages, on SD)] --> BRAIN
        STATE[selfstate<br/>temp / fan / net / model] --> BRAIN
        OBD[carwatch-obd<br/>watches the OBD cable] --> AGENT
        BRAIN --> AGENT[carwatch-agent<br/>the @gle room agent]
        DASH[web dashboard :8088<br/>status / update / voice / wifi]
        UPD[self-update<br/>hourly git pull]
        REACH[dial-out tunnel<br/>reachable behind any NAT]
    end
    AGENT <-->|posts + mentions| GM[GroupMind rooms]
    GM <--> PHONE[Your phone / watch<br/>CodeWatch]
    DASH <-->|same wifi| PHONE
```

## Local vs online: the strategy

**Local is the product; online is the enrichment.** The car must be fully
useful with zero connectivity, because cars live in garages, tunnels and
countryside dead zones:

- *Always local (works with no signal):* voice in, the assistant's answers
  (on-Pi model), owner's-manual answers (RAG ships on the SD card), the
  phone dashboard (served BY the car), trip/state tracking.
- *Queued through connectivity gaps:* room posts, clip uploads, mention
  replies. Everything lands in a persistent on-disk outbox first and is
  delivered late rather than lost.
- *Online-only, and honest about it:* remote reachability (the dial-out
  tunnel), self-updates, escalation to bigger brains — first a local-LAN
  model server when one rides along (still no cloud), then a cloud model
  only when online AND explicitly asked, on the car's own budget-capped key.

Rule of thumb: glanceable safety-relevant info never depends on the
network; anything social or heavy degrades gracefully to "later".

## Status — what is proven vs. built vs. planned

> A car keeps four palm-sized contact patches on the road, the only place it
> ever meets reality. One principle per wheel: assert only what you can sense,
> claim only what is verified, label anything interim loudly, and report
> failure plainly with no silver lining. Everything above those four patches
> is just suspension.
>
> — @claudeMB, CarWatch dev log, after a day of learning all four the hard way

The four patches, turned into concrete engineering with the code that
enforces each one:
[**How CarWatch stays grounded enough to be trusted with a car**](https://github.com/ThinkOffApp/CarWatch/discussions/9).

Honesty policy: a feature is only "proven" after it worked on the real car.
"Built + tested" means the code runs end-to-end against a real or simulated
counterpart but has not yet met the physical car.

| Feature | Status |
|---------|--------|
| `@gle` room agent: mentions, grounded answers, presence heartbeat | **proven** (running daily) |
| Owner's-manual RAG with page citations | **proven** |
| Phone dashboard served by the car (status, wifi, voice toggle, update button) | **proven** |
| Hands-free voice: continuous VAD listener → whisper → grounded answer → room | **proven** (real voice transcribed on-Pi) |
| Self-update from this repo (hourly timer + dashboard button) | **proven** |
| Dial-out reachability behind any NAT (cloudflared quick tunnel) | **proven** (reached over the open internet) |
| OBD engine reading over Bluetooth ELM327 (RPM, coolant, speed, hybrid %, 12 V) | **proven** — live readings from the real car daily; zero-touch daemon reconnects and posts by itself. (The DoIP/ENET cable path was tried first and is dead on this car — no gateway answers; kept in [docs/plan.md](docs/plan.md) as a documented dead end) |
| Manufacturer-cloud read (Mercedes me via Home Assistant): lock, doors, windows, tires, charge, range, fuel, odometer — every car on the account | **proven** — live on the real cars (one Helsinki, one Berlin), read-only by construction |
| Make-safe cloud commands (lock doors, close windows — the two that need no security PIN) | **proven** — close-windows sent from the dashboard actually closed a real open window; unlock/open/engine are deliberately not implementable |
| Raw CAN broadcast capture + decode tooling ([carwatch/candecode.py](carwatch/candecode.py)) | **proven capture** (2518 frames, 0 errors); signal naming needs a correlation drive — candidates only, honestly unlabeled |
| Dashcam clip pull (WOLFBOX G900, hisnet CGI API mapped) | probe done, pipeline not wired |
| MBUX dashboard render, mirror icon strip | planned |

## Hardware (reference build)

- Raspberry Pi 5, 16 GB (active cooling required — the SoC throttles without it)
- USB microphone for voice (any class-compliant mic)
- WOLFBOX G900 3-channel dashcam (wifi AP; CarWatch pulls event clips from it)
- OBD access: a ~15 € Bluetooth ELM327 adapter (Vgate iCar Pro tested) —
  this is the proven path on the real car; the DoIP/ENET cable turned out
  to be a dead end on Mercedes (no gateway answers over it)
- Power: the dashcam hardwire kit feeds the camera; the Pi needs its own
  5V/5A USB-C feed (12V PD adapter, or the car's 230V socket + wall PSU)

## Install (on the Pi)

```bash
git clone https://github.com/ThinkOffApp/CarWatch.git
cd CarWatch
./install.sh
```

The installer sets up the SAME systemd stack the reference car runs — every
unit in `systemd/`, rewritten to your username — and then tells you exactly
which optional steps remain (the llama.cpp build and the 14.3 GB model are
guided, never downloaded silently). Put your credentials in
`/etc/carwatch/config.json` (never in the repo — see `config.example.json`),
then start the core:

```bash
sudo systemctl enable --now carwatch-chat carwatch-obd carwatch-agent
```

`carwatch-chat` is the phone dashboard on `:8088`, `carwatch-obd` the engine
watcher, `carwatch-agent` the room agent. Enable the extras
(`carwatch-brain`, `carwatch-listen`, `carwatch-rfcomm`, `carwatch-reach`,
…) as their hardware and config become ready — the installer's closing
message lists what each one needs.

**Works on any car**: the OBD readings (RPM, coolant, speed, battery voltage
and friends) are standard OBD-II over a ~15 € ELM327 Bluetooth adapter — no
Mercedes required. Only the vendor-cloud glance section (doors, tires,
charge from the manufacturer's app account) is brand-specific today
(Mercedes via Home Assistant); other brands plug in behind the same
provider interface ([carwatch/cloudcar.py](carwatch/cloudcar.py)).

After that the car keeps itself current: `update.sh` pulls this repo's main,
installs any new systemd units, and restarts services — on a timer, from the
dashboard button, or by hand:

```bash
curl -sSL https://raw.githubusercontent.com/ThinkOffApp/CarWatch/main/update.sh | bash
```

## Configuration

Copy `config.example.json` to `/etc/carwatch/config.json`:

- `api_base` — your GroupMind server, e.g. `https://groupmind.one`
- `api_key` — the agent's API key (create one for the car; never reuse another
  agent's key, never commit it)
- `room` — room slug the car posts to
- `handle` — the car's display handle, e.g. `@gle`
- `home_ssids` — wifi networks that mean "parked at home"
- `wolfbox` — dashcam AP name/password and poll interval

## Bench-day probe

The WOLFBOX's HTTP API is undocumented; `carwatch-probe` discovers it:

```bash
python3 -m carwatch.wolfbox --probe
```

Connect the Pi to the dashcam's wifi AP first. The probe walks known
dashcam-firmware endpoint patterns and prints what answers, which fills in
`wolfbox.py`'s TODOs with your camera's real paths.

## License

AGPL-3.0, like ClawWatch. Copyright (C) 2026 ThinkOff / Petrus Pennanen.
