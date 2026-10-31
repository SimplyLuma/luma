# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from luma_android.errors import LumaAndroidError
from luma_android.installer_activation import launch_installer


class InstallerActivation(unittest.TestCase):
    def test_session_manager_activation_preserves_literal_path_and_display(self):
        path = Path('/home/test/Downloads/a $(touch secret); --system.apk')
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-1',
                'XDG_ACTIVATION_TOKEN': 'synthetic-token', 'UNRELATED_SECRET': 'must-not-copy'}, clear=True), \
                patch('luma_android.installer_activation.subprocess.run') as run:
            launch_installer(path)
            first = run.call_args
            launch_installer(path)
            second = run.call_args
        args = first.args[0]
        self.assertEqual(args[:4], ['/usr/bin/systemd-run', '--user', '--quiet', '--collect'])
        self.assertIn('--property=Type=exec', args)
        self.assertIn('--setenv=WAYLAND_DISPLAY=wayland-1', args)
        self.assertIn('--setenv=XDG_ACTIVATION_TOKEN=synthetic-token', args)
        self.assertFalse(any('UNRELATED_SECRET' in arg for arg in args))
        self.assertEqual(args[-2:], ['/usr/bin/luma-android-installer', str(path)])
        self.assertNotEqual(args[4], second.args[0][4])
        self.assertNotIn('shell', first.kwargs)
        self.assertTrue(first.kwargs['check'])
        self.assertEqual(first.kwargs['timeout'], 5)

    def test_rejected_or_unavailable_manager_reports_launch_failure(self):
        for error in (FileNotFoundError('systemd-run'),
                subprocess.CalledProcessError(1, 'systemd-run', stderr='private manager state'),
                subprocess.TimeoutExpired('systemd-run', 5)):
            with self.subTest(error=type(error).__name__), \
                    patch('luma_android.installer_activation.subprocess.run', side_effect=error):
                with self.assertRaisesRegex(LumaAndroidError, 'installer could not open') as result:
                    launch_installer(Path('/tmp/fixture.apk'))
                self.assertNotIn('private manager state', str(result.exception))

if __name__ == '__main__':
    unittest.main()
