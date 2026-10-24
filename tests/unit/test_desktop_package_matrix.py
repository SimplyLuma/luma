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
