# SPDX-License-Identifier: Apache-2.0
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / 'scripts/android/export-stream.py'
spec = importlib.util.spec_from_file_location('export_stream', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Streaming(unittest.TestCase):
    def copy(self, directory, *, blocks=32, size=None, digest=None, sync=None):
        data = b'x' * 1024**2
        process = subprocess.Popen([sys.executable, '-c',
            'import sys; data=b"x"*1048576; [sys.stdout.buffer.write(data) for _ in range(int(sys.argv[1]))]',
            str(blocks)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        target = Path(directory) / 'image.img'
        try:
            kwargs = dict(size=blocks * len(data) if size is None else size,
                          digest=digest or hashlib.sha256(data * blocks).hexdigest(),
                          deadline=time.monotonic() + 20)
            return module.copy_verified_stream(process.stdout, target, **kwargs)
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)

    def test_large_stream_syncs_incrementally_and_publishes_verified_bytes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module.os, 'fdatasync', wraps=module.os.fdatasync) as sync:
            result = self.copy(directory)
            self.assertGreaterEqual(sync.call_count, 5)
            target = Path(directory) / 'image.img'
            with target.open('rb') as stream:
                self.assertEqual(hashlib.file_digest(stream, 'sha256').hexdigest(), result['sha256'])
            self.assertEqual(target.stat().st_size, 32 * 1024**2)
            self.assertFalse(target.with_name('image.img.partial').exists())

    def test_wrong_checksum_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.copy(directory, blocks=1, digest='0' * 64)
            self.assertFalse((Path(directory) / 'image.img').exists())
            self.assertTrue((Path(directory) / 'image.img.partial').exists())

    def test_short_and_excess_streams_never_publish(self):
        for expected in (1, 2 * 1024**2):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    self.copy(directory, blocks=1, size=expected)
                self.assertFalse((Path(directory) / 'image.img').exists())

    def test_existing_artifact_and_symlink_are_preserved(self):
        for symlink in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'image.img'
                other = Path(directory) / 'original'; other.write_bytes(b'original')
                if symlink:
                    target.symlink_to(other)
                else:
                    target.write_bytes(b'original')
                with self.assertRaises(FileExistsError):
                    self.copy(directory, blocks=1)
                self.assertEqual(other.read_bytes(), b'original')
                self.assertEqual(target.read_bytes(), b'original')

    def test_deadline_leaves_only_unpublished_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'], stdout=subprocess.PIPE)
            try:
                with self.assertRaises(TimeoutError):
                    module.copy_verified_stream(process.stdout, Path(directory) / 'image.img',
                        size=0, digest=hashlib.sha256(b'').hexdigest(), deadline=time.monotonic() + .05)
                self.assertFalse((Path(directory) / 'image.img').exists())
            finally:
                process.terminate(); process.communicate(timeout=5)

if __name__ == '__main__':
    unittest.main()
