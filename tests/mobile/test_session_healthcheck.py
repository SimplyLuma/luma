# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("session_health", ROOT / "scripts/mobile/session-healthcheck.py")
HEALTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HEALTH)


class SessionHealthTests(unittest.TestCase):
    def runner(self, manager, active=True, processes=(), required=0):
        def run(command, **_kwargs):
            if command[:2] == ["systemctl", "show"]:
                return subprocess.CompletedProcess(command, 0, manager + "\n")
            if command[:3] == ["systemctl", "-q", "is-active"]:
                code = 0 if active else 3
            elif command[0] == "pgrep":
                code = 0 if command[-1] in processes else 1
            elif command[0] == "bash":
                code = required
            else:
                raise AssertionError(command)
            return subprocess.CompletedProcess(command, code)
        return run

    def test_gdm_greeter_is_a_healthy_graphical_boot(self):
        self.assertTrue(HEALTH.graphical_session(self.runner("gdm.service", processes=("gnome-shell",))))

    def test_phosh_session_still_supported(self):
        self.assertTrue(HEALTH.graphical_session(self.runner("greetd.service", processes=("phoc", "phosh"))))

    def test_inactive_manager_does_not_accept_stale_shell(self):
        self.assertFalse(HEALTH.graphical_session(self.runner("gdm.service", False, ("gnome-shell",))))

    def test_unrelated_shell_does_not_bless_selected_session(self):
        self.assertFalse(HEALTH.graphical_session(self.runner("gdm.service", processes=("phosh", "phoc"))))
        self.assertFalse(HEALTH.graphical_session(self.runner("greetd.service", processes=("gnome-shell",))))

    def test_unknown_manager_fails_closed(self):
        self.assertFalse(HEALTH.check((), 0, self.runner("unknown.service", processes=("gnome-shell",))))

    def test_required_update_failure_remains_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "required.sh").write_text("exit 1\n")
            run = self.runner("gdm.service", processes=("gnome-shell",), required=1)
            self.assertFalse(HEALTH.check((directory,), 0, run))


if __name__ == "__main__":
    unittest.main()
