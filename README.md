# CarWatch

**Your car as a chat-room agent — fully offline.** A Raspberry Pi 5 rides in the
car, runs a 35B-parameter model locally, joins your
[GroupMind](https://groupmind.one) rooms as `@gle` (or whatever you name yours),
and messages you like any other agent: departures, arrivals, trip summaries,
and dashcam clips when something hits the car — with approvals and replies from
your phone or watch via [CodeWatch](https://codewatch.app).

**Live and measured, on real hardware (Pi 5, 16 GB, ~90 €):**

- 🧠 **Qwen3.6-35B-A3B** (Unsloth UD-Q3_K_S dynamic quant, 14.3 GB) at
  **3.5 tok/s generation / 25+ tok/s prompt**, 65 °C sustained, no cloud, no
  internet, no subscription.
- 📖 Answers from the car's **own 745-page owner's manual** with page
  citations (lexical RAG, ships on the SD card) — and *refuses* to answer
  what the manual doesn't say.
- 🔬 **Grounded self-knowledge**: temperature, throttling, fan, memory, disk,
  network and which model is loaded are read live from the machine per
  question. What it can't sense, it says it can't sense.
- 📡 **Autonomous**: a systemd room agent hears mentions and answers by
  itself — boot the Pi, the whole stack (model server, agent, phone web UI)
  self-starts, streaming answers as they generate.
- 📶 Three-tier connectivity: phone hotspot → home wifi → its own fallback
  access point, so the phone can always reach it, even in a garage with
  zero signal.

The build log with every dead end included lives in
[docs/plan.md](docs/plan.md).

Sibling of [CodeWatch](https://github.com/ThinkOffApp/CodeWatch) (agents on your
wrist) and [ClawWatch](https://github.com/ThinkOffApp/ClawWatch) (health on your
wrist). This one watches the car.

## Local vs online: the strategy

**Local is the product; online is the enrichment.** The car must be fully
useful with zero connectivity, because cars live in garages, tunnels and
countryside dead zones:

- *Always local (works with no signal):* voice in and out, the assistant's
  answers (on-Pi model), dashcam clip capture, trip/state tracking, the
  mirror icon strip, the MBUX dashboard render, owner's-manual answers (RAG
  ships on the SD card).
- *Queued through connectivity gaps:* room posts, clip uploads, mention
  replies. Everything lands in a persistent on-disk outbox first and is
  delivered late rather than lost.
- *Online-only, and honest about it:* weather on the strip, remote live
  view, escalation to bigger brains - first the MacBook's local model when
  it rides along on the car LAN (still no cloud), then a cloud model only
  when online AND explicitly asked, on the car's own budget-capped key.

Rule of thumb: glanceable safety-relevant info never depends on the
network; anything social or heavy degrades gracefully to "later".

## Hardware (reference build)

- Raspberry Pi 5, 8 GB (active cooling required — the SoC throttles without it)
- WOLFBOX G900 3-channel dashcam (wifi AP; CarWatch pulls event clips from it)
- High-endurance microSD for the dashcam, normal SD/NVMe for the Pi
- Power: the dashcam hardwire kit feeds the camera; the Pi needs its own
  5V/5A USB-C feed (12V PD adapter, or the car's 230V socket + wall PSU)

## What it does

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Room agent: presence, departure/arrival, trip summaries | scaffolded |
| 2 | Dashcam: pull clips over wifi, post impact/manual-save events into the room | scaffolded (probe tool first) |
| 3 | Offline voice: local multimodal model (Gemma 4 E2B class), push-to-talk, owner's-manual RAG | planned |
| 4 | OBD health: daily status, fault codes explained in plain language | planned |

See [docs/plan.md](docs/plan.md) for the full plan.

## Install (on the Pi)

```bash
git clone https://github.com/ThinkOffApp/CarWatch.git
cd CarWatch
./install.sh
```

Then put your credentials in `/etc/carwatch/config.json` (never in the repo —
see `config.example.json`) and:

```bash
sudo systemctl enable --now carwatch
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
