"""preflight (#47): each tile says READY or names what is missing; the
summary is READY only when every tile is. Probes are replaced, files live
in a temp CARWATCH_STATE, nothing touches the real box."""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from carwatch import preflight as pf


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = os.environ.get("CARWATCH_STATE")
        os.environ["CARWATCH_STATE"] = self.tmp
        self.addCleanup(self._restore_env)
        # a fully healthy box, then each test breaks one thing
        self.runs = {
            ("ip", "route", "show", "default"): "default via 10.158.216.1 dev wlan0",
            ("systemctl", "is-enabled", "carwatch-brain"): "enabled",
            ("systemctl", "is-active", "carwatch-presence"): "active",
            ("systemctl", "is-active", "carwatch-chat"): "active",
            ("tailscale", "ip", "-4"): "100.65.0.9",
            ("bluetoothctl", "info", "AA:BB:CC:DD:EE:FF"): "Device AA:BB:CC:DD:EE:FF\n\tPaired: yes\n\tTrusted: yes",
        }
        self.http = {pf.INTERNET_PROBE: 204, "http://100.97.140.13:8123/api/": 401}
        self._patches = [
            mock.patch.object(pf, "_run", lambda cmd, timeout=5.0: self.runs.get(tuple(cmd))),
            mock.patch.object(pf, "_http_status", lambda url, timeout=4.0: self.http.get(url)),
            mock.patch("carwatch.trips.current_ssid", lambda: "Petrus's S26 Ultra"),
            mock.patch("carwatch.brain.model_url", lambda now=None: "http://127.0.0.1:8080/v1/chat/completions"),
            mock.patch("carwatch.brain._healthy", lambda url, timeout=2.0: True),
            mock.patch.object(pf, "_local_state", lambda: "down"),
            mock.patch("carwatch.mercedesme._ha_url", lambda: "http://100.97.140.13:8123"),
            mock.patch("carwatch.mercedesme._TOKEN_FILE", os.path.join(self.tmp, "ha-token")),
            mock.patch.object(pf, "_adapter_present", lambda: None),
            mock.patch.object(pf, "_obd_mac", lambda: "AA:BB:CC:DD:EE:FF"),
            mock.patch.object(pf, "_ha_auth", lambda token: "ok"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self._snap("obd-all.json", 10)
        self._snap("cloud-last.json", 60)
        with open(os.path.join(self.tmp, "ha-token"), "w") as f:
            f.write("x")

    def _restore_env(self):
        if self._env is None:
            os.environ.pop("CARWATCH_STATE", None)
        else:
            os.environ["CARWATCH_STATE"] = self._env

    def _snap(self, name, age_s, payload=None):
        """A REAL snapshot by default: obd carries a numeric reading, cloud
        carries ok:true + a car + fetched_at. payload overrides for the
        negative cases."""
        if payload is None:
            if name == "obd-all.json":
                payload = {"ts": time.time() - age_s, "readings": {"rpm": 800}}
            else:
                payload = {"ok": True, "cars": {"isk-579": {"lock": "locked"}},
                           "fetched_at": time.time() - age_s}
        with open(os.path.join(self.tmp, name), "w") as f:
            if isinstance(payload, str):
                f.write(payload)
            else:
                json.dump(payload, f)

    def _tile(self, res, name):
        return next(t for t in res["tiles"] if t["tile"] == name)

    def test_all_ready(self):
        res = pf.run()
        self.assertTrue(res["ready"], res)
        self.assertEqual(res["summary"], "READY")
        self.assertEqual([t["tile"] for t in res["tiles"]],
                         ["network", "dash", "brain", "mercedes", "obd", "presence"])
        self.assertIn("via remote", self._tile(res, "brain")["detail"])

    def test_mercedes_names_missing_token_and_unreachable_ha(self):
        os.remove(os.path.join(self.tmp, "ha-token"))
        self.http.pop("http://100.97.140.13:8123/api/")
        self.runs.pop(("tailscale", "ip", "-4"))
        res = pf.run()
        t = self._tile(res, "mercedes")
        self.assertFalse(t["ok"])
        self.assertIn("no HA token", t["detail"])
        self.assertEqual(res["summary"], "READY EXCEPT: mercedes")

    def test_mercedes_token_rejected_is_not_ready(self):
        with mock.patch.object(pf, "_ha_auth", lambda token: "rejected"):
            t = self._tile(pf.run(), "mercedes")
        self.assertFalse(t["ok"]); self.assertIn("rejected the token", t["detail"])

    def test_mercedes_unreachable_names_tailnet_and_internet(self):
        self.runs.pop(("tailscale", "ip", "-4"))
        self.http.pop(pf.INTERNET_PROBE)
        with mock.patch.object(pf, "_ha_auth", lambda token: "unreachable"):
            t = self._tile(pf.run(), "mercedes")
        self.assertFalse(t["ok"])
        self.assertIn("unreachable and not on the tailnet, no internet", t["detail"])

    def test_mercedes_needs_real_cloud_data_not_a_file(self):
        cases = {
            "missing": None,
            "malformed": "{not json",
            "ok false": {"ok": False, "error": "not connected yet", "fetched_at": time.time()},
            "no cars": {"ok": True, "cars": {}, "fetched_at": time.time()},
            "label-only car (fully unavailable vehicle)": {"ok": True, "cars": {"car": {"label": "car", "slug": "car"}}, "fetched_at": time.time()},
            "stale:true last-known": {"ok": True, "stale": True, "cars": {"x": {"lock": {"locked": True}}}, "fetched_at": time.time()},
            # usable car data in both, so ONLY the timestamp can be the reason (codexmb)
            "future ts": {"ok": True, "cars": {"x": {"lock": {"locked": True}}}, "fetched_at": time.time() + 3600},
            "stale": {"ok": True, "cars": {"x": {"lock": {"locked": True}}}, "fetched_at": time.time() - 7200},
        }
        for label, payload in cases.items():
            path = os.path.join(self.tmp, "cloud-last.json")
            if payload is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                self._snap("cloud-last.json", 0, payload)
            t = self._tile(pf.run(), "mercedes")
            self.assertFalse(t["ok"], f"{label}: {t}")
            expect = "stale" if label == "stale" else "no valid cloud data"
            self.assertIn(expect, t["detail"], f"{label}: {t}")
            if label in ("future ts", "stale"):
                # a control with the same car and a fresh timestamp must pass,
                # proving the failure above came from the timestamp alone
                self._snap("cloud-last.json", 30, {**payload, "fetched_at": time.time() - 30})
                self.assertTrue(self._tile(pf.run(), "mercedes")["ok"], f"{label} control")

    def test_obd_file_that_is_not_a_reading_is_not_a_reading(self):
        for payload in ("", "{bad", {"ts": time.time()}, {"ts": time.time(), "readings": {}},
                        {"ts": time.time() + 3600, "readings": {"rpm": 1}}, {"ok": False, "error": "x"}):
            self._snap("obd-all.json", 0, payload)
            t = self._tile(pf.run(), "obd")
            self.assertIn("no valid reading yet", t["detail"], repr(payload))
            self.assertNotIn("dongle answering", t["detail"], repr(payload))
            self.assertEqual(t["status"], "unverified", repr(payload))

    def test_obd_fresh_reading_is_ready_on_any_path(self):
        # Bluetooth dongle, no USB node, fresh valid reading: it answered.
        t = self._tile(pf.run(), "obd")
        self.assertTrue(t["ok"], t); self.assertIn("dongle answering", t["detail"])

    def test_obd_paired_but_absent_is_unverified_not_ready(self):
        # This morning's case: paired last week, on the kitchen table today, no reading.
        os.remove(os.path.join(self.tmp, "obd-all.json"))
        res = pf.run()
        t = self._tile(res, "obd")
        self.assertEqual(t["status"], "unverified"); self.assertFalse(t["ok"])
        self.assertIn("unverified until a reading", t["detail"])
        self.assertFalse(res["ready"])
        self.assertEqual(res["summary"], "READY except unverified until ignition: obd")

    def test_obd_stale_reading_is_unverified(self):
        self._snap("obd-all.json", 3600)
        t = self._tile(pf.run(), "obd")
        self.assertEqual(t["status"], "unverified"); self.assertIn("car off?", t["detail"])

    def test_obd_usb_node_alone_is_unverified_not_ready(self):
        # A /dev/ttyUSB0 is any serial device until it answers as OBD.
        os.remove(os.path.join(self.tmp, "obd-all.json"))
        with mock.patch.object(pf, "_adapter_present", lambda: "/dev/ttyUSB0"), \
             mock.patch.object(pf, "_obd_mac", lambda: ""):
            t = self._tile(pf.run(), "obd")
        self.assertEqual(t["status"], "unverified"); self.assertIn("not identified as OBD", t["detail"])

    def test_obd_rfcomm_bound_is_unverified_too(self):
        os.remove(os.path.join(self.tmp, "obd-all.json"))
        with mock.patch.object(pf, "_adapter_present", lambda: "/dev/rfcomm0"):
            t = self._tile(pf.run(), "obd")
        self.assertEqual(t["status"], "unverified"); self.assertIn("rfcomm bound", t["detail"])

    def test_obd_not_ready_when_no_dongle_configured_or_paired(self):
        os.remove(os.path.join(self.tmp, "obd-all.json"))
        with mock.patch.object(pf, "_adapter_present", lambda: None), \
             mock.patch.object(pf, "_obd_mac", lambda: ""):
            t = self._tile(pf.run(), "obd")
        self.assertEqual(t["status"], "not ready"); self.assertIn("no OBD dongle configured", t["detail"])
        self.runs[("bluetoothctl", "info", "AA:BB:CC:DD:EE:FF")] = "Device AA:BB:CC:DD:EE:FF\n\tPaired: no"
        with mock.patch.object(pf, "_adapter_present", lambda: None):
            t = self._tile(pf.run(), "obd")
        self.assertEqual(t["status"], "not ready"); self.assertIn("not paired", t["detail"])

    def test_offline_car_is_still_ready_and_mercedes_says_no_internet(self):
        self.http.pop(pf.INTERNET_PROBE)                 # no internet
        with mock.patch.object(pf, "_ha_auth", lambda token: "unreachable"):  # so HA is too
            res = pf.run()
        self.assertTrue(self._tile(res, "network")["ok"])
        self.assertIn("no internet", self._tile(res, "network")["detail"])
        m = self._tile(res, "mercedes")
        self.assertFalse(m["ok"]); self.assertIn("no internet", m["detail"])
        self.assertEqual(res["summary"], "READY EXCEPT: mercedes")

    def test_brain_down_names_both_sides(self):
        with mock.patch("carwatch.brain._healthy", lambda url, timeout=2.0: False):
            t = self._tile(pf.run(), "brain")
        self.assertFalse(t["ok"])
        self.assertIn("no brain answers", t["detail"])
        self.assertIn("local unit down", t["detail"])
        self.assertIn("is-enabled enabled", t["detail"])

    def test_presence_inactive_and_summary_lists_every_bad_tile(self):
        self.runs[("systemctl", "is-active", "carwatch-presence")] = "inactive"
        os.remove(os.path.join(self.tmp, "obd-all.json"))
        with mock.patch.object(pf, "_obd_mac", lambda: ""):
            res = pf.run()
        self.assertFalse(res["ready"])
        self.assertEqual(res["summary"], "READY EXCEPT: obd, presence")

    def test_summary_separates_failures_from_unverified(self):
        self.runs[("systemctl", "is-active", "carwatch-presence")] = "inactive"
        os.remove(os.path.join(self.tmp, "obd-all.json"))   # obd -> unverified (paired, no reading)
        res = pf.run()
        self.assertEqual(res["summary"], "READY EXCEPT: presence; unverified until ignition: obd")

    def test_a_crashing_check_does_not_hide_the_others(self):
        def boom():
            raise RuntimeError("probe exploded")
        with mock.patch.object(pf, "CHECKS", [boom, pf.check_presence]):
            res = pf.run()
        self.assertEqual(res["tiles"][0]["detail"], "check failed: probe exploded")
        self.assertTrue(res["tiles"][1]["ok"])

    def test_text_format_and_exit_code(self):
        txt = pf.format_text(pf.run())
        self.assertIn("READY", txt.splitlines()[-1])
        self.assertEqual(pf.main([]), 0)
        self.runs[("systemctl", "is-active", "carwatch-chat")] = "inactive"
        self.assertEqual(pf.main(["--json"]), 1)


if __name__ == "__main__":
    unittest.main()
