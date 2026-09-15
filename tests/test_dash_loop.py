"""The dash page's poll scheduler (cwLoop) must keep scheduling when a poll
never settles. Post-merge review of #57 (codexmb): the first version awaited
fn() with no bound, so one stalled voice request stopped that loop for good,
which setInterval never did. The scheduler is extracted from webchat.py and
run under node with fake timers; skipped when node is not installed."""
import pathlib, re, shutil, subprocess, pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "carwatch" / "webchat.py"


def _scheduler_js():
    text = SRC.read_text()
    m = re.search(r"const CW_FAST=.*?function cwLoop.*?run\(\);\}\n", text, re.S)
    assert m, "cwLoop block not found in webchat.py"
    return m.group(0)


HARNESS = r"""
const document={hidden:false,addEventListener(){}};
const AbortController=class{constructor(){this.signal={aborted:false,_l:[],addEventListener(n,f){this._l.push(f)}}}abort(){this.signal.aborted=true;this.signal._l.forEach(f=>f())}};
let now=0; const timers=[];
const setTimeout=(f,ms)=>{timers.push({at:now+ms,f});return timers.length};
const clearTimeout=(id)=>{if(id)timers[id-1]=null};
// Promise continuations (the race settling, finally, the reschedule) are
// microtasks: drain them after every fired timer or the harness never sees
// the next setTimeout(run, ...) that the scheduler registers.
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
async function advance(ms){const target=now+ms;for(;;){const due=timers.filter(t=>t&&t.at<=target).sort((a,b)=>a.at-b.at)[0];if(!due)break;now=due.at;timers[timers.indexOf(due)]=null;due.f();await flush();}now=target;await flush();}
const Date={now:()=>now};
%s
(async()=>{
let calls=0, settled=0; const order=[];
// never settles on its own, but honours the abort signal like a real fetch
const stalled=(signal)=>{calls++;order.push('call'+calls);return new Promise((res,rej)=>{signal.addEventListener('abort',()=>{settled++;order.push('settle'+calls);rej(new Error('aborted'))})})};
cwLoop('voice',stalled);
await advance(0);                    // first call
const c1=calls;
await advance(CW_DEADLINE-1);        // before the deadline: no second call (non-overlap)
const c2=calls;
await advance(CW_FAST.voice+2);      // deadline passed: first run aborted + settled, next interval elapsed
const c3=calls;
console.log(JSON.stringify({c1,c2,c3,settled,order,deadline:CW_DEADLINE}));
})();
"""

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_stalled_poll_does_not_stop_the_loop(tmp_path):
    js = tmp_path / "loop.js"
    js.write_text(HARNESS % _scheduler_js())
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    r = __import__("json").loads(out.stdout.strip().splitlines()[-1])
    assert r["c1"] == 1, r
    assert r["c2"] == 1, "a second poll started while the first was still pending"
    assert r["c3"] == 2, "the loop stopped after a poll that never settled"
    assert r["settled"] == 1, "the deadline did not abort the stalled poll"
    assert r["order"] == ["call1", "settle1", "call2"], f"second run started before the first settled: {r['order']}"
