# SPDX-License-Identifier: Apache-2.0
"""Retiring a stale Tide dev-preview launch override (see
`luma_tide/retire_overrides.py`'s docstring for the real incident this
addresses): a `org.projectluma.Tide.service` and/or
`org.projectluma.Tide.desktop` left under `$XDG_DATA_HOME/dbus-1/services/`
or `$XDG_DATA_HOME/applications/`, pointing anywhere other than the real
installed binary, must be moved (never deleted) out of the way -- and a
file that already points at the real binary must be left completely alone.

All ids/paths/servers in this file are invented placeholders, never any
real person's.
"""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from luma_tide.retire_overrides import INSTALLED_EXEC, retire_stale_overrides


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class RetireStaleOverridesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_home = Path(self.tmp.name) / "data"
        self.state_home = Path(self.tmp.name) / "state"
        self.log = logging.getLogger("test-retire-overrides")

    def _service_path(self) -> Path:
        return self.data_home / "dbus-1/services/org.projectluma.Tide.service"

    def _desktop_path(self) -> Path:
        return self.data_home / "applications/org.projectluma.Tide.desktop"

    def _retire(self):
        return retire_stale_overrides(
            log=self.log, data_home=self.data_home, state_home=self.state_home
        )

    def test_a_stale_service_override_is_moved_never_deleted(self) -> None:
        content = (
            "[D-BUS Service]\n"
            "Name=org.projectluma.Tide\n"
            "Exec=/home/someone/dev-preview/run-preview\n"
        )
        _write(self._service_path(), content)

        retired = self._retire()

        self.assertEqual(retired, [self._service_path()])
        self.assertFalse(self._service_path().exists(), "must be moved out of place")
        backups = list((self.state_home / "luma-tide").rglob("org.projectluma.Tide.service"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), content, "content must be preserved exactly")

    def test_a_stale_desktop_override_is_moved_never_deleted(self) -> None:
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Exec=/home/someone/dev-preview/run-preview %U\n"
        )
        _write(self._desktop_path(), content)

        retired = self._retire()

        self.assertEqual(retired, [self._desktop_path()])
        self.assertFalse(self._desktop_path().exists())
        backups = list((self.state_home / "luma-tide").rglob("org.projectluma.Tide.desktop"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), content)

    def test_a_service_file_already_pointing_at_the_real_binary_is_left_alone(self) -> None:
        _write(
            self._service_path(),
            f"[D-BUS Service]\nName=org.projectluma.Tide\nExec={INSTALLED_EXEC} --gapplication-service\n",
        )

        retired = self._retire()

        self.assertEqual(retired, [])
        self.assertTrue(self._service_path().exists(), "a correct override must not be touched")

    def test_a_desktop_file_with_a_bare_command_name_resolving_to_the_real_binary_is_left_alone(
        self,
    ) -> None:
        # This is exactly the shape of the *installed* system desktop file
        # (Exec=org.projectluma.Tide %U, resolved via $PATH) -- if someone's
        # user override happens to say the same thing, it's not stale.
        import os
        import stat

        fake_bin_dir = Path(self.tmp.name) / "bin"
        fake_bin_dir.mkdir()
        fake_installed = fake_bin_dir / "org.projectluma.Tide"
        fake_installed.write_text("#!/bin/sh\n")
        fake_installed.chmod(fake_installed.stat().st_mode | stat.S_IEXEC)

        _write(self._desktop_path(), "[Desktop Entry]\nExec=org.projectluma.Tide %U\n")

        old_path = os.environ.get("PATH", "")
        old_exec = None
        try:
            # Point $PATH at our fake binary and monkeypatch the module's
            # notion of the "real" installed path to match it, since the
            # real /usr/bin/org.projectluma.Tide obviously doesn't exist
            # in this sandbox.
            import luma_tide.retire_overrides as retire_overrides_module

            old_exec = retire_overrides_module.INSTALLED_EXEC
            retire_overrides_module.INSTALLED_EXEC = str(fake_installed)
            os.environ["PATH"] = f"{fake_bin_dir}:{old_path}"

            retired = self._retire()
        finally:
            os.environ["PATH"] = old_path
            if old_exec is not None:
                retire_overrides_module.INSTALLED_EXEC = old_exec

        self.assertEqual(retired, [])
        self.assertTrue(self._desktop_path().exists())

    def test_no_overrides_present_is_a_clean_no_op(self) -> None:
        self.assertEqual(self._retire(), [])
        self.assertFalse(self.state_home.exists(), "must not create anything when there's nothing to do")

    def test_running_twice_only_retires_once(self) -> None:
        _write(
            self._service_path(),
            "[D-BUS Service]\nExec=/home/someone/dev-preview/run-preview\n",
        )

        first = self._retire()
        second = self._retire()

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [], "nothing left to retire the second time")

    def test_both_overrides_present_are_both_retired_independently(self) -> None:
        _write(
            self._service_path(),
            "[D-BUS Service]\nExec=/home/someone/dev-preview/run-preview\n",
        )
        _write(self._desktop_path(), "[Desktop Entry]\nExec=/home/someone/dev-preview/run-preview %U\n")

        retired = self._retire()

        self.assertEqual(set(retired), {self._service_path(), self._desktop_path()})
        self.assertFalse(self._service_path().exists())
        self.assertFalse(self._desktop_path().exists())

    def test_on_retired_callback_fires_once_per_retired_path(self) -> None:
        _write(
            self._service_path(),
            "[D-BUS Service]\nExec=/home/someone/dev-preview/run-preview\n",
        )
        _write(self._desktop_path(), "[Desktop Entry]\nExec=/home/someone/dev-preview/run-preview %U\n")

        seen: list[Path] = []
        retire_stale_overrides(
            log=self.log,
            data_home=self.data_home,
            state_home=self.state_home,
            on_retired=seen.append,
        )

        self.assertEqual(set(seen), {self._service_path(), self._desktop_path()})

    def test_a_callback_failure_does_not_stop_the_other_file_from_being_retired(self) -> None:
        _write(
            self._service_path(),
            "[D-BUS Service]\nExec=/home/someone/dev-preview/run-preview\n",
        )
        _write(self._desktop_path(), "[Desktop Entry]\nExec=/home/someone/dev-preview/run-preview %U\n")

        def flaky(path: Path) -> None:
            raise RuntimeError("pretend the mtime bump failed")

        retired = retire_stale_overrides(
            log=self.log,
            data_home=self.data_home,
            state_home=self.state_home,
            on_retired=flaky,
        )

        self.assertEqual(len(retired), 2, "both files still get retired despite the callback failing")

    def test_a_file_with_no_recognizable_exec_line_is_left_alone(self) -> None:
        # Not our file shape at all -- must never be touched, let alone moved.
        _write(self._service_path(), "not actually a service file\n")

        retired = self._retire()

        self.assertEqual(retired, [])
        self.assertTrue(self._service_path().exists())


class StateHomeTests(unittest.TestCase):
    """systemd's StateDirectory=luma-tide (see the .service file) creates
    %h/.local/state/luma-tide itself and sets $STATE_DIRECTORY to it --
    preferring that over re-deriving the same path from $XDG_STATE_HOME
    sidesteps any chance of the two disagreeing, and (more importantly)
    means the backup destination never needs to already exist."""

    def test_prefers_state_directory_env_var_when_set(self) -> None:
        import os
        from unittest import mock

        import luma_tide.retire_overrides as retire_overrides_module

        with mock.patch.dict(os.environ, {"STATE_DIRECTORY": "/run/fake/.local/state/luma-tide"}):
            self.assertEqual(
                retire_overrides_module._state_home(), Path("/run/fake/.local/state")
            )

    def test_falls_back_to_xdg_state_home_when_unset(self) -> None:
        import os
        from unittest import mock

        import luma_tide.retire_overrides as retire_overrides_module

        env = dict(os.environ)
        env.pop("STATE_DIRECTORY", None)
        env["XDG_STATE_HOME"] = "/run/fake2/.local/state"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                retire_overrides_module._state_home(), Path("/run/fake2/.local/state")
            )


if __name__ == "__main__":
    unittest.main()
