# SPDX-License-Identifier: Apache-2.0
"""ADR-031: the system helper's override-remove and override-reset validation."""

from copy import deepcopy
import dataclasses
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from luma_installer import system_overrides as so
from luma_installer.depot_catalog import validate_catalog
from luma_installer.depot_system_apps import parse_status

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'rpm-ostree'


def entry(app_id, package, desktop, removable=True, tier='luma'):
    return {"id": app_id, "name": app_id.title(), "tier": tier, "visibility": "public",
            "backend": "rpm", "repository": "luma", "source_id": package, "distribution": "publisher",
            "architectures": ["x86_64"], "reference_url": f"https://simplyluma.com/apps/{app_id}",
            "qualification": "pending", "qualification_reason": "Included with Luma.",
            "sources": {"luma_system": {"package": package, "desktop_id": desktop, "removable": removable}}}


def catalog(*entries, generated="2026-09-15T00:00:00Z"):
    return validate_catalog({"schema_version": 4, "generated_at": generated,
                             "applications": [deepcopy(e) for e in entries]})


TIDE = entry('tide', 'luma-tide', 'org.projectluma.Tide.desktop')
REMOVED_TIDE = ["luma-tide-0.1.0-2.fc44.noarch", "luma-tide", 0, "0.1.0", "2.fc44", "noarch"]
NOTES = entry('notes', 'prairie-core-apps', 'org.projectluma.Notes.desktop')
PHONE = entry('phone', 'prairie-core-apps', 'org.projectluma.Phone.desktop', removable=False)
FILER = entry('filer', 'nautilus', 'org.gnome.Nautilus.desktop', removable=False)
SHELL = entry('shell', 'gnome-shell', 'org.gnome.Shell.desktop')


def status(booted=None, pending=None, transaction=None):
    rows = []
    if pending is not None:
        rows.append({"id": "luma-b.0", "checksum": "b", "base-checksum": "base2", "booted": False,
                     "staged": True, "origin": "luma:luma/1/x86_64/stable", **pending})
    rows.append({"id": "luma-a.0", "checksum": "a", "booted": True, "staged": False,
                 "origin": "luma:luma/1/x86_64/stable", **(booted or {})})
    return parse_status({"deployments": rows, "transaction": transaction})


class Runner:
    """Answers rpm queries like an rpm database holding ``present``."""

    def __init__(self, present=('luma-tide', 'prairie-core-apps', 'nautilus'), requires=None, base=None):
        self.present, self.requires, self.calls = set(present), requires or {}, []
        self.base = set(present if base is None else base)

    def __call__(self, command, **_kwargs):
        self.calls.append(command)
        if command[1:3] == ['-q', '--dbpath']:
            return subprocess.CompletedProcess(command, 0 if command[-1] in self.base else 1, '', '')
        if command[1:3] == ['-q', '--']:
            return subprocess.CompletedProcess(command, 0 if command[3] in self.present else 1, '', '')
        if '--whatrequires' in command:
            needed = self.requires.get(command[-1], ())
            return subprocess.CompletedProcess(command, 0 if needed else 1,
                                               ''.join(f'{name}\n' for name in needed), '')
        raise AssertionError(command)


