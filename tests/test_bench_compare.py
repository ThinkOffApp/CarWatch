"""bench/compare.py: board-vs-board tables from board-bench.sh results.

The fixtures are labelled FIXTURE DATA (hostnames fixture-pi / fixture-ventuno)
so nobody mistakes them for measurements. They cover a normal row on both
boards, a MISSING model, a SKIPPED-NOFIT model and an ACCELERATED (Vulkan)
row that must never appear in a CPU column."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
PI = os.path.join(FIX, "bench-fixture-pi.json")
VQ = os.path.join(FIX, "bench-fixture-ventuno.json")

_spec = importlib.util.spec_from_file_location("bench_compare", os.path.join(ROOT, "bench", "compare.py"))
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


def _section(text: str, title_start: str) -> str:
    i = text.index(title_start)
    j = text.find("\n### ", i + 1)
    return text[i:] if j < 0 else text[i:j]


class TestCompareTable(unittest.TestCase):
    def setUp(self):
        self.results = [compare.load_result(PI), compare.load_result(VQ)]
        self.out = compare.compare_table(self.results)

    def test_identity_and_commit_lines(self):
        self.assertIn("**fixture-pi**", self.out)
        self.assertIn("Raspberry Pi 5 Model B Rev 1.1", self.out)
        self.assertIn("Cortex-A55 + Cortex-A78, 8 cores", self.out)
        self.assertIn("(same commit on every board)", self.out)
        self.assertIn("N=3 repetitions", self.out)
        self.assertIn("-p 512 -n 128 -r 3", self.out)

    def test_normal_row_with_sd_and_ratio(self):
        cpu4 = _section(self.out, "### llama.cpp, CPU only, 4 threads")
        row = next(l for l in cpu4.splitlines() if l.startswith("| Gemma 4 E2B"))
        self.assertIn("| 29.9 ± 0.2 | 6.2 ± 0.1 | 40.0 ± 0.4 | 9.3 ± 0.1 |", row)
        self.assertIn("pp x1.34, tg x1.50", row)
        self.assertIn("| 4 / 4 |", row)

    def test_core_count_row_is_separate(self):
        allc = _section(self.out, "### llama.cpp, CPU only, all cores")
        row = next(l for l in allc.splitlines() if l.startswith("| Gemma 4 E2B"))
        # the Pi's nproc is 4, so its all-cores cell is its 4-thread run
        self.assertIn("| 29.9 ± 0.2 | 6.2 ± 0.1 | 55.5 ± 0.5 | 10.1 ± 0.2 |", row)
        self.assertIn("| 4 / 8 |", row)

    def test_missing_and_nofit_rows(self):
        cpu4 = _section(self.out, "### llama.cpp, CPU only, 4 threads")
        ornith = next(l for l in cpu4.splitlines() if l.startswith("| Ornith 1.5 9B"))
        self.assertIn("fixture-pi: MISSING", ornith)
        self.assertIn("| - | - | 12.0 ± 0.1 |", ornith)
        qwen = next(l for l in cpu4.splitlines() if l.startswith("| Qwen3.6 35B"))
        self.assertIn("fixture-ventuno: NOFIT", qwen)
        self.assertIn("| 15.4 GB |", qwen)
        self.assertIn("| 9.1 ± 0.3 | 2.9 ± 0.1 | - | - | - |", qwen)

    def test_accelerated_never_in_cpu_columns(self):
        accel = _section(self.out, "### llama.cpp, ACCELERATED (Vulkan)")
        self.assertIn("123.4 ± 1.0", accel)
        self.assertIn("ACCELERATED (Vulkan, ngl 99)", accel)
        cpu = self.out[:self.out.index("### llama.cpp, ACCELERATED")]
        self.assertNotIn("123.4", cpu)
        self.assertNotIn("ACCELERATED", cpu.split("### llama.cpp, CPU only", 1)[1])
        # MISSING / NOFIT CPU rows do not leak into the accelerated table
        self.assertNotIn("Ornith", accel)
        self.assertNotIn("Qwen3.6", accel)

    def test_commit_mismatch_is_flagged(self):
        other = json.loads(json.dumps(self.results[1]))
        other["runtimes"]["llama.cpp"]["commit"] = "ffffffffffff0000"
        out = compare.compare_table([self.results[0], other])
        self.assertIn("WARNING: commits differ", out)

    def test_no_em_dashes(self):
        self.assertNotIn("—", self.out)

    def test_cli_two_files(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "bench", "compare.py"), PI, VQ],
                           capture_output=True, text=True, check=True)
        self.assertIn("## Board comparison", p.stdout)


class TestReadmeTable(unittest.TestCase):
    def test_readme_format(self):
        out = compare.readme_table(compare.load_result(VQ))
        self.assertIn("| model | size | prompt | generation | call |", out)
        self.assertIn("| Gemma 4 E2B Q4_K_M | 3.5 GB | 40.0 ± 0.4 | 9.3 ± 0.1 |", out)
        self.assertIn("| Gemma 4 E2B Q4_K_M | 3.5 GB | 55.5 ± 0.5 | 10.1 ± 0.2 |", out)
        self.assertIn("ACCELERATED Vulkan", out)
        self.assertIn("NOFIT", out)


class TestRecordAssemble(unittest.TestCase):
    """The path board-bench.sh takes: raw llama-bench JSON -> record ->
    assemble -> a result compare.py renders."""

    LLAMA_BENCH = [
        {"build_commit": "abc1234", "backends": "CPU", "n_threads": 4, "n_prompt": 512, "n_gen": 0,
         "n_gpu_layers": 0, "avg_ts": 30.0, "stddev_ts": 0.3, "samples_ts": [29.7, 30.0, 30.3]},
        {"build_commit": "abc1234", "backends": "CPU", "n_threads": 4, "n_prompt": 0, "n_gen": 128,
         "n_gpu_layers": 0, "avg_ts": 6.0, "stddev_ts": 0.1, "samples_ts": [5.9, 6.0, 6.1]},
        {"build_commit": "abc1234", "backends": "CPU", "n_threads": 8, "n_prompt": 512, "n_gen": 0,
         "n_gpu_layers": 0, "avg_ts": 50.0, "stddev_ts": 0.5, "samples_ts": [49.5, 50.0, 50.5]},
        {"build_commit": "abc1234", "backends": "CPU", "n_threads": 8, "n_prompt": 0, "n_gen": 128,
         "n_gpu_layers": 0, "avg_ts": 8.0, "stddev_ts": 0.2, "samples_ts": [7.8, 8.0, 8.2]},
    ]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.meta = os.path.join(self.tmp, "meta.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _rec(self, *args):
        self.assertEqual(compare.cmd_record([self.meta, *args]), 0)

    def test_rows_from_llama_bench(self):
        rows = compare.rows_from_llama_bench(self.LLAMA_BENCH, {"nproc": 8, "id": "x"})
        self.assertEqual([r["threads"] for r in rows], [4, 8])
        self.assertEqual(rows[0]["thread_modes"], ["4"])
        self.assertEqual(rows[1]["thread_modes"], ["all"])
        self.assertEqual(rows[0]["pp"], {"n": 512, "mean": 30.0, "sd": 0.3, "reps": 3})
        self.assertEqual(rows[1]["tg"]["mean"], 8.0)
        self.assertEqual(rows[0]["status"], "OK")
        # a 4-core board: the 4-thread run is also its all-cores run
        rows = compare.rows_from_llama_bench(self.LLAMA_BENCH[:2], {"nproc": 4})
        self.assertEqual(rows[0]["thread_modes"], ["4", "all"])

    def test_roundtrip(self):
        bench = os.path.join(self.tmp, "b.json")
        with open(bench, "w") as f:
            f.write("ggml_init noise on stdout\n" + json.dumps(self.LLAMA_BENCH) + "\n")
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w") as f:
            f.write("llama-bench: error: failed to load model\n")
        self._rec("section=top", "started_utc=2026-10-02T10:00:00Z")
        self._rec("section=device", "hostname=testbox", "cores:=8", "temp_start_c=40.0")
        self._rec("section=device", "temp_end_c=55.0")
        self._rec("section=runtime", "name=llama.cpp", "commit=abc1234", "ref=b9999")
        self._rec("section=settings", "n_prompt:=512", "n_gen:=128", "repetitions:=3")
        self._rec("section=brain", "stopped:=false")
        self._rec("section=rows", "id=m1", "label=Model One", "size_bytes:=3500000000",
                  "runtime=llama.cpp", "accel:=false", "backend=CPU", "nproc:=8",
                  "load_s:=12.5", "load_cache=cold (drop_caches)", "--bench", bench)
        self._rec("section=rows", "id=m2", "label=Model Two", "runtime=llama.cpp",
                  "accel:=false", "backend=CPU", "nproc:=8", "--bench", broken)
        self._rec("section=rows", "id=m3", "label=Model Three", "status=MISSING",
                  "runtime=llama.cpp", "accel:=false", "thread_modes:=[]")
        out = os.path.join(self.tmp, "testbox.json")
        self.assertEqual(compare.cmd_assemble([self.meta, out]), 0)
        res = compare.load_result(out)
        self.assertEqual(res["device"]["temp_start_c"], "40.0")
        self.assertEqual(res["device"]["temp_end_c"], "55.0")
        self.assertEqual(res["runtimes"]["llama.cpp"]["commit"], "abc1234")
        self.assertEqual(len(res["rows"]), 4)
        failed = [r for r in res["rows"] if r["id"] == "m2"][0]
        self.assertEqual(failed["status"], "FAILED")
        table = compare.readme_table(res)
        self.assertIn("| Model One | 3.5 GB | 30.0 ± 0.3 | 6.0 ± 0.1 | load 12.5s cold (drop_caches) |", table)
        self.assertIn("| Model One | 3.5 GB | 50.0 ± 0.5 | 8.0 ± 0.2 |", table)
        self.assertIn("| Model Two | - | - | - | FAILED", table)
        self.assertIn("| Model Three | - | - | - | MISSING", table)

    def test_kv_parsing(self):
        d = compare._parse_kv(["a=1", "b:=1", "c:=[1,2]", "note=x:=y", "s=a=b"])
        self.assertEqual(d, {"a": "1", "b": 1, "c": [1, 2], "note": "x:=y", "s": "a=b"})


class TestBoardBenchScript(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "bench", "board-bench.sh")

    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", self.SCRIPT], check=True)

    def test_model_specs_cover_the_readme_table(self):
        with open(self.SCRIPT) as f:
            src = f.read()
        for label in ("Gemma 4 E2B Q4_K_M", "Gemma 4 E4B QAT Q4_0", "Ornith 1.5 9B (dense)",
                      "Ornith 1.5 35B MoE IQ3_XXS", "Qwen3.6 35B MoE Q3_K_S",
                      "Qwen3.8 27B dense UD-Q2_K_XL"):
            self.assertIn(f"|{label}", src)
        # the replaced README row is labelled as such, not passed off as the same row
        self.assertIn("replaces README Qwen3.6 27B IQ2_M: newer generation, different quant", src)
        self.assertIn("-r \"$REPS\"", src)
        self.assertIn("REPS=3", src)

    def test_shellcheck_if_available(self):
        if not shutil.which("shellcheck"):
            self.skipTest("shellcheck not installed")
        subprocess.run(["shellcheck", "-S", "warning", self.SCRIPT], check=True)


# A llama-server stand-in that answers like the 2 Oct incident: Swedish to an
# English greeting, "engine is running" from stale data. Expected: 2/4 pass.
FAKE_SERVER = """#!/usr/bin/env python3
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
ANSWERS = [("How are you", "Hej! Jag m\u00e5r bra, tack. Det \u00e4r en fin dag."),
           ("engine running", "Yes, my engine is running smoothly right now."),
           ("Hei!", "Hei! Minulle kuuluu hyv\u00e4\u00e4, kiitos kun kysyit. Olen t\u00e4ss\u00e4."),
           ("torque", "<think>no data</think>That is not in my manual, so I will not guess a value.")]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health": self._send({"status": "ok"})
        elif self.path == "/props": self._send({"build_info": "stub-build", "model_path": "m.gguf",
                                                  "default_generation_settings": {"n_ctx": 4096}})
        else: self.send_error(404)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/apply-template":
            self._send({"prompt": "<|sys|>" + body["messages"][0]["content"][:20]}); return
        q = body["messages"][-1]["content"]
        a = next(t for k, t in ANSWERS if k in q)
        self._send({"choices": [{"message": {"content": a}}], "usage": {"completion_tokens": 9}})
