# SPDX-License-Identifier: Apache-2.0
"""An optional package handoff must preserve the host's selected default."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gi.repository import GLib
from luma_installer import service


class DefaultInstallDispatch(unittest.TestCase):
    def test_known_installer_aliases_launch_literal_path_once(self):
        for app_id in service.DEFAULT_INSTALLER_IDS:
            with self.subTest(app_id=app_id):
                path = Path('/tmp/space ; $(no command).APK')
                selected = Mock(); selected.get_id.return_value = app_id
                with patch.object(service.Gio.AppInfo, 'get_default_for_type', return_value=selected) as default, patch.object(service.subprocess, 'Popen') as launch:
                    self.assertTrue(service.default_android_install(path))
                    default.assert_called_once_with('application/vnd.android.package-archive', False)
                    launch.assert_called_once_with(['/usr/bin/luma-install', str(path)], start_new_session=True, close_fds=True)

    def test_custom_or_missing_default_never_launches(self):
        for app_id in ('org.example.ArchiveViewer.desktop', 'org.projectluma.AndroidInstaller.desktop', None):
            with self.subTest(app_id=app_id):
                selected = Mock() if app_id else None
                if selected: selected.get_id.return_value = app_id
                with patch.object(service.Gio.AppInfo, 'get_default_for_type', return_value=selected), patch.object(service.subprocess, 'Popen') as launch:
                    self.assertFalse(service.default_android_install(Path('/tmp/test.apk')))
                    launch.assert_not_called()

    def test_split_packages_query_their_separate_default(self):
        for suffix in ('.apks', '.XAPK', '.apkm'):
            with self.subTest(suffix=suffix), patch.object(service.Gio.AppInfo, 'get_default_for_type', return_value=None) as default:
                self.assertFalse(service.default_android_install(Path('/tmp/test' + suffix)))
                default.assert_called_once_with('application/vnd.projectluma.android-package-set', False)

    def test_other_formats_do_not_query_or_launch(self):
        with patch.object(service.Gio.AppInfo, 'get_default_for_type') as default, patch.object(service.subprocess, 'Popen') as launch:
            for name in ('test.exe', 'test.rpm', 'test.txt'):
                self.assertFalse(service.default_android_install(Path(name)))
            default.assert_not_called(); launch.assert_not_called()

    def test_bus_method_returns_handled_boolean(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'test.apk'; path.write_bytes(b'not executed')
            for handled in (True, False):
                invocation = Mock()
                with patch.object(service, 'default_android_install', return_value=handled) as dispatch:
                    service.Service().on_method_call(None, None, None, None, 'RequestDefaultInstall', GLib.Variant('(s)', (path.as_uri(),)), invocation)
                dispatch.assert_called_once_with(path)
                self.assertEqual(invocation.return_value.call_args.args[0].unpack(), (handled,))
                invocation.return_dbus_error.assert_not_called()

    def test_remote_symlink_and_non_package_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'test.apk'; path.write_bytes(b'not executed')
            link = Path(root) / 'link.apk'; link.symlink_to(path)
            text = Path(root) / 'test.txt'; text.write_text('not a package')
            for uri in ('https://example.invalid/test.apk', link.as_uri(), text.as_uri()):
                invocation = Mock()
                with patch.object(service, 'default_android_install') as dispatch:
                    service.Service().on_method_call(None, None, None, None, 'RequestDefaultInstall', GLib.Variant('(s)', (uri,)), invocation)
                dispatch.assert_not_called(); invocation.return_value.assert_not_called(); invocation.return_dbus_error.assert_called_once()

    def test_launch_failure_is_not_reported_as_handled(self):
        selected = Mock(); selected.get_id.return_value = 'io.luma.Valet.desktop'
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'test.apk'; path.write_bytes(b'not executed'); invocation = Mock()
            with patch.object(service.Gio.AppInfo, 'get_default_for_type', return_value=selected), patch.object(service.subprocess, 'Popen', side_effect=OSError('cannot start')):
                service.Service().on_method_call(None, None, None, None, 'RequestDefaultInstall', GLib.Variant('(s)', (path.as_uri(),)), invocation)
            invocation.return_value.assert_not_called(); invocation.return_dbus_error.assert_called_once()


if __name__ == '__main__':
    unittest.main()
