# SPDX-License-Identifier: MPL-2.0
"""`luma-keyring --check`: one line and an exit code, for the checks.

A login keyring that cannot be unlocked automatically is a broken state
worth reporting: the person is asked for a password at every login and
saved passwords are out of reach until they answer. Exit 0 when there is
nothing to report, 1 when there is.

`luma-keyring --autologin` is the session-start step (see autounlock): it
always exits 0, so a keyring problem never holds up the session.

The rest read secrets from standard input, never the command line or the
environment: one secret is the whole input less one trailing newline or
NUL; two are each NUL-terminated. They switch core dumps off first.

* `--enable-auto-unlock` (in the person's session): what the one-time
  question does on a device set up earlier. `--unencrypted` is the explicit
  opt-in to an empty keyring password on a device that cannot seal.
* `--recover` (in the person's session): unwrap the keyring key with the
  account password where the seal no longer opens, and seal it again.
* `--rewrap` (old and new password, NUL-terminated).
* `--pam-rewrap` (the new password, from pam_luma_keyring.so in the passwd
  stack once it verified against /etc/shadow; PAM_USER names the account).
  Exit 0 when re-wrapped or nothing to do, 1 when no seal opened.
* `--enrol USER` (root): the installer's step.
"""

from __future__ import annotations

import sys

from .state import read

__all__ = ("check", "autologin", "enable_auto_unlock", "recover", "rewrap", "pam_rewrap", "enrol",
           "dispatch")


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


def _secrets(count: int = 1) -> list[str]:
    from .autounlock import no_core_dumps, read_secrets

    no_core_dumps()
    return read_secrets(sys.stdin, count)


def _say(outcome) -> int:
    print(outcome.message)
    return 0 if outcome.ok else 1


def enable_auto_unlock(argv: list[str] | None = None) -> int:
    from .repair import enable_auto_unlock as enable, store_unencrypted

    (password,) = _secrets()
    if argv and "--unencrypted" in argv:
        return _say(store_unencrypted(password))
    return _say(enable(password))


def recover() -> int:
    from .repair import recover as recover_key

    (password,) = _secrets()
    return _say(recover_key(password))


def rewrap() -> int:
    from .repair import rewrap as rewrap_key

    try:
        old, new = _secrets(2)
    except ValueError as error:
        print(f"luma-keyring: {error}", file=sys.stderr)
        return 2
    return _say(rewrap_key(old, new))


def pam_rewrap() -> int:
    """The PAM module ignores the result; it only logs a failure."""
    import os

    from .autounlock import pam_rewrap as rewrap_for
    from .secrets import KeyringError

    user = os.environ.get("PAM_USER", "")
    if os.environ.get("PAM_TYPE") != "password" or not user:
        return 0
    (password,) = _secrets()
    if not password:
        return 0
    try:
        if rewrap_for(user, password):
            return 0
        print("luma-keyring: the keyring key could not be unsealed; the "
              "recovery copy keeps the old password", file=sys.stderr)
    except (KeyError, OSError, KeyringError) as error:
        print(f"luma-keyring: recovery copy not re-wrapped: {error}", file=sys.stderr)
    return 1


def enrol(args: list[str]) -> int:
    from .autounlock import enrol_user
    from .secrets import KeyringError

    if len(args) != 1 or not args[0] or args[0].startswith("-"):
        print("usage: luma-keyring --enrol USER < password", file=sys.stderr)
        return 2
    (password,) = _secrets()
    if not password:
        print("luma-keyring: no password given; nothing enrolled", file=sys.stderr)
        return 1
    try:
        path = enrol_user(args[0], password)
    except (KeyError, OSError, KeyringError) as error:
        print(f"luma-keyring: nothing enrolled for {args[0]}: {error}", file=sys.stderr)
        return 1
    print(f"luma-keyring: enrolled {args[0]} ({path})")
    return 0


def verify_login() -> int:
    """luma-keyring-verify@.service: one password on stdin (the socket), one answer out."""
    from .login import PamUnavailable, _check_with_pam

    (password,) = _secrets()
    try:
        answer = "yes" if password and _check_with_pam(password) else "no"
    except PamUnavailable:
        answer = "unavailable"
    sys.stdout.write(answer + "\n")
    sys.stdout.flush()
    return 0


def dispatch(argv: list[str]) -> int | None:
    """The command-line steps, without loading GTK; None for the service."""
    if "--verify-login" in argv:
        return verify_login()
    if "--check" in argv:
        return check()
    if "--autologin" in argv:
        return autologin()
    if "--enable-auto-unlock" in argv:
        return enable_auto_unlock(argv)
    if "--recover" in argv:
        return recover()
    if "--pam-rewrap" in argv:
        return pam_rewrap()
    if "--rewrap" in argv:
        return rewrap()
    if "--enrol" in argv:
        return enrol(argv[argv.index("--enrol") + 1:])
    return None
