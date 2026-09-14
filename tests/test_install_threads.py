"""The BRAIN_THREADS block of install.sh, run for real under the script's own
`set -euo pipefail`, with lscpu/nproc/sudo stubbed on PATH. codexmb's review
of #42: a numeric simulation outside set -e proved nothing about the
failure modes that matter (lscpu missing, failing, or empty)."""

import os
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _block() -> str:
    """Extract the 5b block from install.sh so the test runs the shipped
    text, not a copy that can drift."""
    with open(os.path.join(ROOT, "install.sh")) as f:
        src = f.read()
    m = re.search(r"\n(# 5b\).*?\nfi\n)", src, re.S)
    assert m, "5b block not found in install.sh"
    return m.group(1)


class TestInstallBrainThreads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.home)
        os.makedirs(self.bin)
        self._stub("sudo", 'shift 0; exec "$@"')  # run the command as-is

    def _stub(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def _run(self):
        script = "set -euo pipefail\nHOME_DIR=%r\nRUN_USER=%r\n%s" % (
            self.home, os.environ.get("USER", "u"), _block())
        env = dict(os.environ, PATH=self.bin + ":" + os.environ["PATH"])
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, env=env)
        return r

    def _threads(self):
        with open(os.path.join(self.home, ".config/carwatch/brain.env")) as f:
            return f.read()

    def _lscpu(self, cores, smt=2):
        lines = ["# Core,Socket"]
        for c in range(cores):
            lines += ["%d,0" % c] * smt
        self._stub("lscpu", "printf '%s\\n' " + " ".join("'%s'" % l for l in lines))

    def test_physical_cores_not_logical(self):
        self._lscpu(12, smt=2)          # 12c/24t Ryzen
        self._stub("nproc", "echo 24")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._threads(), "BRAIN_THREADS=10\n")

    def test_lscpu_missing_falls_back_to_nproc(self):
        # no lscpu stub, and hide the real one
        self._stub("lscpu", "exit 127")
        self._stub("nproc", "echo 4")
        r = self._run()
        self.assertEqual(r.returncode, 0, "installer died instead of falling back: " + r.stderr)
        self.assertEqual(self._threads(), "BRAIN_THREADS=4\n")

    def test_lscpu_failing_falls_back(self):
        self._stub("lscpu", "echo boom >&2; exit 1")
        self._stub("nproc", "echo 8")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._threads(), "BRAIN_THREADS=6\n")

    def test_lscpu_empty_falls_back(self):
        self._stub("lscpu", "printf '# Core,Socket\\n'")
        self._stub("nproc", "echo 6")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._threads(), "BRAIN_THREADS=4\n")

    def test_existing_value_never_overwritten(self):
        d = os.path.join(self.home, ".config/carwatch")
        os.makedirs(d)
        with open(os.path.join(d, "brain.env"), "w") as f:
            f.write("BRAIN_MODEL=/x.gguf\nBRAIN_THREADS=3\n")
        self._lscpu(12)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._threads(), "BRAIN_MODEL=/x.gguf\nBRAIN_THREADS=3\n")


if __name__ == "__main__":
    unittest.main()
