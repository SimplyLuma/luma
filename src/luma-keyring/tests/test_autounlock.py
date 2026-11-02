# SPDX-License-Identifier: MPL-2.0
"""Who signs in automatically, and what happens to their keyring, without a daemon."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luma_keyring import autounlock, repair  # noqa: E402
from luma_keyring.autounlock import Result  # noqa: E402
from luma_keyring.secrets import Collection, KeyringError  # noqa: E402


class FakeKeyring:
    """Enough of gnome-keyring: one login collection, keyed by a password."""

    def __init__(self, password=None, locked=True, running=True, files=None):
        self.password = password  # None: no login keyring
        self.locked = locked
        self._running = running
        self.files = files
        self.created = []

    def running(self):
        return self._running

    def activate(self):
        pass

    def collection(self, path=None):
        if self.password is None:
            return None
        return Collection("/login", locked=self.locked, label="Login")

    def unlock_with(self, password, path=None):
        if password != self.password:
            raise KeyringError("The password was invalid")
        self.locked = False

    def change_password(self, old, new, path=None):
        if old != self.password:
            raise KeyringError("The password was invalid")
        self.password = new

    def create_login(self, password, label="login"):
        self.password = password
        self.locked = False
        self.created.append(password)
        self.files.joinpath("login.keyring").write_text("keyring")
        return "/login"


class Paths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(self.root / "data")
        self.addCleanup(self._restore)
        self.keyrings = self.root / "data" / "keyrings"
        self.keyrings.mkdir(parents=True)

    def _restore(self):
        if self._old is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._old

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        return path


class AutologinUsersTests(Paths):
    def test_gdm_automatic_login(self):
        gdm = self.write("custom.conf", "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=nick\n")
        self.assertEqual(autounlock.autologin_users(gdm, self.root / "none.toml"), {"nick"})

    def test_gdm_disabled_or_unnamed_is_nobody(self):
        for text in ("[daemon]\nAutomaticLoginEnable=False\nAutomaticLogin=nick\n",
                     "[daemon]\nAutomaticLoginEnable=true\n",
                     "[daemon]\n#AutomaticLoginEnable=True\n#AutomaticLogin=nick\n",
                     "not an ini file at all\n"):
            gdm = self.write("custom.conf", text)
            self.assertEqual(autounlock.autologin_users(gdm, self.root / "none.toml"), set(), text)

    def test_gdm_timed_login_counts(self):
        gdm = self.write("custom.conf", "[daemon]\nTimedLoginEnable=1\nTimedLogin=tv\n")
        self.assertEqual(autounlock.autologin_users(gdm, self.root / "none.toml"), {"tv"})

    def test_greetd_initial_session(self):
        greetd = self.write("config.toml",
                            '[default_session]\ncommand = "agreety"\nuser = "greeter"\n'
                            '[initial_session]\ncommand = "phosh-session"\nuser = "luma"\n')
        self.assertEqual(autounlock.autologin_users(self.root / "none.conf", greetd), {"luma"})

    def test_missing_files_are_nobody(self):
        self.assertEqual(autounlock.autologin_users(self.root / "a", self.root / "b"), set())


class EnsureTests(Paths):
    def seal(self, path):
        path.write_text("sealed")
        return "device-key"

    def unseal(self, path):
        return "device-key"

    def ensure(self, keyring, autologin, **kwargs):
        return autounlock.ensure(keyring, autologin=autologin, seal=self.seal,
                                 unseal=self.unseal, wait=0, **kwargs)

    def test_password_accounts_are_untouched(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.NOT_AUTOLOGIN)
        self.assertEqual(keyring.created, [])
        self.assertFalse(autounlock.credential_path().exists())

    def test_first_automatic_sign_in_makes_a_device_keyed_keyring(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.CREATED)
        self.assertEqual(keyring.created, ["device-key"])
        self.assertTrue(autounlock.credential_path().exists())

    def test_without_a_sealed_key_the_keyring_has_an_empty_password(self):
        def cannot_seal(path):
            raise KeyringError("no host key")

        keyring = FakeKeyring(password=None, files=self.keyrings)
        result = autounlock.ensure(keyring, autologin=True, seal=cannot_seal, wait=0)
        self.assertEqual(result, Result.CREATED)
        self.assertEqual(keyring.created, [""])
        self.assertFalse(autounlock.credential_path().exists())

    def test_later_sign_ins_open_it_with_the_sealed_key(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        autounlock.credential_path().write_text("sealed")
        keyring = FakeKeyring(password="device-key", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.UNLOCKED)
        self.assertFalse(keyring.locked)

    def test_a_password_locked_keyring_is_never_rekeyed_silently(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        keyring = FakeKeyring(password="their-password", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NEEDS_PASSWORD)
        self.assertEqual(keyring.password, "their-password")
        self.assertTrue(keyring.locked)
        self.assertEqual(keyring.created, [])

    def test_already_unlocked_is_left_alone(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        keyring = FakeKeyring(password="", locked=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.ALREADY_UNLOCKED)

    def test_an_unencrypted_keyring_is_sealed_without_asking(self):
        self.keyrings.joinpath("login.keyring").write_text("[keyring]\ndisplay-name=Login\n")
        keyring = FakeKeyring(password="", locked=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.SEALED)
        self.assertEqual(keyring.password, "device-key")
        self.assertTrue(autounlock.credential_path().exists())

    def test_an_unencrypted_keyring_shown_locked_is_sealed_too(self):
        self.keyrings.joinpath("login.keyring").write_text("[keyring]\ndisplay-name=Login\n")
        keyring = FakeKeyring(password="", locked=True, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.SEALED)
        self.assertEqual(keyring.password, "device-key")

    def test_an_unencrypted_keyring_of_a_password_account_is_left(self):
        self.keyrings.joinpath("login.keyring").write_text("[keyring]\ndisplay-name=Login\n")
        keyring = FakeKeyring(password="", locked=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.NOT_AUTOLOGIN)
        self.assertEqual(keyring.password, "")

    def test_sealed_key_still_opens_it_after_automatic_sign_in_is_turned_off(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        autounlock.credential_path().write_text("sealed")
        keyring = FakeKeyring(password="device-key", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.UNLOCKED)

    def test_a_key_that_no_longer_opens_it_is_reported(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        autounlock.credential_path().write_text("sealed")
        keyring = FakeKeyring(password="some-other-key", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.KEY_UNUSABLE)

    def test_no_daemon_is_not_an_error(self):
        keyring = FakeKeyring(running=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NO_DAEMON)


class SwitchTests(Paths):
    def seal(self, path):
        path.write_text("sealed")
        return "device-key"

    def test_switch_with_the_right_password_keeps_the_keyring(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        keyring = FakeKeyring(password="their-password", files=self.keyrings)
        outcome = repair.switch_to_device_key("their-password", keyring, seal=self.seal)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, "device-key")
        self.assertFalse(keyring.locked)
        self.assertTrue(autounlock.credential_path().exists())
        self.assertFalse(autounlock.credential_path().with_name("login.cred.new").exists())

    def test_switch_with_the_wrong_password_changes_nothing(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        keyring = FakeKeyring(password="their-password", files=self.keyrings)
        outcome = repair.switch_to_device_key("wrong", keyring, seal=self.seal)
        self.assertFalse(outcome.ok)
        self.assertEqual(keyring.password, "their-password")
        self.assertFalse(autounlock.credential_path().exists())
        self.assertFalse(autounlock.credential_path().with_name("login.cred.new").exists())

    def test_start_again_keeps_the_old_file(self):
        self.keyrings.joinpath("login.keyring").write_text("old")
        keyring = FakeKeyring(password="forgotten", files=self.keyrings)
        outcome = repair.start_again_with_device_key(keyring, seal=self.seal)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(outcome.kept_file.read_text(), "old")
        self.assertEqual(keyring.created, ["device-key"])
        self.assertTrue(autounlock.credential_path().exists())

    def test_back_to_the_login_password(self):
        self.keyrings.joinpath("login.keyring").write_text("keyring")
        autounlock.credential_path().write_text("sealed")
        keyring = FakeKeyring(password="device-key", locked=False, files=self.keyrings)
        outcome = repair.use_login_password("login-pw", keyring, unseal=lambda p: "device-key",
                                            verify=lambda p: p == "login-pw")
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, "login-pw")
        self.assertFalse(autounlock.credential_path().exists())

    def test_back_to_a_wrong_login_password_changes_nothing(self):
        autounlock.credential_path().write_text("sealed")
        keyring = FakeKeyring(password="device-key", locked=False, files=self.keyrings)
        outcome = repair.use_login_password("nope", keyring, unseal=lambda p: "device-key",
                                            verify=lambda p: p == "login-pw")
        self.assertFalse(outcome.ok)
        self.assertEqual(keyring.password, "device-key")
        self.assertTrue(autounlock.credential_path().exists())


if __name__ == "__main__":
    unittest.main()
