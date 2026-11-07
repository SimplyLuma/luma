# SPDX-License-Identifier: Apache-2.0
import hashlib
from pathlib import Path
import re
import tomllib
import unittest

DEVICE = Path(__file__).resolve().parents[1]
ROOT = DEVICE.parents[2]
CARD = ROOT / 'src/luma-rpi5-platform/card'


def toml(name):
    return tomllib.loads((DEVICE / name).read_text())


class RaspberryPi5Profile(unittest.TestCase):
    def test_luma_pi_kernel_matches_its_source_lock_and_package(self):
        import json
        kernel = toml('device.toml')['kernel']
        lock = json.loads((ROOT / kernel['source_lock']).read_text())
        self.assertEqual(kernel['release'], lock['release'])
        self.assertEqual(lock['device_tree'], kernel['dtb'])
        self.assertEqual(lock['pi_defconfig'], 'bcm2711_defconfig')
        spec = (ROOT / 'packaging/rpm/kernel-luma-rpi.spec').read_text()
        version, release = kernel['rpm_version_release'].rsplit('.fc', 1)[0].split('-', 1)
        self.assertIn(f'Version: {version}', spec)
        self.assertIn(f'Release: {release}%{{?dist}}', spec)
        self.assertTrue((ROOT / kernel['config']).is_file())

    def test_uefi_card_boot_and_experimental_tier(self):
        device, boot = toml('device.toml'), toml('boot.toml')
        self.assertEqual(device['tier'], 'experimental')
        self.assertEqual(boot['format'], 'uefi')
        self.assertEqual(boot['uefi']['dtb'], device['kernel']['dtb'])
        self.assertNotIn('releases', device)

    def test_card_menu_uses_the_profile_arguments_and_never_console(self):
        boot = toml('boot.toml')
        self.assertIn('console=', boot['cmdline']['forbidden'])
        self.assertIn('firmware_class.path=', boot['cmdline']['forbidden'])
        script = (ROOT / 'scripts/devices/build-rpi5-sd-image.sh').read_text()
        args = re.search(r'^installer_args="([^"]*)"', script, re.M).group(1).split()
        self.assertEqual(args[:len(boot['cmdline']['args'])], boot['cmdline']['args'])
        for arg in args:
            for forbidden in boot['cmdline']['forbidden']:
                self.assertFalse(arg == forbidden or (forbidden.endswith('=') and arg.startswith(forbidden)), arg)
        grub = (CARD / 'grub.cfg.in').read_text().splitlines()
        self.assertFalse([line for line in grub if 'console=' in line and not line.lstrip().startswith('#')])

    def test_card_names_both_board_device_trees(self):
        script = (ROOT / 'scripts/devices/rpi5-board-dtbs.sh').read_text()
        dtb = toml('device.toml')['kernel']['dtb']
        self.assertIn(dtb.split('/', 1)[1], script)
        self.assertIn('bcm2712-d-rpi-5-b.dtb', script)
        self.assertIn('bcm2712d0-rpi-5-b.dtb', script)
        self.assertIn('kernel=u-boot.bin', (CARD / 'config.txt').read_text())

    def test_firmware_manifest_is_exact_and_matches_the_packages(self):
        device = toml('device.toml')
        manifest = (DEVICE / 'firmware.manifest').read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), device['firmware']['manifest_sha256'])
        platform = (ROOT / 'packaging/rpm/luma-rpi5-platform.spec').read_text()
        bluetooth = (ROOT / 'packaging/rpm/luma-rpi-bluetooth-firmware.spec').read_text()
        files = 0
        for row in manifest.decode().splitlines()[1:]:
            kind, _, digest, path, target = row.split('\t')
            name = path.split('/')[-1]
            if kind == 'F':
                files += 1
                self.assertTrue(name.endswith('.hcd'))
                self.assertIn(name.split('.')[0], bluetooth)
                if name == 'BCM4345C0.hcd':
                    self.assertIn(digest, bluetooth)
            elif kind == 'L' and name.endswith('.hcd'):
                self.assertTrue(target.endswith('.hcd'))
            elif kind == 'L':
                self.assertIn(f'ln -s {target} \\', platform)
                self.assertIn(name, platform)
        self.assertEqual(files, device['firmware']['files'])
