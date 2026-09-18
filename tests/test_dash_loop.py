"""The dash page's poll scheduler (cwLoop) must keep scheduling when a poll
never settles. Post-merge review of #57 (codexmb): the first version awaited
fn() with no bound, so one stalled voice request stopped that loop for good,
which setInterval never did. The scheduler is extracted from webchat.py and
run under node with fake timers; skipped when node is not installed."""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "carwatch" / "webchat.py"


def _fetch_helper_js():
    text = SRC.read_text()
    m = re.search(r"const F=\(u,o=\{\},ms=4000\)=>.*?\};\n", text, re.S)
    assert m, "F helper not found in webchat.py"
    return m.group(0)


F_HARNESS = r"""
%s
const _q=(u)=>u;
const seen=[];
const fetch=(u,o)=>{seen.push(o.signal);return new Promise(()=>{});};  // never answers
(async()=>{
  const AbortControllerReal=AbortController;
  // 1. no outer signal: F's own timeout must abort the signal fetch received
  F('/a',{},20); await new Promise(r=>setTimeout(r,60));
  const ownTimeoutAborts=seen[0]&&seen[0].aborted===true;
  // 2. caller passes {signal:undefined} (pollVoice called by hand): same
  F('/b',{signal:undefined},20); await new Promise(r=>setTimeout(r,60));
  const undefinedOuterOk=seen[1]&&seen[1].aborted===true;
  // 3. outer signal aborted by the scheduler must abort the request before F's timeout
  const outer=new AbortControllerReal(); F('/c',{signal:outer.signal},10000); outer.abort(); await new Promise(r=>setTimeout(r,10));
  const outerAborts=seen[2]&&seen[2].aborted===true;
  console.log(JSON.stringify({ownTimeoutAborts,undefinedOuterOk,outerAborts,distinct:seen[2]!==outer.signal}));
})();
"""


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

def _run_node(harness: str, body: str) -> dict:
    """Write the harness to a temp file, run it under node, return its JSON."""
    with tempfile.TemporaryDirectory() as td:
        js = pathlib.Path(td) / "t.js"
        js.write_text(harness % body)
        out = subprocess.run(["node", str(js)], capture_output=True,
                             text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DashLoop(unittest.TestCase):
    """These ran under pytest only, and pytest is not installed in CI, so the
    import error was the ONLY thing CI ever reported from this file. As
    unittest they actually execute."""

    def test_stalled_poll_does_not_stop_the_loop(self):
        r = _run_node(HARNESS, _scheduler_js())
        self.assertEqual(r["c1"], 1, r)
        self.assertEqual(r["c2"], 1,
                         "a second poll started while the first was still pending")
        self.assertEqual(r["c3"], 2,
                         "the loop stopped after a poll that never settled")
        self.assertEqual(r["settled"], 1,
                         "the deadline did not abort the stalled poll")
        self.assertEqual(r["order"], ["call1", "settle1", "call2"],
                         f"second run started before the first settled: {r['order']}")

    def test_fetch_helper_abort_reaches_the_request(self):
        """codexmb, #61: Object.assign({signal}, o) let the caller's options
        overwrite the helper's own signal, so its timeout never aborted."""
        r = _run_node(F_HARNESS, _fetch_helper_js())
        self.assertTrue(r["ownTimeoutAborts"],
                        "F's own timeout did not abort the request (no outer signal)")
        self.assertTrue(r["undefinedOuterOk"],
                        "a caller passing {signal: undefined} disabled F's timeout")
        self.assertTrue(r["outerAborts"],
                        "an aborted outer signal did not abort the request")
        self.assertTrue(r["distinct"],
                        "F passed the outer signal itself instead of its linked one")


if __name__ == "__main__":
    unittest.main()
