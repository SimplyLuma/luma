"""Depot says what went wrong in plain words, and updates never pin a commit."""
import sys
import types
import unittest
from unittest import mock

from luma_installer import depot_errors, depot_flatpak
from luma_installer.depot_errors import explain


class FakeGLibError(Exception):
    def __init__(self, domain, code, message):
        super().__init__(f'{domain}: {message} ({code})')
        self.domain, self.code, self.message = domain, code, message


class Explain(unittest.TestCase):
    def test_the_thinkpad_failure_reads_as_a_sentence_with_the_raw_text_behind_details(self):
        error = FakeGLibError('flatpak-error-quark', 4,
                              "Aborted due to failure (Can't update to a specific commit without root permissions)")
        result = explain(error, name='Discord', action='update')
        self.assertTrue(result.message.startswith("Discord couldn't update"))
        self.assertNotIn('quark', result.message)
        self.assertNotIn('(4)', result.message)
        self.assertIn("Can't update to a specific commit", result.detail)
        self.assertEqual(result.kind, 'authorization')

    def test_causes_wrapped_in_aborted_are_found(self):
        cases = {
            'Aborted due to failure (While pulling app/x from remote flathub: min-free-space-size 500MB would be exceeded)': 'space',
            'Aborted due to failure (While fetching https://dl.flathub.org/repo/objects/ab/cd: Could not resolve hostname)': 'network',
            'Aborted due to failure (GPG signatures found, but none are in trusted keyring)': 'verification',
            'Aborted due to failure (Flatpak system operation Deploy not allowed for user)': 'authorization',
        }
        for text, kind in cases.items():
            with self.subTest(kind=kind):
                result = explain(FakeGLibError('flatpak-error-quark', 4, text), name='Signal', action='install')
                self.assertEqual(result.kind, kind)
                self.assertTrue(result.message.startswith("Signal couldn't be installed"))
                self.assertEqual(result.detail, text)

    def test_codes_without_telling_text(self):
        self.assertEqual(explain(FakeGLibError('flatpak-error-quark', 18, 'x'), name='A', action='update').kind, 'space')
        self.assertEqual(explain(FakeGLibError('flatpak-error-quark', 24, 'x'), name='A', action='update').kind,
                         'authorization')
        self.assertEqual(explain(FakeGLibError('flatpak-error-quark', 6, 'x'), name='A', action='update').kind, 'flatpak')
        other = explain(FakeGLibError('flatpak-error-quark', 11, 'Invalid data'), name='A', action='remove')
        self.assertEqual(other.message, "A couldn't be removed. Try again, or see details.")

    def test_depots_own_checks_already_speak_plainly(self):
        error = depot_flatpak.SourceUnavailable('Flathub is not configured on this device.')
        result = explain(error, name='VLC', action='install')
        self.assertEqual(result.message, "VLC couldn't be installed. Flathub is not configured on this device.")

    def test_every_failure_is_journaled_for_vitals(self):
        sent = []
        journal_module = types.SimpleNamespace(send=lambda line, **fields: sent.append((line, fields)))
        systemd = types.ModuleType('systemd')
        systemd.journal = journal_module
        error = FakeGLibError('flatpak-error-quark', 4, 'Aborted due to failure (Not authorized)')
        explanation = explain(error, name='Discord', action='update')
        with mock.patch.dict(sys.modules, {'systemd': systemd, 'systemd.journal': journal_module}):
            depot_errors.journal(explanation, app_id='catalog:discord', name='Discord', action='update', error=error)
        (line, fields), = sent
        self.assertEqual(fields['MESSAGE_ID'], depot_errors.MESSAGE_ID)
        self.assertEqual((fields['LUMA_ERROR_DOMAIN'], fields['LUMA_ERROR_CODE']), ('flatpak-error-quark', '4'))
        self.assertEqual(fields['LUMA_DEPOT_FAILURE'], 'authorization')
        self.assertIn('Not authorized', line)


