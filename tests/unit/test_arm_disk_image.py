# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('arm_disk_image', ROOT / 'scripts/os/lib/arm_disk_image.py')
disk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(disk)


class ArmDiskImage(unittest.TestCase):
    def test_unsparse_restores_img2simg_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, sparse, back = (Path(tmp, n) for n in ('raw', 'sparse', 'back'))
            raw.write_bytes(bytes(8192) + os.urandom(4096) + bytes(4096) + b'\x01' * 4096)
            subprocess.run([sys.executable, ROOT / 'scripts/mobile/img2simg.py', raw, sparse], check=True)
            disk.unsparse(sparse, back)
            self.assertEqual(back.read_bytes(), raw.read_bytes())

    def test_entry_requires_a_passing_gate_for_the_manifest_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = 'luma-nightly-20261110.2-aarch64-disk.sparse'
            image = Path(tmp, name)
            image.write_bytes(b'sparse')
            Path(tmp, name + '.sha256').write_text(f'{disk.sha256(image)}  {name}\n')
            Path(tmp, name + '.manifest').write_text(
                f'commit={"c" * 64}\nversion=44.20261110.2\nref=luma:luma/1/aarch64/nightly\norigin: [origin]\n')
            gate = Path(tmp, 'gate.json')
            gate.write_text(json.dumps({'result': 'pass', 'commit': 'c' * 64, 'checks': {}}))
            row = disk.entry(image, 'https://media.simplyluma.com/media/aarch64/nightly/', gate)
            self.assertEqual(row['download_url'], 'https://media.simplyluma.com/media/aarch64/nightly/' + name)
            self.assertEqual(row['manifest_file'], 'luma-nightly-20261110.2-aarch64-disk.manifest.txt')
            self.assertEqual((row['commit'], row['size_bytes']), ('c' * 64, 6))
            gate.write_text(json.dumps({'result': 'pass', 'commit': 'd' * 64}))
            with self.assertRaises(SystemExit):
                disk.entry(image, 'https://media.simplyluma.com/media/aarch64/nightly/', gate)


if __name__ == '__main__':
    unittest.main()
