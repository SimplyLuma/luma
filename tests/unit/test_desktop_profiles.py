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
            capabilities = t.capabilities(ROOT, arch)['capabilities']
            # Phones are ARM-only: full is every desktop capability, without the phone stack.
            self.assertFalse(capabilities.pop('phone'))
            self.assertTrue(all(capabilities.values()))

    def test_preview_excludes_only_declared_capability_providers(self):
        selection = t.profile(ROOT, 'aarch64', 'arm-preview')
        full = t.packages(ROOT, 'aarch64')
        preview = t.packages(ROOT, 'aarch64', profile_name='arm-preview')
        self.assertEqual(preview, [p for p in full if not t.excluded(t.package_name(p), selection)])
        names = {t.package_name(p) for p in preview}
        self.assertTrue({'luma-relay', 'luma-application-installer', 'luma-apk-metadata', 'prairie-core-apps'}.issubset(names))
        self.assertTrue({'luma-android-runtime', 'luma-android-images'}.issubset(names))
        for name in ['wine-luma-relay-explorer', 'luma-cast', 'luma-cast-googlecast', 'luma-cast-backend-common', 'luma-cast-dlna', 'luma-cast-miracast']:
            self.assertNotIn(name, names)

    def test_preview_filters_fedora_packages_and_shared_roles(self):
        fedora = t.packages(ROOT, 'aarch64', fedora=True, profile_name='arm-preview')
        self.assertIn('waydroid', fedora)
        for name in ['wine', 'ntsync-autoload', 'wine-dxvk', 'winetricks']:
            self.assertNotIn(name, fedora)
        self.assertIn('greenboot', fedora)
        roles = t.roles(ROOT, 'aarch64', 'arm-preview')
        self.assertIn('luma-android-runtime', roles)
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
        self.assertIn('luma-android-runtime', result.stdout.splitlines())

    def test_phone_capability_adds_the_phone_stack_on_arm_only(self):
        phone = t.lines(ROOT / 'config/os/phone-packages.txt')
        self.assertTrue(phone)
        arm = t.packages(ROOT, 'aarch64', fedora=True, profile_name='arm-preview')
        self.assertTrue(set(phone) <= set(arm))
        for arch in t.ARCHITECTURES:
            self.assertFalse(set(phone) & set(t.packages(ROOT, arch, fedora=True)))

    def test_capabilities_describe_preview_honestly(self):
        self.assertEqual(t.capabilities(ROOT, 'aarch64', 'arm-preview')['capabilities'],
                         dict(desktop=True, android_apps=True, windows_apps=False, casting=False, phone=True))

    def test_shell_environment_passes_profile_and_rejects_x86(self):
        env = {**os.environ, 'LUMA_TARGET_ARCHITECTURE': 'aarch64', 'LUMA_OS_PROFILE': 'arm-preview'}
        result = subprocess.run(['bash', '-ec', '. scripts/os/lib/common.sh; luma_os_desktop_packages'], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), t.packages(ROOT, 'aarch64', profile_name='arm-preview'))
        env['LUMA_TARGET_ARCHITECTURE'] = 'x86_64'
        self.assertNotEqual(subprocess.run(['bash', '-ec', '. scripts/os/lib/common.sh'], cwd=ROOT, env=env, capture_output=True).returncode, 0)

    def test_hardware_target_adds_only_its_packages_and_kernel(self):
        generic = t.packages(ROOT, 'aarch64', profile_name='arm-preview')
        pi = t.packages(ROOT, 'aarch64', profile_name='arm-preview', hardware_target='raspberrypi,rpi5')
        device = t.hardware(ROOT, 'aarch64', 'raspberrypi,rpi5')
        added = {t.package_name(p) for p in pi} - {t.package_name(p) for p in generic}
        self.assertEqual(added, set(device['packages']))
        self.assertTrue(set(generic) < set(pi))
        self.assertEqual(device['kernel'], 'kernel-luma-rpi')
        for arch in t.ARCHITECTURES:
            names = {t.package_name(p) for p in t.packages(ROOT, arch)}
            self.assertFalse(names & t.device_package_names(ROOT), arch)

    def test_hardware_target_has_its_own_ref_and_architecture(self):
        values = t.contract(ROOT, 'aarch64', 'arm-preview', 'raspberrypi,rpi5')
        self.assertEqual(values['LUMA_OS_REF_PREFIX'], 'luma/1/aarch64/raspberrypi-rpi5')
        self.assertEqual(values['LUMA_OS_HARDWARE_TARGET'], 'raspberrypi,rpi5')
        self.assertEqual(values['LUMA_OS_KERNEL_PACKAGE'], 'kernel-luma-rpi')
        self.assertNotIn('LUMA_OS_HARDWARE_TARGET', t.contract(ROOT, 'aarch64', 'arm-preview'))
        for arch, target in (('x86_64', 'raspberrypi,rpi5'), ('aarch64', 'fairphone,fp6'), ('aarch64', 'unknown')):
            with self.subTest(arch=arch, target=target), self.assertRaises(ValueError):
                t.packages(ROOT, arch, hardware_target=target)

    def test_shell_environment_passes_the_hardware_target(self):
        env = {**os.environ, 'LUMA_TARGET_ARCHITECTURE': 'aarch64', 'LUMA_OS_PROFILE': 'arm-preview',
               'LUMA_OS_HARDWARE_TARGET': 'raspberrypi,rpi5'}
        result = subprocess.run(['bash', '-ec', '. scripts/os/lib/common.sh; echo "$LUMA_OS_REF_PREFIX"; luma_os_desktop_packages'],
                                cwd=ROOT, env=env, text=True, capture_output=True, check=True)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], 'luma/1/aarch64/raspberrypi-rpi5')
        self.assertEqual(lines[1:], t.packages(ROOT, 'aarch64', profile_name='arm-preview', hardware_target='raspberrypi,rpi5'))

if __name__ == '__main__':
    unittest.main()
