# SPDX-License-Identifier: MPL-2.0
"""What state the login keyring is in, said plainly.

The login keyring is unlocked during login by pam_gnome_keyring, using the
password the person typed to log in. When their account password and their
keyring password are no longer the same -- a password changed outside their
own PAM stack, an installer that made the account one way and the keyring
another -- that unlock fails and the person is asked, at every login,
forever.

A session service sees the result a moment after the session starts, so the
check waits for the keyring to settle before concluding anything: the race
between PAM's unlock and the first thing that asks is real, and calling it a
mismatch would be wrong.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from pathlib import Path

from .secrets import Keyring

__all__ = ("State", "Reading", "read", "keyring_path")

SETTLE_SECONDS = 20


class State(enum.Enum):
    NO_DAEMON = "no-daemon"
    NO_KEYRING = "no-keyring"
    UNLOCKED = "unlocked"
    LOCKED = "locked"

    @property
    def is_broken(self) -> bool:
        return self is State.LOCKED


@dataclass(frozen=True)
class Reading:
    state: State
    repairable: bool
    label: str = ""

    @property
    def sentence(self) -> str:
        """One line, for a person or a check to read."""
        if self.state is State.NO_DAEMON:
            return "No keyring is running, so nothing keeps saved passwords."
        if self.state is State.NO_KEYRING:
            return "There is no login keyring yet; one is made when it is first needed."
        if self.state is State.UNLOCKED:
            return "Your saved passwords unlock when you log in."
        if not self.repairable:
            return ("Your saved passwords are locked with an older password, and "
                    "this keyring cannot be re-keyed in place.")
        return "Your saved passwords are locked with an older password."


def keyring_path() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "keyrings" / "login.keyring"


def read(keyring: Keyring | None = None) -> Reading:
    """The state right now, without waiting for anything."""
    keyring = keyring or Keyring()
    if not keyring.running():
        return Reading(State.NO_DAEMON, repairable=False)

    collection = keyring.collection()
    if collection is None:
        # No login collection at all: nothing has been saved yet, or the
        # keyring file is not there.
        return Reading(State.NO_KEYRING, repairable=False)

    if not collection.locked:
        return Reading(State.UNLOCKED, repairable=True, label=collection.label)

    if not keyring_path().exists():
        return Reading(State.NO_KEYRING, repairable=False, label=collection.label)

    return Reading(State.LOCKED, repairable=keyring.supports_repair(),
                   label=collection.label)
