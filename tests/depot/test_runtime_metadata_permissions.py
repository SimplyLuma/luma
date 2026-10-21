# SPDX-License-Identifier: Apache-2.0
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

source = Path(__file__).resolve().parents[2] / 'scripts/depot/runtime/normalize-publish-metadata.py'
spec = importlib.util.spec_from_file_location('runtime_metadata_permissions', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MetadataPermissions(unittest.TestCase):
    def test_private_builder_mode_becomes_readable_without_changing_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / 'metadata'
            metadata.write_text('[Runtime]\nname=org.projectluma.Platform\n')
            metadata.chmod(0o640)
            original = metadata.read_bytes()
            module.normalize(root)
            self.assertEqual(stat.S_IMODE(metadata.stat().st_mode), 0o644)
            self.assertEqual(metadata.read_bytes(), original)

    def test_hardlinked_source_inode_is_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'sealed-source'
            original.write_text('[Runtime]\nname=org.projectluma.Platform\n')
            original.chmod(0o400)
            os.link(original, root / 'metadata')
            module.normalize(root)
            self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE((root / 'metadata').stat().st_mode), 0o644)
            self.assertEqual(original.read_bytes(), (root / 'metadata').read_bytes())
            self.assertNotEqual(original.stat().st_ino, (root / 'metadata').stat().st_ino)

    def test_metadata_symlink_cannot_change_another_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'private-file'
            target.write_text('private')
            target.chmod(0o600)
            (root / 'metadata').symlink_to(target)
            with self.assertRaises(OSError):
                module.normalize(root)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_nonregular_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.mkfifo(root / 'metadata', 0o600)
            with self.assertRaises(ValueError):
                module.normalize(root)

    def test_symlinked_staging_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / 'actual'
            actual.mkdir()
            (actual / 'metadata').write_text('original')
            (root / 'alias').symlink_to(actual)
            with self.assertRaises(OSError):
                module.normalize(root / 'alias')


if __name__ == '__main__':
    unittest.main()
