# SPDX-License-Identifier: Apache-2.0
# Private source integration: real GLib, controlled libflatpak transaction boundary.
import unittest
from types import SimpleNamespace as NS
from unittest import mock
import gi.repository as repository
from gi.repository import GLib
from luma_installer import depot_flatpak as flatpak

OLD, NEW = 'a' * 64, 'b' * 64
SOURCE = flatpak.ResolvedSource('org.mozilla.firefox', 'x86_64',
    'app/org.mozilla.firefox/x86_64/stable', NEW, 'flathub', 'stable')

class Transaction:
    def __init__(self):
        self.callbacks = {}
        self.operations = [NS(get_ref=lambda: SOURCE.ref, get_commit=lambda: NEW,
                              get_remote=lambda: 'flathub')]
        self.updates = []
    def connect(self, signal, callback): self.callbacks[signal] = callback
    def get_operations(self): return self.operations
    def add_update(self, *arguments): self.updates.append(arguments)

class ActualReadyBoundary(unittest.TestCase):
    def setUp(self):
        self.state = {'commit': OLD, 'present': True, 'url': 'https://dl.flathub.org/repo/'}
        self.tx = Transaction()
        self.create = mock.Mock(return_value=self.tx)
        self.typelib = mock.patch.object(repository, 'Flatpak',
            NS(Transaction=NS(new_for_installation=self.create)), create=True)
        self.typelib.start()
        self.ref = NS(format_ref=lambda: SOURCE.ref, get_commit=lambda: self.state['commit'],
                      get_origin=lambda: 'flathub')
        self.remote = NS(get_url=lambda: self.state['url'], get_disabled=lambda: False,
                         get_gpg_verify=lambda: True)
        self.installation = NS(list_installed_refs=lambda _: [self.ref] if self.state['present'] else [],
                               get_remote_by_name=lambda *_: self.remote)
        self.resolve = mock.patch.object(flatpak, 'resolve', return_value=SOURCE)
        self.resolve.start()
    def tearDown(self):
        self.resolve.stop()
        self.typelib.stop()
    def prepare(self):
        return flatpak.transaction(self.installation, SOURCE, update=True,
                                   expected_installed_commit=OLD)
    def test_unchanged_baseline_and_trusted_target_proceeds(self):
        self.assertIs(self.prepare(), self.tx)
        self.assertTrue(self.tx.callbacks['ready'](self.tx))
        self.assertEqual(self.tx.updates, [(SOURCE.ref, None, None)])
    def test_replaced_baseline_fails_before_transaction_creation(self):
        self.state['commit'] = 'c' * 64
        with self.assertRaises(flatpak.SourceUnavailable): self.prepare()
        self.create.assert_not_called()
    def test_replaced_or_removed_baseline_fails_at_ready(self):
        self.prepare()
        self.state['commit'] = 'c' * 64
        self.assertFalse(self.tx.callbacks['ready'](self.tx))
        self.state['present'] = False
        self.assertFalse(self.tx.callbacks['ready'](self.tx))
    def test_changed_prepared_target_fails_at_ready(self):
        self.prepare()
        self.tx.operations[0].get_commit = lambda: 'd' * 64
        self.assertFalse(self.tx.callbacks['ready'](self.tx))
    def test_changed_remote_trust_fails_at_ready(self):
        self.prepare()
        self.state['url'] = 'https://untrusted.invalid/repo/'
        self.assertFalse(self.tx.callbacks['ready'](self.tx))

if __name__ == '__main__': unittest.main()
