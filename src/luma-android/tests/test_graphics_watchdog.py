# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_android import graphics_watchdog as watchdog


def process(root, pid, comm, start, cgroup=watchdog.CONTAINER_CGROUP):
    entry = Path(root) / str(pid)
    entry.mkdir()
    (entry / "comm").write_text(comm + "\n")
    (entry / "cgroup").write_text(cgroup + "\n")
    fields = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 10
    (entry / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")


class GraphicsWatchdog(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.proc = Path(directory.name)

    def test_only_container_processes_count(self):
        process(self.proc, 10, "surfaceflinger", 500)
        process(self.proc, 11, "system_server", 400, cgroup="0::/user.slice")
        process(self.proc, 12, "system_server", 300)
        self.assertEqual(watchdog.container_processes(self.proc),
                         {"surfaceflinger": (10, 500), "system_server": (12, 300)})

    def test_surfaceflinger_newer_than_framework_is_stale(self):
        self.assertEqual(watchdog.stale_framework(
            {"surfaceflinger": (10, 500), "system_server": (12, 300)}), 12)
        self.assertIsNone(watchdog.stale_framework(
            {"surfaceflinger": (10, 100), "system_server": (12, 300)}))
        self.assertIsNone(watchdog.stale_framework({"surfaceflinger": (10, 500)}))

    def test_stale_framework_is_restarted_once_after_settling(self):
        process(self.proc, 10, "surfaceflinger", 500)
        process(self.proc, 12, "system_server", 300)
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds
            if now[0] > 20:
                raise StopIteration

        with patch("luma_android.graphics_watchdog.restart_android") as restart:
            with self.assertRaises(StopIteration):
                watchdog.monitor(self.proc, sleep=sleep, clock=lambda: now[0])
        restart.assert_called()
        self.assertLessEqual(restart.call_count, watchdog.MAX_RESTARTS)

    def test_restarts_are_bounded(self):
        process(self.proc, 10, "surfaceflinger", 500)
        process(self.proc, 12, "system_server", 300)
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds
            if now[0] > 300:
                raise StopIteration

        with patch("luma_android.graphics_watchdog.restart_android") as restart:
            with self.assertRaises(StopIteration):
                watchdog.monitor(self.proc, sleep=sleep, clock=lambda: now[0])
        self.assertEqual(restart.call_count, watchdog.MAX_RESTARTS)

    def test_recovery_starts_the_owner_unit(self):
        entry = self.proc / "40"
        entry.mkdir()
        (entry / "cmdline").write_bytes(b"/usr/bin/python3\0/usr/libexec/luma-waydroid-session\0")
        with patch("luma_android.graphics_watchdog.pwd.getpwuid") as user, \
             patch("luma_android.graphics_watchdog.subprocess.run") as run:
            user.return_value.pw_name = "luma"
            watchdog.restart_android(self.proc)
        run.assert_called_once_with(
            ["/usr/bin/systemctl", "--no-block", "start", "luma-android-recover@luma.service"],
            check=False, timeout=10)

    def test_no_session_owner_restarts_nothing(self):
        with patch("luma_android.graphics_watchdog.subprocess.run") as run:
            watchdog.restart_android(self.proc)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
