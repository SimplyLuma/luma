import unittest
from types import SimpleNamespace
from unittest.mock import patch
from luma_installer.backends import (LAUNCHER_METADATA_VERSION, _container_desktop,
                                     refresh_capsule_launcher)
from luma_installer.errors import InstallerError

class PackageLaunchers(unittest.TestCase):
    def test_deb_only_uses_owned_launcher(self):
        calls = []
        def run(argv):
            calls.append(argv)
            if 'dpkg-query' in argv:
                return SimpleNamespace(stdout='/usr/share/applications/steam.desktop\n/usr/lib/steam/steam.desktop\n/usr/share/doc/steam/README\n')
            self.assertEqual(argv[-1], '/usr/share/applications/steam.desktop')
            return SimpleNamespace(stdout='[Desktop Entry]\nType=Application\nName=Steam\nExec=/usr/bin/steam\n')
        with patch('luma_installer.backends._run', side_effect=run):
            text, path = _container_desktop('test', 'steam-launcher', 'deb')
        self.assertIn('Name=Steam', text)
        self.assertEqual(calls[0][-3:], ['dpkg-query', '-L', 'steam-launcher'])
        self.assertEqual(path, '/usr/share/applications/steam.desktop')

    def test_no_owned_launcher_does_not_select_dependency(self):
        with patch('luma_installer.backends._run', return_value=SimpleNamespace(stdout='/usr/bin/helper\n')) as run:
            with self.assertRaisesRegex(InstallerError, 'no application launcher of its own'):
                _container_desktop('test', 'helper', 'deb')
        # The shared directory is never read after the package answered for itself.
        self.assertEqual(len(run.call_args_list), 1)

    def test_dependency_entry_is_not_adopted_when_the_package_ships_none(self):
        # The Steam .deb pulls in xterm, whose uxterm.desktop sorts first.
        listing = ('/usr/share/applications/uxterm.desktop\n'
                   '/usr/share/applications/xterm.desktop\n'
                   '/usr/share/applications/steam.desktop\n')
        def run(argv):
            if 'dpkg-query' in argv:
                return SimpleNamespace(stdout='/usr/bin/steam\n/usr/share/doc/steam/README\n')
            return SimpleNamespace(stdout=listing)
        with patch('luma_installer.backends._run', side_effect=run):
            with self.assertRaisesRegex(InstallerError, 'steam-launcher'):
                _container_desktop('test', 'steam-launcher', 'deb', 'steam-launcher')

    def test_hidden_launcher_is_rejected(self):
        def run(argv):
            if 'dpkg-query' in argv:
                return SimpleNamespace(stdout='/usr/share/applications/steam.desktop\n')
            return SimpleNamespace(stdout='[Desktop Entry]\nType=Application\nName=Steam\nHidden=true\n')
        with patch('luma_installer.backends._run', side_effect=run):
            with self.assertRaises(InstallerError):
                _container_desktop('test', 'steam', 'deb')

    def test_package_cannot_be_query_option(self):
        with patch('luma_installer.backends._run') as run:
            with self.assertRaises(InstallerError):
                _container_desktop('test', '--help', 'deb')
            run.assert_not_called()

    def test_directory_scan_picks_the_named_application_not_the_first(self):
        entries = {
            '/usr/share/applications/uxterm.desktop':
                '[Desktop Entry]\nType=Application\nName=UXTerm\nExec=uxterm\n',
            '/usr/share/applications/xterm.desktop':
                '[Desktop Entry]\nType=Application\nName=XTerm\nExec=xterm\n',
            '/usr/share/applications/steam.desktop':
                '[Desktop Entry]\nType=Application\nName=Steam\nExec=/usr/bin/steam %U\n',
        }
        def run(argv):
            if 'find' in ' '.join(argv):
                return SimpleNamespace(stdout='\n'.join(sorted(entries)) + '\n')
            return SimpleNamespace(stdout=entries[argv[-1]])
        with patch('luma_installer.backends._run', side_effect=run):
            text, path = _container_desktop('test', '', 'deb', 'steam-launcher')
        self.assertIn('Name=Steam', text)
        self.assertEqual(path, '/usr/share/applications/steam.desktop')

    def test_directory_scan_refuses_to_guess_a_neighbour(self):
        entries = {
            '/usr/share/applications/uxterm.desktop':
                '[Desktop Entry]\nType=Application\nName=UXTerm\nExec=uxterm\n',
            '/usr/share/applications/xterm.desktop':
                '[Desktop Entry]\nType=Application\nName=XTerm\nExec=xterm\n',
        }
        def run(argv):
            if 'find' in ' '.join(argv):
                return SimpleNamespace(stdout='\n'.join(sorted(entries)) + '\n')
            return SimpleNamespace(stdout=entries[argv[-1]])
        with patch('luma_installer.backends._run', side_effect=run):
            with self.assertRaisesRegex(InstallerError, 'did not guess'):
                _container_desktop('test', '', 'deb', 'steam-launcher')

    def test_owned_launcher_is_chosen_by_name_among_its_own_entries(self):
        entries = {
            '/usr/share/applications/audacity.desktop':
                '[Desktop Entry]\nType=Application\nName=Audacity\nExec=audacity %F\n',
            '/usr/share/applications/audacity-setup.desktop':
                '[Desktop Entry]\nType=Application\nName=Audacity Setup\nExec=audacity-setup\n',
        }
        def run(argv):
            if 'dpkg-query' in argv:
                return SimpleNamespace(stdout='\n'.join(sorted(entries)) + '\n')
            return SimpleNamespace(stdout=entries[argv[-1]])
        with patch('luma_installer.backends._run', side_effect=run):
            _text, path = _container_desktop('test', 'audacity', 'deb', 'Audacity')
        self.assertEqual(path, '/usr/share/applications/audacity.desktop')

    def test_refresh_uses_recorded_package_and_updates_identity(self):
        calls = []
        def run(argv):
            calls.append(argv)
            if argv[1:3] == ['create', '--network=none']:
                return SimpleNamespace(stdout='metadata-container\n')
            if 'dpkg-query' in argv:
                return SimpleNamespace(stdout='/usr/share/applications/steam.desktop\n')
            if argv[-1] == '/usr/share/applications/steam.desktop':
                return SimpleNamespace(stdout='[Desktop Entry]\nType=Application\nName=Steam\nExec=/usr/bin/steam\nStartupWMClass=steam\n')
            return SimpleNamespace(stdout='')
        record = {'application_id': 'deb-steam', 'format': 'deb', 'package': 'steam',
                  'image': 'localhost/luma-steam:abc', 'launcher_metadata_version': 3}
        with patch('luma_installer.backends._run', side_effect=run), \
             patch('luma_installer.backends._export_container_icon', return_value='steam'), \
             patch('luma_installer.backends.subprocess.run'), \
             patch('luma_installer.backends.write_launcher'), \
             patch('luma_installer.backends.write_record') as write:
            updated = refresh_capsule_launcher(record)
        query = next(call for call in calls if 'dpkg-query' in call)
        self.assertEqual(query[-1], 'steam')
        self.assertEqual(updated['desktop_path'], '/usr/share/applications/steam.desktop')
        self.assertEqual(updated['command'], '/usr/bin/steam')
        self.assertEqual(updated['startup_wm_class'], 'steam')
        self.assertEqual(updated['launcher_metadata_version'], LAUNCHER_METADATA_VERSION)
        self.assertFalse(updated['terminal'])
        self.assertEqual(updated['exported_command'], '')
        write.assert_called_once()

    def test_refresh_finds_the_package_from_the_launcher_it_shipped(self):
        # Records written before the package name was stored still name the
        # launcher the package shipped, which is enough to bring them up to
        # date instead of leaving them on their first launcher for good.
        calls = []
        def run(argv):
            calls.append(argv)
            if argv[1:3] == ['create', '--network=none']:
                return SimpleNamespace(stdout='metadata-container\n')
            if 'rpm' in argv and '-ql' in argv:
                return SimpleNamespace(stdout='/usr/share/applications/chatgpt.desktop\n')
            if argv[-1] == '/usr/share/applications/chatgpt.desktop':
                return SimpleNamespace(stdout='[Desktop Entry]\nType=Application\nName=ChatGPT\n'
                                              'Exec=chatgpt %U\nMimeType=x-scheme-handler/codex;\n')
            return SimpleNamespace(stdout='')
        record = {'application_id': 'rpm-chatgpt-25ec6b75b803', 'format': 'rpm',
                  'desktop_path': '/usr/share/applications/chatgpt.desktop',
                  'image': 'localhost/luma-rpm-chatgpt:abc', 'launcher_metadata_version': 4}
        with patch('luma_installer.backends._run', side_effect=run), \
             patch('luma_installer.backends._export_container_icon', return_value='chatgpt'), \
             patch('luma_installer.backends.subprocess.run'), \
             patch('luma_installer.backends.write_launcher') as launcher, \
             patch('luma_installer.backends.write_record'):
            updated = refresh_capsule_launcher(record)
        self.assertEqual(updated['name'], 'ChatGPT')
        self.assertEqual(updated['mime_types'], ['x-scheme-handler/codex'])
        self.assertEqual(updated['launcher_metadata_version'], LAUNCHER_METADATA_VERSION)
        self.assertEqual(launcher.call_args[0][1], 'ChatGPT')
        self.assertEqual(launcher.call_args[0][6], ['x-scheme-handler/codex'])

    def test_refresh_preserves_record_on_ambiguous_identity(self):
        record = {'application_id': 'deb-steam', 'format': 'deb',
                  'image': 'localhost/luma-steam:abc', 'launcher_metadata_version': 3}
        with patch('luma_installer.backends._run') as run, \
             patch('luma_installer.backends.write_record') as write:
            with self.assertRaises(InstallerError):
                refresh_capsule_launcher(record)
        run.assert_not_called()
        write.assert_not_called()

class LaunchAfterFailedMigration(unittest.TestCase):
    def test_an_unmigratable_record_still_opens(self):
        # Records written before package identity was recorded cannot be
        # migrated. That is metadata housekeeping; Open must still open.
        from luma_installer import launcher
        record = {'application_id': 'deb-steam-launcher-abc', 'format': 'deb',
                  'image': 'localhost/luma-deb-steam:abc', 'command': 'uxterm',
                  'sha256': 'a' * 64, 'launcher_metadata_version': 4}
        with patch('luma_installer.launcher.read_record', return_value=record), \
             patch('luma_installer.backends.refresh_capsule_launcher',
                   side_effect=InstallerError('Cannot migrate a capsule launcher')), \
             patch('luma_installer.launcher._launch_deb', return_value=0) as launch:
            self.assertEqual(launcher.main(['deb-steam-launcher-abc']), 0)
        launch.assert_called_once()
        self.assertIs(launch.call_args[0][0], record)


if __name__ == '__main__': unittest.main()
