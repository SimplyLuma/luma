# SPDX-License-Identifier: MPL-2.0
"""The two repairs, and what they cost.

Repairing in place keeps everything that is stored and asks for the old
keyring password to do it. Starting again keeps nothing but the file: the
old keyring is moved aside with the date on it, so a password remembered
next week still opens it, and a new login keyring is made with the account
password so it unlocks by itself from now on.

On an account that signs in automatically the keyring is on a random
keyring key, sealed for the device and wrapped with the account password
(see autounlock). Where the installer did not enrol it, the password is
asked for once here; where the seal stopped opening, the password unwraps
the key and it is sealed again. A password change re-wraps it.

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

__all__ = ("Outcome", "repair_in_place", "start_again", "enable_auto_unlock",
           "recover", "rewrap", "rewrap_current", "start_again_with_auto_unlock", "use_login_password")

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




_UNLOCKS_BY_ITSELF = ("Your saved passwords are unlocked, and will unlock by "
                      "themselves from now on. Your password still recovers them "
                      "on other hardware.")


def _enrol(password: str, keyring: Keyring, rekey, seal=None) -> Outcome:
    """Put the keyring on a new keyring key, sealed and wrapped.

    `rekey(key)` moves the keyring onto `key`. The sealed and wrapped copies
    are written first under temporary names and put in place only once the
    keyring has moved, so it is never on a key nothing keeps. Where nothing
    can be sealed, the keyring goes onto an empty password instead.
    """
    key = autounlock.new_key()
    try:
        sealed = (seal or autounlock.seal_key)(key)
    except KeyringError as error:
        log.warning("nothing can be sealed on this device: %s", error)
        try:
            rekey("")
        except KeyringError as change_error:
            return Outcome(False, f"The keyring could not be changed: {change_error}")
        return Outcome(True, "Your saved passwords are unlocked and will unlock "
                             "by themselves. This device cannot keep a sealed "
                             "key, so they are stored without encryption.")

    credential, wrap = autounlock.credential_path(), autounlock.wrap_path()
    pending_credential = credential.with_name(credential.name + ".new")
    pending_wrap = wrap.with_name(wrap.name + ".new")
    autounlock.write_private(pending_wrap, autounlock.wrap_key(
        key, password, changed=autounlock.password_changed()))
    autounlock.write_private(pending_credential, sealed)
    try:
        rekey(key)
    except KeyringError:
        pending_credential.unlink(missing_ok=True)
        pending_wrap.unlink(missing_ok=True)
        raise
    pending_wrap.replace(wrap)
    pending_credential.replace(credential)
    try:
        keyring.unlock_with(key)
    except KeyringError as error:
        log.info("the keyring did not open straight away: %s", error)
    return Outcome(True, _UNLOCKS_BY_ITSELF)


def enable_auto_unlock(password: str, keyring: Keyring | None = None,
                       seal=None, verify=None) -> Outcome:
    """Asked once on an account that signs in automatically."""
    keyring = keyring or Keyring()
    wrong = _login_password_is_right(password, verify)
    if wrong is not None:
        return wrong

    path = keyring_path()
    if not path.exists():
        def rekey(key):
            keyring.create_login(key)
    elif autounlock.stored_unencrypted():
        def rekey(key):
            keyring.unlock_with("")
            keyring.change_password("", key)
    else:
        def rekey(key):
            keyring.change_password(password, key)

    try:
        return _enrol(password, keyring, rekey, seal)
    except KeyringError:
        return Outcome(False, "Your saved passwords are locked with a different "
                              "password than your login password. Repair them "
                              "first, or start a new keyring.")


def recover(password: str, keyring: Keyring | None = None, seal=None) -> Outcome:
    """The seal no longer opens: the account password unwraps the key."""
    keyring = keyring or Keyring()
    try:
        wrapped = autounlock.wrap_path().read_bytes()
    except OSError:
        return Outcome(False, "This device has no way to recover them without "
                              "the key it lost. You can start a new keyring.")
    try:
        key = autounlock.unwrap_key(wrapped, password)
    except autounlock.WrongPassword:
        return Outcome(False, "That password does not open them. If you changed "
                              "your password outside Settings, type the previous one.")
    except KeyringError as error:
        return Outcome(False, str(error))
    try:
        keyring.unlock_with(key)
    except KeyringError as error:
        return Outcome(False, f"The keyring did not open: {error}")
    try:
        autounlock.write_private(autounlock.credential_path(), (seal or autounlock.seal_key)(key))
    except KeyringError as error:
        return Outcome(True, "Your saved passwords are unlocked, but this device "
                             f"could not keep their key ({error}), so it will ask again.")
    return Outcome(True, _UNLOCKS_BY_ITSELF)


def rewrap(old_password: str, new_password: str) -> Outcome:
    """A password change: the recovery copy follows the new password."""
    path = autounlock.wrap_path()
    try:
        wrapped = path.read_bytes()
    except OSError:
        return Outcome(True, "There is no recovery copy to change.")
    try:
        key = autounlock.unwrap_key(wrapped, old_password)
    except autounlock.WrongPassword:
        try:
            autounlock.unwrap_key(wrapped, new_password)
        except autounlock.WrongPassword:
            return Outcome(False, "The recovery copy is locked with another password.")
        return Outcome(True, "The recovery copy already uses the new password.")
    except KeyringError as error:
        return Outcome(False, str(error))
    autounlock.write_private(path, autounlock.wrap_key(
        key, new_password, changed=autounlock.password_changed()))
    return Outcome(True, "The recovery copy now uses the new password.")


def rewrap_current(password: str, unseal=None, verify=None) -> Outcome:
    """After a password change: wrap the key, unsealed here, with the new password.

    The old password is not needed: the seal opens the key on this device.
    """
    wrong = _login_password_is_right(password, verify)
    if wrong is not None:
        return wrong
    try:
        key = (unseal or autounlock.unseal_key)(autounlock.credential_path())
    except KeyringError as error:
        return Outcome(False, f"This device could not open its key: {error}")
    autounlock.write_private(autounlock.wrap_path(), autounlock.wrap_key(
        key, password, changed=autounlock.password_changed()))
    return Outcome(True, "Your password can recover your saved passwords again.")


def start_again_with_auto_unlock(login_password: str, keyring: Keyring | None = None,
                                 now: datetime.datetime | None = None,
                                 seal=None, verify=None) -> Outcome:
    """A new login keyring on a new keyring key, with the old one kept aside."""
    keyring = keyring or Keyring()
    wrong = _login_password_is_right(login_password, verify)
    if wrong is not None:
        return wrong
    path = keyring_path()
    kept = _keep_aside(path, now)
    try:
        outcome = _enrol(login_password, keyring, lambda key: keyring.create_login(key), seal)
    except KeyringError as error:
        if kept is not None:
            kept.rename(path)
        return Outcome(False, f"A new keyring could not be made: {error}")
    return Outcome(outcome.ok, "A new keyring is in use. " + outcome.message +
                   " The old one is kept in case you remember its password.",
                   kept_file=kept)


def use_login_password(login_password: str, keyring: Keyring | None = None,
                       unseal=None, verify=None) -> Outcome:
    """Automatic sign-in was turned off: back onto the login password."""
    keyring = keyring or Keyring()
    wrong = _login_password_is_right(login_password, verify)
    if wrong is not None:
        return wrong
    try:
        key = (unseal or autounlock.unseal_key)(autounlock.credential_path())
        keyring.change_password(key, login_password)
    except KeyringError as error:
        return Outcome(False, f"The keyring could not be changed: {error}")
    autounlock.credential_path().unlink(missing_ok=True)
    autounlock.wrap_path().unlink(missing_ok=True)
    return Outcome(True, "Your saved passwords now unlock with your login "
                         "password, and only with it.")
