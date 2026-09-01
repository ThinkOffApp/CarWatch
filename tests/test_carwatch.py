"""Regression tests for the review findings. Run: python3 -m pytest tests/
(or python3 tests/test_carwatch.py for a dependency-free run)."""

import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from carwatch.commands import Commands  # noqa: E402
from carwatch.outbox import Outbox  # noqa: E402
from carwatch.trips import State, TripTracker  # noqa: E402
from carwatch.wolfbox import Wolfbox  # noqa: E402


class FakeTrips:
    state = State.PARKED_HOME


class TestTrips(unittest.TestCase):
    def _tracker(self, ssid_sequence):
        t = TripTracker(["Home"], idle_seconds=1, neutral_ssids=["CamAP"])
        seq = iter(ssid_sequence)
        import carwatch.trips as trips_mod
        self._orig = trips_mod.current_ssid
        trips_mod.current_ssid = lambda: next(seq)
        self.addCleanup(lambda: setattr(trips_mod, "current_ssid", self._orig))
        return t

    def test_parked_away_holds_no_oscillation(self):
        # codexmb P1: PARKED_AWAY + no signal must hold, not re-enter
        # DRIVING and repost "Parked away" every idle interval.
        import time
        t = self._tracker(["Home", None, None, None, None, None])
        t.tick()  # PARKED_HOME
        t.tick()  # -> DRIVING (real departure)
        time.sleep(1.1)
        events = t.tick()  # decays -> PARKED_AWAY
        self.assertEqual([e.kind for e in events], ["parked_away"])
        time.sleep(1.1)
        self.assertEqual(t.tick(), [])  # holds silently
        time.sleep(1.1)
        self.assertEqual(t.tick(), [])

    def test_boot_away_is_not_a_departure(self):
        t = self._tracker([None])
        events = t.tick()  # UNKNOWN -> DRIVING silently
        self.assertEqual(events, [])

    def test_camera_ap_is_park_neutral(self):
        t = self._tracker(["Home", "CamAP", "CamAP"])
        t.tick()
        self.assertEqual(t.tick(), [])  # camera AP: hold, no departure
        self.assertEqual(t.state, State.PARKED_HOME)


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.c = Commands("@gle", self.dir, FakeTrips())

    def test_first_run_replays_nothing(self):
        msgs = [{"id": "2", "from": "p", "body": "@gle battery"}]
        replies, _ = self.c.pending_replies(msgs)
        self.assertEqual(replies, [])

    def test_mark_after_post_and_token_mention(self):
        self.c.pending_replies([{"id": "1", "from": "p", "body": "x"}])  # seed
        msgs = [
            {"id": "3", "from": "p", "body": "@gleen typo"},
            {"id": "2", "from": "p", "body": "@gle status"},
            {"id": "1", "from": "p", "body": "x"},
        ]
        replies, nid = self.c.pending_replies(msgs)
        self.assertEqual(len(replies), 1)
        # unmarked -> same replies again (offline retry semantics)
        self.assertEqual(self.c.pending_replies(msgs)[0], replies)
        self.c.mark(nid)
        self.assertEqual(self.c.pending_replies(msgs)[0], [])

    def test_word_bound_regexes(self):
        self.assertIn("OBD", self.c._dispatch("@gle battery"))
        self.assertIn("Not sure", self.c._dispatch("@gle batteries wherever"))


class TestOutbox(unittest.TestCase):
    def test_offline_retains_online_drains(self):
        o = Outbox(tempfile.mkdtemp())
        o.enqueue("a")
        o.enqueue("b", file_url="u", file_name="n", file_size=1)
        sent = []

        class Fail:
            def post(self, body, **kw):
                raise OSError

        class OK:
            def post(self, body, **kw):
                sent.append((body, kw))

        o.flush(Fail())
        self.assertEqual(len(o._load()), 2)
        o.flush(OK())
        self.assertEqual([s[0] for s in sent], ["a", "b"])
        self.assertEqual(o._load(), [])


