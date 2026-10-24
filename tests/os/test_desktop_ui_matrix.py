#!/usr/bin/env python3
"""The UI rebuild selector must preserve required work and skip unrelated changes."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / 'scripts/os/desktop-ui-matrix.py'
spec = importlib.util.spec_from_file_location('desktop_ui_matrix', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class SelectionTests(unittest.TestCase):
    def test_settings_patch(self):
        self.assertEqual(m.select(['patches/gnome-control-center/0059.patch']), ['gnome-control-center'])
    def test_filer_recipe(self):
        self.assertEqual(m.select(['scripts/packages/build-nautilus.sh']), ['nautilus'])
    def test_shell_test(self):
        self.assertEqual(m.select(['tests/gnome-shell/clock.js']), ['gnome-shell'])
    def test_shared_provider(self):
        for path in ['config/desktop/inputs.env', 'src/luma-platform/foo.c', 'scripts/packages/build-gtk4.sh', 'scripts/os/build-desktop-ui.sh']:
            with self.subTest(path=path):
                self.assertEqual(m.select([path]), m.COMPONENTS)
    def test_unrelated(self):
        self.assertEqual(m.select(['BUILDING.md', 'scripts/os/build-staff-media.sh', 'scripts/packages/build-luma-messages-bridges.sh']), [])
    def test_selector_only(self):
        self.assertEqual(m.select(['.github/workflows/desktop-ui.yml', 'scripts/os/desktop-ui-matrix.py', 'tests/os/test_desktop_ui_matrix.py']), [])
    def test_multiple(self):
        self.assertEqual(m.select(['scripts/packages/build-nautilus.sh', 'patches/gnome-shell/clock.patch']), ['nautilus', 'gnome-shell'])

if __name__ == '__main__':
    unittest.main()
