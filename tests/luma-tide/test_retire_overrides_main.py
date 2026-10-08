# SPDX-License-Identifier: Apache-2.0
"""The systemd-unit entry point layered on top of `retire_overrides.py`:
after retiring anything, it bumps the watched directories' mtimes (so GIO's
inotify-backed monitors reliably notice even if the original move event was
missed) and asks the session bus to reload — both best-effort fallbacks for
the case where the unit runs late, not load-bearing for correctness.
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from luma_tide import retire_overrides_main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class BumpMtimeTests(unittest.TestCase):
    def test_bumps_the_parent_directory_of_a_retired_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "dbus-1" / "services"
            directory.mkdir(parents=True)
            before = directory.stat().st_mtime
            # A file that no longer exists (it was just moved away) --
            # bumping the parent must not require the file itself to exist.
            retire_overrides_main._bump_mtime(directory / "org.projectluma.Tide.service")
            after = directory.stat().st_mtime
            self.assertGreaterEqual(after, before)

    def test_a_missing_parent_directory_is_logged_not_raised(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "does" / "not" / "exist" / "x.service"
        with self.assertLogs("luma-tide-retire-preview-overrides", level="ERROR"):
            retire_overrides_main._bump_mtime(missing)  # must not raise


class ReloadSessionBusTests(unittest.TestCase):
    def test_missing_gi_is_a_logged_warning_not_a_crash(self) -> None:
        # Force the ImportError path deterministically -- whether `gi` is
        # actually importable depends on the environment (it isn't on a
        # plain dev machine, but it is wherever python3-gobject is
        # installed, e.g. the real RPM build sandbox), so this must not
        # rely on ambient sandbox contents to exercise the fallback.
        log = mock.Mock()
        with mock.patch.dict("sys.modules", {"gi": None}):
            retire_overrides_main._reload_session_bus(log)
        log.warning.assert_called_once()
        log.exception.assert_not_called()

    def test_a_reload_failure_once_gi_is_available_is_logged_not_raised(self) -> None:
        # Covers the other real-world shape this takes: gi is importable
        # (python3-gobject is a hard Requires) but there's no session bus
        # to actually talk to -- e.g. inside an RPM %check sandbox. Must
        # degrade the same way: logged, never a crash.
        log = mock.Mock()
        retire_overrides_main._reload_session_bus(log)
        self.assertFalse(log.warning.called and log.exception.called, "not both at once")
        self.assertTrue(
            log.warning.called or log.exception.called,
            "a failure to reload must always be logged one way or another",
        )


class MainTests(unittest.TestCase):
    def test_main_retires_and_attempts_a_reload_only_when_something_was_retired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_home = Path(tmp) / "data"
            state_home = Path(tmp) / "state"
            _write(
                data_home / "dbus-1/services/org.projectluma.Tide.service",
                "[D-BUS Service]\nExec=/home/someone/dev-preview/run-preview\n",
            )
            env = {
                **os.environ,
                "XDG_DATA_HOME": str(data_home),
                "XDG_STATE_HOME": str(state_home),
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                retire_overrides_main, "_reload_session_bus"
            ) as reload_mock:
                code = retire_overrides_main.main()

        self.assertEqual(code, 0)
        reload_mock.assert_called_once()

    def test_main_does_not_attempt_a_reload_when_nothing_was_retired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_home = Path(tmp) / "data"
            state_home = Path(tmp) / "state"
            env = {
                **os.environ,
                "XDG_DATA_HOME": str(data_home),
                "XDG_STATE_HOME": str(state_home),
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                retire_overrides_main, "_reload_session_bus"
            ) as reload_mock:
                code = retire_overrides_main.main()

        self.assertEqual(code, 0)
        reload_mock.assert_not_called()


class ReloadSessionBusTimeoutTests(unittest.TestCase):
    """A real systemd --user session test (not just these mocks) showed
    Gio.bus_get_sync() can block well past its own supposed default
    timeout while dbus.socket's activation of dbus-broker.service settles
    -- a oneshot unit must never be able to hang because of that."""

    def test_a_reload_that_finishes_quickly_is_not_reported_as_timed_out(self) -> None:
        log = mock.Mock()
        with mock.patch.object(retire_overrides_main, "_reload_session_bus", lambda _log: None):
            retire_overrides_main._reload_session_bus_with_timeout(log, timeout=1.0)
        log.warning.assert_not_called()

    def test_a_reload_that_never_returns_is_bounded_by_the_timeout_not_blocked_on(self) -> None:
        import time

        never_returns = threading.Event()

        def hang(_log: object) -> None:
            never_returns.wait()  # blocks until the test explicitly releases it

        log = mock.Mock()
        started = time.monotonic()
        try:
            with mock.patch.object(retire_overrides_main, "_reload_session_bus", hang):
                retire_overrides_main._reload_session_bus_with_timeout(log, timeout=0.2)
        finally:
            never_returns.set()  # let the leaked daemon thread exit
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0, "must return promptly once the timeout elapses")
        log.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
