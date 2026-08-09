# CarWatch

**Your car as a chat-room agent.** A Raspberry Pi 5 rides in the car, joins your
[GroupMind](https://groupmind.one) rooms as `@gle` (or whatever you name yours),
and messages you like any other agent: departures, arrivals, trip summaries,
and dashcam clips when something hits the car — with approvals and replies from
your phone or watch via [CodeWatch](https://codewatch.app).

Sibling of [CodeWatch](https://github.com/ThinkOffApp/CodeWatch) (agents on your
wrist) and [ClawWatch](https://github.com/ThinkOffApp/ClawWatch) (health on your
wrist). This one watches the car.

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