class TestWolfbox(unittest.TestCase):
    def test_novatek_parse_returns_all_events_sorted(self):
        w = Wolfbox("h")
        w._novatek = True
        xml = ("<L><FPATH>A:\\DCIM\\Event\\B_RO.MP4</FPATH>"
               "<FPATH>A:\\DCIM\\Movie\\C.MP4</FPATH>"
               "<FPATH>A:\\DCIM\\Event\\A_RO.MP4</FPATH></L>")
        w._get = types.MethodType(lambda self, p, timeout=6: xml.encode(), w)
        self.assertEqual(
            w.new_event_clips(),
            ["http://h/DCIM/Event/A_RO.MP4", "http://h/DCIM/Event/B_RO.MP4"],
        )


class GroundingTests(unittest.TestCase):
    """The car must never assert state it cannot actually sense."""

    def test_unsensed_state_is_never_a_fact(self):
        from carwatch.grounding import build_system_prompt, default_state
        facts, cannot = default_state(engine_on=False, parked=True)
        prompt = build_system_prompt(facts, cannot)
        # what we DO know
        self.assertIn("engine: OFF", prompt)
        # what we do NOT: must be listed as unsensable, not omitted
        for unknown in ("fuel level", "battery voltage", "tyre pressures"):
            self.assertIn(unknown, prompt)
        # The obd fact must be precise both ways: the software exists (so the
        # model cannot claim "not built" - it did exactly that on Aug 12),
        # AND the live link is absent (so it cannot claim readings).
        self.assertIn("IS built", prompt)
        self.assertIn("cable link to the car is NOT up", prompt)

    def test_engine_unknown_is_not_asserted(self):
        from carwatch.grounding import build_system_prompt, default_state
        facts, cannot = default_state()  # engine state genuinely unknown
        prompt = build_system_prompt(facts, cannot)
        self.assertNotIn("engine: ON", prompt)
        self.assertNotIn("engine: OFF", prompt)
        self.assertIn("whether your engine is running", prompt)

    def test_manual_claim_requires_actual_excerpts(self):
        from carwatch.grounding import build_system_prompt, default_state
        facts, cannot = default_state(engine_on=False)
        self.assertIn("do not claim to have read the manual",
                      build_system_prompt(facts, cannot))
        self.assertIn("may cite their page numbers",
                      build_system_prompt(facts, cannot, manual_excerpts="p.344: ..."))


class ManualQueryExpansionTests(unittest.TestCase):
    """A question phrased in ordinary words must still find the manual's section."""

    def test_voltage_synonyms_reach_the_manual_wording(self):
        from carwatch.manual import _expand
        # petrus asked about "230V"; the GLE prints 115V. Must bridge.
        for said in ("230v", "240v", "mains"):
            expanded = _expand([said])
            self.assertIn("115", expanded, f"{said} should reach the manual's 115")
            self.assertIn("socket", expanded)

    def test_everyday_words_map_to_manual_words(self):
        from carwatch.manual import _expand
        self.assertIn("cargo", _expand(["boot"]))
        self.assertIn("windshield", _expand(["windscreen"]))
        self.assertIn("tire", _expand(["tyre"]))

    def test_expansion_only_widens_never_drops(self):
        from carwatch.manual import _expand
        original = ["tyre", "pressure", "warning"]
        expanded = _expand(original)
        for t in original:
            self.assertIn(t, expanded)


class ObdDoipTests(unittest.TestCase):
    """The DoIP/UDS message layer, testable without the car present."""

    def test_doip_frame_roundtrip(self):
        from carwatch import obd
        f = obd._doip_frame(obd.PT_VEHICLE_IDENT_REQ, b"")
        ptype, body = obd._parse_doip(f)
        self.assertEqual(ptype, obd.PT_VEHICLE_IDENT_REQ)
        self.assertEqual(body, b"")

    def test_pid_decoders(self):
        from carwatch import obd
        self.assertEqual(obd._rpm(bytes([0x1A, 0xF8])), 1726.0)
        self.assertEqual(obd._coolant(bytes([0x7B])), 83)
        self.assertEqual(obd._speed(bytes([0x64])), 100)
        self.assertAlmostEqual(obd._volts(bytes([0x2E, 0xE0])), 12.0, places=2)

    def test_mode01_response_parse_and_reject(self):
        from carwatch import obd
        self.assertEqual(
            obd._parse_pid_response(bytes([0x41, 0x0C, 0x1A, 0xF8]), 0x0C),
            ("engine_rpm", 1726.0))
        # negative response (0x7F) must not decode as a reading
        self.assertIsNone(
            obd._parse_pid_response(bytes([0x7F, 0x01, 0x11]), 0x0C))


