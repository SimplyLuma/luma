# SPDX-License-Identifier: MPL-2.0
"""Who signs in automatically, and what happens to their keyring, without a daemon."""

import base64
import json
import os
import stat
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
        self._old_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.root / "state")
        # The real parameters are checked once; the rest need speed, not cost.
        self._params = autounlock.ARGON2ID
        autounlock.ARGON2ID = {"memory_cost": 8, "iterations": 1, "lanes": 1}
        self._changed = autounlock.password_changed
        autounlock.password_changed = lambda user=None: None

    def _restore(self):
        autounlock.ARGON2ID = self._params
        autounlock.password_changed = self._changed
        if self._old_state is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._old_state
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


KEY = "keyring-key"


def seal(key, user=None):
    return b"sealed:" + key.encode()


def unseal(path):
    data = Path(path).read_bytes()
    if not data.startswith(b"sealed:"):
        raise KeyringError("Bad credential format.")
    return data[len(b"sealed:"):].decode()


def cannot_seal(key, user=None):
    raise KeyringError("no host key")


class EnsureTests(Paths):
    """The keyring is on a random key; the device keeps it sealed."""

    def enrolled(self, key=KEY):
        autounlock.write_private(autounlock.credential_path(), seal(key))

    def ensure(self, keyring, autologin):
        return autounlock.ensure(keyring, autologin=autologin, wait=0, unseal=unseal)

    def keyring_file(self, text="GnomeKeyring\n"):
        self.keyrings.joinpath("login.keyring").write_text(text)

    def test_password_accounts_are_untouched(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.NOT_AUTOLOGIN)
        self.assertEqual(keyring.created, [])

    def test_first_automatic_sign_in_makes_the_keyring_on_the_key(self):
        self.enrolled()
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.CREATED)
        self.assertEqual(keyring.created, [KEY])

    def test_later_sign_ins_unlock_it(self):
        self.enrolled()
        self.keyring_file()
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.UNLOCKED)
        self.assertFalse(keyring.locked)

    def test_not_enrolled_nothing_is_made_or_changed(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NEEDS_PASSWORD)
        self.assertEqual(keyring.created, [])
        self.keyring_file()
        keyring = FakeKeyring(password="theirs", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NEEDS_PASSWORD)
        self.assertEqual((keyring.password, keyring.locked), ("theirs", True))

    def test_a_corrupt_seal_leaves_the_keyring_locked_and_intact(self):
        autounlock.write_private(autounlock.credential_path(), b"garbage")
        self.keyring_file()
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.SEAL_UNUSABLE)
        self.assertEqual((keyring.password, keyring.locked), (KEY, True))
        self.assertEqual(self.keyrings.joinpath("login.keyring").read_text(), "GnomeKeyring\n")

    def test_a_corrupt_seal_never_makes_a_keyring(self):
        autounlock.write_private(autounlock.credential_path(), b"garbage")
        keyring = FakeKeyring(password=None, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.SEAL_UNUSABLE)
        self.assertEqual(keyring.created, [])

    def test_a_seal_that_does_not_open_it_is_reported(self):
        self.enrolled("another-key")
        self.keyring_file()
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.SEAL_UNUSABLE)

    def test_still_unlocks_after_automatic_sign_in_is_turned_off(self):
        self.enrolled()
        self.keyring_file()
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.UNLOCKED)

    def test_an_unencrypted_keyring_opens_without_a_prompt(self):
        self.keyring_file("[keyring]\ndisplay-name=Login\n")
        keyring = FakeKeyring(password="", files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.UNENCRYPTED)

    def test_already_unlocked_is_left_alone(self):
        self.keyring_file()
        keyring = FakeKeyring(password="x", locked=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.ALREADY_UNLOCKED)

    def test_no_daemon_is_not_an_error(self):
        keyring = FakeKeyring(running=False, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, True), Result.NO_DAEMON)


