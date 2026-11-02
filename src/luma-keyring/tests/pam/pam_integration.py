#!/usr/bin/python3 -sP
# SPDX-License-Identifier: MPL-2.0
"""pam_luma_keyring.so in a real passwd stack, against real /etc/shadow.

Destructive to nothing but a throwaway account it creates (and removes):
run as root on a disposable Fedora system or VM with systemd running
(systemd-creds and systemd-userdbd), never on someone's own machine:

    meson setup build && ninja -C build
    sudo python3 tests/pam/pam_integration.py build

Every case drives pam_chauthtok as set-user-ID passwd does (tests/pam/
chauthtok.c) through a copy of Fedora's passwd stack with the module added,
and, where a change fails, compares the prompts with the same stack without
the module.
"""

import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))

from luma_keyring import autounlock  # noqa: E402

BUILD = Path(sys.argv.pop(1) if len(sys.argv) > 1 else SOURCE / "build").resolve()
USER = "luma-keyring-pamtest"
OLD = "Old-Pass-for-tests-7431"
NEW = "New-Pass-for-tests-9182"
NEWER = "Newer-Pass-for-tests-2654"
NEWEST = "Newest-Pass-for-tests-5807"
PAM_D = Path("/etc/pam.d")
NO_SUBIDS = ("-K", "SUB_UID_COUNT=0", "-K", "SUB_GID_COUNT=0")
SERVICES = ("luma-kt-passwd", "luma-kt-passwd-plain", "luma-kt-passwd-fail",
            "luma-kt-passwd-fail-plain", "luma-kt-auth-fail")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


class PamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0:
            raise unittest.SkipTest("needs root on a disposable system")
        cls.work = Path(tempfile.mkdtemp(prefix="luma-kt-"))
        os.chmod(cls.work, 0o755)
        cls.module = BUILD / "pam_luma_keyring.so"
        cls.driver = BUILD / "chauthtok"
        # The real helper, with the source tree on its path and the recovery
        # script where the package would install it.
        cls.helper = cls.work / "luma-keyring"
        cls.helper.write_text(
            "#!/bin/sh\n"
            "exec /usr/bin/python3 -BsP -c '\n"
            "import sys; from pathlib import Path\n"
            f"sys.path.insert(0, \"{SOURCE}\")\n"
            "from luma_keyring import autounlock, cli\n"
            f"autounlock.RECOVERY_TOOL = Path(\"{SOURCE}/tools/luma-keyring-recover-standalone\")\n"
            "raise SystemExit(cli.dispatch(sys.argv))' luma-keyring \"$@\"\n")
        cls.helper.chmod(0o755)
        line = f"-password   optional\t{cls.module} helper={cls.helper} debug\n"
        stacks = {
            # Fedora's /etc/pam.d/passwd, with and without the module.
            "luma-kt-passwd": f"password substack system-auth\n{line}password substack postlogin\n",
            "luma-kt-passwd-plain": "password substack system-auth\npassword substack postlogin\n",
            # pam_pwquality sets the new token, then the change fails in the
            # update phase (as pam_unix failing to write would).
            "luma-kt-auth-fail": ("password requisite pam_pwquality.so\n"
                                  "password requisite pam_exec.so quiet /usr/bin/false\n"
                                  "password sufficient pam_unix.so yescrypt shadow use_authtok\n"
                                  "password required pam_deny.so\n"),
            "luma-kt-passwd-fail": f"password substack luma-kt-auth-fail\n{line}",
            "luma-kt-passwd-fail-plain": "password substack luma-kt-auth-fail\n",
        }
        for name, text in stacks.items():
            (PAM_D / name).write_text("#%PAM-1.0\n" + text)
        subprocess.run(["userdel", "-r", USER], capture_output=True)
        run("useradd", "-m", *NO_SUBIDS, USER)
        run("chpasswd", input=f"{USER}:{OLD}\n")
        cls.account = pwd.getpwnam(USER)
        cls.home = Path(cls.account.pw_dir)
        run(str(cls.helper), "--enrol", USER, input=OLD)

    @classmethod
    def tearDownClass(cls):
        for name in SERVICES:
            (PAM_D / name).unlink(missing_ok=True)
        subprocess.run(["userdel", "-r", USER], capture_output=True)
        shutil.rmtree(cls.work, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    @property
    def state(self):
        return autounlock.state_dir(self.home)

    def chauthtok(self, service, answers, root=False):
        args = [str(self.driver), service, USER] + (["root"] if root else [])
        started = time.monotonic()
        done = subprocess.run(args, input="".join(a + "\n" for a in answers),
                              capture_output=True, text=True, timeout=60)
        prompts = [l for l in done.stdout.splitlines() if l.startswith("prompt: ")]
        return done.returncode, prompts, time.monotonic() - started

    def shadow(self):
        return autounlock.shadow_hash(USER)

    def opens(self, name, password):
        data = (self.state / name).read_bytes()
        try:
            return autounlock.unwrap_key(data, password)
        except autounlock.WrongPassword:
            return None

    def as_user(self, code):
        """Python as the account in a clean environment, as the session runs it."""
        done = subprocess.run(
            ["setpriv", f"--reuid={self.account.pw_uid}", f"--regid={self.account.pw_gid}",
             "--clear-groups", "env", "-i", f"HOME={self.home}", "PATH=/usr/bin",
             "/usr/bin/python3", "-BsP", "-c",
             f"import sys; sys.path.insert(0, {str(SOURCE)!r})\n"
             "from luma_keyring import autounlock\n"
             "autounlock.password_changed = lambda user=None: None\n" + code],
            capture_output=True, text=True, timeout=60, cwd="/")
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def set_password(self, password):
        run("chpasswd", input=f"{USER}:{password}\n")

    # -- cases -----------------------------------------------------------------
    def test_1_wiring(self):
        """pam_luma_keyring.so is placed after pam_gnome_keyring; pam_exec is replaced."""
        sample = self.work / "passwd"
        sample.write_text("#%PAM-1.0\npassword   substack\tsystem-auth\n"
                          "-password   optional\tpam_gnome_keyring.so use_authtok\n"
                          "-password   optional\tpam_exec.so quiet expose_authtok "
                          "/usr/libexec/luma-keyring --pam-rewrap\n"
                          "password   substack\tpostlogin\n")
        setup = SOURCE / "data" / "pam" / "luma-keyring-pam-setup"
        for _ in range(2):
            run("sh", str(setup), "add", str(sample))
        lines = sample.read_text().splitlines()
        self.assertNotIn("expose_authtok", sample.read_text())
        self.assertEqual(sum("pam_luma_keyring.so" in l for l in lines), 1)
        self.assertEqual(lines.index("-password   optional\tpam_luma_keyring.so"),
                         lines.index("-password   optional\tpam_gnome_keyring.so use_authtok") + 1)
        run("sh", str(setup), "remove", str(sample))
        self.assertNotIn("luma", sample.read_text())

    def test_2_failed_change_with_a_token_keeps_the_wrap(self):
        before = (self.state / "login.wrap").read_bytes()
        hashed = self.shadow()
        code, prompts, _ = self.chauthtok("luma-kt-passwd-fail", [OLD, NEW, NEW])
        _, plain, _ = self.chauthtok("luma-kt-passwd-fail-plain", [OLD, NEW, NEW])
        self.assertNotEqual(code, 0)
        self.assertEqual(prompts, plain)
        self.assertEqual(self.shadow(), hashed)
        self.assertEqual((self.state / "login.wrap").read_bytes(), before)
        self.assertFalse((self.state / "login.wrap.prev").exists())

    def test_3_rejected_password_clears_the_token_and_adds_no_prompt(self):
        before = (self.state / "login.wrap").read_bytes()
        weak = ["a"] * 12
        code, prompts, _ = self.chauthtok("luma-kt-passwd", [OLD] + weak)
        _, plain, _ = self.chauthtok("luma-kt-passwd-plain", [OLD] + weak)
        self.assertNotEqual(code, 0)
        self.assertEqual(prompts, plain)
        self.assertNotIn("prompt: Password: ", prompts)
        self.assertEqual((self.state / "login.wrap").read_bytes(), before)

    def test_4_change_rewraps_keeps_the_previous_and_ignores_a_fifo(self):
        fifo = self.state / "login.cred.new"
        os.mkfifo(fifo, 0o600)
        os.chown(fifo, self.account.pw_uid, self.account.pw_gid)
        try:
            code, prompts, seconds = self.chauthtok("luma-kt-passwd", [OLD, NEW, NEW])
        finally:
            fifo.unlink()
        self.assertEqual(code, 0)
        self.assertEqual(len(prompts), 3)  # current, new, again: nothing added
        self.assertLess(seconds, 15)
        key = self.opens("login.wrap", NEW)
        self.assertIsNotNone(key)
        self.assertIsNone(self.opens("login.wrap", OLD))
        self.assertEqual(self.opens("login.wrap.prev", OLD), key)
        record = json.loads((self.state / "login.wrap").read_bytes())
        self.assertEqual(record["password_id"], autounlock.password_id(key, self.shadow()))
        tool = self.state / "luma-keyring-recover"
        self.assertTrue(tool.exists())
        info = tool.stat()
        self.assertEqual((info.st_uid, stat.S_IMODE(info.st_mode)), (self.account.pw_uid, 0o700))
        self.assertTrue((self.state / ".deja-dup-ignore").exists())
        for path in self.state.iterdir():
            self.assertEqual(path.stat().st_uid, self.account.pw_uid, path)

    def test_5_session_confirms_the_change(self):
        self.assertTrue((self.state / "login.wrap.prev").exists())
        # Writing /etc/shadow for another account is no password change.
        run("useradd", *NO_SUBIDS, "luma-keyring-pamtest2")
        run("userdel", "-r", "luma-keyring-pamtest2")
        self.assertEqual(self.as_user("print(autounlock.wrap_is_stale())"), "False")
        self.assertFalse((self.state / "login.wrap.prev").exists())

    def test_6_a_rolled_back_change_puts_the_confirmed_wrap_back(self):
        new_hash = self.shadow()  # confirmed in test 5
        # Two changes, neither confirmed by a session.
        code, _, _ = self.chauthtok("luma-kt-passwd", [NEW, NEWER, NEWER])
        self.assertEqual(code, 0)
        code, _, _ = self.chauthtok("luma-kt-passwd", [NEWER, NEWEST, NEWEST])
        self.assertEqual(code, 0)
        self.assertIsNotNone(self.opens("login.wrap.prev", NEW))
        run("usermod", "-p", new_hash, USER)  # /etc as it was before both changes
        self.assertEqual(self.as_user("print(autounlock.wrap_is_stale())"), "False")
        self.assertIsNotNone(self.opens("login.wrap", NEW))
        self.assertFalse((self.state / "login.wrap.prev").exists())

    def test_7_a_change_outside_pam_is_seen(self):
        hashed = self.shadow()
        self.set_password(NEWER)  # chpasswd: not the passwd stack
        self.assertEqual(self.as_user("print(autounlock.wrap_is_stale())"), "True")
        run("usermod", "-p", hashed, USER)
        self.assertEqual(self.as_user("print(autounlock.wrap_is_stale())"), "False")

    def test_8_administrator_change(self):
        code, prompts, _ = self.chauthtok("luma-kt-passwd", [NEWER, NEWER], root=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(prompts), 2)
        self.assertIsNotNone(self.opens("login.wrap", NEWER))
        self.assertIsNotNone(self.opens("login.wrap.prev", NEW))

    def test_9_the_standalone_script_reaches_every_slot(self):
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader(
            "recover", str(self.state / "luma-keyring-recover"))
        spec = importlib.util.spec_from_loader("recover", loader)
        tool = importlib.util.module_from_spec(spec)
        loader.exec_module(tool)
        tool.STATE = self.state
        key = self.opens("login.wrap", NEWER)
        found = dict(tool.from_wraps(NEW))
        self.assertEqual(found.get("login.wrap.prev"), key)
        # A seal that opens with a key the keyring refuses falls through to the wraps.
        taken = []
        name = tool.recover("login", lambda: NEWER,
                            seals=lambda: iter([("login.cred", "wrong-key")]),
                            rekey=lambda old, new: taken.append(old) or old == key)
        self.assertEqual(name, "login.wrap")
        self.assertEqual(taken, ["wrong-key", key])


if __name__ == "__main__":
    unittest.main(verbosity=2)
