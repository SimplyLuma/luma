# SPDX-License-Identifier: MPL-2.0
"""The automatic sign-in keyring without a daemon: policy, slots, crashes."""

import base64
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luma_keyring import autounlock, repair  # noqa: E402
from luma_keyring.autounlock import Result  # noqa: E402
from luma_keyring.secrets import Collection, KeyringError  # noqa: E402

KEY = "keyring-key"
PASSWORD = "account-password"


class Crash(BaseException):
    """The power going out: nothing after it runs."""


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
        if self.password is None or password != self.password:
            raise KeyringError("The password was invalid")
        self.locked = False

    def change_password(self, old, new, path=None):
        if self.password is None or old != self.password:
            raise KeyringError("The password was invalid")
        self.password = new

    def create_login(self, password, label="login"):
        self.password = password
        self.locked = False
        self.created.append(password)
        self.files.joinpath("login.keyring").write_text("GnomeKeyring\n")
        return "/login"

    def reboot(self):
        self.locked = True


def seal(key, user=None):
    return b"sealed:" + key.encode()


def unseal(path, user=None):
    data = Path(path).read_bytes()
    if not data.startswith(b"sealed:"):
        raise KeyringError("Bad credential format.")
    return data[len(b"sealed:"):].decode()


def cannot_seal(key, user=None):
    raise KeyringError("no host key")


def verify(password):
    return password == PASSWORD


