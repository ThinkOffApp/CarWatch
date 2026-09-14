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

    def _snap(self, name, age_s):
        with open(os.path.join(self.tmp, name), "w") as f:
            json.dump({"ts": time.time() - age_s}, f)

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
        self.assertIn("unreachable and not on the tailnet", t["detail"])
        self.assertEqual(res["summary"], "READY EXCEPT: mercedes")

    def test_obd_pre_drive_is_about_pairing_not_freshness(self):
        # Paired dongle, no snapshot yet (car off): READY, freshness is info.
        os.remove(os.path.join(self.tmp, "obd-all.json"))
        t = self._tile(pf.run(), "obd")
        self.assertTrue(t["ok"], t); self.assertIn("paired", t["detail"]); self.assertIn("no snapshot yet", t["detail"])
        # Stale snapshot with a paired dongle: still READY, says car off?
        self._snap("obd-all.json", 3600)
        t = self._tile(pf.run(), "obd")
        self.assertTrue(t["ok"], t); self.assertIn("car off?", t["detail"])

    def test_obd_not_ready_when_no_dongle_configured_or_paired(self):
        with mock.patch.object(pf, "_obd_mac", lambda: ""):
            t = self._tile(pf.run(), "obd")
        self.assertFalse(t["ok"]); self.assertIn("no OBD dongle configured", t["detail"])
        self.runs[("bluetoothctl", "info", "AA:BB:CC:DD:EE:FF")] = "Device AA:BB:CC:DD:EE:FF\n\tPaired: no"
        t = self._tile(pf.run(), "obd")
        self.assertFalse(t["ok"]); self.assertIn("not paired", t["detail"])

    def test_obd_ready_when_an_adapter_path_exists(self):
        with mock.patch.object(pf, "_adapter_present", lambda: "/dev/rfcomm0"), \
             mock.patch.object(pf, "_obd_mac", lambda: ""):
            t = self._tile(pf.run(), "obd")
        self.assertTrue(t["ok"]); self.assertIn("/dev/rfcomm0", t["detail"])

    def test_offline_car_is_still_ready_and_mercedes_says_no_internet(self):
        self.http.pop(pf.INTERNET_PROBE)                 # no internet
        self.http.pop("http://100.97.140.13:8123/api/")  # so HA is unreachable too
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
        with mock.patch.object(pf, "_obd_mac", lambda: ""):
            res = pf.run()
        self.assertFalse(res["ready"])
        self.assertEqual(res["summary"], "READY EXCEPT: obd, presence")

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
