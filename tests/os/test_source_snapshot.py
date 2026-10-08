#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

FILE = Path(__file__).resolve().parents[2] / 'scripts/os/lib/source_snapshot.py'
spec = importlib.util.spec_from_file_location('source_snapshot', FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
REV = 'a' * 40


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luma-private-source-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'source'
        self.root.mkdir()
        for name in (*m.REQUIRED, 'tests/os/gate/current.sh', 'src/app.py'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('actual-current-source\n')
        (self.root / '.git').mkdir()
        (self.root / '.git' / 'HEAD').write_text('git-metadata-excluded\n')
        self.snapshot = self.base / 'SOURCE-SNAPSHOT.json'
        self.data = {'mode': 'private-dirty-candidate', 'base_revision': REV,
                     'files': m.observed(self.root)}
        self.save()
        self.bundle = self.base / 'bundle'
        self.provenance = self.base / 'provenance.json'

    def save(self):
        self.snapshot.write_text(json.dumps(self.data, sort_keys=True))

    def capture(self):
        result = m.capture(self.root, self.snapshot, REV, self.bundle)
        self.provenance.write_text(json.dumps({'source': {'revision': REV, 'dirty': True,
            'private': True, 'snapshot_sha256': result['source_snapshot_sha256'],
            'release_checks_sha256': result['release_checks_sha256']}}))
        return result

    def admit(self, hashes):
        return m.admit(self.bundle, REV, hashes['source_snapshot_sha256'],
                       hashes['release_checks_sha256'], self.provenance)

    def test_current_dirty_checks_admitted_without_reading_base_git(self):
        hashes = self.capture()
        self.assertEqual((self.admit(hashes) / 'tests/os/gate/current.sh').read_text(), 'actual-current-source\n')
        self.assertFalse((self.bundle / 'tree/src').exists())

    def test_full_source_change_outside_check_subset_refused(self):
        (self.root / 'src/app.py').write_text('bad-current-source\n')
        with self.assertRaisesRegex(ValueError, 'snapshot differs'):
            self.capture()

    def test_extra_untracked_file_refused(self):
        (self.root / 'extra.py').write_text('not-in-snapshot\n')
        with self.assertRaisesRegex(ValueError, 'snapshot differs'):
            self.capture()

    def test_changed_executable_mode_refused(self):
        (self.root / 'src/app.py').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'snapshot differs'):
            self.capture()

    def test_symlink_substitution_refused(self):
        p = self.root / 'src/app.py'
        p.unlink(); p.symlink_to('../config/os/release.env')
        with self.assertRaisesRegex(ValueError, 'snapshot differs'):
            self.capture()

    def test_wrong_revision_refused(self):
        with self.assertRaisesRegex(ValueError, 'not this private'):
            m.validate(self.root, self.snapshot, 'b' * 40)

    def test_empty_gate_capture_refused(self):
        p = self.root / 'tests/os/gate/current.sh'
        p.unlink(); self.data['files'].pop('tests/os/gate/current.sh'); self.save()
        with self.assertRaisesRegex(ValueError, 'no release gate'):
            self.capture()

    def test_duplicate_json_key_refused(self):
        self.snapshot.write_text('{"mode":"private-dirty-candidate","mode":"public"}')
        with self.assertRaisesRegex(ValueError, 'duplicate manifest key'):
            self.capture()

    def test_traversal_refused(self):
        self.data['files']['../outside'] = self.data['files']['src/app.py']; self.save()
        with self.assertRaisesRegex(ValueError, 'unsafe manifest path'):
            self.capture()

    def test_root_owned_protection_refuses_group_writable_source(self):
        self.root.chmod(0o775)
        with self.assertRaisesRegex(ValueError, 'not root-owned and protected'):
            self.capture()

    def test_wrong_full_snapshot_digest_refused(self):
        self.capture()
        with self.assertRaisesRegex(ValueError, 'digest changed'):
            m.validate(self.root, self.snapshot, REV, '0' * 64)

    def test_retained_check_bytes_tamper_refused(self):
        hashes = self.capture()
        (self.bundle / 'tree/tests/os/gate/current.sh').write_text('base-source-checks\n')
        with self.assertRaisesRegex(ValueError, 'do not match'):
            self.admit(hashes)

    def test_retained_check_mode_tamper_refused(self):
        hashes = self.capture()
        (self.bundle / 'tree/tests/os/gate/current.sh').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'do not match'):
            self.admit(hashes)

    def test_provenance_false_dirty_or_wrong_digest_refused(self):
        hashes = self.capture()
        original = json.loads(self.provenance.read_text())
        for field, value in [('dirty', False), ('snapshot_sha256', '0' * 64),
                             ('release_checks_sha256', '1' * 64), ('private', False)]:
            with self.subTest(field=field):
                data = json.loads(json.dumps(original)); data['source'][field] = value
                self.provenance.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'provenance does not bind'):
                    self.admit(hashes)

    def test_real_linux_control_character_filename_preserved(self):
        (self.root / '\x01').write_bytes(b'')
        self.data['files'] = m.observed(self.root); self.save()
        hashes = self.capture()
        self.assertTrue(self.admit(hashes).exists())
        self.assertIn('\x01', m.load(self.bundle / 'SOURCE-SNAPSHOT.json')['files'])

    def test_manifest_declared_symlink_escape_refused(self):
        (self.root / 'outside-link').symlink_to('../SOURCE-SNAPSHOT.json')
        with self.assertRaisesRegex(ValueError, 'symlink escapes'):
            m.observed(self.root)

    def test_snapshot_metadata_symlink_refused(self):
        target = self.base / 'manifest-real.json'
        self.snapshot.rename(target); self.snapshot.symlink_to(target.name)
        with self.assertRaisesRegex(ValueError, 'symlink ancestor/metadata'):
            self.capture()

    def test_provenance_metadata_symlink_refused(self):
        hashes = self.capture()
        target = self.base / 'provenance-real.json'
        self.provenance.rename(target); self.provenance.symlink_to(target.name)
        with self.assertRaisesRegex(ValueError, 'symlink ancestor/metadata'):
            self.admit(hashes)

    def test_retained_bundle_metadata_symlink_refused(self):
        hashes = self.capture()
        metadata = self.bundle / 'RELEASE-CHECKS.json'
        target = self.bundle / 'checks-real.json'
        metadata.rename(target); metadata.symlink_to(target.name)
        with self.assertRaisesRegex(ValueError, 'symlink ancestor/metadata'):
            self.admit(hashes)

    def test_symlink_source_ancestor_refused(self):
        alias = self.base / 'alias'; alias.symlink_to(self.root.name)
        with self.assertRaisesRegex(ValueError, 'symlink ancestor/metadata'):
            m.validate(alias, self.snapshot, REV)

    def test_writable_capture_parent_refused_before_creation(self):
        parent = self.base / 'unsafe'; parent.mkdir(); parent.chmod(0o775)
        output = parent / 'capture'
        with self.assertRaisesRegex(ValueError, 'not root-owned and protected'):
            m.capture(self.root, self.snapshot, REV, output)
        self.assertFalse(output.exists())

    def test_existing_admission_never_overwritten(self):
        self.capture()
        with self.assertRaises(FileExistsError):
            m.capture(self.root, self.snapshot, REV, self.bundle)


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('These private-release ownership controls must run as root in an isolated fixture.')
    unittest.main()
