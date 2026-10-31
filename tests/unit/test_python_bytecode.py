# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/os/lib/prepare_python_bytecode.py'


class BytecodeTests(unittest.TestCase):
    def test_cache_survives_export_timestamp_and_invalidates_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.py'
            source.write_text('value = 41\n')
            subprocess.run([sys.executable, str(SCRIPT), directory], check=True, capture_output=True)
            cache = Path(importlib.util.cache_from_source(str(source)))
            first = cache.read_bytes()
            self.assertEqual(struct.unpack('<I', first[4:8])[0], 3)
            source.touch()
            import os
            os.utime(source, (0, 0))
            env = dict(os.environ, PYTHONPATH=directory)
            result = subprocess.run([sys.executable, '-c', 'import sample;print(sample.value)'],
                env=env, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), '41')
            self.assertEqual(cache.read_bytes(), first)
            source.write_text('value = 42\n')
            os.utime(source, (0, 0))
            result = subprocess.run([sys.executable, '-c', 'import sample;print(sample.value)'],
                env=env, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), '42')


if __name__ == '__main__':
    unittest.main()
