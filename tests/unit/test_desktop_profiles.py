# SPDX-License-Identifier: Apache-2.0
"""Explicit preview capabilities may differ; full desktop selection must not."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('desktop_target', ROOT / 'scripts/os/lib/desktop_target.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

class DesktopProfiles(unittest.TestCase):
    def test_full_selection_unchanged(self):
        for arch in t.ARCHITECTURES:
            self.assertEqual(t.packages(ROOT, arch), t.packages(ROOT, arch, profile_name='full'))
            self.assertTrue(all(t.capabilities(ROOT, arch)['capabilities'].values()))

    def test_preview_excludes_only_declared_capability_providers(self):
        selection = t.profile(ROOT, 'aarch64', 'arm-preview')
        full = t.packages(ROOT, 'aarch64')
        preview = t.packages(ROOT, 'aarch64', profile_name='arm-preview')
        self.assertEqual(preview, [p for p in full if not t.excluded(t.package_name(p), selection)])
        names = {t.package_name(p) for p in preview}
        self.assertTrue({'luma-relay', 'luma-application-installer', 'luma-apk-metadata', 'prairie-core-apps'}.issubset(names))
        for name in ['luma-android-runtime', 'luma-android-images', 'wine-luma-relay-explorer', 'luma-cast', 'luma-cast-googlecast', 'luma-cast-backend-common', 'luma-cast-dlna', 'luma-cast-miracast']:
            self.assertNotIn(name, names)

    def test_preview_filters_fedora_packages_and_shared_roles(self):
        fedora = t.packages(ROOT, 'aarch64', fedora=True, profile_name='arm-preview')
        for name in ['waydroid', 'wine', 'ntsync-autoload', 'wine-dxvk', 'winetricks']:
            self.assertNotIn(name, fedora)
        self.assertIn('greenboot', fedora)
        roles = t.roles(ROOT, 'aarch64', 'arm-preview')
        self.assertNotIn('luma-android-runtime', roles)
        self.assertIn('luma-application-installer', roles)
        self.assertIn('prairie-core-apps', roles)

    def test_preview_is_explicit_and_arm_only(self):
        with self.assertRaises(ValueError):
            t.packages(ROOT, 'x86_64', profile_name='arm-preview')
        with self.assertRaises(ValueError):
            t.packages(ROOT, 'aarch64', profile_name='unknown')
        self.assertEqual(t.contract(ROOT, 'aarch64', 'arm-preview')['LUMA_OS_PROFILE'], 'arm-preview')

    def test_preflight_and_cli_reject_unsupported_profile(self):
        for args in [('--arch', 'x86_64', '--profile', 'arm-preview'), ('--arch', 'aarch64', '--profile', 'unknown')]:
            result = subprocess.run(['python3', 'scripts/os/preflight-desktop.py', *args, '--package-dir', str(ROOT)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
        env = {**os.environ, 'LUMA_OS_PROFILE': 'arm-preview'}
        result = subprocess.run(['python3', 'scripts/os/lib/desktop_target.py', '--arch', 'aarch64', 'roles'], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
        self.assertNotIn('luma-android-runtime', result.stdout.splitlines())

    def test_capabilities_describe_preview_honestly(self):
        self.assertEqual(t.capabilities(ROOT, 'aarch64', 'arm-preview')['capabilities'],
                         dict(desktop=True, android_apps=False, windows_apps=False, casting=False))

    def test_shell_environment_passes_profile_and_rejects_x86(self):
        env = {**os.environ, 'LUMA_TARGET_ARCHITECTURE': 'aarch64', 'LUMA_OS_PROFILE': 'arm-preview'}
        result = subprocess.run(['bash', '-ec', '. scripts/os/lib/common.sh; luma_os_desktop_packages'], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), t.packages(ROOT, 'aarch64', profile_name='arm-preview'))
        env['LUMA_TARGET_ARCHITECTURE'] = 'x86_64'
        self.assertNotEqual(subprocess.run(['bash', '-ec', '. scripts/os/lib/common.sh'], cwd=ROOT, env=env, capture_output=True).returncode, 0)

if __name__ == '__main__':
    unittest.main()
