# SPDX-License-Identifier: Apache-2.0
"""Pure file-boundary controls; real settings persistence is tested separately."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).resolve().parents[1] / 'luma_installer/app_preferences.py'
spec = importlib.util.spec_from_file_location('preferences', SOURCE)
prefs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prefs)

class AppPreferencesFiles(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.root = Path(self.work.name)
        self.path = self.root / 'import.json'

    def tearDown(self):
        self.work.cleanup()

    def test_fixed_reader_contract(self):
        tree = ET.fromstring(prefs.reader_xml())
        self.assertEqual(len(tree.findall('schema')), 5)
        self.assertEqual(sum(len(s.findall('key')) for s in tree), 28)
        self.assertTrue(all('path' not in s.attrib for s in tree))
        self.assertEqual((SOURCE.parent.parent / 'data/org.projectluma.AppPreferences.Read.gschema.xml').read_text(), prefs.reader_xml())
        with self.assertRaises(prefs.PreferencesError): prefs.reader_id('org.projectluma.Unknown')

    def test_atomic_exclusive_publish_and_existing_import_preserved(self):
        prefs._write(self.path, {'owned': True}, exclusive=True)
        original = self.path.read_bytes()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            prefs._write(self.path, {'owned': False}, exclusive=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.preferences-*')), [])

    def test_interrupted_flush_never_publishes_partial_snapshot(self):
        with patch.object(prefs.os, 'fsync', side_effect=OSError('injected storage failure')):
            with self.assertRaises(OSError): prefs._write(self.path, {'owned': True}, exclusive=True)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_linked_and_writable_directories_refused(self):
        linked = self.root / 'linked'; linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(prefs.PreferencesError): prefs._directory(linked)
        for mode in (0o722, 0o770):
            self.root.chmod(mode)
            with self.assertRaises(prefs.PreferencesError): prefs._directory(self.root)
        self.root.chmod(0o700)

    def test_duplicate_fields_oversized_public_and_linked_files_refused(self):
        prefs._write(self.path, {'owned': True}, exclusive=True)
        self.path.write_text('{"owned":true,"owned":false}')
        with self.assertRaises(prefs.PreferencesError): prefs._read(self.path)
        self.path.write_bytes(b' ' * (prefs.MAX_BYTES + 1))
        with self.assertRaises(prefs.PreferencesError): prefs._read(self.path)
        self.path.write_text('{}'); self.path.chmod(0o644)
        with self.assertRaises(prefs.PreferencesError): prefs._read(self.path)
        self.path.chmod(0o600)
        link = self.root / 'linked'; link.symlink_to(self.path)
        with self.assertRaises(OSError): prefs._read(link)

    def test_completion_marker_cannot_replace_symlink(self):
        prefs._write(self.path, {'owned': True}, exclusive=True)
        marker = self.root / 'applied.json'; marker.symlink_to(self.path)
        original = self.path.read_bytes()
        with self.assertRaises(OSError): prefs._write(marker, {'complete': True})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(marker.is_symlink())

    def test_application_requires_durable_private_backend(self):
        with patch.dict(os.environ, {'GSETTINGS_BACKEND': 'memory'}):
            with self.assertRaises(prefs.PreferencesError):
                prefs.apply('org.projectluma.Write', self.root)

if __name__ == '__main__': unittest.main()
