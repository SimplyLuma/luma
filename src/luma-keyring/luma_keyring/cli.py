# SPDX-License-Identifier: MPL-2.0
"""`luma-keyring --check`: one line and an exit code, for the checks.

A login keyring that cannot be unlocked automatically is a broken state
worth reporting: the person is asked for a password at every login and
saved passwords are out of reach until they answer. Exit 0 when there is
nothing to report, 1 when there is.

`luma-keyring --autologin` is the session-start step (see autounlock): it
always exits 0, so a keyring problem never holds up the session.

`luma-keyring --enable-auto-unlock` reads the account password from
standard input, inside the person's session, and does what the window does:
the keyring keeps the password and the device keeps a sealed copy.

`luma-keyring --enrol USER` (root) reads USER's password from standard input
and seals it for them; the installer runs it when "Sign me in automatically"
is chosen, and the login keyring is made with the password at the first
automatic sign-in.
"""

from __future__ import annotations

import sys

from .state import read

__all__ = ("check", "autologin", "enable_auto_unlock", "enrol")


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


def _read_password() -> str:
    line = sys.stdin.readline()
    return line[:-1] if line.endswith("\n") else line


def enable_auto_unlock() -> int:
    from .repair import enable_auto_unlock as enable

    outcome = enable(_read_password())
    print(outcome.message)
    return 0 if outcome.ok else 1


def enrol(args: list[str]) -> int:
    from .autounlock import enrol_user
    from .secrets import KeyringError

    if len(args) != 1 or not args[0] or args[0].startswith("-"):
        print("usage: luma-keyring --enrol USER < password", file=sys.stderr)
        return 2
    password = _read_password()
    if not password:
        print("luma-keyring: no password given; nothing sealed", file=sys.stderr)
        return 1
    try:
        path = enrol_user(args[0], password)
    except (KeyError, OSError, KeyringError) as error:
        print(f"luma-keyring: nothing sealed for {args[0]}: {error}", file=sys.stderr)
        return 1
    print(f"luma-keyring: sealed the password of {args[0]} in {path}")
    return 0
