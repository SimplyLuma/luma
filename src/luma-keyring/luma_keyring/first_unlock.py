# SPDX-License-Identifier: MPL-2.0
"""The first unlock on an account that signs in automatically.

A device the installer did not enrol (set up before luma-keyring 0.2, or
automatic sign-in turned on later) has a login keyring locked with the
account password and nothing that opens it at sign-in. Rather than let the
first app that wants a secret raise gnome-keyring's bare prompt, Luma asks
once, itself, at the start of the session: "Unlock your saved passwords",
a password, and "Unlock automatically on this TV", on by default.

The password is checked against the keyring itself (it unlocks it, through
the Secret Service daemon). With the switch on, the existing enrolment then
puts the keyring on a key sealed to this device (see autounlock and repair),
so later sessions open without a question; that step checks the password
with PAM too and fails closed. With the switch off, only this session is
unlocked and the next one asks again.

Nothing here keeps the password: it is passed to the calls that need it.
GTK-free, so the policy and the outcomes are tested without a display.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

from . import autounlock, repair
from .secrets import Keyring, KeyringError
from .state import State, read

__all__ = ("Kind", "Result", "should_ask", "unlock", "set_automatic", "is_automatic",
           "enrol_with_login_password", "login_password_differs",
           "device_noun")

log = logging.getLogger("luma-keyring")


class Kind(enum.Enum):
    WRONG_PASSWORD = "wrong-password"
    UNAVAILABLE = "unavailable"
    UNLOCKED = "unlocked"            # this session only, as asked
    AUTOMATIC = "automatic"          # unlocked, and from now on by itself
    UNLOCKED_ONLY = "unlocked-only"  # unlocked, but automatic unlock could not be set up
    NOT_CHANGED = "not-changed"      # already unlocked; the password did not enrol it
    NEEDS_LOGIN_PASSWORD = "needs-login-password"  # unlocked with an older password: one more step
    CHANGED = "changed"              # automatic unlock turned on or off from the window


@dataclass(frozen=True)
class Result:
    kind: Kind
    message: str = ""

    @property
    def unlocked(self) -> bool:
        return self.kind in (Kind.UNLOCKED, Kind.AUTOMATIC, Kind.UNLOCKED_ONLY, Kind.CHANGED)


def is_automatic() -> bool:
    """Whether a device seal (or one being written) exists for this account."""
    credential = autounlock.credential_path()
    return credential.exists() or autounlock.pending(credential).exists()


def should_ask(keyring: Keyring | None = None, autologin: bool | None = None) -> bool:
    """Whether the session starts with Luma's sheet.

    An account that signs in automatically, has no device seal and whose
    login keyring is locked: the one case nothing else opens. A sealed
    account whose seal stopped opening is the recovery path (a notification),
    and a keyring that does not exist yet is made when first needed.
    """
    autologin = autounlock.is_autologin() if autologin is None else autologin
    if not autologin or is_automatic():
        return False
    reading = read(keyring)
    return reading.state is State.LOCKED


def unlock(password: str, automatic: bool, keyring: Keyring | None = None,
           seal=None, verify=None) -> Result:
    """The sheet's Unlock: open the keyring with `password`, then enrol if asked."""
    keyring = keyring or Keyring()
    if not password:
        return Result(Kind.WRONG_PASSWORD, "Type your password.")
    collection = keyring.collection()
    if collection is None:
        return Result(Kind.UNAVAILABLE, "Your saved passwords aren't available right now.")
    was_locked = collection.locked
    if was_locked:
        try:
            keyring.unlock_with(password)
        except KeyringError as error:
            # Wrong password, or the daemon went away: which one decides the words.
            if keyring.collection() is None:
                log.info("the keyring went away while unlocking: %s", error)
                return Result(Kind.UNAVAILABLE, "Your saved passwords aren't available right now.")
            return Result(Kind.WRONG_PASSWORD, "That password didn't unlock them. Try again.")
    else:
        # Already open (unlocked earlier this session): an unlock proves nothing, so the
        # password is checked against the keyring itself, which re-keys it to the same one.
        try:
            keyring.change_password(password, password)
        except KeyringError as error:
            if keyring.collection() is None:
                log.info("the keyring went away while checking: %s", error)
                return Result(Kind.UNAVAILABLE, "Your saved passwords aren't available right now.")
            return Result(Kind.WRONG_PASSWORD, "That isn't the password your saved passwords use. Try again.")
    if not automatic:
        return Result(Kind.UNLOCKED)
    if login_password_differs(password, verify):
        # The keyring opened with an older password than the one this account signs in
        # with: the recovery copy must be wrapped with the current one, so ask for it.
        return Result(Kind.NEEDS_LOGIN_PASSWORD, "")
    # Enrolling checks the password again, with PAM and against the keyring
    # (it re-keys from it): on an unlocked keyring that is the only check.
    outcome = repair.enable_auto_unlock(password, keyring, seal=seal, verify=verify)
    if outcome.ok:
        # Only the weak-password advice is worth saying after the sheet closes.
        return Result(Kind.AUTOMATIC, repair.password_quality_note(password).strip())
    log.info("automatic unlock was not set up: %s", outcome.message)
    if not was_locked:
        return Result(Kind.NOT_CHANGED, outcome.message)
    return Result(Kind.UNLOCKED_ONLY, "They're unlocked for now, but can't unlock "
                                      "automatically yet. " + outcome.message)


