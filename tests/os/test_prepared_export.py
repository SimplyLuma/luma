#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Authenticate native export evidence and actual OSTree commit identities."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('prepared_export', ROOT / 'scripts/os/lib/prepared_export.py')
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)

class PreparedExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'build'
        self.directory.mkdir()
        self.values = {
            'LUMA_BUILD_ID': '20261026.1', 'LUMA_OS_ARCH': 'aarch64',
            'LUMA_OS_REF_PREFIX': 'luma/1/aarch64', 'LUMA_OS_PROFILE': 'arm-preview',
            'LUMA_OS_VERSION': '1.0.0-nightly.20261026.1', 'LUMA_OS_CHANNEL': 'nightly',
            'LUMA_SOURCE_REVISION': 'a' * 40, 'LUMA_SOURCE_DIRTY': 'false',
            'LUMA_SOURCE_SNAPSHOT_SHA256': '', 'LUMA_RELEASE_CHECKS_SHA256': '',
            'LUMA_IMAGE_ID': 'b' * 64, 'LUMA_BASE_IMAGE': 'example.invalid/base@sha256:' + 'c' * 64,
        }
        self.inventory = 'example\t0\t1\t1\taarch64\n'
        for name in export.FILES:
            (self.directory / name).write_text('{}\n')
        (self.directory / 'packages-installed.tsv').write_text(self.inventory)
        (self.directory / 'image-modes.txt').write_text('4111 /usr/bin/sudo\n')
        self.write_env()
        self.provenance = {
            'build_id': self.values['LUMA_BUILD_ID'], 'architecture': 'aarch64',
            'version': self.values['LUMA_OS_VERSION'], 'channel': 'nightly',
            'source': {'revision': 'a' * 40, 'dirty': False}, 'image': {'id': 'b' * 64},
            'installed_packages': {'inventory_sha256': export.sha(self.directory / 'packages-installed.tsv')},
            'sbom': {'sha256': export.sha(self.directory / 'sbom.spdx.json')},
        }
        self.tree = 'd' * 64
        self.write_json('provenance.json', self.provenance)
        self.write_json('image-import-metadata.json', {'image_id': 'b' * 64, 'raw_import': self.tree})
        self.doc = self.capture()
        self.save_receipt()

    def write_json(self, name, value):
        (self.directory / name).write_text(json.dumps(value))

    def write_env(self):
        (self.directory / 'build.env').write_text(''.join(key + '=' + value + '\n' for key, value in self.values.items()))

    def capture(self, candidate='e' * 64, parent=''):
        return export.receipt(self.directory, candidate, self.tree, parent,
                              'luma/1/aarch64/nightly', '7.2.11.aarch64', 'f' * 64)

    def save_receipt(self):
        self.write_json('export-prepared.json', self.doc)
        self.digest = export.sha(self.directory / 'export-prepared.json')

    def verify(self, **kwargs):
        return export.verify(self.directory, kwargs.get('digest', self.digest),
            kwargs.get('source', 'a' * 40), kwargs.get('image', 'b' * 64), kwargs.get('parent', ''))

    def test_exact_unsigned_receipt_is_not_release_success(self):
        self.assertEqual(self.verify(), self.doc)
        self.assertEqual(self.doc['result'], 'PREPARED_UNSIGNED_NOT_RELEASE_QUALIFIED')
        self.assertFalse((self.directory / 'export.env').exists())

    def test_receipt_digest_and_authenticated_identities_must_match(self):
        for values in ({'digest': '0' * 64}, {'source': '0' * 40},
                       {'image': '0' * 64}, {'parent': '0' * 64}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.verify(**values)

    def test_each_bound_file_rejects_tampering(self):
        for name in export.FILES:
            path = self.directory / name
            body = path.read_bytes()
            path.write_bytes(body + b'\n')
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify()
            path.write_bytes(body)

    def test_environment_cannot_choose_signing_paths_or_duplicate_fields(self):
        path = self.directory / 'build.env'
        body = path.read_text()
        for line in ('LUMA_OS_KEYS=/attacker\n', 'LUMA_BUILD_ID=20261026.1\n'):
            path.write_text(body + line)
            with self.subTest(line=line), self.assertRaises(ValueError):
                export.build_env(path)
        path.write_text(body)

    def test_private_source_is_not_eligible_for_remote_signing(self):
        for key, value in [('LUMA_SOURCE_DIRTY', 'true'),
                           ('LUMA_SOURCE_SNAPSHOT_SHA256', '0' * 64),
                           ('LUMA_RELEASE_CHECKS_SHA256', '0' * 64)]:
            old = self.values[key]
            self.values[key] = value
            self.write_env()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.capture()
            self.values[key] = old
        self.write_env()

    def test_provenance_and_import_must_describe_exact_build(self):
        self.write_json('image-import-metadata.json', {'image_id': '0' * 64, 'raw_import': self.tree})
        with self.assertRaises(ValueError):
            self.capture()
        self.write_json('image-import-metadata.json', {'image_id': 'b' * 64, 'raw_import': self.tree})
        self.provenance['architecture'] = 'x86_64'
        self.write_json('provenance.json', self.provenance)
        with self.assertRaises(ValueError):
            self.capture()

    def make_commit(self, overrides=None, parent=''):
        repo = Path(self.temp.name) / 'repo'
        if not repo.exists():
            subprocess.run(['ostree', 'init', '--repo=' + str(repo), '--mode=archive'], check=True)
        tree = Path(self.temp.name) / 'tree'
        tree.mkdir(exist_ok=True)
        (tree / 'payload').write_text('actual native test payload\n')
        metadata = {
            'version': self.values['LUMA_OS_VERSION'],
            'org.projectluma.build-id': '20261026.1',
            'org.projectluma.build-version': self.values['LUMA_OS_VERSION'],
            'org.projectluma.channel': 'nightly',
            'org.projectluma.source-revision': 'a' * 40,
            'org.projectluma.source-dirty': 'false',
            'org.projectluma.architecture': 'aarch64',
            'org.projectluma.base-image-digest': 'sha256:' + 'c' * 64,
            'org.projectluma.provenance-sha256': export.sha(self.directory / 'provenance.json'),
            'org.projectluma.sbom-sha256': export.sha(self.directory / 'sbom.spdx.json'),
            'org.projectluma.image-tree': self.tree,
        }
        metadata.update(overrides or {})
        args = ['ostree', 'commit', '--repo=' + str(repo), '--orphan',
                '--tree=dir=' + str(tree), '--subject=native export test',
                '--bind-ref=luma/1/aarch64/nightly',
                '--add-metadata=rpmostree.rpmdb.pkglist=@a(sssss) [(\'example\', \'0\', \'1\', \'1\', \'aarch64\')]']
        if parent:
            args.append('--parent=' + parent)
        args.extend('--add-metadata-string=' + key + '=' + value for key, value in metadata.items())
        candidate = subprocess.check_output(args, text=True).strip()
        return repo, candidate

    @unittest.skipUnless(shutil.which('ostree'), 'Requires actual native OSTree')
    def test_actual_ostree_commit_metadata_passes_then_wrong_arch_parent_fail(self):
        repo, candidate = self.make_commit()
        doc = self.capture(candidate=candidate)
        self.assertEqual(export.verify_commit(repo, self.directory, doc)['result'], 'PASS_NATIVE_CANDIDATE_METADATA')
        for overrides in ({'org.projectluma.architecture': 'x86_64'},
                          {'org.projectluma.source-revision': '0' * 40},
                          {'org.projectluma.provenance-sha256': '0' * 64}):
            _repo, other = self.make_commit(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                export.verify_commit(repo, self.directory, self.capture(candidate=other))
        _repo, child = self.make_commit(parent=candidate)
        with self.assertRaises(ValueError):
            export.verify_commit(repo, self.directory, self.capture(candidate=child))

    def test_signer_refuses_symlink_or_writable_input_before_loading_environment(self):
        path = self.directory / 'build.env'
        path.chmod(0o666)
        with self.assertRaises(ValueError):
            export.require_private_inputs(self.directory)
        path.chmod(0o600)
        original = self.directory / 'env-original'
        path.rename(original)
        path.symlink_to(original.name)
        with self.assertRaises(ValueError):
            export.require_private_inputs(self.directory)

if __name__ == '__main__':
    unittest.main()
