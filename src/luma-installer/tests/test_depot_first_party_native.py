# SPDX-License-Identifier: Apache-2.0
"""ADR-031 in Depot's native provider: listings of apps Luma's image ships."""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve()
# In the source tree luma_depot sits beside luma-installer; in the package build
# both are at the top of the source directory.
if (HERE.parents[2] / 'luma-depot').is_dir():
    sys.path.insert(0, str(HERE.parents[2] / 'luma-depot'))

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import GLib  # noqa: F401
    from luma_depot import native as _native  # noqa: F401
except (ImportError, ValueError):  # pragma: no cover - needs PyGObject and Depot
    gi = None


def entry(identifier, name, package, desktop, removable=True, **extra):
    row = {"id": identifier, "name": name, "app_id": desktop.removesuffix('.desktop'), "tier": "luma",
           "visibility": "public", "backend": "rpm", "repository": "luma", "source_id": package,
           "distribution": "publisher", "architectures": ["x86_64", "aarch64"],
           "reference_url": f"https://simplyluma.com/apps/{identifier}", "qualification": "pending",
           "qualification_reason": "Included with Luma.", "categories": ["utilities"],
           "sources": {"luma_system": {"package": package, "desktop_id": desktop, "removable": removable}},
           "preinstalled_on_luma": True}
    row.update(extra)
    return row


TIDE = entry('tide', 'Tide', 'luma-tide', 'org.projectluma.Tide.desktop', backend='flatpak',
             source_id='org.projectluma.Tide', app_id='org.projectluma.Tide',
             sources={"luma_system": {"package": "luma-tide", "desktop_id": "org.projectluma.Tide.desktop",
                                      "removable": True},
                      "flatpak": {"repository": "luma", "source_id": "org.projectluma.Tide", "branch": "stable"}})
NOTES = entry('notes', 'Notes', 'prairie-core-apps', 'org.projectluma.Notes.desktop')
CLOCK = entry('clock', 'Clock', 'prairie-core-apps', 'org.projectluma.Clock.desktop')
FILER = entry('filer', 'Filer', 'nautilus', 'org.gnome.Nautilus.desktop', removable=False)
LEAF = entry('leaf', 'Leaf', 'luma-leaf', 'org.projectluma.Leaf.desktop')
IMAGER = entry('imager', 'Imager', 'luma-imager', 'org.projectluma.Imager.desktop')
DARKROOM = entry('darkroom', 'Darkroom', 'luma-darkroom', 'org.projectluma.Darkroom.desktop')


class Record:
    def __init__(self, desktop_id, name):
        self.desktop_id, self.name = desktop_id, name
        self.provider, self.management, self.can_review_removal = 'Luma', 'Installed with Luma', True
        self.app_info = self

    def get_icon(self):
        return None

    def get_string(self, _key):
        return None


