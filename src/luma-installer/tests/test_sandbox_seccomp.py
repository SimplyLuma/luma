# SPDX-License-Identifier: Apache-2.0
import ctypes
import os
import platform
import shutil
import subprocess
import sys
import unittest

from luma_installer import sandbox_seccomp as seccomp

ALLOW, KILL = 0x7FFF0000, 0x80000000


def errno(value):
    return 0x00050000 | value


class FilterTest(unittest.TestCase):
    def check(self, machine, arch, number, arguments=()):
        return seccomp.evaluate(seccomp.program(machine), seccomp.ARCHITECTURES[arch], number, arguments)

    def test_every_listed_call_is_refused_on_each_architecture_it_exists_on(self):
        for machine, architectures in seccomp.MACHINES.items():
            for arch in architectures:
                for name, numbers in seccomp.REFUSED.items():
                    if arch in numbers:
                        self.assertEqual(self.check(machine, arch, numbers[arch]), errno(seccomp.EPERM), (arch, name))

    def test_ordinary_calls_and_namespace_calls_are_allowed(self):
        # read, write, openat, clone, unshare, mount, setns, pivot_root, chroot, clone3, ptrace
        for number in (0, 1, 257, 56, 272, 165, 308, 155, 161, 435, 101):
            self.assertEqual(self.check("x86_64", "x86_64", number), ALLOW, number)
        for number in (63, 64, 56, 220, 97, 40, 268, 41, 51, 435, 117):
            self.assertEqual(self.check("aarch64", "aarch64", number), ALLOW, number)

    def test_terminal_injection_is_refused_whatever_the_upper_half_says(self):
        for command in seccomp.REFUSED_IOCTLS:
            self.assertEqual(self.check("x86_64", "x86_64", 16, (0, command)), errno(seccomp.EPERM))
            self.assertEqual(self.check("x86_64", "x86_64", 16, (0, command | (1 << 32))), errno(seccomp.EPERM))
            self.assertEqual(self.check("x86_64", "i386", 54, (0, command)), errno(seccomp.EPERM))
            self.assertEqual(self.check("aarch64", "aarch64", 29, (0, command)), errno(seccomp.EPERM))
        self.assertEqual(self.check("x86_64", "x86_64", 16, (0, 0x5401)), ALLOW)  # TCGETS

    def test_personality(self):
        for value in seccomp.PERSONALITIES:
            self.assertEqual(self.check("x86_64", "x86_64", 135, (value,)), ALLOW, hex(value))
        self.assertEqual(self.check("x86_64", "x86_64", 135, (0x0400000,)), errno(seccomp.EPERM))  # READ_IMPLIES_EXEC
        self.assertEqual(self.check("aarch64", "aarch64", 92, (0x4,)), errno(seccomp.EPERM))

    def test_socket_families(self):
        for family in seccomp.SOCKET_FAMILIES:
            self.assertEqual(self.check("x86_64", "x86_64", 41, (family, 1, 0)), ALLOW, family)
        for family in (17, 29, 31, 38, 40):  # packet, CAN, Bluetooth, kernel crypto, vsock
            self.assertEqual(self.check("x86_64", "x86_64", 41, (family, 1, 0)), errno(seccomp.EAFNOSUPPORT), family)
            self.assertEqual(self.check("aarch64", "aarch64", 198, (family, 1, 0)), errno(seccomp.EAFNOSUPPORT), family)
        # A call after a checked one is still judged by its own number.
        self.assertEqual(self.check("x86_64", "x86_64", 42), ALLOW)

    def test_other_architectures_and_x32(self):
        self.assertEqual(self.check("x86_64", "aarch64", 63), KILL)
        self.assertEqual(self.check("aarch64", "x86_64", 0), KILL)
        self.assertEqual(self.check("x86_64", "x86_64", seccomp.X32_BIT | 0), errno(seccomp.ENOSYS))
        # 32-bit calls from a 64-bit machine are judged by the 32-bit numbers.
        self.assertEqual(self.check("x86_64", "i386", 288), errno(seccomp.EPERM))   # keyctl
        self.assertEqual(self.check("x86_64", "i386", 250), ALLOW)                  # fadvise64, not keyctl
        self.assertEqual(self.check("aarch64", "arm", 311), errno(seccomp.EPERM))

    def test_unknown_machine_has_no_filter(self):
        self.assertEqual(seccomp.program("riscv64"), b"")
        self.assertEqual(len(seccomp.program("x86_64")) % 8, 0)

    def test_numbers_match_libseccomp_when_it_is_installed(self):
        try:
            import seccomp as libseccomp
        except ImportError:
            self.skipTest("libseccomp's Python module is not installed")
        names = {"x86_64": "x86_64", "i386": "x86", "aarch64": "aarch64", "arm": "arm"}
        tables = {**seccomp.REFUSED, "ioctl": seccomp.IOCTL, "personality": seccomp.PERSONALITY, "socket": seccomp.SOCKET}
        for name, numbers in tables.items():
            for arch, number in numbers.items():
                self.assertEqual(libseccomp.resolve_syscall(libseccomp.Arch(names[arch]), name), number, (name, arch))


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bwrap") and platform.machine() in ("x86_64", "aarch64"),
                     "needs bubblewrap on Linux")
class KernelTest(unittest.TestCase):
    """The kernel's verdict, under bubblewrap."""

    PROBE = r"""
import ctypes, errno, os, socket, sys
libc = ctypes.CDLL(None, use_errno=True)
def call(number, *arguments):
    ctypes.set_errno(0)
    return libc.syscall(number, *arguments), ctypes.get_errno()
import platform
keyctl = {"x86_64": 250, "aarch64": 219}[platform.machine()]
result, error = call(keyctl, 0, 0, 0, 0, 0)
assert (result, error) == (-1, errno.EPERM), ("keyctl", result, error)
try:
    socket.socket(17, socket.SOCK_RAW)
    raise SystemExit("packet socket was not refused")
except OSError as failure:
    assert failure.errno == errno.EAFNOSUPPORT, failure
socket.socket(socket.AF_UNIX).close()
os.unshare(0x10000000)  # CLONE_NEWUSER: a nested sandbox still starts
print("ok")
"""

    def test_filter_under_bubblewrap(self):
        fd = seccomp.descriptor()
        self.assertIsNotNone(fd)
        try:
            probe = subprocess.run(["bwrap", "--dev-bind", "/", "/", "true"], capture_output=True)
            if probe.returncode != 0:
                self.skipTest("bubblewrap cannot make a namespace here")
            result = subprocess.run(["bwrap", "--dev-bind", "/", "/", "--seccomp", str(fd), sys.executable, "-c", self.PROBE],
                                    pass_fds=[fd], capture_output=True, text=True)
        finally:
            os.close(fd)
        self.assertEqual(result.stdout.strip(), "ok", result.stderr)


if __name__ == "__main__":
    unittest.main()
