# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise the actual userspace FAT image and readback, without loop mounts."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('efi_image', ROOT / 'scripts/install/atlas-iso/make-efi-image.py')
EFI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EFI)


@unittest.skipUnless(all(shutil.which(tool) for tool in ('mcopy', 'mmd', 'mkfs.msdos', 'fsck.fat')),
                     'requires real FAT/mtools commands')
class EfiImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.boot = self.root / 'BOOT'
        (self.boot / 'fonts').mkdir(parents=True)
        (self.boot / 'BOOTAA64.EFI').write_bytes(bytes(range(256)) * 50)
        (self.boot / 'grub.cfg').write_text('set timeout=1\n')
        (self.boot / 'fonts/font with spaces.pf2').write_bytes(b'font\0\xff')
        # Lorax itself is unavailable on ordinary source-test hosts. Only its
        # sizing input is supplied here; filesystem tools and all readback run.
        estimator = ModuleType('pylorax.imgutils')
        estimator.estimate_size = lambda *args, **kwargs: 16 * 1024 * 1024
        self.modules = patch.dict(sys.modules, {'pylorax.imgutils': estimator})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.environment = patch.dict(os.environ, {'SOURCE_DATE_EPOCH': '1735689600', 'TZ': 'UTC'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_real_fat_bytes_and_reproducible_image(self):
        first, second = self.root / 'first.img', self.root / 'second.img'
        EFI.create(self.boot, first, 'ANACONDA')
        time.sleep(2.1)  # Cross a FAT timestamp tick before reconstructing it.
        EFI.create(self.boot, second, 'ANACONDA')
        self.assertEqual(EFI.digest(first), EFI.digest(second))
        output = self.root / 'boot-readback'
        subprocess.run(['mcopy', '-i', str(first), '::/EFI/BOOT/BOOTAA64.EFI', str(output)], check=True)
        self.assertEqual(output.read_bytes(), (self.boot / 'BOOTAA64.EFI').read_bytes())

    def test_corrupted_readback_is_fatal(self):
        original = EFI.digest
        with patch.object(EFI, 'digest', side_effect=lambda path: 'bad' if path.parent.name.startswith('efi-readback-') else original(path)):
            with self.assertRaisesRegex(ValueError, 'readback differs'):
                EFI.create(self.boot, self.root / 'corrupt.img', 'ANACONDA')

    def test_symlink_and_existing_image_are_rejected(self):
        image = self.root / 'keep.img'
        image.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'existing EFI image'):
            EFI.create(self.boot, image, 'ANACONDA')
        self.assertEqual(image.read_bytes(), b'keep')
        (self.boot / 'alias').symlink_to('grub.cfg')
        with self.assertRaisesRegex(ValueError, 'without symlinks'):
            EFI.create(self.boot, self.root / 'symlink.img', 'ANACONDA')

    def test_atomic_replacement_preserves_original_on_readback_failure(self):
        image = self.root / 'replace.img'
        image.write_bytes(b'original')
        original = EFI.digest
        with patch.object(EFI, 'digest', side_effect=lambda path: 'bad' if path.parent.name.startswith('efi-readback-') else original(path)):
            with self.assertRaisesRegex(ValueError, 'readback differs'):
                EFI.replace_image(self.boot, image, 'ANACONDA')
        self.assertEqual(image.read_bytes(), b'original')
        EFI.replace_image(self.boot, image, 'ANACONDA')
        output = self.root / 'replacement-readback'
        subprocess.run(['mcopy', '-i', str(image), '::/EFI/BOOT/BOOTAA64.EFI', str(output)], check=True)
        self.assertEqual(output.read_bytes(), (self.boot / 'BOOTAA64.EFI').read_bytes())


if __name__ == '__main__':
    unittest.main()
