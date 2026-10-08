# SPDX-License-Identifier: Apache-2.0
import os
import signal
import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import launch_guard, launcher
from luma_installer.errors import InstallerError

LOADER = ("/app/bin/CrealityPrint: error while loading shared libraries: libbz2.so.1.0: "
          "cannot open shared object file: No such file or directory")


class Classification(unittest.TestCase):
    def test_missing_library_is_named_whenever_it_happens(self):
        failure = launch_guard.classify(127, 40.0, ["starting", LOADER])
        self.assertEqual((failure.kind, failure.library), ("missing-library", "libbz2.so.1.0"))
        title, body = launch_guard.message("CrealityPrint", failure)
        self.assertEqual(title, "CrealityPrint couldn't open")
        self.assertEqual(body, "A system library it needs is missing. Details in Luma Vitals.")
        self.assertIn("libbz2.so.1.0 is missing", launch_guard.summary("CrealityPrint", failure))

    def test_too_old_library(self):
        failure = launch_guard.classify(1, 0.2, ["/app/x: /lib64/libc.so.6: version `GLIBC_2.99' not found (required by /app/x)"])
        self.assertEqual((failure.kind, failure.library), ("library-version", "GLIBC_2.99"))

    def test_crash_while_opening(self):
        failure = launch_guard.classify(128 + signal.SIGSEGV, 2.0, [])
        self.assertEqual((failure.kind, failure.signal_name), ("crashed", "SIGSEGV"))
        self.assertEqual(launch_guard.classify(-signal.SIGABRT, 2.0, []).kind, "crashed")

    def test_deliberate_and_late_exits_are_not_failures(self):
        self.assertIsNone(launch_guard.classify(0, 0.5, [LOADER]))
        for stop in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGKILL):
            self.assertIsNone(launch_guard.classify(-stop, 1.0, []))
            self.assertIsNone(launch_guard.classify(128 + stop, 1.0, []))
        # Closed after it was open: a status is the application's business.
        self.assertIsNone(launch_guard.classify(1, launch_guard.STARTUP_SECONDS + 1, []))
        self.assertEqual(launch_guard.classify(1, 3.0, []).kind, "exited")


class Running(unittest.TestCase):
    def test_error_output_passes_through_and_is_kept(self):
        code = "import sys; [print(f'line {i}', file=sys.stderr) for i in range(60)]; sys.exit(3)"
        status, seconds, tail = launch_guard.run([sys.executable, "-c", code])
        self.assertEqual(status, 3)
        self.assertEqual(len(tail), launch_guard.STDERR_LINES)
        self.assertEqual(tail[-1], "line 59")
        self.assertGreaterEqual(seconds, 0)


class FakeCompositor(threading.Thread):
    """Answers registry and sync requests the way a Wayland compositor does."""

    def __init__(self, path: str, globals_=("wl_compositor", "gtk_shell1")):
        super().__init__(daemon=True)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(1)
        self.globals = globals_
        self.requests = []

    @staticmethod
    def event(object_id, opcode, payload=b""):
        return struct.pack("=II", object_id, ((8 + len(payload)) << 16) | opcode) + payload

    def run(self):
        connection, _ = self.server.accept()
        buffer = b""
        with connection:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                while len(buffer) >= 8:
                    object_id, word = struct.unpack_from("=II", buffer)
                    size = word >> 16
                    if len(buffer) < size:
                        break
                    payload, buffer = buffer[8:size], buffer[size:]
                    self.requests.append((object_id, word & 0xFFFF, payload))
                    if (object_id, word & 0xFFFF) == (1, 1):
                        for name, interface in enumerate(self.globals, start=1):
                            connection.sendall(self.event(2, 0, struct.pack("=I", name) + launch_guard._wl_string(interface)
                                                          + struct.pack("=I", 7)))
                    elif (object_id, word & 0xFFFF) == (1, 0):
                        connection.sendall(self.event(struct.unpack("=I", payload)[0], 0, struct.pack("=I", 1)))


class StartupSequence(unittest.TestCase):
    def test_sets_the_launch_token_on_gtk_shell(self):
        with tempfile.TemporaryDirectory() as runtime:
            compositor = FakeCompositor(os.path.join(runtime, "wayland-0"))
            compositor.start()
            self.assertTrue(launch_guard.end_startup({"XDG_RUNTIME_DIR": runtime, "WAYLAND_DISPLAY": "wayland-0",
                                                      "XDG_ACTIVATION_TOKEN": "token-123"}))
            bind = [r for r in compositor.requests if r[:2] == (2, 0)][0]
            self.assertEqual(struct.unpack_from("=I", bind[2])[0], 2)  # gtk_shell1's registry name
            self.assertIn(b"gtk_shell1\0", bind[2])
            self.assertIn((4, 1, launch_guard._wl_string("token-123")), compositor.requests)

    def test_nothing_to_end_without_a_token_or_gtk_shell(self):
        self.assertFalse(launch_guard.end_startup({"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/nonexistent"}))
        with tempfile.TemporaryDirectory() as runtime:
            compositor = FakeCompositor(os.path.join(runtime, "wayland-0"), ("wl_compositor",))
            compositor.start()
            self.assertFalse(launch_guard.end_startup({"XDG_RUNTIME_DIR": runtime, "WAYLAND_DISPLAY": "wayland-0",
                                                       "DESKTOP_STARTUP_ID": "token"}))