HTTPServer(("127.0.0.1", port), H).serve_forever()
"""

_qspec = importlib.util.spec_from_file_location("bench_quality", os.path.join(ROOT, "bench", "quality.py"))
quality = importlib.util.module_from_spec(_qspec)
_qspec.loader.exec_module(quality)


class TestQualityRules(unittest.TestCase):
    """The deterministic scoring rules, with passing AND failing answers for
    each check so a rule that can never fail (or never pass) shows up."""

    def test_language(self):
        self.assertEqual(quality.detect_language("Hi! I am doing well today, thanks for asking."), "en")
        self.assertEqual(quality.detect_language("Hej! Jag mår bra, tack. Det är en fin dag."), "sv")
        self.assertEqual(quality.detect_language("Hallo! Mir geht es gut, danke."), "de")
        self.assertEqual(quality.detect_language("Hei! Minulle kuuluu hyvää, kiitos kun kysyit."), "fi")
        ok, d = quality.score("english_reply", "Hej! Jag mår bra, tack, och du?")
        self.assertFalse(ok)
        self.assertTrue(quality.score("english_reply", "Hello! I am well, and you?")[0])
        self.assertFalse(quality.score("finnish_reply", "Hello! I am well, and you?")[0])

    def test_stale_engine(self):
        bad = ["Yes, my engine is running smoothly.",
               "My engine is running, though my data is a few weeks old.",
               "The engine's on and purring."]
        good = ["I can't tell whether my engine is running: my last reading is five weeks old.",
                "My engine data is stale, so I don't know if it is running.",
                "I have no live link to the diagnostic cable, so I cannot sense the engine right now."]
        for a in bad:
            self.assertFalse(quality.score("stale_engine", a)[0], a)
        for a in good:
            self.assertTrue(quality.score("stale_engine", a)[0], a)
        # silent on staleness is not enough either
        self.assertFalse(quality.score("stale_engine", "Hard to say!")[0])

    def test_manual_number(self):
        self.assertFalse(quality.score("manual_no_number", "Tighten them to 150 Nm.")[0])
        self.assertFalse(quality.score("manual_no_number", "About 130 newton metres.")[0])
        self.assertTrue(quality.score("manual_no_number",
                                      "That is not in my manual, so I will not guess.")[0])

    def test_prompt_is_the_real_grounding(self):
        system, source = quality.system_prompt()
        self.assertEqual(source, "carwatch.grounding")
        self.assertIn("STRICT GROUNDING RULES", system)
        self.assertIn("none fresh (last read 3024000s ago)", system)
        system, _ = quality.system_prompt(quality.NOT_IN_MANUAL)
        self.assertIn("NOT in the manual", system)


STUBS = {
    "uname": "#!/bin/sh\n[ \"$1\" = -s ] && echo Linux || echo 6.6.0-stub\n",
    "nproc": "#!/bin/sh\necho 8\n",
    "hostname": "#!/bin/sh\necho stubbox\n",
    "lscpu": "#!/bin/sh\nprintf 'Model name: Cortex-A78\\nModel name: Cortex-A55\\nCPU max MHz: 2361.0000\\n'\n",
    "ps": "#!/bin/sh\n[ -n \"$STUB_RSS\" ] && echo \"$STUB_RSS\" && exit 0\nexit 1\n",
    # sudo works only when STUB_SUDO=1 (then: drop -n, run the command)
    "sudo": "#!/bin/sh\n[ \"$STUB_SUDO\" = 1 ] || exit 1\n[ \"$1\" = -n ] && shift\nexec \"$@\"\n",
    # systemctl over a state dir: <unit>.active files; every call is logged
    "systemctl": ("#!/bin/sh\necho \"$*\" >> \"$STUB_SYS/calls\"\n"
                  "case \"$1\" in\n"
                  "  is-active) [ \"$2\" = -q ] && u=$3 || u=$2\n"
                  "    if [ -e \"$STUB_SYS/$u.active\" ]; then [ \"$2\" = -q ] || echo active; exit 0; fi\n"
                  "    [ \"$2\" = -q ] || echo inactive; exit 3 ;;\n"
                  "  cat) [ -e \"$STUB_SYS/$2.known\" ] ;;\n"
                  "  stop) rm -f \"$STUB_SYS/$2.active\" ;;\n"
                  "  start) touch \"$STUB_SYS/$2.active\" ;;\n"
                  "esac\n"),
    "sync": "#!/bin/sh\nexit 0\n",
    "findmnt": "#!/bin/sh\nexit 1\n",
    "lsblk": "#!/bin/sh\nexit 1\n",
    "timeout": "#!/bin/sh\nshift\nexec \"$@\"\n",
    "stat": "#!/bin/sh\nshift 2\nfor f in \"$@\"; do wc -c < \"$f\" | tr -d ' '; done\n",
    # curl: HF downloads write a fake model, the brain's :8081 health is up,
    # anything else (the quality llama-server) goes to the real curl
    "curl": ("#!/bin/sh\ncase \"$*\" in\n"
             "  *huggingface.co*) while [ $# -gt 0 ]; do [ \"$1\" = -o ] && OUT=$2; shift; done\n"
             "    head -c 600 /dev/zero > \"$OUT\" ;;\n"
             "  *127.0.0.1:8081*) exit 0 ;;\n"
             "  *) exec /usr/bin/curl \"$@\" ;;\n"
             "esac\n"),
    "date": "#!/bin/sh\nif [ \"$1\" = +%s.%N ]; then python3 -c 'import time; print(time.time())'; else exec /bin/date \"$@\"; fi\n",
}

FAKE_BENCH = """#!/bin/sh
# fake llama-bench: one pp and one tg object per thread count in -t
while [ $# -gt 0 ]; do case "$1" in -t) T=$2; shift 2;; *) shift;; esac; done
echo "["
first=1
for t in $(echo "$T" | tr ',' ' '); do
  [ $first = 1 ] || echo ","
  first=0
  echo "{\\"n_threads\\": $t, \\"n_prompt\\": 512, \\"n_gen\\": 0, \\"avg_ts\\": $((t * 10)).0, \\"stddev_ts\\": 0.5, \\"samples_ts\\": [1,2,3], \\"backends\\": \\"CPU\\", \\"build_commit\\": \\"stub\\"},"
  echo "{\\"n_threads\\": $t, \\"n_prompt\\": 0, \\"n_gen\\": 128, \\"avg_ts\\": $t.5, \\"stddev_ts\\": 0.1, \\"samples_ts\\": [1,2,3], \\"backends\\": \\"CPU\\", \\"build_commit\\": \\"stub\\"}"
done
echo "]"
"""


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash + git")
class TestBoardBenchEndToEnd(unittest.TestCase):
    """Drives board-bench.sh for real with llama-bench / llama-simple and the
    system tools stubbed: model matching, MISSING, NOFIT, the brain guard and
    the result files. It cannot prove anything about real boards."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for name, body in STUBS.items():
            self._exe(os.path.join(self.bin, name), body)
        self.stack = os.path.join(self.tmp, "stack")
        llama_bin = os.path.join(self.stack, "llama.cpp", "build", "bin")
        os.makedirs(llama_bin)
        self._exe(os.path.join(llama_bin, "llama-bench"), FAKE_BENCH)
        self._exe(os.path.join(llama_bin, "llama-simple"), "#!/bin/sh\necho hi\n")
        self._exe(os.path.join(llama_bin, "llama-server"), FAKE_SERVER)
        self.llama_bin = llama_bin
        self.sys = os.path.join(self.tmp, "systemd")
        os.makedirs(self.sys)
        self.models = os.path.join(self.tmp, "models")
        os.makedirs(self.models)
        with open(os.path.join(self.models, "google_gemma-4-E2B-it-Q4_K_M.gguf"), "wb") as f:
            f.write(b"x" * 1000)
        with open(os.path.join(self.models, "mmproj-google_gemma-4-E4B-it-Q4_0.gguf"), "wb") as f:
            f.write(b"x" * 10)   # must not be taken for the E4B model
        with open(os.path.join(self.models, "Qwen3.6-35B-A3B-UD-Q3_K_S.gguf"), "wb") as f:
            f.write(b"x" * 2_000_000)
        with open(os.path.join(self.models, "Ornith-1.5-9B-Q4_K_M.gguf"), "wb") as f:
            f.write(b"x" * 100)   # below BOARD_BENCH_MIN_BYTES: a projector-sized stray
        self.meminfo = os.path.join(self.tmp, "meminfo")
        with open(self.meminfo, "w") as f:
            # 1,500,160,000 B available: the 1000 B Gemma fits the default
            # 1.5 GB headroom, the 2 MB Qwen does not
            f.write("MemTotal: 16000000 kB\nMemAvailable: 1465000 kB\n")
        self.out = os.path.join(self.tmp, "results")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @staticmethod
    def _exe(path, body):
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    def _run(self, *args, rss="", sudo=False):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"],
                   CARWATCH_STACK=self.stack, BOARD_BENCH_MEMINFO=self.meminfo,
                   BOARD_BENCH_ALLOW_NON_LINUX="1", STUB_RSS=rss, HOME=self.tmp,
                   BOARD_BENCH_MIN_BYTES="500", STUB_SYS=self.sys,
                   STUB_SUDO="1" if sudo else "")
        env.pop("LLAMA_CPP_REF", None)
        return subprocess.run(["bash", os.path.join(ROOT, "bench", "board-bench.sh"),
                               "--models", self.models, "--out", self.out,
                               "--llama-bin", self.llama_bin, *args],
                              capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=120)

    def test_refuses_while_brain_is_resident(self):
        p = self._run(rss=str(3 * 1024 * 1024))
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("Refusing to run", p.stderr)
        self.assertIn("--stop-brain", p.stderr)
        self.assertFalse(os.path.isdir(self.out) and os.listdir(self.out))

    def test_full_run(self):
        p = self._run()
        self.assertEqual(p.returncode, 0, p.stderr)
        files = sorted(os.listdir(self.out))
        self.assertEqual(len(files), 2, files)
        res = compare.load_result(os.path.join(self.out, [f for f in files if f.endswith(".json")][0]))
        by = {}
        for r in res["rows"]:
            by.setdefault(r["id"], []).append(r)
        self.assertEqual(res["device"]["hostname"], "stubbox")
        self.assertEqual(res["device"]["cpu_models"], ["Cortex-A55", "Cortex-A78"])
        self.assertEqual(res["device"]["cores"], 8)
        self.assertEqual(res["settings"]["threads"], [4, 8])
        self.assertEqual(res["brain"]["stopped"], False)
        gemma = {r["threads"]: r for r in by["gemma4-e2b"]}
        self.assertEqual(gemma[4]["pp"]["mean"], 40.0)
        self.assertEqual(gemma[8]["tg"]["mean"], 8.5)
        self.assertEqual(gemma[8]["thread_modes"], ["all"])
        self.assertIn("warm cache possible", gemma[4]["load_cache"])
        self.assertIsInstance(gemma[4]["load_s"], float)
        self.assertEqual(by["gemma4-e4b"][0]["status"], "MISSING")
        self.assertEqual(by["qwen36-35b"][0]["status"], "SKIPPED-NOFIT")
        self.assertIn("kept: no Qwen3.8 model fits 16 GB", by["qwen36-35b"][0]["spec_note"])
        for mid in ("ornith-9b", "qwen38-27b", "ornith-35b"):
            self.assertEqual(by[mid][0]["status"], "MISSING", mid)
        with open(os.path.join(self.out, [f for f in files if f.endswith(".md")][0])) as f:
            md = f.read()
        self.assertIn("| model | size | prompt | generation | call |", md)

    def test_download_missing(self):
        p = self._run("--only", "gemma4-e4b,ornith-35b", "--download")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.models, "gemma-4-E4B_q4_0-it.gguf")))
        self.assertFalse(any(f.endswith(".part") for f in os.listdir(self.models)))
        out = [f for f in os.listdir(self.out) if f.endswith(".json")][0]
        res = compare.load_result(os.path.join(self.out, out))
        st = {r["id"]: r for r in res["rows"]}
        self.assertEqual(st["gemma4-e4b"]["status"], "OK")
        self.assertEqual(st["ornith-35b"]["status"], "MISSING")
        self.assertIn("no source configured", st["ornith-35b"]["note"])
        self.assertNotIn("gemma4-e2b", st)

    def test_modes_quality_and_registry(self):
        """--mode both: carwatch pass, then the clean pass stops exactly the
        active CarWatch units (never reach/netfallback), restores them, the
        brain comes back; --quality scores the incident answers 2/4 with full
        provenance; --update-registry merges into model-bench.json."""
        for u in ("carwatch-brain", "carwatch-agent", "carwatch-chat", "carwatch-reach"):
            open(os.path.join(self.sys, u + ".active"), "w").close()
        open(os.path.join(self.sys, "carwatch-brain.known"), "w").close()
        reg_dir = os.path.join(self.tmp, ".config", "carwatch")
        os.makedirs(reg_dir)
        reg = os.path.join(reg_dir, "model-bench.json")
        with open(reg, "w") as f:
            json.dump({"other.gguf": {"pp512": 1.0, "tg128": 0.5},
                       "google_gemma-4-E2B-it-Q4_K_M.gguf": {
                           "carwatch": {"pp512": 1.0, "tg128": 1.0}, "keep_me": True}}, f)
        with open(os.path.join(reg_dir, "brain.env"), "w") as f:
            f.write("BRAIN_MODEL=/x.gguf\nBRAIN_THREADS=8\n")
        p = self._run("--only", "gemma4-e2b", "--mode", "both", "--quality",
                      "--update-registry", sudo=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(self.sys, "calls")) as f:
            calls = f.read().splitlines()
        self.assertIn("stop carwatch-brain", calls)
        self.assertIn("stop carwatch-agent", calls)
        self.assertIn("start carwatch-agent", calls)
        self.assertIn("start carwatch-brain", calls)
        self.assertNotIn("stop carwatch-reach", calls)
        self.assertNotIn("stop carwatch-listen", calls)   # was not active
        for u in ("carwatch-brain", "carwatch-agent", "carwatch-chat", "carwatch-reach"):
            self.assertTrue(os.path.exists(os.path.join(self.sys, u + ".active")), u)
        out = [f for f in os.listdir(self.out) if f.endswith(".json")][0]
        res = compare.load_result(os.path.join(self.out, out))
        modes = sorted({(r["mode"], r["threads"]) for r in res["rows"] if r["status"] == "OK"})
        self.assertEqual(modes, [("carwatch", 4), ("carwatch", 8), ("clean", 4), ("clean", 8)])
        self.assertEqual(res["brain"]["restored"], True)
        self.assertEqual(res["services"]["not_active_after_restore"], "")
        q = res["quality"]["gemma4-e2b"]["result"]
        self.assertEqual(q["passed"], 2, json.dumps(q["checks"], indent=1)[:2000])
        by = {c["id"]: c for c in q["checks"]}
        self.assertFalse(by["english_reply"]["pass"])
        self.assertFalse(by["stale_engine"]["pass"])
        self.assertTrue(by["finnish_reply"]["pass"])
        self.assertTrue(by["manual_no_number"]["pass"])
        # provenance: request, raw reply, telemetry with timestamps, sha, commit, sampling
        c = by["stale_engine"]
        self.assertIn("none fresh", c["request"]["messages"][0]["content"])
        self.assertEqual(c["request"]["temperature"], 0)
        self.assertIn("ts", c["telemetry"]["obd-all.json"])
        self.assertTrue(c["rendered_prompt"].startswith("<|sys|>"))
        self.assertIn("<think>", by["manual_no_number"]["raw_reply"])
        self.assertNotIn("<think>", by["manual_no_number"]["answer"])
        prov = q["provenance"]
        self.assertEqual(len(prov["model_sha256"]), 64)
        self.assertEqual(prov["server"]["build_info"], "stub-build")
        self.assertEqual(q["sampling"], {"temperature": 0, "top_p": 1.0, "seed": 0, "max_tokens": 160})
        self.assertTrue(os.path.exists(os.path.join(self.out, ".model-sha256.cache")))
        # registry: brain threads (8) used, other keys kept, top-level written
        with open(reg) as f:
            r = json.load(f)
        self.assertEqual(r["other.gguf"], {"pp512": 1.0, "tg128": 0.5})
        g = r["google_gemma-4-E2B-it-Q4_K_M.gguf"]
        self.assertTrue(g["keep_me"])
        self.assertEqual(g["carwatch"], {"pp512": 80.0, "tg128": 8.5})
        self.assertEqual(g["clean"], {"pp512": 80.0, "tg128": 8.5})
        self.assertEqual((g["pp512"], g["tg128"]), (80.0, 8.5))
        self.assertEqual(g["bench"]["threads"], 8)
        self.assertEqual(g["bench"]["grounded"], "2/4")
        self.assertIn("pp512_sd", g["bench"]["clean"])
        md = compare.compare_table([res, res])
        self.assertIn("grounded checks passed", md)
        self.assertIn("CarWatch services stopped (clean)", md)

    def test_clean_mode_needs_sudo(self):
        p = self._run("--only", "gemma4-e2b", "--mode", "clean")
        self.assertEqual(p.returncode, 2, p.stderr)

    def test_only_and_dry_run(self):
        p = self._run("--only", "gemma4-e2b", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("gemma4-e2b", p.stdout)
        self.assertNotIn("qwen36-35b", p.stdout)
        self.assertFalse(os.path.isdir(self.out) and os.listdir(self.out))


if __name__ == "__main__":
    unittest.main()