def login_password_differs(password: str, verify=None) -> bool:
    """Whether PAM says this is not the account's current password (False when PAM cannot say)."""
    from .login import PamUnavailable, check_login_password

    try:
        return not (verify or check_login_password)(password)
    except PamUnavailable:
        return False


def enrol_with_login_password(login_password: str, keyring_password: str,
                              keyring: Keyring | None = None, seal=None, verify=None) -> Result:
    """The second step: the keyring (open, on `keyring_password`) onto a sealed key,
    its recovery copy wrapped with the current login password."""
    keyring = keyring or Keyring()
    if not login_password:
        return Result(Kind.WRONG_PASSWORD, "Type the password you sign in with.")
    if login_password_differs(login_password, verify):
        return Result(Kind.WRONG_PASSWORD, "That isn't the password you sign in with. Try again.")
    outcome = repair.enable_auto_unlock(login_password, keyring, seal=seal, verify=verify,
                                        keyring_password=keyring_password)
    if outcome.ok:
        return Result(Kind.AUTOMATIC, repair.password_quality_note(login_password).strip())
    return Result(Kind.UNLOCKED_ONLY, "They're unlocked for now, but can't unlock automatically yet. "
                                      + outcome.message)


def set_automatic(on: bool, password: str, keyring: Keyring | None = None,
                  seal=None, unseal=None, verify=None) -> Result:
    """The window's switch. Either way needs the password once.

    On: unlock with it where locked, then enrol. Off: the keyring goes back
    onto the login password and both slots are removed (use_login_password);
    removing only the seal would leave it on a key that nothing but the
    recovery copy opens.
    """
    keyring = keyring or Keyring()
    if not password:
        return Result(Kind.WRONG_PASSWORD, "Type your password.")
    if on:
        result = unlock(password, True, keyring, seal=seal, verify=verify)
        if result.kind is Kind.AUTOMATIC:
            return Result(Kind.CHANGED, result.message)
        return result
    outcome = repair.use_login_password(password, keyring, unseal=unseal, verify=verify)
    if outcome.ok:
        return Result(Kind.CHANGED, "Your saved passwords now unlock with your password.")
    return Result(Kind.WRONG_PASSWORD, outcome.message)


def device_noun() -> str:
    """ "this TV", "this computer"...: LumaUI's device_noun().

    A LumaUI that predates device_noun() (the TV layer brings it) is read the
    same way it reads the posture, so the copy is right on a TV either way.
    """
    try:
        from luma_appkit.lumaui import device_noun as lumaui_noun
    except ImportError:
        lumaui_noun = None
    if lumaui_noun is not None:
        try:
            return lumaui_noun()
        except Exception as error:  # noqa: BLE001 - copy must never fail the sheet
            log.info("device_noun failed: %s", error)
    return "this TV" if _tv_posture() else "this computer"


def _tv_posture() -> bool:
    """LumaUI's own rule (context.system_posture): LUMA_POSTURE, then the
    shell-state posture, then a device that says it is a television."""
    import os
    from pathlib import Path

    forced = os.environ.get("LUMA_POSTURE", "").strip().lower()
    if forced:
        return forced == "tv"
    selected = "automatic"
    try:
        from gi.repository import Gio

        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.project_luma.shell-state", True) if source else None
        if schema is not None and schema.has_key("posture"):
            selected = Gio.Settings.new("org.project_luma.shell-state").get_string("posture")
    except Exception as error:  # noqa: BLE001
        log.info("could not read the posture: %s", error)
    if selected == "tv":
        return True
    try:
        device = Path("/etc/luma-device-class").read_text(encoding="utf-8").strip().lower()
    except (OSError, UnicodeError):
        device = ""
    return selected == "automatic" and device == "tv"
