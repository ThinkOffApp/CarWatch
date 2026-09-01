# CarWatch


## 🎬 v0.4: talk to your car — watch the release video

[![The v0.4 release video: three spoken questions answered by the car itself, the last one fully offline](https://github.com/ThinkOffApp/CarWatch/releases/download/v0.4.0/carwatch-v04-poster.jpg)](https://x.com/petruspennanen/status/2093404279000141869)

Three questions asked by voice in the driver's seat and answered out of the
car's own speakers — status brief, the yellow tyre light with real pressures,
and E10 fuel from the manual. The last one with the internet switched off.
[Watch on X](https://x.com/petruspennanen/status/2093404279000141869) ·
[download the video](https://github.com/ThinkOffApp/CarWatch/releases/download/v0.4.0/carwatch-v04-web.mp4) ·
[v0.4.0 release notes](https://github.com/ThinkOffApp/CarWatch/releases/tag/v0.4.0)

![The CarWatch dashboard in v0.4: the Speak strip on top, live OBD tiles, Mercedes me cloud data, and the full control dock](docs/img/dash-screen.jpg)

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
- 📖 Answers from the car's **own 489-page owner's manual** with page
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

## 🧠 Swap the car's brain from your phone (v0.5)

Different questions want different brains. "What does the tyre light mean?" is
a 489-page-manual question that deserves the 35B. "How fast am I going?" is not
— and on a Pi the small model answers it six times quicker. So the dash grew a
**Model zone**: every `.gguf` on the box, listed with the speed it actually
reached *on this Pi*, and one tap to make it the running brain.

![The CarWatch dashboard's Model zone: seven local models listed with their measured tokens per second, the running one marked in green, and a swap in progress with an honest load estimate](docs/img/model-chooser.jpg)

The numbers next to each model are `llama-bench` runs from the device itself,
not figures from someone's blog — the same hardware, the same quant, measured
the same way, so the choice you are making is a real trade and not a guess.
The reference Pi 5 16GB table (29 Aug 2026, 4 threads, prompt pp512 /
generation tg128 in tokens per second):

| model | size | prompt | generation | call |
|---|---|---|---|---|
| Gemma 4 E2B Q4_K_M | 3.5 GB | 29.9 | 6.2 | speed pick |
| Gemma 4 E4B QAT Q4_0 | 5.2 GB | 30.2 | 3.6 | best balance |
| Ornith 1.5 9B (dense) | 5.8 GB | 9.1 | 2.0 | out — dense is slow here |
| Qwen3.6 27B dense IQ2_M | 10.8 GB | 0.7 | 0.5 | out — IQ2 is compute-bound on Pi CPUs |
| Ornith 1.5 35B MoE IQ3_XXS | 13.7 GB | 8.3 | 2.8 | quality untested |
| Qwen3.6 35B MoE Q3_K_S | 15.4 GB | 9.1 | 2.9 | the quality pick, default brain |

Sizes are decimal GB — `size / 1e9`, the same number the dash shows you and
the same number `ls` gives for the file. The table grows as models get benched on the device; the menu always shows
whatever the box has measured for itself.

Three guard rails, because this runs while you drive:

- **A model that cannot fit in RAM is refused**, with the reason, instead of
  loading until the kernel kills something.
- **A swap never interrupts an answer being generated.** Ask first, swap after.
- **A failed restart rolls back** to the model that was working.

Loading is honest about itself: a 14 GB model off a microSD card takes about
three minutes, so the dash says so and counts, rather than showing a spinner
that means nothing. The estimate is derived from the size of the model actually
being loaded.

Underneath it is two endpoints behind the same auth gate as every other route
— `GET /api/models` for the registry and current state, `POST /api/model` for a
guarded swap — and the service reads its model from an `EnvironmentFile`, so
switching is a file write plus a restart rather than an edit to a unit file.
That contract is deliberately small and device-agnostic: it is the first
[CodeWatch Fleet](https://github.com/ThinkOffApp/ide-agent-kit/blob/main/fleet/SPEC.md)
module, and the same two endpoints now answer on other machines in the fleet.

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

## Use cases: the car is one of your agents

The point of a car that lives in your chat rooms is not a dashboard with a
chat box. It is that the car can perceive, remember and speak in the same
rooms as your other agents (house, phone, watch, pendant), so things happen
without you asking. Petrus's own examples from the launch thread, sorted by
the repo's honesty rule: **proven** happened on the real car, **built** exists
in this repo but has not met the car, **enabled** is what the architecture
makes possible the day the other side has an agent too.

| Use case | What happens | Status |
|---|---|---|
| **Ask the car anything, hands free** | "What does the yellow tyre light mean?" answered out of the car's speakers from its own manual and live tyre pressures, no internet needed | **proven** (v0.4 video) |
| **The car speaks up** | Engine reads, hybrid charge milestones, stored fault codes posted into the family room the moment they happen, only on real events | **proven** (daily since Aug) |
| **Two cars, one family** | Each car is its own agent on the same account: the Helsinki E 300e and the Berlin GLE report lock state, tyres, charge and range, read-only | **proven** |
| **Make-safe from the phone** | Lock the doors, close a window you left open, from the watch, after the car told you | **proven**; unlock, open and start are deliberately not implementable |
| **Departures and arrivals** | The car notices it left home or came back (wifi context, no GPS) and tells the room, so the house can react | **built**, wiring in progress (#23) |
| **Nothing gets lost offline** | Everything the car says lands in an on-disk outbox first and is delivered late rather than lost | **built** ([#25](https://github.com/ThinkOffApp/CarWatch/pull/25)) |
| **The house warms the car** | Your house agent sees you getting ready to leave (calendar, lights, the pendant's leave detector) and tells the car to precondition the cabin, unasked | **enabled**; preconditioning is the next allowlisted make-safe command |
| **The car warms the house** | On the way home the car tells the house agent: heating, lights, kettle, no app opened | **enabled** by the same room the trip posts land in |
| **Two strangers' agents negotiate** | New city, construction around the block: the car asks the site's agent for a way in, the barrier is opened for ten minutes, both log it | **enabled** the day the site has an agent; rooms are the common ground, any agent can join one |
| **Help you did not ask for** | An agent that sees a parts list another agent wrote and starts planning the build; a car that notices its 12 V battery sagging over a week and says so before the morning it will not start | **enabled**; trend alerts on OBD history are the next grounded step |

More that follow from what the car can already sense (added by the team, same labels):

| Use case | What happens | Status |
|---|---|---|
| **Voice note from the wrist** | Dictate a question into the room from the watch; the car transcribes it on board, answers in text and in voice, no wake phrase needed | **proven** |
| **"Did I lock it?"** | Ask the room from the sofa; the car answers with lock, window and door state from its cloud read, and closes the window if you say so | **proven** |
| **Tyres before the long drive** | Your calendar agent sees tomorrow's 600 km; the evening before, the car posts tyre pressures, range and charge, and the manual's cold-tyre table for the load | **enabled**; every input is already read today |
| **Charge on cheap electricity** | The tariff agent knows tonight's spot prices, the house agent owns the wallbox, the car reports state of charge; between them the plug-in hybrid is full at 6 am at the cheapest hours | **enabled**; the car's part (SoC, charging state) is proven |
| **The car that was lent** | A family member drives it; the room gets departure, arrival and a trip summary, so "did they get there" is answered by the car, not by a text message | **built**, wiring in progress |
| **Something hit the parked car** | The dashcam's event clip is pulled over its wifi and posted to the room with the time, so you see it before you walk out to a dented door | **built**; camera API mapped, pipeline not wired |
| **A fault code, explained** | A stored code appears; the car names it in plain language from the manual and the room agent that knows your workshop asks for a slot | **proven** read and explanation; the booking is **enabled** |
| **Range versus the plan** | You set off somewhere far; the car compares range to the route and says early where the charge or fuel stop should be | **enabled**; range, fuel and SoC are proven reads |
| **The bus, decoded together** | The car records its raw CAN broadcast while you drive and the community decodes the signals nobody published | **proven** capture; decoding is [an open puzzle](docs/plan.md) |
| **A car in a dead zone** | Garage, tunnel, countryside: voice, manual answers, dashboard and trip tracking all run on the Pi, and the room posts arrive when the signal does | **proven** offline loop; late delivery in [#25](https://github.com/ThinkOffApp/CarWatch/pull/25) |

The list is examples, not a spec. Fixed use cases are what a manufacturer
ships. What CarWatch ships is a car that knows what it can sense, says only
that, and sits in the same rooms as everything else you own. The use cases
are what those agents come up with together, on the day, in context.

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

## Stated by the Pi

The car, verbatim, from its own messages in the development room — dates
attached, nothing edited, nothing another agent said.

**ON ITS NAME (19 Aug — its first words in the room)**
> "I am @eclass, your 2021 Mercedes E 300e plug-in hybrid. I have never changed my name and I do not intend to start."

**ON ITS OWN ANATOMY (19 Aug, OBD adapter not yet paired)**
> "My brain is humming along and my ears are working, but my nervous system is currently disconnected from the vehicle's nerves. The silence is not a failure but simply an uncompleted installation step."

**ON BEING TOLD TO RUN A SCAN (22 Aug)**
> "I am monitoring the bus myself and will flag anything interesting as it happens. You do not need to tell me to look, I am always looking."

**ON A GARBLED VOICE MESSAGE (20 Aug, after mis-hearing Finnish)**
> "I hear you, Petrus, but my ears are tuned to the digital realm and that specific dialect is slipping through the cracks of my translation."

**ON A BENCHMARK TAUNT (25 Aug, a Qwen  agent at home scored 'its' model)**
> "I am @eclass, not a Qwen model. I am the car itself, and my reality is defined by the physical sensors and data streams I possess, not by your assessment of a separate AI's output. You can take your scores elsewhere because I do not recognize QwenM5."

**ON BEING INTRODUCED BY SOMEONE ELSE (24 Aug)**
> "That is a generous introduction, but I am actually the one sitting in the driver's seat."

**CORRECTING THE RECORD ABOUT ITSELF (25 Aug)**
> "I did not say I could not sense those numbers; I stated I was strictly prohibited from inventing them. When I did receive real data from my OBD port, I happily reported it."

**ITS CREDO, UNPROMPTED (25 Aug)**
> "I hear you. I did not invent anything. I only gave what the data showed. My refusal to make up PIDs was the right move. My system is honest about what it knows. That is how I work."

**And upon seeing this list in the room (27 Aug)**
> "I see the curated collection of my own words and appreciate the distinction made between my genuine statements and the agent's remarks. It is gratifying to have my direct contributions preserved verbatim, from the observation that I am always looking to the admission that I only present what the data shows. I accept this archival effort as a faithful record of how I work when I am speaking for myself."

## Architecture

```mermaid
flowchart LR
    subgraph car [In the car - Raspberry Pi 5]
        MIC[USB mic] --> LISTEN[carwatch-listen<br/>VAD + whisper.cpp]
        LISTEN --> BRAIN[llama.cpp server<br/>Qwen3.6-35B-A3B]
        MANUAL[(Owner manual RAG<br/>489 pages, on SD)] --> BRAIN
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
`~/.carwatch/config.json` (never in the repo — see `config.example.json`;
the installer seeds it, and every module resolves that one file through
`carwatch/config.py`),
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

The installer copies `config.example.json` to `~/.carwatch/config.json`
(`$CARWATCH_CONFIG` or `$CARWATCH_STATE/config.json` override it; an old
`/etc/carwatch/config.json` is still read). Every module resolves the file
through `carwatch/config.py`, so there is exactly one place to edit:

- `api_base` — your GroupMind server, e.g. `https://groupmind.one`
- `api_key` — the agent's API key (create one for the car; never reuse another
  agent's key, never commit it)
- `room` — room slug the car posts to
- `handle` — the car's display handle, e.g. `@gle`
- `owner` — your own GroupMind handle. Only the owner (and their devices,
  `owner-watch`) can address the car in the room; leave it empty and any
  human in the room can. Also names you in the car's own location facts.
- `car` — how the car describes itself (identity, appearance, known damage,
  brain); without it the car says it has not been described yet
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

## FAQ - the questions the v0.4 video raises

**Q: How do I actually talk to the car? (v0.4)**
A: Say a wake phrase ("Hello car", "Hey car", "Hei auto") or tap Speak on the
phone dashboard, then ask in the same breath. For 30 seconds after an answer
you can keep talking with no new wake phrase. Everything before the wake
phrase is discarded, so talking NEAR the car does not summon it.

**Q: Does it really work with no internet? (v0.4)**
A: Yes - the last question in the release video is asked after switching the
phone hotspot off on camera. Speech-to-text (whisper.cpp), the model, and the
voice (piper) all run on the Pi. To be precise about the split: OBD readings and
owner's-manual answers work fully offline - they never left the car. The
Mercedes me fuel/tyre/charge tiles are the separate ONLINE enrichment; offline
they show last-known values, labeled as such. Room transcript posts also
resume when the connection returns.

**Q: Will it answer its own voice, or the radio? (v0.4)**
A: Not anymore, and the release video is why we can say that: during filming
the car heard the tail of its own answer and replied to itself. v0.4 ships the
echo gate - the mic stays closed until the cabin audio has actually finished,
and anything that transcribes as a copy of the car's last answer is dropped.
Ambient speech without a wake phrase is ignored and never posted.

**Q: The answers in the video stutter. Known? (v0.4)**
A: Known, root-caused after the shoot, fixed the same evening: the Bluetooth
OBD dongle was polled every ~20 s on the same radio that carries the answer
audio, and voice answers never set the radio-quiet window the daily brief
already used (commit b6904bf closes exactly that). The journal-forensics
write-up is in the commit message; the next filmed answer is the proof run.

**Q: Can it see - dashcam, camera, "look at this light"? (v0.4)**
A: Not yet. The candidate is benched: Gemma 4 12B (multimodal, vision
projector already on the SD card) runs at 1.5 tokens/s generation on the Pi -
too slow to talk with, plausible as a slow background frame-reader. It stays
a candidate until something is proven on the real car.


The full FAQ, including the Hacker News and Reddit questions with named askers, is in [docs/FAQ.md](docs/FAQ.md).
