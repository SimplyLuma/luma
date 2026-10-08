import configparser
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from luma_installer.facts import flatpak_metadata
from luma_installer.model import PackageReport
from luma_installer.progress import Cancelled, Transaction, current, phase
from luma_installer import workflow
from luma_installer.errors import InstallerError


class WorkflowTests(unittest.TestCase):
    def test_progress_cancellation_does_not_cross_commit_boundary(self):
        observed = []
        tx = Transaction(observed.append)
        tx.phase('Inspect', 0, True)
        self.assertTrue(tx.cancel())
        with self.assertRaises(Cancelled): tx.phase('Install', .3)
        self.assertEqual(len(observed), 1)
        tx = Transaction(observed.append)
        tx.phase('Commit', .5, False)
        self.assertFalse(tx.cancel())
        self.assertFalse(tx.cancelled.is_set())

    def test_changed_source_never_reaches_backend(self):
        report = PackageReport(Path('/tmp/input.rpm'), 'rpm', 'Name', '', 1, 'a'*64)
        with patch('luma_installer.workflow.fingerprint', return_value=(1, 'b'*64)), patch('luma_installer.workflow.backends.install') as backend:
            with self.assertRaisesRegex(InstallerError, 'changed after review'): workflow.install(report)
            backend.assert_not_called()

    def test_no_record_is_not_reported_as_registered(self):
        report = PackageReport(Path('/tmp/input.rpm'), 'rpm', 'Name', '', 1, 'a'*64)
        with patch('luma_installer.workflow.fingerprint', return_value=(1, 'a'*64)), patch('luma_installer.workflow.backends.install', return_value='Name'), patch('luma_installer.workflow.iter_records', return_value=[]):
            with self.assertRaisesRegex(InstallerError, 'without publishing'): workflow.install(report)

    def test_permissions_are_read_from_metadata_and_keep_explicit_denials(self):
        details = flatpak_metadata('[Application]\nname=org.example.App\nruntime=org.example.Runtime/x86_64/1\n[Context]\nfilesystems=xdg-download;!home;\n[Session Bus Policy]\norg.example.Service=talk\n')
        self.assertEqual(details['Context · filesystems'], 'xdg-download;!home;')
        self.assertEqual(details['Session Bus Policy · org.example.service'], 'talk')
        self.assertNotIn('Network', details)

    def test_failed_file_removal_keeps_the_error_visible(self):
        from luma_installer.manager import _safe_remove_tree
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root / 'app'; target.mkdir()
            with patch('luma_installer.manager.shutil.rmtree', side_effect=PermissionError('denied')):
                with self.assertRaisesRegex(InstallerError, 'could not be removed'):
                    _safe_remove_tree(target, root)
            self.assertTrue(target.exists())

    def test_appimage_launcher_symlink_cannot_change_a_host_file(self):
        from luma_installer.backends import _install_appimage
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary); outside=home/'private-file'; outside.write_text('private')
            outside.chmod(0o600)
            report=PackageReport(home/'a.AppImage','appimage','Example','',1,'a'*64)
            def extract(args):
                partial=Path(args[args.index('-d')+1]);partial.mkdir(parents=True)
                (partial/'AppRun').symlink_to(outside)
            with patch('luma_installer.backends.Path.home',return_value=home), \
                 patch('luma_installer.backends.require_free_space'), \
                 patch('luma_installer.backends.stage_user_copy',return_value=report.path), \
                 patch('luma_installer.backends.appimage_payload.locate',return_value=('squashfs',0)), \
                 patch('luma_installer.backends._run',side_effect=extract):
                with self.assertRaisesRegex(InstallerError,'outside its application payload'):
                    _install_appimage(report)
            self.assertEqual(outside.stat().st_mode & 0o777,0o600)
            self.assertEqual(outside.read_text(),'private')

    def test_old_android_record_uses_its_registered_file_icon(self):
        from unittest.mock import Mock
        other = Mock(); other.get_id.return_value = 'waydroid.org.example.Other.desktop'
        other.get_string.return_value = None
        app = Mock(); app.get_id.return_value = 'waydroid.org.example.App.desktop'
        app.get_string.return_value = None
        app.get_icon.return_value.to_string.return_value = '/home/test/App Icon.png'
        record = {'format': 'android', 'package': 'org.example.App', 'icon': 'application-x-executable'}
        self.assertEqual(workflow.installed_icon(record, [other, app]), '/home/test/App Icon.png')
        self.assertEqual(record['icon'], 'application-x-executable')
        self.assertEqual(workflow.installed_icon(record, [other]), 'application-x-executable')

    def test_open_uses_literal_application_identity(self):
        with patch('luma_installer.workflow.subprocess.Popen') as launch:
            workflow.open_application({'format':'flatpak','flatpak_id':'org.example.App'})
            launch.assert_called_once_with(['flatpak','run','org.example.App'], start_new_session=True, close_fds=True)

if __name__ == '__main__': unittest.main()
