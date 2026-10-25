# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('desktop_matrix', ROOT / 'scripts/os/desktop-package-matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


class NativeMatrix(unittest.TestCase):
    def test_documentation_does_not_rebuild_native_packages(self):
        self.assertEqual(matrix.matrix(ROOT, {'README.md'})['include'], [])

    def test_component_change_always_checks_both_architectures(self):
        rows = matrix.matrix(ROOT, {'patches/fwupd/0001-fix.patch'})['include']
        self.assertEqual({row['architecture'] for row in rows}, {'x86_64', 'aarch64'})
        self.assertEqual({row['component'] for row in rows}, {'fwupd'})
        self.assertEqual(len(rows), 2)

    def test_shared_builder_change_checks_all_groups(self):
        rows = matrix.matrix(ROOT, {'scripts/packages/run-in-rpm-builder.sh'})['include']
        self.assertEqual(rows, matrix.matrix(ROOT)['include'])
        self.assertGreater(len(rows), 2)
        for row in rows:
            self.assertTrue((ROOT / row['recipe']).is_file())
            self.assertIn('@sha256:', row['image'])
            self.assertEqual(row['runner'].endswith('-arm'), row['architecture'] == 'aarch64')

    def test_adding_a_group_does_not_rebuild_unchanged_groups(self):
        previous = json.loads((ROOT / matrix.REGISTRY).read_text())
        previous['groups'].pop('apk-metadata')
        rows = matrix.matrix(ROOT, {matrix.REGISTRY}, previous)['include']
        self.assertEqual({row['component'] for row in rows}, {'apk-metadata'})
        self.assertEqual({row['architecture'] for row in rows}, {'x86_64', 'aarch64'})

    def test_changing_a_group_rechecks_only_that_group(self):
        previous = json.loads((ROOT / matrix.REGISTRY).read_text())
        previous['groups']['fwupd']['paths'] = []
        rows = matrix.matrix(ROOT, {matrix.REGISTRY}, previous)['include']
        self.assertEqual({row['component'] for row in rows}, {'fwupd'})
        self.assertEqual(len(rows), 2)

    def test_unknown_previous_registry_checks_all_groups(self):
        self.assertEqual(matrix.matrix(ROOT, {matrix.REGISTRY}), matrix.matrix(ROOT))

    def test_installer_is_canonical_noarch_and_uses_checked_toolkit(self):
        rows = matrix.matrix(ROOT, {'src/luma-installer/luma_installer/retired_apps.py'})['include']
        self.assertEqual([(row['component'], row['architecture']) for row in rows],
                         [('application-installer', 'x86_64')])
        self.assertTrue(rows[0]['toolkit'])
        self.assertEqual(rows[0]['spec'], 'packaging/rpm/luma-application-installer.spec')

    def test_ari_and_policy_keep_both_native_architectures(self):
        for path, group, toolkit in (
                ('src/luma-ari/ari/service.py', 'ari', True),
                ('src/luma-desktop-launcher-policy/default-apps/file', 'desktop-launcher-policy', False)):
            with self.subTest(group=group):
                rows = matrix.matrix(ROOT, {path})['include']
                self.assertEqual({row['component'] for row in rows}, {group})
                self.assertEqual({row['architecture'] for row in rows}, {'x86_64', 'aarch64'})
                self.assertEqual({row['toolkit'] for row in rows}, {toolkit})
                self.assertTrue(all(row['spec'] for row in rows))

    def test_installer_includes_bundled_depot_and_catalog_fixture_changes(self):
        for path in ('src/luma-depot/luma_depot/window.py', 'tests/fixtures/depot-v70.json'):
            rows = matrix.matrix(ROOT, {path})['include']
            self.assertEqual({row['component'] for row in rows}, {'application-installer'})

    def test_invalid_or_duplicate_architecture_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / matrix.REGISTRY).parent.mkdir(parents=True)
            (root / 'config/desktop').mkdir()
            (root / 'config/desktop/inputs.env').write_text((ROOT / 'config/desktop/inputs.env').read_text())
            for architectures in ([], ['sparc'], ['x86_64', 'x86_64'], 'x86_64'):
                registry = {'schema': 'org.projectluma.desktop-build-groups/v1', 'groups': {
                    'invalid': {'recipe': 'unused', 'paths': [], 'architectures': architectures}}}
                (root / matrix.REGISTRY).write_text(json.dumps(registry))
                with self.subTest(architectures=architectures), self.assertRaises(ValueError):
                    matrix.matrix(root)

    def test_new_noarch_cohort_is_five_native_jobs(self):
        previous = json.loads((ROOT / matrix.REGISTRY).read_text())
        for group in ('application-installer', 'ari', 'desktop-launcher-policy'):
            previous['groups'].pop(group)
        rows = matrix.matrix(ROOT, {matrix.REGISTRY}, previous)['include']
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(row['toolkit'] for row in rows), 3)
