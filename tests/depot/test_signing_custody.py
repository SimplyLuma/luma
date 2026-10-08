# SPDX-License-Identifier: Apache-2.0
"""Actual filesystem custody controls; no signing key is used by these tests."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('publisher_control', SOURCE / 'scripts/depot/seal-signing-control.py')
control = importlib.util.module_from_spec(spec); spec.loader.exec_module(control)

@unittest.skipUnless(os.geteuid() == 0, 'root-owned custody checks require the qualified root builder')
class Custody(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='luma-publisher-custody-', dir='/root'))
        self.source = self.base / 'input'; self.source.mkdir(mode=0o700)
        (self.source / 'actual-object').write_bytes(b'immutable producer bytes\n')
        self.target = self.base / 'sealed'
        self.digest = control.seal(self.source, self.target, inputs=True)
    def tearDown(self): shutil.rmtree(self.base)
    def verify(self): return control.verify(self.target, self.digest, 'org.projectluma.signing-inputs/v1')
    def test_actual_snapshot_positive_and_no_overwrite(self):
        self.verify()
        with self.assertRaises(ValueError): control.seal(self.source, self.target, inputs=True)
    def test_empty_ostree_directories_preserved_and_required(self):
        other = self.base / 'with-empty'; other.mkdir(mode=0o700)
        (other / 'repo/tmp').mkdir(parents=True)
        (other / 'repo/config').write_text('[core]\nmode=archive\n')
        target = self.base / 'empty-sealed'
        digest = control.seal(other, target, inputs=True)
        control.verify(target, digest, 'org.projectluma.signing-inputs/v1')
        self.assertTrue((target / 'repo/tmp').is_dir())
        (target / 'repo/tmp').rmdir()
        with self.assertRaises(ValueError): control.verify(target, digest, 'org.projectluma.signing-inputs/v1')
    def test_changed_code_or_input_fails(self):
        (self.target / 'actual-object').write_bytes(b'execute stolen-key copy\n')
        with self.assertRaises(ValueError): self.verify()
    def test_changed_manifest_digest_fails(self):
        (self.target / control.MANIFEST).write_text('{}')
        with self.assertRaises(ValueError): self.verify()
    def test_undeclared_member_fails(self):
        (self.target / 'new-publisher.py').write_text('unreviewed code')
        with self.assertRaises(ValueError): self.verify()
    def test_symlink_member_fails(self):
        path = self.target / 'actual-object'; path.unlink(); path.symlink_to(self.source / 'actual-object')
        with self.assertRaises(ValueError): self.verify()
    def test_writable_parent_fails(self):
        self.base.chmod(0o770)
        with self.assertRaises(ValueError): self.verify()
    def test_wrong_owner_fails(self):
        os.chown(self.target / 'actual-object', 65534, 65534)
        with self.assertRaises(ValueError): self.verify()

if __name__ == '__main__': unittest.main()
