#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real bundle/copy custody checks; current signed-ref admission stays separate."""
import base64
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('baseline_producer',ROOT/'scripts/depot/build-offline-baseline.py')
PRODUCER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(PRODUCER)


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


class Reuse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Exercise the actual maintained root ownership checks rather than
        # replacing them. This tiny unsigned bundle tests identity/custody;
        # it never claims current summary or commit signature acceptance.
        if os.geteuid()!=0:raise RuntimeError('Run these boundary tests in the admitted root builder')
        cls.workspace=tempfile.TemporaryDirectory(prefix='baseline-reuse-test-')
        cls.work=Path(cls.workspace.name);repo=cls.work/'repo';tree=cls.work/'tree';tree.mkdir()
        (tree/'files').mkdir();(tree/'files/payload').write_bytes(b'real tiny payload\n')
        (tree/'metadata').write_text('[Application]\nname=org.projectluma.Clock\nruntime=org.projectluma.Platform/x86_64/44\n')
        cls.ref='app/org.projectluma.Clock/x86_64/beta'
        def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=30).strip()
        run('ostree','init','--mode=archive-z2','--repo='+str(repo))
        cls.commit=run('ostree','commit','--repo='+str(repo),'--branch='+cls.ref,'--tree=dir='+str(tree),'--subject=reuse-test')
        cls.bundle=cls.work/'real.flatpak'
        run('flatpak','build-bundle','--arch=x86_64',str(repo),str(cls.bundle),'org.projectluma.Clock','beta')
        config=configparser.ConfigParser();config.read(ROOT/'config/os/flatpak/luma.flatpakrepo')
        cls.public=base64.b64decode(config['Flatpak Repo']['GPGKey'])

    @classmethod
    def tearDownClass(cls):cls.workspace.cleanup()

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=self.work)
        self.directory=Path(self.temporary.name);self.cache=self.directory/'old';self.cache.mkdir()
        shipping={'schema':'org.projectluma.os-app-baseline/v1','applications':['org.projectluma.Clock']}
        roles={'schema':'org.projectluma.first-party-app-roles/v1','roles':{'org.projectluma.Clock':'signed-system-flatpak'}}
        (self.cache/'shipping.json').write_text(json.dumps(shipping))
        (self.cache/'roles.json').write_text(json.dumps(roles))
        (self.cache/'luma-depot.gpg').write_bytes(self.public)
        self.rows=[]
        for ref in [self.ref]+['runtime/'+name+'/x86_64/44' for name in PRODUCER.RUNTIMES]:
            body=self.bundle.read_bytes() if ref==self.ref else ref.encode()
            sha=hashlib.sha256(body).hexdigest();(self.cache/(sha+'.flatpak')).write_bytes(body)
            self.rows.append({'ref':ref,'commit':self.commit if ref==self.ref else 'a'*64,
                              'sha256':sha,'bytes':len(body),'file':sha+'.flatpak'})
        self.document={'schema':'org.projectluma.offline-app-baseline/v1','architecture':'x86_64',
                       'selection_scope':'os-shipping','default_channel':'beta',
                       'shipping_contract_sha256':digest(self.cache/'shipping.json'),
                       'role_contract_sha256':digest(self.cache/'roles.json'),
                       'public_key_sha256':digest(self.cache/'luma-depot.gpg'),'bundles':self.rows}
        self.publish()
        self.target=self.directory/'new.flatpak'

    def tearDown(self):self.temporary.cleanup()

    def publish(self):
        (self.cache/'manifest.json').write_text(json.dumps(self.document))
        self.pin=digest(self.cache/'manifest.json')

    def admitted(self):return PRODUCER.prepare_reuse(self.cache,'x86_64',self.public,self.pin)

    def test_exact_bundle_copied_and_read_back_with_real_identity(self):
        cache=self.admitted();source=self.cache/self.rows[0]['file']
        before=PRODUCER.source_identity(source)
        row=PRODUCER.copy_reused_bundle(cache,self.ref,self.commit,self.target)
        self.assertEqual(row,self.rows[0]);self.assertEqual(digest(self.target),row['sha256'])
        self.assertEqual(PRODUCER.source_identity(source),before)
        self.assertNotEqual(source.stat().st_ino,self.target.stat().st_ino)

    def test_changed_commit_requires_normal_producer(self):
        cache=self.admitted()
        self.assertIsNone(PRODUCER.reuse_candidate(cache,self.ref,'b'*64))
        with self.assertRaises(ValueError):PRODUCER.copy_reused_bundle(cache,self.ref,'b'*64,self.target)
        self.assertFalse(self.target.exists())

    def test_same_size_corrupt_body_rejected(self):
        cache=self.admitted();source=self.cache/self.rows[0]['file']
        data=bytearray(source.read_bytes());data[-1]^=1;source.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'bytes differ'):
            PRODUCER.copy_reused_bundle(cache,self.ref,self.commit,self.target)

    def test_manifest_pin_and_changed_source_refused(self):
        with self.assertRaises(ValueError):PRODUCER.prepare_reuse(self.cache,'x86_64',self.public,'b'*64)
        cache=self.admitted();(self.cache/'manifest.json').write_text(json.dumps(self.document)+'\n')
        with self.assertRaisesRegex(ValueError,'source changed'):
            PRODUCER.copy_reused_bundle(cache,self.ref,self.commit,self.target)

    def test_other_current_key_refused(self):
        with self.assertRaisesRegex(ValueError,'different authenticated signing key'):
            PRODUCER.prepare_reuse(self.cache,'x86_64',self.public+b'changed',self.pin)

    def test_actual_bundle_commit_cannot_be_replaced_by_manifest_claim(self):
        self.rows[0]['commit']='f'*64;self.publish();cache=self.admitted()
        with self.assertRaisesRegex(ValueError,'content identity differs'):
            PRODUCER.copy_reused_bundle(cache,self.ref,'f'*64,self.target)

    def test_retired_app_is_historical_cache_only(self):
        retired='org.projectluma.Displays'
        shipping=json.loads((self.cache/'shipping.json').read_text())
        shipping['applications'].append(retired)
        (self.cache/'shipping.json').write_text(json.dumps(shipping))
        roles=json.loads((self.cache/'roles.json').read_text())
        roles['roles'][retired]='signed-system-flatpak'
        (self.cache/'roles.json').write_text(json.dumps(roles))
        body=b'historical retired bundle, never selected for reuse'
        sha=hashlib.sha256(body).hexdigest();(self.cache/(sha+'.flatpak')).write_bytes(body)
        self.rows.append({'ref':'app/'+retired+'/x86_64/beta','commit':'c'*64,
                          'sha256':sha,'bytes':len(body),'file':sha+'.flatpak'})
        self.document.update(shipping_contract_sha256=digest(self.cache/'shipping.json'),
                             role_contract_sha256=digest(self.cache/'roles.json'))
        self.publish()
        cache=self.admitted()
        # Only an independently authenticated current ref is copied. The old
        # retired bundle remains untouched and never appears in this output.
        PRODUCER.copy_reused_bundle(cache,self.ref,self.commit,self.target)
        self.assertEqual(digest(self.target),self.rows[0]['sha256'])
        loader=PRODUCER.importlib.machinery.SourceFileLoader('test_baseline_normal',
            str(ROOT/'image/luma-desktop/rootfs/usr/libexec/luma-app-baseline'))
        spec=PRODUCER.importlib.util.spec_from_loader(loader.name,loader)
        validator=PRODUCER.importlib.util.module_from_spec(spec);loader.exec_module(validator)
        with self.assertRaisesRegex(ValueError,'unique reviewed shipping selection'):
            validator.validate(self.cache,'x86_64',hash_bundles=False)
        shipping['applications'][-1]='org.projectluma.Write'
        (self.cache/'shipping.json').write_text(json.dumps(shipping))
        with self.assertRaisesRegex(ValueError,'unique reviewed shipping selection'):
            validator.validate(self.cache,'x86_64',hash_bundles=False,historical=True)


if __name__=='__main__':unittest.main()