class WrapTests(Paths):
    def test_round_trip_and_wrong_password(self):
        wrapped = autounlock.wrap_key(KEY, "pw")
        self.assertEqual(autounlock.unwrap_key(wrapped, "pw"), KEY)
        with self.assertRaises(autounlock.WrongPassword):
            autounlock.unwrap_key(wrapped, "not-pw")

    def test_the_file_is_versioned_and_holds_no_secret(self):
        autounlock.ARGON2ID = self._params
        wrapped = autounlock.wrap_key(KEY, "pw")
        record = json.loads(wrapped)
        self.assertEqual((record["magic"], record["version"]), ("luma-login-keyring-wrap", 1))
        self.assertEqual(record["kdf_params"], {"memory_cost": 65536, "iterations": 3, "lanes": 4})
        self.assertEqual(len(base64.b64decode(record["salt"])), 16)
        self.assertNotIn(b"pw", wrapped.replace(b"kdf_params", b""))
        self.assertNotIn(KEY.encode(), wrapped)

    def test_salts_and_nonces_are_fresh(self):
        one, two = (json.loads(autounlock.wrap_key(KEY, "pw")) for _ in range(2))
        self.assertNotEqual(one["salt"], two["salt"])
        self.assertNotEqual(one["ciphertext"], two["ciphertext"])

    def test_a_tampered_header_or_body_is_refused(self):
        record = json.loads(autounlock.wrap_key(KEY, "pw"))
        for change in ({"version": 2}, {"magic": "x"},
                       {"ciphertext": base64.b64encode(b"x" * 40).decode()}):
            broken = json.dumps({**record, **change}).encode()
            with self.assertRaises((KeyringError, autounlock.WrongPassword)):
                autounlock.unwrap_key(broken, "pw")
        with self.assertRaises(KeyringError):
            autounlock.unwrap_key(b"not json", "pw")

    def test_keys_are_256_bit_and_random(self):
        keys = {autounlock.new_key() for _ in range(8)}
        self.assertEqual(len(keys), 8)
        self.assertTrue(all(len(base64.b64decode(k)) == 32 for k in keys))


