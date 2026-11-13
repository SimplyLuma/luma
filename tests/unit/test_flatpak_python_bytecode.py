# SPDX-License-Identifier: Apache-2.0
"""Flatpak apps and the Luma Platform runtime ship hash-checked Python bytecode.

OSTree exports every file with mtime 0, so timestamp-checked caches never
match their sources; with /app and /usr read-only, each launch would recompile
every module it imports.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / 'packaging/flatpak/apps'
RUNTIME = ROOT / 'packaging/flatpak/runtime/org.projectluma.Platform.yaml'
COMPILE = 'python3 -m compileall -q -f -j0 --invalidation-mode checked-hash /app/lib/python3.14/site-packages'


def build_commands(manifest):
    data = yaml.safe_load(manifest.read_text())
    return [c for m in data.get('modules', []) if isinstance(m, dict) for c in m.get('build-commands', [])]


class ManifestTests(unittest.TestCase):
    def test_apps_compile_site_packages_after_the_last_write(self):
        checked = 0
        for manifest in sorted(APPS.glob('*/*.yml')):
            commands = build_commands(manifest)
            writes = [i for i, c in enumerate(commands) if 'site-packages' in c and 'compileall' not in c]
            if not writes:
                continue
            with self.subTest(manifest.name):
                self.assertIn(COMPILE, commands)
                self.assertGreater(commands.index(COMPILE), max(writes))
            checked += 1
        self.assertGreater(checked, 0)

    def test_runtime_compiles_last(self):
        cleanup = yaml.safe_load(RUNTIME.read_text())['cleanup-append']
        compile_at = cleanup.rindex('--invalidation-mode checked-hash')
        self.assertGreater(compile_at, cleanup.rindex('PY_TV'), 'imports after the compile leave timestamp caches')
        self.assertIn("hash-checked Python bytecode PASS", cleanup[compile_at:])


class CacheTests(unittest.TestCase):
    def test_checked_hash_cache_survives_a_zero_mtime_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.py'
            source.write_text('value = 41\n')
            subprocess.run([sys.executable, '-m', 'compileall', '-q', '-f', '--invalidation-mode', 'checked-hash',
                            directory], check=True)
            cache = Path(importlib.util.cache_from_source(str(source)))
            first = cache.read_bytes()
            self.assertTrue(int.from_bytes(first[4:8], 'little') & 1)
            os.utime(source, (0, 0))
            env = dict(os.environ, PYTHONPATH=directory)
            run = lambda: subprocess.run([sys.executable, '-c', 'import sample; print(sample.value)'],
                                         env=env, text=True, capture_output=True, check=True).stdout.strip()
            self.assertEqual(run(), '41')
            self.assertEqual(cache.read_bytes(), first, 'a valid cache is not rewritten')
            source.write_text('value = 42\n')
            os.utime(source, (0, 0))
            self.assertEqual(run(), '42', 'a changed source still wins')


if __name__ == '__main__':
    unittest.main()
