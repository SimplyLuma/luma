# SPDX-License-Identifier: Apache-2.0
"""Real Depot provider/controller with controlled inventory and remote boundaries.

These are source integration tests, not a signed Flatpak install/boot gate.
They require actual Gio/GLib; no fake gi module may supply a passing result.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve()
if (HERE.parents[2] / 'luma-depot').is_dir():
    sys.path.insert(0, str(HERE.parents[2] / 'luma-depot'))

from gi.repository import GLib
from luma_depot import native, autoupdate
from luma_depot.providers import Result, Permission
from luma_installer import depot_flatpak as flatpak, depot_catalog, depot_autoupdate as policy

APP = 'org.projectluma.Tide'
OLD, NEW = 'a' * 64, 'b' * 64
ENTRY = NS(id='tide', source_id=APP, name='Tide', summary='Music', backend='flatpak', release=None)
GROW = Permission('files.music', 'Music', '', True, 'sensitive', 'widened')
PENDING = {'version': '2', 'bytes': 123, 'summary': 'New', 'commit': NEW, 'changes': (GROW,), 'installed_commit': OLD}


class InventorySafety(unittest.TestCase):
    def test_no_exported_launcher_still_holds_widened_update(self):
        ref = NS(get_appdata_version=lambda: '1', get_installed_size=lambda: 20,
                 get_origin=lambda: 'luma', get_commit=lambda: OLD, get_branch=lambda: 'beta')
        provider = native.NativeInstallation()
        with mock.patch.object(provider, '_channel_state', return_value=None), \
             mock.patch.object(provider, '_pending_updates', return_value={APP: PENDING}), \
             mock.patch.object(native, 'inventory', return_value=()), \
             mock.patch.object(native, 'installed_flatpak_refs', return_value={APP: (object(), ref)}), \
             mock.patch.object(native, 'local_permissions', return_value=()), \
             mock.patch.object(native, 'run_async', side_effect=lambda work, cb, _: cb(Result(value=work()))), \
             mock.patch.object(flatpak, 'APPLICATION_SOURCES', {'tide': APP}), \
             mock.patch.object(depot_catalog, 'local_catalog', return_value=NS(applications=(ENTRY,))):
            results = []
            provider.installed(results.append)
        self.assertEqual(len(results[0].value), 1)
        self.assertEqual(results[0].value[0].update_channel, 'beta')
        items = autoupdate.pending_for(results[0].value)
        self.assertTrue(items[0].widens)
        self.assertEqual((items[0].commit, items[0].installed_commit), (NEW, OLD))
        self.assertEqual(policy.plan(items, {'approved': [], 'notified': []}, enabled=True).install, ())

    def test_cached_comparison_cannot_be_rebound_to_replaced_install(self):
        ref = NS(get_appdata_version=lambda: '1', get_installed_size=lambda: 20,
                 get_origin=lambda: 'luma', get_commit=lambda: 'c' * 64, get_branch=lambda: 'beta')
        pending = dict(PENDING, changes=(), installed_commit=OLD)
        provider = native.NativeInstallation()
        with mock.patch.object(provider, '_channel_state', return_value=None), \
             mock.patch.object(provider, '_pending_updates', return_value={APP: pending}), \
             mock.patch.object(native, 'inventory', return_value=()), \
             mock.patch.object(native, 'installed_flatpak_refs', return_value={APP: (object(), ref)}), \
             mock.patch.object(native, 'local_permissions', return_value=()), \
             mock.patch.object(native, 'run_async', side_effect=lambda work, cb, _: cb(Result(value=work()))), \
             mock.patch.object(flatpak, 'APPLICATION_SOURCES', {'tide': APP}), \
             mock.patch.object(depot_catalog, 'local_catalog', return_value=NS(applications=(ENTRY,))):
            results = []
            provider.installed(results.append)
        items = autoupdate.pending_for(results[0].value)
        self.assertEqual(policy.plan(items, {'approved': [], 'notified': []}, enabled=True).install, (),
                         'cached permissions for A must not authorize target on unrelated installed C')

    def test_missing_snapshot_metadata_never_uses_unbound_fallback(self):
        ref = NS(get_name=lambda: APP, get_origin=lambda: 'luma', get_kind=lambda: 0,
                 get_arch=lambda: 'x86_64', get_branch=lambda: 'beta')
        remote = NS(get_metadata=lambda: None)
        install = NS(fetch_remote_ref_sync=lambda *_: remote,
                     fetch_remote_metadata_sync=mock.Mock(side_effect=AssertionError('unbound metadata used')))
        with mock.patch.object(flatpak, 'pending_updates', return_value=((install, ref),)), \
             mock.patch.object(native, '_installations', return_value=()), \
             mock.patch.object(depot_catalog, 'local_catalog', return_value=NS(applications=(ENTRY,))):
            self.assertEqual(native.NativeInstallation()._check_updates(), {})
        install.fetch_remote_metadata_sync.assert_not_called()

    def test_wrong_or_broken_snapshot_cannot_be_called_permission_free(self):
        ref = NS(get_name=lambda: APP, get_origin=lambda: 'luma', get_kind=lambda: 0,
                 get_arch=lambda: 'x86_64', get_branch=lambda: 'beta',
                 load_metadata=lambda _: GLib.Bytes.new(f'[Application]\nname={APP}\n'.encode()))
        for metadata in ('broken', '[Application]\nname=wrong.application\n'):
            remote = NS(get_metadata=lambda: GLib.Bytes.new(metadata.encode()), get_commit=lambda: NEW, get_download_size=lambda: 123)
            install = NS(fetch_remote_ref_sync=lambda *_: remote)
            with mock.patch.object(flatpak, 'pending_updates', return_value=((install, ref),)), \
                 mock.patch.object(native, '_installations', return_value=()), \
                 mock.patch.object(depot_catalog, 'local_catalog', return_value=NS(applications=(ENTRY,))):
                self.assertEqual(native.NativeInstallation()._check_updates(), {})


class BackgroundSnapshot(unittest.TestCase):
    def test_dispatch_uses_the_planned_target_and_installed_baseline(self):
        window = NS(jobs={}, _update=mock.Mock())
        runner = autoupdate.AppUpdateRun(NS(), window)
        runner.pending = {'catalog:tide': NS(app_id='catalog:tide', name='Tide', release='2', widens=False, commit=NEW, installed_commit=OLD)}
        runner.queue = ['catalog:tide']
        runner._next()
        window._update.assert_called_once_with('catalog:tide', approve=False, automatic=True,
                                               expected_commit=NEW, expected_installed_commit=OLD)

    def test_failed_or_cancelled_update_never_announces_success(self):
        runner = autoupdate.AppUpdateRun(NS(), NS())
        item = NS(app_id='catalog:tide', name='Tide', release='2', widens=False, commit=NEW, installed_commit=OLD)
        runner.pending = {item.app_id: item}
        runner.completed = []
        with mock.patch.object(GLib, 'idle_add'):
            for ok, cancelled in ((False, False), (True, True)):
                runner.current = item.app_id
                runner.update_finished(item.app_id, NS(ok=ok), cancelled)
                self.assertEqual(runner.completed, [])
            runner.current = item.app_id
            runner.update_finished(item.app_id, NS(ok=True), False)
        self.assertEqual(runner.completed, [item])


if __name__ == '__main__':
    unittest.main()
