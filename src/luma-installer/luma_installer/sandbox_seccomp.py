# SPDX-License-Identifier: Apache-2.0
"""The system-call filter of an application started with bubblewrap.

Podman gave every capsule a filter; bubblewrap gives none unless it is handed
one. This is the filter, modelled on Flatpak's (flatpak-run.c, setup_seccomp):
a short list of calls an application has no use for and the kernel has had
bugs behind, refused with an error, everything else allowed.

Where it differs from Flatpak's, and why:

* **Namespaces stay.** Flatpak refuses unshare, setns, mount, pivot_root,
  chroot, clone3 and clone(CLONE_NEWUSER). Chromium's own sandbox, and the
  bubblewrap sandboxes some applications run their tools in, are made of
  exactly those calls, in a user namespace nested in the application's. They
  are allowed; what a nested namespace may mount is decided by SELinux
  (luma_application.cil).
* **ptrace stays.** Flatpak allows it only with --devel. Editors run debuggers,
  and crash reporters attach to their own children; a capsule allowed both.
  The PID namespace and the Landlock scope keep it to the application's own
  processes.
* **More is refused.** Calls that change the machine and need a capability the
  application can never hold (modules, kexec, swap, the clock, reboot, raw
  port access) are refused here too, so a kernel bug in one of them is not
  reachable from an application.

The filter is classic BPF, generated here so that no binding is needed at
launch. A 64-bit program can make 32-bit system calls, so each machine's filter
covers its 32-bit architecture as well; anything else is killed. On x86 the
old socketcall(2) multiplexer keeps its arguments in memory, where a filter
cannot see them, so the address-family rule does not apply to it (the same
holds for Flatpak).

Numbers are from `ausyscall ARCH NAME --exact` (audit 4.1); the test checks
them against libseccomp where its Python module is installed.
"""
from __future__ import annotations

import os
import platform
import struct

EPERM, ENOSYS, EAFNOSUPPORT = 1, 38, 97

ARCHITECTURES = {"x86_64": 0xC000003E, "i386": 0x40000003, "aarch64": 0xC00000B7, "arm": 0x40000028}
# The architectures a process on each machine can make system calls for.
MACHINES = {"x86_64": ("x86_64", "i386"), "aarch64": ("aarch64", "arm")}
X32_BIT = 0x40000000

_ORDER = ("x86_64", "i386", "aarch64", "arm")


def _row(*numbers: int | None) -> dict[str, int]:
    return {arch: number for arch, number in zip(_ORDER, numbers) if number is not None}


# Refused with EPERM.                      x86_64 i386 aarch64 arm
REFUSED = {
    # Flatpak's list.
    "syslog": _row(103, 103, 116, 103),
    "uselib": _row(134, 86, None, 86),
    "acct": _row(163, 51, 89, 51),
    "quotactl": _row(179, 131, 60, 131),
    "quotactl_fd": _row(443, 443, 443, 443),
    "add_key": _row(248, 286, 217, 309),
    "keyctl": _row(250, 288, 219, 311),
    "request_key": _row(249, 287, 218, 310),
    "move_pages": _row(279, 317, 239, 344),
    "mbind": _row(237, 274, 235, 319),
    "get_mempolicy": _row(239, 275, 236, 320),
    "set_mempolicy": _row(238, 276, 237, 321),
    "set_mempolicy_home_node": _row(450, 450, 450, 450),
    "migrate_pages": _row(256, 294, 238, 400),
    "perf_event_open": _row(298, 336, 241, 364),
    # Changes to the machine itself.
    "kexec_load": _row(246, None, 104, 347),
    "kexec_file_load": _row(320, None, 294, 401),
    "reboot": _row(169, 88, 142, 88),
    "swapon": _row(167, 87, 224, 87),
    "swapoff": _row(168, 115, 225, 115),
    "init_module": _row(175, 128, 105, 128),
    "finit_module": _row(313, 350, 273, 379),
    "delete_module": _row(176, 129, 106, 129),
    "create_module": _row(174, 127, None, None),
    "get_kernel_syms": _row(177, 130, None, None),
    "query_module": _row(178, 167, None, None),
    "open_by_handle_at": _row(304, 342, 265, 371),
    "bpf": _row(321, 357, 280, 386),
    "iopl": _row(172, 110, None, None),
    "ioperm": _row(173, 101, None, None),
    "settimeofday": _row(164, 79, 170, 79),
    "clock_settime": _row(227, 264, 112, 262),
    "clock_settime64": _row(None, 404, None, 404),
    "stime": _row(None, 25, None, 25),
    "vhangup": _row(153, 111, 58, 111),
    "nfsservctl": _row(180, 169, 42, 169),
    "lookup_dcookie": _row(212, 253, 18, 249),
    "_sysctl": _row(156, 149, None, None),
    "vm86": _row(None, 166, None, None),
    "vm86old": _row(None, 113, None, None),
    "pciconfig_read": _row(None, None, None, 272),
    "pciconfig_write": _row(None, None, None, 273),
}

IOCTL = _row(16, 54, 29, 54)
# Pushing input into a terminal: how a sandboxed program types into the shell that started it.
REFUSED_IOCTLS = (0x5412, 0x541C)  # TIOCSTI, TIOCLINUX