class Launcher(unittest.TestCase):
    record = {"application_id": "appimage-crealityprint-b28f66c25d0c", "name": "CrealityPrint",
              "format": "appimage", "icon": "appimage-crealityprint-b28f66c25d0c", "root": "/nonexistent",
              "sha256": "b28f66c25d0c", "command": "/app/AppRun"}

    def test_failed_open_is_reported_once(self):
        with tempfile.TemporaryDirectory() as home, patch("pathlib.Path.home", return_value=Path(home)), \
                patch("luma_installer.launcher.read_record", return_value=dict(self.record)), \
                patch("luma_installer.launcher._start_bus_proxy", return_value=(None, None)), \
                patch("luma_installer.launcher.launch_guard.should_capture", return_value=True), \
                patch("luma_installer.launcher.launch_guard.run", return_value=(127, 0.4, [LOADER])), \
                patch("luma_installer.launcher.launch_guard.end_startup") as end, \
                patch("luma_installer.launcher.launch_guard.notify") as notify, \
                patch("luma_installer.launcher.launch_guard.journal") as journal:
            self.assertEqual(launcher.main([self.record["application_id"]]), 127)
        end.assert_called_once()
        notify.assert_called_once_with("CrealityPrint couldn't open",
                                       "A system library it needs is missing. Details in Luma Vitals.",
                                       self.record["icon"])
        self.assertEqual(journal.call_args.args[2].library, "libbz2.so.1.0")

    def test_appimage_gets_the_runtime_environment_and_a_laid_out_home(self):
        with tempfile.TemporaryDirectory() as home, patch("pathlib.Path.home", return_value=Path(home)), \
                patch("luma_installer.launcher._start_bus_proxy", return_value=(None, None)), \
                patch("luma_installer.launcher._person_folders", return_value=[]), \
                patch("luma_installer.launcher.launch_guard.run", return_value=(0, 300.0, [])) as run:
            self.assertEqual(launcher._launch_appimage(dict(self.record)), 0)
            environment = run.call_args.kwargs["env"]
            self.assertEqual((environment["APPIMAGE"], environment["APPDIR"], environment["ARGV0"]),
                             ("/app/AppRun", "/app", "/app/AppRun"))
            data = Path(home) / ".local/share/luma/installer/data" / self.record["sha256"]
            for folder in (".config", ".cache", ".local/share", ".local/state"):
                self.assertTrue((data / folder).is_dir(), folder)

    def test_normal_run_reports_nothing(self):
        with tempfile.TemporaryDirectory() as home, patch("pathlib.Path.home", return_value=Path(home)), \
                patch("luma_installer.launcher.read_record", return_value=dict(self.record)), \
                patch("luma_installer.launcher._start_bus_proxy", return_value=(None, None)), \
                patch("luma_installer.launcher.launch_guard.run", return_value=(0, 300.0, [])), \
                patch("luma_installer.launcher.launch_guard.report") as report:
            self.assertEqual(launcher.main([self.record["application_id"]]), 0)
        report.assert_not_called()

    def test_unreadable_record_is_reported(self):
        with patch("luma_installer.launcher.read_record", side_effect=InstallerError("The installed application's Luma record is unavailable.")), \
                patch("luma_installer.launcher.launch_guard.report") as report:
            self.assertEqual(launcher.main(["gone"]), 1)
        self.assertEqual(report.call_args.args[3].kind, "launcher")

    def test_journal_entry_carries_the_specifics(self):
        failure = launch_guard.classify(127, 0.4, [LOADER])
        sent = {}

        class Journal:
            @staticmethod
            def send(message, **fields):
                sent.update(fields, MESSAGE=message)

        with patch.dict(sys.modules, {"systemd": type(sys)("systemd"), "systemd.journal": Journal}):
            sys.modules["systemd"].journal = Journal
            launch_guard.journal("CrealityPrint", "appimage-crealityprint-b28f66c25d0c", failure)
        self.assertEqual(sent["MESSAGE_ID"], launch_guard.MESSAGE_ID)
        self.assertEqual(sent["LUMA_MISSING"], "libbz2.so.1.0")
        self.assertEqual(sent["LUMA_LAUNCH_FAILURE"], "missing-library")
        self.assertIn("libbz2.so.1.0", sent["LUMA_STDERR"])


if __name__ == "__main__":
    unittest.main()
