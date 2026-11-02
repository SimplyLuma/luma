# SPDX-License-Identifier: MPL-2.0
"""The login keyring of an account that signs in automatically.

pam_gnome_keyring unlocks the login keyring with the password typed at
login. An account that signs in automatically types none, so its login
keyring stays locked and the first thing that stores a secret -- a network,
an app sign-in -- asks for it. On a television there is nobody to ask.

Such an account's login keyring is locked with a random keyring key (256
bits, made at enrolment), kept two ways:

Both live in the user's private state directory
(``$XDG_STATE_HOME/luma-keyring``, 0700; files 0600, written atomically).
This is envelope encryption with one key slot per unlock method, as LUKS2
keyslots or a TPM protector plus a recovery password; gnome-keyring's own
files and formats are untouched.

* sealed with systemd-creds as a user-scoped credential (``login.cred``),
  which only this user on this machine can open: bound to the TPM2 and the
  installation's host key where there is a TPM2, to the host key otherwise
  (``--with-key=auto``), and not to boot measurements, so firmware and
  Secure Boot database updates do not lock it out. Each automatic sign-in
  unseals it and unlocks the keyring with it -- the unlock pam_gnome_keyring
  does with a typed password;
* wrapped (``login.wrap``) with a key derived from the account password by
  Argon2id (RFC 9106, 64 MiB, t=3, p=4; salt and parameters stored with it)
  and AES-256-GCM, both from python-cryptography (OpenSSL). Typing the
  account password once recovers the keyring key wherever the seal no longer
  opens: another board, a restore onto new hardware, a cleared TPM.

The account password itself is never kept, in any form that can be turned
back into it, and no secret is passed in argv or the environment or logged
(systemd-creds gets them on stdin). Python cannot reliably wipe its strings;
key material lives only as long as one call.

The account password is not recoverable from these files: someone who can
open the seal -- on a device without a TPM2, anyone with root or the whole
disk -- gets the keyring key, not a password the person may use elsewhere.
Guessing the password from the wrap costs Argon2id per guess.

Enrolment happens where the password is in hand: in the installer when
"Sign me in automatically" is chosen (``enrol_user``), or once in the
session (repair.enable_auto_unlock) on devices set up earlier. Each wrap
records when the account password last changed (AccountsService, to the
day); after a change, the session service asks for the new password once
and re-wraps the key, which it can unseal without the old one.

At the start of each session, before anything in it can ask for a secret
(``ensure``):

* sealed key present: unlock the login keyring with it, or make the login
  keyring with it on the first automatic sign-in;
* signs in automatically, no sealed key: leave it -- the session service
  asks for the password once;
* a keyring with an empty password opens without one.

Accounts that sign in with a password are not touched.
"""

from __future__ import annotations

import base64
import configparser
import getpass
import json
import logging
import os
import pwd
import secrets as random
import subprocess
import time
import tomllib
from pathlib import Path

from .secrets import Keyring, KeyringError
from .state import keyring_path

__all__ = ("autologin_users", "is_autologin", "state_dir", "credential_path", "wrap_path",
           "new_key", "seal_key", "unseal_key", "wrap_key", "unwrap_key", "WrongPassword",
           "password_changed", "wrap_is_stale", "stored_unencrypted", "ensure",
           "enrol_user", "Result")

log = logging.getLogger("luma-keyring")

GDM_CONFIG = Path("/etc/gdm/custom.conf")
GREETD_CONFIG = Path("/etc/greetd/config.toml")
CREDENTIAL_NAME = "luma-login-keyring"
SYSTEMD_CREDS = "/usr/bin/systemd-creds"
DAEMON_WAIT_SECONDS = 10
SEAL_ARGS = ("--with-key=auto", "--tpm2-pcrs=")
WRAP_MAGIC = "luma-login-keyring-wrap"
WRAP_VERSION = 1
# RFC 9106 section 4, second recommended option: 64 MiB, t=3, p=4 (about
# 0.25 s on a Raspberry Pi 5). Stored with each wrap, so it can be raised.
ARGON2ID = {"memory_cost": 65536, "iterations": 3, "lanes": 4}


class Result:
    """What ensure() did, as a word for logs and tests."""
    NOT_AUTOLOGIN = "not-autologin"
    NO_DAEMON = "no-daemon"
    ALREADY_UNLOCKED = "already-unlocked"
    CREATED = "created"
    UNLOCKED = "unlocked"
    UNENCRYPTED = "unencrypted"
    NEEDS_PASSWORD = "needs-password"
    SEAL_UNUSABLE = "seal-unusable"


class WrongPassword(Exception):
    """The password does not unwrap the keyring key."""


