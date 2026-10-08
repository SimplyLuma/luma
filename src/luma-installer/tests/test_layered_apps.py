"""Externally installed native RPM ownership and privileged removal boundaries."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from luma_installer import layered_apps, system_helper, manager, removal
from luma_installer.depot_system_apps import Deployment, SystemView

class LayeredApps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.apps = self.root / 'applications'; self.apps.mkdir()
        self.base = self.root / 'base'; self.base.mkdir()
        self.desktop, self.package = 'firefox.desktop', 'firefox'
        self.write_launcher(); self.pending_removed = False; self.calls = []
        self.view = SystemView(Deployment(booted=True, layered=frozenset({'firefox'})))
    def write_launcher(self):
        (self.apps / self.desktop).write_text('[Desktop Entry]\nType=Application\nName=Owned test app\nExec=true\n')
    def runner(self, argv, **kwargs):
        self.calls.append(argv); rc, out = 0, ''
        if '-qf' in argv: out = self.package + '\n'
        elif '--dbpath' in argv: rc = 1
        elif argv[1] == 'status':
            rows = []
            if self.view.booted: rows.append({'booted': True, 'requested-packages': list(self.view.booted.layered)})
            if self.pending_removed: rows.insert(0, {'staged': True, 'requested-packages': []})
            if self.view.pending: rows.insert(0, {'staged': True, 'requested-packages': list(self.view.pending.layered)})
            out = json.dumps({'deployments': rows, 'transaction': self.view.transaction})
        elif argv[1] == 'uninstall': self.pending_removed = True
        elif argv[1] == '-q': rc = 1
        return subprocess.CompletedProcess(argv, rc, out, '')
    def resolve(self, expected=None, runner=None):
        original = Path.lstat
        def metadata(path):
            from types import SimpleNamespace
            value = original(path)
            return SimpleNamespace(st_uid=0, st_mode=value.st_mode)
        with patch.object(Path, 'lstat', metadata):
            return layered_apps.identity(self.desktop, expected, runner=runner or self.runner,
                                         applications=self.apps, base_db=self.base)
    def test_firefox_and_gimp_are_external_rpm_owners(self):
        for package, desktop in [('firefox', 'firefox.desktop'), ('gimp', 'org.gimp.GIMP.desktop')]:
            with self.subTest(package=package):
                self.package, self.desktop = package, desktop; self.write_launcher()
                self.view = SystemView(Deployment(booted=True, layered=frozenset({package})))
                self.assertEqual(self.resolve(package), package)
    def test_base_unknown_staged_removed_transaction_and_essential_refuse(self):
        for view in [SystemView(None), SystemView(Deployment(booted=True)),
                     SystemView(Deployment(booted=True, layered=frozenset({'firefox'})), transaction=True),
                     SystemView(Deployment(booted=True, layered=frozenset({'firefox'})), Deployment())]:
            self.view = view
            with self.assertRaises(ValueError): self.resolve()
        self.package = 'gnome-shell'
        with self.assertRaisesRegex(ValueError, 'needs'): self.resolve()
    def test_live_same_base_deployment_can_own_added_running_package(self):
        self.view = SystemView(Deployment(booted=True, checksum='base'),
                               Deployment(checksum='layer', base_checksum='base',
                                          layered=frozenset({'firefox'})))
        # Use the exact same-base status shape produced by rpm-ostree apply-live.
        def runner(argv, **kwargs):
            if argv[1] == 'status':
                return subprocess.CompletedProcess(argv, 0, json.dumps({'deployments': [
                    {'staged': True, 'checksum': 'layer', 'base-checksum': 'base', 'requested-packages': ['firefox']},
                    {'booted': True, 'checksum': 'base'}]}), '')
            return self.runner(argv, **kwargs)
        self.assertEqual(self.resolve(runner=runner), 'firefox')
        def other_base(argv, **kwargs):
            result = runner(argv, **kwargs)
            if argv[1] == 'status': result.stdout = result.stdout.replace('"base-checksum": "base"', '"base-checksum": "different"')
            return result
        with self.assertRaises(ValueError): self.resolve(runner=other_base)
    def test_booted_or_pending_replaced_packages_refuse(self):
        replacement = [[['firefox-2-1.x86_64', 'firefox'], ['firefox-1-1.x86_64', 'firefox']]]
        for position in (0, 1):
            rows = [{'staged': True, 'checksum': 'layer', 'base-checksum': 'base', 'requested-packages': ['firefox']},
                    {'booted': True, 'checksum': 'base', 'requested-packages': ['firefox']}]
            rows[position]['base-local-replacements'] = replacement
            def runner(argv, **kwargs):
                if argv[1] == 'status': return subprocess.CompletedProcess(argv, 0, json.dumps({'deployments': rows}), '')
                return self.runner(argv, **kwargs)
            with self.subTest(position=position), self.assertRaises(ValueError): self.resolve(runner=runner)
    def test_base_database_query_failure_or_base_membership_refuses(self):
        self.base.rmdir()
        with self.assertRaisesRegex(ValueError, 'database'): self.resolve()
        self.base.mkdir()
        for code in [0, 2]:
            def runner(argv, **kwargs):
                if '--dbpath' in argv: return subprocess.CompletedProcess(argv, code, '', '')
                return self.runner(argv, **kwargs)
            with self.assertRaisesRegex(ValueError, 'system image'): self.resolve(runner=runner)
    def test_malicious_name_shadow_symlink_and_owner_change_refuse(self):
        with self.assertRaises(ValueError): layered_apps.identity('../../tmp/test.desktop')
        with self.assertRaisesRegex(ValueError, 'changed'): self.resolve('gimp')
        path = self.apps / self.desktop; path.chmod(0o666)
        with self.assertRaisesRegex(ValueError, 'owned'): self.resolve()
        path.unlink(); path.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'owned'): self.resolve()
    def test_root_rechecks_before_transaction_and_keeps_receipt_for_restart(self):
        with patch.object(layered_apps, 'identity', side_effect=ValueError('changed')), \
             patch.object(system_helper, 'SYSTEM_STATE_ROOT', self.root / 'state'):
            with self.assertRaisesRegex(ValueError, 'changed'):
                layered_apps.remove(self.desktop, self.package, runner=self.runner)
        self.assertFalse(any('uninstall' in args for args in self.calls))
        with patch.object(layered_apps, 'identity', return_value='firefox'), \
             patch.object(system_helper, 'SYSTEM_STATE_ROOT', self.root / 'state'), \
             patch.object(system_helper, 'apply_live', return_value=False):
            self.assertTrue(layered_apps.remove(self.desktop, self.package, runner=self.runner))
        receipt = json.loads(next((self.root / 'state/receipts').glob('*.json')).read_text())
        self.assertEqual(receipt['state'], 'removal-pending-restart')
        self.assertEqual(receipt['package_name'], 'firefox')
        self.assertIn(['/usr/bin/rpm-ostree', 'uninstall', 'firefox'], self.calls)
    def test_success_readback_failure_keeps_recovery_receipt(self):
        def no_change(argv, **kwargs):
            if argv[1] == 'uninstall': return subprocess.CompletedProcess(argv, 0, '', '')
            return self.runner(argv, **kwargs)
        with patch.object(layered_apps, 'identity', return_value='firefox'), \
             patch.object(system_helper, 'SYSTEM_STATE_ROOT', self.root / 'state'), \
             patch.object(system_helper, 'apply_live') as apply:
            with self.assertRaisesRegex(RuntimeError, 'did not stage'):
                layered_apps.remove(self.desktop, self.package, runner=no_change)
            apply.assert_not_called()
        self.assertTrue(list((self.root / 'state/receipts').glob('*.json')))
    def test_interrupted_transaction_keeps_receipt_for_revalidated_retry(self):
        def failed(argv, **kwargs):
            if argv[1] == 'uninstall': return subprocess.CompletedProcess(argv, 1, '', 'interrupted')
            return self.runner(argv, **kwargs)
        with patch.object(layered_apps, 'identity', return_value='firefox'), \
             patch.object(system_helper, 'SYSTEM_STATE_ROOT', self.root / 'state'), \
             patch.object(system_helper, 'apply_live') as apply:
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                layered_apps.remove(self.desktop, self.package, runner=failed)
            apply.assert_not_called()
        self.assertEqual(json.loads(next((self.root / 'state/receipts').glob('*.json')).read_text())['state'], 'removing')
    def test_live_absence_removes_receipt_but_keeps_user_data(self):
        data = self.root / 'personal'; data.write_text('retained')
        with patch.object(layered_apps, 'identity', return_value='firefox'), \
             patch.object(system_helper, 'SYSTEM_STATE_ROOT', self.root / 'state'), \
             patch.object(system_helper, 'apply_live', return_value=True):
            self.assertFalse(layered_apps.remove(self.desktop, self.package, runner=self.runner))
        self.assertFalse(list((self.root / 'state/receipts').glob('*.json')))
        self.assertEqual(data.read_text(), 'retained')
    def test_adopted_native_rpm_never_offers_to_delete_unowned_data(self):
        view = removal.removal_view({'format': 'rpm', 'external_layered': True, 'package_name': 'gimp'}, 'GIMP', keep=False)
        self.assertEqual(view.action, 'Uninstall'); self.assertFalse(view.show_keep); self.assertTrue(view.keep)
    def test_manager_passes_only_identity_to_authenticated_owner(self):
        record = {'application_id': 'layered-rpm-gimp', 'format': 'rpm', 'external_layered': True,
                  'desktop_id': 'org.gimp.GIMP.desktop', 'package_name': 'gimp'}
        with patch.object(manager, '_record', return_value=record), patch.object(manager, '_run') as run, \
             patch.object(manager.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')), \
             patch.object(manager, 'remove_record') as forget, patch.object(manager, '_safe_remove_tree') as files:
            self.assertIn('retained', manager.remove(record['application_id'], delete_data=True))
            run.assert_called_once_with(['pkexec', '/usr/libexec/luma-installer-system', 'remove-layered', 'org.gimp.GIMP.desktop', 'gimp'])
            forget.assert_called_once(); files.assert_not_called()
    def test_polkit_boundary_refuses_nonroot(self):
        with patch.object(system_helper.os, 'geteuid', return_value=1000), patch.object(layered_apps, 'remove') as mutate:
            self.assertEqual(system_helper.main(['remove-layered', self.desktop, self.package]), 1)
            mutate.assert_not_called()
    def test_adoption_receipt_cannot_bypass_current_owner_revalidation(self):
        with patch.object(system_helper.subprocess, 'run') as mutate:
            with self.assertRaisesRegex(ValueError, 'Review'):
                system_helper.manage_backend('remove', {'format': 'rpm', 'package_name': 'firefox', 'external_layered': True})
            mutate.assert_not_called()
if __name__ == '__main__': unittest.main()
