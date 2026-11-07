# SPDX-License-Identifier: Apache-2.0
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("LUMA_SETUP_FIXTURE", "1")

from luma_setup import apply  # noqa: E402


class FakeBus:
    def __init__(self, fail: str = "") -> None:
        self.calls = []
        self.fail = fail

    def call(self, name, path, interface, method, signature, args, reply=None):
        self.calls.append((interface, method, args))
        if method == self.fail:
            raise RuntimeError(f"{method} failed")
        if method == "CreateUser":
            return ("/org/freedesktop/Accounts/User1000",)
        return None


ANSWERS = {"full_name": "Nick McMillan", "username": "nick", "password": "s3cret pw", "face": None,
           "locale": "es_ES.UTF-8", "layout": "us+dvorak", "timezone": "America/Chicago", "ntp": True,
           "pretty_hostname": "Nick’s Luma", "static_hostname": "nicks-luma", "accessibility": {"zoom": True}}


class Apply(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.runs = []

        def run(argv, **kwargs):
            self.runs.append((argv, kwargs.get("input")))
            return subprocess.CompletedProcess(argv, 0, "", "")
        patches = [mock.patch.object(apply.subprocess, "run", run),
                   mock.patch.object(apply, "human_accounts", lambda: []),
                   mock.patch.object(apply, "_taken", lambda name: False),
                   mock.patch.object(apply.os, "access", lambda path, mode: True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_every_step_in_order(self):
        out, bus = io.StringIO(), FakeBus()
        self.assertEqual(apply.apply(dict(ANSWERS), out=out, bus=bus, state_dir=self.state), 0)
        self.assertEqual(out.getvalue().splitlines(),
                         ["step account ok @nick", "step language ok", "step timezone ok", "step name ok",
                          "step keyring ok", "done"])
        methods = [m for _i, m, _a in bus.calls]
        self.assertEqual(methods, ["CreateUser", "SetLanguage", "SetLocale", "SetX11Keyboard", "SetTimezone",
                                   "SetNTP", "SetPrettyHostname", "SetStaticHostname"])
        self.assertEqual(bus.calls[0][2], ("nick", "Nick McMillan", 1))  # an administrator
        self.assertEqual(bus.calls[3][2], ("us", "", "dvorak", "", True, False))
        # the password reaches chpasswd and luma-keyring on stdin, never argv
        self.assertEqual(self.runs[0], (["chpasswd"], "nick:s3cret pw\n"))
        self.assertEqual(self.runs[1], (["/usr/libexec/luma-keyring", "--enrol", "nick"], "s3cret pw"))
        for argv, _stdin in self.runs:
            self.assertNotIn("s3cret pw", " ".join(argv))
        done = json.loads((self.state / "first-boot.json").read_text())
        self.assertEqual(done["user"], "nick")
        welcome = json.loads((self.state / "welcome" / "nick").read_text())
        self.assertEqual(welcome["accessibility"], {"zoom": True})

    def test_once_only(self):
        apply.apply(dict(ANSWERS), out=io.StringIO(), bus=FakeBus(), state_dir=self.state)
        with self.assertRaises(apply.Refused):
            apply.apply(dict(ANSWERS), out=io.StringIO(), bus=FakeBus(), state_dir=self.state)

    def test_not_with_an_account(self):
        with mock.patch.object(apply, "human_accounts", lambda: ["someone"]):
            with self.assertRaises(apply.Refused):
                apply.apply(dict(ANSWERS), out=io.StringIO(), bus=FakeBus(), state_dir=self.state)

    def test_reserved_usernames_and_no_password(self):
        with self.assertRaises(apply.Refused):
            apply.apply(dict(ANSWERS, username="root"), out=io.StringIO(), bus=FakeBus(), state_dir=self.state)
        with self.assertRaises(apply.Refused):
            apply.apply(dict(ANSWERS, password=""), out=io.StringIO(), bus=FakeBus(), state_dir=self.state)

    def test_a_later_step_failing_still_finishes(self):
        out = io.StringIO()
        apply.apply(dict(ANSWERS), out=out, bus=FakeBus(fail="SetTimezone"), state_dir=self.state)
        self.assertIn("step timezone fail", out.getvalue())
        self.assertTrue(out.getvalue().endswith("done\n"))
        self.assertTrue((self.state / "first-boot.json").exists())

    def test_password_failing_removes_the_account(self):
        def run(argv, **kwargs):
            raise subprocess.CalledProcessError(1, argv, "", "chpasswd: (user nick) pam_chauthtok() failed")
        bus = FakeBus()
        with mock.patch.object(apply.subprocess, "run", run), \
                mock.patch.object(apply.pwd, "getpwnam", lambda name: mock.Mock(pw_uid=1000)):
            with self.assertRaises(subprocess.CalledProcessError):
                apply.apply(dict(ANSWERS), out=io.StringIO(), bus=bus, state_dir=self.state)
        self.assertEqual([m for _i, m, _a in bus.calls], ["CreateUser", "DeleteUser"])
        self.assertEqual(bus.calls[1][2], (1000, True))
        self.assertFalse((self.state / "first-boot.json").exists())

    def test_create_failing_after_passwd_removes_the_account(self):
        # useradd writes passwd, then fails on the home folder (an SELinux denial on /var/home)
        bus = FakeBus(fail="CreateUser")
        with mock.patch.object(apply.pwd, "getpwnam", lambda name: mock.Mock(pw_uid=1000)):
            with self.assertRaises(RuntimeError):
                apply.apply(dict(ANSWERS), out=io.StringIO(), bus=bus, state_dir=self.state)
        self.assertEqual([m for _i, m, _a in bus.calls], ["CreateUser", "DeleteUser"])

    def test_create_failing_before_anything_is_made(self):
        def missing(name):
            raise KeyError(name)
        bus = FakeBus(fail="CreateUser")
        with mock.patch.object(apply.pwd, "getpwnam", missing):
            with self.assertRaises(RuntimeError):
                apply.apply(dict(ANSWERS), out=io.StringIO(), bus=bus, state_dir=self.state)
        self.assertEqual([m for _i, m, _a in bus.calls], ["CreateUser"])

    def test_account_language_failing_still_finishes(self):
        out = io.StringIO()
        apply.apply(dict(ANSWERS), out=out, bus=FakeBus(fail="SetLanguage"), state_dir=self.state)
        self.assertTrue(out.getvalue().startswith("step account ok @nick"))
        self.assertTrue(out.getvalue().endswith("done\n"))

    def test_last_frame_is_data_with_the_ticks(self):
        frame = {"posture": "desktop", "light": False, "wallpaper": "/usr/share/backgrounds/luma/prism.png",
                 "first": "Nick", "face": "", "rows": [["account", "Creating your account", "@nick"],
                                                       ["timezone", "Time zone", "Chicago"]],
                 "settings": {"gtk-xft-dpi": 98304, "gtk-font-name": "Figtree 11", "evil": "x"}, "scale": ""}
        apply.apply(dict(ANSWERS, last_frame=frame), out=io.StringIO(), bus=FakeBus(fail="SetTimezone"),
                    state_dir=self.state)
        last = json.loads((self.state / "welcome" / "nick").read_text())["last_frame"]
        self.assertEqual(last["marks"], {"account": True, "language": True, "timezone": False, "name": True,
                                         "keyring": True})
        self.assertEqual(last["settings"], {"gtk-xft-dpi": 98304, "gtk-font-name": "Figtree 11"})
        self.assertEqual(last["rows"], frame["rows"])

    def test_last_frame_refuses_paths_outside_the_system(self):
        frame = {"first": "Nick", "rows": [["account", "a", "b"]], "wallpaper": "/home/nick/x.png"}
        apply.apply(dict(ANSWERS, last_frame=frame), out=io.StringIO(), bus=FakeBus(), state_dir=self.state)
        self.assertIsNone(json.loads((self.state / "welcome" / "nick").read_text())["last_frame"])


if __name__ == "__main__":
    unittest.main()
