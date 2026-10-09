# SPDX-License-Identifier: Apache-2.0
"""An image's native canonical launcher must not hide its Flatpak update."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from luma_depot import native
from luma_depot.providers import Result
from luma_installer import depot_flatpak  # Load the real source registry before catalogue mocks.


class NativeLauncherUpdates(unittest.TestCase):
    def setUp(self):
        self.app_id = 'com.rhyme.viola'
        self.assertIn(self.app_id, depot_flatpak.APPLICATION_SOURCES.values())
        self.info = Mock()
        self.info.get_string.return_value = None
        self.record = SimpleNamespace(
            desktop_id=self.app_id + '.desktop', app_info=self.info,
            name='Viola', provider='System or external package',
            management='Managed by its package provider', can_review_removal=False)
        self.ref = Mock()
        self.ref.get_origin.return_value = 'luma'
        self.ref.get_commit.return_value = 'installed-commit'
        self.ref.get_appdata_version.return_value = '0.2.10'
        self.ref.get_installed_size.return_value = 500
        self.ref.get_branch.return_value = 'beta'
        self.refs = {self.app_id: (Mock(), self.ref)}

    def test_canonical_native_launcher_reports_real_installed_update(self):
        provider = native.NativeInstallation()
        catalog = SimpleNamespace(applications=[SimpleNamespace(
            id='viola', backend='flatpak', source_id=self.app_id,
            luma_system=None, name='Viola', summary='Browser')])
        pending = {'version': '0.2.10.36', 'bytes': 50, 'summary': 'New UI',
                   'changes': (), 'installed_commit': 'installed-commit',
                   'commit': 'published-commit'}
        results = []

        def synchronous(work, callback, _cancellable=None):
            callback(Result(value=work()))

        with (patch.object(native, '_system_identities', {}),
              patch.object(native, 'inventory', return_value=[self.record]),
              patch.object(native, 'installed_flatpak_refs', return_value=self.refs),
              patch.object(native, 'local_permissions', return_value=()),
              patch.object(native, 'run_async', side_effect=synchronous),
              patch.object(provider, '_channel_state', return_value=None),
              patch.object(provider, '_pending_updates', return_value={self.app_id: pending}),
              patch('luma_installer.depot_catalog.local_catalog', return_value=catalog)):
            provider.installed(results.append)

        self.assertTrue(results[0].ok)
        self.assertEqual(len(results[0].value), 1)
        installed = results[0].value[0]
        self.assertEqual(installed.app_id, 'catalog:viola')
        self.assertTrue(installed.managed)
        self.assertEqual(installed.commit, 'installed-commit')
        self.assertEqual(installed.update_commit, 'published-commit')
        self.assertEqual(installed.update_channel, 'beta')

    def test_no_inference_from_alias_missing_ref_or_unknown_origin(self):
        self.record.desktop_id = 'viola-browser.desktop'
        self.assertIsNone(native.installed_source(self.record, self.refs))
        self.record.desktop_id = self.app_id + '.desktop'
        self.assertIsNone(native.installed_source(self.record, {}))
        self.ref.get_origin.return_value = 'unknown-remote'
        self.assertIsNone(native.installed_source(self.record, self.refs))

    def test_explicit_flatpak_identity_is_not_overridden(self):
        self.info.get_string.return_value = 'org.example.Other'
        self.assertEqual(native.installed_source(self.record, self.refs), 'org.example.Other')


if __name__ == '__main__':
    unittest.main()
