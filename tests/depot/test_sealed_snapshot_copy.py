#!/usr/bin/env python3
"""Real isolated-inode and corruption/race controls for publisher snapshots.

Run as the release administrator on an owned reflink filesystem. This test
never needs publisher keys and creates only its own disposable test files.
"""
import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

SOURCE = Path(os.environ.get('LUMA_SEAL_TEST_SOURCE',
    str(Path(__file__).resolve().parents[2] / 'scripts/depot/seal-signing-control.py')))
spec = importlib.util.spec_from_file_location('snapshot_sealer', SOURCE)
sealer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sealer)


class SnapshotCopyControls(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.geteuid(), 0, 'Exercise actual root-owned publisher boundary.')
        self.temp = tempfile.TemporaryDirectory(prefix='seal-copy-controls-',
            dir=os.environ.get('LUMA_SEAL_TEST_PARENT'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir(mode=0o700)
        self.file = self.source / 'payload'
        self.file.write_bytes(bytes(range(256)) * 8192)
        self.file.chmod(0o644)
        self.target = self.root / 'sealed'

    def seal(self):
        return sealer.seal(self.source, self.target, inputs=True)

    def assert_no_manifest(self):
        self.assertFalse((self.target / sealer.MANIFEST).exists())

    def test_real_reflink_fresh_inode_and_destination_write_isolation(self):
        original = self.file.read_bytes()
        inode = self.file.stat().st_ino
        ioctl = sealer.fcntl.ioctl
        successful = []
        def observe(*args):
            value = ioctl(*args)
            successful.append(args[1])
            return value
        with mock.patch.object(sealer.fcntl, 'ioctl', side_effect=observe):
            digest = self.seal()
        self.assertTrue(successful, 'Actual filesystem reflink must succeed in this control.')
        dest = self.target / 'payload'
        self.assertNotEqual(dest.stat().st_ino, inode)
        self.assertEqual(dest.stat().st_uid, 0)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o400)
        self.assertEqual(dest.read_bytes(), original)
        sealer.verify(self.target, digest, 'org.projectluma.signing-inputs/v1')
        dest.chmod(0o600)
        with dest.open('r+b') as stream:
            stream.write(b'DESTINATION MUTATION')
            stream.flush()
            os.fsync(stream.fileno())
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(self.file.stat().st_ino, inode)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o644)

    def test_explicit_unsupported_ioctl_streams_exact_fresh_bytes(self):
        for code in (errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL):
            with self.subTest(errno=code):
                self.target = self.root / ('sealed-' + str(code))
                with mock.patch.object(sealer.fcntl, 'ioctl', side_effect=OSError(code, 'unsupported')):
                    digest = self.seal()
                dest = self.target / 'payload'
                self.assertNotEqual(dest.stat().st_ino, self.file.stat().st_ino)
                self.assertEqual(dest.read_bytes(), self.file.read_bytes())
                sealer.verify(self.target, digest, 'org.projectluma.signing-inputs/v1')

    def test_unexpected_ioctl_errors_never_fall_back_or_admit(self):
        for code in (errno.EPERM, errno.EIO, errno.ENOSPC):
            with self.subTest(errno=code):
                self.target = self.root / ('rejected-' + str(code))
                with mock.patch.object(sealer.fcntl, 'ioctl', side_effect=OSError(code, 'refuse')):
                    with self.assertRaises(OSError):
                        self.seal()
                self.assert_no_manifest()

    def test_actual_source_mutation_during_snapshot_is_rejected(self):
        copy = sealer.copy_snapshot_bytes
        def mutate(origin, output):
            result = copy(origin, output)
            with self.file.open('r+b') as stream:
                stream.write(b'SOURCE RACE')
                stream.flush()
                os.fsync(stream.fileno())
            return result
        with mock.patch.object(sealer, 'copy_snapshot_bytes', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'source changed during snapshot'):
                self.seal()
        self.assert_no_manifest()

    def test_actual_destination_corruption_is_rejected(self):
        copy = sealer.copy_snapshot_bytes
        original = self.file.read_bytes()
        def corrupt(origin, output):
            result = copy(origin, output)
            output.seek(0)
            output.write(b'CORRUPT DESTINATION')
            output.flush()
            os.fsync(output.fileno())
            return result
        with mock.patch.object(sealer, 'copy_snapshot_bytes', side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, 'destination bytes differ'):
                self.seal()
        self.assertEqual(self.file.read_bytes(), original)
        self.assert_no_manifest()

    def test_existing_target_is_never_replaced(self):
        self.target.mkdir(mode=0o700)
        sentinel = self.target / 'sentinel'
        sentinel.write_bytes(b'original')
        with self.assertRaisesRegex(ValueError, 'refusing to replace'):
            self.seal()
        self.assertEqual(sentinel.read_bytes(), b'original')

    def test_symlink_member_is_rejected(self):
        (self.source / 'alias').symlink_to(self.file)
        with self.assertRaisesRegex(ValueError, 'nonregular publisher source'):
            self.seal()
        self.assert_no_manifest()

    def test_tampered_sealed_bytes_are_refused_by_full_verifier(self):
        digest = self.seal()
        dest = self.target / 'payload'
        dest.chmod(0o600)
        with dest.open('r+b') as stream:
            stream.write(b'changed')
        dest.chmod(0o400)
        with self.assertRaisesRegex(ValueError, 'publisher control member changed'):
            sealer.verify(self.target, digest, 'org.projectluma.signing-inputs/v1')

    def test_zero_length_and_executable_members_have_exact_sealed_modes(self):
        (self.source / 'empty').write_bytes(b'')
        executable = self.source / 'executable'
        executable.write_bytes(b'#!/bin/sh\nexit 0\n')
        executable.chmod(0o755)
        digest = self.seal()
        self.assertEqual((self.target / 'empty').read_bytes(), b'')
        self.assertEqual(stat.S_IMODE((self.target / 'executable').stat().st_mode), 0o500)
        self.assertEqual(executable.read_bytes(), (self.target / 'executable').read_bytes())
        sealer.verify(self.target, digest, 'org.projectluma.signing-inputs/v1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
