# SPDX-License-Identifier: Apache-2.0
import contextlib
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('media_vm_iso', ROOT / 'scripts/os/lib/media_vm_iso.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


NO_REFLINK = OSError(errno.EOPNOTSUPP, 'Operation not supported')
BODY = b'release medium' * 100


class IsoFixture(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.source, self.fixture = self.root / 'source.iso', self.root / 'fixture.iso'
        self.source.write_bytes(BODY)
        self.before = media.identity(self.source.stat())

    def tearDown(self):
        self._dir.cleanup()

    def assert_untouched(self):
        self.assertFalse(self.fixture.exists())
        self.assertEqual(self.source.read_bytes(), BODY)
        self.assertEqual(self.before, media.identity(self.source.stat()))

    def test_fixture_isolates_libvirt_ownership_and_writes(self):
        # A reflink where this filesystem has them, a verified copy elsewhere.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, fixture, proof = [root / name for name in ('source.iso', 'fixture.iso', 'proof.json')]
            source.write_bytes(b'release medium' * 100)
            before = media.identity(source.stat())
            record = media.fixture(source, fixture, copy_budget=media.GIB, reserve=0)
            self.assertIn(record['method'], ('FICLONE', 'copy'))
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

    def test_no_reflink_without_a_budget_creates_nothing(self):
        with patch.object(media.fcntl, 'ioctl', side_effect=NO_REFLINK):
            with self.assertRaises(media.NoReflink):
                media.fixture(self.source, self.fixture)
        self.assert_untouched()

    def test_no_reflink_exits_3_and_says_what_to_do(self):
        argv = ['media_vm_iso.py', 'prepare', str(self.source), str(self.fixture), str(self.root / 'proof.json'),
                '--reader', __import__('pwd').getpwuid(os.getuid()).pw_name]
        stderr = io.StringIO()
        with patch.object(media.fcntl, 'ioctl', side_effect=NO_REFLINK), patch.object(sys, 'argv', argv), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit:
                media.main()
        self.assertEqual(exit.exception.code, media.NO_REFLINK_EXIT)
        self.assertIn('--copy-budget-gib', stderr.getvalue())
        self.assert_untouched()

    def test_no_reflink_with_a_budget_makes_a_verified_copy(self):
        Path(f'{self.source}.sha256').write_text(hashlib.sha256(BODY).hexdigest() + '\n')
        with patch.object(media.fcntl, 'ioctl', side_effect=NO_REFLINK):
            record = media.fixture(self.source, self.fixture, copy_budget=media.GIB, reserve=0)
        self.assertEqual(record['method'], 'copy')
        self.assertTrue(record['body_sha256_verified'])
        self.assertTrue(record['release_sha256_record'])
        self.assertEqual(record['body_sha256'], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(self.fixture.read_bytes(), BODY)
        self.assertNotEqual(self.fixture.stat().st_ino, self.source.stat().st_ino)
        proof = self.root / 'proof.json'
        proof.write_text(json.dumps(record))
        self.fixture.write_bytes(b'guest side mutation')
        media.verify(proof)
        self.assertEqual(self.before, media.identity(self.source.stat()))

    def test_copy_over_budget_is_refused(self):
        with patch.object(media.fcntl, 'ioctl', side_effect=NO_REFLINK):
            with self.assertRaisesRegex(ValueError, 'copy budget; raise LUMA_OS_MEDIA_COPY_BUDGET_GIB'):
                media.fixture(self.source, self.fixture, copy_budget=len(BODY) - 1, reserve=0)
        self.assert_untouched()

    def test_copy_without_free_space_is_refused(self):
        full = os.statvfs_result((4096, 4096, 100, 0, 0, 100, 0, 0, 0, 255))
        with patch.object(media.fcntl, 'ioctl', side_effect=NO_REFLINK), patch.object(media.os, 'statvfs', return_value=full):
            with self.assertRaisesRegex(ValueError, 'free in .*free space there'):
                media.fixture(self.source, self.fixture, copy_budget=media.GIB, reserve=0)
        self.assert_untouched()

    def test_copy_that_does_not_match_the_release_record_is_removed(self):
        Path(f'{self.source}.sha256').write_text('0' * 64 + '\n')
        with patch.object(media.fcntl, 'ioctl', side_effect=NO_REFLINK):
            with self.assertRaisesRegex(ValueError, 'does not match source.iso.sha256'):
                media.fixture(self.source, self.fixture, copy_budget=media.GIB, reserve=0)
        self.assert_untouched()

    def test_other_clone_errors_are_not_copied_around(self):
        with patch.object(media.fcntl, 'ioctl', side_effect=OSError(errno.EIO, 'I/O error')):
            with self.assertRaises(OSError) as raised:
                media.fixture(self.source, self.fixture, copy_budget=media.GIB, reserve=0)
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assert_untouched()

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
            media.prepare(source, fixture, proof, 'qemu', copy_budget=media.GIB, reserve=0)
            media.verify(proof)
            self.assertEqual(before, media.identity(source.stat()))
            self.assertEqual(release.stat().st_mode & 0o777, 0o700)
            self.assertEqual(fixture.stat().st_mode & 0o777, 0o400)
            # A fixture below an inaccessible leaf must fail before VM launch.
            with self.assertRaisesRegex(ValueError, 'qemu cannot read .* searchable by qemu'):
                media.prepare(source, release / 'unreadable.iso', release / 'bad-proof.json', 'qemu',
                              copy_budget=media.GIB, reserve=0)
            self.assertFalse((release / 'unreadable.iso').exists())


if __name__ == '__main__':
    unittest.main()
