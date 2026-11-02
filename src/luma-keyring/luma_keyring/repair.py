# SPDX-License-Identifier: MPL-2.0
"""The two repairs, and what they cost.

Repairing in place keeps everything that is stored and asks for the old
keyring password to do it. Starting again keeps nothing but the file: the
old keyring is moved aside with the date on it, so a password remembered
next week still opens it, and a new login keyring is made with the account
password so it unlocks by itself from now on.

On an account that signs in automatically there is no login password to
re-key to; the keyring is keyed to the device instead (see autounlock), and
the old password is asked for once to do it. When automatic sign-in is
turned off again, the keyring can go back to the login password.

None of these is done without the person choosing it, and none is claimed
to do more than it does.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path

from .login import PamUnavailable, check_login_password
from .secrets import Keyring, KeyringError
from .state import keyring_path
from . import autounlock

__all__ = ("Outcome", "repair_in_place", "start_again", "switch_to_device_key",
           "start_again_with_device_key", "use_login_password")

log = logging.getLogger("luma-keyring")


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str
    kept_file: Path | None = None


def _login_password_is_right(login_password: str, verify=None) -> Outcome | None:
    """None when the password is right; an Outcome to show when it is not.

    `verify` exists so the acceptance tests can stand in for PAM, which
    cannot check a password that no account has.
    """
    try:
        if (verify or check_login_password)(login_password):
            return None
    except PamUnavailable as error:
        log.info("could not check the login password: %s", error)
        # Better to say so than to re-key to something unverified and lock
        # the person out at their next login.
        return Outcome(False,
                       "Luma could not check your login password on this "
                       "system, so it has changed nothing.")
    return Outcome(False, "That is not your current login password.")


def repair_in_place(old_password: str, login_password: str,
                    keyring: Keyring | None = None, verify=None) -> Outcome:
    """Re-key the login keyring to the account password, keeping its contents."""
    keyring = keyring or Keyring()

    wrong = _login_password_is_right(login_password, verify)
    if wrong is not None:
        return wrong

    try:
        keyring.change_password(old_password, login_password)
    except KeyringError as error:
        text = str(error).lower()
        if "match" in text or "wrong" in text or "denied" in text or "password" in text:
            return Outcome(False, "That is not the password your saved "
                                  "passwords were locked with.")
        return Outcome(False, f"The keyring could not be changed: {error}")

    try:
        keyring.unlock_with(login_password)
    except KeyringError as error:
        # Re-keyed but still locked: say so plainly rather than promising.
        return Outcome(True, "Your saved passwords now use your login "
                             f"password, and will unlock at your next login ({error}).")

    return Outcome(True, "Your saved passwords are unlocked, and will unlock "
                         "by themselves from now on.")


def _keep_aside(path: Path, now: datetime.datetime | None) -> Path | None:
    if not path.exists():
        return None
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d")
    kept = path.with_name(f"{path.name}.kept-{stamp}")
    count = 1
    while kept.exists():
        count += 1
        kept = path.with_name(f"{path.name}.kept-{stamp}-{count}")
    path.rename(kept)
    return kept


def start_again(login_password: str, keyring: Keyring | None = None,
                now: datetime.datetime | None = None, verify=None) -> Outcome:
    """A new login keyring, with the old one kept aside."""
    keyring = keyring or Keyring()

    wrong = _login_password_is_right(login_password, verify)
    if wrong is not None:
        return wrong

    path = keyring_path()
    kept = _keep_aside(path, now)

    try:
        keyring.create_login(login_password)
    except KeyringError as error:
        if kept is not None:
            kept.rename(path)
        return Outcome(False, f"A new keyring could not be made: {error}")

    return Outcome(True,
                   "A new keyring is in use, and unlocks when you log in. The "
                   "old one is kept in case you remember its password.",
                   kept_file=kept)


def _pending_credential() -> tuple[Path, Path]:
    final = autounlock.credential_path()
    return final, final.with_name(final.name + ".new")


def switch_to_device_key(old_password: str, keyring: Keyring | None = None,
                         seal=None) -> Outcome:
    """Key the login keyring to this device, keeping its contents.

    For an account that signs in automatically: asked once for the password
    the keyring is locked with, it opens by itself from then on.
    """
    keyring = keyring or Keyring()
    final, pending = _pending_credential()
    try:
        key = (seal or autounlock.seal_new_key)(pending)
    except KeyringError as error:
        return Outcome(False, f"This device could not keep a key for the keyring: {error}")

    try:
        keyring.change_password(old_password, key)
    except KeyringError as error:
        pending.unlink(missing_ok=True)
        text = str(error).lower()
        if "match" in text or "wrong" in text or "denied" in text or "password" in text:
            return Outcome(False, "That is not the password your saved "
                                  "passwords are locked with.")
        return Outcome(False, f"The keyring could not be changed: {error}")
    pending.replace(final)

    try:
        keyring.unlock_with(key)
    except KeyringError as error:
        return Outcome(True, "Your saved passwords will unlock by themselves "
                             f"from your next sign-in ({error}).")
    return Outcome(True, "Your saved passwords are unlocked, and will unlock "
                         "by themselves from now on.")


def start_again_with_device_key(keyring: Keyring | None = None,
                                now: datetime.datetime | None = None,
                                seal=None) -> Outcome:
    """A new login keyring keyed to this device, with the old one kept aside."""
    keyring = keyring or Keyring()
    final, pending = _pending_credential()
    try:
        key = (seal or autounlock.seal_new_key)(pending)
    except KeyringError as error:
        return Outcome(False, f"This device could not keep a key for the keyring: {error}")

    path = keyring_path()
    kept = _keep_aside(path, now)
    try:
        keyring.create_login(key)
    except KeyringError as error:
        pending.unlink(missing_ok=True)
        if kept is not None:
            kept.rename(path)
        return Outcome(False, f"A new keyring could not be made: {error}")
    pending.replace(final)
    return Outcome(True,
                   "A new keyring is in use, and unlocks by itself. The old one "
                   "is kept in case you remember its password.",
                   kept_file=kept)


def use_login_password(login_password: str, keyring: Keyring | None = None,
                       unseal=None, verify=None) -> Outcome:
    """Back from the device key to the login password, keeping the contents.

    For an account that no longer signs in automatically.
    """
    keyring = keyring or Keyring()
    wrong = _login_password_is_right(login_password, verify)
    if wrong is not None:
        return wrong

    final = autounlock.credential_path()
    try:
        key = (unseal or autounlock.unseal_key)(final)
        keyring.change_password(key, login_password)
    except KeyringError as error:
        return Outcome(False, f"The keyring could not be changed: {error}")
    final.unlink(missing_ok=True)
    return Outcome(True, "Your saved passwords now unlock with your login "
                         "password, and only with it.")