class RemoveValidation(unittest.TestCase):
    def check(self, package, view=None, catalogs=None, runner=None):
        return so.check_remove(package, view or status(), catalogs if catalogs is not None else [catalog(TIDE)],
                               runner=runner or Runner())

    def test_a_removable_base_package_may_be_removed(self):
        self.assertFalse(self.check('luma-tide'))

    def test_package_names_are_validated_strictly(self):
        for name in ('', '-luma-tide', '--install=evil', 'luma tide', 'luma-tide;reboot', '../luma-tide',
                     'luma/tide', 'a' * 129, 'luma-tide\n', None, 3):
            with self.assertRaises(so.Refused, msg=repr(name)):
                self.check(name)

    def test_only_catalogued_removable_packages(self):
        with self.assertRaisesRegex(so.Refused, 'catalogue'):
            self.check('luma-darkroom', runner=Runner(present={'luma-darkroom'}))
        with self.assertRaisesRegex(so.Refused, 'catalogue'):
            self.check('nautilus', catalogs=[catalog(FILER)])
        with self.assertRaisesRegex(so.Refused, 'catalogue'):
            self.check('luma-tide', catalogs=[])

    def test_every_listing_of_a_shared_package_must_allow_it(self):
        self.assertFalse(self.check('prairie-core-apps', catalogs=[catalog(NOTES)]))
        with self.assertRaisesRegex(so.Refused, 'catalogue'):
            self.check('prairie-core-apps', catalogs=[catalog(NOTES, PHONE)])

    def test_a_non_luma_tier_listing_cannot_authorize_removal(self):
        verified = deepcopy(TIDE)
        verified['tier'] = 'verified'
        with self.assertRaisesRegex(so.Refused, 'catalogue'):
            self.check('luma-tide', catalogs=[catalog(verified)])

    def test_the_newest_trusted_catalogue_decides(self):
        withdrawn = deepcopy(TIDE)
        withdrawn['sources']['luma_system']['removable'] = False
        older, newer = catalog(TIDE, generated="2026-09-01T00:00:00Z"), catalog(withdrawn)
        with self.assertRaises(so.Refused):
            self.check('luma-tide', catalogs=[older, newer])
        stale = dataclasses.replace(newer, generated_at='2026-08-01T00:00:00Z')
        self.assertFalse(self.check('luma-tide', catalogs=[stale, older]))

    def test_the_floor_wins_over_any_catalogue(self):
        with self.assertRaisesRegex(so.Refused, 'needs this package'):
            self.check('gnome-shell', catalogs=[catalog(SHELL)], runner=Runner(present={'gnome-shell'}))

    def test_refused_while_another_transaction_runs(self):
        busy = status(transaction=["PkgChange", "rpm-ostree override remove", "/org/x"])
        with self.assertRaisesRegex(so.Refused, 'in progress'):
            self.check('luma-tide', view=busy)
        with self.assertRaisesRegex(so.Refused, 'in progress'):
            so.check_reset('luma-tide', busy)

    def test_layered_and_replaced_packages_are_not_base_removals(self):
        with self.assertRaisesRegex(so.Refused, 'added to this computer'):
            self.check('luma-tide', view=status({"requested-packages": ["luma-tide"]}))
        replaced = status({"base-local-replacements": [[["luma-tide-2-1.noarch", "luma-tide", 0, "2", "1", "noarch"],
                                                        ["luma-tide-1-1.noarch", "luma-tide", 0, "1", "1", "noarch"]]]})
        with self.assertRaisesRegex(so.Refused, 'replaced'):
            self.check('luma-tide', view=replaced)

    def test_packages_other_packages_need_are_refused_with_their_names(self):
        runner = Runner(requires={'prairie-core-apps': ('luma-agenda', 'luma-continuity')})
        with self.assertRaisesRegex(so.Refused, 'luma-agenda, luma-continuity'):
            self.check('prairie-core-apps', catalogs=[catalog(NOTES)], runner=runner)

    def test_absent_packages_are_refused(self):
        with self.assertRaisesRegex(so.Refused, 'not part of the system'):
            self.check('luma-tide', runner=Runner(present=()))

    def test_an_already_pending_removal_is_nothing_to_do(self):
        pending = {"requested-base-removals": ["luma-tide"], "base-removals": [REMOVED_TIDE]}
        self.assertTrue(self.check('luma-tide', view=status(pending=pending)))

    def test_a_package_missing_from_rpm_ostrees_base_database_is_refused(self):
        # The Luma nightly of 2026-09-15: its base database lists only the
        # Fedora base image, and rpm-ostree records the removal as inactive.
        with mock.patch.object(so, 'in_base_database', wraps=so.in_base_database) as probe, \
                mock.patch('pathlib.Path.is_dir', return_value=True):
            with self.assertRaisesRegex(so.Refused, 'package database'):
                self.check('luma-tide', runner=Runner(base=()))
        self.assertFalse(probe.call_args.kwargs['fixtures'])

    def test_an_inactive_removal_is_refused(self):
        with self.assertRaisesRegex(so.Refused, 'cannot remove'):
            self.check('luma-tide', view=status(pending={"requested-base-removals": ["luma-tide"]}))


