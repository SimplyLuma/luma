# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('desktop_scope', ROOT / 'scripts/os/desktop-ci-changed.py')
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class NativeProviderScope(unittest.TestCase):
    def test_architecture_specific_engine_inputs_only_rebuild_their_cpu(self):
        for release, owner in ((30, 'x86_64'), (31, 'aarch64')):
            path = f'src/external/viola/chromium-linux/luma-package/native-{release}-engine.sha256'
            for architecture in ('x86_64', 'aarch64'):
                self.assertEqual(scope.affected('browser', [path], architecture),
                                 architecture == owner)

    def test_shared_host_changes_rebuild_both_cpus(self):
        path = 'src/external/viola/chromium-linux/appkit-spike/viola_host.py'
        for architecture in ('x86_64', 'aarch64'):
            self.assertTrue(scope.affected('browser', [path], architecture))

    def test_single_cpu_matrix_does_not_add_the_other_cpu(self):
        rows = scope.matrix(['aarch64'])['include']
        self.assertEqual([row['architecture'] for row in rows], ['aarch64'])
        self.assertEqual(rows[0]['runner'], 'ubuntu-24.04-arm')
        self.assertRegex(rows[0]['image'], r'@sha256:[a-f0-9]{64}$')

    def test_browser_changes_preserve_the_running_webkit_build(self):
        self.assertFalse(scope.affected('webkit', ['scripts/os/build-desktop-browser.sh']))
        self.assertTrue(scope.affected('browser', ['scripts/os/build-desktop-browser.sh']))

    def test_upstream_engine_change_rebuilds_webkit(self):
        self.assertTrue(scope.affected('webkit', ['config/shared/webkitgtk-inputs.env']))
        self.assertTrue(scope.affected('webkit', ['patches/webkitgtk/0001-change.patch']))

    def test_provider_build_metadata_does_not_recompile_product(self):
        for provider in ('webkit', 'browser'):
            self.assertFalse(scope.affected(provider, ['.github/workflows/desktop-webkit.yml',
                             'scripts/os/desktop-ci-changed.py', 'README.md']))

    def test_shared_build_inputs_recheck_both_providers(self):
        for provider in ('webkit', 'browser'):
            self.assertTrue(scope.affected(provider, ['config/desktop/inputs.env']))
            self.assertTrue(scope.affected(provider, ['scripts/packages/run-in-rpm-builder.sh']))