class CarFactsIntegrationTests(unittest.TestCase):
    """OBD readings must flow into grounded facts, and vanish when no link."""

    def test_engine_readings_become_facts(self):
        import json, os, tempfile
        from unittest import mock
        from carwatch import selfstate, obd
        cfgdir = tempfile.mkdtemp()
        cfg = os.path.join(cfgdir, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"obd": {"enabled": True, "gateway_ip": "169.254.1.1"}}, fh)
        with mock.patch.dict(os.environ, {"CARWATCH_CONFIG": cfg}), \
             mock.patch.object(obd, "connect", return_value=mock.Mock()), \
             mock.patch.object(obd, "read_all", return_value={
                 "engine_rpm": 820.0, "coolant_c": 88, "module_voltage": 14.2}):
            facts = selfstate.car_facts()
        self.assertEqual(facts["engine"], "running at 820 rpm")
        self.assertEqual(facts["coolant"], "88 C")
        self.assertEqual(facts["car 12V battery"], "14.2 V")

    def test_no_facts_when_obd_disabled(self):
        import json, os, tempfile
        from unittest import mock
        from carwatch import selfstate
        cfg = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(cfg, "w") as fh:
            json.dump({}, fh)
        with mock.patch.dict(os.environ, {"CARWATCH_CONFIG": cfg}):
            self.assertEqual(selfstate.car_facts(), {})


class TestDtcDecode(unittest.TestCase):
    """decode_dtc_reply must be CAN-aware: multiple ECUs each answering
    '43 00' (zero stored codes) must NOT decode as phantom P0043 pairs -
    the exact bug that showed identical fake codes on both cars."""

    def _dec(self, s):
        from carwatch.elm327 import decode_dtc_reply
        return decode_dtc_reply(s)

    def test_multi_ecu_zero_codes_is_empty(self):
        self.assertEqual(self._dec("43 00 43 00 43 00"), [])

    def test_single_ecu_zero_codes_is_empty(self):
        self.assertEqual(self._dec("43 00"), [])

    def test_real_single_code_on_can(self):
        self.assertEqual(self._dec("43 01 00 43"), ["P0043"])

    def test_two_codes_on_can(self):
        self.assertEqual(self._dec("43 02 01 33 04 20"), ["P0133", "P0420"])

    def test_kline_legacy_format(self):
        self.assertEqual(self._dec("43 04 20 00 00"), ["P0420"])

    def test_garbage_is_empty(self):
        self.assertEqual(self._dec("NO DATA"), [])
        self.assertEqual(self._dec(""), [])


class TestServedPages(unittest.TestCase):
    """The dash outage of Aug 15: a bare \\n inside a JS regex in a non-raw
    Python string became a real newline and killed the whole script block.
    Guard the construction: no served script may contain a newline right
    after a regex opener."""

    def test_no_newline_inside_js_regex(self):
        from carwatch import webchat
        for name in ("PAGE", "DASH_PAGE"):
            page = getattr(webchat, name)
            self.assertNotIn("(/\n", page, f"{name}: newline inside JS regex")


