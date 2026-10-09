"""carwatch.radiation: three sources, five states that must never look alike.

Every source is exercised against a real local HTTP server or a stand-in
radwatch script, never the network: the JSON bridge, Home Assistant (200,
401, 404, unreachable) and `radwatch.py status` (missing script, missing
DB, empty DB, stale, live, an older radwatch without `status`).

The negative control at the bottom collapses the stale/error distinction in
a copy of the classifier and checks that the scenario table catches it, so
the table cannot pass vacuously.

Set RADWATCH_SCRIPT=/path/to/radwatch.py to also run the real radwatch
`status` against a temp database (skipped otherwise)."""

import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from carwatch import radiation

NOW = 1791382470.0

# Shape of a real bridge reading (read 7 Oct 2026). serial and address are
# scrubbed: this repo is public. The optional fields (accumulated_dose,
# battery, temperature, hardness) are ABSENT, as for the first minute after
# a bridge restart.
BRIDGE_SAMPLE = {"dose_rate": 0.14134200682747178, "count_rate": 9.889473915100098,
                 "serial": "RC-10X-TEST", "firmware": "4.14",
                 "ts": NOW - 13.1, "served_at": NOW - 8.8}
BRIDGE_FULL = dict(BRIDGE_SAMPLE, accumulated_dose=12.5, battery=81, temperature=24.5,
                   hardness=1.02)


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class _Fake:
    """One local server; each test sets `routes` {path: (code, body)}."""
    routes: dict = {}
    seen_auth: list = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            _Fake.seen_auth.append(self.headers.get("Authorization"))
            code, body = _Fake.routes.get(self.path, (404, {"message": "Entity not found."}))
            if 300 <= code < 400:   # {"location": url} -> a redirect
                self.send_response(code)
                self.send_header("Location", body["location"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass


def setUpModule():
    import socketserver

    class _S(ThreadingHTTPServer):
        def server_bind(self):   # skip getfqdn(), see test_webchat_ask
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "localhost", self.server_address[1]
    global SRV, BASE
    port = _free_port()
    SRV = _S(("127.0.0.1", port), _Fake.H)
    threading.Thread(target=SRV.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{port}"


def tearDownModule():
    SRV.shutdown()


def _dead_url():
    return f"http://127.0.0.1:{_free_port()}"   # nothing listens there


# ── no source / bad config ─────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    def test_no_block_is_unconfigured_not_error(self):
        d = radiation.status({}, now=NOW)
        self.assertEqual(d["state"], "unconfigured")
        self.assertIsNone(d["error"])
        self.assertIn("radiation.source", d["message"])

    def test_empty_source_is_unconfigured(self):
        self.assertEqual(radiation.status({"radiation": {"source": ""}}, now=NOW)["state"],
                         "unconfigured")

    def test_unknown_source_is_an_error_naming_it(self):
        d = radiation.status({"radiation": {"source": "geiger"}}, now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("geiger", d["error"])

    def test_stale_seconds_default_and_override(self):
        self.assertEqual(radiation.status({}, now=NOW)["stale_seconds"], 60)
        d = radiation.status({"radiation": {"stale_seconds": 15}}, now=NOW)
        self.assertEqual(d["stale_seconds"], 15)
        d = radiation.status({"radiation": {"stale_seconds": "nonsense"}}, now=NOW)
        self.assertEqual(d["stale_seconds"], 60)

    def test_an_unexpected_exception_is_shown_not_swallowed(self):
        with mock.patch.dict(radiation.SOURCES, {"json": lambda c, now: 1 / 0}):
            d = radiation.status({"radiation": {"source": "json"}}, now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("ZeroDivisionError", d["error"])

    def test_reads_config_file_when_no_cfg_given(self):
        with mock.patch.object(radiation, "load_raw", return_value={}):
            self.assertEqual(radiation.status(now=NOW)["state"], "unconfigured")


# ── JSON bridge (the recommended source) ───────────────────────────────

class TestJsonBridge(unittest.TestCase):
    def _cfg(self, url=None, **kw):
        return {"radiation": {"source": "json", "json": {"url": url or BASE + "/radiacode.json"}, **kw}}

    def _serve(self, code, body):
        _Fake.routes = {"/radiacode.json": (code, body)}

    def test_live_sample(self):
        self._serve(200, BRIDGE_SAMPLE)
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "live", d)
        self.assertAlmostEqual(d["values"]["dose_rate"]["value"], 0.1413, places=3)
        self.assertEqual(d["values"]["dose_rate"]["unit"], "µSv/h")
        self.assertAlmostEqual(d["values"]["count_rate"]["value"], 9.889, places=2)
        self.assertEqual(d["age_s"], 13.1)
        self.assertEqual(d["extra"]["firmware"], "4.14")
        self.assertEqual(d["extra"]["bridge_lag_s"], 4.3)

    def test_absent_optional_fields_are_not_yet_reported_not_error(self):
        self._serve(200, BRIDGE_SAMPLE)
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "live")
        for k in ("accumulated_dose", "battery", "temperature"):
            self.assertIsNone(d["values"][k]["value"])
            self.assertEqual(d["values"][k]["note"], "not yet reported")
        self.assertNotIn("hardness", d["extra"])

    def test_full_payload(self):
        self._serve(200, BRIDGE_FULL)
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["values"]["battery"]["value"], 81)
        self.assertEqual(d["values"]["accumulated_dose"]["value"], 12.5)
        self.assertEqual(d["values"]["temperature"]["value"], 24.5)
        self.assertEqual(d["extra"]["hardness"], 1.02)
        self.assertIsNone(d["values"]["battery"]["note"])

    def test_null_field_is_unavailable_not_not_yet(self):
        self._serve(200, dict(BRIDGE_SAMPLE, battery=None))
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["values"]["battery"]["note"], "unavailable")

    def test_stale_ts(self):
        self._serve(200, dict(BRIDGE_SAMPLE, ts=NOW - 300))
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "stale")
        self.assertIn("300s ago", d["message"])
        self.assertIsNone(d["error"])
        self.assertIsNotNone(d["values"]["dose_rate"]["value"])  # last known still shown

    def test_stale_seconds_is_configurable(self):
        self._serve(200, dict(BRIDGE_SAMPLE, ts=NOW - 30))
        self.assertEqual(radiation.status(self._cfg(), now=NOW)["state"], "live")
        self.assertEqual(radiation.status(self._cfg(stale_seconds=20), now=NOW)["state"], "stale")

    def test_iso_ts_with_offset(self):
        from datetime import datetime, timezone
        iso = datetime.fromtimestamp(NOW - 5, timezone.utc).isoformat().replace("+00:00", "Z")
        self._serve(200, dict(BRIDGE_SAMPLE, ts=iso))
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "live")
        self.assertEqual(d["age_s"], 5.0)

    def test_missing_ts_is_stale_never_live(self):
        body = {k: v for k, v in BRIDGE_SAMPLE.items() if k not in ("ts", "served_at")}
        self._serve(200, body)
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "stale")
        self.assertIn("age unknown", d["message"])

    def test_future_ts_is_clock_skew_not_live(self):
        self._serve(200, dict(BRIDGE_SAMPLE, ts=NOW + 600))
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "stale")
        self.assertIn("future", d["message"])

    def test_no_readings_in_object_is_empty(self):
        self._serve(200, {"ts": NOW, "serial": "RC-10X-TEST"})
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "empty")

    def test_unreachable(self):
        d = radiation.status(self._cfg(url=_dead_url() + "/x.json"), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("unreachable", d["error"])

    def test_bad_json(self):
        self._serve(200, b"<html>not json")
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("invalid JSON", d["error"])

    def test_json_array_is_error(self):
        self._serve(200, [1, 2])
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("expected an object", d["error"])

    def test_object_with_no_known_fields_is_error(self):
        self._serve(200, {"hello": "world"})
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("none of the expected fields", d["error"])

    def test_http_error(self):
        self._serve(500, {"error": "boom"})
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("HTTP 500", d["error"])

    def test_url_not_set(self):
        d = radiation.status({"radiation": {"source": "json"}}, now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("radiation.json.url", d["error"])


# ── Home Assistant ─────────────────────────────────────────────────────

def _ha_state(eid, state, unit=None, **kw):
    a = {"friendly_name": eid}
    if unit:
        a["unit_of_measurement"] = unit
    return {"entity_id": eid, "state": state, "attributes": a, **kw}


def _ha_routes(prefix="sensor.radiacode_", last_reading=None, skip=()):
    from datetime import datetime, timezone
    lr = last_reading or datetime.fromtimestamp(NOW - 7, timezone.utc).isoformat()
    vals = {"dose_rate": ("0.12", "µSv/h"), "count_rate": ("8.4", "cps"),
            "accumulated_dose": ("3.2", "µSv"), "battery": ("77", "%"),
            "temperature": ("22.0", "°C"), "last_reading": (lr, None)}
    return {f"/api/states/{prefix}{k}": (200, _ha_state(prefix + k, v, u))
            for k, (v, u) in vals.items() if k not in skip}


class TestHomeAssistant(unittest.TestCase):
    TOKEN = "test-token-not-a-secret-0123456789"

    def _cfg(self, url=None, token=None, **kw):
        return {"radiation": {"source": "ha", "ha": {"url": url or BASE,
                                                     "token": self.TOKEN if token is None else token,
                                                     **kw}}}

    def setUp(self):
        _Fake.seen_auth = []
        for k in ("CARWATCH_RADIATION_HA_URL", "CARWATCH_RADIATION_HA_TOKEN"):
            p = mock.patch.dict(os.environ, {}, clear=False)
            p.start(); self.addCleanup(p.stop)
            os.environ.pop(k, None)

    def test_live(self):
        _Fake.routes = _ha_routes()
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "live", d)
        self.assertEqual(d["values"]["dose_rate"]["value"], 0.12)
        self.assertEqual(d["values"]["count_rate"]["unit"], "cps")   # HA's own unit
        self.assertEqual(d["age_basis"], "last_reading sensor")
        self.assertEqual(d["age_s"], 7.0)
        self.assertIn("not available through Home Assistant", d["alarm_note"])
        self.assertIn(f"Bearer {self.TOKEN}", _Fake.seen_auth)

    def test_401(self):
        _Fake.routes = {p: (401, {"message": "Unauthorized"}) for p in _ha_routes()}
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("rejected the token (HTTP 401)", d["error"])
        self.assertNotIn(self.TOKEN, json.dumps(d))

    def test_redirect_is_refused_and_the_token_goes_nowhere(self):
        # Codex review of #76: urllib would follow a 30x with the Authorization header.
        # The target is a dead port: following it would read "unreachable", not "redirect".
        _Fake.routes = {p: (302, {"location": _dead_url() + p}) for p in _ha_routes()}
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "error", d)
        self.assertIn("redirect (HTTP 302)", d["error"])
        self.assertNotIn("unreachable", d["error"])
        self.assertNotIn(self.TOKEN, json.dumps(d))
        self.assertEqual(len(_Fake.seen_auth), 1)   # one request to the private HA, none after

    def test_unreachable(self):
        t0 = time.time()
        d = radiation.status(self._cfg(url=_dead_url()), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("unreachable", d["error"])
        self.assertLess(time.time() - t0, 5)

    def test_stale_last_reading(self):
        from datetime import datetime, timezone
        _Fake.routes = _ha_routes(last_reading=datetime.fromtimestamp(NOW - 3600, timezone.utc).isoformat())
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "stale")

    def test_missing_entity_is_a_note_not_an_error(self):
        _Fake.routes = _ha_routes(skip=("battery",))
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "live")
        self.assertEqual(d["values"]["battery"]["note"], "no such entity")
        self.assertIn("sensor.radiacode_battery", d["extra"]["missing_entities"])

    def test_unavailable_state_is_not_zero(self):
        r = _ha_routes()
        r["/api/states/sensor.radiacode_temperature"] = (200, _ha_state("sensor.radiacode_temperature", "unavailable"))
        _Fake.routes = r
        d = radiation.status(self._cfg(), now=NOW)
        self.assertIsNone(d["values"]["temperature"]["value"])
        self.assertEqual(d["values"]["temperature"]["note"], "unavailable")

    def test_no_entities_at_all_is_error(self):
        _Fake.routes = {}
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("none of the sensor.radiacode_* entities", d["error"])

    def test_falls_back_to_last_reported(self):
        from datetime import datetime, timezone
        r = _ha_routes(skip=("last_reading",))
        eid = "sensor.radiacode_dose_rate"
        r[f"/api/states/{eid}"] = (200, _ha_state(eid, "0.12", "µSv/h",
                                   last_reported=datetime.fromtimestamp(NOW - 9, timezone.utc).isoformat()))
        _Fake.routes = r
        d = radiation.status(self._cfg(), now=NOW)
        self.assertEqual(d["state"], "live")
        self.assertIn("last_reported", d["age_basis"])

    def test_no_token_is_error_naming_both_places(self):
        d = radiation.status(self._cfg(token=""), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("CARWATCH_RADIATION_HA_TOKEN", d["error"])

    def test_env_beats_config(self):
        _Fake.routes = _ha_routes()
        with mock.patch.dict(os.environ, {"CARWATCH_RADIATION_HA_TOKEN": "env-token-0123456789abcdef",
                                          "CARWATCH_RADIATION_HA_URL": BASE}):
            d = radiation.status(self._cfg(url="http://homeassistant.local:8123"), now=NOW)
        self.assertEqual(d["state"], "live")
        self.assertIn("Bearer env-token-0123456789abcdef", _Fake.seen_auth)

    def test_token_never_sent_to_a_public_host(self):
        with mock.patch("carwatch.mercedesme._is_private_ha", return_value=False), \
                mock.patch.object(radiation, "_get_json") as g:
            d = radiation.status(self._cfg(url="http://ha.example.com:8123"), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("non-private", d["error"])
        g.assert_not_called()


# ── radwatch on this box ───────────────────────────────────────────────

def _fake_radwatch(tmp, payload=None, exit_code=0, stderr=""):
    """A stand-in for `radwatch.py status`: prints payload, exits exit_code."""
    p = os.path.join(tmp, "radwatch.py")
    with open(p, "w") as f:
        f.write("import sys, json\n"
                f"assert sys.argv[1:3] == ['status', '--db'], sys.argv\n"
                f"sys.stderr.write({stderr!r})\n"
                f"print({json.dumps(payload) if payload is not None else ''!r})\n"
                f"sys.exit({exit_code})\n")
    return p


def _rw_reading(age, **kw):
    return {"ok": True, "readings": 700,
            "reading": {"ts": "2026-10-07T16:00:00", "age_s": age, "dose_rate_usv_h": 0.11,
                        "dose_rate_err_pct": 9.0, "count_rate_cps": 9.5, "count_rate_err_pct": 3.0},
            "device": {"ts": "2026-10-07T15:59:30", "age_s": age + 30, "accumulated_dose_usv": 4.2,
                       "accumulated_over_s": 3600, "temperature_c": 23.0, "battery_pct": 64.0},
            "watch": {"ts": "2026-10-07T16:00:00", "baseline_cps": 9.4, "window_cps": 9.6,
                      "sigma": 0.8, "alert": False, "threshold_calibrated": False},
            "watch_note": None, **kw}


class TestRadwatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _cfg(self, script):
        return {"radiation": {"source": "radwatch",
                              "radwatch": {"python": sys.executable, "script": script,
                                           "db": os.path.join(self.tmp, "radwatch.sqlite")}}}

    def test_live_with_alarm(self):
        d = radiation.status(self._cfg(_fake_radwatch(self.tmp, _rw_reading(3.0))), now=NOW)
        self.assertEqual(d["state"], "live", d)
        self.assertEqual(d["values"]["dose_rate"]["value"], 0.11)
        self.assertEqual(d["values"]["battery"]["value"], 64.0)
        self.assertEqual(d["alarm"]["sigma"], 0.8)   # radwatch's number, not recomputed
        self.assertFalse(d["alarm"]["alert"])

    def test_stale(self):
        d = radiation.status(self._cfg(_fake_radwatch(self.tmp, _rw_reading(120.0))), now=NOW)
        self.assertEqual(d["state"], "stale")
        self.assertIsNone(d["error"])

    def test_empty_db(self):
        payload = {"ok": True, "readings": 0, "reading": None, "device": None,
                   "watch": None, "watch_note": "not enough readings yet: 0 of 630"}
        d = radiation.status(self._cfg(_fake_radwatch(self.tmp, payload)), now=NOW)
        self.assertEqual(d["state"], "empty")
        self.assertIn("no readings yet", d["message"])
        self.assertIsNone(d["error"])
        self.assertEqual(d["alarm_note"], "not enough readings yet: 0 of 630")

    def test_no_device_record_yet_is_not_yet_reported(self):
        payload = _rw_reading(3.0, device=None)
        d = radiation.status(self._cfg(_fake_radwatch(self.tmp, payload)), now=NOW)
        self.assertEqual(d["state"], "live")
        self.assertEqual(d["values"]["battery"]["note"], "not yet reported")

    def test_missing_db_is_error_with_radwatchs_reason(self):
        payload = {"ok": False, "error": "no radwatch database at /x/radwatch.sqlite"}
        d = radiation.status(self._cfg(_fake_radwatch(self.tmp, payload, exit_code=2)), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("no radwatch database", d["error"])

    def test_script_missing(self):
        d = radiation.status(self._cfg(os.path.join(self.tmp, "nope.py")), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("radwatch not found", d["error"])

    def test_old_radwatch_without_status(self):
        p = _fake_radwatch(self.tmp, None, exit_code=2,
                           stderr="radwatch.py: error: argument cmd: invalid choice: 'status'\n")
        d = radiation.status(self._cfg(p), now=NOW)
        self.assertEqual(d["state"], "error")
        self.assertIn("invalid choice", d["error"])


@unittest.skipUnless(os.environ.get("RADWATCH_SCRIPT"), "set RADWATCH_SCRIPT to run against real radwatch")
class TestRealRadwatch(unittest.TestCase):
    """The same states through the real `radwatch.py status`, on a temp DB."""

    def _cfg(self, db):
        return {"radiation": {"source": "radwatch", "radwatch": {
            "python": sys.executable, "script": os.environ["RADWATCH_SCRIPT"], "db": db}}}

    def _db(self, rows):
        db = os.path.join(tempfile.mkdtemp(), "r.sqlite")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE reading(ts TEXT PRIMARY KEY, count_rate REAL, count_rate_err REAL, "
                  "dose_rate REAL, dose_rate_err REAL, flags INT)")
        c.executemany("INSERT INTO reading VALUES(?,?,?,?,?,0)", rows)
        c.commit(); c.close()
        return db

    def test_missing_empty_stale_live(self):
        from datetime import datetime, timedelta
        missing = os.path.join(tempfile.mkdtemp(), "typo.sqlite")
        self.assertEqual(radiation.status(self._cfg(missing))["state"], "error")
        self.assertFalse(os.path.exists(missing))
        self.assertEqual(radiation.status(self._cfg(self._db([])))["state"], "empty")
        old = (datetime.now() - timedelta(minutes=10)).isoformat()
        self.assertEqual(radiation.status(self._cfg(self._db([(old, 9, 3, 1e-7, 9)])))["state"], "stale")
        new = datetime.now().isoformat()
        d = radiation.status(self._cfg(self._db([(new, 9, 3, 1e-7, 9)])))
        self.assertEqual(d["state"], "live", d)
        self.assertEqual(d["values"]["dose_rate"]["value"], 0.1)


# ── negative control ───────────────────────────────────────────────────

def _scenario_failures(status_fn):
    """Run the four must-differ scenarios through status_fn; list what went wrong."""
    bad = []
    _Fake.routes = {"/b.json": (200, dict(BRIDGE_SAMPLE, ts=NOW - 600))}
    cfg = {"radiation": {"source": "json", "json": {"url": BASE + "/b.json"}}}
    expect = [
        ({}, "unconfigured"),
        ({"radiation": {"source": "json", "json": {"url": _dead_url() + "/b.json"}}}, "error"),
        (cfg, "stale"),
    ]
    for c, want in expect:
        got = status_fn(c, now=NOW)["state"]
        if got != want:
            bad.append(f"{want} rendered as {got}")
    _Fake.routes = {"/b.json": (200, dict(BRIDGE_SAMPLE, ts=NOW - 2))}
    if status_fn(cfg, now=NOW)["state"] != "live":
        bad.append("live not live")
    return bad


class TestNegativeControl(unittest.TestCase):
    def test_real_classifier_keeps_every_state_distinct(self):
        self.assertEqual(_scenario_failures(radiation.status), [])

    def test_collapsed_stale_is_caught(self):
        """A classifier that drops the age check (stale shown as live) must fail the table."""
        def no_stale(reading, stale_s):
            return "live", "collapsed"
        with mock.patch.object(radiation, "classify", no_stale):
            self.assertIn("stale rendered as live", _scenario_failures(radiation.status))

    def test_collapsed_error_is_caught(self):
        """A status() that swallows source errors into 'empty' must fail the table."""
        real = radiation.status

        def swallow(cfg, now=None):
            d = real(cfg, now)
            return dict(d, state="empty") if d["state"] == "error" else d
        self.assertIn("error rendered as empty", _scenario_failures(swallow))


# ── the routes, through a real server ──────────────────────────────────

class TestRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socketserver
        from carwatch import webchat
        cls.webchat = webchat

        class _S(webchat.ThreadingHTTPServer):
            def server_bind(self):
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = "localhost", self.server_address[1]
        cls.port = _free_port()
        cls.srv = _S(("127.0.0.1", cls.port), webchat.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        with mock.patch.object(self.webchat.Handler, "_authorised", lambda self: True):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read().decode()

    def test_api_returns_state_json(self):
        with mock.patch.object(radiation, "load_raw", return_value={}):
            code, ctype, body = self._get("/api/radiation")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(body)["state"], "unconfigured")

    def test_page_serves_and_knows_every_state(self):
        code, ctype, body = self._get("/radiation?t=x")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        for st in ("live", "stale", "error", "empty", "unconfigured"):
            self.assertIn(f"{st}:", body.split("const TITLES=")[1].split("};")[0])
        self.assertIn("/api/radiation", body)

    def test_unified_page_links_here(self):
        self.assertIn("href=/radiation", self.webchat.UNIFIED_PAGE)


if __name__ == "__main__":
    unittest.main()