class FlowTests(Paths):
    PASSWORD = "account-password"

    def verify(self, password):
        return password == self.PASSWORD

    def keyring_file(self, text="GnomeKeyring\n"):
        self.keyrings.joinpath("login.keyring").write_text(text)

    def stored_key(self):
        return unseal(autounlock.credential_path())

    def test_enable_moves_an_existing_keyring_onto_a_sealed_wrapped_key(self):
        self.keyring_file()
        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        outcome = repair.enable_auto_unlock(self.PASSWORD, keyring, seal=seal, verify=self.verify)
        self.assertTrue(outcome.ok, outcome.message)
        key = self.stored_key()
        self.assertEqual(keyring.password, key)
        self.assertNotEqual(key, self.PASSWORD)
        self.assertEqual(autounlock.unwrap_key(autounlock.wrap_path().read_bytes(), self.PASSWORD), key)
        for path in (autounlock.credential_path(), autounlock.wrap_path()):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(autounlock.state_dir().stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in autounlock.state_dir().iterdir()),
                         ["login.cred", "login.wrap"])

    def test_enable_with_a_wrong_password_changes_nothing(self):
        self.keyring_file()
        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        outcome = repair.enable_auto_unlock("wrong", keyring, seal=seal, verify=self.verify)
        self.assertFalse(outcome.ok)
        self.assertEqual(keyring.password, self.PASSWORD)
        self.assertFalse(autounlock.state_dir().exists() and any(autounlock.state_dir().iterdir()))

    def test_enable_on_a_mismatched_keyring_changes_nothing(self):
        self.keyring_file()
        keyring = FakeKeyring(password="older", files=self.keyrings)
        outcome = repair.enable_auto_unlock(self.PASSWORD, keyring, seal=seal, verify=self.verify)
        self.assertFalse(outcome.ok)
        self.assertEqual(keyring.password, "older")
        self.assertEqual(list(autounlock.state_dir().iterdir()), [])

    def test_enable_without_a_keyring_makes_one(self):
        keyring = FakeKeyring(password=None, files=self.keyrings)
        outcome = repair.enable_auto_unlock(self.PASSWORD, keyring, seal=seal, verify=self.verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.created, [self.stored_key()])

    def test_empty_password_only_when_nothing_can_be_sealed(self):
        self.keyring_file()
        keyring = FakeKeyring(password=self.PASSWORD, files=self.keyrings)
        outcome = repair.enable_auto_unlock(self.PASSWORD, keyring, seal=cannot_seal,
                                            verify=self.verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, "")
        self.assertIn("without encryption", outcome.message)
        self.assertFalse(autounlock.credential_path().exists())

    def test_recover_unwraps_and_seals_again(self):
        self.keyring_file()
        autounlock.write_private(autounlock.wrap_path(), autounlock.wrap_key(KEY, self.PASSWORD))
        autounlock.write_private(autounlock.credential_path(), b"from another board")
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertFalse(repair.recover("wrong", keyring, seal=seal).ok)
        self.assertTrue(keyring.locked)
        outcome = repair.recover(self.PASSWORD, keyring, seal=seal)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertFalse(keyring.locked)
        self.assertEqual(self.stored_key(), KEY)

    def test_recover_with_a_corrupt_wrap_leaves_everything(self):
        self.keyring_file()
        autounlock.write_private(autounlock.wrap_path(), b"{broken")
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        outcome = repair.recover(self.PASSWORD, keyring, seal=seal)
        self.assertFalse(outcome.ok)
        self.assertEqual((keyring.password, keyring.locked), (KEY, True))

    def test_rewrap_follows_a_password_change(self):
        autounlock.write_private(autounlock.wrap_path(), autounlock.wrap_key(KEY, "old"))
        self.assertTrue(repair.rewrap("old", "new").ok)
        wrapped = autounlock.wrap_path().read_bytes()
        self.assertEqual(autounlock.unwrap_key(wrapped, "new"), KEY)
        with self.assertRaises(autounlock.WrongPassword):
            autounlock.unwrap_key(wrapped, "old")
        self.assertTrue(repair.rewrap("old", "new").ok)  # already done
        self.assertFalse(repair.rewrap("neither", "nor").ok)

    def test_a_password_change_makes_the_wrap_stale(self):
        autounlock.password_changed = lambda user=None: 1000 * 86400
        autounlock.write_private(autounlock.wrap_path(), autounlock.wrap_key(KEY, "old", changed=1000 * 86400))
        self.assertFalse(autounlock.wrap_is_stale())
        autounlock.password_changed = lambda user=None: 1001 * 86400
        self.assertTrue(autounlock.wrap_is_stale())

    def test_rewrap_current_needs_only_the_new_password(self):
        autounlock.write_private(autounlock.credential_path(), seal(KEY))
        autounlock.write_private(autounlock.wrap_path(), autounlock.wrap_key(KEY, "old"))
        self.assertFalse(repair.rewrap_current("not-current", unseal=unseal, verify=self.verify).ok)
        outcome = repair.rewrap_current(self.PASSWORD, unseal=unseal, verify=self.verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(autounlock.unwrap_key(autounlock.wrap_path().read_bytes(), self.PASSWORD), KEY)

    def test_start_again_keeps_the_old_file(self):
        self.keyring_file("old")
        keyring = FakeKeyring(password="forgotten", files=self.keyrings)
        outcome = repair.start_again_with_auto_unlock(self.PASSWORD, keyring, seal=seal,
                                                      verify=self.verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(outcome.kept_file.read_text(), "old")
        self.assertEqual(keyring.created, [self.stored_key()])

    def test_back_to_the_login_password(self):
        self.keyring_file()
        autounlock.write_private(autounlock.credential_path(), seal(KEY))
        autounlock.write_private(autounlock.wrap_path(), autounlock.wrap_key(KEY, self.PASSWORD))
        keyring = FakeKeyring(password=KEY, locked=False, files=self.keyrings)
        outcome = repair.use_login_password(self.PASSWORD, keyring, unseal=unseal, verify=self.verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, self.PASSWORD)
        self.assertFalse(autounlock.credential_path().exists() or autounlock.wrap_path().exists())


if __name__ == "__main__":
    unittest.main()
