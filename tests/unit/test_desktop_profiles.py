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

    def test_pi_userspace_is_in_every_arm_image_and_its_kernel_in_none(self):
        arm = {t.package_name(p) for p in t.packages(ROOT, 'aarch64', profile_name='arm-preview')}
        x86 = {t.package_name(p) for p in t.packages(ROOT, 'x86_64')}
        pi = {'luma-rpi5-platform', 'luma-rpi-bluetooth-firmware',
              'gstreamer1-plugins-bad-free', 'gstreamer1-plugins-bad-free-libs'}
        self.assertTrue(pi <= arm, pi - arm)
        self.assertFalse(pi & x86)
        for names in (arm, x86):
            self.assertNotIn('kernel-luma-rpi', names)
        self.assertIn('kernel-luma-rpi', t.device_package_names(ROOT))

    def test_pi_kernel_is_a_device_repository_package_not_a_target(self):
        row, pins = t.device_repository(ROOT, 'raspberrypi-rpi5')
        self.assertEqual(row['architecture'], 'aarch64')
        self.assertEqual(row['url'], 'https://dl.simplyluma.com/os/devices/aarch64/raspberrypi-rpi5/')
        self.assertEqual([t.package_name(p) for p in pins], ['kernel-luma-rpi'])
        self.assertTrue(pins[0].endswith('.aarch64'))
        with self.assertRaises(ValueError):
            t.hardware(ROOT, 'aarch64', 'raspberrypi,rpi5')
        devices = (ROOT / 'scripts/os/lib/devices.tsv').read_text()
        self.assertNotIn('raspberrypi,rpi5\t', devices)
        result = subprocess.run(['python3', '-I', str(ROOT / 'scripts/os/lib/desktop_target.py'), '--arch', 'aarch64',
                                 '--device-repository', 'raspberrypi-rpi5', 'device-repository'],
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.split(), pins)

    def _target_root(self, tmp):
        """A copy of the inputs with one synthetic hardware target that owns a
        pinned noarch package and the Pi kernel (the hardware-target machinery
        has no real desktop target today)."""
        import json, shutil
        root = Path(tmp)
        shutil.copytree(ROOT / 'config', root / 'config')
        shutil.copytree(ROOT / 'scripts/os/lib', root / 'scripts/os/lib')
        registry = json.loads((root / 'config/os/desktop-targets.json').read_text())
        registry['device_repositories'] = {}
        registry['hardware_targets'] = {'example,board': {
            'kernel': 'kernel-luma-rpi', 'packages': ['kernel-luma-rpi', 'luma-rpi5-platform']}}
        (root / 'config/os/desktop-targets.json').write_text(json.dumps(registry))
        with (root / 'scripts/os/lib/devices.tsv').open('a') as stream:
            stream.write('example,board\texample-board\taarch64\toci\tdesktop\t-\tluma-desktop\n')
        return root

    def test_hardware_target_adds_only_its_packages_and_kernel(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self._target_root(tmp)
            generic = t.packages(root, 'aarch64', profile_name='arm-preview')
            board = t.packages(root, 'aarch64', profile_name='arm-preview', hardware_target='example,board')
            device = t.hardware(root, 'aarch64', 'example,board')
            added = {t.package_name(p) for p in board} - {t.package_name(p) for p in generic}
            self.assertEqual(added, set(device['packages']))
            self.assertTrue(set(generic) < set(board))
            self.assertEqual(device['kernel'], 'kernel-luma-rpi')
            for arch in t.ARCHITECTURES:
                names = {t.package_name(p) for p in t.packages(root, arch)}
                self.assertFalse(names & t.device_package_names(root), arch)

    def test_hardware_target_has_its_own_ref_and_architecture(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self._target_root(tmp)
            values = t.contract(root, 'aarch64', 'arm-preview', 'example,board')
            self.assertEqual(values['LUMA_OS_REF_PREFIX'], 'luma/1/aarch64/example-board')
            self.assertEqual(values['LUMA_OS_HARDWARE_TARGET'], 'example,board')
            self.assertEqual(values['LUMA_OS_KERNEL_PACKAGE'], 'kernel-luma-rpi')
            self.assertNotIn('LUMA_OS_HARDWARE_TARGET', t.contract(root, 'aarch64', 'arm-preview'))
            for arch, target in (('x86_64', 'example,board'), ('aarch64', 'fairphone,fp6'), ('aarch64', 'unknown')):
                with self.subTest(arch=arch, target=target), self.assertRaises(ValueError):
                    t.packages(root, arch, hardware_target=target)

    def test_shell_environment_passes_the_hardware_target(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self._target_root(tmp)
            env = {**os.environ, 'LUMA_TARGET_ARCHITECTURE': 'aarch64', 'LUMA_OS_PROFILE': 'arm-preview',
                   'LUMA_OS_HARDWARE_TARGET': 'example,board'}
            result = subprocess.run(['bash', '-ec', '. scripts/os/lib/common.sh; echo "$LUMA_OS_REF_PREFIX"; luma_os_desktop_packages'],
                                    cwd=root, env=env, text=True, capture_output=True, check=True)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], 'luma/1/aarch64/example-board')
            self.assertEqual(lines[1:], t.packages(root, 'aarch64', profile_name='arm-preview', hardware_target='example,board'))

if __name__ == '__main__':
    unittest.main()
