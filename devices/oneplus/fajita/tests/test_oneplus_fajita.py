# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
from pathlib import Path
import re
import tomllib
import unittest

DEVICE = Path(__file__).resolve().parents[1]
ROOT = DEVICE.parents[2]


def load(path: Path) -> dict:
    return tomllib.loads(path.read_text())


class OnePlusFajitaProfile(unittest.TestCase):
    def setUp(self):
        self.device = load(DEVICE / 'device.toml')
        self.boot = load(DEVICE / 'boot.toml')
        self.soc = load(ROOT / 'socs' / 'qcom' / 'sdm845' / 'soc.toml')

    def test_experimental_on_sdm845_with_header_v0(self):
        self.assertEqual(self.device['tier'], 'experimental')
        self.assertEqual(self.device['soc'], 'qcom/sdm845')
        self.assertEqual(self.soc['boot']['format'], 'android-boot-v0')
        self.assertNotIn('format', self.boot)
        self.assertEqual(self.boot['partition_size'], 64 * 1024 * 1024)

    def test_no_published_release_or_own_ref_is_invented(self):
        self.assertNotIn('tag', self.device['kernel'])
        self.assertNotIn('releases', self.device)
        # The phone follows the shared ARM nightly ref.
        self.assertNotIn('updates', self.device)

    def test_kernel_lock_matches_manifest(self):
        lock = json.loads((ROOT / self.device['kernel']['source_lock']).read_text())
        self.assertEqual(lock['release'], self.device['kernel']['release'])
        self.assertEqual(lock['device_tree'], self.device['kernel']['dtb'])
        self.assertEqual(set(lock['sources']), {'linux', 'overlay'})

    def test_firmware_manifest_is_exact(self):
        manifest = (DEVICE / 'firmware.manifest').read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(),
                         self.device['firmware']['manifest_sha256'])
        self.assertIn(b'qcom/sdm845/OnePlus/enchilada/modem.mbn', manifest)
        self.assertIn(b'ath10k/WCN3990/hw1.0/wlanmdsp.mbn.zst', manifest)

    def test_guide_writes_only_boot_and_userdata(self):
        guide = (DEVICE / 'INSTALL.md').read_text()
        written = set(re.findall(r'fastboot\s+(?:flash|erase|format)(?::\S+)?\s+(\S+)', guide))
        self.assertEqual(written, {'userdata', 'boot_a', 'boot_b'})
        for partition in ('vbmeta', 'dtbo', 'modem_', 'modemst', 'persist', 'fsg', 'fsc'):
            self.assertNotRegex(guide, rf'fastboot\s+(?:flash|erase|format)\S*\s+{partition}')


if __name__ == '__main__':
    unittest.main()
