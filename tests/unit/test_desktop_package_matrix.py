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

    def test_noarch_checker_changes_only_rebuild_its_consumers(self):
        rows = matrix.matrix(ROOT, {'scripts/os/check-native-noarch-package.py'})['include']
        self.assertEqual(len(rows), 8)
        self.assertEqual({row['component'] for row in rows},
                         {'application-installer', 'ari', 'desktop-launcher-policy', 'shell-state', 'icon-theme', 'android-runtime'})
        self.assertTrue(all(row['spec'] for row in rows))

    def test_missing_noarch_cohort_addition_checks_only_two_canonical_jobs(self):
        previous = json.loads((ROOT / matrix.REGISTRY).read_text())
        for group in ('shell-state', 'icon-theme'):
            previous['groups'].pop(group)
        rows = matrix.matrix(ROOT, {matrix.REGISTRY}, previous)['include']
        self.assertEqual([(row['component'], row['architecture']) for row in rows],
                         [('shell-state', 'x86_64'), ('icon-theme', 'x86_64')])
        self.assertTrue(all(row['spec'] and not row['toolkit'] for row in rows))

    def test_theme_and_shell_state_source_changes_select_canonical_noarch(self):
        for path, group in (
                ('src/luma-shell-state/gtk.css', 'shell-state'),
                ('assets/icon-theme/Prairie/index.theme', 'icon-theme'),
                ('packaging/gsettings/90-prairie.gschema.override', 'icon-theme')):
            rows = matrix.matrix(ROOT, {path})['include']
            self.assertEqual([(row['component'], row['architecture']) for row in rows],
                             [(group, 'x86_64')])

    def test_paths_only_addition_preserves_all_execution_bytes(self):
        current = (ROOT / matrix.WORKFLOW).read_text()
        additions = ["      - 'src/luma-shell-state/**'\n",
                     "      - 'assets/icon-theme/**'\n",
                     "      - 'packaging/gsettings/90-prairie.gschema.override'\n",
                     "      - 'packaging/rpm/luma-shell-state.spec'\n",
                     "      - 'packaging/rpm/prairie-icon-theme.spec'\n"]
        previous = current
        for line in additions:
            previous = previous.replace(line, '')
        self.assertTrue(matrix.routing_only_workflow_additions(previous, current))
        old_registry = json.loads((ROOT / matrix.REGISTRY).read_text())
        for group in ('shell-state', 'icon-theme'):
            old_registry['groups'].pop(group)
        rows = matrix.matrix(ROOT, {matrix.WORKFLOW, matrix.REGISTRY}, old_registry,
                             previous)['include']
        self.assertEqual({row['component'] for row in rows}, {'shell-state', 'icon-theme'})
        checked = matrix.matrix(ROOT, {matrix.WORKFLOW, 'scripts/os/check-native-noarch-package.py'},
                                previous_workflow=previous)['include']
        self.assertEqual(len(checked), 8)

    def test_unknown_or_nonrouting_workflow_changes_still_rebuild_every_group(self):
        import tempfile
        import shutil
        current = (ROOT / matrix.WORKFLOW).read_text()
        previous = current.replace("      - 'assets/icon-theme/**'\n", '')
        variants = [current.replace('contents: read', 'contents: write'),
                    current.replace("RPM_BUILD_NCPUS: '2'", "RPM_BUILD_NCPUS: '4'"),
                    current.replace('max-parallel: 4', 'max-parallel: 3'),
                    current.replace("      - 'assets/icon-theme/**'", '      - "assets/icon-theme/**"'),
                    current.replace("      - 'patches/**'\n", ''),
                    current.replace('if-no-files-found: error', 'if-no-files-found: warn')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in (matrix.REGISTRY, 'config/desktop/inputs.env', matrix.WORKFLOW):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / path, target)
            all_rows = matrix.matrix(root)['include']
            for changed in variants:
                with self.subTest(change=changed):
                    (root / matrix.WORKFLOW).write_text(changed)
                    self.assertFalse(matrix.routing_only_workflow_additions(previous, changed))
                    self.assertEqual(matrix.matrix(root, {matrix.WORKFLOW},
                                                   previous_workflow=previous)['include'], all_rows)
        self.assertFalse(matrix.routing_only_workflow_additions(None, current))
        self.assertFalse(matrix.routing_only_workflow_additions(current, previous))
        self.assertFalse(matrix.routing_only_workflow_additions(current, current))

    def test_routing_exception_requires_the_top_level_event_block(self):
        current = (ROOT / matrix.WORKFLOW).read_text()
        previous = current.replace("      - 'assets/icon-theme/**'\n", '')
        for replacement in ('inputs:', 'xon:', '  on:'):
            with self.subTest(replacement=replacement):
                self.assertFalse(matrix.routing_only_workflow_additions(
                    previous.replace('\non:\n', '\n' + replacement + '\n'),
                    current.replace('\non:\n', '\n' + replacement + '\n')))

    def test_exact_pr_base_correction_preserves_execution_contract(self):
        current = (ROOT / matrix.WORKFLOW).read_text()
        previous = current.replace('github.event.pull_request.base.sha || github.event.before',
                                   'github.event.before || github.event.pull_request.base.sha')
        self.assertTrue(matrix.selection_base_only_workflow_change(previous, current))
        self.assertEqual(matrix.matrix(ROOT, {matrix.WORKFLOW}, previous_workflow=previous)['include'], [])
        self.assertFalse(matrix.selection_base_only_workflow_change(
            previous, current.replace('timeout-minutes: 60', 'timeout-minutes: 1')))
        self.assertEqual(matrix.matrix(ROOT, {matrix.WORKFLOW}), matrix.matrix(ROOT))

    def test_known_pin_changes_check_their_consumers(self):
        values = dict(line.split('=', 1) for line in (ROOT / 'config/desktop/inputs.env').read_text().splitlines()
                      if line and not line.startswith('#') and '=' in line)
        for key, expected in [
                ('GNOME_SHELL_LUMA_RELEASE', set()),
                ('PRAIRIE_ICON_THEME_RELEASE', {'icon-theme', 'ari'}),
                ('LUMA_APPLICATION_INSTALLER_RELEASE', {'application-installer', 'ari'}),
                ('LUMA_DEVELOPER_PLATFORM_RELEASE', {'application-installer', 'ari'})]:
            previous = {**values, key: 'prior'}
            rows = matrix.matrix(ROOT, {'config/desktop/inputs.env'}, previous_inputs=previous)['include']
            with self.subTest(key=key):
                self.assertEqual({row['component'] for row in rows}, expected)
        previous = {**values, 'UNKNOWN_PROVIDER': 'old'}
        self.assertEqual(matrix.matrix(ROOT, {'config/desktop/inputs.env'}, previous_inputs=previous), matrix.matrix(ROOT))
        # A shared builder change still checks every group alongside a narrow pin change.
        previous = {**values, 'GNOME_SHELL_LUMA_RELEASE': 'prior'}
        self.assertEqual(matrix.matrix(ROOT, {'config/desktop/inputs.env', 'scripts/packages/run-in-rpm-builder.sh'},
                                      previous_inputs=previous), matrix.matrix(ROOT))
