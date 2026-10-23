# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('desktop_scope', ROOT / 'scripts/os/desktop-ci-changed.py')
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class NativeProviderScope(unittest.TestCase):
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
