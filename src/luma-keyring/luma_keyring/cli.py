# SPDX-License-Identifier: MPL-2.0
"""`luma-keyring --check`: one line and an exit code, for the checks.

A login keyring that cannot be unlocked automatically is a broken state
worth reporting: the person is asked for a password at every login and
saved passwords are out of reach until they answer. Exit 0 when there is
nothing to report, 1 when there is.

`luma-keyring --autologin` is the session-start step (see autounlock): it
always exits 0, so a keyring problem never holds up the session.

`luma-keyring --switch-to-device-key` reads the keyring's current password
from standard input and keys the keyring to this device, for setting up an
existing account that signs in automatically from a terminal or over SSH.
"""

from __future__ import annotations

import sys

from .state import read

__all__ = ("check", "autologin", "switch_to_device_key")


def check() -> int:
    reading = read()
    print(reading.sentence)
    return 1 if reading.state.is_broken else 0


def autologin() -> int:
    from .autounlock import ensure
    from .secrets import KeyringError

    try:
        print(f"login keyring: {ensure()}")
    except KeyringError as error:
        print(f"login keyring: left as it is ({error})", file=sys.stderr)
    return 0


def switch_to_device_key() -> int:
    from .repair import switch_to_device_key as switch

    password = sys.stdin.readline().rstrip("\n")
    outcome = switch(password)
    print(outcome.message)
    return 0 if outcome.ok else 1
