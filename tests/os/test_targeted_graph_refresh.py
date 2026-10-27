# SPDX-License-Identifier: Apache-2.0
"""Real protected synthetic producer receipts; no release is qualified here."""
import importlib.util
import os
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
approved=module('approved_targeted_refresh','scripts/os/lib/approved_graph.py')
fixture=module('targeted_refresh_fixture','tests/os/test_targeted_desktop_gate.py')

@unittest.skipUnless(os.geteuid()==0,'real protected receipt boundary requires root')
class TargetedRefresh(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TargetedAdmission('test_explicit_reuse_keeps_actual_checks')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.release=dict(commit=self.f.new,version='1.0.0-nightly.fixture',released_at='2026-10-27T00:00:00Z',paused=False)
        self.manifest=dict(commit=self.f.new,version=self.release['version'],published_utc=self.release['released_at'],
            channel='nightly',ref='luma/1/aarch64/nightly',architecture='aarch64',parent=self.f.old,
            source_revision='c'*40,gate={'result':'pass'},
            provenance_sha256=self.f.record['proofs']['candidate-provenance']['sha256'])
        self.delivery=dict(schema='org.projectluma.os-public-delivery/v1',commit=self.f.new,
            complete_closure_readback=True,commit_signature_verified=True,summary_signature_verified=True,
            repository_url='https://dl.simplyluma.com/os/repo')
    def admit(self,**changes):
        args=dict(release=self.release,manifest=self.manifest,delivery=self.delivery,gate=self.f.record,
            public_repository_url='https://dl.simplyluma.com/os/repo',
            provenance=self.f.docs['candidate-provenance'],architecture='aarch64')
        args.update(changes);return approved.admitted_release(**args)
    def test_actual_sealed_targeted_proofs_are_admitted_without_relabel(self):
        self.admit();self.assertEqual(self.f.record['fresh'],'reused')
    def test_missing_producer_rejected(self):
        del self.f.record['proofs']['guest']
        with self.assertRaises(ValueError):self.admit()
    def test_changed_proof_body_rejected(self):
        p=Path(self.f.record['proofs']['guest']['path']);p.chmod(0o600);p.write_bytes(b'changed');p.chmod(0o400)
        with self.assertRaises(ValueError):self.admit()
    def test_parent_architecture_and_provenance_must_match(self):
        for change in ({'parent':'d'*64},{'provenance_sha256':'e'*64}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.admit(manifest=self.manifest|change)
        with self.assertRaises(ValueError):self.admit(architecture='x86_64')
    def test_targeted_admission_does_not_replace_delivery(self):
        for change in ({'complete_closure_readback':False},{'repository_url':'https://private.invalid/repo'}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.admit(delivery=self.delivery|change)
    def test_reused_string_without_exact_schema_still_rejected(self):
        self.f.record['schema']='unrecognized'
        with self.assertRaises(ValueError):self.admit()

if __name__=='__main__':unittest.main()
