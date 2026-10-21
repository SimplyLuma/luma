#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A retry must not admit changed, mismatched or credential-bearing media."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('media_reuse', ROOT / 'scripts/os/lib/media_reuse.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MediaReuse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.iso = Path(self.tmp.name) / 'candidate.iso'
        self.iso.write_bytes(b'isolated media fixture')
        self.sha = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        self.fields = dict(channel='nightly', ref='luma/1/x86_64/nightly', commit='a'*64,
                           version='1.0.0-nightly.20261019.3', candidate_build='20261019.3',
                           atlas_source_revision='b'*40, release_key_fingerprint='C'*40,
                           preview_credential='none', test='', test_build='')
        self.sidecar = Path(str(self.iso) + '.sha256')
        self.sidecar.write_text(self.sha + '\n')
        self.release = Path(str(self.iso) + '.release')
        self.write_release()

    def write_release(self):
        self.release.write_text(''.join(f'{k}={v}\n' for k, v in self.fields.items()))

    def admit(self):
        module.admit(self.iso, self.sha, 'nightly', 'luma/1/x86_64/nightly', 'a'*64,
                     '1.0.0-nightly.20261019.3', '20261019.3', 'b'*40, 'C'*40)

    def test_exact_candidate(self):
        self.admit()

    def test_changed_iso_and_sidecar_cannot_replace_explicit_digest(self):
        self.iso.write_bytes(b'changed fixture')
        with self.assertRaises(ValueError): self.admit()
        self.sidecar.write_text(hashlib.sha256(self.iso.read_bytes()).hexdigest())
        with self.assertRaises(ValueError): self.admit()

    def test_mismatched_release_inputs_and_credentials(self):
        for name in self.fields:
            with self.subTest(field=name):
                original = self.fields[name]
                self.fields[name] = 'different'
                self.write_release()
                with self.assertRaises(ValueError): self.admit()
                self.fields[name] = original

    def test_duplicate_field_and_symlink(self):
        with self.release.open('a') as f: f.write('test=\n')
        with self.assertRaises(ValueError): self.admit()
        self.write_release()
        original = self.iso.with_suffix('.original')
        self.iso.rename(original)
        self.iso.symlink_to(original)
        with self.assertRaises(ValueError): self.admit()

    def test_codec_route_requires_the_current_official_public_address(self):
        self.assertEqual(module.codec_endpoint('38.145.32.20', ['38.145.32.20']),
                         ('codecs.fedoraproject.org', '38.145.32.20'))
        with self.assertRaises(ValueError):
            module.codec_endpoint('38.145.32.20', ['38.145.32.21'])

    def test_codec_route_refuses_private_reserved_and_shell_input(self):
        for value in ('127.0.0.1', '10.0.0.1', '0.0.0.0', '169.254.1.1',
                      '192.0.2.1', '224.0.0.1', '::1', '38.145.32.20;id'):
            with self.subTest(address=value), self.assertRaises(ValueError):
                module.codec_endpoint(value, [value])


if __name__ == '__main__':
    unittest.main()
