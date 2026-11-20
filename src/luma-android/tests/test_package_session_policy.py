import contextlib
import io
from pathlib import Path
import re
import runpy
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = "/usr/libexec/luma-waydroid-package-session"
helper = runpy.run_path(str(ROOT / "bin/luma-waydroid-package-session"))
helper_globals = helper["main"].__globals__
RULES = (ROOT / "data/luma-waydroid-package-session.rules").read_text()


class _Account:
    pw_name = "someone"
    pw_gid = 1000
    pw_dir = "/home/someone"


def run_helper(arguments, groups):
    """Run the helper as a caller in GROUPS; return (exit status, Android commands)."""
    commands = []

    def check_output(command, **_kwargs):
        commands.append(command)
        if command[0] == "/usr/bin/lxc-info":
            return "RUNNING\n"
        if "show_ime_with_hard_keyboard" in command:
            return "0\n" if "desktop" in arguments else "1\n"
        return ""

    def run(command, **_kwargs):
        commands.append(command)
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.dict(helper_globals["os"].environ, {"PKEXEC_UID": "1000"}), \
            mock.patch.object(helper_globals["os"], "getgrouplist", return_value=groups), \
            mock.patch.object(helper_globals["pwd"], "getpwuid", return_value=_Account()), \
            mock.patch.object(helper_globals["grp"], "getgrnam", return_value=mock.Mock(gr_gid=10)), \
            mock.patch.object(helper_globals["subprocess"], "check_output", side_effect=check_output), \
            mock.patch.object(helper_globals["subprocess"], "run", side_effect=run), \
            mock.patch("sys.argv", ["helper", *arguments]), \
            contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            status = helper["main"]()
        except SystemExit as exit:
            status = exit.code
    return status, [command for command in commands if command[0] == "/usr/bin/lxc-attach"]


class SessionRequests(unittest.TestCase):
    def test_rule_admits_exactly_the_helper_session_requests(self):
        self.assertIn(f'"{HELPER_PATH}"', RULES)
        block = re.search(r"sessionRequests = \[(.*?)\]", RULES, re.S).group(1)
        admitted = {tuple(line.split()) for line in re.findall(r'"([^"]+)"', block)}
        self.assertEqual(admitted, set(helper["SESSION_REQUESTS"]))

    def test_rule_keeps_everything_else_for_administrators(self):
        self.assertIn('subject.isInGroup("wheel")', RULES)
        self.assertIn("!subject.active || !subject.local", RULES)
        self.assertIn('action.lookup("command_line") == helper + " " + sessionRequests[i]', RULES)

    def test_non_administrator_session_upkeep_reaches_android(self):
        for arguments in (["input-policy", "desktop"], ["input-policy", "handheld"],
                          ["boot-from-charger-mode"]):
            with self.subTest(arguments=arguments):
                status, commands = run_helper(arguments, groups=[1000])
                self.assertEqual(status, 0)
                self.assertTrue(commands)

    def test_non_administrator_cannot_manage_packages(self):
        for arguments in (["installed-packages"], ["device-profile"],
                          ["remove-package", "org.example.App", "keep-data"],
                          ["pm", "install", "--user", "0", "-r",
                           "/data/waydroid_tmp/luma-" + "0" * 32 + "-0000.apk"]):
            with self.subTest(arguments=arguments):
                status, commands = run_helper(arguments, groups=[1000])
                self.assertEqual(status, 2)
                self.assertEqual(commands, [])

    def test_session_requests_take_no_other_arguments(self):
        for arguments in (["input-policy"], ["input-policy", "desktop", "extra"],
                          ["input-policy", "phone"], ["boot-from-charger-mode", "1"]):
            with self.subTest(arguments=arguments):
                status, commands = run_helper(arguments, groups=[1000, 10])
                self.assertEqual(status, 2)
                self.assertEqual(commands, [])

    def test_administrator_keeps_package_queries(self):
        status, commands = run_helper(["installed-packages"], groups=[1000, 10])
        self.assertEqual(status, 0)
        self.assertEqual(commands[0][-5:], ["/system/bin/pm", "list", "packages", "--user", "0"])


if __name__ == "__main__":
    unittest.main()
