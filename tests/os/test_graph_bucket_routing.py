# SPDX-License-Identifier: Apache-2.0
"""Routing contracts; no keys, signing, network or publication."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
buckets=load('graph_buckets','scripts/os/lib/graph_bucket.py')
entry=load('signer_entry','scripts/os/graph-signer-entry.py')

class BucketRouting(unittest.TestCase):
    def test_desktop_canonical_and_legacy_roots(self):
        x86=buckets.GraphBucket()
        self.assertEqual(x86.directories('/graph'),[Path('/graph/x86_64'),Path('/graph')])
        self.assertEqual(x86.public_prefixes(),['os/graph/x86_64','os/graph'])
        arm=buckets.GraphBucket('aarch64')
        self.assertEqual(arm.directories('/graph'),[Path('/graph/aarch64')])
        self.assertEqual(arm.public_prefixes(),['os/graph/aarch64'])

    def test_devices_same_arch_have_distinct_graph_release_and_unit_paths(self):
        targets=[buckets.GraphBucket('aarch64',target) for target in ('fairphone,fp6','unihertz,titan2e')]
        for bucket in targets:
            self.assertEqual(bucket.releases('/repo'),Path('/repo/luma/targets/aarch64')/bucket.slug/'releases')
            self.assertEqual(bucket.public_prefix(),'os/graph/aarch64/'+bucket.slug)
            self.assertEqual(bucket.ref('nightly'),'luma/1/aarch64/'+bucket.slug+'/nightly')
        self.assertNotEqual(targets[0].unit_name('nightly'),targets[1].unit_name('nightly'))
        self.assertNotEqual(targets[0].directory('/graph'),targets[1].directory('/graph'))

    def test_wrong_arch_and_unregistered_targets_refused(self):
        for arch,target in [('x86_64','fairphone,fp6'),('aarch64','../fp6'),('aarch64','fairphone,fp5')]:
            with self.subTest(arch=arch,target=target),self.assertRaises(ValueError):buckets.GraphBucket(arch,target)

    def test_cross_device_and_generic_graphs_refused(self):
        bucket=buckets.GraphBucket('aarch64','fairphone,fp6')
        for target in ('','unihertz,titan2e'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                bucket.validate_graph(dict(channel='nightly',arch='aarch64',hardware_target=target),'nightly')
        with self.assertRaises(ValueError):
            buckets.GraphBucket('aarch64').validate_graph(dict(channel='nightly',arch='aarch64',hardware_target='fairphone,fp6'),'nightly')

    def test_signature_comment_binds_exact_hardware_target(self):
        bucket=buckets.GraphBucket('aarch64','fairphone,fp6')
        graph=dict(schema_version=2,channel='nightly',arch='aarch64',hardware_target='fairphone,fp6',generated_at='2026-10-24T00:00:00Z')
        self.assertEqual(bucket.comment(graph,'fixture'),'luma-update-graph channel=nightly arch=aarch64 generated_at=2026-10-24T00:00:00Z key=fixture hardware_target=fairphone,fp6')

    def test_manifest_bucket_is_explicit_and_version_cannot_traverse(self):
        bucket=buckets.GraphBucket('aarch64','fairphone,fp6')
        base=dict(channel='nightly',architecture='aarch64',hardware_target='fairphone,fp6',ref=bucket.ref('nightly'))
        bucket.validate_manifest(base)
        for change in ({'ref':'luma/1/aarch64/nightly'},{'hardware_target':'unihertz,titan2e'},{'architecture':'x86_64'}):
            with self.subTest(change=change),self.assertRaises(ValueError):bucket.validate_manifest({**base,**change})
        with self.assertRaises(ValueError):bucket.release_manifest('/repo','../../other')

    def test_entry_infers_device_arch_without_inheriting_ambient_target(self):
        self.assertEqual(entry.selection(ROOT).architecture,'x86_64')
        self.assertEqual(entry.selection(ROOT,hardware_target='fairphone,fp6').architecture,'aarch64')
        with self.assertRaises(ValueError):entry.selection(ROOT,'x86_64','arm-preview')

    def test_actual_bucket_cli_has_one_arch_component(self):
        for arch,target in [('x86_64',''),('aarch64',''),('aarch64','fairphone,fp6')]:
            argv=[sys.executable,'-B',str(ROOT/'scripts/os/lib/graph_bucket.py'),'--arch',arch,'--hardware-target',target,'/fixture/graph']
            result=subprocess.check_output(argv,text=True).splitlines()
            self.assertEqual(result,[str(path) for path in buckets.GraphBucket(arch,target).directories('/fixture/graph')])
            self.assertEqual(result[0].split('/').count(arch),1)

if __name__=='__main__':unittest.main()
