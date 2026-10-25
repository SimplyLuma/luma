#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise archive admission; synthetic signatures never claim authenticity."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/os/qa/viola-native/signed-flatpak-inputs.py'
SPEC = importlib.util.spec_from_file_location('signed_browser_inputs', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SignedBrowserInputTest(unittest.TestCase):
    def capsule(self, root, *, extra=None, mutate=None, member=None):
        refs = {ref: 'a' * 64 for ref in MODULE.REFS}
        files = {'repo/config': b'[core]\nrepo_version=1\nmode=archive-z2\n',
                 'repo/summary': b'synthetic summary', 'repo/summary.sig': b'synthetic signature',
                 'site/luma.flatpakrepo': b'synthetic descriptor'}
        files.update({'repo/refs/heads/' + ref: (commit + '\n').encode() for ref, commit in refs.items()})
        if extra:
            files.update(extra)
        profile = {'source': MODULE.SOURCE, 'architecture': 'aarch64', 'refs': refs,
                   'reachable_object_count': 0, 'reachable_stored_bytes': 0}
        for name, key in [('repo/summary', 'summary_sha256'), ('repo/summary.sig', 'summary_signature_sha256'),
                          ('site/luma.flatpakrepo', 'flatpakrepo_sha256')]:
            profile[key] = hashlib.sha256(files[name]).hexdigest()
        document = {'schema': 'org.projectluma.depot.runtime-input/v1', 'profile': profile,
                    'files': {name: {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
                              for name, body in files.items()}}
        if mutate:
            mutate(document)
        files['INPUTS.json'] = json.dumps(document).encode()
        archive = root / 'runtime-input.tar'
        with tarfile.open(archive, 'w') as tar:
            for name, body in files.items():
                row = tarfile.TarInfo(name); row.size = len(body)
                tar.addfile(row, io.BytesIO(body))
            if member:
                tar.addfile(member)
        return archive, MODULE.digest(archive)

    def test_byte_intake_accepts_inventory_without_claiming_signature_or_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive, digest = self.capsule(root)
            result = MODULE.validate(archive, root / 'mirror', digest)
            self.assertEqual(result['scope'], 'SIGNED_MIRROR_TRANSPORT_BYTES_NOT_RUNTIME_ACCEPTANCE')
            self.assertEqual(set(result['profile']['refs']), MODULE.REFS)

    def test_wrong_outer_digest_refused_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive, _ = self.capsule(root)
            with self.assertRaises(AssertionError):
                MODULE.validate(archive, root / 'mirror', '0' * 64)
            self.assertFalse((root / 'mirror').exists())

    def test_wider_ref_set_and_inventory_change_refused(self):
        mutations = [lambda d: d['profile']['refs'].update({'runtime/org.projectluma.Sdk/aarch64/44': 'b' * 64}),
                     lambda d: d['files']['repo/summary'].update({'sha256': 'b' * 64}),
                     lambda d: d['profile'].update({'source': 'b' * 40})]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); archive, digest = self.capsule(root, mutate=mutation)
                with self.assertRaises(AssertionError):
                    MODULE.validate(archive, root / 'mirror', digest)

    def test_paths_links_and_unselected_files_refused(self):
        for name, kind in [('../outside', tarfile.REGTYPE), ('repo/evil', tarfile.SYMTYPE),
                           ('repo/hard', tarfile.LNKTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                member = tarfile.TarInfo(name); member.type = kind; member.linkname = '/etc/passwd'
                archive, digest = self.capsule(root, member=member)
                with self.assertRaises(AssertionError):
                    MODULE.validate(archive, root / 'mirror', digest)
                self.assertFalse((root / 'outside').exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive, digest = self.capsule(root, extra={'repo/unselected': b'no'})
            with self.assertRaises(AssertionError):
                MODULE.validate(archive, root / 'mirror', digest)


if __name__ == '__main__':
    unittest.main()
