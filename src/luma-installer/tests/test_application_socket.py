# SPDX-License-Identifier: Apache-2.0
"""The PipeWire socket for installed applications (data/pipewire).

Clients on the session's own socket must be treated as they are without these
files; only the application socket is held back.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data/pipewire"
SOCKET = "pipewire-0-application"


def settings(name):
    """The file without its comments."""
    return "\n".join(line for line in (DATA / name).read_text().splitlines()
                     if not line.lstrip().startswith(("#", "--")))


class ApplicationSocketFiles(unittest.TestCase):
    def test_access_module_keeps_its_default_mode(self):
        text = settings("60-luma-application-socket.conf")
        self.assertNotIn("module.access.args", text)
        self.assertNotIn("access.socket", text)

    def test_only_the_application_socket_is_marked(self):
        entries = re.findall(r'\{\s*name\s*=\s*"([^"]+)"([^{}]*(?:\{[^{}]*\})?[^{}]*)\}',
                             settings("60-luma-application-socket.conf"))
        marked = {name: 'pipewire.client.access = "luma-application"' in rest for name, rest in entries}
        self.assertEqual(marked, {"pipewire-0": False, "pipewire-0-manager": False, SOCKET: True})

    def test_session_manager_goes_by_the_socket(self):
        rule = f'pipewire.sec.socket = "{SOCKET}"'
        self.assertIn(rule, settings("70-luma-application-access.conf"))
        self.assertNotIn("pipewire.access =", settings("70-luma-application-access.conf"))
        self.assertIn(f'["pipewire.sec.socket"] ~= "{SOCKET}"', settings("application-capture.lua"))


@unittest.skipUnless(all(shutil.which(tool) for tool in ("pipewire", "pw-cli", "pw-dump")),
                     "needs pipewire and its tools")
class ApplicationSocketServer(unittest.TestCase):
    """A PipeWire server with the file and no session manager."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        runtime = root / "run"
        runtime.mkdir(mode=0o700)
        fragments = root / "config/pipewire/pipewire.conf.d"
        fragments.mkdir(parents=True)
        shutil.copy(DATA / "60-luma-application-socket.conf", fragments)
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in ("PIPEWIRE_REMOTE", "PIPEWIRE_CORE", "PIPEWIRE_RUNTIME_DIR")}
        self.environment.update(XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(root / "config"),
                                HOME=str(root))
        self.server = subprocess.Popen(["pipewire"], env=self.environment,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while not all((runtime / name).exists() for name in ("pipewire-0", "pipewire-0-manager", SOCKET)):
            if self.server.poll() is not None or time.monotonic() > deadline:
                self.tearDown()
                self.skipTest("no PipeWire server in this environment")
            time.sleep(0.1)

    def tearDown(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(10)
            except subprocess.TimeoutExpired:
                self.server.kill()
        self.directory.cleanup()

    def test_session_client_does_not_wait_for_the_session_manager(self):
        try:
            result = subprocess.run(["pw-cli", "-r", "pipewire-0", "info", "0"], env=self.environment,
                                    capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            self.fail("a client on pipewire-0 waits for a session manager")
        self.assertEqual(result.returncode, 0)

    def test_application_client_waits_with_the_application_access(self):
        waiting = subprocess.Popen(["pw-cli", "-r", SOCKET, "info", "0"], env=self.environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            found = None
            deadline = time.monotonic() + 15
            while found is None and time.monotonic() < deadline:
                time.sleep(0.3)
                dump = subprocess.run(["pw-dump"], env=self.environment, capture_output=True, timeout=15)
                for item in json.loads(dump.stdout or b"[]"):
                    properties = (item.get("info") or {}).get("props") or {}
                    if item.get("type", "").endswith(":Client") and properties.get("pipewire.sec.socket") == SOCKET:
                        found = properties
            self.assertIsNotNone(found, "the client on the application socket was not seen")
            self.assertEqual(found.get("pipewire.access"), "luma-application")
            self.assertIsNone(waiting.poll(), "the application client was answered without a session manager")
        finally:
            waiting.kill()
            waiting.wait()


if __name__ == "__main__":
    unittest.main()