class ResetValidation(unittest.TestCase):
    def test_restore_is_allowed_for_anything_in_the_removal_set(self):
        # No catalogue at all: a person can always undo a removal.
        self.assertFalse(so.check_reset('luma-darkroom', status({"requested-base-removals": ["luma-darkroom"]})))
        self.assertFalse(so.check_reset('luma-tide', status(pending={"requested-base-removals": ["luma-tide"]})))

    def test_restore_reads_the_deployment_the_next_boot_uses(self):
        # Removed in the booted system, already restored in the staged one.
        view = status({"requested-base-removals": ["luma-tide"]}, pending={"requested-base-removals": []})
        with self.assertRaisesRegex(so.Refused, 'has not been removed'):
            so.check_reset('luma-tide', view)

    def test_restore_refuses_what_was_not_removed_and_bad_names(self):
        with self.assertRaisesRegex(so.Refused, 'has not been removed'):
            so.check_reset('luma-tide', status())
        with self.assertRaises(so.Refused):
            so.check_reset('--help', status({"requested-base-removals": ["--help"]}))


class FakePopen:
    def __init__(self, lines, code=0):
        self.lines, self.code, self.command = lines, code, None

    def __call__(self, command, **_kwargs):
        self.command = command
        self.stdout = io.StringIO(''.join(line + '\n' for line in self.lines))
        return self

    def wait(self, timeout=None):
        return self.code


class Transaction(unittest.TestCase):
    def run_override(self, action, package, before, after, popen, catalogs=None):
        views = iter((before, after))
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            result = so.override(action, package, invoking_uid=1000, runner=Runner(), popen=popen,
                                 catalogs=catalogs if catalogs is not None else [catalog(TIDE)],
                                 read_status=lambda: next(views))
        return result, output.getvalue()

    def test_remove_runs_rpm_ostree_and_reports_progress(self):
        popen = FakePopen(['Checking out tree 4e550c3... done', 'Inactive base packages: luma-tide',
                           'Writing OSTree commit... done', 'Staging deployment... done', 'Freed: 10 MB'])
        result, output = self.run_override('override-remove', 'luma-tide', status(),
                                           status(pending={"requested-base-removals": ["luma-tide"],
                                                           "base-removals": [REMOVED_TIDE]}), popen)
        self.assertEqual(result, 'staged')
        self.assertEqual(popen.command, [so.RPM_OSTREE, 'override', 'remove', 'luma-tide'])
        fractions = [float(line.split()[1]) for line in output.splitlines() if line.startswith('progress:')]
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(fractions[-1], 1.0)

    def test_reset_runs_rpm_ostree(self):
        popen = FakePopen(['Staging deployment... done'])
        result, _ = self.run_override('override-reset', 'luma-tide',
                                      status({"requested-base-removals": ["luma-tide"]}),
                                      status({"requested-base-removals": ["luma-tide"]}, pending={}), popen,
                                      catalogs=[])
        self.assertEqual((result, popen.command[1:]), ('staged', ['override', 'reset', 'luma-tide']))

    def test_success_without_the_staged_change_is_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'did not stage'):
            self.run_override('override-reset', 'luma-tide', status({"requested-base-removals": ["luma-tide"]}),
                              status({"requested-base-removals": ["luma-tide"]}), FakePopen(['done']), catalogs=[])

    def test_an_inactive_removal_is_withdrawn_and_reported(self):
        # What rpm-ostree 2026.2 does on the nightly: success, "Inactive base
        # removals: luma-tide", and a staged deployment that removes nothing.
        commands = []

        class Recording(FakePopen):
            def __call__(self, command, **kwargs):
                commands.append(command[1:])
                return super().__call__(command, **kwargs)
        inactive = status(pending={"requested-base-removals": ["luma-tide"], "base-removals": []})
        with self.assertRaisesRegex(RuntimeError, 'nothing changed'):
            self.run_override('override-remove', 'luma-tide', status(), inactive,
                              Recording(['Inactive base removals:', '  luma-tide', 'Staging deployment...done']))
        self.assertEqual(commands, [['override', 'remove', 'luma-tide'], ['override', 'reset', 'luma-tide'],
                                    ['cleanup', '--pending']])

    def test_withdrawing_never_cleans_up_someone_elses_staged_deployment(self):
        commands = []

        class Recording(FakePopen):
            def __call__(self, command, **kwargs):
                commands.append(command[1:])
                return super().__call__(command, **kwargs)
        update = status(pending={})
        with self.assertRaises(RuntimeError):
            self.run_override('override-remove', 'luma-tide', update, update, Recording([]))
        self.assertNotIn(['cleanup', '--pending'], commands)

    def test_rpm_ostree_failure_reports_its_last_line(self):
        with self.assertRaisesRegex(RuntimeError, 'requires luma-tide'):
            self.run_override('override-remove', 'luma-tide', status(), status(),
                              FakePopen(['Resolving dependencies...', 'error: package x requires luma-tide'], 1))

    def test_a_pending_removal_is_not_staged_twice(self):
        popen = FakePopen([])
        pending = status(pending={"requested-base-removals": ["luma-tide"], "base-removals": [REMOVED_TIDE]})
        result, _ = self.run_override('override-remove', 'luma-tide', pending, pending, popen)
        self.assertEqual((result, popen.command), ('unchanged', None))


