"""#56 / #31: the car posted "adapter asleep or car off" on every boot.

@eclass sent petrus the identical line at 12:58Z and 13:06Z on 15 Sep with the
car parked at home. `no_data_posted` was a local variable, so every boot and
every rfcomm rebind started a fresh process with the flag cleared. The same
file already learned this lesson once - DEEP_STAMP is persisted because the
in-memory `deep_done` reset on rfcomm flaps - and it was not applied here.
"""
import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class NoDataEpochMarker(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from carwatch import obdwatch
        self.ow = importlib.reload(obdwatch)
        self.ow.NO_DATA_STAMP = os.path.join(self.tmp.name, "no-data-posted.stamp")

    def test_marker_survives_a_restart(self):
        # A fresh process must be able to see that the line was already sent.
        self.assertFalse(self.ow._no_data_posted())
        self.ow._set_no_data_posted(True)
        self.assertTrue(self.ow._no_data_posted(),
                        "the epoch marker did not persist; a restart reposts")

    def test_a_successful_reading_clears_the_epoch(self):
        self.ow._set_no_data_posted(True)
        self.ow._set_no_data_posted(False)
        self.assertFalse(self.ow._no_data_posted())

    def test_clearing_an_absent_marker_is_not_an_error(self):
        self.ow._set_no_data_posted(False)          # must not raise
        self.assertFalse(self.ow._no_data_posted())

    def test_bookkeeping_failure_never_raises(self):
        # A read-only or missing state dir must not kill the read loop.
        self.ow.NO_DATA_STAMP = "/proc/definitely/not/writable/stamp"
        self.ow._set_no_data_posted(True)           # must not raise
        self.assertFalse(self.ow._no_data_posted())

    def test_post_is_gated_on_having_seen_data(self):
        # The #56 rule lives in the read loop; assert the source states it, so
        # the gate cannot be dropped silently in a refactor.
        import inspect
        src = inspect.getsource(self.ow.run)
        self.assertIn("and seen_data", src,
                      "the no-data post is no longer gated on a data -> lost "
                      "transition; it will fire on a cold boot again")


if __name__ == "__main__":
    unittest.main()
