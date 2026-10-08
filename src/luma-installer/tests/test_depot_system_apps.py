# SPDX-License-Identifier: Apache-2.0
"""ADR-031: state of Luma-shipped apps, from captured ``rpm-ostree status --json`` documents.

Fixtures in fixtures/rpm-ostree are real captures reduced to the fields Depot
reads, with commit checksums replaced and private origins renamed:

* layered-and-replaced.json -- a Luma development machine: first-party apps
  layered as local RPMs and base packages locally replaced, nothing pending.
* The vm-*.json captures come from the Luma nightly in a VM, taken at each
  step of remove, restart, restore, restart, and with a staged update.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from luma_installer import depot_system_apps as sa

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'rpm-ostree'


def fixture(name):
    return sa.parse_status(json.loads((FIXTURES / name).read_text()))


class Parsing(unittest.TestCase):
    def test_a_development_machine(self):
        view = fixture('layered-and-replaced.json')
        self.assertIsNone(view.pending)
        self.assertFalse(view.transaction)
        self.assertTrue(view.booted.luma_metadata)
        self.assertIn('luma-sticky-notes', view.booted.layered)
        self.assertIn('gnome-shell-extension-appindicator', view.booted.layered)
        self.assertIn('prairie-core-apps', view.booted.replaced)
        self.assertEqual(view.booted.requested_removals, frozenset())

    def test_package_names(self):
        self.assertEqual(sa.package_name('luma-agenda-0.1.0-1.luma.8.fc44.noarch', nevra=True), 'luma-agenda')
        self.assertEqual(sa.package_name('gnome-shell-extension-appindicator'), 'gnome-shell-extension-appindicator')
        self.assertEqual(sa.package_name(['luma-tide-0.1.0-2.fc44.noarch', 'luma-tide', 0, '0.1.0', '2.fc44',
                                          'noarch']), 'luma-tide')
        self.assertEqual(sa.package_name('perl-5:5.40.0-1.fc44.x86_64', nevra=True), 'perl')

    def test_pending_is_the_first_deployment_when_it_is_not_booted(self):
        view = sa.parse_status({"deployments": [
            {"id": "b", "booted": False, "staged": True, "requested-base-removals": ["luma-tide"]},
            {"id": "a", "booted": True}], "transaction": None})
        self.assertEqual((view.pending.id, view.booted.id), ('b', 'a'))
        # A rollback deployment sitting after the booted one is not pending.
        view = sa.parse_status({"deployments": [{"id": "a", "booted": True}, {"id": "r", "booted": False}]})
        self.assertIsNone(view.pending)

    def test_transaction(self):
        view = sa.parse_status({"deployments": [{"id": "a", "booted": True}],
                                "transaction": ["UpdateDeployment", "rpm-ostree override remove x", "/o"]})
        self.assertTrue(view.transaction)

    def test_dbus_variants_unpack(self):
        class Variant:
            def __init__(self, value):
                self.value = value

            def unpack(self):
                return self.value
        deployment = sa.parse_deployment({"booted": Variant(True), "requested-base-removals": Variant(["luma-tide"])})
        self.assertEqual(deployment.requested_removals, frozenset({'luma-tide'}))


def view(booted=None, pending=None):
    rows = [] if pending is None else [{"id": "b", "booted": False, "staged": True, **pending}]
    rows.append({"id": "a", "booted": True, **(booted or {})})
    return sa.parse_status({"deployments": rows})


class States(unittest.TestCase):
    PRESENT = frozenset({'luma-tide', 'prairie-core-apps', 'luma-sticky-notes'})

    def test_installed_with_luma(self):
        self.assertEqual(sa.app_state(view(), self.PRESENT, 'luma-tide'), sa.INSTALLED)

    def test_layered_on_the_development_machine(self):
        machine = fixture('layered-and-replaced.json')
        self.assertEqual(sa.app_state(machine, self.PRESENT, 'luma-sticky-notes'), sa.LAYERED)
        # Locally replaced base packages are still the image's app.
        self.assertEqual(sa.app_state(machine, self.PRESENT, 'prairie-core-apps'), sa.INSTALLED)

    REMOVED_TIDE = [["luma-tide-0.1.0-2.fc44.noarch", "luma-tide", 0, "0.1.0", "2.fc44", "noarch"]]

    def test_removal_staged(self):
        pending = {"requested-base-removals": ["luma-tide"], "base-removals": self.REMOVED_TIDE}
        self.assertEqual(sa.app_state(view(pending=pending), self.PRESENT, 'luma-tide'), sa.REMOVAL_PENDING)

    def test_an_inactive_removal_changes_nothing(self):
        inactive = view(pending={"requested-base-removals": ["luma-tide"], "base-removals": []})
        self.assertEqual(sa.app_state(inactive, self.PRESENT, 'luma-tide'), sa.INSTALLED)
        self.assertTrue(sa.inactive_removal(inactive, 'luma-tide'))
        self.assertFalse(sa.inactive_removal(view(), 'luma-tide'))

    def test_base_database(self):
        def runner(command, **_kwargs):
            self.assertEqual(command[:4], ['rpm', '-q', '--dbpath', str(sa.BASE_DB)])
            return subprocess.CompletedProcess(command, 0 if command[-1] == 'bash' else 1, '', '')
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch('pathlib.Path.is_dir', return_value=True):
            os.environ.pop('LUMA_DEPOT_BASE_PACKAGES', None)
            self.assertTrue(sa.in_base_database('bash', runner=runner))
            self.assertFalse(sa.in_base_database('luma-tide', runner=runner))
        with mock.patch('pathlib.Path.is_dir', return_value=False):
            self.assertIsNone(sa.in_base_database('luma-tide', fixtures=False))

    def test_removed(self):
        removed = {"requested-base-removals": ["luma-tide"],
                   "base-removals": [["luma-tide-0.1.0-2.fc44.noarch", "luma-tide", 0, "0.1.0", "2.fc44", "noarch"]]}
        present = self.PRESENT - {'luma-tide'}
        self.assertEqual(sa.app_state(view(removed), present, 'luma-tide'), sa.REMOVED)
        # Still removed while an unrelated change is staged on top.
        self.assertEqual(sa.app_state(view(removed, pending=removed), present, 'luma-tide'), sa.REMOVED)

    def test_restore_staged(self):
        removed = {"requested-base-removals": ["luma-tide"]}
        state = sa.app_state(view(removed, pending={}), self.PRESENT - {'luma-tide'}, 'luma-tide')
        self.assertEqual(state, sa.RESTORE_PENDING)

    def test_absent_from_the_image(self):
        self.assertEqual(sa.app_state(view(), self.PRESENT, 'luma-session-preview'), sa.ABSENT)

    def test_without_rpm_ostree_only_presence_counts(self):
        self.assertEqual(sa.app_state(None, self.PRESENT, 'luma-tide'), sa.INSTALLED)
        self.assertEqual(sa.app_state(None, self.PRESENT, 'luma-reel'), sa.ABSENT)

    def test_system_owned_states_never_offer_the_flatpak(self):
        self.assertEqual(set(sa.SYSTEM_OWNED), {sa.INSTALLED, sa.LAYERED, sa.REMOVAL_PENDING, sa.RESTORE_PENDING})


class VirtualMachineCaptures(unittest.TestCase):
    """Remove, restart, restore, restart, then the same over a staged update.

    Captured from the Luma nightly (.5 then .6, re-committed with a refreshed
    rpm-ostree base database) with Depot's own helper path doing each change.
    ``installed`` is what ``rpm -q`` answered at each step.
    """

    ALL = frozenset({'luma-tide', 'luma-darkroom', 'luma-leaf', 'prairie-core-apps', 'nautilus'})

    def state(self, name, package, installed=None):
        return sa.app_state(fixture(name), self.ALL if installed is None else installed, package)

    def test_remove_restart_restore_restart(self):
        self.assertEqual(self.state('vm-01-installed.json', 'luma-tide'), sa.INSTALLED)
        self.assertEqual(self.state('vm-02-removal-staged.json', 'luma-tide'), sa.REMOVAL_PENDING)
        gone = self.ALL - {'luma-tide'}
        self.assertEqual(self.state('vm-03-removed.json', 'luma-tide', gone), sa.REMOVED)
        self.assertEqual(self.state('vm-04-restore-staged.json', 'luma-tide', gone), sa.RESTORE_PENDING)
        self.assertEqual(self.state('vm-05-restored.json', 'luma-tide'), sa.INSTALLED)
        for name in ('vm-01-installed.json', 'vm-03-removed.json', 'vm-05-restored.json'):
            self.assertEqual(self.state(name, 'luma-darkroom'), sa.INSTALLED, name)

    def test_what_rpm_ostree_records(self):
        staged = fixture('vm-02-removal-staged.json')
        self.assertTrue(staged.pending.staged)
        self.assertEqual(staged.pending.requested_removals, frozenset({'luma-tide'}))
        self.assertEqual(staged.pending.removals, frozenset({'luma-tide'}))
        self.assertEqual(staged.booted.removals, frozenset())
        self.assertEqual(staged.pending.base_commit, staged.booted.base_commit)
        removed = fixture('vm-03-removed.json')
        self.assertIsNone(removed.pending)
        self.assertEqual(removed.booted.removals, frozenset({'luma-tide'}))

    def test_an_update_staged_over_a_pending_removal_keeps_it(self):
        view = fixture('vm-06-update-staged-over-removal.json')
        self.assertNotEqual(view.pending.base_commit, view.booted.base_commit)
        self.assertEqual(view.pending.removals, frozenset({'luma-darkroom'}))
        self.assertEqual(sa.app_state(view, self.ALL, 'luma-darkroom'), sa.REMOVAL_PENDING)

    def test_a_removal_staged_over_a_staged_update_keeps_the_update(self):
        before = fixture('vm-06-update-staged-over-removal.json')
        after = fixture('vm-07-removal-over-staged-update.json')
        # luma-update recognises its staged update by the base commit; it is unchanged.
        self.assertEqual(after.pending.base_commit, before.pending.base_commit)
        self.assertEqual(after.pending.removals, frozenset({'luma-darkroom', 'luma-tide'}))
        for package in ('luma-tide', 'luma-darkroom'):
            self.assertEqual(sa.app_state(after, self.ALL, package), sa.REMOVAL_PENDING)

    def test_after_the_update_restart_and_restoring_on_the_new_version(self):
        gone = self.ALL - {'luma-tide', 'luma-darkroom'}
        after = fixture('vm-08-removed-after-update.json')
        self.assertEqual(after.booted.base_commit, fixture('vm-07-removal-over-staged-update.json').pending.base_commit)
        for package in ('luma-tide', 'luma-darkroom'):
            self.assertEqual(sa.app_state(after, gone, package), sa.REMOVED)
            self.assertEqual(self.state('vm-09-restores-staged-after-update.json', package, gone),
                             sa.RESTORE_PENDING)


class LumaDetection(unittest.TestCase):
    def release(self, text):
        handle = tempfile.NamedTemporaryFile('w', delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return Path(handle.name)

    def test_a_luma_image(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LUMA_DEPOT_SYSTEM', None)
            self.assertTrue(sa.is_luma(view(), self.release('ID=luma\nID_LIKE=fedora\n')))
            self.assertTrue(sa.is_luma(fixture('layered-and-replaced.json'), self.release('ID=fedora\n')))
            self.assertFalse(sa.is_luma(view(), self.release('ID=fedora\nVARIANT_ID=silverblue\n')))
            self.assertTrue(sa.is_luma(None, self.release('ID=luma\n')))
            self.assertFalse(sa.is_luma(None, self.release('ID=fedora\n')))

    def test_non_luma_systems_have_no_system_state(self):
        apps = sa.SystemApps(False, view(), frozenset({'nautilus'}))
        self.assertEqual(apps.state('nautilus'), sa.ABSENT)


class Queries(unittest.TestCase):
    def test_installed_packages_asks_rpm_once_and_ignores_bad_names(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, 'luma-tide\npackage luma-reel is not installed\n', '')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LUMA_DEPOT_INSTALLED_PACKAGES', None)
            found = sa.installed_packages(['luma-tide', 'luma-reel', '-bad'], runner=runner)
        self.assertEqual(found, frozenset({'luma-tide'}))
        self.assertEqual(calls, [['rpm', '-q', '--qf', '%{NAME}\\n', '--', 'luma-reel', 'luma-tide']])

    def test_required_by(self):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, 'luma-agenda\nluma-continuity\nprairie-core-apps\n', '')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LUMA_DEPOT_INSTALLED_PACKAGES', None)
            self.assertEqual(sa.required_by('prairie-core-apps', runner=runner), ('luma-agenda', 'luma-continuity'))


if __name__ == '__main__':
    unittest.main()
