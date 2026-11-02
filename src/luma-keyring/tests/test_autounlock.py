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
    """The keyring keeps the account password; the device keeps a sealed copy."""

    PASSWORD = "account-password"

    def seal(self):
        autounlock.credential_path().write_text("sealed")

    def ensure(self, keyring, autologin, unseal=None):
        return autounlock.ensure(keyring, autologin=autologin, wait=0,
                                 unseal=unseal or (lambda path: self.PASSWORD))

    def keyring_file(self, text="GnomeKeyring\n"):
        self.keyrings.joinpath("login.keyring").write_text(text)

    def test_password_accounts_are_untouched(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.NOT_AUTOLOGIN)
        self.assertEqual(keyring.created, [])

    def test_first_automatic_sign_in_makes_the_keyring_with_the_account_password(self):
        self.seal()
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.CREATED)
        self.assertEqual(keyring.created, [self.PASSWORD])

    def test_later_sign_ins_open_it_with_the_sealed_password(self):
        self.seal()
        self.keyring_file()
        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.UNLOCKED)
        self.assertFalse(keyring.locked)
        self.assertEqual(keyring.password, self.PASSWORD)

    def test_without_a_sealed_password_nothing_is_made_or_changed(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NEEDS_PASSWORD)
        self.assertEqual(keyring.created, [])
        self.keyring_file()
        keyring = FakeKeyring(password="theirs", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NEEDS_PASSWORD)
        self.assertEqual(keyring.password, "theirs")
        self.assertTrue(keyring.locked)

    def test_an_unencrypted_keyring_opens_without_a_prompt(self):
        self.keyring_file("[keyring]\ndisplay-name=Login\n")
        keyring = FakeKeyring(password="", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.UNENCRYPTED)
        self.assertEqual(keyring.password, "")

    def test_already_unlocked_is_left_alone(self):
        self.keyring_file()
        keyring = FakeKeyring(password=self.PASSWORD, locked=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.ALREADY_UNLOCKED)

    def test_a_stale_sealed_password_is_reported_not_forced(self):
        self.seal()
        self.keyring_file()
        keyring = FakeKeyring(password="changed-since", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.SEAL_UNUSABLE)
        self.assertEqual(keyring.password, "changed-since")

    def test_a_seal_this_device_cannot_open_is_reported(self):
        self.seal()
        self.keyring_file()

        def cannot(path):
            raise KeyringError("Bad credential format.")

        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True, unseal=cannot), Result.SEAL_UNUSABLE)

    def test_the_sealed_copy_goes_when_automatic_sign_in_is_turned_off(self):
        self.seal()
        self.keyring_file()
        keyring = FakeKeyring(password=self.PASSWORD, locked=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.RETIRED)
        self.assertFalse(autounlock.credential_path().exists())
        self.assertEqual(keyring.password, self.PASSWORD)

    def test_no_daemon_is_not_an_error(self):
        keyring = FakeKeyring(running=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NO_DAEMON)


class EnableTests(Paths):
    PASSWORD = "account-password"

    def seal(self, password):
        self.sealed = password
        autounlock.credential_path().write_text("sealed")

    def cannot_seal(self, password):
        raise KeyringError("no host key")

    def enable(self, keyring, password, seal=None):
        return repair.enable_auto_unlock(password, keyring, seal=seal or self.seal,
                                         verify=lambda p: p == self.PASSWORD)

    def test_existing_keyring_keeps_its_password_and_gets_a_sealed_copy(self):
        self.keyrings.joinpath("login.keyring").write_text("GnomeKeyring\n")
        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        outcome = self.enable(keyring, self.PASSWORD)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, self.PASSWORD)
        self.assertFalse(keyring.locked)
        self.assertEqual(self.sealed, self.PASSWORD)

    def test_the_wrong_password_seals_nothing(self):
        self.keyrings.joinpath("login.keyring").write_text("GnomeKeyring\n")
        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        outcome = self.enable(keyring, "wrong")
        self.assertFalse(outcome.ok)
        self.assertFalse(autounlock.credential_path().exists())
        self.assertTrue(keyring.locked)

    def test_no_keyring_yet_makes_one_with_the_login_password(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertFalse(self.enable(keyring, "not-the-login-password").ok)
        self.assertEqual(keyring.created, [])
        outcome = self.enable(keyring, self.PASSWORD)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.created, [self.PASSWORD])

    def test_an_unencrypted_keyring_gets_the_login_password(self):
        self.keyrings.joinpath("login.keyring").write_text("[keyring]\n")
        keyring = FakeKeyring(password="", files=self.keyrings)
        outcome = self.enable(keyring, self.PASSWORD)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, self.PASSWORD)

    def test_empty_password_only_when_nothing_can_be_sealed(self):
        self.keyrings.joinpath("login.keyring").write_text("GnomeKeyring\n")
        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        outcome = self.enable(keyring, self.PASSWORD, seal=self.cannot_seal)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, "")
        self.assertIn("without encryption", outcome.message)

    def test_start_again_uses_the_login_password_and_keeps_the_old_file(self):
        self.keyrings.joinpath("login.keyring").write_text("old")
        keyring = FakeKeyring(password="forgotten", files=self.keyrings)
        outcome = repair.start_again_with_auto_unlock(
            self.PASSWORD, keyring, seal=self.seal, verify=lambda p: p == self.PASSWORD)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(outcome.kept_file.read_text(), "old")
        self.assertEqual(keyring.created, [self.PASSWORD])
        self.assertEqual(self.sealed, self.PASSWORD)


if __name__ == "__main__":
    unittest.main()
