# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest

from luma_tester import presets
from luma_tester.core import TesterError


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'presets.json'
        self.manifest_path = self.root / 'mobile.json'
        self.manifest = dict(schema=1, id='phone-preset-fixture', target='mobile', arch='aarch64',
            boot='uefi', user='luma', cloud_init=True, build='Manifest fixture only',
            image=dict(url=(self.root / 'no-disk.qcow2').as_uri(), bytes=42, sha256='a' * 64))
        self.write_manifest()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def test_named_configuration_survives_restart_and_replaces_same_name(self):
        presets.save(self.path, 'Phone dev', self.manifest_path, 'develop')
        saved = presets.load(self.path)
        self.assertEqual(saved[0]['intent'], 'develop')
        self.assertEqual(saved[0]['arch'], 'aarch64')
        self.assertEqual(presets.resolve(saved[0]), self.manifest_path)
        presets.save(self.path, 'Phone dev', self.manifest_path, 'try')
        self.assertEqual(len(presets.load(self.path)), 1)
        self.assertEqual(presets.load(self.path)[0]['intent'], 'try')
        self.assertEqual(self.path.stat().st_mode & 0o077, 0)

    def test_changed_nightly_never_restores_as_saved_image(self):
        item = presets.save(self.path, 'Pinned phone', self.manifest_path, 'develop')[0]
        self.manifest['image']['sha256'] = 'b' * 64
        self.write_manifest()
        with self.assertRaisesRegex(TesterError, 'image has changed'):
            presets.resolve(item)

    def test_invalid_name_preserves_existing_configuration(self):
        presets.save(self.path, 'Phone', self.manifest_path, 'try')
        original = self.path.read_bytes()
        for name in ('', 'bad\nname', 'x' * 81):
            with self.assertRaises(TesterError):
                presets.save(self.path, name, self.manifest_path, 'try')
        self.assertEqual(self.path.read_bytes(), original)

    def test_vm_settings_are_pinned_with_preset(self):
        from luma_tester import vm
        settings=vm.load(self.root)
        settings.update(memory=8192,cpus=6,agent_ssh=True)
        presets.save(self.path,'Development',self.manifest_path,'develop',settings)
        settings['memory']=4096
        saved=presets.load(self.path)[0]['vm']
        self.assertEqual(saved['memory'],8192)
        self.assertEqual(saved['cpus'],6)
        self.assertTrue(saved['agent_ssh'])


if __name__ == '__main__':
    unittest.main()
