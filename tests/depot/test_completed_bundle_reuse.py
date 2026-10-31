#!/usr/bin/env python3
"""Exercise cache admission using an actual completed Flatpak bundle.

Run as root with --bundle PATH --sha256 INDEPENDENT_SHA; the bundle's actual
identity supplies the positive fixture, never an invented baseline manifest.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


class CompletedBundleTests(unittest.TestCase):
    def test_actual_bundle(self):
        self.assertEqual(self.module.prepare_single_bundle(self.bundle,self.ref,self.sha,self.heads)['bytes'],self.bundle.stat().st_size)

    def test_copy_readback(self):
        cache=self.module.prepare_single_bundle(self.bundle,self.ref,self.sha,self.heads)
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'copy.flatpak';self.module.copy_single_bundle(cache,target)
            with target.open('rb') as stream:self.assertEqual(hashlib.file_digest(stream,'sha256').hexdigest(),self.sha)

    def test_wrong_independent_hash(self):
        with self.assertRaises(ValueError):self.module.prepare_single_bundle(self.bundle,self.ref,'0'*64,self.heads)

    def test_wrong_ref(self):
        parts=self.ref.split('/');parts[1]+='.Foreign';other='/'.join(parts)
        with self.assertRaises(ValueError):self.module.prepare_single_bundle(self.bundle,other,self.sha,{other:self.commit})

    def test_wrong_commit(self):
        with self.assertRaises(ValueError):self.module.prepare_single_bundle(self.bundle,self.ref,self.sha,{self.ref:'0'*64})

    def test_wrong_architecture(self):
        parts=self.ref.split('/');parts[2]='x86_64' if parts[2]=='aarch64' else 'aarch64';other='/'.join(parts)
        with self.assertRaises(ValueError):self.module.prepare_single_bundle(self.bundle,other,self.sha,{other:self.commit})

    def test_truncated_body_with_its_own_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'truncated.flatpak'
            with self.bundle.open('rb') as stream:target.write_bytes(stream.read(1024))
            digest=hashlib.sha256(target.read_bytes()).hexdigest()
            with self.assertRaises(Exception):self.module.prepare_single_bundle(target,self.ref,digest,self.heads)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--sha256',required=True)
    args=parser.parse_args();assert os.geteuid()==0
    import gi
    gi.require_version('Flatpak','1.0')
    from gi.repository import Flatpak,Gio
    bundle=Flatpak.BundleRef.new(Gio.File.new_for_path(str(args.bundle)))
    helper=Path(__file__).resolve().parents[2]/'scripts/depot/build-offline-baseline.py'
    spec=importlib.util.spec_from_file_location('baseline',helper);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    CompletedBundleTests.module=module;CompletedBundleTests.bundle=args.bundle;CompletedBundleTests.sha=args.sha256
    CompletedBundleTests.ref=bundle.format_ref();CompletedBundleTests.commit=bundle.get_commit();CompletedBundleTests.heads={bundle.format_ref():bundle.get_commit()}
    unittest.main(argv=[__file__],verbosity=2)
