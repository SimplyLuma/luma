# SPDX-License-Identifier: MPL-2.0
"""The login keyring of an account that signs in automatically.

pam_gnome_keyring unlocks the login keyring with the password typed at
login. An account that signs in automatically types none, so its login
keyring stays locked and the first thing that stores a secret -- a network,
an app sign-in -- asks for a password the person never set. On a television
there is nobody to ask.

The device is the security boundary there, as it is on other televisions
and set-top boxes, so the keyring is keyed to the device instead of to a
password: a random key, sealed with systemd-creds as a credential only this
user on this machine can open. With a TPM2 the key is bound to the chip and
the installation (``--with-key=auto``); without one, to the installation's
host key. It is not bound to boot measurements, so a firmware or Secure
Boot database update does not lock the keyring out.

At the start of each session, before anything in it can ask for a secret:

* no login keyring yet, account signs in automatically: make one keyed to
  the sealed key;
* login keyring locked, sealed key present: open it with the key;
* login keyring with an empty password (stored unencrypted, the usual
  advice for automatic sign-in): key it to the sealed key, which needs no
  password to do;
* login keyring locked with a password and no sealed key: leave it. Nobody
  can change a keyring's password without the old one, so the session
  service offers the switch once and the person types it once.

Accounts that sign in with a password are not touched: their keyring keeps
following the login password.
"""

from __future__ import annotations

import base64
import configparser
import getpass
import logging
import os
import secrets as random
import subprocess
import time
import tomllib
from pathlib import Path

from .secrets import Keyring, KeyringError
from .state import keyring_path

__all__ = ("autologin_users", "is_autologin", "credential_path", "seal_new_key",
           "unseal_key", "ensure", "Result")

log = logging.getLogger("luma-keyring")

GDM_CONFIG = Path("/etc/gdm/custom.conf")
GREETD_CONFIG = Path("/etc/greetd/config.toml")
CREDENTIAL_NAME = "luma-login-keyring"
SYSTEMD_CREDS = "/usr/bin/systemd-creds"
DAEMON_WAIT_SECONDS = 10


class Result:
    """What ensure() did, as a word for logs and tests."""
    NOT_AUTOLOGIN = "not-autologin"
    NO_DAEMON = "no-daemon"
    ALREADY_UNLOCKED = "already-unlocked"
    CREATED = "created"
    UNLOCKED = "unlocked"
    SEALED = "sealed"
    NEEDS_PASSWORD = "needs-password"
    KEY_UNUSABLE = "key-unusable"


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


# -- the sealed key ------------------------------------------------------------
def credential_path() -> Path:
    """Next to the keyring it opens, so the two travel together."""
    return keyring_path().with_name("login.cred")


def _creds(*args: str, data: bytes | None = None) -> bytes:
    try:
        done = subprocess.run([SYSTEMD_CREDS, "--user", f"--name={CREDENTIAL_NAME}", *args],
                              input=data, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KeyringError(f"systemd-creds could not run: {error}") from None
    if done.returncode != 0:
        raise KeyringError(done.stderr.decode(errors="replace").strip()
                           or f"systemd-creds exited {done.returncode}")
    return done.stdout


def seal_new_key(path: Path | None = None) -> str:
    """A new random keyring key, sealed to this user on this machine.

    The sealed file is written before the key is used, so a keyring is never
    keyed to something that was not kept.
    """
    path = path or credential_path()
    key = base64.b64encode(random.token_bytes(32)).decode("ascii")
    sealed = _creds("--with-key=auto", "--tpm2-pcrs=", "encrypt", "-", "-",
                    data=key.encode("ascii"))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(sealed)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)
    return key


def unseal_key(path: Path | None = None) -> str:
    return _creds("decrypt", str(path or credential_path()), "-").decode("ascii").strip()


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
           seal=seal_new_key, unseal=unseal_key, wait: float = DAEMON_WAIT_SECONDS) -> str:
    """Make the login keyring open without a prompt where that is the policy."""
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
        if autologin and not credential.exists() and _stored_unencrypted():
            return _seal_empty(keyring, credential, seal)
        return Result.ALREADY_UNLOCKED

    if not keyring_path().exists():
        if not autologin:
            return Result.NOT_AUTOLOGIN
        return _create(keyring, credential, seal)

    if not credential.exists():
        if autologin and _stored_unencrypted():
            # gnome-keyring shows a plain keyring locked until it is opened.
            try:
                keyring.unlock_with("")
            except KeyringError:
                return Result.NEEDS_PASSWORD
            return _seal_empty(keyring, credential, seal)
        return Result.NEEDS_PASSWORD

    # Also after automatic sign-in was turned off: keep opening it, and let
    # the session service offer the login password instead.
    try:
        keyring.unlock_with(unseal(credential))
    except KeyringError as error:
        log.warning("the sealed keyring key could not be used: %s", error)
        return Result.KEY_UNUSABLE
    return Result.UNLOCKED


def _stored_unencrypted() -> bool:
    """gnome-keyring writes a keyring with an empty password as plain text."""
    try:
        with open(keyring_path(), "rb") as handle:
            return handle.read(9) == b"[keyring]"
    except OSError:
        return False


def _seal_empty(keyring: Keyring, credential: Path, seal) -> str:
    pending = credential.with_name(credential.name + ".new")
    try:
        key = seal(pending)
        keyring.change_password("", key)
    except KeyringError as error:
        pending.unlink(missing_ok=True)
        log.info("the unencrypted keyring was left as it is: %s", error)
        return Result.ALREADY_UNLOCKED
    pending.replace(credential)
    return Result.SEALED


def _create(keyring: Keyring, credential: Path, seal) -> str:
    try:
        key = seal(credential)
    except KeyringError as error:
        log.warning("no sealed key could be made (%s); using an empty keyring "
                    "password, as GNOME does for automatic sign-in", error)
        credential.unlink(missing_ok=True)
        key = ""
    try:
        keyring.create_login(key)
    except KeyringError:
        credential.unlink(missing_ok=True)
        raise
    return Result.CREATED
