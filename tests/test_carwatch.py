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


if __name__ == "__main__":
    unittest.main()
