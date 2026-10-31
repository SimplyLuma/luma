#!/usr/bin/env python3
"""Adapter unit contracts, never an actual guest qualification."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[2] / 'scripts/os/lib/server_installed_runner.py'
spec = importlib.util.spec_from_file_location('server_runner', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class RunnerPreparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / 'original.py'
        self.source = """# unit original, no actual guest behavior
ssh=['ssh','-n','-T']
try:
 copy(w/'installed-checks','/var/tmp/nightly-installed-checks/')
 subprocess.run(ssh+['guest-checks'],timeout=1800)
 assert real_guest_result=='PASS'
 qpath=out/'nightly-installed-results/result.json'
 # historical Mac metadata below extraction boundary must disappear
 native_mac_vm='unit-old-mac'
finally:
 pass
"""
        self.original.write_text(self.source)
        self.digest = hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.out = self.root / 'prepared.py'

    def test_byte_preserved_behavior_adapter_only(self):
        proof = m.prepare_runner(self.original, self.digest, self.out)
        result = self.out.read_text()
        ast.parse(result)
        self.assertIn('assert real_guest_result', result)
        self.assertIn('timeout=1800', result)
        self.assertNotIn('native_mac_vm', result)
        self.assertIn(m.NEW_COPY, result)
        self.assertEqual(proof['result'], 'PREPARED_ONLY_NOT_GUEST_EXECUTED')
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o400)

    def test_wrong_frozen_source_refused(self):
        with self.assertRaises(ValueError): m.prepare_runner(self.original, '0' * 64, self.out)
        self.assertFalse(self.out.exists())

    def test_missing_exact_boundaries_refused(self):
        self.original.write_text(self.source.replace('timeout=1800', 'timeout=1801'))
        digest = hashlib.sha256(self.original.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): m.prepare_runner(self.original, digest, self.out)

    def test_overwrite_refused(self):
        self.out.write_text('preserve prior evidence')
        with self.assertRaises(FileExistsError): m.prepare_runner(self.original, self.digest, self.out)
        self.assertEqual(self.out.read_text(), 'preserve prior evidence')

    def test_mac_fields_inside_behavior_refused(self):
        self.original.write_text(self.source.replace(" assert real_guest_result", " native_mac_vm='old'\n assert real_guest_result"))
        digest = hashlib.sha256(self.original.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): m.prepare_runner(self.original, digest, self.out)


if __name__ == '__main__':
    unittest.main()
