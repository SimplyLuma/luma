#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HELPER = Path(os.environ.get('LUMA_BROWSER_POLICY_HELPER', REPO / 'scripts/os/lib/viola_desktop_policy.py'))
spec = importlib.util.spec_from_file_location('viola_policy', HELPER)
policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
NATIVE = Path(os.environ.get('LUMA_BROWSER_NATIVE_DESKTOPS', REPO / 'src/external/viola/chromium-linux/luma-package'))
EXPORT = Path(os.environ.get('LUMA_BROWSER_SIGNED_DESKTOP', REPO / 'tests/fixtures/viola-signed-export.desktop'))
PROBE = '''import json,sys
from gi.repository import Gio
rows=[]
for app in Gio.AppInfo.get_all():
 if app.get_id() in ('com.rhyme.viola.desktop','viola-browser.desktop'):
  rows.append({'id':app.get_id(),'visible':app.should_show(), 'filename':app.get_filename(),
               'executable':app.get_executable(),'commandline':app.get_commandline(),
               'wmclass':app.get_startup_wm_class()})
print(json.dumps(rows))
'''


class DesktopPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Gio rejects an entry whose Exec program is absent. The source-check
        # holder intentionally has no installed native browser. Bind only the
        # discovery copies to this private, non-launching executable; production
        # composition still consumes and emits the real dispatcher unchanged.
        self.executable = self.root / 'bin/viola-browser'
        self.executable.parent.mkdir()
        self.executable.write_text('#!/bin/sh\nexit 125\n')
        self.executable.chmod(0o755)
        self.discovery = self.root / 'discovery'
        native = self.root / 'usr/share/applications'; native.mkdir(parents=True)
        for name in ('com.rhyme.viola.desktop', 'viola-browser.desktop'):
            (native / name).write_bytes((NATIVE / name).read_bytes())
        self.dock = self.root / 'etc/dconf/db/luma.d/00-luma-desktop'
        self.dock.parent.mkdir(parents=True)
        self.before = "[org/gnome/shell]\nfavorite-apps=['org.gnome.Nautilus.desktop', 'viola-browser.desktop', 'org.projectluma.Ari.desktop']\n"
        self.dock.write_text(self.before)
        self.personal = self.root / 'home/qa/.config/dconf/user'
        self.personal.parent.mkdir(parents=True); self.personal.write_bytes(b'owned personal preference')
        self.exports = self.root / 'flatpak/exports/share/applications'
        self.exports.mkdir(parents=True)
        (self.exports / 'com.rhyme.viola.desktop').write_bytes(EXPORT.read_bytes())

    def probe(self, signed, exports_first=False):
        dirs = [self.root / 'usr/share/luma/desktop-overrides', self.root / 'usr/share']
        if signed:
            dirs.insert(0 if exports_first else 1, self.exports.parent)
        staged = []
        for directory in dirs:
            destination = self.discovery / directory.relative_to(self.root)
            applications = destination / 'applications'
            applications.mkdir(parents=True, exist_ok=True)
            for source in (directory / 'applications').glob('*.desktop'):
                text = source.read_text()
                if 'Exec=/usr/bin/viola-browser %U' in text.splitlines():
                    text = text.replace('Exec=/usr/bin/viola-browser %U',
                                        'Exec=' + str(self.executable) + ' %U')
                (applications / source.name).write_text(text)
            staged.append(destination)
        env = dict(os.environ, XDG_DATA_DIRS=':'.join(map(str, staged)),
                   XDG_DATA_HOME=str(self.root / 'empty-data'),
                   XDG_CONFIG_HOME=str(self.root / 'empty-config'),
                   XDG_CACHE_HOME=str(self.root / 'empty-cache'), XDG_CURRENT_DESKTOP='GNOME')
        return json.loads(subprocess.check_output([sys.executable, '-c', PROBE], env=env, text=True))

    def test_gio_rejects_missing_fixture_executable(self):
        policy.compose(self.root, False)
        self.executable.unlink()
        self.assertFalse(any(row['visible'] for row in self.probe(False)))

    def test_native_only_preserves_defaults_alias_and_window_identity(self):
        policy.compose(self.root, False)
        rows = {row['id']:row for row in self.probe(False)}
        self.assertEqual([r['id'] for r in rows.values() if r['visible']], ['viola-browser.desktop'])
        self.assertEqual(rows['viola-browser.desktop']['wmclass'], 'org.projectluma.Viola.NativeIntegration')
        self.assertIsNone(rows['com.rhyme.viola.desktop']['wmclass'])
        self.assertEqual(self.dock.read_text(), self.before)
        legacy = self.root / 'usr/share/applications/viola-browser.desktop'
        self.assertEqual(legacy.read_bytes(), (NATIVE / legacy.name).read_bytes())
        canonical = self.root / 'usr/share/luma/desktop-overrides/applications/com.rhyme.viola.desktop'
        self.assertIn('Exec=/usr/bin/viola-browser %U', canonical.read_text().splitlines())

    def test_signed_both_priorities_use_one_canonical_identity_and_legacy_dispatch(self):
        policy.compose(self.root, True)
        for first in (False, True):
            with self.subTest(exports_first=first):
                rows = {row['id']:row for row in self.probe(True, first)}
                self.assertEqual([r['id'] for r in rows.values() if r['visible']], ['com.rhyme.viola.desktop'])
                canonical = rows['com.rhyme.viola.desktop']
                expected = self.exports if first else self.root / 'usr/share/luma/desktop-overrides/applications'
                self.assertEqual(Path(canonical['filename']), self.discovery / expected.relative_to(self.root) / 'com.rhyme.viola.desktop')
                self.assertEqual(canonical['executable'], '/usr/bin/flatpak' if first else str(self.executable))
                self.assertIn('com.rhyme.viola', canonical['commandline'] if first else canonical['wmclass'])
                legacy = rows['viola-browser.desktop']
                self.assertFalse(legacy['visible']); self.assertIsNone(legacy['wmclass'])
                self.assertEqual(legacy['commandline'], str(self.executable) + ' %U')
                original = self.root / 'usr/share/luma/desktop-overrides/applications/viola-browser.desktop'
                self.assertIn('Exec=/usr/bin/viola-browser %U', original.read_text().splitlines())
                canonical_original = self.root / 'usr/share/luma/desktop-overrides/applications/com.rhyme.viola.desktop'
                self.assertIn('Exec=/usr/bin/viola-browser %U', canonical_original.read_text().splitlines())
        self.assertIn("'com.rhyme.viola.desktop'", self.dock.read_text())
        self.assertNotIn("'viola-browser.desktop'", self.dock.read_text())
        self.assertEqual(self.personal.read_bytes(), b'owned personal preference')

    def test_unfixed_signed_plus_native_is_detected_as_duplicate(self):
        rows = self.probe(True, True)
        self.assertEqual({r['id'] for r in rows if r['visible']}, {'com.rhyme.viola.desktop', 'viola-browser.desktop'})

    def test_native_hidden_canonical_override_with_export_lower_is_detected(self):
        policy.compose(self.root, False)
        self.assertFalse(next(r for r in self.probe(True, False) if r['id']=='com.rhyme.viola.desktop')['visible'])

    def test_hidden_legacy_without_signed_role_is_detected_as_no_launcher(self):
        policy.compose(self.root, True)
        canonical = self.root / 'usr/share/luma/desktop-overrides/applications/com.rhyme.viola.desktop'
        canonical.write_text(canonical.read_text()+'NoDisplay=true\n')
        self.assertFalse(any(r['visible'] for r in self.probe(False)))

    def test_changed_managed_command_refused_before_desktop_writes(self):
        canonical = self.root / 'usr/share/applications/com.rhyme.viola.desktop'
        canonical.write_text(canonical.read_text().replace('/usr/bin/viola-browser %U', '/usr/bin/other %U'))
        with self.assertRaisesRegex(ValueError, 'managed dispatcher'): policy.compose(self.root, True)
        self.assertEqual(self.dock.read_text(), self.before)
        self.assertFalse((self.root/'usr/share/luma/desktop-overrides/applications/com.rhyme.viola.desktop').exists())

    def test_changed_favorite_refused_before_desktop_writes(self):
        self.dock.write_text(self.before.replace('viola-browser.desktop','other.desktop'))
        with self.assertRaisesRegex(ValueError, 'native Viola policy'): policy.compose(self.root, True)
        self.assertFalse((self.root/'usr/share/luma/desktop-overrides/applications/com.rhyme.viola.desktop').exists())


if __name__ == '__main__': unittest.main()