class TestModelSelector(unittest.TestCase):
    """THI-38: the registry lists real ggufs (never an mmproj), the fit
    check refuses what RAM cannot hold, and a swap never interrupts a
    running answer or an in-flight load."""

    GB = 1024 ** 3

    def setUp(self):
        import carwatch.models as models_mod
        import carwatch.selfstate as selfstate_mod
        import carwatch.voicestate as voicestate_mod
        self.m = models_mod
        self.vs = voicestate_mod
        self.tmp = tempfile.mkdtemp()
        self._orig = {
            "dirs": models_mod.MODEL_DIRS,
            "mem": models_mod._mem_total,
            "bench": models_mod._bench_map,
            "env": models_mod.ENV_FILE,
            "serving": selfstate_mod.serving_model,
            "state": models_mod.brain_state,
            "lock": voicestate_mod.BRAIN_LOCK,
        }
        models_mod.MODEL_DIRS = [self.tmp]
        models_mod.ENV_FILE = os.path.join(self.tmp, "brain.env")
        models_mod._mem_total = lambda: 16 * self.GB
        models_mod._bench_map = lambda: {
            "small.gguf": {"pp512": 30.0, "tg128": 6.2}}
        selfstate_mod.serving_model = lambda: "small.gguf"
        models_mod.brain_state = lambda: "ready"
        voicestate_mod.BRAIN_LOCK = os.path.join(self.tmp, "brain.lock")
        self.addCleanup(self._restore)

    def _restore(self):
        import carwatch.selfstate as selfstate_mod
        self.m.MODEL_DIRS = self._orig["dirs"]
        self.m._mem_total = self._orig["mem"]
        self.m._bench_map = self._orig["bench"]
        self.m.ENV_FILE = self._orig["env"]
        selfstate_mod.serving_model = self._orig["serving"]
        self.m.brain_state = self._orig["state"]
        self.vs.BRAIN_LOCK = self._orig["lock"]

    def _gguf(self, name, size):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.seek(size - 1)
            f.write(b"\0")
        return path

    def test_registry_filters_mmprojs_and_sizes_honestly(self):
        self._gguf("small.gguf", 1 * self.GB)
        self._gguf("big.gguf", 16 * self.GB)
        self._gguf("vision-mmproj.gguf", 1 * self.GB)
        with open(os.path.join(self.tmp, "note.txt"), "w") as f:
            f.write("not a model")
        models = self.m.list_models()
        self.assertEqual([m["file"] for m in models],
                         ["small.gguf", "big.gguf"])
        small, big = models
        self.assertTrue(small["fits"])
        self.assertTrue(small["running"])
        self.assertEqual(small["bench"]["tg128"], 6.2)
        # 15GB into 16GB minus headroom must be refused, not attempted.
        self.assertFalse(big["fits"])

    def test_select_refusals(self):
        self._gguf("small.gguf", 1 * self.GB)
        self._gguf("big.gguf", 16 * self.GB)
        self.assertIn("no such model",
                      self.m.select_model("ghost")["error"])
        self.assertIn("already the running brain",
                      self.m.select_model("small")["error"])
        self.assertIn("too big",
                      self.m.select_model("big")["error"])

    def _patch_run(self, fn):
        orig_run = self.m.subprocess.run
        self.m.subprocess.run = fn
        self.addCleanup(lambda: setattr(self.m.subprocess, "run", orig_run))

    class _R:
        def __init__(self, rc=0, err=""):
            self.returncode = rc
            self.stdout = ""
            self.stderr = err

    def test_select_never_mid_answer_or_mid_load(self):
        import fcntl
        self._gguf("mid.gguf", 5 * self.GB)
        # A REAL held lock, not a stubbed probe: the swap must refuse while
        # an answer holds voicestate's flock (codexmb's race finding).
        holder = open(self.vs.BRAIN_LOCK, "w")
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            self.assertIn("mid-answer", self.m.select_model("mid")["error"])
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
        self.m.brain_state = lambda: "loading"
        self.assertIn("already loading", self.m.select_model("mid")["error"])

    def test_select_holds_lock_across_restart(self):
        # The never-mid-answer guard must not be probe-and-release: while
        # systemctl restart runs, a competing ask must find the lock HELD.
        import fcntl
        self._gguf("mid.gguf", 5 * self.GB)
        seen = {}

        def run(cmd, **kw):
            with open(self.vs.BRAIN_LOCK, "a") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen["held"] = False
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    seen["held"] = True
            return self._R()

        self._patch_run(run)
        self.assertTrue(self.m.select_model("mid")["ok"])
        self.assertTrue(seen.get("held"),
                        "brain lock was not held during the restart")

    def test_select_writes_env_and_restarts(self):
        path = self._gguf("mid.gguf", 5 * self.GB)
        calls = []
        self._patch_run(lambda cmd, **kw: (calls.append(cmd), self._R())[1])
        res = self.m.select_model("mid")
        self.assertTrue(res["ok"], res)
        with open(self.m.ENV_FILE) as f:
            self.assertEqual(f.read().strip(), "BRAIN_MODEL=" + path)
        self.assertEqual(calls[0][-2:], ["restart", "carwatch-brain"])

    def test_select_rolls_back_env_on_restart_failure(self):
        # A failed restart must not leave the unverified model armed for the
        # next boot (codexmb's persistence finding).
        self._gguf("mid.gguf", 5 * self.GB)
        self._patch_run(lambda cmd, **kw: self._R(rc=1, err="unit hosed"))
        # Case 1: a previous selection existed - it must come back.
        with open(self.m.ENV_FILE, "w") as f:
            f.write("BRAIN_MODEL=/old/model.gguf\n")
        res = self.m.select_model("mid")
        self.assertFalse(res["ok"])
        with open(self.m.ENV_FILE) as f:
            self.assertEqual(f.read().strip(), "BRAIN_MODEL=/old/model.gguf")
        # Case 2: no previous selection - the file must be gone again.
        os.remove(self.m.ENV_FILE)
        res = self.m.select_model("mid")
        self.assertFalse(res["ok"])
        self.assertFalse(os.path.exists(self.m.ENV_FILE))

    def test_brain_busy_fails_closed(self):
        # If the lock cannot even be inspected, claim busy - a wrong "idle"
        # removes the one warning this probe exists to give.
        self.vs.BRAIN_LOCK = os.path.join(self.tmp, "no-such-dir", "x.lock")
        self.assertTrue(self.m.brain_busy())



