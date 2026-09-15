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
                   CARWATCH_KIOSK_TRIES="3", CARWATCH_KIOSK_SLEEP="0",
                   CARWATCH_CAGE=str(binp / "cage"), CARWATCH_CHROMIUM="/usr/bin/chromium")
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
                   CARWATCH_KIOSK_TRIES="1", CARWATCH_KIOSK_SLEEP="0",
                   CARWATCH_CAGE=str(binp / "cage"), CARWATCH_CHROMIUM="/snap/bin/chromium")
        res = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(f"--user-data-dir={home}/snap/chromium/common/carwatch-kiosk", cage_log.read_text())


if __name__ == "__main__":
    unittest.main()
