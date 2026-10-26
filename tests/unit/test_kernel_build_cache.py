# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('cache',ROOT/'scripts/kernel/build-cache.py')
cache=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cache)

class KernelBuildCache(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.repo=self.root/'repo';self.build=self.root/'build';self.base=self.root/'base'
        (self.repo/'config/kernel').mkdir(parents=True)
        (self.repo/'config/kernel/surface-pro12-7.3.json').write_text(json.dumps({'release':'7.3-surface'}))
        (self.repo/'config/kernel/surface-pro12.config').write_text('fragment')
        self.base.write_text('base config')
        for name in cache.ARTIFACTS:
            path=self.build/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
        (self.build/'include/config/kernel.release').write_text('7.3-surface\n')
        (self.build/'modules.order').write_text('drivers/test.o\n')
        (self.build/'drivers').mkdir();(self.build/'drivers/test.ko').write_bytes(b'compiled fixture')
        cache.record(self.repo,self.build,self.base)
    def tearDown(self):self.tmp.cleanup()
    def verify(self):cache.verify(self.repo,self.build,self.base)
    def test_completed_unchanged_build_can_be_reused(self):self.verify()
    def test_changed_boot_bytes_are_refused(self):
        (self.build/'arch/arm64/boot/Image').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'cache changed'):self.verify()
    def test_changed_module_is_refused(self):
        (self.build/'drivers/test.ko').write_bytes(b'changed module')
        with self.assertRaisesRegex(ValueError,'cache changed'):self.verify()
    def test_changed_configuration_inputs_are_refused(self):
        (self.repo/'config/kernel/surface-pro12.config').write_text('different fragment')
        with self.assertRaisesRegex(ValueError,'cache changed'):self.verify()
    def test_missing_module_is_refused(self):
        (self.build/'drivers/test.ko').unlink()
        with self.assertRaises(FileNotFoundError):self.verify()
    def test_replaced_symlink_module_is_refused(self):
        module=self.build/'drivers/test.ko';module.unlink();module.symlink_to(self.base)
        with self.assertRaisesRegex(ValueError,'escapes'):self.verify()

    def test_kbuild_internal_parent_paths_are_normalized(self):
        (self.build/'drivers/unused').mkdir()
        (self.build/'modules.order').write_text('drivers/unused/../test.o\n')
        self.assertEqual(cache.inventory(self.build)[0]['path'], 'drivers/test.ko')
    def test_parent_paths_cannot_escape_build_root(self):
        (self.build/'modules.order').write_text('drivers/../../outside.o\n')
        with self.assertRaisesRegex(ValueError,'Unsafe'):cache.inventory(self.build)
