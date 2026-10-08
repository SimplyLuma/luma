# SPDX-License-Identifier: Apache-2.0
"""Real GI and private dconf profiles; no actor or desktop settings are touched.

Run with dbus-run-session. Native and sandbox profiles share only the isolated
session and compiled schemas; each operation is a new process with real storage.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parents[1] / 'luma_installer/app_preferences.py'
spec = importlib.util.spec_from_file_location('preferences', SOURCE)
prefs = importlib.util.module_from_spec(spec); spec.loader.exec_module(prefs)

def worker():
    from gi.repository import Gio, GLib
    action, app, directory = sys.argv[2:5]
    values = json.load(sys.stdin)
    if action in ('seed', 'read', 'legacy'):
        settings, _ = prefs._settings(app, reader=action == 'legacy',
            path=prefs.PATHS[app][-1] if action == 'legacy' else None)
        if action != 'read':
            for key, value in values.items():
                assert settings.set_value(key, GLib.Variant(prefs.KEYS[app][key], value))
            Gio.Settings.sync()
        result = {key: settings.get_value(key).unpack() for key in prefs.KEYS[app]}
    elif action == 'capture':
        result = prefs.capture(app, Path(directory))
    elif action == 'apply':
        result = prefs.apply(app, Path(directory))
    else:
        raise AssertionError(action)
    print(json.dumps(result, sort_keys=True))

class AppPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            raise RuntimeError('This control requires a newly isolated dbus-run-session.')
        cls.work = tempfile.TemporaryDirectory(prefix='luma-private-preferences-')
        cls.root = Path(cls.work.name)
        cls.schemas = cls.root / 'schemas'; cls.schemas.mkdir()
        (cls.schemas / 'readers.gschema.xml').write_text(prefs.reader_xml())
        files = {
            'Write': Path(__file__).parent / 'preferences-schemas/Write.gschema.xml.in',
            'Grid': Path(__file__).parent / 'preferences-schemas/Grid.gschema.xml.in',
            'Stage': Path(__file__).parent / 'preferences-schemas/Stage.gschema.xml.in',
            'Session': Path(__file__).parent / 'preferences-schemas/Session.gschema.xml.in',
            'Reel': Path(__file__).parent / 'preferences-schemas/Reel.gschema.xml',
        }
        fixture_root = Path(__file__).parent / 'preferences-schemas'
        provenance = json.loads((fixture_root / 'SOURCE.json').read_text())
        if set(provenance) != {p.name for p in files.values()}:
            raise AssertionError('Schema fixture membership differs from provenance.')
        for name, path in files.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != provenance[path.name]['sha256']:
                raise AssertionError('Schema fixture bytes differ from maintained source.')
            app = 'org.projectluma.' + name
            xml = path.read_text().replace('@APP_ID@', app).replace('@APPLICATION_ID@', app)
            xml = xml.replace('@SETTINGS_PATH@', prefs.PATHS[app][0])
            (cls.schemas / (name + '.gschema.xml')).write_text(xml)
        subprocess.run(['glib-compile-schemas', '--strict', str(cls.schemas)], check=True)
        cls.config = cls.root / 'config'; cls.config.mkdir()
        # The private dconf service inherits this directory when first activated.
        os.environ['XDG_CONFIG_HOME'] = str(cls.config)
        subprocess.run(['dbus-update-activation-environment', 'XDG_CONFIG_HOME'], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        self.directory = self.root / self._testMethodName; self.directory.mkdir(mode=0o700)
        self.profiles = {}
        for kind in ('native', 'sandbox'):
            profile = self.directory / (kind + '.profile')
            profile.write_text('user-db:' + self._testMethodName + '_' + kind + '\n')
            self.profiles[kind] = profile

    def call(self, action, app, values=None, *, kind='native', destination=None, good=True):
        env = dict(os.environ, GSETTINGS_BACKEND='dconf', G_DEBUG='fatal-criticals',
            GSETTINGS_SCHEMA_DIR=str(self.schemas), DCONF_PROFILE=str(self.profiles[kind]))
        if kind == 'sandbox':
            config = self.directory / 'sandbox-config'; config.mkdir(mode=0o700, exist_ok=True)
            env.update(GSETTINGS_BACKEND='keyfile', XDG_CONFIG_HOME=str(config))
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
            '--worker', action, app, str(destination or self.directory)],
            input=json.dumps(values or {}), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, timeout=20)
        if good:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_all_five_real_schemas_capture_apply_and_native_retention(self):
        choices = {
            'Write': {'zoom': 1.75, 'recent-files': ['/owned/document.docx'], 'page-theme': 'moon'},
            'Grid': {'recent-files': ['/owned/workbook.xlsx'], 'show-welcome': False},
            'Stage': {'default-new-format': 'odp', 'recovery-interval-seconds': 300},
            'Session': {'sample-rate': 96000, 'default-input': 'owned-node', 'buffer-frames': 512},
            'Reel': {'show-welcome': False},
        }
        for name, values in choices.items():
            app = 'org.projectluma.' + name
            target = self.directory / name; target.mkdir(mode=0o700)
            before = self.call('seed', app, values)
            self.assertEqual(self.call('read', app), before)
            captured = self.call('capture', app, destination=target)
            self.assertEqual(captured['keys'], len(values))
            payload = json.loads((target / 'import.json').read_text())
            self.assertEqual(set(payload['values']), set(values))
            self.assertEqual(self.call('apply', app, kind='sandbox', destination=target)['applied'], len(values))
            after = self.call('read', app, kind='sandbox')
            for key, value in values.items(): self.assertEqual(after[key], value)
            self.assertEqual(self.call('read', app), before)
            self.assertEqual((target / 'import.json').stat().st_mode & 0o777, 0o600)

    def test_stage_legacy_path_is_read_but_canonical_user_value_wins(self):
        app = 'org.projectluma.Stage'
        self.call('legacy', app, {'default-new-format': 'pptx', 'present-without-motion': True})
        self.call('seed', app, {'default-new-format': 'odp'})
        self.call('capture', app)
        self.call('apply', app, kind='sandbox')
        values = self.call('read', app, kind='sandbox')
        self.assertEqual(values['default-new-format'], 'odp')
        self.assertTrue(values['present-without-motion'])

    def test_existing_private_edit_and_repeat_never_replace_user_values(self):
        app = 'org.projectluma.Write'
        self.call('seed', app, {'zoom': 1.75, 'page-theme': 'moon'})
        self.call('capture', app)
        self.call('seed', app, {'zoom': 2.5}, kind='sandbox')
        self.assertEqual(self.call('apply', app, kind='sandbox')['applied'], 1)
        self.call('seed', app, {'page-theme': 'sun'}, kind='sandbox')
        self.assertTrue(self.call('apply', app, kind='sandbox')['already_complete'])
        values = self.call('read', app, kind='sandbox')
        self.assertEqual(values['zoom'], 2.5); self.assertEqual(values['page-theme'], 'sun')
        # Simulate interruption after settings commit, before completion marker.
        (self.directory / 'applied.json').unlink()
        self.assertEqual(self.call('apply', app, kind='sandbox')['applied'], 0)

    def test_malformed_foreign_unknown_and_out_of_range_fail_before_writes(self):
        app = 'org.projectluma.Write'
        self.call('seed', app, {'zoom': 1.75})
        self.call('capture', app)
        original = json.loads((self.directory / 'import.json').read_text())
        cases = [dict(original, app_id='org.projectluma.Stage'),
                 dict(original, values={'credential': {'type': 's', 'text': "'secret'"}}),
                 dict(original, values={'zoom': {'type': 's', 'text': "'wrong type'"}}),
                 dict(original, values={'zoom': {'type': 'd', 'text': '999.0'}}),
                 dict(original, values={'zoom': {'type': 'd', 'text': 'not-a-variant'}})]
        for payload in cases:
            (self.directory / 'import.json').write_text(json.dumps(payload))
            self.call('apply', app, kind='sandbox', good=False)
            self.assertEqual(self.call('read', app, kind='sandbox')['zoom'], 1.0)
            self.assertFalse((self.directory / 'applied.json').exists())

    def test_links_oversize_and_unknown_app_are_refused(self):
        app = 'org.projectluma.Grid'
        self.call('seed', app, {'recent-files': ['x' * 5000]})
        self.call('capture', app, good=False)
        self.assertFalse((self.directory / 'import.json').exists())
        self.call('capture', 'org.projectluma.Unknown', good=False)
        self.call('seed', app, {'recent-files': ['/owned/file']})
        self.call('capture', app)
        original = self.directory / 'original'; (self.directory / 'import.json').rename(original)
        (self.directory / 'import.json').symlink_to(original)
        self.call('apply', app, kind='sandbox', good=False)
        self.assertFalse((self.directory / 'applied.json').exists())
        self.assertEqual(self.call('read', app, kind='sandbox')['recent-files'], [])

    def test_reader_xml_is_exact_contract_and_defaults_are_not_exported(self):
        self.assertEqual((Path(__file__).resolve().parents[1] / 'data/org.projectluma.AppPreferences.Read.gschema.xml').read_text(), prefs.reader_xml())
        app = 'org.projectluma.Session'
        self.assertEqual(self.call('capture', app)['keys'], 0)
        self.assertEqual(json.loads((self.directory / 'import.json').read_text())['values'], {})
        self.assertEqual(self.call('apply', app, kind='sandbox')['applied'], 0)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker()
    else:
        unittest.main()
