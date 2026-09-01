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

4. **Point CarWatch at it.** Either set it in `~/.carwatch/config.json`:
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

## Alternative: Home Assistant Cloud (Nabu Casa)

If you'd rather not run Tailscale, [Nabu Casa](https://www.nabucasa.com/)
(Home Assistant's own subscription, a few € / month) gives you a stable
remote URL and handles the proxy details for you. It's worth it if you *also*
want Alexa / Google voice control, cloud text-to-speech, or encrypted cloud
backups — and the money funds Home Assistant's development. For CarWatch's
purpose alone, though, Tailscale does the job for free, so this is a
convenience choice, not a requirement. Point CarWatch at the Nabu Casa URL
the same way (step 4 above).

## What you should NOT do

- **Do not forward port 8123 from your router** to Home Assistant. That
  publishes your house's login page — and any weak integration behind it — to
  the whole internet, permanently. Both options above avoid it.
- **Do not** pay a monthly subscription for *only* a remote URL. Tailscale
  covers that need for free; per-user subscriptions defeat the point of a
  self-hosted, own-your-data project.
- **Do not add your tailnet to Home Assistant's `trusted_networks`.** It is the
  tempting next thought once the mesh works — skip the login when you are "on
  the VPN" — and it is the one change that turns this setup from private into
  open. `trusted_networks` authenticates by *source address*, and on a tailnet
  every device shares that range, so any device that joins it, or any phone
  that is lost or compromised, has your whole house with no password. Keep the
  normal token login. The Pi already uses one.

**Check that you got it right:** from the Pi, run

```bash
curl -o /dev/null -w '%{http_code}\n' http://100.x.y.z:8123/api/
```

`401` is the answer you want — Home Assistant is reachable *and* still asking
for credentials. `200` means something is letting requests through
unauthenticated, and you should find out what before driving off.

> Using a reverse proxy instead of Tailscale (cloudflared, nginx, Nabu Casa
> internally)? Home Assistant will reject the proxied request with **HTTP
> 400** until its `configuration.yaml` lists the proxy's source IP under
> `trusted_proxies` (with `use_x_forwarded_for: true`). Read that IP from
> HA's own log line (`untrusted proxy <ip>`), don't guess it — on Docker
> Desktop it's the host gateway, not the `docker0` bridge. Tailscale sidesteps
> all of this because the Pi connects to HA directly, with no proxy.
