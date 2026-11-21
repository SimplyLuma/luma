"""Keep an application's abstract sockets and signals to itself.

A capsule shares the host's network namespace, so that a browser's sign-in can
hand a link back to the application on localhost. That namespace also holds
every abstract Unix socket of the session: the X server's, an input method's,
any service that listens on one. Mount and PID namespaces do not cover them.

Landlock can scope both without a network namespace (ABI 6, Linux 6.12): a
process in the scope connects only to abstract sockets made inside it, and
signals only processes inside it. This module is run in front of the command
that starts the application:

    python3 -m luma_installer.capsule_scope --abstract --signal -- podman run ...

It applies the scope to itself and then becomes that command, so everything
the application starts inherits it. That is how an application in a
bubblewrap sandbox is started. Podman cannot be started this way: it has to
join its user namespace first, which a scope forbids, so a capsule is given
`runtime_main` as its OCI runtime instead, and the scope is applied where the
container's first process is made.

Scoping needs no_new_privs, which is inherited too: set-user-ID programs
inside the capsule no longer gain privileges. Nested user namespaces are
unaffected, so a browser's own sandbox works as before.

On a kernel without ABI 6 the command runs unscoped, as it did before.
"""
from __future__ import annotations

import ctypes
import os
import platform
import sys

SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
SCOPE_SIGNAL = 1 << 1
REQUIRED_ABI = 6

_CREATE_RULESET_VERSION = 1 << 0
_PR_SET_NO_NEW_PRIVS = 38
# The Landlock system calls were added after system call numbers were unified,
# so they are the same on every architecture Luma runs on.
_SYS_CREATE_RULESET = 444
_SYS_RESTRICT_SELF = 446


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


def _libc() -> ctypes.CDLL | None:
    if platform.system() != "Linux":
        return None
    try:
        return ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None


def abi() -> int:
    """The kernel's Landlock ABI version; 0 when Landlock is not available."""
    libc = _libc()
    if libc is None:
        return 0
    version = libc.syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_CREATE_RULESET_VERSION))
    return version if version > 0 else 0


def supported() -> bool:
    return abi() >= REQUIRED_ABI


def restrict(scopes: int) -> bool:
    """Scope this process and everything it starts. False when it could not be."""
    libc = _libc()
    if libc is None or not scopes or abi() < REQUIRED_ABI:
        return False
    attribute = _RulesetAttr(0, 0, scopes)
    ruleset = libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attribute), ctypes.c_size_t(ctypes.sizeof(attribute)),
                           ctypes.c_uint32(0))
    if ruleset < 0:
        return False
    try:
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)):
            return False
        return libc.syscall(_SYS_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0)) == 0
    finally:
        os.close(ruleset)


SECCOMP_PLACEHOLDER = "{luma-seccomp-fd}"


def prefix(abstract: bool = True, signal: bool = True, seccomp: bool = False) -> list[str]:
    """What to put in front of a command to run it scoped; empty when the kernel cannot.

    With `seccomp`, the command is bubblewrap and carries `seccomp_arguments()`:
    the system-call filter (sandbox_seccomp.py) is opened here, after every
    wrapper that would not pass a descriptor on, and its number is written
    into bubblewrap's options.
    """
    flags = [flag for flag, wanted in (("--abstract", abstract), ("--signal", signal)) if wanted] if supported() else []
    if seccomp:
        flags.append("--seccomp")
    if not flags:
        return []
    return [sys.executable or "/usr/bin/python3", "-m", "luma_installer.capsule_scope", *flags, "--"]


def seccomp_arguments() -> list[str]:
    """Bubblewrap's options for the filter; only behind `prefix(seccomp=True)`."""
    return ["--seccomp", SECCOMP_PLACEHOLDER]


def with_filter(command: list[str]) -> tuple[list[str], int | None]:
    """The command with the filter's descriptor in place of the placeholder (or without the option)."""
    if SECCOMP_PLACEHOLDER not in command:
        return command, None
    from . import sandbox_seccomp
    descriptor = sandbox_seccomp.descriptor()
    index = command.index(SECCOMP_PLACEHOLDER)
    if descriptor is None:
        return command[:index - 1] + command[index + 1:], None
    return command[:index] + [str(descriptor)] + command[index + 1:], descriptor


def strip(arguments: list[str]) -> list[str]:
    """The command without the scope in front of it."""
    if len(arguments) > 3 and arguments[1:3] == ["-m", "luma_installer.capsule_scope"] and "--" in arguments:
        return arguments[arguments.index("--") + 1:]
    return arguments


REAL_RUNTIME = "/usr/bin/crun"


def runtime_main(argv: list[str] | None = None) -> int:
    """crun, with the container's first process scoped.

    Podman is given this as the capsule's OCI runtime. It is run for every
    step of a container's life; only the one that makes the container's
    processes is scoped. The others (start, kill, delete, exec) have to reach
    those processes from outside, which a scope of their own would forbid.
    """
    values = list(sys.argv[1:] if argv is None else argv)
    if any(value in ("create", "run") for value in values):
        restrict(SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL)
    try:
        os.execv(REAL_RUNTIME, ["crun", *values])
    except OSError as error:
        print(f"luma-capsule-runtime: {REAL_RUNTIME}: {error}", file=sys.stderr)
        return 127
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if "--" not in values or not values[values.index("--") + 1:]:
        print("usage: capsule_scope [--abstract] [--signal] [--seccomp] -- COMMAND [ARGUMENT ...]", file=sys.stderr)
        return 2
    flags, command = values[:values.index("--")], values[values.index("--") + 1:]
    scopes = ((SCOPE_ABSTRACT_UNIX_SOCKET if "--abstract" in flags else 0)
              | (SCOPE_SIGNAL if "--signal" in flags else 0))
    if scopes and not restrict(scopes):
        # Never a reason not to open what the person asked for.
        print("luma-capsule-scope: this kernel cannot scope the application; continuing without", file=sys.stderr)
    command, descriptor = with_filter(command)
    if "--seccomp" in flags and descriptor is None:
        print("luma-capsule-scope: no system-call filter for this machine; continuing without", file=sys.stderr)
    try:
        os.execvp(command[0], command)
    except OSError as error:
        print(f"luma-capsule-scope: {command[0]}: {error}", file=sys.stderr)
        return 127
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
