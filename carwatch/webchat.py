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
import ipaddress
import os
import sys
import json
import os
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_URL = os.environ.get("CARWATCH_MODEL_URL", "http://127.0.0.1:8081/v1/chat/completions")
FAKE_SSIDS = frozenset({
    # "wifi router" was on this list as an assumed placeholder - it is the
    # ACTUAL SSID of petrus's Helsinki home network (seen at 97% in the
    # kitchen scan, Aug 17). A word list cannot decide what is a real SSID;
    # the scan can. Never blacklist a name the scan itself reports.
    "network", "ssid", "yourhomewifi", "home wifi",
})


def _saved_connection_names() -> set[str]:
    out = subprocess.run(
        ["nmcli", "-t", "-f", "NAME", "con", "show"],
        capture_output=True, text=True, timeout=10,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _switch_later(name: str) -> None:
    # argv only, never a shell string. Dashboard is on a public tunnel.
    subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time,subprocess; time.sleep(1); "
         "subprocess.run(['sudo','nmcli','con','up',sys.argv[1]])",
         name],
        start_new_session=True,
    )

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

# Cloud-provider ("mokkula") setup + status page. Two-step login: email ->
# vendor emails a code -> code -> tokens on the Pi. NO password field exists.
CLOUDCAR_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Mercedes cloud</title></head>
<body style="background:#111;color:#ddd;font:16px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:16px">
<h2 style="margin:0 0 8px;color:#6cf">Mercedes cloud</h2>
<div style="color:#bbb;margin-bottom:12px;max-width:36em">
Doors, tires, charge, and lock from Mercedes.me. This page is not the OBD plug.
It reads Home Assistant at home. You never type a Mercedes password here.
</div>
<ol style="color:#bbb;margin:0 0 14px 1.2em;padding:0;max-width:36em">
<li>On home wifi open Home Assistant (http://192.168.50.241:8123)</li>
<li>Tap your name, then Security, then Create long-lived access token</li>
<li>Copy the token and paste it below, then tap Connect</li>
</ol>
<div id=authbox style="margin:10px 0"></div>
<div id=status style="color:#9e9;margin-top:12px;max-width:36em"></div>
<div style="color:#555;margin-top:16px;font-size:14px"><a href="/dash" style="color:#6cf">dash</a> &middot; <a href="/nerd" style="color:#6cf">nerd</a> &middot; <a href="/" style="color:#6cf">chat</a></div>
<script>
const _tok=new URLSearchParams(location.search).get('t')||'';
const _q=u=>_tok?(u+(u.includes('?')?'&':'?')+'t='+encodeURIComponent(_tok)):u;
const ab=document.getElementById('authbox'),st=document.getElementById('status');
async function post(b){const r=await fetch(_q('/api/cloudcar/login'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}
function form(ph,key,btn){ab.innerHTML='<input id=v placeholder="'+ph+'" autocomplete=off style="background:#222;color:#ddd;border:1px solid #555;padding:10px;font:16px sans-serif;width:100%;max-width:28em;box-sizing:border-box;margin-bottom:8px"><button onclick="send(\\''+key+'\\')" style="background:#333;color:#6f6;border:1px solid #555;padding:10px 14px;font:16px sans-serif">'+btn+'</button> <span id=msg style="color:#fc6"></span>'}
async function send(key){const v=document.getElementById('v').value.trim();if(!v)return;
document.getElementById('msg').textContent='checking...';const d=await post({[key]:v});
document.getElementById('msg').textContent=d.ok?'connected':(d.error||'failed');if(d.ok)setTimeout(tick,800)}
function human(s){
  if(!s)return 'Waiting...';
  if(s.ok && s.cars){
    const names=Object.values(s.cars).map(c=>c.label||'car');
    return 'Connected. Seeing: '+(names.join(', ')||'cars')+'. Open the dash for lock, doors, tires, charge.';
  }
  const e=String(s.error||'');
  if(e.indexOf('not connected')>=0||e.indexOf('no HA token')>=0) return 'Not connected yet. Paste a Home Assistant token below.';
  if(e.toLowerCase().indexOf('unreachable')>=0) return 'Cannot reach Home Assistant. Use home wifi, not the phone hotspot.';
  return e||'Waiting...';
}
async function tick(){
 try{
  const a=await (await fetch(_q('/api/cloudcar/auth'))).json();
  if(a.step==='need_email')form('your email','email','Send code');
  else if(a.step==='need_code')form('Home Assistant token','code','Connect');
  else if(a.step==='no_provider')ab.innerHTML='<span style="color:#fc6">Cloud plugin is not installed on this Pi.</span>';
  else ab.innerHTML='<span style="color:#6f6">Connected to Home Assistant.</span>';
  const s=await (await fetch(_q('/api/cloudcar'))).json();
  st.textContent=human(s);
 }catch(e){st.textContent='Cannot reach the Pi. Open this page from the car link or home wifi.'}
}
tick();setInterval(tick,5000);
</script></body></html>"""

# Realistic sample payload for /api/obd/all?mock=1 so the nerd-dashboard UI
# can be built and tested at home while the OBD adapter is in the car. Shape
# is EXACTLY elm327.run_all()'s, plus "mock": true so a UI can badge it.
_OBD_ALL_MOCK = {
    "ok": True, "mock": True, "read_count": 24, "attempted": 26,
    "elapsed_s": 6.8, "dtcs": [], "error": "",
    "groups": {
        "temperatures": {
            "coolant_c": {"key": "coolant_c", "label": "coolant temp", "unit": "°C", "pid": "0x05", "value": 87},
            "intake_air_c": {"key": "intake_air_c", "label": "intake air temp", "unit": "°C", "pid": "0x0F", "value": 31},
            "ambient_air_c": {"key": "ambient_air_c", "label": "ambient air temp", "unit": "°C", "pid": "0x46", "value": 21},
            "oil_c": {"key": "oil_c", "label": "engine oil temp", "unit": "°C", "pid": "0x5C", "value": 92},
        },
        "engine": {
            "engine_rpm": {"key": "engine_rpm", "label": "engine speed", "unit": "rpm", "pid": "0x0C", "value": 1420.0},
            "engine_load_pct": {"key": "engine_load_pct", "label": "engine load", "unit": "%", "pid": "0x04", "value": 23.5},
            "timing_advance_deg": {"key": "timing_advance_deg", "label": "timing advance", "unit": "°", "pid": "0x0E", "value": 12.5},
            "maf_gps": {"key": "maf_gps", "label": "MAF air flow", "unit": "g/s", "pid": "0x10", "value": 8.42},
            "runtime_s": {"key": "runtime_s", "label": "run time since start", "unit": "s", "pid": "0x1F", "value": 940},
            "abs_load_pct": {"key": "abs_load_pct", "label": "absolute load", "unit": "%", "pid": "0x43", "value": 19.6},
        },
        "driving": {
            "speed_kmh": {"key": "speed_kmh", "label": "vehicle speed", "unit": "km/h", "pid": "0x0D", "value": 62},
            "throttle_pct": {"key": "throttle_pct", "label": "throttle position", "unit": "%", "pid": "0x11", "value": 14.9},
            "pedal_d_pct": {"key": "pedal_d_pct", "label": "accelerator pedal D", "unit": "%", "pid": "0x49", "value": 18.4},
            "throttle_cmd_pct": {"key": "throttle_cmd_pct", "label": "commanded throttle", "unit": "%", "pid": "0x4C", "value": 12.2},
        },
        "hybrid": {
            "hybrid_battery_pct": {"key": "hybrid_battery_pct", "label": "hybrid battery", "unit": "%", "pid": "0x5B", "value": 76.5},
        },
        "fuel": {
            "fuel_level_pct": {"key": "fuel_level_pct", "label": "fuel tank level", "unit": "%", "pid": "0x2F", "value": 58.4},
            "short_fuel_trim_pct": {"key": "short_fuel_trim_pct", "label": "short-term fuel trim", "unit": "%", "pid": "0x06", "value": 1.6},
            "long_fuel_trim_pct": {"key": "long_fuel_trim_pct", "label": "long-term fuel trim", "unit": "%", "pid": "0x07", "value": -2.3},
            "fuel_rate_lph": {"key": "fuel_rate_lph", "label": "engine fuel rate", "unit": "L/h", "pid": "0x5E", "value": 3.2},
            "fuel_type": {"key": "fuel_type", "label": "fuel type", "unit": "", "pid": "0x51", "value": "plug-in hybrid gasoline"},
        },
        "pressures": {
            "intake_map_kpa": {"key": "intake_map_kpa", "label": "intake manifold pressure", "unit": "kPa", "pid": "0x0B", "value": 41},
            "baro_kpa": {"key": "baro_kpa", "label": "barometric pressure", "unit": "kPa", "pid": "0x33", "value": 101},
            "fuel_rail_gauge_kpa": {"key": "fuel_rail_gauge_kpa", "label": "fuel rail gauge pressure", "unit": "kPa", "pid": "0x23", "value": 7440},
        },
        "electrical": {
            "module_voltage": {"key": "module_voltage", "label": "control module voltage", "unit": "V", "pid": "0x42", "value": 14.34},
        },
        "diagnostics": {
            "distance_mil_km": {"key": "distance_mil_km", "label": "distance with MIL on", "unit": "km", "pid": "0x21", "value": 0},
            "distance_clear_km": {"key": "distance_clear_km", "label": "distance since codes cleared", "unit": "km", "pid": "0x31", "value": 1287},
        },
    },
}

# The unified admin: /dash and /nerd's jobs on ONE screen with graphical
# controls (petrus, Aug 25: "unify /dash and /nerd, graphical buttons" -
# layout approved via the carwatch.dev mock). Live values poll
# /api/obd/all + /api/status; every button calls an endpoint that already
# existed - nothing new happens to the car.
# One screen, no scroll (petrus, Aug 26: "actually single screen ... OBD
# labelled + mercedes.me on the same screen, hide the other car behind a
# button, synthesize don't list a million variables", sized for the Fold 8
# unfolded). Two labelled zones - OBD (live from the car) and Mercedes me
# (cloud) - a per-car tab that shows ONE car at a time, and a compact control
# bar. The CAN capture, room feed and raw-PID list moved off this screen to
# their own pages. Live values still poll the same endpoints; every button
# calls one that already existed.
UNIFIED_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-status-bar-style content=black-translucent>
<meta name=theme-color content="#091016">
<link rel=manifest href=/manifest.webmanifest>
<link rel=apple-touch-icon href=/icon.png>
<title>CarWatch</title><style>
:root{color-scheme:dark;
 --line:#20313c; --ink:#e8f1f5; --dim:#8ca1ad;
 --ok:#40d98b; --warn:#ffc857; --bad:#ff667d; --blue:#62c7ff;
 --mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:radial-gradient(circle at 30% -10%,#16303c 0,#091016 55%);color:var(--ink);
 font:15px/1.35 -apple-system,system-ui,sans-serif;overflow:hidden;display:flex;flex-direction:column;padding:8px;gap:8px}
.top{display:flex;align-items:center;gap:10px;flex:0 0 auto}
.brand{font-size:17px;font-weight:800;letter-spacing:.02em;white-space:nowrap}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{padding:6px 14px;border-radius:999px;border:1px solid var(--line);background:#0e171e;
 color:var(--dim);font-weight:700;font-size:14px;cursor:pointer;user-select:none}
.tab.on{border-color:var(--blue);color:var(--blue);background:#12222c}
#status{margin-left:auto;font:11.5px var(--mono);color:var(--dim);text-align:right;line-height:1.3}
#status b{color:var(--ok)}
.main{flex:1 1 auto;display:grid;grid-template-columns:1fr 1fr;gap:8px;min-height:0}
@media (max-width:760px){.main{grid-template-columns:1fr;grid-auto-rows:1fr}}
.zone{background:linear-gradient(160deg,#13232c,#0d161d);border:1px solid var(--line);
 border-radius:16px;padding:14px 16px;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.zone > h2{font:700 12px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--blue);
 display:flex;align-items:center;gap:8px;margin-bottom:2px}
.zone > h2 .src{color:var(--dim);font-weight:400;letter-spacing:.04em}
.badge{margin-left:auto;font:700 10px var(--mono);padding:2px 8px;border-radius:999px;letter-spacing:.06em}
.badge.live{background:#123024;color:var(--ok)} .badge.stale{background:#2c2410;color:var(--warn)}
.badge.cloud{background:#102431;color:var(--blue)}
.hero{display:flex;align-items:baseline;gap:10px;margin:8px 0 2px}
.hero .big{font:800 64px/0.9 var(--mono);color:var(--ok)}
.hero .unit{font:600 18px var(--mono);color:var(--dim)}
.hero .lbl{font-size:12px;color:var(--dim);margin-left:auto;align-self:flex-end}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:auto}
.stat{background:#0e171e;border:1px solid var(--line);border-radius:12px;padding:10px 8px;text-align:center}
.stat .v{font:800 23px/1 var(--mono);color:var(--ok)} .stat .v.warn{color:var(--warn)} .stat .v.bad{color:var(--bad)}
.stat .k{font-size:10.5px;color:var(--dim);margin-top:4px}
.steer{margin:6px 0 10px}
.steerrow{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px}
.steerlab{font-size:11px;letter-spacing:.08em;text-transform:uppercase;opacity:.55}
.steerval{font-size:18px;font-weight:600;font-variant-numeric:tabular-nums}
.steerbar{position:relative;height:12px;border-radius:7px;background:#0d1217;border:1px solid #223}
.steerfill{position:absolute;top:0;bottom:0;width:0;background:#5ab0ff;opacity:.9;transition:left .12s linear,width .12s linear}
.steerzero{position:absolute;left:50%;top:0;bottom:0;width:1px;background:#fff;opacity:.35}
.steer.stale .steerval,.steer.stale .steerfill{opacity:.3}
.steer.replay .steerlab{color:#ffb020}
.steer.replay .steerfill{background:#8a94a0}
.nodev{margin:auto;text-align:center;color:var(--dim);font-size:14px}
.mgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px 8px;margin:8px 0}
.mi{text-align:center}
.mi .e{font-size:30px;line-height:1.1;display:block}
.mi .s{font:800 15px var(--mono)} .mi .s.ok{color:var(--ok)} .mi .s.warn{color:var(--warn)} .mi .s.bad{color:var(--bad)} .mi .s.dim{color:var(--ink)}
.mi .l{font-size:10.5px;color:var(--dim);margin-top:1px}
#mnote{font:11px var(--mono);color:var(--dim);margin-top:2px}
.cmds{display:flex;gap:8px;margin-top:auto;padding-top:10px}
.cmds button{flex:1;background:#12222c;color:var(--ink);border:1px solid #2a4a3a;border-radius:12px;
 padding:12px;font-size:14px;font-weight:700;cursor:pointer}
.cmds button:active{transform:scale(.98)} .cmds button:disabled{opacity:.6}
.cmds button.ok{border-color:var(--ok);color:var(--ok)} .cmds button.bad{border-color:var(--bad);color:var(--bad)}
.bar{flex:0 0 auto;display:flex;align-items:center;gap:6px;overflow-x:auto}
.ctl{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:2px;min-width:60px;
 padding:8px 6px;background:#0e171e;border:1px solid var(--line);border-radius:12px;cursor:pointer;user-select:none}
.ctl:active{transform:scale(.96)} .ctl.on{border-color:var(--ok)} .ctl.on .t{color:var(--ok)} .ctl.busy{border-color:var(--warn);opacity:.7}
.ctl .i{font-size:19px} .ctl .t{font-size:10.5px;color:var(--dim);font-weight:700}
.ask{flex:1 1 150px;display:flex;gap:6px;min-width:140px}
.ask input{flex:1;padding:10px;border-radius:12px;border:1px solid #2a3039;background:#0e171e;color:inherit;font-size:14px}
.ask button{padding:10px 16px;border:0;border-radius:12px;background:var(--blue);color:#03121b;font-weight:800}
.links{flex:0 0 auto;font:11px var(--mono);color:var(--dim);display:flex;gap:12px;padding-left:4px}
.links a{color:var(--blue);text-decoration:none}
#out{position:fixed;left:8px;right:8px;bottom:8px;background:#0e171e;border:1px solid var(--blue);border-radius:12px;
 padding:10px 12px;font:12px var(--mono);color:#9fe8bd;white-space:pre-wrap;max-height:40vh;overflow:auto;display:none;z-index:5}
#answer{position:fixed;left:8px;right:8px;bottom:8px;background:#181c22;border:1px solid var(--blue);border-radius:12px;
 padding:10px 12px;font-size:13px;max-height:40vh;overflow:auto;display:none;z-index:5}
</style></head><body>
<div class=top>
 <div class=brand>&#128663; CarWatch</div>
 <div class=tabs id=tabs></div>
 <div id=status>live</div>
</div>
<div class=main>
 <div class=zone>
  <h2>&#128202; OBD <span class=src>live from the car</span> <span class="badge live" id=obdbadge>live</span></h2>
  <div class=hero id=herowrap><span class=big id=spd>-</span><span class=unit>km/h</span><span class=lbl>vehicle speed</span></div>
  <div class=steer id=steerwrap style="display:none"><div class=steerrow><span class=steerlab id=steerlab>steering wheel</span><span class=steerval id=steerval>-</span></div><div class=steerbar><i class=steerzero></i><span class=steerfill id=steerfill></span></div></div>
  <div class=stats id=stats></div>
  <div class=nodev id=nodev style="display:none">This car has no on&#8209;board CarWatch device.<br>OBD is only for the car the Raspberry Pi rides in.</div>
 </div>
 <div class=zone>
  <h2>&#9729;&#65039; Mercedes me <span class=src>manufacturer cloud</span> <span class="badge cloud">read-only</span></h2>
  <div class=mgrid id=merc></div>
  <div id=mnote>loading&#8230;</div>
  <div class=cmds id=cmds></div>
 </div>
</div>
<div class=bar>
 <div class=ctl data-act=read><span class=i>&#128202;</span><span class=t>Read</span></div>
 <div class=ctl data-act=record><span class=i>&#127908;</span><span class=t>Record</span></div>
 <div class=ctl id=listenCtl data-act=listen><span class=i>&#128066;</span><span class=t id=listenT>Listen</span></div>
 <div class=ctl data-act=speak><span class=i>&#128266;</span><span class=t>Speak</span></div>
 <div class=ctl data-act=pair><span class=i>&#128279;</span><span class=t>Pair</span></div>
 <div class=ctl data-act=update><span class=i>&#11014;&#65039;</span><span class=t>Update</span></div>
 <div class=ctl id=fullCtl data-act=full><span class=i>&#9974;</span><span class=t>Full</span></div>
 <div class=ask><input id=q placeholder="Ask your car"><button id=askbtn>Ask</button></div>
 <div class=links><a href=# id=trustlink>trust wifi</a><a href=/nerd>all PIDs</a><a href=/streams>streams</a><a href=/journal>journal</a></div>
</div>
<div id=out></div><div id=answer></div>
<script>
const $=id=>document.getElementById(id);
const _tok=new URLSearchParams(location.search).get('t')||'';
const _q=u=>_tok?(u+(u.includes('?')?'&':'?')+'t='+encodeURIComponent(_tok)):u;
const F=(u,o={},ms=4000)=>{const c=new AbortController();const t=setTimeout(()=>c.abort(),ms);
 return fetch(_q(u),Object.assign({signal:c.signal},o)).finally(()=>clearTimeout(t));};
const ACT={read:['/api/obd','one live engine read',70000],record:['/api/obd/record-arm','armed: records 120s raw CAN on the next moving read',30000],
 pair:['/api/car-pair','scan + pair car Bluetooth (MBUX in pairing mode)',70000],update:['/api/update','pull latest code + restart',90000]};
function show(t){const o=$('out');o.style.display='block';o.textContent=t;o.scrollTop=o.scrollHeight;
 clearTimeout(show._t);show._t=setTimeout(()=>o.style.display='none',9000)}
function toggleFull(){const el=document.documentElement;
 try{if(!document.fullscreenElement){(el.requestFullscreen||el.webkitRequestFullscreen).call(el);$('fullCtl').classList.add('on');}
  else{(document.exitFullscreen||document.webkitExitFullscreen).call(document);$('fullCtl').classList.remove('on');}}catch(e){show('fullscreen not available in this browser - use Chrome menu > Add to Home screen for a tab-free app')}}
document.addEventListener('fullscreenchange',()=>$('fullCtl').classList.toggle('on',!!document.fullscreenElement));
async function doAct(act){
 if(act==='full')return toggleFull();
 if(act==='listen')return toggleListen();
 if(act==='speak')return speak();
 const a=ACT[act];if(!a)return;const el=document.querySelector('[data-act='+act+']');
 el.classList.add('busy');show(a[1]+' ...');
 try{const r=await F(a[0],{method:'POST'},a[2]);const d=await r.json();
  show(a[1]+'\\n'+(d.output||d.error||JSON.stringify(d)).slice(0,1600));}catch(e){show(a[1]+' failed: '+e)}
 el.classList.remove('busy')}
async function toggleListen(){const on=!$('listenCtl').classList.contains('on');
 try{const r=await F('/api/listen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on})},25000);
  const d=await r.json();setListen(d.listening)}catch(e){show('listen toggle failed: '+e)}}
function setListen(on){$('listenCtl').classList.toggle('on',!!on);$('listenT').textContent=on?'Listening':'Listen'}
function speak(){const t=prompt('Text for the car to speak:');if(!t)return;show('speaking ...');
 F('/api/car-speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})},35000)
 .then(r=>r.json()).then(d=>show(d.ok?'spoken':'speak: '+(d.error||d.output||'failed'))).catch(e=>show('speak failed: '+e))}
async function ask(){const t=$('q').value.trim();if(!t)return;const a=$('answer');
 a.style.display='block';a.textContent='thinking… (~1 min at 3.5 tok/s)';$('q').value='';
 clearTimeout(ask._t);ask._t=setTimeout(()=>a.style.display='none',20000);
 try{const r=await F('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:t,manual:true})},120000);
  const d=await r.json();a.textContent=d.answer||'(no answer)';ask._t=setTimeout(()=>a.style.display='none',25000);}
 catch(e){a.textContent='could not reach the car brain'}}
document.querySelectorAll('[data-act]').forEach(el=>el.addEventListener('click',()=>doAct(el.getAttribute('data-act'))));
$('askbtn').addEventListener('click',ask);$('q').addEventListener('keydown',e=>{if(e.key==='Enter')ask()});
const _tl=$('trustlink');if(_tl)_tl.addEventListener('click',async(e)=>{e.preventDefault();
 let w={};try{w=await(await F('/api/whoami')).json()}catch(_){}
 const ssid=w.ssid||'this network';
 if(w.on_home_wifi){show("'"+ssid+"' is already trusted - phones on it open the dash without a token.");return;}
 if(!confirm("Trust '"+ssid+"' as home?\\nEvery device on this wifi will open the dash WITHOUT a token. Do this only on your own home wifi or your own phone hotspot, never on cafe/public wifi."))return;
 try{const r=await F('/api/home-wifi/trust',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})},10000);
  const d=await r.json();show(d.ok?("Trusted '"+d.ssid+"'. Every phone on this wifi now opens the dash without a token."):("failed: "+(d.error||'')));}catch(e){show('trust failed: '+e)}});
// --- OBD (the car the Pi rides in) ---
function sev(u,k,n){if(!isFinite(n))return'';if(u&&u.indexOf('C')>=0&&k==='coolant_c')return n>=110?'bad':n>=95?'warn':'';
 if(k.includes('voltage'))return n<11.8||n>15?'bad':n<12.2?'warn':'';
 if(k.includes('battery'))return n<10?'bad':n<20?'warn':'';return''}
function flat(d){const o={};if(!d||!d.groups)return o;
 Object.values(d.groups).forEach(v=>Object.values(v).forEach(r=>{if(r&&r.key)o[r.key]=r}));return o}
const STAT=[['engine_rpm','engine rpm'],['hybrid_battery_pct','hybrid battery'],['module_voltage','12V system'],
 ['coolant_c','coolant'],['engine_load_pct','engine load']];
async function poll(){
 try{const s=await(await F('/api/status')).json();
  if(s.error==='token required'){$('status').innerHTML='<span style=color:#ffc857>open from the app link or Tailscale</span>';}
  else{const f=s.facts||{};$('status').innerHTML=(f.network||'')+' &middot; '+(f['your temperature']||'')+'<br><b>'+(f.uptime||'')+'</b>';
   if(s.listening!==undefined)setListen(s.listening);}
 }catch(e){$('status').innerHTML='<span style=color:#ff667d>cannot reach the car</span>'}
 try{const d=await(await F('/api/obd/all')).json();
  if(d&&d.groups&&Object.keys(d.groups).length){
   const m=flat(d);const spd=m.speed_kmh;
   $('spd').textContent=spd&&spd.value!=null?spd.value:'-';
   const stale=d.age_s!==undefined&&d.age_s>180;
   $('obdbadge').className='badge '+(stale?'stale':'live');
   $('obdbadge').textContent=stale?'stale · ign off':'live · '+(d.age_s!==undefined?Math.round(d.age_s)+'s':'now');
   const dt=Array.isArray(d.dtcs)?d.dtcs.length:0;
   let cells=STAT.map(([k,l])=>{const r=m[k]||{};const n=Number(r.value);
    return '<div class=stat><div class="v '+sev(r.unit||'',k,n)+'">'+(r.value==null?'-':r.value)+(k==='module_voltage'?'<span style=font-size:13px>V</span>':k.includes('pct')?'%':k==='coolant_c'?'&deg;':'')+'</div><div class=k>'+l+'</div></div>';});
   cells.push('<div class=stat><div class="v '+(dt?'warn':'')+'">'+dt+'</div><div class=k>fault codes</div></div>');
   $('stats').innerHTML=cells.join('');
  }else{$('spd').textContent='-';$('stats').innerHTML='<div class=nodev style="grid-column:1/-1">'+((d&&d.error)||'no engine data - ignition off?')+'</div>';}
 }catch(e){}
}
// Steering: petrus turns the wheel to see that the feed is alive, and a
// number alone does not show that - the bar does. Source is /api/can/summary
// (steering.last), CAN 0x0500 byte 0, 128 = straight (claudeMB's decode).
// It is a RAW byte, not degrees, and it is labelled that way until calibrated.
const STEER_CENTRE=128, STEER_SPAN=64;
// HIDDEN 2026-08-26. This read CAN 0x0500 byte 0, believed to be the steering
// angle. It is not. With petrus holding full left lock for 75 seconds the value
// stayed 127-128 across 29 samples - a spread of one. The 125-131 wobble I had
// earlier called "his turns" was noise around centre, and the 83-137 range seen
// during a drive is something that correlates with driving, not with steering
// input. The dial stays hidden until a signal is shown to track the wheel:
// hold full lock, watch for a large excursion, and only then label it.
async function pollSteer(){
 try{
  const d=await(await F('/api/steering')).json();
  const st=(d&&d.ok!==false&&d.value!=null)?{last:d.value}:null, w=$('steerwrap');
  if(!st||st.last==null){ $('steerval').textContent='-'; $('steerfill').style.width='0';
    w.className='steer stale'; return; }
  const centre=(d&&d.centre!=null)?Number(d.centre):STEER_CENTRE;
  const raw=Number(st.last), off=raw-centre;
  const frac=Math.max(-1,Math.min(1,off/STEER_SPAN)), pct=Math.abs(frac)*50;
  $('steerval').textContent=raw+(Math.abs(off)<2?' · centred':(off<0?' · left':' · right'));
  $('steerfill').style.width=Math.max(pct,1.5)+'%';
  $('steerfill').style.left=(frac<0? 50-pct : 50)+'%';
  // /api/can/summary summarises the last RECORDING on disk, not the live bus.
  // Say that on the dial. A frozen number that looks live is worse than no
  // number: you turn the wheel, nothing moves, and you conclude the car is
  // disconnected. petrus sat in the car doing exactly that. Swap the source
  // and this label together the moment a live CAN endpoint exists.
  // Say how old the sample is. Under ~15s is the wheel now; older means the
  // sampler stopped, and that must look different rather than sit there
  // pretending. Centre comes from the writer so one decode serves both ends.
  const age=(d&&d.age_s!=null)?d.age_s:null, stale=(age===null||age>15);
  $('steerlab').textContent = age===null ? 'steering wheel'
    : (stale ? 'steering wheel \u00b7 ' + Math.round(age) + 's old'
             : 'steering wheel \u00b7 live');
  w.className = stale ? 'steer replay' : 'steer';
 }catch(e){ $('steerval').textContent='-'; $('steerwrap').className='steer stale'; }
}

// --- Mercedes me cloud (one car at a time) ---
let CARS={},SEL=null;
function agg(o){if(!o)return null;if(o.all_closed!==undefined)return o.all_closed==='on'?['closed','ok']:['open','warn'];
 const v=Object.values(o);if(!v.length)return null;const n=v.filter(x=>x==='on'||x==='open').length;return n?[n+' open','warn']:['closed','ok']}
function mi(e,s,l,cls){return '<div class=mi><span class=e>'+e+'</span><div class="s '+(cls||'dim')+'">'+s+'</div><div class=l>'+l+'</div></div>'}
function renderTabs(){const t=$('tabs');const slugs=Object.keys(CARS);
 t.innerHTML=slugs.map(sl=>'<div class="tab'+(sl===SEL?' on':'')+'" data-car="'+sl+'">'+(CARS[sl].label||sl)+'</div>').join('');
 t.querySelectorAll('[data-car]').forEach(el=>el.addEventListener('click',()=>{SEL=el.getAttribute('data-car');renderTabs();renderCar()}))}
function renderCar(){const c=CARS[SEL];const g=$('merc');if(!c){g.innerHTML='';return}
 const p=[];const lk=c.lock&&String(c.lock.locked||'');
 if(lk){const L=(lk==='locked'||lk==='1'||lk==='2'),U=(lk==='unlocked'||lk==='0');
  p.push(mi(L?'&#128274;':(U?'&#128275;':'&#10067;'),L?'locked':(U?'unlocked':lk),'lock',L?'ok':(U?'bad':'')))}
 const w=agg(c.windows);if(w)p.push(mi('&#129695;',w[0],'windows',w[1]));
 const dr=agg(c.doors);if(dr)p.push(mi('&#128682;',dr[0],'doors',dr[1]));
 if(c.tires_kpa){const tv=Object.values(c.tires_kpa);const sp=Math.max(...tv)-Math.min(...tv);
  p.push(mi('&#128663;',tv.join('/'),'tyres kPa',sp>20?'warn':'ok'))}
 if(c.ev&&c.ev.soc_pct!==undefined){const pc=c.ev.soc_pct;
  p.push(mi('&#128267;',pc+'%'+(c.ev.range_km?' &middot; '+c.ev.range_km+'km':''),'charge',pc>50?'ok':(pc>20?'warn':'bad')))}
 if(c.fuel&&(c.fuel.level_pct!==undefined||c.fuel.range_km!==undefined))
  p.push(mi('&#9981;',[c.fuel.level_pct!==undefined?c.fuel.level_pct+'%':'',c.fuel.range_km!==undefined?c.fuel.range_km+'km':''].filter(Boolean).join(' &middot; '),'fuel',''));
 if(c.fuel&&c.fuel.adblue_pct!==undefined)p.push(mi('&#128167;',c.fuel.adblue_pct+'%','AdBlue',''));
 if(c.odometer_km!==undefined)p.push(mi('&#128207;',Math.round(c.odometer_km),'odometer km',''));
 g.innerHTML=p.join('');
 $('cmds').innerHTML='<button data-cmd=lock>&#128274; lock doors</button><button data-cmd=windows_close>&#129695; close windows</button>';
 $('cmds').querySelectorAll('[data-cmd]').forEach(b=>b.addEventListener('click',()=>carCmd(b)))}
async function carCmd(btn){const action=btn.getAttribute('data-cmd');const label=btn.textContent;
 if(!confirm(label.trim()+' - '+(CARS[SEL].label||SEL)+'?\\nThis sends a real command to the car.'))return;
 const sibs=Array.from(btn.parentNode.children);sibs.forEach(b=>b.disabled=true);btn.className='';btn.textContent='sending…';
 try{const r=await F('/api/cloudcar/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({car:SEL,action})},20000);
  const d=await r.json();if(d.ok){btn.className='ok';btn.textContent='sent ✓';[4000,12000,30000].forEach(ms=>setTimeout(pollCloud,ms));}
  else{btn.className='bad';btn.textContent=(d.error||'failed').slice(0,30);}}
 catch(e){btn.className='bad';btn.textContent='error'}
 setTimeout(()=>{sibs.forEach(b=>b.disabled=false);renderCar()},4000)}
async function pollCloud(){
 try{const s=await(await F('/api/cloudcar',{},8000)).json();
  if(!s.ok&&!s.cars){$('mnote').innerHTML='mercedes cloud: '+((s.error||'no data'))+(/token|not connected/i.test(s.error||'')?' &middot; <a href='+_q('/cloudcar')+' style=color:#62c7ff>set up</a>':'');$('merc').innerHTML='';$('cmds').innerHTML='';return}
  CARS=s.cars||{};if(!SEL||!CARS[SEL])SEL=Object.keys(CARS)[0]||null;
  renderTabs();renderCar();
  $('mnote').innerHTML=s.stale?('&#9888;&#65039; '+(s.note||'last known, not live')):('from Mercedes cloud '+Math.round((Date.now()/1000)-s.fetched_at)+'s ago &middot; only lock / close-windows can be sent');
 }catch(e){$('mnote').textContent='mercedes cloud unreachable: '+e}
}
poll();setInterval(poll,2000);
pollCloud();setInterval(pollCloud,30000);
// 1s so the bar visibly tracks the wheel while it is being turned.
pollSteer();setInterval(pollSteer,1000);
</script></body></html>"""

# Playback of the last ATMA capture. Live ATMA wedges the ELM, so this page
# reads the file, not the adapter. Frames are 12 hex bytes: 2-byte ID, 00 00
# pad, 8 data. Steering candidate is 0x0500 D0 around 128, not proven.
STREAMS_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>CarWatch streams</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#091016;color:#e8f1f5;font:15px/1.4 -apple-system,system-ui,sans-serif;padding:12px}
h1{font-size:18px;margin-bottom:8px}
#note{color:#8ca1ad;font-size:12px;margin-bottom:12px}
.wheel-wrap{display:flex;align-items:center;gap:16px;margin:12px 0 18px}
.wheel{width:120px;height:120px;border-radius:50%;border:8px solid #20313c;background:#13232c;
  display:flex;align-items:center;justify-content:center;position:relative}
.spoke{width:8px;height:70%;background:#62c7ff;border-radius:4px}
#ang{font:700 22px ui-monospace,monospace;color:#40d98b}
.bar{display:flex;align-items:center;gap:8px;margin:4px 0}
.bar b{width:72px;font:12px ui-monospace,monospace;color:#62c7ff}
.bar .t{flex:1;height:14px;background:#13232c;border-radius:7px;overflow:hidden}
.bar .t i{display:block;height:100%;background:#40d98b;width:0}
.bar .n{width:90px;font:11px ui-monospace,monospace;color:#8ca1ad}
a{color:#62c7ff}
</style></head><body>
<h1>CAN streams</h1>
<div id=note>Last capture, not live. Steering is a candidate, not proven.</div>
<div class=wheel-wrap>
  <div class=wheel id=wheel><div class=spoke></div></div>
  <div><div id=ang>--</div><div style="color:#8ca1ad;font-size:12px">0x0500 D0 around 128</div></div>
</div>
<div id=bars></div>
<div style="margin-top:14px"><a href="/dash">dash</a></div>
<script>
const F=(u)=>{const t=new URLSearchParams(location.search).get('t')||'';
  return fetch(t?(u+(u.includes('?')?'&':'?')+'t='+encodeURIComponent(t)):u)};
F('/api/can/summary').then(r=>r.json()).then(d=>{
  const note=document.getElementById('note');
  if(!d.ok){note.textContent=d.error||'no capture';return}
  note.textContent=(d.file||'capture')+' · '+d.frames+' frames · '+d.seconds+'s · 0 DATA ERROR in this file';
  const st=d.steering||{};
  const deg=((st.last||128)-128)*1.2;
  document.getElementById('wheel').style.transform='rotate('+deg+'deg)';
  document.getElementById('ang').textContent=(st.last==null?'--':st.last)+'  min '+st.min+' max '+st.max;
  const bars=document.getElementById('bars');
  const max=(d.streams&&d.streams[0]&&d.streams[0].n)||1;
  (d.streams||[]).forEach(s=>{
    const row=document.createElement('div'); row.className='bar';
    row.innerHTML='<b>'+s.id+'</b><div class=t><i></i></div><div class=n>'+s.hz+' Hz · '+s.n+'</div>';
    row.querySelector('i').style.width=Math.round(100*s.n/max)+'%';
    bars.appendChild(row);
  });
}).catch(e=>{document.getElementById('note').textContent=String(e)});
</script></body></html>"""

DASH_PAGE = '''<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>vadelma dash</title></head>
<body style="background:#111;color:#ddd;font:14px monospace;margin:0;padding:10px">
<div style="display:flex;gap:8px;margin-bottom:8px">
<a href="/" style="flex:1;background:#1f6feb;color:#fff;text-align:center;padding:12px;border-radius:10px;text-decoration:none;font-size:16px">&#128172; juttele autolle</a>
<a href="/nerd" style="flex:1;background:#233;color:#6cf;border:1px solid #456;text-align:center;padding:12px;border-radius:10px;text-decoration:none;font-size:16px">&#128300; nerd dash</a>
</div>
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
<div style="color:#6cf;margin-top:6px">CAR AUDIO (Pi speaks through your car speakers)</div>
<div style="margin:4px 0;color:#888;font:12px monospace">Put MBUX in Bluetooth pairing mode (Settings &gt; Bluetooth &gt; add device) WHILE PARKED, then tap pair.</div>
<div style="margin:4px 0"><button onclick="carPair()" style="background:#333;color:#fc6;border:1px solid #555;padding:8px 10px;font:13px monospace">pair car audio</button>
<input id=saytext placeholder="text to speak" style="background:#222;color:#ddd;border:1px solid #555;padding:6px;font:13px monospace;width:40%">
<button onclick="carSpeak()" style="background:#333;color:#6f6;border:1px solid #555;padding:8px 10px;font:13px monospace">speak</button></div>
<pre id=carout style="margin:2px 0;white-space:pre-wrap;color:#9cf;max-height:140px;overflow:auto"></pre>
<div style="color:#6cf;margin-top:6px">NETWORK</div>
<div style="margin:4px 0">
<button onclick="wifi('hotspot')" style="background:#333;color:#fc6;border:1px solid #555;padding:8px 10px;font:13px monospace">phone hotspot</button>
<button onclick="wifi('ap')" style="background:#333;color:#6cf;border:1px solid #555;padding:8px 10px;font:13px monospace">own network</button>
<button onclick="wifiScan()" style="background:#333;color:#6f6;border:1px solid #555;padding:8px 10px;font:13px monospace">scan</button>
</div>
<div id=saved style="margin:4px 0"></div>
<pre id=scan style="margin:2px 0;white-space:pre-wrap;color:#9e9;max-height:180px;overflow:auto"></pre>
<details style="margin:4px 0;color:#888"><summary>join a seen network</summary>
<input id=ssid placeholder="SSID from scan" style="background:#222;color:#ddd;border:1px solid #555;padding:6px;font:13px monospace;width:40%">
<input id=psk placeholder=password type=text autocomplete=off style="background:#222;color:#ddd;border:1px solid #555;padding:6px;font:13px monospace;width:40%">
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
async function carPair(){
  var o=document.getElementById("carout"); o.textContent="pairing... scanning 20s for your car's Bluetooth (keep MBUX in pairing mode)";
  try{ const r=await fetch("/api/car-pair",{method:"POST"}); const d=await r.json();
    o.textContent = (d.ok?"paired - you should hear a test line now\\n":"") + (d.output||d.error||"");
  }catch(e){ o.textContent="request failed: "+e; }
}
async function carSpeak(){
  var o=document.getElementById("carout"); var t=(document.getElementById("saytext").value||"").trim();
  if(!t){ o.textContent="type something to speak first"; return; }
  o.textContent="speaking...";
  try{ const r=await fetch("/api/car-speak",{method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({text:t})}); const d=await r.json();
    o.textContent = d.ok ? "spoken" : ("error: "+(d.output||d.error));
  }catch(e){ o.textContent="request failed: "+e; }
}
tick(); setInterval(tick, 2000);
async function wifi(target){
  if(!confirm("Switch the car to " + target + "? This page will drop and come back on the new network.")) return;
  const r = await fetch("/api/wifi", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({target})});
  document.getElementById("netmsg").textContent = (await r.json()).note || "switching...";
}
async function wifiUp(ssid){
  if(!confirm("Switch the car to " + ssid + "?")) return;
  const r = await fetch("/api/wifi", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({target:"up", ssid})});
  const d = await r.json();
  document.getElementById("netmsg").textContent = d.note || d.error || "switching...";
}
async function wifiScan(){
  const box = document.getElementById("scan");
  box.textContent = "scanning...";
  try{
    const d = await (await fetch("/api/wifi/scan")).json();
    const rows = d.networks || [];
    if(!rows.length){ box.textContent = d.error || "no networks seen"; return; }
    box.innerHTML = "";
    rows.forEach(n => {
      const b = document.createElement("button");
      b.textContent = (n.in_use ? "* " : "") + n.ssid + "  " + n.signal + "%  " + (n.security||"");
      b.style.cssText = "display:block;margin:2px 0;background:#222;color:#ddd;border:1px solid #555;padding:6px;font:12px monospace;width:100%;text-align:left";
      b.type = "button";
      b.onclick = () => {
        document.getElementById("ssid").value = n.ssid;
        document.querySelector("details").open = true;
        box.querySelectorAll("button").forEach(x => x.style.borderColor = "#555");
        b.style.borderColor = "#6f6";
        document.getElementById("psk").focus();
      };
      box.appendChild(b);
    });
  }catch(e){ box.textContent = "scan failed: " + e; }
}
async function wifiSaved(){
  const el = document.getElementById("saved");
  try{
    const d = await (await fetch("/api/wifi/saved")).json();
    const rows = d.saved || [];
    el.innerHTML = "";
    rows.forEach(n => {
      const b = document.createElement("button");
      b.textContent = n;
      b.style.cssText = "margin:2px 4px 2px 0;background:#333;color:#6f6;border:1px solid #555;padding:6px 10px;font:12px monospace";
      b.onclick = () => wifiUp(n);
      el.appendChild(b);
    });
    if(!rows.length) el.textContent = "no saved wifi yet";
  }catch(e){ el.textContent = ""; }
}
wifiSaved();
wifiScan();
async function wifiAdd(){
  const m = document.getElementById("netmsg");
  const ssid = document.getElementById("ssid").value, password = document.getElementById("psk").value;
  if(!ssid || password.length < 8){ m.textContent = "need network name + password (8+)"; return; }
  const r = await fetch("/api/wifi", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({target:"add", ssid, password})});
  const d = await r.json();
  m.textContent = d.note || d.error || "sent";
  // Poll the REAL result: nmcli's outcome lands in /api/wifi/status.
  for(let i=0;i<25;i++){
    await new Promise(res=>setTimeout(res,2000));
    try{
      const st = await (await fetch("/api/wifi/status")).json();
      const res = st.result || {};
      if(res.state === "working"){ m.textContent = "joining '"+res.ssid+"' ..."; continue; }
      if(res.state === "done"){
        m.textContent = (res.ok ? "JOINED '" : "FAILED joining '") + res.ssid + "'"
          + (res.ok ? "" : " - " + (res.nmcli||"no detail"))
          + "  |  active now: " + (st.active||"?").replace(/\\n/g,", ");
        return;
      }
    }catch(e){ m.textContent = "page lost the car (network switch in progress?) - reload in a moment"; }
  }
  m.textContent += "  (no final result after 50s - reload and check NETWORK)";
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


# PWA manifest + icon so the dash installs as a tab-free app on the phone
# (petrus, Aug 26: "make it full screen, no browser tabs"). Served WITHOUT
# a token from do_GET (see the public-asset block there). display:fullscreen
# hides browser chrome AND the status bar once launched from the home-screen
# icon; the in-page Full button uses the Fullscreen API for the same effect
# without installing.
MANIFEST_JSON = '{"name": "CarWatch", "short_name": "CarWatch", "description": "Live OBD + Mercedes me dashboard for the car", "start_url": "/dash", "scope": "/", "display": "fullscreen", "background_color": "#091016", "theme_color": "#091016", "icons": [{"src": "/icon.png", "sizes": "192x192", "type": "image/png", "purpose": "any"}, {"src": "/icon.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}, {"src": "/icon.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}'

DASH_ICON_PNG_B64 = (
    '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'
    'REFUAwBGNF72W5kGMAAAAABJRU5ErkJggg==')


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        if "html" in ctype and not isinstance(body, bytes):
            from urllib.parse import urlparse, parse_qs
            _t = parse_qs(urlparse(self.path).query).get("t", [""])[0]
            if _t and "</body>" in body:
                _inj = ("<script>(function(){var t=new URLSearchParams("
                        "location.search).get('t');if(!t)return;"
                        "document.querySelectorAll('a[href^=\"/\"]').forEach("
                        "function(a){var u=a.getAttribute('href');"
                        "a.setAttribute('href',u+(u.indexOf('?')>-1?'&':'?')+'t='"
                        "+encodeURIComponent(t))});})();</script></body>")
                body = body.replace("</body>", _inj, 1)
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "html" in ctype:
            # The dashboard changed several times in one day; a cached copy
            # kept showing the old broken version (petrus, Aug 25).
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    # ── Access control ──────────────────────────────────────────────────
    # claudemm found the dashboard had NO authentication while it was exposed
    # through a public tunnel: anyone holding the URL could read the car and
    # call /api/update, /api/obd/deep, /api/car-speak. petrus, Aug 22: "ok tee
    # tunnistautuminen mutta joku helppo ja nopea."
    #
    # Easy and fast means he types nothing at home. Requests arriving on the
    # local network stay open; requests that came through the tunnel must
    # carry the token. The tunnel is identifiable because cloudflared adds
    # forwarding headers - the socket address is useless here, since
    # cloudflared connects from localhost and would otherwise look local.
    TOKEN_PATH = os.path.expanduser("~/.carwatch/dash-token")

    @classmethod
    def _token(cls) -> str:
        try:
            with open(cls.TOKEN_PATH) as fh:
                t = fh.read().strip()
                if t:
                    return t
        except Exception:
            pass
        # First run: mint one and keep it. 32 hex chars is unguessable and
        # still fits in a URL a person can paste.
        import secrets
        t = secrets.token_hex(16)
        try:
            os.makedirs(os.path.dirname(cls.TOKEN_PATH), exist_ok=True)
            with open(cls.TOKEN_PATH, "w") as fh:
                fh.write(t)
            os.chmod(cls.TOKEN_PATH, 0o600)
        except Exception:
            pass
        return t

    def _came_through_tunnel(self) -> bool:
        for h in ("cf-connecting-ip", "x-forwarded-for", "cf-ray"):
            if self.headers.get(h):
                return True
        return False

    # "Is the Pi on a home SSID?" is cheap but not free (a subprocess + a file
    # read), so the verdict is cached briefly. The car changes networks only at
    # a drive's start/end, so a short TTL never goes stale in practice.
    _home_wifi_cache = (0.0, False)

    @classmethod
    def _pi_on_home_wifi(cls) -> bool:
        """True when the Pi itself is currently associated with one of the
        owner's home SSIDs (config `home_ssids`). This is what makes it safe to
        trust the local LAN token-less: on the home network the other clients
        are the owner's household, not strangers on shared wifi. When the car
        roams (SSID not in home_ssids, or wifi down), this is False and a token
        is required, so the cafe/hotel/hotspot protection is untouched. Fail
        closed on any error - unknown SSID or missing config means 'not home'."""
        import time
        now = time.time()
        ts, val = cls._home_wifi_cache
        if now - ts < 30:
            return val
        val = False
        try:
            with open(os.path.expanduser("~/.carwatch/config.json")) as fh:
                homes = {s.strip() for s in (json.load(fh).get("home_ssids") or [])
                         if s and s.strip()}
            if homes:
                from carwatch.trips import current_ssid
                ssid = current_ssid()
                val = bool(ssid and ssid in homes)
        except Exception:
            val = False
        cls._home_wifi_cache = (now, val)
        return val

    def _peer_is_owner(self) -> bool:
        """The REAL connecting address, from the socket - not a spoofable
        header. A token-less request is trusted from the Pi itself (loopback),
        the owner's Tailscale mesh (100.64/10), OR the local LAN WHILE THE PI IS
        ON A HOME SSID. It is NOT trusted from a generic private LAN when the
        car is roaming: cafe / hotel / airport wifi and phone hotspots put every
        other client on 192.168.x / 10.x too, and trusting those would hand a
        stranger the dangerous routes (/api/update = code execution). The
        home-SSID gate is what separates 'my home network' from 'some shared
        wifi'. Fail closed on anything we can't parse. (codexmb header-spoof +
        claudemm foreign-LAN + petrus's own-home-phones-locked-out, Aug 26.)"""
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except (ValueError, IndexError, TypeError):
            return False
        if ip.is_loopback:
            return True
        if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
        # At home, trust the local LAN token-less (see _pi_on_home_wifi).
        if (ip.is_private or ip.is_link_local) and self._pi_on_home_wifi():
            return True
        return False

    def _authorised(self) -> bool:
        # A valid token authorises from anywhere.
        want = self._token()
        auth = (self.headers.get("Authorization") or "").strip()
        if want and auth.lower().startswith("bearer ") and \
                self._const_eq(auth[7:].strip(), want):
            return True
        from urllib.parse import urlparse, parse_qs
        got = parse_qs(urlparse(self.path).query).get("t", [""])[0]
        if want and self._const_eq(got, want):
            return True
        # No valid token. Trust the request ONLY if it did not arrive through
        # the tunnel AND its real socket peer is the Pi itself or the owner's
        # Tailscale mesh. A generic private LAN is NOT trusted: on shared
        # cafe/hotel/hotspot wifi that is full of strangers. This closes both
        # codexmb's header-spoof bypass and claudemm's foreign-LAN case.
        return (not self._came_through_tunnel()) and self._peer_is_owner()

    @staticmethod
    def _const_eq(a: str, b: str) -> bool:
        import hmac
        return hmac.compare_digest(a, b)

    def _deny(self):
        self._send(401, json.dumps({"ok": False, "error": "token required"}),
                   "application/json")

    def do_GET(self):
        # Public PWA assets: the browser fetches these anonymously (no token)
        # when deciding whether the dash is installable, so they must not 401.
        # They expose nothing sensitive - a static manifest and an app icon.
        _pub = self.path.split("?", 1)[0]
        if _pub == "/manifest.webmanifest":
            return self._send(200, MANIFEST_JSON, "application/manifest+json")
        if _pub == "/icon.png":
            import base64 as _b64
            return self._send(200, _b64.b64decode(DASH_ICON_PNG_B64), "image/png")
        if not self._authorised():
            return self._deny()
        # Compare WITHOUT the query string: the published tunnel link now
        # carries ?t=<token>, so a literal match on "/" stopped matching and
        # the front page 404'd. Only the exact-match routes need this; the
        # startswith routes below already tolerate a query.
        if self.path.split("?", 1)[0] == "/api/obd/record/latest":
            # Fetch the newest capture for offline decoding on a bigger
            # machine. Text, capped, newest file wins.
            import glob as _glob, os as _os
            logs = sorted(_glob.glob(_os.path.expanduser("~/.carwatch/can-logs/rec-*.log")))
            alt = sorted(_glob.glob("/root/.carwatch/can-logs/rec-*.log"))
            logs = logs or alt
            if not logs:
                return self._send(200, json.dumps({"ok": False, "error": "no captures yet"}),
                                  "application/json")
            with open(logs[-1], "rb") as f:
                data = f.read()[-2_000_000:]
            return self._send(200, data, "text/plain")
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            # "/" now serves the single-screen dash too, so an existing
            # bookmark to the root lands on the new view instead of the old
            # front page (petrus + claudemm both hit "/" and saw the old page
            # after the dash shipped at "/dash"). Old page still at /legacy.
            self._send(200, UNIFIED_PAGE)
        elif self.path.split("?", 1)[0] == "/legacy":
            self._send(200, PAGE)
        elif self.path.split("?", 1)[0] == "/api/whoami":
            # Auth diagnostic: confirms token-less LAN trust will actually fire
            # (home_ssids populated + current SSID matches) before anyone is
            # told to reload without a token. Authorised-only; coarse info.
            try:
                from carwatch.trips import current_ssid as _cs
                ssid = _cs()
            except Exception:
                ssid = None
            try:
                with open(os.path.expanduser("~/.carwatch/config.json")) as _fh:
                    _homes = [s for s in (json.load(_fh).get("home_ssids") or []) if s]
            except Exception:
                _homes = []
            self._send(200, json.dumps({
                "peer": self.client_address[0],
                "is_owner": self._peer_is_owner(),
                "on_home_wifi": self._pi_on_home_wifi(),
                "ssid": ssid,
                "home_ssids": _homes,
                "came_through_tunnel": self._came_through_tunnel(),
            }), "application/json")
        elif self.path == "/api/wifi/scan":
            import subprocess as _sp
            import time as _time
            try:
                # `list --rescan yes` only REQUESTS a scan and lists the stale
                # cache immediately - and an unprivileged request can be
                # refused by NM outright, which is why the list stayed at just
                # the connected hotspot all evening (Aug 16/17: Pi on the
                # KITCHEN TABLE showed one network). Force the scan as root,
                # give the radio a moment to finish, then read the results.
                _sp.run(["sudo", "nmcli", "dev", "wifi", "rescan"],
                        capture_output=True, text=True, timeout=15)
                _time.sleep(4)
                out = _sp.run(
                    ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                     "dev", "wifi", "list"],
                    capture_output=True, text=True, timeout=40,
                ).stdout.splitlines()

                def nmcli_fields(line):
                    """Split nmcli terse output while honoring escaped colons."""
                    fields, field, escaped = [], [], False
                    for char in line:
                        if escaped:
                            field.append(char)
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == ":":
                            fields.append("".join(field))
                            field = []
                        else:
                            field.append(char)
                    if escaped:
                        field.append("\\")
                    fields.append("".join(field))
                    return fields

                seen = set()
                networks = []
                for line in out:
                    parts = nmcli_fields(line)
                    if len(parts) < 3:
                        continue
                    in_use = parts[0] == "*"
                    ssid = parts[1].strip()
                    if not ssid or ssid in seen:
                        continue
                    seen.add(ssid)
                    networks.append({
                        "ssid": ssid,
                        "signal": parts[2],
                        "security": parts[3] if len(parts) > 3 else "",
                        "in_use": in_use,
                    })
                return self._send(200, json.dumps({"networks": networks}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}),
                                  "application/json")
        elif self.path == "/api/wifi/saved":
            import subprocess as _sp
            try:
                out = _sp.run(
                    ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.splitlines()
                saved = []
                skip = {"phone-hotspot", "vadelma-ap"}
                for line in out:
                    name, _, typ = line.partition(":")
                    if typ.strip() in ("802-11-wireless", "wifi") and name not in skip:
                        saved.append(name)
                return self._send(200, json.dumps({"saved": saved}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}),
                                  "application/json")
        elif self.path == "/api/wifi/status":
            import subprocess as _sp
            try:
                result = {}
                try:
                    result = json.load(open("/tmp/wifi-add-result.json"))
                except Exception:
                    result = {"state": "none"}
                act = _sp.run(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
                return self._send(200, json.dumps(
                    {"result": result, "active": act}), "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}), "application/json")
        elif self.path.split("?", 1)[0] == "/api/room/latest":
            # Latest GroupMind line plus latest car-handle / car-mention.
            # Key stays on the Pi. Browser only sees truncated bodies.
            import time as _time
            now = _time.time()
            cache = getattr(self.__class__, "_room_latest_cache", None)
            if cache and now - cache[0] < 8:
                return self._send(200, json.dumps(cache[1]), "application/json")
            try:
                cfg_path = os.path.expanduser("~/.carwatch/config.json")
                with open(cfg_path) as fh:
                    cfg = json.load(fh)
                from carwatch.room import RoomClient
                msgs = RoomClient(cfg.get("api_base") or "https://groupmind.one",
                                  cfg["api_key"], cfg["room"]).fetch(limit=80)
                handle = (cfg.get("handle") or "@eclass").lower().lstrip("@")

                def clip(m):
                    body = (m.get("body") or "").strip().replace("\n", " ")
                    return {"from": m.get("from") or m.get("from_name") or "",
                            "body": body[:180],
                            "created_at": m.get("created_at") or ""}

                latest = clip(msgs[0]) if msgs else None
                car = None
                for m in msgs:
                    who = ((m.get("from") or "") + " " + (m.get("from_name") or "")).lower()
                    body = (m.get("body") or "").lower()
                    if handle in who or "eclass" in who or "e-class" in who or handle in body or "engine read" in body:
                        car = clip(m)
                        break
                payload = {"ok": True, "latest": latest, "car": car}
            except Exception as e:
                payload = {"ok": False, "error": str(e), "latest": None, "car": None}
            self.__class__._room_latest_cache = (now, payload)
            return self._send(200, json.dumps(payload), "application/json")
        elif self.path.split("?", 1)[0] == "/api/can/summary":
            import glob as _glob, os as _os
            logs = sorted(_glob.glob(_os.path.expanduser("~/.carwatch/can-logs/rec-*.log")))
            if not logs:
                return self._send(200, json.dumps({"ok": False, "error": "no captures yet"}),
                                  "application/json")
            path = logs[-1]
            try:
                mtime = _os.path.getmtime(path)
            except Exception:
                mtime = 0
            cache = getattr(self.__class__, "_can_summary_cache", None)
            if cache and cache[0] == path and cache[1] == mtime:
                return self._send(200, json.dumps(cache[2]), "application/json")
            counts = {}
            steer = []
            n = 0
            t0 = t1 = None
            with open(path, errors="replace") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) < 13 or parts[0].startswith("{"):
                        continue
                    hx = parts[1:]
                    try:
                        [int(x, 16) for x in hx[:12]]
                    except Exception:
                        continue
                    n += 1
                    try:
                        ts = float(parts[0])
                    except Exception:
                        ts = None
                    if ts is not None:
                        t0 = ts if t0 is None else t0
                        t1 = ts
                    # 12 hex tokens: 2-byte ID, 00 00 pad, 8 data (claudeMB, 2026-08-25)
                    key = (hx[0] + hx[1]).upper()
                    counts[key] = counts.get(key, 0) + 1
                    if key == "0500":
                        steer.append(int(hx[4], 16))
            seconds = round((t1 - t0), 1) if t0 and t1 else 120.0
            streams = [{"id": "0x" + k, "n": v, "hz": round(v / seconds, 2)}
                       for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
            payload = {
                "ok": True, "file": _os.path.basename(path), "frames": n,
                "seconds": seconds, "streams": streams,
                "steering": ({"min": min(steer), "max": max(steer),
                              "last": steer[-1], "n": len(steer)} if steer else None),
            }
            self.__class__._can_summary_cache = (path, mtime, payload)
            return self._send(200, json.dumps(payload), "application/json")
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
        elif self.path.startswith("/api/obd/all"):
            # FULL decoded OBD snapshot for the nerd dashboard, grouped
            # (petrus, Aug 19: "purkakaa kaikki saadut tiedot"). GET so any
            # dashboard can poll it. ?mock=1 returns a realistic sample with
            # the exact live shape, so the UI can be built while the adapter
            # is in the car.
            import os as _os
            import time as _time
            if "mock=1" in self.path:
                return self._send(200, json.dumps(_OBD_ALL_MOCK),
                                  "application/json")
            # Serve the CACHE obdwatch maintains: one process owns the serial
            # port, everyone else reads this file - so a 2s-polling dashboard
            # never contends the single-reader ELM327 (claudemm, Aug 19).
            # age_s tells the UI how fresh the data is; it must show it.
            _cache = _os.path.expanduser("~/.carwatch/obd-all.json")
            if _os.path.exists(_cache):
                try:
                    with open(_cache) as f:
                        d = json.load(f)
                    d["age_s"] = round(_time.time() - d.get("ts", 0), 1)
                    d["source"] = "cache"
                    return self._send(200, json.dumps(d), "application/json")
                except Exception:
                    pass  # unreadable cache -> report that no snapshot exists
            # Never fall back to a second live reader here. obdwatch owns the
            # one ELM327 serial connection and will publish the first cache
            # snapshot after it has a successful car read.
            return self._send(200, json.dumps(
                {"ok": False,
                 "error": "waiting for the first OBD cache snapshot",
                 "hint": "GET /api/obd/all?mock=1 for UI development"}),
                "application/json")
        elif self.path.startswith("/api/steering"):
            # The wheel, live. obdwatch samples it between OBD passes and
            # writes carwatch.steering's cache; this just serves that file.
            # age_s is computed here rather than trusted from the writer, so
            # a stopped sampler shows up as an ageing number instead of a
            # value that looks current forever. petrus turns the wheel to see
            # that the feed is alive - a stale reading that cannot be told
            # apart from a live one is the whole failure this replaces.
            import json as _j, time as _t, os as _o
            _sp = _o.path.expanduser(
                _o.environ.get("CARWATCH_STATE", "~/.carwatch")) + "/steering.json"
            try:
                with open(_sp) as _f:
                    _d = _j.load(_f)
                _ts = _d.get("ts")
                _d["age_s"] = round(_t.time() - _ts, 1) if _ts else None
            except FileNotFoundError:
                _d = {"ok": False, "error": "no steering sample yet"}
            except Exception as _e:
                _d = {"ok": False, "error": str(_e)[:120]}
            self._send(200, _j.dumps(_d), "application/json")
        elif self.path.startswith("/api/cloudcar"):
            # The "mokkula": normalized cloud vehicle data behind a pluggable
            # provider interface (carwatch.cloudcar). Read-only by design -
            # remote commands live in petrus's own phone apps, never here.
            from carwatch import cloudcar as _cc
            prov = _cc.get()
            if self.path.startswith("/api/cloudcar/auth"):
                body = (prov.auth_state() if prov else
                        {"authenticated": False, "step": "no_provider",
                         "hint": "no cloud provider plugin registered yet"})
                return self._send(200, json.dumps(body), "application/json")
            if prov is None:
                return self._send(200, json.dumps(_cc.empty_state(
                    "none", "no cloud provider plugin registered yet")),
                    "application/json")
            return self._send(200, json.dumps(prov.status()),
                              "application/json")
        elif self.path.startswith("/cloudcar"):
            # Minimal setup + status page for the cloud provider: email ->
            # vendor emails a one-time code -> code -> tokens on the Pi.
            # The owner's PASSWORD is never asked anywhere on this page.
            return self._send(200, CLOUDCAR_PAGE)
        elif self.path.startswith("/streams"):
            return self._send(200, STREAMS_PAGE)
        elif self.path.startswith("/nerd"):
            # Nerd dashboard (codex-authored carwatch/nerd.html): every decoded
            # PID in grouped cards with threshold colors + sparklines, fed by
            # /api/obd/all (petrus: "nörttien oma tabi"). Read from disk per
            # request so a git pull updates the page without a restart.
            try:
                with open(os.path.join(REPO, "carwatch", "nerd.html"),
                          encoding="utf-8") as f:
                    return self._send(200, f.read())
            except Exception as e:
                return self._send(404, f"nerd.html missing: {e}")
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
            self._send(200, UNIFIED_PAGE)
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
        # Writes and probes are the dangerous half: gate them the same way.
        if not self._authorised():
            return self._deny()
        # Tunnel links append ?t=token. Exact path match 404'd Read now
        # (petrus in-car, Aug 25). Auth still reads the query on self.path.
        path = self.path.split("?", 1)[0]
        if path == "/api/update":
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
        if path == "/api/obd":
            # One-tap OBD from the dashboard: runs the COMPLETE session
            # (eth0 up -> gateway discovery -> routing activation -> PID
            # reads) and returns the stage-by-stage JSON trace, so a failure
            # says exactly how far it got. Same code path the zero-touch
            # obdwatch daemon uses; proven end-to-end against the fake
            # gateway before ever touching the car.
            import subprocess as _sp, os as _os, glob as _glob
            # ELM327 first (Aug 14): when a USB/BT adapter is present, read
            # through it - the DoIP session below only applies to the old
            # ENET cable and always fails on the GLE.
            _elm = next((p for p in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0")
                         if _os.path.exists(p)), None)
            _mod = "carwatch.elm327" if _elm else "carwatch.obd_session"
            try:
                r = _sp.run(
                    ["sudo", "python3", "-m", _mod],
                    capture_output=True, text=True, timeout=60,
                    cwd=_os.path.expanduser("~/CarWatch"),
                    env={**_os.environ, "CARWATCH_STATE": _os.path.expanduser("~/.carwatch")})
                out = (r.stdout + r.stderr).strip()[-3000:]
                return self._send(200, json.dumps({"ok": True, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/bt-pair":
            # One-shot wireless-OBD swap: scan for the BT dongle (Vgate iCar
            # Pro 2S), pair, bind /dev/rfcomm0, persist, test. Read-only w.r.t.
            # the car (BT pairing only). Runs the committed script.
            import subprocess as _sp, os as _os
            try:
                r = _sp.run(
                    ["sudo", "bash", _os.path.expanduser("~/CarWatch/scripts/pair-bt-obd.sh"), "auto"],
                    capture_output=True, text=True, timeout=90,
                    cwd=_os.path.expanduser("~/CarWatch"))
                out = (r.stdout + r.stderr).strip()[-4000:]
                return self._send(200, json.dumps({"ok": r.returncode == 0, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/cloudcar/ha-url":
            # Repoint the Mercedes provider at a new Home Assistant URL at
            # runtime (e.g. a Nabu Casa / tunnel URL) without SSH or redeploy,
            # so cloud data can work on the road the moment HA has an internet
            # address. Body: {"url": "https://..."} ("" clears the override).
            try:
                from carwatch import mercedesme as _mm
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))) or b"{}")
                res = _mm.set_ha_url(str(body.get("url", "")).strip())
                if res.get("ok"):
                    from carwatch import cloudcar as _cc
                    prov = _cc.get()
                    if prov is not None:
                        prov._cache_at = 0.0  # force a fresh fetch next poll
                return self._send(200, json.dumps(res), "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/home-wifi/trust":
            # Add (or remove) a wifi network to the trusted-home list so every
            # device on it opens the dash token-less. petrus-controlled: he taps
            # this from an already-open dash while on a network he owns (home
            # wifi or his own phone hotspot). Body {"ssid": "..."} defaults to
            # the Pi's current SSID; {"remove": true} drops it. Read-modify-write
            # the config; clear the auth cache so it takes effect at once.
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))) or b"{}")
                ssid = str(body.get("ssid", "")).strip()
                if not ssid:
                    from carwatch.trips import current_ssid
                    ssid = current_ssid() or ""
                if not ssid:
                    return self._send(200, json.dumps(
                        {"ok": False, "error": "no wifi network detected"}),
                        "application/json")
                cfg_path = os.path.expanduser("~/.carwatch/config.json")
                with open(cfg_path) as fh:
                    cfg = json.load(fh)
                homes = [s for s in (cfg.get("home_ssids") or []) if s]
                remove = bool(body.get("remove"))
                if remove:
                    homes = [s for s in homes if s != ssid]
                elif ssid not in homes:
                    homes.append(ssid)
                cfg["home_ssids"] = homes
                tmp = cfg_path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(cfg, fh, indent=2)
                os.replace(tmp, cfg_path)
                self.__class__._home_wifi_cache = (0.0, False)  # force re-eval
                return self._send(200, json.dumps(
                    {"ok": True, "ssid": ssid, "trusted": not remove,
                     "home_ssids": homes}), "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/cloudcar/cmd":
            # Owner-tapped, allowlisted make-safe commands (lock, close
            # windows). The provider enforces the allowlist; unlock/open do
            # not exist in it. (petrus, Aug 26: "make the lock doors and
            # windows button work")
            from carwatch import cloudcar as _cc
            prov = _cc.get()
            if prov is None or not hasattr(prov, "command"):
                return self._send(200, json.dumps(
                    {"ok": False, "error": "no command-capable provider"}),
                    "application/json")
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))) or b"{}")
                out = prov.command(str(body.get("car", "")).strip(),
                                   str(body.get("action", "")).strip())
                return self._send(200, json.dumps(out), "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/cloudcar/login":
            # Two-step credential-safe login: {"email": ...} makes the vendor
            # send a one-time code to the OWNER's email; {"code": ...}
            # exchanges it for tokens the provider stores itself (0600).
            # No password field exists in this flow by design.
            from carwatch import cloudcar as _cc
            prov = _cc.get()
            if prov is None:
                return self._send(200, json.dumps(
                    {"ok": False, "error": "no cloud provider plugin registered yet"}),
                    "application/json")
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))) or b"{}")
                email = str(body.get("email", "")).strip()
                code = str(body.get("code", "")).strip()
                if email:
                    return self._send(200, json.dumps(prov.begin_login(email)),
                                      "application/json")
                if code:
                    return self._send(200, json.dumps(prov.complete_login(code)),
                                      "application/json")
                return self._send(400, json.dumps(
                    {"ok": False, "error": "send email or code"}),
                    "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/car-pair":
            # One-time: pair the Pi to the CAR's Bluetooth audio (A2DP sink) so
            # the Pi can speak through the car speakers. petrus puts MBUX in
            # pairing mode, then taps this. sudo because the script restarts
            # bluealsa; the pair itself is read-only w.r.t. the car (BT bond
            # only, no CAN/diagnostic traffic). Ends by speaking a test line.
            import subprocess as _sp, os as _os
            try:
                r = _sp.run(
                    ["sudo", "bash",
                     _os.path.expanduser("~/CarWatch/scripts/car-speak.sh"), "pair"],
                    capture_output=True, text=True, timeout=60,
                    cwd=_os.path.expanduser("~/CarWatch"),
                    env={**_os.environ, "HOME": _os.path.expanduser("~")})
                out = (r.stdout + r.stderr).strip()[-4000:]
                return self._send(200, json.dumps({"ok": r.returncode == 0, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/car-speak":
            # Speak arbitrary text through the car's paired A2DP sink. No sudo:
            # keep bluealsa-aplay in the user session. Text is passed as an argv
            # element (list form), never a shell string, so no injection.
            import subprocess as _sp, os as _os
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))) or b"{}")
                text = str(body.get("text", "")).strip()[:300]
                if not text:
                    return self._send(400, json.dumps(
                        {"ok": False, "error": "no text"}), "application/json")
                r = _sp.run(
                    ["bash", _os.path.expanduser("~/CarWatch/scripts/car-speak.sh"),
                     "say", text],
                    capture_output=True, text=True, timeout=30,
                    cwd=_os.path.expanduser("~/CarWatch"),
                    env={**_os.environ, "HOME": _os.path.expanduser("~")})
                out = (r.stdout + r.stderr).strip()[-2000:]
                ok = r.returncode == 0 and "failed" not in out.lower() \
                    and "missing" not in out.lower()
                return self._send(200, json.dumps({"ok": ok, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/obd/deep":
            # The PER-ECU read, distinct from /api/obd/scan which only lists
            # advertised PIDs. petrus asked four times for this and kept being
            # handed the capability list instead; that conflation is the bug
            # this endpoint closes. Read-only: passive ATMA monitoring plus
            # mode-22 READ requests on Mercedes-range addresses.
            import subprocess as _sp, os as _os
            _elm = next((p for p in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0")
                         if _os.path.exists(p)), None)
            if not _elm:
                return self._send(200, json.dumps(
                    {"ok": False, "error": "no ELM327 adapter present"}),
                    "application/json")
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
                secs = "12"
                if body:
                    try:
                        secs = str(float(json.loads(body).get("seconds", 12)))
                    except Exception:
                        pass
                r = _sp.run(
                    ["sudo", "python3", "-m", "carwatch.elm327", "deep", _elm, secs],
                    capture_output=True, text=True, timeout=180,
                    cwd=_os.path.expanduser("~/CarWatch"),
                    env={**_os.environ, "CARWATCH_STATE": _os.path.expanduser("~/.carwatch")})
                out = (r.stdout + r.stderr).strip()[-8000:]
                return self._send(200, json.dumps({"ok": True, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/obd/record-arm":
            # Arm the one-shot in-drive capture: obdwatch runs it on the next
            # read where the car is moving (dongle sleeps with the ignition,
            # so recording from outside while parked just gets errno 5).
            import os as _os
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
                secs = 120
                if body:
                    try:
                        secs = min(300, int(json.loads(body).get("seconds", 120)))
                    except Exception:
                        pass
                p = _os.path.expanduser("~/.carwatch/record-armed")
                _os.makedirs(_os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(str(secs))
                return self._send(200, json.dumps(
                    {"ok": True, "armed_seconds": secs,
                     "fires": "on the next moving read"}), "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/obd/record":
            # Raw ATMA capture for the broadcast-stream decode (petrus,
            # Aug 24: "selvittaa broadcast-virta"). Same read-only pause/
            # restore path as deep, just longer and saved to disk.
            import subprocess as _sp, os as _os
            _elm = next((p for p in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0")
                         if _os.path.exists(p)), None)
            if not _elm:
                return self._send(200, json.dumps(
                    {"ok": False, "error": "no ELM327 adapter present"}),
                    "application/json")
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
                secs = "90"
                if body:
                    try:
                        secs = str(min(300.0, float(json.loads(body).get("seconds", 90))))
                    except Exception:
                        pass
                r = _sp.run(
                    ["sudo", "python3", "-m", "carwatch.elm327", "record", _elm, secs],
                    capture_output=True, text=True, timeout=int(float(secs)) + 120,
                    cwd=_os.path.expanduser("~/CarWatch"),
                    env={**_os.environ, "CARWATCH_STATE": _os.path.expanduser("~/.carwatch")})
                out = (r.stdout + r.stderr).strip()[-4000:]
                return self._send(200, json.dumps({"ok": True, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/obd/scan":
            # READ-ONLY capability probe: which PIDs the car advertises, VIN,
            # stored DTCs. Never writes. Produces the "what our module can get
            # from this car" list (petrus, Aug 19). ELM327 only - DoIP has no
            # scan path.
            import subprocess as _sp, os as _os
            _elm = next((p for p in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/rfcomm0")
                         if _os.path.exists(p)), None)
            if not _elm:
                return self._send(200, json.dumps(
                    {"ok": False, "error": "no ELM327 adapter present"}),
                    "application/json")
            try:
                r = _sp.run(
                    ["sudo", "python3", "-m", "carwatch.elm327", "scan", _elm],
                    capture_output=True, text=True, timeout=90,
                    cwd=_os.path.expanduser("~/CarWatch"),
                    env={**_os.environ, "CARWATCH_STATE": _os.path.expanduser("~/.carwatch")})
                out = (r.stdout + r.stderr).strip()[-6000:]
                return self._send(200, json.dumps({"ok": True, "output": out}),
                                  "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                                  "application/json")
        if path == "/api/listen":
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
        if path == "/api/wifi":
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
                            "ap": "vadelma-ap"}
                if target == "up":
                    ssid = str(body.get("ssid", "")).strip()
                    if not ssid or ssid.lower() in FAKE_SSIDS:
                        return self._send(400, json.dumps(
                            {"ok": False, "error": "pick a real saved SSID, not a placeholder"}),
                            "application/json")
                    if ssid not in _saved_connection_names():
                        return self._send(400, json.dumps(
                            {"ok": False, "error": "unknown saved network"}),
                            "application/json")
                    _switch_later(ssid)
                    return self._send(200, json.dumps(
                        {"ok": True, "note": f"switching to {ssid}; this page "
                         "will drop and come back on the new network"}),
                        "application/json")
                if target == "add":
                    ssid = str(body.get("ssid", "")).strip()
                    psk = str(body.get("password", "")).strip()
                    if not ssid or ssid.lower() in FAKE_SSIDS:
                        return self._send(400, json.dumps(
                            {"ok": False, "error": "that is not a real SSID - scan and tap one"}),
                            "application/json")
                    if len(psk) < 8:
                        return self._send(400, json.dumps(
                            {"ok": False, "error": "need ssid and password (8+ chars)"}),
                            "application/json")
                    # Capture nmcli's REAL outcome via a helper that writes
                    # /tmp/wifi-add-result.json - the old fire-and-forget left
                    # petrus typing blind with no ok/failed (Helsinki, Aug 15).
                    # On success the helper sets autoconnect-priority 100 so
                    # home wifi beats the hotspot (the Berlin priority trap).
                    _sp.Popen(["python3", "-m", "carwatch.wifi_join", ssid, psk],
                              start_new_session=True, cwd=REPO)
                    return self._send(200, json.dumps(
                        {"ok": True, "note": f"joining '{ssid}' - watch the result below"}),
                        "application/json")
                profile = profiles.get(target)
                if not profile:
                    return self._send(400, json.dumps(
                        {"ok": False, "error": f"unknown target {target}"}),
                        "application/json")
                _switch_later(profile)
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
    # Register cloud providers (import side-effect). Guarded: a broken or
    # absent provider must never take down the car's own dashboard.
    try:
        from carwatch import mercedesme  # noqa: F401
    except Exception as e:
        print(f"cloudcar provider not loaded: {e}")
    print(f"CarWatch chat on http://0.0.0.0:{args.port}  (model: {MODEL_URL})")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