@unittest.skipIf(gi is None, 'needs PyGObject')
class Snapshot(unittest.TestCase):
    def setUp(self):
        from luma_depot import native
        from luma_installer.depot_catalog import validate_catalog
        self.native = native
        self.catalog = validate_catalog({"schema_version": 4, "generated_at": "2026-09-15T00:00:00Z",
                                         "applications": [TIDE, NOTES, CLOCK, FILER, LEAF, IMAGER, DARKROOM]})
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        status = Path(self.work.name) / 'status.json'
        status.write_text(json.dumps({'transaction': None, 'deployments': [
            {'id': 'staged', 'booted': False, 'staged': True,
             'requested-base-removals': ['luma-leaf', 'luma-darkroom'],
             'base-removals': [['luma-leaf-1-1.noarch', 'luma-leaf', 0, '1', '1', 'noarch'],
                               ['luma-darkroom-1-1.noarch', 'luma-darkroom', 0, '1', '1', 'noarch']]},
            {'id': 'booted', 'booted': True, 'requested-base-removals': ['luma-leaf', 'luma-imager'],
             'base-removals': [['luma-leaf-1-1.noarch', 'luma-leaf', 0, '1', '1', 'noarch'],
                               ['luma-imager-1-1.noarch', 'luma-imager', 0, '1', '1', 'noarch']]}]}))
        self.env = {'LUMA_DEPOT_SYSTEM': 'luma', 'LUMA_DEPOT_RPM_OSTREE_STATUS': str(status),
                    'LUMA_DEPOT_INSTALLED_PACKAGES': 'luma-tide,prairie-core-apps,nautilus,luma-darkroom',
                    'LUMA_DEPOT_REQUIRED_BY': 'prairie-core-apps:luma-agenda'}
        self.records = (Record('org.projectluma.Tide.desktop', 'Tide'),
                        Record('org.projectluma.Notes.desktop', 'Notes'),
                        Record('org.gnome.Nautilus.desktop', 'Files'),
                        Record('org.projectluma.Darkroom.desktop', 'Darkroom'))

    def snapshot(self, records=None, **env):
        provider = self.native.NativeCatalogue()
        with mock.patch.dict(os.environ, {**self.env, **env}), \
                mock.patch.object(self.native, 'inventory',
                                  return_value=self.records if records is None else records), \
                mock.patch.object(self.native, 'installed_flatpak_refs', return_value={}), \
                mock.patch.object(self.native, 'architecture', return_value='x86_64'), \
                mock.patch('luma_depot.native_metadata.cached_metadata', return_value={}), \
                mock.patch('luma_installer.depot_flatpak.luma_remote_available', return_value=True), \
                mock.patch.object(provider, '_catalog', return_value=self.catalog):
            return provider.snapshot()

    def app(self, catalogue, identifier):
        return catalogue.find('catalog:' + identifier)

    def test_states_on_luma(self):
        catalogue = self.snapshot()
        tide = self.app(catalogue, 'tide')
        self.assertEqual((tide.system_state, tide.availability, tide.system_removable), ('installed', 'Installed with Luma', True))
        # Its Flatpak is never offered while the image's package is here.
        self.assertFalse(tide.installable)
        filer = self.app(catalogue, 'filer')
        self.assertFalse(filer.system_removable)
        self.assertIn('Luma needs this app', filer.system_note)
        notes = self.app(catalogue, 'notes')
        self.assertFalse(notes.system_removable)
        self.assertEqual(notes.system_siblings, ('Clock',))
        self.assertEqual(self.app(catalogue, 'leaf').system_state, 'removed')
        self.assertEqual(self.app(catalogue, 'imager').system_state, 'restore-pending')
        self.assertEqual(self.app(catalogue, 'darkroom').system_state, 'removal-pending')

    def test_installed_image_apps_appear_once(self):
        catalogue = self.snapshot()
        ids = [app.app_id for app in catalogue.apps]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn('org.projectluma.Tide.desktop', ids)
        self.assertNotIn('org.gnome.Nautilus.desktop', ids)
        self.assertEqual(self.native.identity_for_installed(self.records[2]), 'catalog:filer')

    def test_a_removed_app_with_a_flatpak_is_restored_not_reinstalled(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump({'deployments': [{'id': 'b', 'booted': True, 'requested-base-removals': ['luma-tide']}]}, handle)
        self.addCleanup(os.unlink, handle.name)
        tide = self.app(self.snapshot(LUMA_DEPOT_RPM_OSTREE_STATUS=handle.name,
                                      LUMA_DEPOT_INSTALLED_PACKAGES='nautilus'), 'tide')
        self.assertEqual((tide.system_state, tide.installable), ('removed', False))

    def test_absent_from_the_image_offers_the_flatpak(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump({'deployments': [{'id': 'b', 'booted': True}]}, handle)
        self.addCleanup(os.unlink, handle.name)
        catalogue = self.snapshot((), LUMA_DEPOT_RPM_OSTREE_STATUS=handle.name, LUMA_DEPOT_INSTALLED_PACKAGES='')
        tide = self.app(catalogue, 'tide')
        self.assertEqual((tide.system_state, tide.installable, tide.availability), ('', True, 'From Luma'))
        leaf = self.app(catalogue, 'leaf')
        self.assertEqual((leaf.installable, leaf.availability), (False, 'Not included in this version of Luma'))

    def test_other_distributions_see_flatpaks_and_available_on_luma(self):
        catalogue = self.snapshot((), LUMA_DEPOT_SYSTEM='other')
        tide = self.app(catalogue, 'tide')
        self.assertEqual((tide.system_state, tide.installable, tide.luma_only), ('', True, False))
        notes = self.app(catalogue, 'notes')
        self.assertEqual((notes.luma_only, notes.availability, notes.homepage),
                         (True, 'Available on Luma', 'https://simplyluma.com/download'))


@unittest.skipIf(gi is None, 'needs PyGObject')
class Helper(unittest.TestCase):
    def test_progress_lines(self):
        from luma_depot import native
        self.assertEqual(native.parse_helper_line('progress: 0.40 Preparing the change'), (0.4, 'Preparing the change'))
        self.assertIsNone(native.parse_helper_line('Checking out tree'))
        self.assertEqual(native.parse_helper_line('progress: 7 x'), (1.0, 'x'))

    def test_errors(self):
        from luma_depot import native
        self.assertIn('did not get permission', native.helper_error(126, []).hint)
        self.assertEqual(native.helper_error(3, ['progress: 0.05 x', 'refused: Other parts of Luma need this app.']).hint,
                         'Other parts of Luma need this app.')
        self.assertEqual(native.helper_error(1, ['error: disk full']).hint, 'disk full')

    def test_runs_pkexec_with_the_package_and_reports_progress(self):
        from luma_depot import native

        class Process:
            def __init__(self, command, **_kwargs):
                Process.command = command
                self.stdout = io.StringIO('progress: 0.05 Checking this computer\nprogress: 0.92 Getting it ready\n'
                                          'progress: 1.00 Restart to finish\nresult: staged\n')

            def wait(self):
                return 0
        seen = []
        with mock.patch.object(native.GLib, 'idle_add', side_effect=lambda function, value: seen.append(value)):
            result = native.run_system_helper('override-remove', 'luma-tide', 'catalog:tide', None, popen=Process)
        self.assertEqual(result, 'staged')
        self.assertEqual(Process.command, ['pkexec', '/usr/libexec/luma-installer-system', 'override-remove', 'luma-tide'])
        self.assertEqual([round(p.fraction, 2) for p in seen], [0.02, 0.05, 0.92, 0.99])


class UpdateAgentState(unittest.TestCase):
    def test_a_foreign_staged_deployment_is_not_an_update_ready(self):
        from luma_installer import depot_system_update as su
        removal = su.from_values({'State': 'restart-required', 'BootedVersion': '1.0.0', 'StagedVersion': '1.0.0'}, 'dbus')
        self.assertFalse(removal.staged)
        self.assertTrue(removal.restart_required and removal.update_ready)
        ours = su.from_values({'State': 'staged', 'BootedVersion': '1.0.0', 'StagedVersion': '1.0.1'}, 'dbus')
        self.assertTrue(ours.staged)


if __name__ == '__main__':
    unittest.main()
