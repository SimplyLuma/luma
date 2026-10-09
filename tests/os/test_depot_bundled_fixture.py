# SPDX-License-Identifier: MPL-2.0
"""Exercise the actual embedded clean-install fixture without a real installation."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest

SCRIPT = Path(__file__).parent / 'gate' / 'depot-out-of-box.sh'
TEXT = SCRIPT.read_text()
CODE = TEXT.split('original = depot_flatpak.installed_ref(installation, source)', 1)[1].split('\nPY\n  py_check', 1)[0]
CODE = 'original = depot_flatpak.installed_ref(installation, source)' + CODE


class FixtureTests(unittest.TestCase):
    def exercise(self, present, fail_remove=False, mismatched=False):
        source = SimpleNamespace(ref='app/Notes/x86_64/beta', commit='signed')
        ref = SimpleNamespace(format_ref=lambda: source.ref,
                              get_commit=lambda: 'older' if mismatched else source.commit,
                              get_origin=lambda: 'luma')
        state = {'ref': ref if present else None, 'events': []}
        def remove(installation, installed):
            def run(unused):
                state['events'].append('remove')
                state['ref'] = None
                if fail_remove and len(state['events']) == 1:
                    raise RuntimeError('observed removal failure')
            return SimpleNamespace(run=run)
        def install(installation, resolved):
            if state['ref'] is not None:
                return None
            def run(unused):
                state['events'].append('install')
                state['ref'] = ref
            return SimpleNamespace(run=run)
        api = SimpleNamespace(installed_ref=lambda *args: state['ref'],
                              removal_transaction=remove, transaction=install)
        namespace = dict(depot_flatpak=api, installation=SimpleNamespace(get_id=lambda: 'system'),
                         source=source, remote='luma', app_id='Notes')
        error = None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                exec(compile(CODE, str(SCRIPT), 'exec'), namespace)
            except (RuntimeError, SystemExit) as exc:
                error = exc
        return state, error

    def test_bundled_app_removed_installed_removed_and_exactly_restored(self):
        state, error = self.exercise(True)
        self.assertIsNone(error)
        self.assertEqual(state['events'], ['remove', 'install', 'remove', 'install'])
        self.assertEqual(state['ref'].get_commit(), 'signed')

    def test_initially_absent_app_is_left_absent(self):
        state, error = self.exercise(False)
        self.assertIsNone(error)
        self.assertEqual(state['events'], ['install', 'remove'])
        self.assertIsNone(state['ref'])

    def test_failure_restores_bundled_app_and_remains_failure(self):
        state, error = self.exercise(True, fail_remove=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(state['events'], ['remove', 'install'])
        self.assertEqual(state['ref'].get_commit(), 'signed')

    def test_other_installed_commit_is_not_changed(self):
        state, error = self.exercise(True, mismatched=True)
        self.assertIsInstance(error, SystemExit)
        self.assertEqual(state['events'], [])
        self.assertEqual(state['ref'].get_commit(), 'older')


if __name__ == '__main__':
    unittest.main()
