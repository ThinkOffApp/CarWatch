"""The kiosk launcher must not start the browser when the dash never answers,
and must start it with the tokenized URL when the dash is up. Runs the real
bash script with stubbed curl and cage on PATH."""
import os, stat, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "kiosk" / "carwatch-kiosk.sh"


def _stub(dirpath: Path, name: str, body: str) -> None:
    p = dirpath / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _drm(tmp: Path, status: str = "connected") -> Path:
    """A fake /sys/class/drm with one HDMI connector reporting [status]."""
    d = tmp / "drm" / "card0-HDMI-A-1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(status + "\n")
    return tmp / "drm"


class KioskLauncherTest(unittest.TestCase):
    def _run(self, curl_ok: bool):
        tmp = Path(tempfile.mkdtemp())
        home = tmp / "home"; (home / ".carwatch").mkdir(parents=True)
        (home / ".carwatch" / "dash-token").write_text("tok123\n")
        binp = tmp / "bin"; binp.mkdir()
        _stub(binp, "curl", "exit 0" if curl_ok else "exit 22")
        cage_log = tmp / "cage.log"
        _stub(binp, "cage", f'echo "$@" > "{cage_log}"; exit 0')
        env = dict(os.environ, HOME=str(home), PATH=f"{binp}:{os.environ['PATH']}",
                   CARWATCH_KIOSK_TRIES="3", CARWATCH_KIOSK_SLEEP="0", CARWATCH_PANEL_POLL="1",
                   CARWATCH_CAGE=str(binp / "cage"), CARWATCH_CHROMIUM="/usr/bin/chromium",
                   CARWATCH_DRM_DIR=str(_drm(tmp)))
        res = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        return res, cage_log, home

    def test_no_browser_when_dash_never_answers(self):
        res, cage_log, _ = self._run(curl_ok=False)
        self.assertNotEqual(res.returncode, 0, res.stderr)
        self.assertFalse(cage_log.exists(), "cage/chromium was started although the dash never answered")
        self.assertIn("not starting the browser", res.stderr)

    def test_browser_started_with_token_when_dash_is_up(self):
        res, cage_log, home = self._run(curl_ok=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        args = cage_log.read_text()
        self.assertIn("--kiosk", args)
        self.assertIn("http://127.0.0.1:8088/dash?t=tok123", args)
        self.assertIn(f"--user-data-dir={home}/.carwatch/kiosk-profile", args)

    def test_snap_chromium_uses_snap_writable_profile(self):
        tmp = Path(tempfile.mkdtemp()); home = tmp / "home"; (home / ".carwatch").mkdir(parents=True)
        binp = tmp / "bin"; binp.mkdir(); _stub(binp, "curl", "exit 0")
        cage_log = tmp / "cage.log"; _stub(binp, "cage", f'echo "$@" > "{cage_log}"; exit 0')
        env = dict(os.environ, HOME=str(home), PATH=f"{binp}:{os.environ['PATH']}",
                   CARWATCH_KIOSK_TRIES="1", CARWATCH_KIOSK_SLEEP="0", CARWATCH_PANEL_POLL="1",
                   CARWATCH_CAGE=str(binp / "cage"), CARWATCH_CHROMIUM="/snap/bin/chromium",
                   CARWATCH_DRM_DIR=str(_drm(tmp)))
        res = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(f"--user-data-dir={home}/snap/chromium/common/carwatch-kiosk", cage_log.read_text())

    # petrus, 15 Sep 2026: the VTA's panel had moved to the Pi and the kiosk
    # kept drawing the dash for nobody (one core, 80 C, four hours).
    def test_no_panel_means_no_browser_and_exit_78(self):
        tmp = Path(tempfile.mkdtemp()); home = tmp / "home"; (home / ".carwatch").mkdir(parents=True)
        binp = tmp / "bin"; binp.mkdir(); _stub(binp, "curl", "exit 0")
        cage_log = tmp / "cage.log"; _stub(binp, "cage", f'echo "$@" > "{cage_log}"; exit 0')
        env = dict(os.environ, HOME=str(home), PATH=f"{binp}:{os.environ['PATH']}",
                   CARWATCH_KIOSK_TRIES="1", CARWATCH_KIOSK_SLEEP="0", CARWATCH_PANEL_POLL="1",
                   CARWATCH_CAGE=str(binp / "cage"), CARWATCH_CHROMIUM="/usr/bin/chromium",
                   CARWATCH_DRM_DIR=str(_drm(tmp, "disconnected")))
        res = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 78, res.stderr)
        self.assertFalse(cage_log.exists(), "browser started with no panel connected")
        self.assertIn("no display connected", res.stderr)

    def test_browser_stopped_when_panel_unplugged(self):
        tmp = Path(tempfile.mkdtemp()); home = tmp / "home"; (home / ".carwatch").mkdir(parents=True)
        binp = tmp / "bin"; binp.mkdir(); _stub(binp, "curl", "exit 0")
        started = tmp / "started"
        # a browser that runs until killed
        _stub(binp, "cage", f'touch "{started}"; trap "exit 0" TERM; while :; do sleep 1; done')
        drm = _drm(tmp, "connected")
        env = dict(os.environ, HOME=str(home), PATH=f"{binp}:{os.environ['PATH']}",
                   CARWATCH_KIOSK_TRIES="1", CARWATCH_KIOSK_SLEEP="0", CARWATCH_PANEL_POLL="1",
                   CARWATCH_CAGE=str(binp / "cage"), CARWATCH_CHROMIUM="/usr/bin/chromium",
                   CARWATCH_DRM_DIR=str(drm))
        proc = subprocess.Popen(["bash", str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        import time
        for _ in range(50):
            if started.exists(): break
            time.sleep(0.1)
        self.assertTrue(started.exists(), "browser never started with a panel connected")
        (drm / "card0-HDMI-A-1" / "status").write_text("disconnected\n")
        try:
            _, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill(); self.fail("launcher kept running after the panel was unplugged")
        self.assertEqual(proc.returncode, 78, err)
        self.assertIn("display disconnected", err)


if __name__ == "__main__":
    unittest.main()
