# SPDX-License-Identifier: Apache-2.0
"""Actual preview launcher identity, measured on the runner's private bus.

The production-ID incumbent is an inert NotesApplication subclass. It counts
activation/open requests, never creates a window and never opens a store.
The child uses the exact sync-lumaui-preview launcher APP_ID override and
real Notes activation, fixture data, and GTK window. All of its windows close.
No native compatibility shim is used: passing must prove the preview stack.
Run ONLY through notes_lumaui_headless.py after capsule recovery.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock
import luma_appkit.widgets as widgets
from gi.repository import Gio, GLib
import notes_lumaui_runtime as base
from prairie_apps import notes

PRODUCTION = 'org.projectluma.Notes'
PREVIEW = PRODUCTION + '.LumaUIPreview'

WRONG_HOOK = r"""
import json
import prairie_apps.notes as m
# Deliberately reproduce the mismatched-hook fault on the private bus only.
m.APPLICATION_ID = m.APP_ID + '.LumaUIPreview'
app = m.NotesApplication()
app.register(None)
print(json.dumps({'application_id': app.get_application_id(),
                  'remote': app.get_is_remote(),
                  'windows': len(app.get_windows())}), flush=True)
# Use GApplication.run so remote activation is flushed before child exit,
# as in the actual launcher, rather than abandoning a queued bus message.
raise SystemExit(app.run(['prairie-notes']))
"""

CHILD = r"""
import json, sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib
import prairie_apps.notes as m
original = m.NotesApplication
class Probe(original):
    def __init__(self):
        super().__init__()
        self.hold()
        self.finished = False
        GLib.timeout_add(7000, self.expired)
    def do_activate(self):
        original.do_activate(self)
        from pathlib import Path
        import os
        from gi.repository import Gio
        from prairie_apps.notes_file import TextFileWindow
        scratch = Path(os.environ['XDG_DATA_HOME']) / 'identity-probe.md'
        scratch.write_text('Private identity probe\n')
        TextFileWindow(self, Gio.File.new_for_path(str(scratch))).present()
        GLib.timeout_add(400, self.inspect_window)
    def inspect_window(self):
        windows = self.get_windows()
        connection = self.get_dbus_connection()
        owner = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (self.get_application_id(),)),
            GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        self.evidence = {'application_id': self.get_application_id(),
                         'remote': self.get_is_remote(),
                         'registered': self.get_is_registered(),
                         'unique_bus_name': connection.get_unique_name(),
                         'preview_bus_owner_before_close': owner,
                         'window_application_ids': [w.get_application().get_application_id() for w in windows],
                         'window_geometry_scopes': [w._geometry_app_id for w in windows],
                         'mapped_windows_before_close': sum(w.get_mapped() for w in windows)}
        for window in windows:
            window.close()
        GLib.timeout_add(150, self.finish)
        return GLib.SOURCE_REMOVE
    def finish(self):
        self.evidence['windows_after_close'] = len(self.get_windows())
        self.finished = True
        print(json.dumps(self.evidence), flush=True)
        self.release()
        self.quit()
        return GLib.SOURCE_REMOVE
    def expired(self):
        if not self.finished:
            print(json.dumps({'error': 'preview did not activate and close'}), flush=True)
            self.quit()
        return GLib.SOURCE_REMOVE
