# SPDX-License-Identifier: Apache-2.0
import hashlib
from pathlib import Path
import tomllib
import unittest

DEVICE = Path(__file__).resolve().parents[1]


class SurfaceProfile(unittest.TestCase):
    def test_matched_kernel_and_uefi_device_tree(self):
        device = tomllib.loads((DEVICE / 'device.toml').read_text())
        boot = tomllib.loads((DEVICE / 'boot.toml').read_text())
        self.assertEqual(device['tier'], 'experimental')
        self.assertEqual(device['soc'], 'qcom/purwa')
        self.assertEqual(boot['format'], 'uefi')
        self.assertEqual(boot['uefi']['dtb'], device['kernel']['dtb'])
        self.assertIn('enforcing=1', boot['cmdline']['args'])
        self.assertNotIn('avb', boot)
        self.assertNotIn('partition', boot)

    def test_no_published_release_is_invented(self):
        device = tomllib.loads((DEVICE / 'device.toml').read_text())
        self.assertNotIn('tag', device['kernel'])
        self.assertNotIn('releases', device)
        self.assertEqual(device['updates']['ostree_ref'],
                         'luma/1/aarch64/microsoft-sp12in/nightly')

    def test_firmware_input_manifest_is_exact(self):
        device = tomllib.loads((DEVICE / 'device.toml').read_text())
        manifest = (DEVICE / 'firmware.manifest').read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(),
                         device['firmware']['manifest_sha256'])
        self.assertIn(b'qcadsp8380.mbn', manifest)
        self.assertIn(b'qcdxkmsucpurwa.mbn', manifest)