# -- who signs in automatically ----------------------------------------------
def _true(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes")


def autologin_users(gdm: Path | None = None, greetd: Path | None = None) -> set[str]:
    """Accounts the display manager signs in without a password."""
    users: set[str] = set()

    gdm = gdm or GDM_CONFIG
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(gdm, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError) as error:
        log.info("could not read %s: %s", gdm, error)
    if parser.has_section("daemon"):
        daemon = parser["daemon"]
        for enable, user in (("AutomaticLoginEnable", "AutomaticLogin"),
                             ("TimedLoginEnable", "TimedLogin")):
            name = (daemon.get(user) or "").strip()
            if _true(daemon.get(enable)) and name:
                users.add(name)

    greetd = greetd or GREETD_CONFIG
    try:
        with open(greetd, "rb") as handle:
            initial = tomllib.load(handle).get("initial_session") or {}
        name = str(initial.get("user") or "").strip()
        if name:
            users.add(name)
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as error:
        log.info("could not read %s: %s", greetd, error)

    return users


def is_autologin(user: str | None = None, **paths) -> bool:
    return (user or getpass.getuser()) in autologin_users(**paths)


# -- the keyring key --------------------------------------------------------------
def state_dir(home: Path | None = None) -> Path:
    """The user's private state directory for this (0700)."""
    if home is not None:
        return home / ".local" / "state" / "luma-keyring"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "luma-keyring"


def credential_path(home: Path | None = None) -> Path:
    return state_dir(home) / "login.cred"


def wrap_path(home: Path | None = None) -> Path:
    return state_dir(home) / "login.wrap"


def new_key() -> str:
    """A random keyring key, as the text gnome-keyring takes as a password."""
    return base64.b64encode(random.token_bytes(32)).decode("ascii")


def _creds(*args: str, data: bytes | None = None, user: str | None = None) -> bytes:
    scope = [f"--uid={user}"] if user else ["--user"]
    try:
        done = subprocess.run([SYSTEMD_CREDS, *scope, f"--name={CREDENTIAL_NAME}", *args],
                              input=data, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KeyringError(f"systemd-creds could not run: {error}") from None
    if done.returncode != 0:
        raise KeyringError(done.stderr.decode(errors="replace").strip()
                           or f"systemd-creds exited {done.returncode}")
    return done.stdout


def write_private(path: Path, data: bytes, owner: tuple[int, int] | None = None) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if owner is not None:
        os.chown(partial, *owner)
    os.replace(partial, path)


def seal_key(key: str, user: str | None = None) -> bytes:
    """The keyring key sealed to this user on this machine (not yet written)."""
    return _creds(*SEAL_ARGS, "encrypt", "-", "-", data=key.encode("ascii"), user=user)


def unseal_key(path: Path | None = None) -> str:
    return _creds("decrypt", str(path or credential_path()), "-").decode("ascii").strip()


def _kdf(password: str, salt: bytes, params: dict):
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    return Argon2id(salt=salt, length=32, iterations=int(params["iterations"]),
                    lanes=int(params["lanes"]),
                    memory_cost=int(params["memory_cost"])).derive(password.encode("utf-8"))


def password_changed(user: str | None = None) -> int | None:
    """When the account password last changed (Unix time, to the day).

    Read from AccountsService in a session, or from /etc/shadow as root (the
    installer). A wrap that predates it was made with an older password.
    """
    if user is not None:
        try:
            with open("/etc/shadow", encoding="utf-8") as shadow:
                for line in shadow:
                    fields = line.split(":")
                    if fields[0] == user and fields[2].isdigit():
                        return int(fields[2]) * 86400
        except OSError:
            pass
        return None
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        path = bus.call_sync("org.freedesktop.Accounts", "/org/freedesktop/Accounts",
                             "org.freedesktop.Accounts", "FindUserById",
                             GLib.Variant("(x)", (os.getuid(),)), GLib.VariantType.new("(o)"),
                             Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        policy = bus.call_sync("org.freedesktop.Accounts", path,
                               "org.freedesktop.Accounts.User", "GetPasswordExpirationPolicy",
                               None, GLib.VariantType.new("(xxxxxx)"),
                               Gio.DBusCallFlags.NONE, 5000, None).unpack()
    except Exception as error:  # noqa: BLE001 - no AccountsService is an answer
        log.info("could not ask when the password changed: %s", error)
        return None
    return int(policy[1]) if policy[1] > 0 else None


def wrap_changed(wrapped: bytes) -> int | None:
    """The password-change time a wrap was made after (not secret)."""
    try:
        value = json.loads(wrapped).get("password_changed")
    except (ValueError, AttributeError):
        return None
    return int(value) if isinstance(value, int) else None


def wrap_is_stale(path: Path | None = None) -> bool:
    try:
        made_after = wrap_changed((path or wrap_path()).read_bytes())
    except OSError:
        return False
    changed = password_changed()
    return changed is not None and (made_after is None or changed > made_after)


def wrap_key(key: str, password: str, params: dict | None = None,
             changed: int | None = None) -> bytes:
    """The keyring key, encrypted under a key derived from the account password."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    params = dict(params or ARGON2ID)
    salt, nonce = random.token_bytes(16), random.token_bytes(12)
    sealed = AESGCM(_kdf(password, salt, params)).encrypt(
        nonce, key.encode("ascii"), _aad(WRAP_VERSION))
    record = {"magic": WRAP_MAGIC, "version": WRAP_VERSION,
              "kdf": "argon2id", "kdf_params": params, "aead": "aes-256-gcm",
              "salt": base64.b64encode(salt).decode("ascii"),
              "nonce": base64.b64encode(nonce).decode("ascii"),
              "ciphertext": base64.b64encode(sealed).decode("ascii"),
              "password_changed": changed}
    return (json.dumps(record, indent=1, sort_keys=True) + "\n").encode("ascii")


def _aad(version: int) -> bytes:
    """The header is authenticated with the key, so it cannot be swapped."""
    return f"{WRAP_MAGIC}/{version}".encode("ascii")


def unwrap_key(wrapped: bytes, password: str) -> str:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        record = json.loads(wrapped)
        if (record.get("magic") != WRAP_MAGIC or record.get("version") != WRAP_VERSION
                or record.get("kdf") != "argon2id" or record.get("aead") != "aes-256-gcm"):
            raise ValueError("unknown format or version")
        salt = base64.b64decode(record["salt"])
        nonce = base64.b64decode(record["nonce"])
        sealed = base64.b64decode(record["ciphertext"])
        derived = _kdf(password, salt, record["kdf_params"])
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise KeyringError(f"the recovery file cannot be read: {error}") from None
    try:
        return AESGCM(derived).decrypt(nonce, sealed, _aad(WRAP_VERSION)).decode("ascii")
    except InvalidTag:
        raise WrongPassword() from None


def enrol_user(user: str, password: str) -> Path:
    """The installer's step, as root: a keyring key sealed and wrapped for USER.

    The login keyring itself is made with the key at the first automatic
    sign-in, by the session's own keyring daemon.
    """
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    for directory in (home / ".local", home / ".local" / "state", state_dir(home)):
        if not directory.exists():
            directory.mkdir(mode=0o755 if directory == home / ".local" else 0o700)
            os.chown(directory, account.pw_uid, account.pw_gid)
    key = new_key()
    owner = (account.pw_uid, account.pw_gid)
    write_private(wrap_path(home), wrap_key(key, password, changed=password_changed(user)),
                  owner=owner)
    write_private(credential_path(home), seal_key(key, user=user), owner=owner)
    return credential_path(home)


def stored_unencrypted() -> bool:
    """gnome-keyring writes a keyring with an empty password as plain text."""
    try:
        with open(keyring_path(), "rb") as handle:
            return handle.read(9) == b"[keyring]"
    except OSError:
        return False


# -- the session-start step ----------------------------------------------------
def _wait_for_daemon(keyring: Keyring, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if keyring.running():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def ensure(keyring: Keyring | None = None, autologin: bool | None = None,
           unseal=unseal_key, wait: float = DAEMON_WAIT_SECONDS) -> str:
    """Open the login keyring without a prompt where that is the policy."""
    autologin = is_autologin() if autologin is None else autologin
    credential = credential_path()
    if not autologin and not credential.exists():
        return Result.NOT_AUTOLOGIN

    keyring = keyring or Keyring()
    if not _wait_for_daemon(keyring, wait):
        keyring.activate()
        if not _wait_for_daemon(keyring, 2):
            return Result.NO_DAEMON

    collection = keyring.collection()
    if collection is not None and not collection.locked:
        return Result.ALREADY_UNLOCKED

    if credential.exists():
        # Also after automatic sign-in is turned off: the keyring is on the
        # keyring key, not the password, until the person moves it back.
        try:
            key = unseal(credential)
        except KeyringError as error:
            log.warning("the sealed keyring key could not be opened: %s", error)
            return Result.SEAL_UNUSABLE
        if not keyring_path().exists():
            if not autologin:
                return Result.NOT_AUTOLOGIN
            keyring.create_login(key)
            return Result.CREATED
        try:
            keyring.unlock_with(key)
        except KeyringError as error:
            log.warning("the sealed keyring key did not open the keyring: %s", error)
            return Result.SEAL_UNUSABLE
        return Result.UNLOCKED

    if stored_unencrypted():
        # No password to open it with, and no prompt either; the session
        # service offers to enrol it.
        try:
            keyring.unlock_with("")
            return Result.UNENCRYPTED
        except KeyringError:
            pass
    return Result.NEEDS_PASSWORD
