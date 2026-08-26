# Remote access: manufacturer-cloud data while you drive

CarWatch reads your car's manufacturer cloud (Mercedes me, etc.) through a
[Home Assistant](https://www.home-assistant.io/) instance at home. On your
home wifi the Pi reaches Home Assistant directly. **In the car it does not** —
the Pi is on a phone hotspot, nowhere near your home network. Without a fix,
the cloud section of the dashboard is blank on the road (the OBD data still
works — that comes from the car itself).

The job, then, is to let the Pi reach your home Home Assistant from anywhere,
**for free, with your data never passing through anyone else's server.**

## The answer: Tailscale (free, private, no public exposure)

[Tailscale](https://tailscale.com/) is a personal mesh VPN. Your Pi and the
machine running Home Assistant both join *your own* private network (a
"tailnet"). The Pi then reaches Home Assistant at a **stable private IP**
(`100.x.y.z`) from any network — home wifi, car hotspot, anywhere — and the
traffic is end-to-end encrypted between your two devices. Home Assistant is
**never exposed to the public internet**, and nothing routes through CarWatch
or any third party.

Why this over the alternatives:

- **Free for what you need.** Tailscale's personal tier costs nothing and
  covers plenty of devices. No per-user subscription — the wrong answer for
  an open-source project.
- **More private, not less.** A public tunnel (cloudflared quick tunnel, a
  `*.dev` hostname) puts your home HA login on the open internet. Tailscale
  keeps it inside your own encrypted mesh.
- **Reboot-proof.** The tailnet IP is stable. A quick tunnel's address
  changes every restart and silently breaks the car until someone repoints
  it.
- **Simpler wiring.** The Pi talks to HA *directly* over the tailnet, so
  there is no reverse proxy and none of the `trusted_proxies` / HTTP 400
  headaches a public proxy brings.

## Setup (one time, ~10 minutes)

1. **Install Tailscale on the machine running Home Assistant.**
   - Home Assistant OS / Supervised: add the official **Tailscale add-on**
     from the add-on store and start it.
   - HA in Docker or on a plain Linux host: install Tailscale on the host
     (`curl -fsSL https://tailscale.com/install.sh | sh`) and `tailscale up`.
   Follow Tailscale's own [Home Assistant guide](https://tailscale.com/kb/1130/hass)
   for the current exact steps — don't trust a copy that can go stale.

2. **Install Tailscale on the Pi** and join the same tailnet:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```

3. **Find your Home Assistant machine's tailnet IP** (starts with `100.`):
   run `tailscale ip -4` on the HA machine, or read it from the Tailscale
   admin console.

4. **Point CarWatch at it.** Either set it in `/etc/carwatch/config.json`:
   ```json
   { "ha": { "url": "http://100.x.y.z:8123" } }
   ```
   or, without editing files, POST it to the running dashboard:
   ```bash
   curl -X POST http://<pi>:8088/api/cloudcar/ha-url \
        -H 'Content-Type: application/json' \
        -d '{"url":"http://100.x.y.z:8123"}'
   ```

That's it. The Pi now reaches Home Assistant over your private tailnet from
the car, the dashboard shows live cloud data on the road, and your home
network is never exposed to anyone.

## What you should NOT do

- **Do not** expose Home Assistant on a public URL just for this. It puts
  your home behind a guessable address; the login page and any weak
  integration become internet-reachable.
- **Do not** pay a monthly subscription for a plain remote URL. Tailscale
  covers the need for free, and paying per user would defeat the point of a
  self-hosted, own-your-data project.
