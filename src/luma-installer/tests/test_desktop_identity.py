import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from luma_installer.desktop import desktop_window_class, write_launcher
from luma_installer.facts import rpm_desktop
from luma_installer.errors import InstallerError

class IdentityTests(unittest.TestCase):
    def test_window_class_survives_managed_launcher(self):
        contents='[Desktop Entry]\nName=Viola\nExec=viola\nStartupWMClass=com.rhyme.viola\n'
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XDG_DATA_HOME':root}):
            value=desktop_window_class(contents, '/usr/share/applications/viola.desktop')
            path=write_launcher('rpm-viola-fixture', 'Viola', 'Browser', 'viola', value)
            self.assertIn('StartupWMClass=com.rhyme.viola\n',path.read_text())
            self.assertIn('Name=Viola\n',path.read_text())
    def test_absent_class_uses_native_desktop_id(self):
        self.assertEqual(desktop_window_class('[Desktop Entry]\nName=ChatGPT\n', '/usr/share/applications/chatgpt.desktop'),'chatgpt')
    def test_rpm_metadata_rejects_traversal_without_starting_tools(self):
        with patch('luma_installer.facts.subprocess.Popen') as popen:
            with self.assertRaises(InstallerError):
                rpm_desktop(Path('/tmp/fixture.rpm'),'/usr/share/applications/../../evil.desktop')
            popen.assert_not_called()
