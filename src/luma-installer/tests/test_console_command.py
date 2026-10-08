import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer.desktop import (command_name, desktop_is_console, export_command, parse_desktop,
                                    remove_command, write_launcher)

HTOP = (
    '[Desktop Entry]\n'
    'Type=Application\n'
    'Name=Htop\n'
    'Comment=Show System Processes\n'
    'Icon=htop\n'
    'Exec=htop\n'
    'Terminal=true\n'
    'Categories=System;Monitor;ConsoleOnly;\n'
)

STEAM = (
    '[Desktop Entry]\n'
    'Type=Application\n'
    'Name=Steam\n'
    'Exec=/usr/bin/steam %U\n'
    'Terminal=false\n'
)


class ConsoleDetection(unittest.TestCase):
    def test_terminal_true_is_console(self):
        self.assertTrue(desktop_is_console(HTOP))

    def test_graphical_entry_is_not_console(self):
        self.assertFalse(desktop_is_console(STEAM))

    def test_console_only_category_without_terminal_key(self):
        self.assertTrue(desktop_is_console(
            '[Desktop Entry]\nType=Application\nName=Tool\nExec=tool\nCategories=ConsoleOnly;\n'))

    def test_command_name_comes_from_the_launcher_command(self):
        self.assertEqual(command_name(parse_desktop(HTOP)[3]), 'htop')
        self.assertEqual(command_name(parse_desktop(STEAM)[3]), 'steam')

    def test_command_name_rejects_a_path_traversal(self):
        self.assertEqual(command_name('../../bin/sh'), 'sh')
        self.assertEqual(command_name(''), '')


class CommandExport(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = patch('luma_installer.desktop.Path.home', return_value=Path(self.home.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        # The real PATH is not part of the fixture: a shim this machine happens
        # to have already exported must not decide the outcome of these tests.
        which = patch('luma_installer.desktop.shutil.which', return_value=None)
        which.start()
        self.addCleanup(which.stop)

    def test_export_publishes_an_executable_shim(self):
        name, refusal = export_command('deb-htop-abc', 'htop')
        self.assertEqual((name, refusal), ('htop', ''))
        shim = Path(self.home.name) / '.local/bin/htop'
        self.assertTrue(os.access(shim, os.X_OK))
        contents = shim.read_text(encoding='utf-8')
        self.assertIn("luma-capsule-launch 'deb-htop-abc' \"$@\"", contents)

    def test_export_refuses_to_shadow_an_existing_command(self):
        with patch('luma_installer.desktop.shutil.which', return_value='/usr/bin/ls'):
            name, refusal = export_command('deb-evil-abc', 'ls')
        self.assertEqual(name, '')
        self.assertIn('/usr/bin/ls', refusal)
        self.assertFalse((Path(self.home.name) / '.local/bin/ls').exists())

    def test_export_refuses_a_foreign_file_already_in_place(self):
        root = Path(self.home.name) / '.local/bin'
        root.mkdir(parents=True)
        (root / 'tool').write_text('#!/bin/sh\necho mine\n', encoding='utf-8')
        with patch('luma_installer.desktop.shutil.which', return_value=None):
            name, refusal = export_command('deb-tool-abc', 'tool')
        self.assertEqual(name, '')
        self.assertIn('not Luma-managed', refusal)
        self.assertIn('echo mine', (root / 'tool').read_text(encoding='utf-8'))

    def test_reinstall_replaces_our_own_shim(self):
        export_command('deb-htop-abc', 'htop')
        with patch('luma_installer.desktop.shutil.which',
                   return_value=str(Path(self.home.name) / '.local/bin/htop')):
            name, refusal = export_command('deb-htop-def', 'htop')
        self.assertEqual((name, refusal), ('htop', ''))
        self.assertIn('deb-htop-def',
                      (Path(self.home.name) / '.local/bin/htop').read_text(encoding='utf-8'))

    def test_removal_only_deletes_a_shim_we_own(self):
        export_command('deb-htop-abc', 'htop')
        root = Path(self.home.name) / '.local/bin'
        (root / 'keepme').write_text('#!/bin/sh\n', encoding='utf-8')
        remove_command('htop')
        remove_command('keepme')
        self.assertFalse((root / 'htop').exists())
        self.assertTrue((root / 'keepme').exists())

    def test_removal_ignores_an_unsafe_name(self):
        remove_command('../../../etc/passwd')
        remove_command(None)


class ConsoleLauncherEntry(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = patch.dict(os.environ, {'XDG_DATA_HOME': self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_console_entry_asks_for_a_terminal(self):
        path = write_launcher('deb-htop-abc', 'Htop', 'Processes', 'htop', '', True)
        contents = path.read_text(encoding='utf-8')
        self.assertIn('Terminal=true', contents)
        self.assertIn('StartupNotify=false', contents)

    def test_graphical_entry_keeps_its_previous_shape(self):
        path = write_launcher('deb-steam-abc', 'Steam', 'Games', 'steam', 'steam')
        contents = path.read_text(encoding='utf-8')
        self.assertIn('Terminal=false', contents)
        self.assertIn('StartupNotify=true', contents)
        self.assertIn('StartupWMClass=steam', contents)


if __name__ == '__main__':
    unittest.main()
