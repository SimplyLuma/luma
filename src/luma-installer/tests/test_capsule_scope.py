"""An application's abstract sockets and signals stay inside its own scope."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import capsule_scope as scope
from luma_installer import launcher

HERE = Path(__file__).resolve().parents[1]


def run_scoped(flags: list[str], code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "luma_installer.capsule_scope", *flags, "--", sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=60, cwd=HERE,
                          env={**os.environ, "PYTHONPATH": str(HERE)})


class Arguments(unittest.TestCase):
    def test_nothing_is_put_in_front_where_the_kernel_cannot_scope(self):
        with patch.object(scope, "abi", return_value=5):
            self.assertEqual(scope.prefix(), [])
        with patch.object(scope, "abi", return_value=0):
            self.assertEqual(scope.prefix(), [])

    def test_the_scope_goes_in_front_of_the_command_and_comes_off_again(self):
        with patch.object(scope, "abi", return_value=6):
            both = scope.prefix()
            signals = scope.prefix(abstract=False)
            self.assertEqual(scope.prefix(abstract=False, signal=False), [])
        self.assertEqual(both[1:], ["-m", "luma_installer.capsule_scope", "--abstract", "--signal", "--"])
        self.assertEqual(signals[1:], ["-m", "luma_installer.capsule_scope", "--signal", "--"])
        self.assertEqual(scope.strip([*both, "podman", "run", "--", "x"]), ["podman", "run", "--", "x"])
        self.assertEqual(scope.strip(["podman", "run", "--", "x"]), ["podman", "run", "--", "x"])

    def test_a_command_is_required(self):
        self.assertEqual(scope.main(["--abstract", "--"]), 2)
        self.assertEqual(scope.main(["--abstract"]), 2)

    def test_the_command_runs_with_its_own_status_and_arguments(self):
        result = run_scoped(["--abstract", "--signal"], "import sys; print(sys.argv[1:]); sys.exit(7)")
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_the_launcher_scopes_each_kind_of_capsule_where_it_can(self):
        with patch.object(scope, "abi", return_value=7), tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "crun"
            runtime.touch()
            self.assertEqual(launcher._scope({})[1:], ["-m", "luma_installer.capsule_scope", "--abstract", "--signal", "--"])
            with patch.object(launcher, "CAPSULE_RUNTIME", runtime):
                self.assertEqual(launcher._runtime_arguments({}), ["--runtime", str(runtime)])
                self.assertEqual(launcher._runtime_arguments({"scope": False}), [])
            with patch.object(launcher, "CAPSULE_RUNTIME", Path(directory) / "absent"):
                self.assertEqual(launcher._runtime_arguments({}), [])
            self.assertEqual(launcher._scope({"scope": False}), [])
        with patch.object(scope, "abi", return_value=5):
            self.assertEqual((launcher._scope({}), launcher._runtime_arguments({})), ([], []))

    def test_the_runtime_scopes_only_the_step_that_makes_the_container(self):
        for arguments, scoped in ((["--root", "/r", "create", "--bundle", "/b", "id"], True),
                                  (["start", "id"], False), (["exec", "--process", "/p", "id"], False),
                                  (["kill", "id", "15"], False), (["delete", "--force", "id"], False)):
            with patch.object(scope, "restrict") as restrict, patch.object(scope.os, "execv") as execv:
                scope.runtime_main(arguments)
            self.assertEqual(restrict.called, scoped, arguments)
            execv.assert_called_once_with("/usr/bin/crun", ["crun", *arguments])


@unittest.skipUnless(scope.supported(), "needs Landlock ABI 6 (Linux 6.12)")
class Kernel(unittest.TestCase):
    def test_an_abstract_socket_made_outside_is_out_of_reach_and_one_made_inside_is_not(self):
        name = f"\0luma-scope-test-{os.getpid()}"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(name)
            server.listen(1)
            code = (
                "import socket, sys\n"
                f"name = '\\0luma-scope-test-{os.getpid()}'\n"
                "s = socket.socket(socket.AF_UNIX)\n"
                "try:\n    s.connect(name); print('outside: connected')\n"
                "except OSError as e:\n    print('outside:', type(e).__name__)\n"
                "inner = socket.socket(socket.AF_UNIX); inner.bind(name + '-inner'); inner.listen(1)\n"
                "c = socket.socket(socket.AF_UNIX); c.connect(name + '-inner'); print('inside: connected')\n")
            scoped = run_scoped(["--abstract"], code)
            self.assertIn("outside: PermissionError", scoped.stdout, scoped.stderr)
            self.assertIn("inside: connected", scoped.stdout, scoped.stderr)
            unscoped = run_scoped(["--signal"], code)
            self.assertIn("outside: connected", unscoped.stdout, unscoped.stderr)

    def test_a_process_outside_cannot_be_signalled_and_one_inside_can(self):
        outside = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            code = (
                "import os, signal, subprocess, sys\n"
                f"try:\n    os.kill({outside.pid}, 0); print('outside: signalled')\n"
                "except PermissionError:\n    print('outside: refused')\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "os.kill(child.pid, signal.SIGTERM); child.wait(); print('inside: signalled')\n")
            scoped = run_scoped(["--signal"], code)
            self.assertIn("outside: refused", scoped.stdout, scoped.stderr)
            self.assertIn("inside: signalled", scoped.stdout, scoped.stderr)
            self.assertIsNone(outside.poll())
        finally:
            outside.send_signal(signal.SIGKILL)
            outside.wait()

    def test_path_sockets_and_new_user_namespaces_still_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "socket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen(1)
                code = (
                    "import ctypes, os, socket\n"
                    f"s = socket.socket(socket.AF_UNIX); s.connect({path!r}); print('path: connected')\n"
                    "print('no_new_privs:', open('/proc/self/status').read().split('NoNewPrivs:')[1].split()[0])\n"
                    "libc = ctypes.CDLL(None, use_errno=True)\n"
                    "print('user namespace:', 'ok' if libc.unshare(0x10000000) == 0 else os.strerror(ctypes.get_errno()))\n")
                result = run_scoped(["--abstract", "--signal"], code)
        self.assertIn("path: connected", result.stdout, result.stderr)
        self.assertIn("no_new_privs: 1", result.stdout, result.stderr)


if __name__ == "__main__":
    unittest.main()
