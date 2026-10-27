# SPDX-License-Identifier: Apache-2.0
import errno
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('media_vm_iso', ROOT / 'scripts/os/lib/media_vm_iso.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class IsoFixture(unittest.TestCase):
    def test_real_reflink_isolates_libvirt_ownership_and_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, fixture, proof = [root / name for name in ('source.iso', 'fixture.iso', 'proof.json')]
            source.write_bytes(b'release medium' * 100)
            before = media.identity(source.stat())
            record = media.clone(source, fixture)
            self.assertEqual(source.read_bytes(), fixture.read_bytes())
            self.assertNotEqual(source.stat().st_ino, fixture.stat().st_ino)
            proof.write_text(json.dumps(record))
            fixture.write_bytes(b'guest side mutation')
            fixture.chmod(0o400)
            if os.geteuid() == 0:
                os.chown(fixture, 65534, 65534)
            media.verify(proof)
            self.assertEqual(before, media.identity(source.stat()))
            self.assertEqual(source.read_bytes(), b'release medium' * 100)
            source.chmod(0o600 if source.stat().st_mode & 0o777 != 0o600 else 0o400)
            with self.assertRaisesRegex(ValueError, 'release ISO'):
                media.verify(proof)

    def test_unsupported_clone_fails_without_copy_or_partial_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, fixture = root / 'source.iso', root / 'fixture.iso'
            source.write_bytes(b'original')
            with patch.object(media.fcntl, 'ioctl', side_effect=OSError(errno.EXDEV, 'cross-device')):
                with self.assertRaises(OSError):
                    media.clone(source, fixture)
            self.assertFalse(fixture.exists())
            self.assertEqual(source.read_bytes(), b'original')
            fixture.write_bytes(b'previous attempt')
            with self.assertRaises(FileExistsError):
                media.clone(source, fixture)
            self.assertEqual(fixture.read_bytes(), b'previous attempt')

    @unittest.skipUnless(os.geteuid() == 0, 'actual QEMU reader preflight requires root')
    def test_private_release_leaf_does_not_need_permission_changes(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            release, public = root / 'private-release', root / 'fixture'
            release.mkdir(mode=0o700)
            public.mkdir(mode=0o755)
            public.chmod(0o755)
            source, fixture, proof = release / 'source.iso', public / 'installer.iso', public / 'proof.json'
            source.write_bytes(b'release')
            source.chmod(0o444)
            before = media.identity(source.stat())
            self.assertNotEqual(subprocess.run(['runuser', '-u', 'qemu', '--', 'test', '-r', str(source)]).returncode, 0)
            media.prepare(source, fixture, proof, 'qemu')
            media.verify(proof)
            self.assertEqual(before, media.identity(source.stat()))
            self.assertEqual(release.stat().st_mode & 0o777, 0o700)
            self.assertEqual(fixture.stat().st_mode & 0o777, 0o400)
            # A fixture below an inaccessible leaf must fail before VM launch.
            with self.assertRaises(subprocess.CalledProcessError):
                media.prepare(source, release / 'unreadable.iso', release / 'bad-proof.json', 'qemu')
            self.assertFalse((release / 'unreadable.iso').exists())


if __name__ == '__main__':
    unittest.main()