m.NotesApplication = Probe
# Exact app launcher statement from sync-lumaui-preview.sh:
m.APP_ID = getattr(m, 'APP_ID', 'org.projectluma.notes') + '.LumaUIPreview'
sys.argv[0] = 'prairie-notes'
raise SystemExit(m.main())
"""


class Incumbent(notes.NotesApplication):
    def __init__(self):
        self.activation_requests = 0
        self.open_requests = 0
        super().__init__()
    def do_activate(self):
        self.activation_requests += 1
    def do_open(self, *_args):
        self.open_requests += 1


class PreviewIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {
            'LUMA_NOTES_FIXTURE': str(base.ROOT / 'tests/fixtures/notes-v70.json'),
            'LUMA_NOTES_STYLE_PATH': str(base.ROOT / 'src/prairie-core/style/notes.css'),
        })
        cls.env.start()
        # Keep one inert production owner for the whole private-bus suite.
        # Disposing and recreating GApplication on the same connection leaves
        # GTK's exported interface registered until that connection closes.
        cls.incumbent = Incumbent()
        if not cls.incumbent.register(None) or cls.incumbent.get_is_remote():
            raise AssertionError('Identity suite must own its inert private-bus incumbent')

    @classmethod
    def tearDownClass(cls):
        cls.incumbent.run_dispose()
        cls.env.stop()

    def setUp(self):
        self.incumbent.activation_requests = 0
        self.incumbent.open_requests = 0

    def test_incorrect_hook_is_detected_by_private_incumbent(self):
        self.assertIn('notes-runtime-', os.environ.get('DBUS_SESSION_BUS_ADDRESS', ''))
        self.assertTrue(hasattr(widgets.LumaAppearance.SurfacePolicy, 'get_surface'),
                        'Preview requires compatible native kit; no identity-probe shim is allowed')
        incumbent = self.incumbent
        child = None
        try:
            self.assertTrue(incumbent.register(None))
            self.assertFalse(incumbent.get_is_remote())
            child = subprocess.Popen([sys.executable, '-c', WRONG_HOOK],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 10
            while child.poll() is None and time.monotonic() < deadline:
                base.pump(.03)
            self.assertIsNotNone(child.poll(), 'incorrect-hook control timed out')
            out, err = child.communicate(timeout=1)
            self.assertEqual(child.returncode, 0, err[-4000:])
            evidence = json.loads(out.strip().splitlines()[-1])
            base.pump(.1)
            self.assertEqual(evidence['application_id'], PRODUCTION)
            self.assertTrue(evidence['remote'], 'Wrong hook must resolve to the inert production incumbent')
            self.assertEqual(evidence['windows'], 0)
            self.assertEqual((incumbent.activation_requests, incumbent.open_requests), (1, 0))
            self.assertEqual(incumbent.get_windows(), [])
            type(self).wrong_hook_evidence = dict(evidence,
                production_activation_requests=incumbent.activation_requests,
                scope='negative control on private bus; inert incumbent, no installed instance')
            print('Notes incorrect-hook control: ' + json.dumps(type(self).wrong_hook_evidence), flush=True)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(2)

    def test_launcher_owns_separate_native_id_and_closes_all_windows(self):
        address = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
        self.assertIn('notes-runtime-', address, 'Evidence must use the isolated runner bus')
        self.assertTrue(hasattr(widgets.LumaAppearance.SurfacePolicy, 'get_surface'),
                        'Preview requires compatible native kit; no identity-probe shim is allowed')
        self.assertEqual(notes.APP_ID, PRODUCTION)
        incumbent = self.incumbent
        child = None
        try:
            self.assertEqual(incumbent.get_application_id(), PRODUCTION)
            self.assertTrue(incumbent.register(None))
            self.assertFalse(incumbent.get_is_remote())
            env = dict(os.environ, LUMA_NOTES_FIXTURE=str(base.ROOT / 'tests/fixtures/notes-v70.json'),
                       LUMA_NOTES_STYLE_PATH=str(base.ROOT / 'src/prairie-core/style/notes.css'))
            for key in ('LUMA_NOTES_SELECTED', 'LUMA_NOTES_SELECTION', 'LUMA_NOTES_CONTEXT_FOLDER',
                        'LUMA_NOTES_QUERY', 'LUMA_NOTES_SIDEBAR', 'LUMA_NOTES_SHARE_QUERY'):
                env.pop(key, None)
            child = subprocess.Popen([sys.executable, '-c', CHILD], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 10
            while child.poll() is None and time.monotonic() < deadline:
                base.pump(.03)
            self.assertIsNotNone(child.poll(), 'preview identity probe timed out')
            out, err = child.communicate(timeout=1)
            self.assertEqual(child.returncode, 0, err[-4000:])
            evidence = json.loads(out.strip().splitlines()[-1])
            self.assertEqual(evidence['application_id'], PREVIEW)
            self.assertTrue(evidence['registered'])
            self.assertFalse(evidence['remote'])
            self.assertEqual(evidence['preview_bus_owner_before_close'], evidence['unique_bus_name'])
            self.assertNotEqual(evidence['unique_bus_name'], incumbent.get_dbus_connection().get_unique_name())
            self.assertEqual(evidence['window_application_ids'], [PREVIEW, PREVIEW])
            self.assertIn(PREVIEW + '.FileWindow', evidence['window_geometry_scopes'])
            self.assertTrue(all(scope.startswith(PREVIEW + '.') for scope in evidence['window_geometry_scopes']))
            self.assertEqual(evidence['mapped_windows_before_close'], 2)
            self.assertEqual(evidence['windows_after_close'], 0)
            self.assertEqual((incumbent.activation_requests, incumbent.open_requests), (0, 0))
            self.assertEqual(notes.APP_ID, PRODUCTION)
            connection = incumbent.get_dbus_connection()
            production_owner = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (PRODUCTION,)),
                GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
            self.assertEqual(production_owner, connection.get_unique_name())
            has_preview = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (PREVIEW,)),
                GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
            self.assertFalse(has_preview, 'child must release preview bus identity on exit')
            evidence.update(production_application_id=incumbent.get_application_id(),
                            production_bus_owner=production_owner,
                            production_activation_requests=incumbent.activation_requests,
                            production_open_requests=incumbent.open_requests,
                            preview_bus_owned_after_exit=has_preview,
                            scope='private bus and fixture only; no installed instance or live desktop')
            if hasattr(type(self), 'wrong_hook_evidence'):
                evidence['incorrect_hook_negative_control'] = type(self).wrong_hook_evidence
            if target := os.environ.get('LUMA_NOTES_IDENTITY_REPORT'):
                Path(target).write_text(json.dumps(evidence, indent=2) + '\n')
            print('Notes preview identity: ' + json.dumps(evidence), flush=True)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(2)


if __name__ == '__main__':
    unittest.main()