class TestModelAwareEstimates(unittest.TestCase):
    """petrus, 29 Aug (live test): the dash showed "~164s typical" - the
    35B's median - while a freshly swapped 2B was answering. Estimates must
    follow the RUNNING model."""

    GB = 1024 ** 3

    def setUp(self):
        import carwatch.models as models_mod
        import carwatch.selfstate as selfstate_mod
        import carwatch.voicestate as vs
        self.m = models_mod
        self.tmp = tempfile.mkdtemp()
        self._orig = (models_mod.MODEL_DIRS, models_mod._bench_map,
                      selfstate_mod.serving_model, vs.STATS_PATH)
        models_mod.MODEL_DIRS = [self.tmp]
        models_mod._bench_map = lambda: {
            "small.gguf": {"pp512": 30.0, "tg128": 6.2}}
        selfstate_mod.serving_model = lambda: "small.gguf"
        vs.STATS_PATH = os.path.join(self.tmp, "voice-stats.json")
        self.addCleanup(self._restore)

    def _restore(self):
        import carwatch.selfstate as selfstate_mod
        import carwatch.voicestate as vs
        (self.m.MODEL_DIRS, self.m._bench_map,
         selfstate_mod.serving_model, vs.STATS_PATH) = self._orig

    def test_estimate_follows_the_running_model(self):
        import carwatch.voicestate as vs
        # Another model's slow median must NOT leak into a fresh model's
        # estimate - bench-derived numbers win until real samples exist.
        vs.record_answer_s(164.0, model="Qwen-35B.gguf")
        self.assertAlmostEqual(self.m.expected_answer_s(),
                               round(1500 / 30.0 + 200 / 6.2), delta=1)
        # Real samples on the running model beat the derivation.
        for s_ in (40.0, 45.0, 50.0):
            vs.record_answer_s(s_, model="small.gguf")
        self.assertEqual(self.m.expected_answer_s(), 45.0)


# Keep this at the BOTTOM of the file: unittest.main() runs the moment this
# line executes, so any class defined after it silently never runs in the
# dependency-free `python3 tests/test_carwatch.py` path (found 28 Aug: the
# direct run reported 14 tests while the suite held 33).
if __name__ == "__main__":
    unittest.main()


