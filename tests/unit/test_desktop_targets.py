# SPDX-License-Identifier: Apache-2.0
"""Architecture selection must preserve releases and isolate update identities."""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('desktop_target', ROOT / 'scripts/os/lib/desktop_target.py')
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)


class DesktopTargets(unittest.TestCase):
    def test_existing_x86_selection_is_unchanged(self):
        self.assertEqual(target.packages(ROOT, 'x86_64'), target.lines(ROOT / 'config/desktop/packages.txt'))
        self.assertEqual(target.packages(ROOT, 'x86_64', fedora=True), target.lines(ROOT / 'config/os/fedora-packages.txt'))
        old = dict(line.split('=', 1) for line in target.lines(ROOT / 'config/os/release.env'))
        # New installations use the arch URL; every other x86 contract field stays identical.
        old['LUMA_OS_GRAPH_PREFIX'] += '/x86_64'
        self.assertEqual(target.contract(ROOT, 'x86_64'), old)

    def test_arm_uses_same_releases_and_noarch_packages(self):
        original = target.packages(ROOT, 'x86_64')
        actual = target.packages(ROOT, 'aarch64')
        self.assertEqual(actual, [p.removesuffix('.x86_64') + '.aarch64' if p.endswith('.x86_64') else p
                                  for p in original if target.package_name(p) != 'intel-crashlog'])
        self.assertFalse(any(p.endswith('.x86_64') for p in actual))
        self.assertFalse(any('surfacepreview' in p for p in actual))

    def test_arm_fedora_exact_pins_follow_target(self):
        values = target.packages(ROOT, 'aarch64', fedora=True)
        self.assertIn('gstreamer1-1.28.7-1.fc44.aarch64', values)
        self.assertIn('greenboot', values)

    def test_arm_update_ref_and_graph_are_isolated(self):
        values = target.contract(ROOT, 'aarch64')
        self.assertEqual(values['LUMA_OS_REF_PREFIX'], 'luma/1/aarch64')
        self.assertEqual(values['LUMA_OS_GRAPH_PREFIX'], 'os/graph/aarch64')
        self.assertIn('arch=aarch64', values['LUMA_OS_HUB_GRAPH_URL_TEMPLATE'])
        self.assertTrue(values['LUMA_OS_BASE_IMAGE_TAG'].endswith('-aarch64'))
        self.assertEqual(values['LUMA_OS_RELEASE_KEY_FINGERPRINT'], target.contract(ROOT, 'x86_64')['LUMA_OS_RELEASE_KEY_FINGERPRINT'])

    def test_shell_consumers_receive_same_target(self):
        command = '. scripts/os/lib/common.sh; luma_os_select_architecture aarch64; luma_os_channel_ref nightly; luma_os_desktop_packages'
        result = subprocess.run(['bash', '-ec', command], cwd=ROOT, text=True, capture_output=True, check=True)
        values = result.stdout.splitlines()
        self.assertEqual(values[0], 'luma/1/aarch64/nightly')
        self.assertEqual(values[1:], target.packages(ROOT, 'aarch64'))

    def test_unknown_architecture_fails_before_build(self):
        with self.assertRaises(ValueError):
            target.packages(ROOT, 'armv7l')
        result = subprocess.run(['bash', '-ec', '. scripts/os/lib/common.sh; luma_os_select_architecture amd64'], cwd=ROOT, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_base_architecture_or_digest_is_rejected(self):
        registry = json.loads((ROOT / 'config/os/desktop-targets.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config/os').mkdir(parents=True)
            path = root / 'config/os/desktop-targets.json'
            registry['architectures']['aarch64']['base_tag'] = '44.20261022.0-x86_64'
            path.write_text(json.dumps(registry))
            with self.assertRaises(ValueError):
                target.target(root, 'aarch64')
            registry['architectures']['aarch64']['base_tag'] = '44.20261022.0-aarch64'
            registry['architectures']['aarch64']['base_digest'] = 'latest'
            path.write_text(json.dumps(registry))
            with self.assertRaises(ValueError):
                target.target(root, 'aarch64')

    def test_runtime_requires_current_desktop_and_owner_webkit_for_both_arches(self):
        def requirements(architecture):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'must-not-be-created'
                result = subprocess.run(
                    ['bash', 'scripts/depot/collect-luma-rpms.sh', '--print-required'],
                    cwd=ROOT, env={**os.environ, 'DEPOT_ARCH': architecture, 'DEPOT_ROOT': str(output)},
                    capture_output=True, text=True, check=True)
                self.assertFalse(output.exists())
                return result.stdout.splitlines()
        x86, arm = requirements('x86_64'), requirements('aarch64')
        self.assertEqual(arm, [p.removesuffix('.x86_64') + '.aarch64' if p.endswith('.x86_64') else p for p in x86])
        desktop = set(target.packages(ROOT, 'aarch64'))
        for name in ('gtk4-', 'libadwaita-', 'luma-developer-platform-'):
            main = next(p for p in arm if p.startswith(name) and '-devel-' not in p)
            self.assertIn(main, desktop)
        for name in ('webkitgtk6.0', 'webkit2gtk4.1', 'javascriptcoregtk6.0', 'javascriptcoregtk4.1'):
            self.assertTrue(any(p.startswith(name + '-') and '-devel-' not in p for p in arm))
            self.assertTrue(any(p.startswith(name + '-devel-') for p in arm))
