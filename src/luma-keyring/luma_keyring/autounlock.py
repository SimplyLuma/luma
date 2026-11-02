# SPDX-License-Identifier: MPL-2.0
"""The login keyring of an account that signs in automatically.

pam_gnome_keyring unlocks the login keyring with the password typed at
login. An account that signs in automatically types none, so its login
keyring stays locked and the first thing that stores a secret -- a network,
an app sign-in -- asks for it. On a television there is nobody to ask.

Every Luma account has a password, and its login keyring stays locked with
that password, exactly as pam_gnome_keyring makes it: the password remains
the way in after a board swap, a restore onto other hardware, or anything
else that makes the device unable to unlock it. gnome-keyring holds one
master password per keyring, so the device does not get a second one;
instead it keeps a copy of the account password sealed with systemd-creds,
as a user-scoped credential only this user on this machine can open, and
unlocks the keyring with it at the start of each automatic sign-in -- the
same unlock pam_gnome_keyring does with a typed password. With a TPM2 the
copy is bound to the chip and the installation (``--with-key=auto``);
without one, to the installation's host key. It is not bound to boot
measurements, so a firmware or Secure Boot database update does not lock
it out.

The copy is sealed where the password is in hand: by the installer when
"Sign me in automatically" is chosen, or once in the session (luma-keyring
asks) on a device that had automatic sign-in turned on later.

At the start of each session, before anything in it can ask for a secret:

* signs in automatically, sealed password present: open the login keyring
  with it, or make the login keyring with it on the first sign-in;
* signs in automatically, no sealed password: leave it -- the session
  service asks for the password once;
* sealed password present, but the account signs in with a password now:
  PAM unlocks the keyring again, so the sealed copy is removed.

Accounts that sign in with a password are not otherwise touched.
"""

from __future__ import annotations

import configparser
import getpass
import logging
import os
import pwd
import subprocess
import time
import tomllib
from pathlib import Path

from .secrets import Keyring, KeyringError
from .state import keyring_path

__all__ = ("autologin_users", "is_autologin", "credential_path", "seal_password",
           "unseal_password", "stored_unencrypted", "ensure", "enrol_user", "Result")

log = logging.getLogger("luma-keyring")

GDM_CONFIG = Path("/etc/gdm/custom.conf")
GREETD_CONFIG = Path("/etc/greetd/config.toml")
CREDENTIAL_NAME = "luma-login-keyring"
SYSTEMD_CREDS = "/usr/bin/systemd-creds"
DAEMON_WAIT_SECONDS = 10
SEAL_ARGS = ("--with-key=auto", "--tpm2-pcrs=")


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
    RETIRED = "retired"


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


# -- the sealed password ---------------------------------------------------------
def credential_path(home: Path | None = None) -> Path:
    """Next to the keyring it opens, so the two travel together."""
    if home is not None:
        return home / ".local" / "share" / "keyrings" / "login.cred"
    return keyring_path().with_name("login.cred")


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


def _write_private(path: Path, data: bytes, owner: tuple[int, int] | None = None) -> None:
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


def seal_password(password: str, path: Path | None = None) -> None:
    """Keep the account password sealed to this user on this machine."""
    sealed = _creds(*SEAL_ARGS, "encrypt", "-", "-", data=password.encode("utf-8"))
    _write_private(path or credential_path(), sealed)


def unseal_password(path: Path | None = None) -> str:
    return _creds("decrypt", str(path or credential_path()), "-").decode("utf-8")


def enrol_user(user: str, password: str) -> Path:
    """Seal an account's password for it, as root (the installer's step).

    The login keyring itself is made with this password at the first
    automatic sign-in, by the session's own keyring daemon, exactly as
    pam_gnome_keyring would make it at a first password login.
    """
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    keyrings = home / ".local" / "share" / "keyrings"
    for directory in (home / ".local", home / ".local" / "share", keyrings):
        if not directory.exists():
            directory.mkdir(mode=0o700 if directory == keyrings else 0o755)
            os.chown(directory, account.pw_uid, account.pw_gid)
    sealed = _creds(*SEAL_ARGS, "encrypt", "-", "-", data=password.encode("utf-8"), user=user)
    path = credential_path(home)
    _write_private(path, sealed, owner=(account.pw_uid, account.pw_gid))
    return path


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
           unseal=unseal_password, wait: float = DAEMON_WAIT_SECONDS) -> str:
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
    locked = collection is None or collection.locked

    if not autologin:
        # Signing in with a password again: PAM unlocks the keyring, and a
        # sealed copy of the password has no reason to stay.
        if locked and keyring_path().exists():
            try:
                keyring.unlock_with(unseal(credential))
            except KeyringError as error:
                log.info("the sealed password did not open the keyring: %s", error)
        credential.unlink(missing_ok=True)
        return Result.RETIRED

    if not locked:
        return Result.ALREADY_UNLOCKED

    if not credential.exists():
        if stored_unencrypted():
            # No password to open it with; no prompt either. The session
            # service offers to lock it with the account password.
            try:
                keyring.unlock_with("")
                return Result.UNENCRYPTED
            except KeyringError:
                pass
        return Result.NEEDS_PASSWORD

    try:
        password = unseal(credential)
    except KeyringError as error:
        log.warning("the sealed password could not be opened: %s", error)
        return Result.SEAL_UNUSABLE

    if not keyring_path().exists():
        keyring.create_login(password)
        return Result.CREATED

    try:
        keyring.unlock_with(password)
    except KeyringError as error:
        # The account password changed since it was sealed, or the keyring
        # was locked with another one.
        log.warning("the sealed password did not open the keyring: %s", error)
        return Result.SEAL_UNUSABLE
    return Result.UNLOCKED