class TestConfigResolution(unittest.TestCase):
    """One config file for every module (issue #23, item 1): explicit env,
    then the state dir, then ~/.carwatch, then the legacy /etc path; and the
    write target is the first candidate when nothing exists yet."""

    def _env(self, **kw):
        from unittest import mock
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("CARWATCH_CONFIG", "CARWATCH_STATE")}
        clean.update(kw)
        return mock.patch.dict(os.environ, clean, clear=True)

    def test_explicit_env_wins(self):
        from carwatch import config
        d = tempfile.mkdtemp()
        explicit = os.path.join(d, "explicit.json")
        state = os.path.join(d, "state")
        os.makedirs(state)
        for p in (explicit, os.path.join(state, "config.json")):
            with open(p, "w") as fh:
                fh.write("{}")
        with self._env(CARWATCH_CONFIG=explicit, CARWATCH_STATE=state):
            self.assertEqual(config.config_path(), explicit)

    def test_explicit_env_wins_even_when_missing(self):
        """A mistyped CARWATCH_CONFIG must fail visibly, never fall back to
        another file's credentials (codex, PR #24)."""
        from carwatch import config
        d = tempfile.mkdtemp()
        state = os.path.join(d, "state")
        os.makedirs(state)
        with open(os.path.join(state, "config.json"), "w") as fh:
            fh.write('{"api_key": "other"}')
        missing = os.path.join(d, "nope.json")
        with self._env(CARWATCH_CONFIG=missing, CARWATCH_STATE=state):
            self.assertEqual(config.config_path(), missing)
            self.assertEqual(config.load_raw(), {})
            with self.assertRaises(FileNotFoundError):
                config.load_strict()

    def test_update_config_fails_closed_on_malformed_file(self):
        """The wifi editor's read-modify-write must leave a malformed or
        unreadable config untouched instead of replacing it with only the
        field being edited (codexmb P1, PR #24)."""
        from carwatch import config
        state = tempfile.mkdtemp()
        p = os.path.join(state, "config.json")
        broken = '{"api_key": "k", "room": "r", "home_ssids": ['   # truncated
        with open(p, "w") as fh:
            fh.write(broken)
        with self._env(CARWATCH_STATE=state):
            with self.assertRaises(ValueError):
                config.update_config(lambda c: c.__setitem__("home_ssids", ["x"]))
        with open(p) as fh:
            self.assertEqual(fh.read(), broken)
        with self._env(CARWATCH_STATE=state):
            with self.assertRaises(FileNotFoundError):
                config.update_config(lambda c: None, os.path.join(state, "none.json"))
        # and the happy path keeps every other key
        with open(p, "w") as fh:
            fh.write('{"api_key": "k", "room": "r", "handle": "@gle"}')
        with self._env(CARWATCH_STATE=state):
            cfg = config.update_config(lambda c: c.__setitem__("home_ssids", ["Home"]))
        self.assertEqual(cfg, {"api_key": "k", "room": "r", "handle": "@gle",
                               "home_ssids": ["Home"]})
        with self._env(CARWATCH_STATE=state):
            self.assertEqual(config.load_strict()["api_key"], "k")

    def test_state_dir_when_no_explicit(self):
        from carwatch import config
        state = tempfile.mkdtemp()
        p = os.path.join(state, "config.json")
        with open(p, "w") as fh:
            fh.write('{"handle": "@x", "owner": "@Anna"}')
        with self._env(CARWATCH_STATE=state):
            self.assertEqual(config.config_path(), p)
            self.assertEqual(config.load_raw()["handle"], "@x")
            self.assertEqual(config.owner_handle(), "anna")

    def test_write_target_is_state_dir_when_nothing_exists(self):
        from carwatch import config
        state = os.path.join(tempfile.mkdtemp(), "fresh")
        with self._env(CARWATCH_STATE=state):
            # nothing exists yet: the path is where a new config belongs
            self.assertEqual(config.config_path(),
                             os.path.join(state, "config.json"))
            written = config.save_raw({"handle": "@y"})
            self.assertEqual(written, os.path.join(state, "config.json"))
            self.assertEqual(oct(os.stat(written).st_mode & 0o777), "0o600")
            self.assertEqual(config.load_raw()["handle"], "@y")

    def test_cli_get_and_path(self):
        from carwatch import config
        import io
        from unittest import mock
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "config.json"), "w") as fh:
            fh.write('{"handle": "@gle"}')
        with self._env(CARWATCH_STATE=state):
            out = io.StringIO()
            with mock.patch.object(sys, "stdout", out):
                self.assertEqual(config.main(["get", "handle"]), 0)
                self.assertEqual(config.main(["path"]), 0)
            lines = out.getvalue().splitlines()
        self.assertEqual(lines, ["@gle", os.path.join(state, "config.json")])


