"""Jabra Speak2 40 heard nothing (petrus, 26 Sep 2026, measured on vadelma).

`arecord -D plughw:0,0 -r 16000 -c 1` failed every time with an ALSA
Input/output error, and the same arecord worked while an aplay of silence
held the card's output open. listen.py opened only arecord, so the loop
reopened the mic every ~3 s forever. The fix keeps a silent aplay on the
same card while a USB mic is open; these tests pin that it starts with the
mic, dies with the mic on every close path (reopen, around _speak,
shutdown), and never appears for Bluetooth HFP or the default device.
subprocess is mocked throughout: nothing touches real audio.
"""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Stop(BaseException):
    """Raised from a fake read to end listen()'s endless loop. BaseException
    because listen() turns any Exception from a read into a reopen."""


class FakeProc:
    def __init__(self, cmd, reads=None):
        self.cmd = cmd
        self.reads = list(reads or [])
        self.terminated = False
        self.killed = False
        self.returncode = None
        self.stdout = self

    # arecord's stdout
    def read(self, n):
        if not self.reads:
            raise _Stop()
        r = self.reads.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode

    @property
    def alive(self):
        return self.returncode is None


class KeepaliveBase(unittest.TestCase):
    USB = "plughw:0,0"

    def setUp(self):
        from carwatch import listen
        self.L = importlib.reload(listen)
        self.addCleanup(setattr, self.L, "_keepalive", None)
        self.procs = []
        self.arecord_reads = []   # one list of reads per arecord opened
        self.usb = self.USB
        self.bt = None

        def popen(cmd, *a, **kw):
            reads = self.arecord_reads.pop(0) if (
                cmd[0] == "arecord" and self.arecord_reads) else []
            p = FakeProc(cmd, reads)
            self.procs.append(p)
            return p

        patches = [
            mock.patch.object(self.L.subprocess, "Popen", side_effect=popen),
            mock.patch.object(self.L.subprocess, "run",
                              return_value=mock.Mock(returncode=0, stdout="")),
            mock.patch.object(self.L.time, "sleep"),
            mock.patch.object(self.L, "_usb_audio_device",
                              side_effect=lambda kind: self.usb),
            mock.patch.object(self.L, "_bt_pcm_mac",
                              side_effect=lambda suffix: self.bt),
            mock.patch.object(self.L.voicestate, "set_state"),
            mock.patch.object(self.L.voicestate, "armed", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("CARWATCH_MIC", None)

    def arecords(self):
        return [p for p in self.procs if p.cmd[0] == "arecord"]

    def keepalives(self):
        return [p for p in self.procs if p.cmd[0] == "aplay"]


class OpenMic(KeepaliveBase):

    def test_usb_starts_silent_output_on_same_card_before_arecord(self):
        self.L._open_mic()
        self.assertEqual([p.cmd[0] for p in self.procs], ["aplay", "arecord"])
        ka = self.keepalives()[0]
        self.assertEqual(ka.cmd[ka.cmd.index("-D") + 1], self.USB)
        self.assertEqual(ka.cmd[ka.cmd.index("-f") + 1], "S16_LE")
        self.assertEqual(ka.cmd[ka.cmd.index("-r") + 1], "16000")
        self.assertEqual(ka.cmd[ka.cmd.index("-c") + 1], "2")
        self.assertEqual(ka.cmd[-1], "/dev/zero")
        ar = self.arecords()[0]
        self.assertEqual(ar.cmd[ar.cmd.index("-D") + 1], self.USB)
        self.assertIs(self.L._keepalive, ka)

    def test_second_open_does_not_leak_first_keepalive(self):
        self.L._open_mic()
        self.L._open_mic()
        first, second = self.keepalives()
        self.assertFalse(first.alive)
        self.assertTrue(second.alive)

    def test_bluetooth_hfp_gets_no_keepalive(self):
        self.usb = None
        self.bt = "AA:BB:CC:DD:EE:FF"
        self.L._open_mic()
        self.assertEqual(self.keepalives(), [])
        ar = self.arecords()[0]
        self.assertIn("bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=sco", ar.cmd)
        self.assertIsNone(self.L._keepalive)

    def test_bt_preference_with_usb_attached_gets_no_keepalive(self):
        self.bt = "AA:BB:CC:DD:EE:FF"
        os.environ["CARWATCH_MIC"] = "bt"
        self.addCleanup(os.environ.pop, "CARWATCH_MIC", None)
        self.L._open_mic()
        self.assertEqual(self.keepalives(), [])

    def test_default_device_gets_no_keepalive(self):
        self.usb = None
        self.L._open_mic()
        self.assertEqual(self.keepalives(), [])
        self.assertNotIn("-D", self.arecords()[0].cmd)

    def test_mic_only_card_keepalive_exit_is_harmless(self):
        # SF-558-style card with no playback: aplay dies at once; the mic
        # still opens and no dead handle is kept around.
        orig = self.L.subprocess.Popen.side_effect

        def popen(cmd, *a, **kw):
            p = orig(cmd, *a, **kw)
            if cmd[0] == "aplay":
                p.returncode = 1
            return p
        self.L.subprocess.Popen.side_effect = popen
        self.L._open_mic()
        self.assertEqual(len(self.arecords()), 1)
        self.assertIsNone(self.L._keepalive)


class ListenLoop(KeepaliveBase):
    LOUD = (b"\xff\x7f" * (1600))    # int16 max, well above threshold
    QUIET = (b"\x00\x00" * (1600))

    def run_listen(self):
        with self.assertRaises(_Stop):
            self.L.listen(700, lambda t: None)

    def test_reopen_stops_old_keepalive(self):
        # First arecord dies instantly (the Jabra I/O error), second is read
        # until the test ends the loop.
        self.arecord_reads = [[b""], []]
        self.run_listen()
        kas = self.keepalives()
        self.assertEqual(len(kas), 2)
        self.assertFalse(kas[0].alive, "keepalive leaked across the reopen")
        self.assertTrue(self.arecords()[0].terminated)

    def test_shutdown_stops_keepalive(self):
        self.arecord_reads = [[]]
        self.run_listen()
        self.assertTrue(all(not k.alive for k in self.keepalives()))
        self.assertTrue(all(not a.alive for a in self.arecords()))
        self.assertIsNone(self.L._keepalive)

    def test_no_keepalive_while_speaking_and_back_after(self):
        seen = {}

        def speak(text):
            seen["arecord_alive"] = [a.alive for a in self.arecords()]
            seen["keepalive_alive"] = [k.alive for k in self.keepalives()]
            return True

        self.arecord_reads = [[self.LOUD] * 4 + [self.QUIET] * 5, []]
        with mock.patch.object(self.L, "_speak", side_effect=speak), \
             mock.patch.object(self.L, "handle_utterance",
                               return_value="reply"):
            self.run_listen()
        self.assertEqual(seen["arecord_alive"], [False])
        self.assertEqual(seen["keepalive_alive"], [False],
                         "keepalive held the Jabra output during _speak")
        # Mic and keepalive came back after the reply.
        self.assertEqual(len(self.keepalives()), 2)
        self.assertEqual(len(self.arecords()), 2)

    def test_bluetooth_loop_never_starts_aplay(self):
        self.usb = None
        self.bt = "AA:BB:CC:DD:EE:FF"
        self.arecord_reads = [[b""], []]
        self.run_listen()
        self.assertEqual(self.keepalives(), [])
        self.assertEqual(len(self.arecords()), 2)


class StopKeepalive(KeepaliveBase):

    def test_kill_when_terminate_does_not_stick(self):
        self.L._start_keepalive(self.USB)
        ka = self.L._keepalive

        def stuck(timeout=None):
            if not ka.killed:
                raise self.L.subprocess.TimeoutExpired("aplay", timeout)
            return -9
        ka.wait = stuck
        self.L._stop_keepalive()
        self.assertTrue(ka.killed)
        self.assertIsNone(self.L._keepalive)

    def test_stop_without_keepalive_is_noop(self):
        self.L._stop_keepalive()
        self.assertEqual(self.procs, [])


if __name__ == "__main__":
    unittest.main()
