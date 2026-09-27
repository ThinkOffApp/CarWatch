"""current_ssid on boxes without wireless-tools (VTA-439, 27 Sep 2026): the
dash asked for a password on the home wifi because iwgetid did not exist.
subprocess and /sys are mocked; nothing touches real networking."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from carwatch import trips  # noqa: E402

IW_LINK = ("Connected to 3c:7c:3f:34:62:44 (on wlp98s0)\n"
           "\tSSID: wifi router\n\tfreq: 5180.0\n")


def fake_run(iwgetid=None, iw=IW_LINK):
    def run(cmd, **kw):
        if cmd[0] == "iwgetid":
            if iwgetid is None:
                raise FileNotFoundError("iwgetid")
            return mock.Mock(stdout=iwgetid)
        if cmd[0] == "iw":
            return mock.Mock(stdout=iw)
        raise AssertionError(cmd)
    return run


class CurrentSsid(unittest.TestCase):
    def _call(self, **kw):
        with mock.patch.object(trips.subprocess, "run", side_effect=fake_run(**kw)), \
                mock.patch.object(trips.os, "listdir", return_value=["lo", "wlp98s0"]), \
                mock.patch.object(trips.os.path, "isdir",
                                  side_effect=lambda p: p.endswith("wlp98s0/wireless")):
            return trips.current_ssid()

    def test_iwgetid_wins_when_present(self):
        self.assertEqual(self._call(iwgetid="Pi Home\n"), "Pi Home")

    def test_falls_back_to_iw_when_iwgetid_missing(self):
        self.assertEqual(self._call(iwgetid=None), "wifi router")

    def test_falls_back_to_iw_when_iwgetid_empty(self):
        self.assertEqual(self._call(iwgetid=""), "wifi router")

    def test_not_connected_is_none(self):
        self.assertIsNone(self._call(iwgetid=None, iw="Not connected.\n"))


if __name__ == "__main__":
    unittest.main()