PERSONALITY = _row(135, 136, 92, 136)
# Linux, 32-bit Linux, their uname-2.6 variants, a debugger's "no address
# randomisation", and the query. Everything else switches on old-ABI bugs.
PERSONALITIES = (0x0, 0x8, 0x20000, 0x20008, 0x40000, 0x40008, 0xFFFFFFFF)

SOCKET = _row(41, 359, 198, 281)
# Unspecified, local, IPv4, IPv6, netlink: Flatpak's list. Other families
# (packet, CAN, Bluetooth, kernel crypto, ...) are driver code few applications need.
SOCKET_FAMILIES = (0, 1, 2, 10, 16)

_LD, _JEQ, _JGE, _JA, _RET = 0x20, 0x15, 0x35, 0x05, 0x06
_ALLOW, _KILL, _ERRNO = 0x7FFF0000, 0x80000000, 0x00050000
_NR, _ARCH = 0, 4


def _argument(index: int) -> int:
    """Offset of the low half of an argument (little-endian machines only)."""
    return 16 + 8 * index


def _instruction(code: int, value: int, true: int = 0, false: int = 0) -> bytes:
    return struct.pack("<HBBI", code, true, false, value & 0xFFFFFFFF)


def _section(arch: str) -> list[bytes]:
    """The rules for one architecture; ends by allowing the call."""
    out = [_instruction(_LD, _NR)]
    if arch == "x86_64":
        # The x32 ABI shares the architecture value and numbers its calls from this bit.
        out += [_instruction(_JGE, X32_BIT, 0, 1), _instruction(_RET, _ERRNO | ENOSYS)]
    for name in sorted(REFUSED):
        number = REFUSED[name].get(arch)
        if number is not None:
            out += [_instruction(_JEQ, number, 0, 1), _instruction(_RET, _ERRNO | EPERM)]

    def checked(number: int | None, index: int, values: tuple[int, ...], allow: bool, errno: int) -> None:
        """Compare one argument with `values`: refuse the listed ones, or all but them."""
        if number is None:
            return
        body = [_instruction(_LD, _argument(index))]
        count = len(values)
        for position, value in enumerate(values):
            remaining = count - position - 1
            if allow:   # a match skips the rest and the refusal
                body.append(_instruction(_JEQ, value, remaining + 1, 0))
            else:       # a match goes to the refusal; after the last, a miss skips it
                body.append(_instruction(_JEQ, value, remaining, 1 if remaining == 0 else 0))
        body += [_instruction(_RET, _ERRNO | errno), _instruction(_LD, _NR)]
        out.append(_instruction(_JEQ, number, 0, len(body)))
        out.extend(body)

    checked(IOCTL.get(arch), 1, REFUSED_IOCTLS, False, EPERM)
    checked(PERSONALITY.get(arch), 0, PERSONALITIES, True, EPERM)
    checked(SOCKET.get(arch), 0, SOCKET_FAMILIES, True, EAFNOSUPPORT)
    out.append(_instruction(_RET, _ALLOW))
    return out


def host_machine() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)


def program(machine: str | None = None) -> bytes:
    """The filter for a machine, as the array of sock_filter bubblewrap reads; b"" for an unknown machine."""
    architectures = MACHINES.get(machine or host_machine())
    if not architectures:
        return b""
    out = [_instruction(_LD, _ARCH)]
    for arch in architectures:
        section = _section(arch)
        # This architecture: step over the jump and into its rules. Otherwise jump past them.
        out += [_instruction(_JEQ, ARCHITECTURES[arch], 1, 0), _instruction(_JA, len(section))]
        out.extend(section)
    out.append(_instruction(_RET, _KILL))
    return b"".join(out)


def descriptor(machine: str | None = None) -> int | None:
    """An inheritable descriptor bubblewrap can read the filter from (--seccomp FD), or None."""
    data = program(machine)
    if not data:
        return None
    try:
        fd = os.memfd_create("luma-seccomp", 0)
    except (AttributeError, OSError):
        return None
    os.write(fd, data)
    os.lseek(fd, 0, os.SEEK_SET)
    os.set_inheritable(fd, True)
    return fd


# -- A reference interpreter, for the tests -------------------------------------------

def evaluate(data: bytes, arch: int, number: int, arguments: tuple[int, ...] = ()) -> int:
    """What the filter returns for one system call."""
    packet = struct.pack("<IIQ6Q", number, arch, 0, *(list(arguments) + [0] * 6)[:6])
    count = len(data) // 8
    accumulator, pc = 0, 0
    while pc < count:
        code, true, false, value = struct.unpack_from("<HBBI", data, pc * 8)
        pc += 1
        if code == _LD:
            accumulator = struct.unpack_from("<I", packet, value)[0]
        elif code == _JEQ:
            pc += true if accumulator == value else false
        elif code == _JGE:
            pc += true if accumulator >= value else false
        elif code == _JA:
            pc += value
        elif code == _RET:
            return value
        else:
            raise ValueError(f"unknown instruction {code:#x}")
    raise ValueError("the filter ran off its end")