class Paths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        for name, value in (("HOME", str(self.home)),
                            ("XDG_DATA_HOME", str(self.home / ".local" / "share"))):
            self.addCleanup(self._put, name, os.environ.get(name))
            os.environ[name] = value
        self.keyrings = self.home / ".local" / "share" / "keyrings"
        self.keyrings.mkdir(parents=True)
        # The real parameters are checked on their own; the rest need speed.
        self.patch(autounlock, "ARGON2ID", {"memory_cost": 8, "iterations": 1, "lanes": 1})
        self.patch(autounlock, "ARGON2ID_BOUNDS",
                   {"memory_cost": (8, 1048576), "iterations": (1, 16), "lanes": (1, 16)})
        self.patch(autounlock, "password_changed", lambda user=None: None)
        self.patch(autounlock, "seal_key", seal)
        self.patch(autounlock, "unseal_key", unseal)
        self.patch(repair, "password_quality_note", lambda password: "")

    @staticmethod
    def _put(name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def patch(self, owner, name, value):
        self.addCleanup(setattr, owner, name, getattr(owner, name))
        setattr(owner, name, value)

    def keyring_file(self, text="GnomeKeyring\n"):
        self.keyrings.joinpath("login.keyring").write_text(text)

    def slots(self, key=KEY, password=PASSWORD):
        autounlock.write_private(autounlock.credential_path(), seal(key))
        autounlock.write_private(autounlock.wrap_path(), autounlock.wrap_key(key, password))

    def ensure(self, keyring, autologin=True):
        return autounlock.ensure(keyring, autologin=autologin, wait=0, unseal=unseal)


class AutologinUsersTests(Paths):
    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        return path

    def test_gdm_automatic_and_timed_login(self):
        gdm = self.write("custom.conf", "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=nick\n"
                                        "TimedLoginEnable=1\nTimedLogin=tv\n")
        self.assertEqual(autounlock.autologin_users(gdm, self.root / "none"), {"nick", "tv"})

    def test_gdm_disabled_or_unnamed_is_nobody(self):
        for text in ("[daemon]\nAutomaticLoginEnable=False\nAutomaticLogin=nick\n",
                     "[daemon]\nAutomaticLoginEnable=true\n", "not an ini file\n"):
            gdm = self.write("custom.conf", text)
            self.assertEqual(autounlock.autologin_users(gdm, self.root / "none"), set(), text)

    def test_greetd_initial_session(self):
        greetd = self.write("config.toml", '[initial_session]\ncommand = "x"\nuser = "luma"\n')
        self.assertEqual(autounlock.autologin_users(self.root / "none", greetd), {"luma"})


class EnsureTests(Paths):
    def test_password_accounts_are_untouched(self):
        keyring = FakeKeyring(files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.NOT_AUTOLOGIN)
        self.assertEqual(keyring.created, [])

    def test_first_automatic_sign_in_makes_the_keyring_on_the_key(self):
        self.slots()
        keyring = FakeKeyring(files=self.keyrings)
        self.assertEqual(self.ensure(keyring), Result.CREATED)
        self.assertEqual(keyring.created, [KEY])

    def test_later_sign_ins_unlock_it(self):
        self.slots()
        self.keyring_file()
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertEqual(self.ensure(keyring), Result.UNLOCKED)
        self.assertFalse(keyring.locked)

    def test_not_enrolled_nothing_is_made_or_changed(self):
        self.keyring_file()
        keyring = FakeKeyring(password="theirs", files=self.keyrings)
        self.assertEqual(self.ensure(keyring), Result.NEEDS_PASSWORD)
        self.assertEqual((keyring.password, keyring.locked), ("theirs", True))

    def test_a_corrupt_seal_leaves_the_keyring_locked_and_intact(self):
        autounlock.write_private(autounlock.credential_path(), b"garbage")
        self.keyring_file()
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertEqual(self.ensure(keyring), Result.SEAL_UNUSABLE)
        self.assertEqual((keyring.password, keyring.locked), (KEY, True))
        self.assertEqual(self.keyrings.joinpath("login.keyring").read_text(), "GnomeKeyring\n")

    def test_a_corrupt_seal_never_makes_a_keyring(self):
        autounlock.write_private(autounlock.credential_path(), b"garbage")
        keyring = FakeKeyring(files=self.keyrings)
        self.assertEqual(self.ensure(keyring), Result.SEAL_UNUSABLE)
        self.assertEqual(keyring.created, [])

    def test_still_unlocks_after_automatic_sign_in_is_turned_off(self):
        self.slots()
        self.keyring_file()
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertEqual(self.ensure(keyring, False), Result.UNLOCKED)

    def test_an_unencrypted_keyring_opens_without_a_prompt(self):
        self.keyring_file("[keyring]\ndisplay-name=Login\n")
        keyring = FakeKeyring(password="", files=self.keyrings)
        self.assertEqual(self.ensure(keyring), Result.UNENCRYPTED)

    def test_no_daemon_is_not_an_error(self):
        self.assertEqual(self.ensure(FakeKeyring(running=False, files=self.keyrings)),
                         Result.NO_DAEMON)


class WrapTests(Paths):
    def test_round_trip_and_wrong_password(self):
        wrapped = autounlock.wrap_key(KEY, "pw")
        self.assertEqual(autounlock.unwrap_key(wrapped, "pw"), KEY)
        with self.assertRaises(autounlock.WrongPassword):
            autounlock.unwrap_key(wrapped, "not-pw")

    def test_real_parameters_are_stored_and_versioned(self):
        autounlock.ARGON2ID = {"memory_cost": 65536, "iterations": 3, "lanes": 4}
        record = json.loads(autounlock.wrap_key(KEY, "pw", changed=86400, now=90000))
        self.assertEqual((record["magic"], record["version"]), ("luma-login-keyring-wrap", 2))
        self.assertEqual(record["kdf_params"], {"memory_cost": 65536, "iterations": 3, "lanes": 4})
        self.assertEqual(len(base64.b64decode(record["salt"])), 16)
        self.assertEqual((record["password_changed"], record["wrapped_at"]), (86400, 90000))
        self.assertNotIn(KEY, json.dumps(record))

    def test_salts_and_nonces_are_fresh(self):
        one, two = (json.loads(autounlock.wrap_key(KEY, "pw")) for _ in range(2))
        self.assertNotEqual(one["salt"], two["salt"])
        self.assertNotEqual(one["nonce"], two["nonce"])

    def test_every_header_field_is_authenticated(self):
        record = json.loads(autounlock.wrap_key(KEY, "pw", changed=86400))
        other_salt = base64.b64encode(os.urandom(16)).decode()
        for change in ({"kdf_params": {"memory_cost": 16, "iterations": 1, "lanes": 1}},
                       {"salt": other_salt}, {"password_changed": 0}, {"wrapped_at": 1},
                       {"nonce": base64.b64encode(os.urandom(12)).decode()},
                       {"ciphertext": base64.b64encode(os.urandom(40)).decode()}):
            broken = json.dumps({**record, **change}).encode()
            with self.assertRaises(autounlock.WrongPassword, msg=str(change)):
                autounlock.unwrap_key(broken, "pw")

    def test_unknown_versions_and_unbounded_parameters_are_refused_before_work(self):
        record = json.loads(autounlock.wrap_key(KEY, "pw"))
        autounlock.ARGON2ID_BOUNDS = {"memory_cost": (19456, 1048576), "iterations": (2, 16),
                                      "lanes": (1, 16)}
        for change in ({"version": 1}, {"magic": "x"}, {"aead": "aes-128-cbc"},
                       {"kdf_params": {"memory_cost": 1 << 30, "iterations": 3, "lanes": 4}},
                       {"kdf_params": {"memory_cost": 65536, "iterations": 1000, "lanes": 4}},
                       {"kdf_params": {"memory_cost": 8, "iterations": 1, "lanes": 1}},
                       {"salt": base64.b64encode(b"short").decode()}):
            broken = json.dumps({**record, **change}).encode()
            with self.assertRaises(KeyringError, msg=str(change)):
                autounlock.unwrap_key(broken, "pw")
        with self.assertRaises(KeyringError):
            autounlock.unwrap_key(b"not json", "pw")

    def test_keys_are_256_bit_and_random(self):
        keys = {autounlock.new_key() for _ in range(8)}
        self.assertEqual(len(keys), 8)
        self.assertTrue(all(len(base64.b64decode(k)) == 32 for k in keys))


class StorageTests(Paths):
    def test_slots_are_private_atomic_and_marked_not_for_backup(self):
        self.slots()
        directory = autounlock.state_dir()
        self.assertEqual(directory, self.home / ".local" / "state" / "luma-keyring")
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for path in (autounlock.credential_path(), autounlock.wrap_path()):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["login.cred", "login.wrap"])
        try:
            value = os.getxattr(autounlock.wrap_path(), "user.xdg.robots.backup")
        except OSError:
            self.skipTest("no user xattrs on this file system")
        self.assertEqual(value, b"false")

    def test_a_planted_link_is_replaced_not_followed(self):
        directory = autounlock.state_dir()
        directory.mkdir(parents=True, mode=0o700)
        victim = self.root / "victim"
        victim.write_text("untouched")
        for name in ("login.wrap.partial", "login.wrap"):
            (directory / name).symlink_to(victim)
        autounlock.write_private(autounlock.wrap_path(), b"slot")
        self.assertEqual(victim.read_text(), "untouched")
        self.assertFalse(autounlock.wrap_path().is_symlink())
        self.assertEqual(autounlock.wrap_path().read_bytes(), b"slot")

    def test_a_linked_or_shared_state_directory_is_refused(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        autounlock.state_dir().parent.mkdir(parents=True)
        autounlock.state_dir().symlink_to(elsewhere)
        with self.assertRaises(KeyringError):
            autounlock.write_private(autounlock.wrap_path(), b"slot")
        autounlock.state_dir().unlink()
        autounlock.state_dir().mkdir(mode=0o755)
        os.chmod(autounlock.state_dir(), 0o755)
        with self.assertRaises(KeyringError):
            autounlock.write_private(autounlock.wrap_path(), b"slot")

    def test_another_accounts_slots_are_refused(self):
        other = types.SimpleNamespace(pw_uid=os.getuid() + 1, pw_gid=0, pw_dir=str(self.home))
        if os.geteuid() == 0:
            self.skipTest("root may write as another account")
        with self.assertRaises(KeyringError):
            autounlock._as_user(other, lambda: None)


class SealPolicyTests(Paths):
    def test_pcr7_only_with_full_disk_encryption(self):
        crypttab = self.root / "crypttab"
        self.assertEqual(autounlock.seal_args(crypttab), ("--with-key=auto", "--tpm2-pcrs="))
        crypttab.write_text("# none\n\n")
        self.assertEqual(autounlock.seal_args(crypttab), ("--with-key=auto", "--tpm2-pcrs="))
        crypttab.write_text("luks-1234 UUID=1234 none discard,tpm2-device=auto\n")
        self.assertEqual(autounlock.seal_args(crypttab), ("--with-key=auto", "--tpm2-pcrs=7"))


class SecretsInputTests(unittest.TestCase):
    def test_one_secret_keeps_every_character_but_one_trailing_newline(self):
        self.assertEqual(autounlock.read_secrets(io.BytesIO(b"a b\nc\n")), ["a b\nc"])
        self.assertEqual(autounlock.read_secrets(io.BytesIO(b"pw\0")), ["pw"])
        self.assertEqual(autounlock.read_secrets(io.BytesIO(b"pw")), ["pw"])

    def test_two_secrets_are_nul_terminated(self):
        self.assertEqual(autounlock.read_secrets(io.BytesIO(b"old\nx\0new\0"), 2), ["old\nx", "new"])
        with self.assertRaises(ValueError):
            autounlock.read_secrets(io.BytesIO(b"old\nnew\n"), 2)


class PasswordChangeTests(Paths):
    DAY = 20000 * 86400

    def wrap(self, changed, wrapped_at):
        autounlock.write_private(autounlock.wrap_path(),
                                 autounlock.wrap_key(KEY, PASSWORD, changed=changed, now=wrapped_at))

    def test_a_later_day_is_a_change(self):
        self.wrap(self.DAY, self.DAY + 3600)
        self.assertTrue(autounlock.wrap_is_stale(changed=self.DAY + 86400, shadow_mtime=0))

    def test_a_same_day_change_after_the_wrap_counts(self):
        self.wrap(self.DAY, self.DAY + 3600)
        self.assertTrue(autounlock.wrap_is_stale(changed=self.DAY, shadow_mtime=self.DAY + 7200))

    def test_no_change_since_the_wrap_is_not_stale(self):
        self.wrap(self.DAY, self.DAY + 3600)
        self.assertFalse(autounlock.wrap_is_stale(changed=self.DAY, shadow_mtime=self.DAY + 1800))
        # Made on a later day than the last change: same answer.
        self.wrap(self.DAY, self.DAY + 5 * 86400)
        self.assertFalse(autounlock.wrap_is_stale(changed=self.DAY, shadow_mtime=self.DAY + 6 * 86400))

    def test_pam_rewrap_needs_only_the_new_password(self):
        account = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(self.home),
                                        pw_name="me")
        self.patch(autounlock.pwd, "getpwnam", lambda user: account)
        self.slots(password="old-password")
        self.assertTrue(autounlock.pam_rewrap("me", "new-password"))
        wrapped = autounlock.wrap_path().read_bytes()
        self.assertEqual(autounlock.unwrap_key(wrapped, "new-password"), KEY)
        with self.assertRaises(autounlock.WrongPassword):
            autounlock.unwrap_key(wrapped, "old-password")
        self.assertFalse(autounlock.wrap_is_stale(changed=json.loads(wrapped)["password_changed"],
                                                  shadow_mtime=0))

    def test_pam_rewrap_leaves_accounts_without_slots_alone(self):
        account = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(self.home))
        self.patch(autounlock.pwd, "getpwnam", lambda user: account)
        self.assertTrue(autounlock.pam_rewrap("me", "new-password"))
        self.assertFalse(autounlock.state_dir().exists())

    def test_rewrap_current_needs_only_the_new_password(self):
        self.slots(password="old")
        self.assertFalse(repair.rewrap_current("not-current", unseal=unseal, verify=verify).ok)
        self.assertTrue(repair.rewrap_current(PASSWORD, unseal=unseal, verify=verify).ok)
        self.assertEqual(autounlock.unwrap_key(autounlock.wrap_path().read_bytes(), PASSWORD), KEY)

    def test_rewrap_with_old_and_new(self):
        self.slots(password="old")
        self.assertTrue(repair.rewrap("old", "new").ok)
        self.assertTrue(repair.rewrap("old", "new").ok)  # already done
        self.assertFalse(repair.rewrap("neither", "nor").ok)


