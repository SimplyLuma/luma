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
    def test_fedora_kernel_matches_the_arm_installer_kernel(self):
        # The card's device trees come from the installer's own kernel.
        kernel = toml('device.toml')['kernel']
        env = dict(line.split('=', 1) for line in
                   (ROOT / 'config/install/atlas/runtime-kernel-aarch64.env').read_text().splitlines()
                   if line.startswith('ATLAS_RUNTIME_KERNEL_'))
        self.assertEqual(kernel['distribution'], 'fedora')
        self.assertEqual(kernel['nevr'], 'kernel-' + env['ATLAS_RUNTIME_KERNEL_NVR'])
        self.assertEqual(kernel['release'], env['ATLAS_RUNTIME_KERNEL_NVR'] + '.aarch64')
        self.assertNotIn('config', kernel)

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

    def test_firmware_manifest_is_exact_and_holds_only_the_alias(self):
        device = toml('device.toml')
        manifest = (DEVICE / 'firmware.manifest').read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), device['firmware']['manifest_sha256'])
        spec = (ROOT / 'packaging/rpm/luma-rpi5-platform.spec').read_text()
        for row in manifest.decode().splitlines():
            kind, _, _, path, target = row.split('\t')
            if kind == 'L':
                name, link = path.split('/')[-1], target
                self.assertIn(f'ln -s {link} \\', spec)
                self.assertIn(name, spec)
