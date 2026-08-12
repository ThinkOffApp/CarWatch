"""A phone-sized chat page served BY the car, for use with no internet.

petrus, standing next to the GLE: "And how am i supposed to talk with it
offline?" Fair - SSH from a laptop is not an answer in a car park. This
serves a single page on the Pi, so a phone joined to the Pi (its own
hotspot, or any shared network) can just open a browser and talk to the car.

Deliberately stdlib-only and dependency-free: it has to work in a tunnel.

    python3 -m carwatch.webchat            # http://<pi>:8088
    python3 -m carwatch.webchat --port 80  # needs root
"""

from __future__ import annotations

import argparse
import os
import sys
import json
import os
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_URL = os.environ.get("CARWATCH_MODEL_URL", "http://127.0.0.1:8081/v1/chat/completions")

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>GLE</title><style>
*{box-sizing:border-box}
body{margin:0;font:16px/1.5 -apple-system,system-ui,sans-serif;background:#0d0f12;color:#e8eaed}
header{padding:14px 16px;border-bottom:1px solid #222;font-weight:600;letter-spacing:.02em}
header small{display:block;font-weight:400;color:#8a9199;font-size:12px;margin-top:2px}
#log{padding:12px 16px 8px;min-height:50vh}
.m{margin:0 0 12px;padding:10px 12px;border-radius:12px;max-width:85%;white-space:pre-wrap;word-wrap:break-word}
.you{background:#1f6feb;margin-left:auto;border-bottom-right-radius:3px}
.car{background:#181c22;border:1px solid #262b33;border-bottom-left-radius:3px}
.meta{font-size:11px;color:#8a9199;margin:-6px 0 12px 4px}
form{position:sticky;bottom:0;display:flex;gap:8px;padding:12px 16px;
     background:#0d0f12;border-top:1px solid #222}
input{flex:1;padding:12px;border-radius:10px;border:1px solid #2a3039;background:#12151a;color:inherit;font-size:16px}
button{padding:12px 18px;border:0;border-radius:10px;background:#1f6feb;color:#fff;font-size:16px}
button:disabled{opacity:.5}
label{display:flex;align-items:center;gap:6px;font-size:12px;color:#8a9199;padding:0 16px 10px}
</style></head><body>
<header>GLE<small>running offline on Vadelma</small></header>
<div id=log></div>
<label><input type=checkbox id=man checked> use owner manual</label>
<form id=f><input id=q placeholder="Ask your car something" autocomplete=off>
<button id=b>Ask</button></form>
<script>
const log=document.getElementById('log'),f=document.getElementById('f'),
      q=document.getElementById('q'),b=document.getElementById('b'),man=document.getElementById('man');
function add(t,cls){const d=document.createElement('div');d.className='m '+cls;d.textContent=t;
  log.appendChild(d);window.scrollTo(0,document.body.scrollHeight);return d}
function meta(t){const d=document.createElement('div');d.className='meta';d.textContent=t;
  log.appendChild(d);window.scrollTo(0,document.body.scrollHeight)}
f.onsubmit=async e=>{e.preventDefault();const text=q.value.trim();if(!text)return;
  add(text,'you');q.value='';b.disabled=true;
  const think=add('thinking...','car');const t0=Date.now();
  try{const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({q:text,manual:man.checked})});
      const j=await r.json();
      think.textContent=j.answer||'(no answer)';
      meta(Math.round((Date.now()-t0)/1000)+'s'+(j.grounded?' · offline':'' ));
  }catch(err){think.textContent='Could not reach the car brain. Is the model running?'}
  b.disabled=false;q.focus()}
</script></body></html>"""

DASH_PAGE = '''<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>vadelma dash</title></head>
<body style="background:#111;color:#ddd;font:14px monospace;margin:0;padding:10px">
<div id=temp style="font-size:34px;color:#6f6">...</div>
<div style="color:#666;font-size:11px">CPU temperature (goes red at 75C) &middot; fan speed in rpm</div>
<div id=sub style="color:#888"></div>
<div style="color:#666;font-size:11px">throttling state &middot; memory used / total</div>
<div style="color:#6cf;margin-top:8px">TOP <span style="color:#888;font-size:11px">- busiest processes right now</span></div>
<div style="color:#666;font-size:11px">columns: %CPU (share of processor) &middot; %MEM (share of the 16GB RAM) &middot; process name</div>
<pre id=top style="margin:2px 0;white-space:pre-wrap"></pre>
<div style="color:#6cf">JOURNAL</div>
<pre id=jrnl style="margin:2px 0;white-space:pre-wrap;color:#9e9"></pre>
<div style="color:#6cf">SSH (Termux)</div>
<pre style="margin:2px 0;color:#fc6">ssh petrus@__IP__</pre>
<div style="color:#6cf;margin-top:6px">CAR SOFTWARE</div>
<div style="margin:4px 0"><button onclick="updateNow()" style="background:#333;color:#6f6;border:1px solid #555;padding:8px 10px;font:13px monospace">update now</button> <span id=updout style="color:#888"></span></div>
<div style="color:#6cf;margin-top:6px">OBD (car engine)</div>
<div style="margin:4px 0"><button onclick="probeObd()" style="background:#333;color:#fc6;border:1px solid #555;padding:8px 10px;font:13px monospace">probe car connection</button></div>
<pre id=obdout style="margin:2px 0;white-space:pre-wrap;color:#9cf;max-height:160px;overflow:auto"></pre>
<div style="color:#6cf;margin-top:6px">VOICE</div>
<div style="margin:4px 0"><span id=voicestate style="color:#888">...</span> <button onclick="setListen(true)" style="background:#333;color:#6f6;border:1px solid #555;padding:8px 10px;font:13px monospace">listen on</button> <button onclick="setListen(false)" style="background:#333;color:#f66;border:1px solid #555;padding:8px 10px;font:13px monospace">listen off</button></div>
<div style="color:#6cf;margin-top:6px">NETWORK</div>
<div style="margin:4px 0">
<button onclick="wifi('hotspot')" style="background:#333;color:#fc6;border:1px solid #555;padding:8px 10px;font:13px monospace">phone hotspot</button>
<button onclick="wifi('home')" style="background:#333;color:#6f6;border:1px solid #555;padding:8px 10px;font:13px monospace">home wifi</button>
<button onclick="wifi('ap')" style="background:#333;color:#6cf;border:1px solid #555;padding:8px 10px;font:13px monospace">own network</button>
</div>
<details style="margin:4px 0;color:#888"><summary>join another wifi</summary>
<input id=ssid placeholder=network style="background:#222;color:#ddd;border:1px solid #555;padding:6px;font:13px monospace;width:40%">
<input id=psk placeholder=password type=password style="background:#222;color:#ddd;border:1px solid #555;padding:6px;font:13px monospace;width:40%">
<button onclick="wifiAdd()" style="background:#333;color:#fc6;border:1px solid #555;padding:6px 10px;font:13px monospace">join</button>
</details>
<div id=netmsg style="color:#fc6"></div>
<div style="color:#555">links: <a href="/" style="color:#6cf">chat</a>
<a href="/journal" style="color:#6cf">full journal</a> &middot; live, 2s</div>
<script>
async function tick(){
  try{
    const r = await fetch("/api/status"); const d = await r.json();
    const f = d.facts || {};
    const t = parseFloat((f["your temperature"]||"").split(" ")[0]);
    const el = document.getElementById("temp");
    el.textContent = (isNaN(t) ? "?" : t) + "\u00b0C  " +
      ((f["your fan"]||"").split(" ")[0]||"0") + " rpm";
    el.style.color = t >= 75 ? "#f66" : "#6f6";
    document.getElementById("sub").textContent =
      (f["throttling"]||"") + " \u00b7 " + (f["memory"]||"");
    document.getElementById("top").textContent = (d.top||[]).join("\\n");
    document.getElementById("jrnl").textContent = (d.journal||[]).join("\\n");
    var vs=document.getElementById("voicestate"); if(vs){ vs.textContent = d.listening ? "listening: ON" : "listening: off"; vs.style.color = d.listening ? "#6f6" : "#888"; }
  }catch(e){
    document.getElementById("sub").textContent = "unreachable: " + e;
  }
}
async function updateNow(){
  var u=document.getElementById("updout"); u.textContent="updating... (~30s, page may blink)";
  try{ const r=await fetch("/api/update",{method:"POST"}); const d=await r.json();
    u.textContent = d.ok ? "updated - reload the page" : ("update issue: "+(d.error||"see log"));
  }catch(e){ u.textContent="updated (page dropped as services restarted) - reload"; }
}
async function probeObd(){
  var o=document.getElementById("obdout"); o.textContent="probing the car... (up to 40s)";
  try{ const r=await fetch("/api/obd",{method:"POST"}); const d=await r.json();
    o.textContent = d.ok ? d.output : ("error: "+d.error);
  }catch(e){ o.textContent="request failed: "+e; }
}
async function setListen(on){
  const r = await fetch("/api/listen", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({on})});
  const d = await r.json();
  var vs=document.getElementById("voicestate"); if(vs) vs.textContent = d.ok ? (d.listening?"listening: ON":"listening: off") : ("error: "+d.error);
}
tick(); setInterval(tick, 2000);
async function wifi(target){
  if(!confirm("Switch the car to " + target + "? This page will drop and come back on the new network.")) return;
  const r = await fetch("/api/wifi", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({target})});
  document.getElementById("netmsg").textContent = (await r.json()).note || "switching...";
}
async function wifiAdd(){
  const ssid = document.getElementById("ssid").value, password = document.getElementById("psk").value;
  if(!ssid || password.length < 8){ document.getElementById("netmsg").textContent = "need network name + password (8+)"; return; }
  const r = await fetch("/api/wifi", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({target:"add", ssid, password})});
  document.getElementById("netmsg").textContent = (await r.json()).note || (await r.json()).error;
}
</script></body></html>'''



def manual_context(question: str) -> str:
    """Real manual excerpts, or empty - never a claim we did not earn."""
    try:
        r = subprocess.run(
            ["python3", "-m", "carwatch.manual", "--ask", question],
            capture_output=True, text=True, cwd=REPO, timeout=30,
            env={**os.environ, "CARWATCH_STATE": os.environ.get("CARWATCH_STATE", "/home/petrus/.carwatch")},
        )
        return r.stdout.strip()[:1500]
    except Exception:
        return ""


def answer(question: str, use_manual: bool = True) -> str:
    from carwatch.grounding import build_system_prompt, default_state

    ctx = manual_context(question) if use_manual else ""
    facts, cannot = default_state()  # nothing sensed yet; engine state unknown
    system = build_system_prompt(facts, cannot, manual_excerpts=ctx)

    req = urllib.request.Request(
        MODEL_URL,
        data=json.dumps({
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": question}],
            "max_tokens": 400,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        msg = json.load(r)["choices"][0]["message"]
    return (msg.get("content") or "").strip() or "[the model spent its budget thinking and did not answer]"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE)
        elif self.path.startswith("/api/status"):
            # Machine-readable twin of /dash. CodeWatch local mode polls
            # this when the phone shares a network with the car (hotspot
            # or Vadelma AP); online it reads the same facts via the UIK
            # intent doc that carwatch.presence publishes. One source,
            # three consumers: browser HTML, UIK online, CodeWatch offline.
            import subprocess as _sp
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from carwatch.selfstate import live_facts
            def run(cmd):
                try:
                    return _sp.run(cmd, capture_output=True, text=True, timeout=8).stdout
                except Exception:
                    return ""
            listening = run(["systemctl", "is-active", "carwatch-listen"]).strip() == "active"
            payload = {
                "device": "vadelma",
                "listening": listening,
                "facts": live_facts(),
                "top": run(["ps", "-eo", "pcpu,pmem,comm", "--sort=-pcpu"]).splitlines()[1:6],
                "journal": run(["journalctl", "-u", "carwatch-agent", "-n", "8",
                                "--no-pager", "-o", "cat"]).splitlines()[-8:],
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/dash"):
            # One-phone-screen dashboard. Updates in place every 2s like
            # `top` (petrus: "make the dashboard updating like top") -
            # fetches /api/status via JS instead of reloading the page,
            # so no flicker and no scroll reset.
            import subprocess as _sp
            ip = "?"
            try:
                ip = (_sp.run(["hostname", "-I"], capture_output=True,
                              text=True, timeout=5).stdout.split() or ["?"])[0]
            except Exception:
                pass
            self._send(200, DASH_PAGE.replace("__IP__", ip))
        elif self.path.startswith("/journal"):
            # The car's thinking, phone-readable: last agent-journal lines,
            # auto-refreshing. petrus asked "how can I see the journal" -
            # the SSH answer only works at a keyboard; in the car the phone
            # is the screen.
            import subprocess as _sp
            try:
                log = _sp.run(
                    ["journalctl", "-u", "carwatch-agent", "-n", "60",
                     "--no-pager", "-o", "short-iso"],
                    capture_output=True, text=True, timeout=10).stdout
            except Exception as e:
                log = f"journal unavailable: {e}"
            import html as _html
            self._send(200, "<!doctype html><html><head><meta charset=utf-8>"
                       "<meta http-equiv=refresh content=5>"
                       "<meta name=viewport content='width=device-width,initial-scale=1'>"
                       "<title>gle journal</title></head>"
                       "<body style='background:#111;color:#9e9;font:12px monospace;"
                       "padding:8px;word-wrap:break-word'><pre style='white-space:pre-wrap'>"
                       + _html.escape(log) + "</pre></body></html>")
        else:
            self._send(404, "not found")

    def do_POST(self):
        if self.path == "/api/update":
            # One-tap self-update from the dashboard, from ANYWHERE (petrus
            # was blocked getting a fix onto the car while it was on his
            # hotspot behind NAT). The Pi pulls the latest code from GitHub
            # itself - no ssh, no home wifi needed.
            import subprocess as _sp, os as _os
            try:
                r = _sp.run(
                    ["bash", "-lc",
                     "curl -sSL https://raw.githubusercontent.com/ThinkOffApp/CarWatch/main/update.sh | bash"],
                    capture_output=True, text=True, timeout=90,
                    env={**_os.environ})
                out = (r.stdout + r.stderr).strip()[-1500:]
                return self._send(200, json.dumps({"ok": r.returncode == 0, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if self.path == "/api/obd":
            # One-tap OBD from the dashboard: runs the COMPLETE session
            # (eth0 up -> gateway discovery -> routing activation -> PID
            # reads) and returns the stage-by-stage JSON trace, so a failure
            # says exactly how far it got. Same code path the zero-touch
            # obdwatch daemon uses; proven end-to-end against the fake
            # gateway before ever touching the car.
            import subprocess as _sp, os as _os
            try:
                r = _sp.run(
                    ["sudo", "python3", "-m", "carwatch.obd_session"],
                    capture_output=True, text=True, timeout=60,
                    cwd=_os.path.expanduser("~/CarWatch"),
                    env={**_os.environ, "CARWATCH_STATE": _os.path.expanduser("~/.carwatch")})
                out = (r.stdout + r.stderr).strip()[-3000:]
                return self._send(200, json.dumps({"ok": True, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if self.path == "/api/listen":
            # Voice-listener on/off from the dashboard (petrus: "have a
            # setting for that on the dashboard"). Toggles the systemd
            # service so the choice survives reboots.
            import subprocess as _sp
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))) or b"{}")
                on = bool(body.get("on"))
                if on:
                    _sp.run(["sudo", "systemctl", "enable", "--now",
                             "carwatch-listen"], timeout=20, capture_output=True)
                else:
                    _sp.run(["sudo", "systemctl", "disable", "--now",
                             "carwatch-listen"], timeout=20, capture_output=True)
                    _sp.run(["pkill", "-9", "arecord"], capture_output=True)
                st = _sp.run(["systemctl", "is-active", "carwatch-listen"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
                return self._send(200, json.dumps(
                    {"ok": True, "listening": st == "active"}),
                    "application/json")
            except Exception as e:
                return self._send(500, json.dumps(
                    {"ok": False, "error": str(e)}), "application/json")
        if self.path == "/api/wifi":
            # Network switching from the phone dashboard (petrus: "can you
            # have switch to hotspot / key in wifi details in the dashboard").
            # LAN-local by nature: whoever can reach this page shares the
            # car's network already. The switch is backgrounded because it
            # tears down the very connection carrying this response.
            import subprocess as _sp
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))) or b"{}")
                target = str(body.get("target", ""))
                profiles = {"hotspot": "phone-hotspot",
                            "home": "PYUR 53A99",
                            "ap": "vadelma-ap"}
                if target == "add":
                    ssid = str(body.get("ssid", "")).strip()
                    psk = str(body.get("password", "")).strip()
                    if not ssid or len(psk) < 8:
                        return self._send(400, json.dumps(
                            {"ok": False, "error": "need ssid and password (8+ chars)"}),
                            "application/json")
                    _sp.Popen(["sudo", "nmcli", "dev", "wifi", "connect",
                               ssid, "password", psk, "ifname", "wlan0"],
                              start_new_session=True)
                    return self._send(200, json.dumps(
                        {"ok": True, "note": f"joining {ssid}; page may drop"}),
                        "application/json")
                profile = profiles.get(target)
                if not profile:
                    return self._send(400, json.dumps(
                        {"ok": False, "error": f"unknown target {target}"}),
                        "application/json")
                _sp.Popen(["sh", "-c",
                           f"sleep 1; sudo nmcli con up '{profile}'"],
                          start_new_session=True)
                return self._send(200, json.dumps(
                    {"ok": True, "note": f"switching to {profile}; this page "
                     "will drop and come back on the new network"}),
                    "application/json")
            except Exception as e:
                return self._send(500, json.dumps(
                    {"ok": False, "error": str(e)}), "application/json")
        if self.path != "/ask":
            return self._send(404, "not found")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            text = answer(str(body.get("q", "")), bool(body.get("manual", True)))
            self._send(200, json.dumps({"answer": text, "grounded": True}), "application/json")
        except Exception as e:
            self._send(500, json.dumps({"answer": f"error: {e}"}), "application/json")

    def log_message(self, *a):  # keep the car's console quiet
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the car's chat page")
    ap.add_argument("--port", type=int, default=8088)
    args = ap.parse_args()
    print(f"CarWatch chat on http://0.0.0.0:{args.port}  (model: {MODEL_URL})")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