class EveryStartIsVisible(unittest.TestCase):
    """Depot may never leave a person with nothing: every start is in the journal."""

    def lifecycle(self, *arguments, **keywords):
        sent = []
        journal_module = types.SimpleNamespace(send=lambda line, **fields: sent.append((line, fields)))
        systemd = types.ModuleType('systemd')
        systemd.journal = journal_module
        with mock.patch.dict(sys.modules, {'systemd': systemd, 'systemd.journal': journal_module}):
            depot_errors.lifecycle(*arguments, **keywords)
        return sent

    def test_a_window_on_screen_is_recorded_with_how_long_it_took(self):
        (line, fields), = self.lifecycle('shown', seconds=1.25, window='DepotWindow')
        self.assertEqual(fields['MESSAGE_ID'], depot_errors.LIFECYCLE_MESSAGE_ID)
        self.assertEqual(fields['LUMA_DEPOT_LIFECYCLE'], 'shown')
        self.assertEqual(fields['LUMA_DEPOT_WINDOW'], 'DepotWindow')
        self.assertEqual(fields['PRIORITY'], '6')
        self.assertIn('window on screen', line)
        self.assertIn('1.2s', line)

    def test_a_start_that_failed_is_an_error_naming_the_reason(self):
        (line, fields), = self.lifecycle('failed', detail='RuntimeError: no catalogue', seconds=0.4)
        self.assertEqual(fields['PRIORITY'], '3')
        self.assertEqual(fields['LUMA_DEPOT_LIFECYCLE'], 'failed')
        self.assertEqual(fields['LUMA_DEPOT_DETAIL'], 'RuntimeError: no catalogue')
        self.assertIn('could not open its window', line)

    def test_a_launch_with_no_window_yet_is_a_warning(self):
        (_line, fields), = self.lifecycle('slow', seconds=12.0, detail='the launch has not put a window on screen')
        self.assertEqual(fields['PRIORITY'], '4')
        self.assertEqual(fields['LUMA_DEPOT_SECONDS'], '12.000')

    def test_a_service_depot_only_shows_is_not_a_failed_start(self):
        (line, fields), = self.lifecycle('degraded', detail='the firmware check could not be started: x')
        self.assertEqual(fields['PRIORITY'], '4')
        self.assertIn('without one of its services', line)

    def test_the_journal_missing_never_raises(self):
        systemd = types.ModuleType('systemd')   # a systemd module with no journal
        with mock.patch.dict(sys.modules, {'systemd': systemd}):
            depot_errors.lifecycle('asked', detail='--view updates')


class UpdatesFollowTheRemote(unittest.TestCase):
    def test_an_update_names_no_commit_and_still_checks_the_resolved_one(self):
        source = depot_flatpak.ResolvedSource('com.discordapp.Discord', 'x86_64',
                                              'app/com.discordapp.Discord/x86_64/stable', 'new', 'flathub')
        calls = {}

        class Transaction:
            @staticmethod
            def new_for_installation(installation, cancellable):
                return Transaction()

            def connect(self, signal, handler):
                calls[signal] = handler

            def add_update(self, ref, subpaths, commit):
                calls['add_update'] = (ref, subpaths, commit)

            def add_install(self, remote, ref, subpaths):
                calls['add_install'] = (remote, ref, subpaths)

        installed = types.SimpleNamespace(get_origin=lambda: 'flathub', get_commit=lambda: 'old')
        repository = types.SimpleNamespace(Flatpak=types.SimpleNamespace(Transaction=Transaction), GLib=types.SimpleNamespace(Error=Exception))
        gi = types.ModuleType('gi')
        gi.repository = repository
        gi.require_version = lambda *args: None
        with mock.patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}), \
                mock.patch.object(depot_flatpak, 'resolve', return_value=source), \
                mock.patch.object(depot_flatpak, 'installed_ref', return_value=installed):
            tx = depot_flatpak.transaction(object(), source, update=True)
        self.assertIsNotNone(tx)
        self.assertEqual(calls['add_update'], (source.ref, None, None))

        op = lambda commit, remote='flathub', ref=source.ref: types.SimpleNamespace(  # noqa: E731
            get_ref=lambda: ref, get_commit=lambda: commit, get_remote=lambda: remote)
        remote = types.SimpleNamespace(get_url=lambda: 'https://dl.flathub.org/repo/', get_disabled=lambda: False,
                                       get_gpg_verify=lambda: True, get_name=lambda: 'flathub')
        installation = types.SimpleNamespace(get_remote_by_name=lambda name, cancellable: remote)
        with mock.patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}), \
                mock.patch.object(depot_flatpak, 'resolve', return_value=source), \
                mock.patch.object(depot_flatpak, 'installed_ref', return_value=installed):
            depot_flatpak.transaction(installation, source, update=True)
        ready = calls['ready']
        self.assertTrue(ready(types.SimpleNamespace(get_operations=lambda: [op('new')])))
        # A remote that moved between resolving and preparing is refused.
        self.assertFalse(ready(types.SimpleNamespace(get_operations=lambda: [op('newer')])))



class GoingBack(unittest.TestCase):
    def test_a_version_the_source_no_longer_offers_is_said_plainly(self):
        from luma_installer import depot_errors
        for text in ("commit-unavailable: No such metadata object abc.commit",
                     "error: No such metadata object 1234.commit"):
            explanation = depot_errors.explain(RuntimeError(text), name="Notes", action="revert")
            self.assertEqual(explanation.kind, "gone")
            self.assertIn("no longer offers that version", explanation.message)
            self.assertIn("couldn't go back", explanation.message)

    def test_activity_is_journaled_without_raising(self):
        from luma_installer import depot_errors
        depot_errors.activity("updated", app_id="org.example.Notes", name="Notes", automatic=True,
                              detail="1.0 to 1.1", version="1.1")

if __name__ == '__main__':
    unittest.main()
