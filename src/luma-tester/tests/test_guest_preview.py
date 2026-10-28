# SPDX-License-Identifier: Apache-2.0
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from luma_tester.guest_preview import CONFIGURE, FLATPAK_REPAIR, supported


class PreviewDefaultsTests(unittest.TestCase):
    def test_only_managed_luma_workspaces_have_preview_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = SimpleNamespace(directory=root, manifest=dict(user='luma',
                media='iso', image=dict(sha256='a'*64)))
            self.assertFalse(supported(session))
            (root/'workspace.qcow2').touch()
            (root/'setup.json').write_text(json.dumps(dict(phase='ready', sha256='a'*64)))
            self.assertTrue(supported(session))
            session.manifest['image']['sha256'] = 'b'*64
            self.assertFalse(supported(session))
            session.manifest['guest_protocol'] = 'luma-unified-v1'
            self.assertTrue(supported(session))
            session.manifest['user'] = 'custom'
            self.assertFalse(supported(session))

    def test_first_use_sets_public_password_and_disables_idle_lock_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(Path, 'home', return_value=home), patch.object(subprocess, 'run') as run, patch.object(subprocess, 'check_output', return_value='active\n'):
                exec(CONFIGURE, {})
                password_calls = [call for call in run.call_args_list if call.args[0] == ['sudo', 'chpasswd']]
                self.assertEqual(len(password_calls), 1)
                self.assertEqual(password_calls[0].kwargs['input'], 'luma:TryLuma\n')
                self.assertIn(['gsettings', 'set', 'org.gnome.desktop.screensaver', 'lock-enabled', 'false'], [call.args[0] for call in run.call_args_list])
                run.reset_mock()
                exec(CONFIGURE, {})
                run.assert_not_called()

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


@unittest.skipUnless(shutil.which('ostree'), 'OSTree is needed for real repository checks')
class FlatpakRepositoryTests(unittest.TestCase):
    def test_config_only_user_repository_is_initialized_without_changing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            repo = home/'.local/share/flatpak/repo'
            repo.mkdir(parents=True)
            config = repo/'config'
            original = '[core]\nrepo_version=1\nmode=bare-user-only\n[remote "example"]\nurl=https://example.invalid/repo\ngpg-verify=true\n'
            config.write_text(original)
            with patch.object(Path, 'home', return_value=home):
                exec(FLATPAK_REPAIR, {})
                self.assertEqual(config.read_text(), original)
                self.assertTrue((repo/'objects').is_dir())
                subprocess.run(['ostree', '--repo='+str(repo), 'refs'], check=True, capture_output=True)
                with patch.object(subprocess, 'run') as run:
                    exec(FLATPAK_REPAIR, {})
                    run.assert_not_called()

    def test_existing_app_data_and_nonempty_repositories_are_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            repo = home/'.local/share/flatpak/repo'
            repo.mkdir(parents=True)
            (repo/'config').write_text('[core]\nmode=bare-user-only\n')
            (repo/'objects').mkdir()
            (repo/'objects/keep').write_text('app data')
            with patch.object(Path, 'home', return_value=home), patch.object(subprocess, 'run') as run:
                exec(FLATPAK_REPAIR, {})
                run.assert_not_called()
                self.assertEqual((repo/'objects/keep').read_text(), 'app data')
                (repo/'objects/keep').unlink()
                (repo/'objects').rmdir()
                (repo/'refs').mkdir()
                exec(FLATPAK_REPAIR, {})
                run.assert_not_called()
