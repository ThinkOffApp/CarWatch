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
<title>cloud car</title></head>
<body style="background:#111;color:#ddd;font:14px monospace;margin:0;padding:14px">
<h2 style="margin:0 0 4px;color:#6cf">Cloud car data</h2>
<div style="color:#888;margin-bottom:10px">Vendor-cloud readings (doors, tires, charge) for the dashboards.
Login asks ONLY your account email + the one-time code the vendor mails you. Never a password.</div>
<div id=authbox style="margin:10px 0"></div>
<pre id=status style="white-space:pre-wrap;color:#9e9"></pre>
<div style="color:#555;margin-top:10px"><a href="/dash" style="color:#6cf">dash</a> &middot; <a href="/nerd" style="color:#6cf">nerd</a> &middot; <a href="/" style="color:#6cf">chat</a></div>
<script>
const ab=document.getElementById('authbox'),st=document.getElementById('status');
async function post(b){const r=await fetch('/api/cloudcar/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}
function form(ph,key,btn){ab.innerHTML=`<input id=v placeholder="${ph}" style="background:#222;color:#ddd;border:1px solid #555;padding:8px;font:14px monospace;width:55%">
<button onclick="send('${key}')" style="background:#333;color:#6f6;border:1px solid #555;padding:8px 12px;font:14px monospace">${btn}</button> <span id=msg style="color:#fc6"></span>`}
async function send(key){const v=document.getElementById('v').value.trim();if(!v)return;
document.getElementById('msg').textContent='...';const d=await post({[key]:v});
document.getElementById('msg').textContent=d.ok?'ok':(d.error||'failed');if(d.ok)setTimeout(tick,800)}
async function tick(){
 try{
  const a=await (await fetch('/api/cloudcar/auth')).json();
  if(a.step==='need_email')form('your vendor-account email','email','send code');
  else if(a.step==='need_code')form('code from your email','code','confirm');
  else if(a.step==='no_provider')ab.innerHTML='<span style="color:#fc6">provider plugin not installed yet</span>';
  else ab.innerHTML='<span style="color:#6f6">authenticated</span>';
  const s=await (await fetch('/api/cloudcar')).json();
  st.textContent=JSON.stringify(s,null,1);
 }catch(e){st.textContent='unreachable: '+e}
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
UNIFIED_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>CarWatch</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#142a35 0,#091016 48%);color:#e8f1f5;
  font:15px/1.5 -apple-system,system-ui,sans-serif;padding:0 12px 40px}
.wrap{max-width:980px;margin:0 auto}
header{padding:18px 0 10px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
h1{font-size:22px}
#status{font:12px ui-monospace,monospace;color:#8ca1ad;text-align:right}
#status b{color:#40d98b}
.controls{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:9px;margin:8px 0 18px}
.ctl{background:linear-gradient(145deg,#13232c,#0e171e);border:1px solid #20313c;border-radius:13px;
  padding:12px 10px;text-align:center;cursor:pointer;user-select:none;transition:transform .06s,border-color .15s}
.ctl:active{transform:scale(.96)}.ctl:hover{border-color:#62c7ff}
.ctl .ico{font-size:24px;display:block;margin-bottom:4px}
.ctl .t{font-weight:700;font-size:13.5px}
.ctl .d{font-size:11px;color:#8ca1ad;margin-top:2px}
.ctl.on{border-color:#40d98b}.ctl.on .t{color:#40d98b}
.ctl.busy{border-color:#ffc857;opacity:.7;pointer-events:none}
#groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:9px}
section{background:linear-gradient(145deg,#13232c,#0e171e);border:1px solid #20313c;border-radius:13px;padding:10px 13px}
section h2{font-size:13.5px;color:#62c7ff;margin-bottom:4px}
.r{display:flex;justify-content:space-between;gap:8px;padding:2px 0;font-size:13.5px}
.r .l{color:#c4d2d8}.r .v{font:700 15px ui-monospace,monospace;color:#40d98b;text-align:right}
.r .v.warn{color:#ffc857}.r .v.bad{color:#ff667d}
#age{color:#8ca1ad;font-size:12px;margin:10px 0;font-family:ui-monospace,monospace}
#out{background:#0e171e;border:1px solid #20313c;border-radius:10px;padding:10px 12px;
  font:12px ui-monospace,monospace;color:#9fe8bd;white-space:pre-wrap;display:none;margin-bottom:14px;max-height:200px;overflow:auto}
.ask{display:flex;gap:8px;margin-top:14px}
.ask input{flex:1;padding:11px;border-radius:10px;border:1px solid #2a3039;background:#12151a;color:inherit;font-size:15px}
.ask button{padding:11px 16px;border:0;border-radius:10px;background:#62c7ff;color:#031019;font-weight:700}
#answer{background:#181c22;border:1px solid #262b33;border-radius:12px;padding:10px 12px;margin-top:10px;display:none;font-size:14px}
.links{margin-top:16px;font-size:13px;color:#8ca1ad}
.links a{color:#62c7ff;text-decoration:none;margin-right:14px}
</style></head><body><div class=wrap>
<header><h1>&#128663; CarWatch</h1><div id=status>connecting&hellip;</div></header>
<div class=controls>
 <div class=ctl onclick="act(this,'/api/obd','one live engine read')"><span class=ico>&#128202;</span><span class=t>Read now</span><div class=d>one live sweep</div></div>
 <div class=ctl onclick="act(this,'/api/obd/deep','per-ECU deep scan (~1 min, parked)')"><span class=ico>&#128300;</span><span class=t>Deep scan</span><div class=d>per-ECU identity</div></div>
 <div class=ctl onclick="act(this,'/api/obd/record-arm','armed: records 120s raw CAN on the next moving read')"><span class=ico>&#127908;</span><span class=t>Record CAN</span><div class=d>arms next drive</div></div>
 <div class=ctl id=listenCtl onclick="toggleListen()"><span class=ico>&#128066;</span><span class=t id=listenT>Listening&hellip;</span><div class=d>whisper ears</div></div>
 <div class=ctl onclick="speak()"><span class=ico>&#128266;</span><span class=t>Car audio</span><div class=d>speak via MBUX</div></div>
 <div class=ctl onclick="act(this,'/api/update','pull latest code + restart services')"><span class=ico>&#11014;&#65039;</span><span class=t>Update</span><div class=d>self-update</div></div>
 <div class=ctl onclick="act(this,'/api/car-pair','scan + pair car Bluetooth (MBUX in pairing mode)')"><span class=ico>&#128279;</span><span class=t>Pair car</span><div class=d>MBUX audio</div></div>
 <div class=ctl onclick="location.href='/journal'"><span class=ico>&#128220;</span><span class=t>Journal</span><div class=d>service log</div></div>
</div>
<div id=out></div>
<div id=age></div>
<div id=groups></div>
<div class=ask><input id=q placeholder="Ask your car something"><button onclick="ask()">Ask</button></div>
<div id=answer></div>
<div class=links><a href="/">&#128172; chat</a><a href="/nerd">&#128300; nerd detail</a><a href="/cloudcar">&#9729;&#65039; cloud car</a></div>
</div><script>
const $=id=>document.getElementById(id);
// Through the tunnel the page URL carries ?t=<token>; same-origin fetches
// must forward it or they 401 and the dashboard hangs on "connecting"
// (petrus, in-car). At home (no tunnel) there is no token and none is needed.
const _tok=new URLSearchParams(location.search).get('t')||'';
const _q=u=>_tok?(u+(u.includes('?')?'&':'?')+'t='+encodeURIComponent(_tok)):u;
const F=(u,o)=>fetch(_q(u),o);
function show(t){const o=$('out');o.style.display='block';o.textContent=t;o.scrollTop=o.scrollHeight}
async function act(el,url,label){
  el.classList.add('busy');show(label+' ...');
  try{const r=await F(url,{method:'POST'});const d=await r.json();
    show(label+'\n'+(d.output||d.error||JSON.stringify(d)).slice(0,1800));
  }catch(e){show(label+' failed: '+e)}
  el.classList.remove('busy')}
async function toggleListen(){
  const on=!$('listenCtl').classList.contains('on');
  try{const r=await F('/api/listen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on})});
    const d=await r.json();setListen(d.listening)}catch(e){show('listen toggle failed: '+e)}}
function setListen(on){$('listenCtl').classList.toggle('on',!!on);$('listenT').textContent=on?'Listening ON':'Listening off'}
function speak(){const t=prompt('Text for the car to speak:');if(!t)return;
  F('/api/car-speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})})
    .then(r=>r.json()).then(d=>show(d.ok?'spoken':'speak: '+(d.error||'failed')))}
async function ask(){const t=$('q').value.trim();if(!t)return;
  const a=$('answer');a.style.display='block';a.textContent='thinking\u2026 (~1 min at 3.5 tok/s)';$('q').value='';
  try{const r=await F('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:t,manual:true})});
    const d=await r.json();a.textContent=d.answer||'(no answer)'}catch(e){a.textContent='could not reach the car brain'}}
function sev(u,k,n){if(!isFinite(n))return'';if(u==='\u00b0C')return n>=110?'bad':n>=95?'warn':'';
  if(k.includes('voltage'))return n<11.8||n>15?'bad':n<12.2?'warn':'';
  if(k.includes('battery')||k.includes('fuel_level'))return n<10?'bad':n<20?'warn':'';return''}
async function poll(){
  try{const r=await F('/api/status');const s=await r.json();
    if(s.error==='token required'){$('status').innerHTML='<span style=color:#ffc857>Open this dashboard from the app link (it needs the access token) or from the home network</span>';return}
    const f=s.facts||{};
    $('status').innerHTML=(f.network||'')+' &middot; '+(f['your temperature']||'')+' &middot; <b>'+(f.uptime||'')+'</b>';
    if(s.listening!==undefined)setListen(s.listening);
  }catch(e){$('status').innerHTML='<span style=color:#ff667d>Cannot reach the car - open this page from the app link so it carries the access token, or use the home network URL</span>'}
  try{const d=await(await F('/api/obd/all')).json();
    const g=$('groups');
    if(d&&d.groups&&Object.keys(d.groups).length){
      $('age').textContent=d.age_s!==undefined?('engine data from '+Math.round(d.age_s)+'s ago'+(d.age_s>180?' - STALE (ignition off?)':'')):'';
      g.innerHTML=Object.entries(d.groups).map(([name,vals])=>
        '<section><h2>'+name+'</h2>'+Object.values(vals).map(r=>
          '<div class=r><span class=l>'+(r.label||r.key)+'</span><span class="v '+sev(r.unit||'',r.key||'',Number(r.value))+'">'+r.value+' '+(r.unit||'')+'</span></div>').join('')+'</section>').join('');
    }else{$('age').textContent='no engine data cached - plug the adapter and turn the ignition on';}
  }catch(e){}
}
poll();setInterval(poll,2000);
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


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
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

    def _authorised(self) -> bool:
        if not self._came_through_tunnel():
            return True                      # same network as the car
        want = self._token()
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer ") and auth[7:].strip() == want:
            return True
        # ?t=... so a plain link works in a browser without any typing.
        from urllib.parse import urlparse, parse_qs
        got = parse_qs(urlparse(self.path).query).get("t", [""])[0]
        import hmac
        return hmac.compare_digest(got, want)

    def _deny(self):
        self._send(401, json.dumps({"ok": False, "error": "token required"}),
                   "application/json")

    def do_GET(self):
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
            self._send(200, PAGE)
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
        if self.path == "/api/bt-pair":
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
        if self.path == "/api/cloudcar/login":
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
        if self.path == "/api/car-pair":
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
        if self.path == "/api/car-speak":
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
        if self.path == "/api/obd/deep":
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
        if self.path == "/api/obd/record-arm":
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
        if self.path == "/api/obd/record":
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
        if self.path == "/api/obd/scan":
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
    print(f"CarWatch chat on http://0.0.0.0:{args.port}  (model: {MODEL_URL})")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