class TestOwnerGate(unittest.TestCase):
    """The room gate reads the owner from config (issue #23, item 3)."""

    def test_configured_owner_and_devices_only(self):
        from carwatch.agent import _owner_ok
        self.assertTrue(_owner_ok("petrus", "petrus"))
        self.assertTrue(_owner_ok("petrus-watch", "@Petrus"))
        self.assertFalse(_owner_ok("anna", "petrus"))
        self.assertFalse(_owner_ok("@claudemm", "petrus"))

    def test_no_owner_means_any_human(self):
        from carwatch.agent import _owner_ok
        self.assertTrue(_owner_ok("anna", ""))
        self.assertFalse(_owner_ok("@claudemm", ""))
        self.assertFalse(_owner_ok("", ""))

    def test_profile_overlays_neutral_defaults(self):
        """A config with a handle but no car block keeps its repo profile
        identity; an explicit car block still wins (codex P2, PR #24)."""
        import json
        from unittest import mock
        from carwatch import agent
        state = tempfile.mkdtemp()
        p = os.path.join(state, "config.json")
        with open(p, "w") as fh:
            json.dump({"handle": "@gle"}, fh)
        with mock.patch.object(agent, "CONFIG_PATH", p):
            car = agent.car_identity()
        self.assertIn("GLE", car["identity"])          # from profiles/gle.json
        self.assertNotEqual(car["appearance"], agent._CAR_DEFAULTS["appearance"])
        with open(p, "w") as fh:
            json.dump({"handle": "@gle", "car": {"appearance": "matte black"}}, fh)
        with mock.patch.object(agent, "CONFIG_PATH", p):
            car = agent.car_identity()
        self.assertEqual(car["appearance"], "matte black")
        self.assertIn("GLE", car["identity"])
        with open(p, "w") as fh:
            json.dump({"handle": "@nobodyscar"}, fh)
        with mock.patch.object(agent, "CONFIG_PATH", p):
            car = agent.car_identity()
        self.assertEqual(car, agent._CAR_DEFAULTS)

    def test_mentions_me_uses_owner(self):
        from carwatch import agent
        msg = {"from": "anna", "body": "@gle how fast am I going"}
        self.assertTrue(agent._mentions_me(msg, "@gle", ""))
        self.assertFalse(agent._mentions_me(msg, "@gle", "petrus"))
        self.assertTrue(agent._mentions_me(dict(msg, **{"from": "petrus"}), "@gle", "petrus"))


class TestPostAsCar(unittest.TestCase):
    """The in-repo poster replaces ~/post-as-gle.py (issue #23, item 2)."""

    def _env(self, state):
        from unittest import mock
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("CARWATCH_CONFIG", "CARWATCH_STATE")}
        clean["CARWATCH_STATE"] = state
        return mock.patch.dict(os.environ, clean, clear=True)

    def test_skips_without_config_and_never_raises(self):
        from carwatch import room
        with self._env(tempfile.mkdtemp()):
            self.assertFalse(room.post_as_car("hello"))

    def test_posts_with_config(self):
        import json
        from unittest import mock
        from carwatch import room
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "config.json"), "w") as fh:
            json.dump({"api_key": "k", "room": "r", "api_base": "https://x"}, fh)
        seen = {}
        def fake_post(self, body, **kw):
            seen["room"], seen["body"], seen["base"] = self.room, body, self.api_base
            return {}
        with self._env(state), mock.patch.object(room.RoomClient, "post", fake_post):
            self.assertTrue(room.post_as_car("engine on"))
        self.assertEqual(seen, {"room": "r", "body": "engine on", "base": "https://x"})

    def test_cli_reads_body_from_file(self):
        import json
        from unittest import mock
        from carwatch import room
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "config.json"), "w") as fh:
            json.dump({"api_key": "k", "room": "r"}, fh)
        body_file = os.path.join(state, "body.txt")
        with open(body_file, "w") as fh:
            fh.write("two\nlines\n")
        seen = []
        with self._env(state), mock.patch.object(
                room.RoomClient, "post", lambda self, body, **kw: seen.append(body) or {}):
            self.assertEqual(room.main(["--file", body_file]), 0)
        self.assertEqual(seen, ["two\nlines"])
