# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from luma_tester.guest_preview import CONFIGURE


class PreviewDefaultsTests(unittest.TestCase):
    def test_existing_password_is_preserved_when_startup_allowance_is_added(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            marker = home / '.local/state/try-luma/preview-configured-v2'
            marker.parent.mkdir(parents=True)
            marker.write_text('1\n')
            with patch.object(Path, 'home', return_value=home), patch.object(subprocess, 'run') as run, patch.object(subprocess, 'check_output', return_value='failed\n'):
                exec(CONFIGURE, {})
                calls = [call.args[0] for call in run.call_args_list]
                self.assertEqual(calls, [['systemctl', '--user', 'daemon-reload'], ['sudo', 'systemctl', '--no-block', 'restart', 'gdm']])
                self.assertEqual((home / '.config/systemd/user/org.gnome.Shell@user.service.d/60-try-luma.conf').read_text(), '[Service]\nTimeoutStartSec=300\n')
                run.reset_mock()
                exec(CONFIGURE, {})
                run.assert_not_called()

    def test_active_desktop_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            marker = home / '.local/state/try-luma/preview-configured-v2'
            marker.parent.mkdir(parents=True)
            marker.touch()
            with patch.object(Path, 'home', return_value=home), patch.object(subprocess, 'run') as run, patch.object(subprocess, 'check_output', return_value='active\n'):
                exec(CONFIGURE, {})
                self.assertEqual([call.args[0] for call in run.call_args_list], [['systemctl', '--user', 'daemon-reload']])