class FlowTests(Paths):
    def enable(self, keyring, password=PASSWORD, seal_with=seal):
        return repair.enable_auto_unlock(password, keyring, seal=seal_with, verify=verify)

    def test_enable_moves_an_existing_keyring_onto_a_sealed_wrapped_key(self):
        self.keyring_file()
        keyring = FakeKeyring(password=PASSWORD, files=self.keyrings)
        outcome = self.enable(keyring)
        self.assertTrue(outcome.ok, outcome.message)
        key = unseal(autounlock.credential_path())
        self.assertEqual(keyring.password, key)
        self.assertNotEqual(key, PASSWORD)
        self.assertEqual(autounlock.unwrap_key(autounlock.wrap_path().read_bytes(), PASSWORD), key)
        self.assertEqual(sorted(p.name for p in autounlock.state_dir().iterdir()),
                         ["login.cred", "login.wrap"])

    def test_enable_with_a_wrong_or_mismatched_password_changes_nothing(self):
        self.keyring_file()
        keyring = FakeKeyring(password=PASSWORD, files=self.keyrings)
        self.assertFalse(self.enable(keyring, "wrong").ok)
        keyring = FakeKeyring(password="older", files=self.keyrings)
        self.assertFalse(self.enable(keyring).ok)
        self.assertEqual(keyring.password, "older")
        self.assertFalse(autounlock.state_dir().exists() and any(autounlock.state_dir().iterdir()))

    def test_a_failed_seal_changes_nothing(self):
        self.keyring_file()
        keyring = FakeKeyring(password=PASSWORD, files=self.keyrings)
        outcome = self.enable(keyring, seal_with=cannot_seal)
        self.assertFalse(outcome.ok)
        self.assertEqual(keyring.password, PASSWORD)
        self.assertFalse(autounlock.credential_path().exists())

    def test_unencrypted_only_by_explicit_opt_in(self):
        self.keyring_file()
        keyring = FakeKeyring(password=PASSWORD, files=self.keyrings)
        outcome = repair.store_unencrypted(PASSWORD, keyring, verify=verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, "")

    def test_recover_unwraps_and_seals_again(self):
        self.keyring_file()
        self.slots()
        autounlock.write_private(autounlock.credential_path(), b"from another board")
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertFalse(repair.recover("wrong", keyring, seal=seal, verify=verify).ok)
        self.assertTrue(keyring.locked)
        outcome = repair.recover(PASSWORD, keyring, seal=seal, verify=verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertFalse(keyring.locked)
        self.assertEqual(unseal(autounlock.credential_path()), KEY)

    def test_recover_with_a_corrupt_wrap_leaves_everything(self):
        self.keyring_file()
        autounlock.write_private(autounlock.wrap_path(), b"{broken")
        keyring = FakeKeyring(password=KEY, files=self.keyrings)
        self.assertFalse(repair.recover(PASSWORD, keyring, seal=seal, verify=verify).ok)
        self.assertEqual((keyring.password, keyring.locked), (KEY, True))
        self.assertTrue(self.keyrings.joinpath("login.keyring").exists())

    def test_start_again_keeps_the_old_file(self):
        self.keyring_file("old")
        keyring = FakeKeyring(password="forgotten", files=self.keyrings)
        outcome = repair.start_again_with_auto_unlock(PASSWORD, keyring, seal=seal, verify=verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(outcome.kept_file.read_text(), "old")
        self.assertEqual(keyring.created, [unseal(autounlock.credential_path())])

    def test_start_again_with_a_failed_seal_puts_the_old_file_back(self):
        self.keyring_file("old")
        keyring = FakeKeyring(password="forgotten", files=self.keyrings)
        outcome = repair.start_again_with_auto_unlock(PASSWORD, keyring, seal=cannot_seal,
                                                      verify=verify)
        self.assertFalse(outcome.ok)
        self.assertEqual(self.keyrings.joinpath("login.keyring").read_text(), "old")

    def test_back_to_the_login_password(self):
        self.keyring_file()
        self.slots()
        keyring = FakeKeyring(password=KEY, locked=False, files=self.keyrings)
        outcome = repair.use_login_password(PASSWORD, keyring, unseal=unseal, verify=verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(keyring.password, PASSWORD)
        self.assertEqual(list(autounlock.state_dir().iterdir()), [])


class CrashTests(Paths):
    """Kill the enrolment at every step; some slot must still open the keyring."""

    def crash_after(self, owner, name, calls):
        real = getattr(owner, name)
        count = {"n": 0}

        def wrapper(*args, **kwargs):
            result = real(*args, **kwargs)
            count["n"] += 1
            if count["n"] >= calls:
                raise Crash(name)
            return result

        self.patch(owner, name, wrapper)

    def crash_before(self, owner, name):
        def wrapper(*args, **kwargs):
            raise Crash(name)

        self.patch(owner, name, wrapper)

    def recovers(self, keyring):
        """After the crash: a new session, then the one-time prompt if asked."""
        keyring.reboot()
        result = self.ensure(keyring)
        if result in (Result.UNLOCKED, Result.CREATED):
            return result
        self.assertIn(result, (Result.NEEDS_PASSWORD, Result.SEAL_UNUSABLE))
        outcome = (repair.recover if result == Result.SEAL_UNUSABLE else
                   repair.enable_auto_unlock)(PASSWORD, keyring, seal=seal, verify=verify)
        self.assertTrue(outcome.ok, outcome.message)
        keyring.reboot()
        self.assertEqual(self.ensure(keyring), Result.UNLOCKED)
        return result

    def run_enable(self, keyring):
        with self.assertRaises(Crash):
            repair.enable_auto_unlock(PASSWORD, keyring, seal=seal, verify=verify)

    def setUp(self):
        super().setUp()
        self.keyring_file()
        self.keyring = FakeKeyring(password=PASSWORD, files=self.keyrings)

    def test_after_the_first_pending_slot(self):
        self.crash_after(autounlock, "write_private", 1)
        self.run_enable(self.keyring)
        self.patch(autounlock, "write_private", self._real_write)
        self.assertEqual(self.keyring.password, PASSWORD)
        self.recovers(self.keyring)

    def test_before_the_keyring_moves(self):
        self.crash_before(self.keyring, "change_password")
        self.run_enable(self.keyring)
        del self.keyring.change_password
        self.assertEqual(self.keyring.password, PASSWORD)
        self.recovers(self.keyring)

    def test_after_the_keyring_moves_before_promotion(self):
        self.crash_before(autounlock, "promote")
        self.run_enable(self.keyring)
        self.assertNotEqual(self.keyring.password, PASSWORD)
        self.assertFalse(autounlock.credential_path().exists())
        self.patch(autounlock, "promote", self._real_promote)
        self.assertEqual(self.recovers(self.keyring), Result.UNLOCKED)
        self.assertTrue(autounlock.credential_path().exists())
        self.assertFalse(autounlock.pending(autounlock.credential_path()).exists())

    def test_halfway_through_promotion(self):
        real_replace = os.replace
        calls = {"n": 0}

        def replace(src, dst):
            if str(src).endswith(".new"):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise Crash("rename")
            return real_replace(src, dst)

        self.patch(autounlock.os, "replace", replace)
        self.run_enable(self.keyring)
        self.patch(autounlock.os, "replace", real_replace)
        self.assertEqual(self.recovers(self.keyring), Result.UNLOCKED)
        self.assertEqual(autounlock.unwrap_key(autounlock.wrap_path().read_bytes(), PASSWORD),
                         self.keyring.password)

    def test_recovery_reaches_a_pending_wrap(self):
        self.crash_before(autounlock, "promote")
        self.run_enable(self.keyring)
        self.patch(autounlock, "promote", self._real_promote)
        autounlock.pending(autounlock.credential_path()).write_bytes(b"lost seal")
        self.keyring.reboot()
        self.assertEqual(self.ensure(self.keyring), Result.SEAL_UNUSABLE)
        outcome = repair.recover(PASSWORD, self.keyring, seal=seal, verify=verify)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertTrue(autounlock.wrap_path().exists())

    _real_write = staticmethod(autounlock.write_private)
    _real_promote = staticmethod(autounlock.promote)


if __name__ == "__main__":
    unittest.main()
