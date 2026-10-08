# SPDX-License-Identifier: Apache-2.0
"""luma-greenboot-common.sh: enforce only on trial boots, and say loudly when it cannot tell.

The helper used to return "not a trial boot" silently when grub2-editenv or
the GRUB environment was missing, so on such a machine every health check
passed and a failed update could never roll back, with nothing in the journal.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / "data/greenboot/luma-greenboot-common.sh"


@unittest.skipIf(shutil.which("bash") is None or shutil.which("grep") is None, "needs bash and grep")
class TrialBoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        os.symlink(shutil.which("grep"), self.bin / "grep")
        self.logged = self.root / "logger.out"
        self.write_tool("logger", f'printf "%s\\n" "$*" >> "{self.logged}"\n')
        self.grubenv = self.root / "grubenv"

    def tearDown(self):
        self.tmp.cleanup()

    def write_tool(self, name, body):
        path = self.bin / name
        path.write_text("#!" + shutil.which("bash") + "\n" + body)
        path.chmod(0o755)

    def run_check(self, failing=True):
        script = f'. "{HELPER}"\n' + ('luma_check_failed "the shell did not answer"\n' if failing else
                                       'luma_trial_boot && echo trial || echo ordinary\n')
        env = {"PATH": str(self.bin), "LUMA_GREENBOOT_GRUBENV": str(self.grubenv)}
        return subprocess.run([shutil.which("bash"), "-c", script], env=env, capture_output=True, text=True,
                              timeout=30)

    def logger_lines(self):
        return self.logged.read_text() if self.logged.exists() else ""

    def fake_editenv(self, output, status=0):
        self.write_tool("grub2-editenv", f'[ "$2" = list ] || exit 2\nprintf "%s\\n" "{output}"\nexit {status}\n')

    def test_trial_boot_enforces(self):
        self.grubenv.write_text("# GRUB Environment Block\n")
        self.fake_editenv("boot_counter=2\ngreenboot_next_deployment_id=1")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("failed on a trial boot", result.stderr)
        self.assertEqual(self.logger_lines(), "")

    def test_ordinary_boot_passes_quietly(self):
        self.grubenv.write_text("# GRUB Environment Block\n")
        self.fake_editenv("boot_success=1")
        result = self.run_check()
        self.assertEqual(result.returncode, 0)
        self.assertIn("not a trial boot, not enforced", result.stderr)
        self.assertEqual(self.logger_lines(), "")

    def test_missing_grub2_editenv_is_an_error_in_the_journal(self):
        self.grubenv.write_text("# GRUB Environment Block\n")
        result = self.run_check(failing=False)
        self.assertEqual(result.stdout.strip(), "ordinary")
        self.assertIn("--priority user.err --tag luma-greenboot", self.logger_lines())
        self.assertIn("grub2-editenv is not installed", self.logger_lines())
        self.assertIn("grub2-editenv is not installed", result.stderr)

    def test_missing_grubenv_is_an_error_in_the_journal(self):
        self.fake_editenv("greenboot_next_deployment_id=1")
        result = self.run_check()
        self.assertEqual(result.returncode, 0)
        self.assertIn("missing or unreadable", self.logger_lines())

    def test_unreadable_environment_is_an_error_in_the_journal(self):
        self.grubenv.write_text("garbage")
        self.fake_editenv("error: invalid environment block", status=1)
        result = self.run_check(failing=False)
        self.assertEqual(result.stdout.strip(), "ordinary")
        self.assertIn("could not read", self.logger_lines())
        self.assertIn("invalid environment block", self.logger_lines())

    def test_without_logger_the_error_still_reaches_stderr(self):
        (self.bin / "logger").unlink()
        result = self.run_check(failing=False)
        self.assertEqual(result.stdout.strip(), "ordinary")
        self.assertIn("luma greenboot: cannot tell whether this is a trial boot", result.stderr)


if __name__ == "__main__":
    unittest.main()
