"""Voice answers were silent on the Jabra (petrus, 26 Sep 2026, vadelma).

~/.carwatch/car-bt-mac held the car's MAC while the car was away, so
_speak aimed every answer at bluealsa A2DP for a head unit that was not
connected and never tried the USB speakerphone. The room gate also dropped
"can you speak using the jabra @eclass" because the handle trailed. These
tests pin both: the car is used only when its A2DP link is live, and the
owner's literal @handle anywhere addresses the car while agents' mentions
still do not. subprocess and piper are mocked: nothing touches real audio.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAR = "88:5A:85:66:67:C2"


class TestSpeakRoute(unittest.TestCase):
    def setUp(self):
        from carwatch import listen
        self.L = listen
        wav = os.path.join(os.path.dirname(__file__), "_speak_route.wav")
        open(wav, "wb").close()
        self.wav = wav
        self.addCleanup(lambda: os.path.exists(wav) and os.unlink(wav))

    def _route(self, a2dp_mac):
        played = []

        def run(cmd, **kw):
            if cmd[0] == "aplay":
                played.append(cmd[cmd.index("-D") + 1])
            return mock.Mock(returncode=0)

        from carwatch import voiceroom
        with mock.patch.object(voiceroom, "tts_wav", return_value=self.wav), \
                mock.patch.object(self.L, "_car_a2dp_mac", return_value=CAR), \
                mock.patch.object(self.L, "_bt_pcm_mac",
                                  side_effect=lambda s: a2dp_mac if s == "a2dpsrc/sink" else None), \
                mock.patch.object(self.L, "_usb_audio_device", return_value="plughw:0,0"), \
                mock.patch.object(self.L, "_echo_tail_sec", return_value=0.0), \
                mock.patch.object(self.L.subprocess, "run", side_effect=run), \
                mock.patch.object(self.L.time, "sleep"):
            self.assertTrue(self.L._speak("hello"))
        return played

    def test_absent_car_falls_back_to_usb(self):
        self.assertEqual(self._route(None), ["plughw:0,0"])

    def test_connect_timeout_still_falls_back_to_usb(self):
        # 26 Sep on vadelma: connect to the absent car hung past 10 s and the
        # TimeoutExpired aborted the whole reply.
        played = []

        def run(cmd, **kw):
            if cmd[0] == "bluetoothctl":
                raise self.L.subprocess.TimeoutExpired(cmd, 10)
            if cmd[0] == "aplay":
                played.append(cmd[cmd.index("-D") + 1])
            return mock.Mock(returncode=0)

        from carwatch import voiceroom
        with mock.patch.object(voiceroom, "tts_wav", return_value=self.wav), \
                mock.patch.object(self.L, "_car_a2dp_mac", return_value=CAR), \
                mock.patch.object(self.L, "_bt_pcm_mac", return_value=None), \
                mock.patch.object(self.L, "_usb_audio_device", return_value="plughw:0,0"), \
                mock.patch.object(self.L, "_echo_tail_sec", return_value=0.0), \
                mock.patch.object(self.L.subprocess, "run", side_effect=run), \
                mock.patch.object(self.L.time, "sleep"):
            self.assertTrue(self.L._speak("hello"))
        self.assertEqual(played, ["plughw:0,0"])

    def test_connected_car_still_wins(self):
        self.assertEqual(self._route(CAR), [f"bluealsa:DEV={CAR},PROFILE=a2dp"])


class TestOwnerTrailingHandle(unittest.TestCase):
    def setUp(self):
        from carwatch import agent
        self.agent = agent

    def test_owner_trailing_handle_is_addressed(self):
        msg = {"from": "petrus", "body": "can you speak using the jabra @eclass"}
        self.assertTrue(self.agent._mentions_me(msg, "@eclass", owner="petrus"))

    def test_agent_trailing_handle_stays_quiet(self):
        msg = {"from": "@claudeMB", "body": "the line @eclass just posted"}
        self.assertFalse(self.agent._mentions_me(msg, "@eclass", owner="petrus"))

    def test_owner_spoken_name_mid_sentence_stays_quiet(self):
        msg = {"from": "petrus", "body": "I parked the eclass outside"}
        self.assertFalse(self.agent._mentions_me(msg, "@eclass", owner="petrus"))


class TestVoiceFact(unittest.TestCase):
    """The car said "I don't have a Jabra speaker" with one plugged in."""

    APLAY = ("**** List of PLAYBACK Hardware Devices ****\n"
             "card 0: MS [Jabra Speak2 40 MS], device 0: USB Audio [USB Audio]\n"
             "  Subdevices: 1/1\n"
             "card 1: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]\n")

    def setUp(self):
        from carwatch import selfstate
        self.S = selfstate

    def test_usb_names_skip_hdmi(self):
        self.assertEqual(self.S._usb_audio_names(self.APLAY), ["Jabra Speak2 40 MS"])

    def _voice(self, bt_info):
        def run(cmd, timeout=5):
            return self.APLAY if cmd[0] == "aplay" else bt_info
        with mock.patch.object(self.S, "_run", side_effect=run), \
                mock.patch("builtins.open", mock.mock_open(read_data=CAR)):
            return self.S.voice()

    def test_car_away_names_the_jabra_as_the_speaker(self):
        v = self._voice("Device x\n\tConnected: no\n")
        self.assertIn("Jabra Speak2 40 MS", v)
        self.assertIn("speak your answers aloud through its speaker", v)
        self.assertIn("Bluetooth audio is not connected", v)

    def test_car_connected_speaks_through_the_car(self):
        v = self._voice("Device x\n\tConnected: yes\n")
        self.assertIn("car's own speakers", v)
        self.assertNotIn("aloud through its speaker", v)

    def test_nothing_plugged_in_says_nothing(self):
        with mock.patch.object(self.S, "_run", return_value=""), \
                mock.patch("builtins.open", side_effect=OSError):
            self.assertIsNone(self.S.voice())