class TrustedCatalogs(unittest.TestCase):
    def test_a_cached_catalogue_must_verify_and_belong_to_the_caller(self):
        from luma_installer import depot_catalog
        with tempfile.TemporaryDirectory() as home:
            cache = Path(home) / so.CACHE
            cache.parent.mkdir(parents=True)
            cache.write_text(json.dumps({"schema_version": 4, "generated_at": "2026-09-30T00:00:00Z",
                                         "applications": [TIDE]}))
            depot_catalog.signature_path(cache).write_bytes(b'not a signature')
            seed = Path(home) / 'seed.json'
            seed.write_text('{}')
            found = so.trusted_catalogs(os.getuid(), seed=seed, key=seed, home_for=lambda _uid: Path(home))
            # Neither the unsigned cache nor a seed not owned by root is trusted.
            self.assertEqual(found, [])

    def test_links_and_fifos_are_never_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'real'
            target.write_text('x')
            link = Path(directory) / 'link'
            link.symlink_to(target)
            with self.assertRaises(OSError):
                so._read_owned(link, os.getuid(), 10)
            fifo = Path(directory) / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaises(OSError):
                so._read_owned(fifo, os.getuid(), 10)
            self.assertEqual(so._read_owned(target, os.getuid(), 10), b'x')
            with self.assertRaises(OSError):
                so._read_owned(target, os.getuid() + 1, 10)


class HelperEntry(unittest.TestCase):
    def test_the_helper_refuses_without_root(self):
        from luma_installer import system_helper
        if os.geteuid() == 0:
            self.skipTest('running as root')
        with mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(system_helper.main(['override-remove', 'luma-tide']), 1)

    def test_refusals_exit_3_with_the_reason(self):
        from contextlib import contextmanager

        @contextmanager
        def lock():
            yield
        errors = io.StringIO()
        with mock.patch.object(so, 'override', side_effect=so.Refused('Nope.')), \
                mock.patch('sys.stderr', errors), mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(so.main(['override-remove', 'luma-tide'], lock=lock), 3)
        self.assertIn('refused: Nope.', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
