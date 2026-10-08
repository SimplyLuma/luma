# SPDX-License-Identifier: MPL-2.0
"""`luma-keyring --check`: one line and an exit code, for the checks.

A login keyring that cannot be unlocked automatically is a broken state
worth reporting: the person is asked for a password at every login and
saved passwords are out of reach until they answer. Exit 0 when there is
nothing to report, 1 when there is.
"""

from __future__ import annotations

from .state import read

__all__ = ("check",)


def check() -> int:
    reading = read()
    print(reading.sentence)
    return 1 if reading.state.is_broken else 0
