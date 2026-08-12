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
<div id=sub style="color:#888"></div>
<div style="color:#6cf;margin-top:8px">TOP</div>
<pre id=top style="margin:2px 0;white-space:pre-wrap"></pre>
<div style="color:#6cf">JOURNAL</div>
<pre id=jrnl style="margin:2px 0;white-space:pre-wrap;color:#9e9"></pre>
<div style="color:#6cf">SSH (Termux)</div>
<pre style="margin:2px 0;color:#fc6">ssh petrus@__IP__</pre>
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
    document.getElementById("top").textContent = (d.top||[]).join("\n");
    document.getElementById("jrnl").textContent = (d.journal||[]).join("\n");
  }catch(e){
    document.getElementById("sub").textContent = "unreachable: " + e;
  }
}
tick(); setInterval(tick, 2000);
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
            payload = {
                "device": "vadelma",
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
