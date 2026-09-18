"""#20: elm327.py swallowed exceptions with no record at all.

hermes's review, 31 Aug. An OBD dongle vanishing mid-drive is normal, so the
handlers are right to continue - but with nothing logged, a decoder bug and a
missing dongle look identical in the field, and a dropped reading shows only
as a blank tile on the dash.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carwatch import elm327  # noqa: E402


class SwallowedTrace(unittest.TestCase):

    def setUp(self):
        elm327._SWALLOWED.clear()

    def test_first_occurrence_is_reported(self):
        err = io.StringIO()
        with redirect_stderr(err):
            elm327._swallowed("decode pid 0x0C", ValueError("bad byte"))
        out = err.getvalue()
        self.assertIn("decode pid 0x0C", out)
        self.assertIn("ValueError", out)
        self.assertIn("bad byte", out)

    def test_repeats_do_not_flood_the_journal(self):
        # A dongle that vanishes "as a rule" must not drown the drive it is
        # being diagnosed on.
        err = io.StringIO()
        with redirect_stderr(err):
            for _ in range(9):
                elm327._swallowed("close", OSError("gone"))
        self.assertEqual(err.getvalue().count("\n"), 1,
                         "repeats should be summarised, not printed each time")

    def test_milestones_still_surface(self):
        err = io.StringIO()
        with redirect_stderr(err):
            for _ in range(10):
                elm327._swallowed("close", OSError("gone"))
        self.assertIn("x10", err.getvalue())

    def test_distinct_sites_and_types_are_tracked_separately(self):
        err = io.StringIO()
        with redirect_stderr(err):
            elm327._swallowed("close", OSError("a"))
            elm327._swallowed("close", ValueError("b"))
            elm327._swallowed("scan_supported_quiet", OSError("c"))
        self.assertEqual(err.getvalue().count("\n"), 3)

    def test_never_writes_to_stdout(self):
        # This module's CLI prints JSON; a stray line would corrupt it.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            elm327._swallowed("close", OSError("gone"))
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(err.getvalue())

    def test_no_bare_swallow_remains_at_the_four_sites(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "carwatch", "elm327.py")).read()
        for marker in ("_swallowed(\"close\"",
                       "_swallowed(f\"decode pid",
                       "_swallowed(f\"decode ext pid",
                       "_swallowed(\"scan_supported_quiet\""):
            self.assertIn(marker, src, f"{marker} lost its trace")


if __name__ == "__main__":
    unittest.main()
